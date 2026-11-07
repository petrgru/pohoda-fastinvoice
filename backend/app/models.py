import enum
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import EmailStr, field_validator
from sqlalchemy import JSON, Column, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(default_factory=get_datetime_utc)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Generic message
class RuntimeConfig(SQLModel):
    """Public feature flags the UI may adapt to."""

    develop: bool = False


class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class MembershipRole(str, enum.Enum):  # noqa: UP042 - str mixin for DB values
    """Role of a user inside one company.

    ADMIN  – manages members, API keys and deletions of the company.
    EDITOR – views, uploads, edits and approves the company's invoices.
    """

    ADMIN = "admin"
    EDITOR = "editor"


# ---------------------------------------------------------------------------
# Companies (Partneři)
# ---------------------------------------------------------------------------

_ICO_RE = re.compile(r"^\d{8}$")
_DIC_RE = re.compile(r"^[A-Z]{2}\d{8,12}$|^\d{8,10}$")

# Czech bank account forms: "prefix-number/bank", "prefix/number/bank",
# "number/bank" and a bare "number" (prefix optional, bank code optional).
_ACCOUNT_HYPHEN = re.compile(r"^(\d{1,6})-(\d{1,16})/(\d{4})$")
_ACCOUNT_SLASHES = re.compile(r"^(\d{1,6})/(\d{1,16})/(\d{4})$")
_ACCOUNT_SLASH = re.compile(r"^(\d{1,16})/(\d{4})$")
_ACCOUNT_BARE = re.compile(r"^(\d{1,16})$")


_IBAN_CZ = re.compile(r"^CZ\d{2}(\d{4})(\d{6})(\d{10})$")


def _parse_bank_account(account: str) -> tuple[str, str, str] | None:
    """Split a Czech bank account into (prefix, number, bank_code).

    Accepts ``prefix-number/code``, ``prefix/number/code``, ``number/code`` and a
    bare ``number``. ``prefix``/``bank_code`` are empty when absent. Returns None
    when the string matches none of the supported forms.
    """
    for pattern in (_ACCOUNT_HYPHEN, _ACCOUNT_SLASHES):
        match = pattern.match(account)
        if match:
            return match.group(1), match.group(2), match.group(3)
    match = _ACCOUNT_SLASH.match(account)
    if match:
        return "", match.group(1), match.group(2)
    match = _ACCOUNT_BARE.match(account)
    if match:
        return "", match.group(1), ""
    return None


def normalize_bank_account(value: str) -> str:
    """Normalize to the canonical Czech form used for the Pohoda export.

    ``0502/123456789/0100`` and ``0502-123456789/0100`` → ``0502-123456789/0100``,
    a Czech IBAN → ``number/bank`` (prefix kept when non-zero). Unparseable
    values (e.g. a foreign IBAN) are returned stripped/uppercased. NOTE: no
    mod-11 check – real Czech accounts (e.g. FIO) legitimately do not satisfy
    it; the checksum is only meaningful for IČO/DIČ.
    """
    account = value.replace(" ", "").upper()
    iban = _IBAN_CZ.match(account)
    if iban:
        bank, prefix, number = iban.groups()
        prefix = prefix.lstrip("0")
        number = number.lstrip("0") or "0"
        head = f"{prefix}-{number}" if prefix else number
        return f"{head}/{bank}"
    parsed = _parse_bank_account(account)
    if parsed is not None:
        prefix, number, bank = parsed
        if prefix:
            return f"{prefix}-{number}/{bank}" if bank else f"{prefix}-{number}"
        return f"{number}/{bank}" if bank else number
    return account


def normalize_iban(value: str) -> str:
    """Uppercase an IBAN without spaces (format checked separately)."""
    return re.sub(r"\s+", "", value).upper()


class InvoiceState(str, enum.Enum):  # noqa: UP042 - str mixin needed for DB values
    """Workflow states of an invoice.

    Transitions (enforced in crud.invoices):
      UPLOADED -> PARSING -> NEEDS_REVIEW -> READY_FOR_EXPORT -> EXPORTED
                                             \\-> EXPORT_FAILED -> NEEDS_REVIEW
    A record may be deleted in any state except EXPORTED.
    """

    UPLOADED = "uploaded"
    PARSING = "parsing"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_EXPORT = "ready_for_export"
    EXPORTED = "exported"
    EXPORT_FAILED = "export_failed"


