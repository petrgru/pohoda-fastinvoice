"""Accounting connector API for the Windows bridge service.

A single endpoint family for any accounting system (Pohoda today, Abra
Flexi, Money, … later) – the connector pulls batches with generic field
names, replays them into its ERP and reports the outcome. Authenticated by
the same company ``X-API-Key`` as the `/data` and `/external` APIs; the key
only ever sees its own company's records. Documents are downloaded through
the read-only `/data` API, not here.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.external import ExternalAccess, require_company_api_key
from app.models import (
    ErpBatchOut,
    ErpConnection,
    ErpConnectionPublic,
    ErpHeartbeatIn,
    ErpSyncOutcome,
    ErpSyncResultsIn,
    ErpSyncResultsOut,
    get_datetime_utc,
)
from app.services import erp_connector, recipient_access
from app.services import invoices as invoice_service

router = APIRouter(prefix="/erp-connector", tags=["erp-connector"])

ConnectorAccess = Annotated[ExternalAccess, Depends(require_company_api_key)]


@router.get("/pending-batch", response_model=ErpBatchOut)
def pending_batch(
    session: SessionDep,
    access: ConnectorAccess,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ErpBatchOut:
    """Claim the next invoices to import; suppliers come before invoices.

    Claimed records switch to the ``processing`` state so no other
    connector instance picks them up; a watchdog re-queues claims whose
    result never arrived.
    """
    _key, company = access
    invoices = invoice_service.claim_export_batch(session, company.id, limit=limit)
    _touch_heartbeat(session, company.id)
    return erp_connector.build_batch(session, company, invoices)


@router.post("/sync-results", response_model=ErpSyncResultsOut)
def sync_results(
    results_in: ErpSyncResultsIn,
    session: SessionDep,
    access: ConnectorAccess,
) -> ErpSyncResultsOut:
    """Report the connector's verdict for each claimed record.

    ``synced`` requires ``remote_number`` (the ERP document number),
    ``error`` counts as one automatic attempt (max 3, then a user must
    re-queue), ``retry_later`` re-queues without touching the counter.
    """
    key, company = access
    outcomes = erp_connector.apply_results(session, company, results_in.results)
    _touch_heartbeat(session, company.id)
    invoice_service.log_audit(
        session,
        "api_erp_sync_results",
        invoice=None,
        user=None,
        details={
            "count": len(outcomes),
            "failed": sum(1 for _item, ok, _d in outcomes if not ok),
            "api_key": key.label,
        },
    )
    session.commit()
    return ErpSyncResultsOut(
        applied=[
            ErpSyncOutcome(
                local_id=item.local_id,
                entity_type=item.entity_type,
                ok=ok,
                detail=detail,
            )
            for item, ok, detail in outcomes
        ]
    )


@router.post("/heartbeat", status_code=204)
def heartbeat(
    heartbeat_in: ErpHeartbeatIn,
    session: SessionDep,
    access: ConnectorAccess,
) -> None:
    """Keep-alive with connector stats; feeds the health dot in the UI."""
    _key, company = access
    connection = session.exec(
        select(ErpConnection).where(ErpConnection.recipient_company_id == company.id)
    ).first()
    if connection is None:
        connection = ErpConnection(recipient_company_id=company.id)
        session.add(connection)
    connection.system = heartbeat_in.system
    connection.version = heartbeat_in.version
    connection.mode = heartbeat_in.mode
    connection.queue_size = heartbeat_in.queue_size
    connection.last_error = heartbeat_in.last_error
    connection.last_seen_at = get_datetime_utc()
    session.add(connection)
    session.commit()


@router.get("/connection", response_model=ErpConnectionPublic | None)
def connection_status(
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
) -> ErpConnectionPublic | None:
    """Last connector heartbeat of a company (for the company dialog)."""
    recipient_access.ensure_company_selectable(session, current_user, company_id)
    connection = session.exec(
        select(ErpConnection).where(ErpConnection.recipient_company_id == company_id)
    ).first()
    return (
        ErpConnectionPublic.model_validate(connection, from_attributes=True)
        if connection
        else None
    )


def _touch_heartbeat(session: Session, company_id: UUID) -> None:
    """Every connector call counts as a heartbeat even without POST /heartbeat."""
    connection = session.exec(
        select(ErpConnection).where(ErpConnection.recipient_company_id == company_id)
    ).first()
    if connection is not None:
        connection.last_seen_at = get_datetime_utc()
        session.add(connection)
