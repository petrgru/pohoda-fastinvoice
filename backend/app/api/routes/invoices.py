from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import ColumnElement, Text, and_, cast, func, or_
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes import companies as companies_route
from app.core.config import settings
from app.models import (
    Invoice,
    InvoicePublic,
    InvoicesPublic,
    InvoiceState,
    InvoiceStats,
    InvoiceUpdate,
    MembershipRole,
    Message,
    RecipientCompany,
    RecipientCompanyAssign,
    RecipientCompanyCreate,
    User,
    get_datetime_utc,
)
from app.services import account_integrity, recipient_access, search_index, storage
from app.services import invoices as invoice_service
from app.services import recipients as recipient_service

router = APIRouter(prefix="/invoices", tags=["invoices"])

QUEUE_STATES = [
    InvoiceState.UPLOADED.value,
    InvoiceState.PARSING.value,
    InvoiceState.NEEDS_REVIEW.value,
    InvoiceState.EXPORT_FAILED.value,
]


def _get_invoice(
    session: SessionDep,
    invoice_id: UUID,
    *,
    user: User | None = None,
    min_role: MembershipRole = MembershipRole.EDITOR,
) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="errors.invoice_not_found")
    if user is not None:
        recipient_access.ensure_invoice_access(session, user, invoice, min_role)
    return invoice


def _assert_mutable(invoice: Invoice) -> None:
    if not invoice.can_delete:
        raise HTTPException(
            status_code=403,
            detail="errors.invoice_locked",
        )


def _assert_parsed(invoice: Invoice) -> None:
    """While a document is still being parsed its fields must stay frozen."""
    if invoice.state in (InvoiceState.UPLOADED, InvoiceState.PARSING):
        raise HTTPException(status_code=409, detail="errors.invoice_parsing")