DELETABLE_STATES = {
    InvoiceState.UPLOADED,
    InvoiceState.PARSING,
    InvoiceState.NEEDS_REVIEW,
    InvoiceState.READY_FOR_EXPORT,
    InvoiceState.EXPORT_FAILED,
}


class RecipientCompanyBase(SQLModel):
    name: str = Field(min_length=1, max_length=255)
    ico: str = Field(unique=True, index=True, max_length=8)
    dic: str | None = Field(default=None, max_length=15)
    address: str | None = Field(default=None, max_length=255)
    bank_account: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=255)
    is_active: bool = True

    @field_validator("ico")
    @classmethod
    def validate_ico(cls, value: str) -> str:
        ico = value.strip()
        if not _ICO_RE.match(ico):
            raise ValueError("IČO musí být 8 číslic")
        return ico

    @field_validator("dic")
    @classmethod
    def validate_dic(cls, value: str | None) -> str | None:
        if value is None:
            return value
        dic = value.replace(" ", "").upper()
        if not _DIC_RE.match(dic):
            raise ValueError("DIČ musí být 8-10 číslic nebo kód země + 8-12 číslic")
        # no mod-11 here – the real validity is checked against VatComply/VIES
        # when the company is saved (see app.services.vat)
        return dic

    @field_validator("bank_account")
    @classmethod
    def validate_bank_account(cls, value: str | None) -> str | None:
        if value is None:
            return value
        account = normalize_bank_account(value)
        if account.startswith("CZ"):  # IBAN – formát ověří až koncová aplikace
            if re.fullmatch(r"CZ\d{22}", account):  # CZ + dvojčíslí + 20 číslic
                return account
            raise ValueError("Neplatný formát IBAN")
        if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", account):
            return account  # foreign IBAN
        if _parse_bank_account(account) is None:
            raise ValueError("Neplatný formát čísla účtu")
        return account


class BankAccountBase(SQLModel):
    """One payment account of a company – domestic format and/or IBAN."""

    account: str | None = Field(default=None, max_length=40)
    iban: str | None = Field(default=None, max_length=34)

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return RecipientCompanyBase.validate_bank_account(value)

    @field_validator("iban")
    @classmethod
    def validate_iban(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        iban = normalize_iban(value)
        if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
            raise ValueError("Neplatný formát IBAN")
        return iban


class BankAccount(BankAccountBase, table=True):
    """A bank account row belonging to a company (invoices may list several)."""

    __tablename__ = "recipient_bank_account"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    recipient_company_id: uuid.UUID = Field(
        foreign_key="recipientcompany.id", index=True, ondelete="CASCADE"
    )
    position: int = Field(default=0)

    # quoted on purpose: SQLModel needs the resolvable class name string here
    recipient_company: RecipientCompany = Relationship(back_populates="bank_accounts")  # noqa: UP037


class BankAccountCreate(BankAccountBase):
    pass


class BankAccountPublic(BankAccountBase):
    id: uuid.UUID
    position: int = 0


class SupplierBankAccount(BankAccountBase):
    """One payment account of the invoice supplier (parsed, user-correctable)."""


class RecipientCompanyCreate(RecipientCompanyBase):
    # Manual company entry requires every field except DIČ; the shared base
    # keeps them nullable so existing rows and the Pohoda sync stay tolerant.
    bank_account: str = Field(max_length=40)
    bank_accounts: list[BankAccountCreate] | None = None

    @field_validator("bank_accounts")
    @classmethod
    def drop_empty_rows(
        cls, value: list[BankAccountCreate] | None
    ) -> list[BankAccountCreate] | None:
        if value is None:
            return None
        return [row for row in value if row.account or row.iban]


class RecipientCompanyUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    ico: str | None = Field(default=None, max_length=8)
    dic: str | None = Field(default=None, max_length=15)
    address: str | None = Field(default=None, max_length=255)
    bank_account: str | None = Field(default=None, max_length=40)
    bank_accounts: list[BankAccountCreate] | None = None
    note: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None

    @field_validator("ico")
    @classmethod
    def validate_ico(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return RecipientCompanyBase.validate_ico(value)

    @field_validator("dic")
    @classmethod
    def validate_dic(cls, value: str | None) -> str | None:
        return RecipientCompanyBase.validate_dic(value)

    @field_validator("bank_account")
    @classmethod
    def validate_bank_account(cls, value: str | None) -> str | None:
        return RecipientCompanyBase.validate_bank_account(value)


class RecipientCompany(RecipientCompanyBase, table=True):
    """The document recipient – the user's own company invoices are filed under."""

    __tablename__ = "recipientcompany"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    updated_at: datetime | None = Field(default_factory=get_datetime_utc)
    source: str = Field(default="manual", max_length=20)  # manual | pohoda_sync
    invoices: list[Invoice] = Relationship(back_populates="recipient_company")
    bank_accounts: list[BankAccount] = Relationship(
        back_populates="recipient_company",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "BankAccount.position",
        },
    )


class RecipientCompanyPublic(RecipientCompanyBase):
    id: uuid.UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source: str = "manual"
    bank_accounts: list[BankAccountPublic] = []
    my_role: MembershipRole | None = None  # current user's role in this company


class RecipientCompaniesPublic(SQLModel):
    data: list[RecipientCompanyPublic]
    count: int


# ---------------------------------------------------------------------------
# RecipientCompany membership and per-company API keys
# ---------------------------------------------------------------------------


class RecipientMembership(SQLModel, table=True):
    """A user's role in one company; superusers are implicitly admin everywhere."""

    __tablename__ = "recipientmembership"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    recipient_company_id: uuid.UUID = Field(
        foreign_key="recipientcompany.id", ondelete="CASCADE", index=True
    )
    role: str = Field(default=MembershipRole.EDITOR.value, max_length=16)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)

    __table_args__ = (UniqueConstraint("user_id", "recipient_company_id"),)


class MembershipCreate(SQLModel):
    """Add a member by e-mail; an unknown address receives an invite e-mail."""

    email: EmailStr
    role: MembershipRole = MembershipRole.EDITOR


class MembershipUpdate(SQLModel):
    role: MembershipRole


class MembershipPublic(SQLModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    is_active: bool = True
    role: MembershipRole
    created_at: datetime | None = None


class RecipientApiKey(SQLModel, table=True):
    """API key granting the Pohoda connector access to ONE company's export."""

    __tablename__ = "recipientapikey"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    recipient_company_id: uuid.UUID = Field(
        foreign_key="recipientcompany.id", ondelete="CASCADE", index=True
    )
    label: str = Field(max_length=64)
    key_prefix: str = Field(max_length=16)  # first chars, shown in listings
    key_hash: str = Field(max_length=64, unique=True, index=True)  # sha256 hex
    created_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class RecipientApiKeyCreate(SQLModel):
    label: str = Field(min_length=1, max_length=64)


class RecipientApiKeyPublic(SQLModel):
    id: uuid.UUID
    label: str
    key_prefix: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class RecipientApiKeyCreated(RecipientApiKeyPublic):
    """Same as the public view but carrying the plaintext key, shown once."""

    key: str


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def _parse_amount(value: object) -> object:
    """Coerce LLM/JSON amounts ("1 234,56", 1234.5) into Decimal-friendly input."""
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d,.]", "", value)
        if "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
        return cleaned
    return value


class ParsedInvoice(SQLModel):
    """Structured fields returned by the LLM extraction (validated by Pydantic)."""

    invoice_number: str | None = Field(default=None, max_length=64)
    supplier_name: str | None = Field(default=None, max_length=255)
    supplier_ico: str | None = None
    supplier_dic: str | None = None
    supplier_bank_account: str | None = None
    supplier_iban: str | None = None
    supplier_bank_accounts: list[SupplierBankAccount] | None = None
    recipient_name: str | None = Field(default=None, max_length=255)
    recipient_ico: str | None = None
    recipient_dic: str | None = None
    document_type: Literal["invoice", "proforma", "receipt", "other"] | None = None
    issue_date: date | None = None
    delivery_date: date | None = None
    due_date: date | None = None
    total_price: Decimal | None = None
    total_vat: Decimal | None = None
    total_without_vat: Decimal | None = None
    currency: str | None = Field(default=None, max_length=3)
    vat_items: list[VatItem] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("total_price", "total_vat", "total_without_vat", mode="before")
    @classmethod
    def coerce_amounts(cls, value: object) -> object:
        return _parse_amount(value)


class VatItem(SQLModel):
    """One VAT rate line of an invoice (Základ daně / DPH / Celkem)."""

    vat_rate: str | None = Field(default=None, max_length=15)  # e.g. "21%", "MPO"
    base_price: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=2)
    tax_price: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=2)
    total_price: Decimal = Field(default=Decimal("0"), max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, max_length=3)  # e.g. "CZK", "EUR"

    @property
    def consistent(self) -> bool:
        return abs((self.base_price + self.tax_price) - self.total_price) <= Decimal(
            "0.5"
        )