@router.post("/upload", response_model=InvoicePublic, status_code=201)
async def upload_invoice(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    recipient_company_id: Annotated[UUID | None, Form()] = None,
) -> InvoicePublic:
    """Store an uploaded scan/photo/PDF and start parsing in the background.

    `recipient_company_id` optionally selects the recipient (the user's own company);
    a member with a single company does not have to choose – it is assigned.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="errors.file_empty")
    try:
        storage_key, file_hash = storage.prepare_invoice_file(
            data, file.content_type or ""
        )
    except storage.UnsupportedFileTypeError:
        raise HTTPException(status_code=415, detail="errors.file_type")
    except storage.FileTooLargeError:
        raise HTTPException(
            status_code=413,
            detail=f"errors.file_too_large|{settings.MAX_UPLOAD_SIZE_MB}",
        )

    duplicate = session.exec(
        select(Invoice).where(Invoice.file_hash == file_hash)
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"errors.duplicate_file|{duplicate.original_filename}",
        )

    # content-addressed key – a re-upload of the same bytes maps to the same
    # path, so saving is idempotent and never leaves an orphan behind
    storage.get_storage().save(storage_key, data)

    if recipient_company_id is not None:
        recipient_access.ensure_company_selectable(
            session, current_user, recipient_company_id
        )
    auto_company: RecipientCompany | None = None
    if recipient_company_id is None:
        auto_company = recipient_access.sole_company_for(session, current_user)
        if auto_company is not None:
            recipient_company_id = auto_company.id

    invoice = Invoice(
        original_filename=file.filename or "invoice",
        storage_key=storage_key,
        content_type=file.content_type or "application/octet-stream",
        file_size=len(data),
        uploaded_by_id=current_user.id,
        file_hash=file_hash,
        recipient_company_id=recipient_company_id,
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    invoice_service.log_audit(
        session,
        "uploaded",
        invoice=invoice,
        user=current_user,
        details={"filename": invoice.original_filename},
    )
    if auto_company is not None:
        invoice_service.log_audit(
            session,
            "company_default_assigned",
            invoice=invoice,
            company=auto_company,
            user=current_user,
        )
    session.commit()
    background_tasks.add_task(invoice_service.process_invoice, invoice.id)
    return invoice_service.to_public(session, invoice)


@router.get("", response_model=InvoicesPublic)
def list_invoices(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 20,
    state: str | None = None,
    recipient_company_id: UUID | None = None,
    q: str | None = None,
) -> InvoicesPublic:
    conditions: list[ColumnElement[bool]] = []
    if state:
        conditions.append(col(Invoice.state) == state)
    if recipient_company_id:
        recipient_access.ensure_company_selectable(
            session, current_user, recipient_company_id
        )
        conditions.append(col(Invoice.recipient_company_id) == recipient_company_id)
    if (visible := recipient_access.invoice_filter(session, current_user)) is not None:
        conditions.append(visible)
    if q and settings.MEILISEARCH_ENABLED:
        # relevance-ranked hits from Meilisearch; Postgres filters out any
        # documents the mirror has not caught up with yet. When the mirror
        # yields nothing usable (empty or stale index, deleted documents)
        # its answer is not trusted and the Postgres query below runs.
        ranked = search_index.search_ids(
            q,
            state=state,
            recipient_company_id=recipient_company_id,
            limit=limit,
            offset=skip,
        )
        if ranked is not None:
            ids, count = ranked
            rows: list[Invoice] = []
            if ids:
                ranked_query = select(Invoice).where(col(Invoice.id).in_(ids))
                if conditions:
                    ranked_query = ranked_query.where(and_(*conditions))
                rows = list(session.exec(ranked_query).all())
                order = {hit: position for position, hit in enumerate(ids)}
                rows = sorted(rows, key=lambda row: order.get(row.id, len(ids)))
            if rows:
                return InvoicesPublic(
                    data=[invoice_service.to_public(session, row) for row in rows],
                    count=max(count - (len(ids) - len(rows)), len(rows)),
                )
    if q:
        tsq = func.plainto_tsquery("simple", q)
        digits = "".join(char for char in q if char.isdigit())
        ilike = f"%{q}%"
        clauses = [
            col(Invoice.search_vector).op("@@")(tsq),
            col(Invoice.supplier_name).ilike(ilike),
            col(Invoice.invoice_number).ilike(ilike),
            col(Invoice.original_filename).ilike(ilike),
            # substring search in the original document text (any item on it)
            col(Invoice.ocr_text).ilike(ilike),
        ]
        if digits:
            clauses.append(col(Invoice.supplier_ico).like(f"%{digits}%"))
            clauses.append(cast(Invoice.total_price, Text).ilike(f"%{digits}%"))
        conditions.append(or_(*clauses))
    query = select(Invoice)
    count_query = select(func.count()).select_from(Invoice)
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)
    total = session.exec(count_query).one()
    items = session.exec(
        query.order_by(col(Invoice.created_at).desc()).offset(skip).limit(limit)
    ).all()
    return InvoicesPublic(
        data=[invoice_service.to_public(session, item) for item in items],
        count=total,
    )


@router.get("/queue", response_model=InvoicesPublic)
def list_queue(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
) -> InvoicesPublic:
    """Invoices still waiting in the task queue (not yet imported)."""
    conditions: list[ColumnElement[bool]] = [col(Invoice.state).in_(QUEUE_STATES)]
    if (visible := recipient_access.invoice_filter(session, current_user)) is not None:
        conditions.append(visible)
    count = session.exec(
        select(func.count()).select_from(Invoice).where(and_(*conditions))
    ).one()
    items = session.exec(
        select(Invoice)
        .where(and_(*conditions))
        .order_by(col(Invoice.created_at).asc())
        .offset(skip)
        .limit(limit)
    ).all()
    return InvoicesPublic(
        data=[invoice_service.to_public(session, item) for item in items], count=count
    )


@router.get("/stats", response_model=InvoiceStats)
def invoice_stats(
    *,
    session: SessionDep,
    current_user: CurrentUser,
) -> InvoiceStats:
    """Aggregated counts for the dashboard (Plocha) chart and cards."""
    return invoice_service.stats(
        session,
        recipient_access.member_company_ids(session, current_user),
        current_user.id,
    )


@router.get("/{invoice_id}", response_model=InvoicePublic)
def get_invoice(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    invoice_id: UUID,
) -> InvoicePublic:
    return invoice_service.to_public(
        session, _get_invoice(session, invoice_id, user=current_user)
    )


@router.get("/{invoice_id}/file")
def get_invoice_file(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    invoice_id: UUID,
) -> object:
    invoice = _get_invoice(session, invoice_id, user=current_user)

    try:
        data = storage.get_storage().open(invoice.storage_key)
    except FileNotFoundError, OSError:
        raise HTTPException(status_code=404, detail="errors.file_missing")
    return Response(
        content=data,
        media_type=invoice.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{invoice.original_filename}"'
        },
    )


@router.put("/{invoice_id}", response_model=InvoicePublic)
def update_invoice(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    invoice_id: UUID,
    invoice_in: InvoiceUpdate,
) -> InvoicePublic:
    invoice = _get_invoice(session, invoice_id, user=current_user)
    _assert_mutable(invoice)
    _assert_parsed(invoice)
    changes = invoice_in.model_dump(exclude_unset=True)
    confirm_account = changes.pop("confirm_account_change", False)
    if changes.get("recipient_company_id"):
        recipient_access.ensure_company_selectable(
            session, current_user, changes["recipient_company_id"]
        )
    # a changed account number must be confirmed by the user first (fraud guard)
    account_integrity.require_confirmation(
        session, invoice, changes, confirmed=confirm_account
    )
    for field, value in changes.items():
        setattr(invoice, field, value)
    if confirm_account and any(
        field in changes for field in account_integrity.ACCOUNT_FIELDS
    ):
        account_integrity.confirm(session, invoice, user=current_user)
    invoice_service.recompute_derived(invoice)
    invoice.updated_at = get_datetime_utc()
    session.add(invoice)
    invoice_service.log_audit(
        session,
        "fields_updated",
        invoice=invoice,
        user=current_user,
        details={"fields": sorted(changes)},
    )
    session.commit()
    session.refresh(invoice)
    invoice_service.update_search_vector(session, invoice)
    return invoice_service.to_public(session, invoice)


@router.put("/{invoice_id}/recipient-company", response_model=InvoicePublic)
def assign_company(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    invoice_id: UUID,
    company_in: RecipientCompanyAssign,
) -> InvoicePublic:
    invoice = _get_invoice(session, invoice_id, user=current_user)
    _assert_mutable(invoice)
    _assert_parsed(invoice)
    recipient_access.ensure_company_selectable(
        session, current_user, company_in.recipient_company_id
    )
    company = session.get(RecipientCompany, company_in.recipient_company_id)
    if not company:
        raise HTTPException(status_code=404, detail="errors.company_not_found")
    invoice_service.assign_company(session, invoice, company, user=current_user)
    session.commit()
    session.refresh(invoice)
    invoice_service.update_search_vector(session, invoice)
    return invoice_service.to_public(session, invoice)


@router.post(
    "/{invoice_id}/recipient-company/create",
    response_model=InvoicePublic,
    status_code=201,
)
def create_company_for_invoice(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    invoice_id: UUID,
    company_in: RecipientCompanyCreate,
) -> InvoicePublic:
    """Create the parsed supplier as a new company and assign it (one tx).

    The user confirms the pre-filled values in the UI before this is called;
    an existing IČO aborts with 409 so the company list stays authoritative.
    """
    invoice = _get_invoice(session, invoice_id, user=current_user)
    _assert_mutable(invoice)
    _assert_parsed(invoice)
    companies_route.assert_dic_valid(company_in.dic)
    existing = session.exec(
        select(RecipientCompany).where(RecipientCompany.ico == company_in.ico)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="errors.company_ico_exists")
    company = RecipientCompany.model_validate(
        company_in.model_dump(exclude={"bank_accounts"})
    )
    session.add(company)
    session.flush()  # persist before the audit rows reference it
    # whoever creates the company through an invoice becomes its first admin
    recipient_access.add_admin_member(session, current_user, company)
    recipient_service.apply_bank_accounts(session, company, company_in.bank_accounts)
    invoice_service.assign_company(session, invoice, company, user=current_user)
    session.commit()
    session.refresh(invoice)
    invoice_service.update_search_vector(session, invoice)
    return invoice_service.to_public(session, invoice)


@router.post("/{invoice_id}/approve", response_model=InvoicePublic)
def approve_invoice(
    *, session: SessionDep, current_user: CurrentUser, invoice_id: UUID
) -> InvoicePublic:
    """Confirm the import: switches the invoice into the export queue."""
    invoice = _get_invoice(session, invoice_id, user=current_user)
    _assert_mutable(invoice)
    try:
        invoice_service.approve(session, invoice, user=current_user)
    except invoice_service.ApprovalNotAllowedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    session.commit()
    session.refresh(invoice)
    return invoice_service.to_public(session, invoice)


@router.post("/{invoice_id}/unapprove", response_model=InvoicePublic)
def unapprove_invoice(
    *, session: SessionDep, current_user: CurrentUser, invoice_id: UUID
) -> InvoicePublic:
    invoice = _get_invoice(session, invoice_id, user=current_user)
    try:
        invoice_service.revoke_approval(session, invoice, user=current_user)
    except invoice_service.InvoiceLockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    session.commit()
    session.refresh(invoice)
    return invoice_service.to_public(session, invoice)


@router.post("/{invoice_id}/reparse", response_model=InvoicePublic)
def reparse_invoice(
    *,
    session: SessionDep,
    current_user: CurrentUser,  # noqa: ARG001
    background_tasks: BackgroundTasks,
    invoice_id: UUID,
) -> InvoicePublic:
    invoice = _get_invoice(session, invoice_id, user=current_user)
    _assert_mutable(invoice)
    try:
        invoice_service.reparse(session, invoice)
    except invoice_service.InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    background_tasks.add_task(invoice_service.process_invoice, invoice.id)
    session.refresh(invoice)
    return invoice_service.to_public(session, invoice)


@router.delete("/{invoice_id}", response_model=Message)
def delete_invoice(
    *, session: SessionDep, current_user: CurrentUser, invoice_id: UUID
) -> Message:
    """Delete an invoice; impossible once it was imported into the system."""
    invoice = _get_invoice(
        session, invoice_id, user=current_user, min_role=MembershipRole.ADMIN
    )
    try:
        invoice_service.delete_invoice(session, invoice, user=current_user)
    except invoice_service.InvoiceLockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    session.commit()
    return Message(message="Faktura smazána")