# Standard + historical VAT rates per country, used only for informational
# warnings (e.g. a DE invoice with the 2020 transitional 16 % rate is fine).
TYPICAL_VAT_RATES: dict[str, set[float]] = {
    "AT": {0.0, 10.0, 12.0, 13.0, 19.0, 20.0},
    "BE": {0.0, 6.0, 12.0, 21.0},
    "CZ": {
        0.0,
        5.0,
        9.0,
        10.0,
        11.0,
        12.0,
        14.0,
        15.0,
        16.0,
        17.0,
        18.0,
        19.0,
        20.0,
        21.0,
    },
    "DE": {0.0, 5.0, 7.0, 16.0, 18.0, 19.0, 22.0},
    "ES": {0.0, 4.0, 10.0, 21.0},
    "FR": {0.0, 5.5, 10.0, 20.0},
    "HU": {0.0, 5.0, 18.0, 27.0},
    "IT": {0.0, 4.0, 5.0, 10.0, 22.0},
    "NL": {0.0, 9.0, 21.0},
    "PL": {0.0, 5.0, 7.0, 8.0, 23.0},
    "RO": {0.0, 9.0, 11.0, 19.0, 21.0},
    "SK": {0.0, 10.0, 20.0},
}
_RATE_NUM_RE = re.compile(r"^(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:%|prozent)$", re.IGNORECASE)


def vat_rate_warnings(
    vat_items: list[VatItem] | None,
    supplier_dic: str | None,
    supplier_iban: str | None,
) -> list[str]:
    """Machine keys flagging rates unusual for the supplier's country."""
    if not vat_items:
        return []
    probe = ((supplier_dic or "") + (supplier_iban or "")).upper()
    country = probe[:2] if probe[:2].isalpha() else "CZ"
    typical = TYPICAL_VAT_RATES.get(country)
    if typical is None:
        return []  # unknown country – do not flag anything
    seen: set[str] = set()
    for item in vat_items:
        match = _RATE_NUM_RE.match((item.vat_rate or "").strip())
        if not match:
            continue  # MPO and other verbal labels are not percentages
        rate = float(match.group(1).replace(",", "."))
        if rate not in typical:
            text = f"{match.group(1)}%".replace(".", ",")
            seen.add(f"vat.rateUnusual|{country}|{text}")
    return sorted(seen)


def as_vat_items(value: object) -> list[VatItem] | None:
    """Coerce a JSON column value (or model instances) into VatItem models."""
    if value is None:
        return None
    raw = value if isinstance(value, list) else []
    return [VatItem.model_validate(item) for item in raw]


def vat_items_to_json(items: list[VatItem] | None) -> list[dict[str, Any]] | None:
    """JSON-serializable representation for storage (Decimals become strings)."""
    if items is None:
        return None
    return [item.model_dump(mode="json") for item in items]


def check_vat_items(
    vat_items: list[VatItem] | None,
    total_price: Decimal | None,
    currency: str | None = None,
) -> tuple[bool | None, Decimal | None]:
    """Return (vat_check_ok, difference) for parsed VAT items vs total price.

    Only lines in the invoice currency are compared (a foreign document may
    list informational amounts in a second currency).
    """
    if not vat_items:
        return None, None
    relevant = [
        item
        for item in vat_items
        if currency is None or item.currency is None or item.currency == currency
    ]
    if not relevant:
        return None, None
    for item in relevant:
        if not item.consistent:
            difference = (
                abs(total_price - sum((i.total_price for i in relevant), Decimal(0)))
                if total_price is not None
                else None
            )
            return False, difference
    if total_price is None:
        return None, None
    difference = (
        sum((i.base_price + i.tax_price for i in relevant), Decimal(0)) - total_price
    )
    return abs(difference) <= Decimal("0.5"), difference


class Invoice(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    updated_at: datetime | None = Field(default_factory=get_datetime_utc)

    # Source file
    original_filename: str = Field(max_length=255)
    storage_key: str = Field(max_length=255, unique=True)
    content_type: str = Field(max_length=100)
    file_size: int
    file_hash: str = Field(unique=True, index=True, max_length=64)  # sha256 hex

    # Workflow
    state: str = Field(default=InvoiceState.UPLOADED.value, index=True, max_length=32)
    state_note: str | None = Field(default=None, max_length=255)

    # Extraction / parsing
    ocr_text: str | None = Field(default=None, sa_column=Column(Text))
    parse_error: str | None = Field(default=None, max_length=255)
    llm_confidence: float | None = None

    # Parsed fields (LLM, user-correctable)
    invoice_number: str | None = Field(default=None, max_length=64)
    supplier_name: str | None = Field(default=None, max_length=255)
    supplier_ico: str | None = Field(default=None, max_length=8)
    supplier_dic: str | None = Field(default=None, max_length=15)
    supplier_bank_account: str | None = Field(default=None, max_length=40)
    supplier_iban: str | None = Field(default=None, max_length=34)
    supplier_bank_accounts: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON)
    )  # serialized SupplierBankAccount models (JSON column)
    # Document recipient (the user's own company detected on the document)
    recipient_name: str | None = Field(default=None, max_length=255)
    recipient_ico: str | None = Field(default=None, max_length=8)
    recipient_dic: str | None = Field(default=None, max_length=15)
    document_type: str | None = Field(
        default=None, max_length=20
    )  # invoice|proforma|receipt|other
    issue_date: date | None = None
    delivery_date: date | None = None
    due_date: date | None = None
    total_price: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    total_vat: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    total_without_vat: Decimal | None = Field(
        default=None, max_digits=12, decimal_places=2
    )
    currency: str | None = Field(default="CZK", max_length=3)
    vat_items: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON)
    )  # serialized VatItem models (JSON column)
    vat_check_ok: bool | None = None
    vat_difference: Decimal | None = Field(
        default=None, max_digits=12, decimal_places=2
    )

    # Relations / approval / export
    recipient_company_id: uuid.UUID | None = Field(
        default=None, foreign_key="recipientcompany.id", index=True, ondelete="SET NULL"
    )
    recipient_company: RecipientCompany | None = Relationship(back_populates="invoices")
    uploaded_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", index=True, ondelete="SET NULL"
    )
    approved_by_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    approved_at: datetime | None = None
    exported_at: datetime | None = None
    export_batch_ref: str | None = Field(default=None, max_length=64, index=True)

    # Full-text search vector (maintained in crud.invoices)
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(TSVECTOR, nullable=True),
    )

    __table_args__ = (
        Index(
            "ix_invoice_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str) -> str:
        InvoiceState(value)
        return value

    @property
    def can_delete(self) -> bool:
        return InvoiceState(self.state) in DELETABLE_STATES


class InvoicePublic(SQLModel):
    """Public representation built via `crud.invoices.to_public()`."""

    id: uuid.UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None
    original_filename: str
    content_type: str
    file_size: int
    state: str
    state_note: str | None = None
    parse_error: str | None = None
    llm_confidence: float | None = None
    invoice_number: str | None = None
    supplier_name: str | None = None
    supplier_ico: str | None = None
    supplier_dic: str | None = None
    supplier_bank_account: str | None = None
    supplier_iban: str | None = None
    supplier_bank_accounts: list[SupplierBankAccount] | None = None
    recipient_name: str | None = None
    recipient_ico: str | None = None
    recipient_dic: str | None = None
    document_type: str | None = None
    issue_date: date | None = None
    delivery_date: date | None = None
    due_date: date | None = None
    total_price: Decimal | None = None
    total_vat: Decimal | None = None
    total_without_vat: Decimal | None = None
    currency: str | None = None
    vat_items: list[VatItem] | None = None
    vat_check_ok: bool | None = None
    vat_difference: Decimal | None = None
    vat_rate_warnings: list[str] = []
    recipient_company_id: uuid.UUID | None = None
    company: RecipientCompanyPublic | None = None
    uploaded_by_id: uuid.UUID | None = None
    approved_by_id: uuid.UUID | None = None
    approved_at: datetime | None = None
    exported_at: datetime | None = None
    can_delete: bool = True
    queue_reasons: list[str] = []


class InvoicesPublic(SQLModel):
    data: list[InvoicePublic]
    count: int


class DataInvoicePublic(SQLModel):
    """Read-only invoice projection for external apps (Data API)."""

    id: uuid.UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None
    state: str
    invoice_number: str | None = None
    document_type: str | None = None
    supplier_name: str | None = None
    supplier_ico: str | None = None
    supplier_dic: str | None = None
    recipient_name: str | None = None
    recipient_ico: str | None = None
    recipient_dic: str | None = None
    recipient_company_id: uuid.UUID | None = None
    company: RecipientCompanyPublic | None = None
    issue_date: date | None = None
    delivery_date: date | None = None
    due_date: date | None = None
    total_price: Decimal | None = None
    total_vat: Decimal | None = None
    total_without_vat: Decimal | None = None
    currency: str | None = None
    vat_items: list[VatItem] | None = None
    original_filename: str | None = None
    content_type: str | None = None
    file_size: int | None = None
    approved_at: datetime | None = None
    exported_at: datetime | None = None


class DataInvoicesPublic(SQLModel):
    data: list[DataInvoicePublic]
    count: int


class InvoiceStateCounts(SQLModel):
    """Invoice counts broken down by workflow state."""

    uploaded: int = 0
    parsing: int = 0
    needs_review: int = 0
    ready_for_export: int = 0
    exported: int = 0
    export_failed: int = 0


class InvoiceStatsBucket(SQLModel):
    """One day/week/month of the dashboard chart series."""

    period: str  # "2026-11-01", "2026-W44" or "2026-11"
    total: int = 0
    by_state: InvoiceStateCounts = InvoiceStateCounts()


class InvoiceStats(SQLModel):
    """Aggregations for the dashboard: recent periods and global totals."""

    day: list[InvoiceStatsBucket] = []
    week: list[InvoiceStatsBucket] = []
    month: list[InvoiceStatsBucket] = []
    totals: InvoiceStateCounts = InvoiceStateCounts()


class RecipientCompanyAssign(SQLModel):
    recipient_company_id: uuid.UUID


class InvoiceUpdate(SQLModel):
    """User corrections of parsed fields; recalculation happens in crud."""

    invoice_number: str | None = Field(default=None, max_length=64)
    supplier_name: str | None = Field(default=None, max_length=255)
    supplier_ico: str | None = Field(default=None, max_length=8)
    supplier_dic: str | None = Field(default=None, max_length=15)
    supplier_bank_account: str | None = Field(default=None, max_length=40)
    supplier_iban: str | None = Field(default=None, max_length=34)
    supplier_bank_accounts: list[SupplierBankAccount] | None = None
    document_type: str | None = Field(default=None, max_length=20)
    issue_date: date | None = None
    delivery_date: date | None = None
    due_date: date | None = None
    total_price: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    total_vat: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    total_without_vat: Decimal | None = Field(
        default=None, max_digits=12, decimal_places=2
    )
    currency: str | None = Field(default=None, max_length=3)
    vat_items: list[VatItem] | None = None

    @field_validator("supplier_ico")
    @classmethod
    def validate_ico(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return RecipientCompanyBase.validate_ico(value)

    @field_validator("supplier_dic")
    @classmethod
    def validate_dic(cls, value: str | None) -> str | None:
        return RecipientCompanyBase.validate_dic(value)

    @field_validator("supplier_iban")
    @classmethod
    def validate_iban(cls, value: str | None) -> str | None:
        return BankAccountBase.validate_iban(value)


# ---------------------------------------------------------------------------
# Export batches, company sync, audit log
# ---------------------------------------------------------------------------


class ExportBatch(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    batch_ref: str = Field(unique=True, index=True, max_length=64)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    note: str | None = Field(default=None, max_length=255)


class ExportResult(SQLModel):
    invoice_id: uuid.UUID
    success: bool
    batch_ref: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=255)


class ExportQueueItem(SQLModel):
    id: uuid.UUID
    state: str
    original_filename: str
    content_type: str
    export_url: str
    company: RecipientCompanyPublic
    invoice_number: str | None = None
    document_type: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    total_price: Decimal | None = None
    total_vat: Decimal | None = None
    total_without_vat: Decimal | None = None
    currency: str | None = None
    vat_items: list[VatItem] | None = None
    approved_at: datetime | None = None


class ExportQueuePublic(SQLModel):
    data: list[ExportQueueItem]
    count: int


class VatInfoPublic(SQLModel):
    """VAT validity plus the registered name/address (for form autofill)."""

    valid: bool
    name: str | None = None
    address: str | None = None
    country_code: str | None = None


class AuditLog(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(default_factory=get_datetime_utc)
    invoice_id: uuid.UUID | None = Field(
        default=None, foreign_key="invoice.id", index=True, ondelete="CASCADE"
    )
    recipient_company_id: uuid.UUID | None = Field(
        default=None, foreign_key="recipientcompany.id", index=True, ondelete="CASCADE"
    )
    user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    action: str = Field(max_length=64, index=True)
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
