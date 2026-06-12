"""Clean Secretary domain DTOs shared by API modules."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    owner = "owner"
    admin = "admin"
    manager = "manager"
    staff = "staff"
    accountant = "accountant"


class Permission(str, Enum):
    bootstrap_manage = "bootstrap.manage"
    company_manage = "company.manage"
    users_manage = "users.manage"
    catalogue_read = "catalogue.read"
    pricing_manage = "pricing.manage"
    crm_manage = "crm.manage"
    language_manage = "language.manage"
    voice_execute = "voice.execute"
    backup_manage = "backup.manage"
    backup_personal = "backup.personal"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.owner: set(Permission),
    Role.admin: {
        Permission.company_manage,
        Permission.users_manage,
        Permission.catalogue_read,
        Permission.pricing_manage,
        Permission.crm_manage,
        Permission.language_manage,
        Permission.voice_execute,
        Permission.backup_manage,
        Permission.backup_personal,
    },
    Role.manager: {
        Permission.catalogue_read,
        Permission.pricing_manage,
        Permission.crm_manage,
        Permission.language_manage,
        Permission.voice_execute,
        Permission.backup_personal,
    },
    Role.staff: {
        Permission.catalogue_read,
        Permission.crm_manage,
        Permission.voice_execute,
        Permission.backup_personal,
    },
    Role.accountant: {
        Permission.catalogue_read,
        Permission.crm_manage,
        Permission.backup_personal,
    },
}


class LanguageScope(str, Enum):
    internal = "internal"
    customer = "customer"
    voice_input = "voice_input"
    voice_output = "voice_output"


class LanguageMode(str, Enum):
    single = "single"
    multilingual = "multilingual"
    context = "context"


class VoiceLanguageStrategy(str, Enum):
    tenant_default = "tenant_default"
    user_preferred = "user_preferred"
    client_preferred = "client_preferred"
    detect_from_context = "detect_from_context"


class CompanyProfile(BaseModel):
    id: str
    legal_name: str
    trading_name: str | None = None
    legal_type: str | None = None
    default_country: str = "GB"
    default_currency: str = "GBP"
    timezone: str = "Europe/London"
    phone: str | None = None
    website: str | None = None
    industry_group: str | None = None
    industry_subtype: str | None = None


class CompanyLegalIdentity(BaseModel):
    legal_name: str
    trading_name: str | None = None
    legal_type: str | None = None
    registration_number: str | None = None
    tax_number: str | None = None
    registered_address: str | None = None
    phone: str | None = None
    website: str | None = None


class CompanyOperatingSettings(BaseModel):
    workspace_mode: str = Field(default="single_company")
    quote_prefix: str = "Q"
    invoice_prefix: str = "INV"
    default_tax_rate_percent: float = 0
    require_quote_acceptance_before_invoice: bool = True


class TenantOperatingProfile(BaseModel):
    company_id: str
    workspace_mode: str = "single_company"
    industry_group: str | None = None
    industry_subtype: str | None = None
    internal_language_mode: LanguageMode = LanguageMode.single
    customer_language_mode: LanguageMode = LanguageMode.multilingual
    default_internal_language_code: str = "en-GB"
    default_customer_language_code: str = "en-GB"
    voice_input_strategy: VoiceLanguageStrategy = VoiceLanguageStrategy.detect_from_context
    voice_output_strategy: VoiceLanguageStrategy = VoiceLanguageStrategy.client_preferred
    auto_translate_customer_to_internal: bool = True
    auto_translate_internal_to_customer: bool = True


class UserAccount(BaseModel):
    id: str
    company_id: str
    email: str
    display_name: str
    role: Role
    permissions: list[Permission]
    preferred_language_code: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    is_active: bool = True
    must_change_password: bool = False


class LoginResponse(BaseModel):
    """Login response that includes both tokens and user profile."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    id: str
    company_id: str
    email: str
    display_name: str
    role: Role
    permissions: list[Permission]
    preferred_language_code: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    is_active: bool = True
    must_change_password: bool = False


class BootstrapStatus(BaseModel):
    needs_first_company: bool
    needs_first_admin: bool
    is_ready: bool


class FirstCompanyCreate(BaseModel):
    legal_name: str
    trading_name: str | None = None
    legal_type: str | None = None
    default_country: str = "GB"
    default_currency: str = "GBP"
    timezone: str = "Europe/London"
    phone: str | None = None
    website: str | None = None
    workspace_mode: str = "single_company"
    industry_group: str | None = None
    industry_subtype: str | None = None
    default_internal_language_code: str = "en-GB"
    default_customer_language_code: str = "en-GB"


class FirstAdminCreate(BaseModel):
    company_id: str
    email: str
    display_name: str
    password: str = Field(min_length=12)
    preferred_language_code: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


class FirstInstallCreate(BaseModel):
    company_name: str = Field(min_length=1)
    company_legal_type: str | None = None
    country: str = "GB"
    timezone: str = "Europe/London"
    currency: str = "GBP"
    default_internal_language_code: str = "en-GB"
    default_customer_language_code: str = "en-GB"
    workspace_mode: str = "single_company"
    primary_industry: str | None = None
    primary_subtype: str | None = None
    selected_industries: list[str] = Field(default_factory=list)
    selected_subtypes: list[str] = Field(default_factory=list)
    selected_activities: list[str] = Field(default_factory=list)
    industry_group: str | None = None
    industry_subtype: str | None = None
    selected_languages: list[str] = Field(default_factory=list)
    voice_input_language_codes: list[str] = Field(default_factory=list)
    voice_output_language_codes: list[str] = Field(default_factory=list)
    first_admin_display_name: str = Field(min_length=1)
    first_admin_email: str = Field(min_length=1)
    first_admin_password: str = Field(min_length=12)
    first_admin_first_name: str | None = None
    first_admin_last_name: str | None = None
    phone: str | None = None
    website: str | None = None


class FirstInstallResult(BaseModel):
    company: CompanyProfile
    admin: UserAccount
    bootstrap_status: BootstrapStatus


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    email: str
    password: str
    display_name: str
    role: str = "worker"
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    preferred_language_code: str | None = None


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    preferred_language_code: str | None = None
    is_active: bool | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    new_password: str


class AssistantMemoryItem(BaseModel):
    """Permanent assistant memory entry ("zapamatuj si")."""
    id: str
    memory_type: str = "long"
    content: str
    updated_at: datetime | None = None


class AssistantMemoryCreate(BaseModel):
    content: str
    memory_type: str = "long"


class ActivityLogEntry(BaseModel):
    """Admin-visible activity log entry (who did what, when, via which channel)."""
    id: str
    entity_type: str = ""
    entity_id: str = ""
    action: str = ""
    description: str = ""
    source_channel: str = "app"
    created_at: datetime | None = None
    actor_user_id: str | None = None
    actor_display_name: str = ""
    actor_email: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class PasswordResetToken(BaseModel):
    id: str
    user_id: str
    email: str
    token_hash: str
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime


class TenantIndustryProfile(BaseModel):
    company_id: str
    primary_industry: str | None = None
    primary_subtype: str | None = None
    selected_industries: list[str] = Field(default_factory=list)
    selected_subtypes: list[str] = Field(default_factory=list)
    selected_activities: list[str] = Field(default_factory=list)


class TenantIndustry(BaseModel):
    """A single industry assigned to a tenant (Phase A1 multi-industry)."""
    industry_code: str
    subtype_code: str | None = None
    is_primary: bool = False


class TenantIndustriesUpdate(BaseModel):
    """Request body for setting the full list of a tenant's industries."""
    industries: list[TenantIndustry] = Field(default_factory=list)


class CalendarEvent(BaseModel):
    """Phase A3: a backend-stored calendar event."""
    id: str
    company_id: str
    title: str
    description: str | None = None
    location: str | None = None
    start_at: datetime
    end_at: datetime | None = None
    all_day: bool = False
    client_id: str | None = None
    job_id: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class CalendarEventCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str | None = None
    location: str | None = None
    start_at: datetime
    end_at: datetime | None = None
    all_day: bool = False
    client_id: str | None = None
    job_id: str | None = None


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None
    client_id: str | None = None
    job_id: str | None = None


class LanguageDefinition(BaseModel):
    code: str
    name: str
    native_name: str


class TenantLanguage(BaseModel):
    company_id: str
    language_code: str
    language_scope: LanguageScope
    is_enabled: bool = True
    is_default: bool = False


class TenantLanguageChoice(BaseModel):
    language_code: str
    language_scope: LanguageScope
    is_enabled: bool = True
    is_default: bool = False


class TenantLanguageUpdate(BaseModel):
    languages: list[TenantLanguageChoice]


class LanguageSettings(BaseModel):
    internal_language_mode: LanguageMode = LanguageMode.single
    customer_language_mode: LanguageMode = LanguageMode.multilingual
    default_internal_language_code: str = "en-GB"
    default_customer_language_code: str = "en-GB"
    voice_input_strategy: VoiceLanguageStrategy = VoiceLanguageStrategy.detect_from_context
    voice_output_strategy: VoiceLanguageStrategy = VoiceLanguageStrategy.client_preferred
    auto_translate_customer_to_internal: bool = True
    auto_translate_internal_to_customer: bool = True


class ClientLanguageSettings(BaseModel):
    client_id: str
    preferred_language_code: str
    resolved_language_code: str
    source: str = "client_preferred"


class ClientLanguageUpdate(BaseModel):
    preferred_language_code: str


class LanguageContext(BaseModel):
    internal_language_code: str
    customer_language_code: str
    voice_input_language_code: str
    voice_output_language_code: str
    translate_customer_to_internal: bool
    translate_internal_to_customer: bool
    resolution_source: str


class TenantActivityPricing(BaseModel):
    company_id: str
    activity_code: str
    is_active: bool = True
    selected_pricing_method_code: str
    rate: float | None = None
    custom_name: str | None = None
    enabled_additional_charge_codes: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class TenantActivityOverrideRequest(BaseModel):
    selected_pricing_method_code: str
    rate: float | None = None
    custom_name: str | None = None
    enabled_additional_charge_codes: list[str] = Field(default_factory=list)


class CRMRecord(BaseModel):
    id: str
    company_id: str
    name: str
    status: str = "open"
    data: dict[str, Any] = Field(default_factory=dict)
    preferred_language_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CRMCreateRequest(BaseModel):
    """Generic create payload for any CRM module record."""
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class CRMUpdateRequest(BaseModel):
    """Generic update payload for any CRM module record."""
    name: str | None = None
    status: str | None = None
    data: dict[str, Any] | None = None


class NoteCreateRequest(BaseModel):
    """Append a timestamped note to a CRM record data notes list."""
    content: str = Field(min_length=1)
    author_name: str | None = None


class WorkReportWorker(BaseModel):
    worker_name: str
    hours: float = 0
    hourly_rate: float = 0
    # Internal cost per hour; missing cost counts as 0 — same semantics as the
    # original profit calculation in commit 440aa04.
    hourly_cost: float = 0


class WorkReportActivity(BaseModel):
    """A catalogue activity performed, with its price. Quantity is in the unit
    of the pricing method (hours, m², visits, items…). The app sends the rate it
    showed the user (tenant override or Oxfordshire default); the backend falls
    back to the tenant pricing override when rate is missing."""
    activity_code: str
    name: str | None = None
    quantity: float = 1
    rate: float = 0
    pricing_method: str | None = None
    unit: str | None = None


class WorkReportEntry(BaseModel):
    """A single line item in a work report (type of work performed)."""
    entry_type: str = "work"
    hours: float = 0
    unit_rate: float = 0
    description: str | None = None


class WorkReportMaterial(BaseModel):
    material_name: str
    quantity: float = 1
    unit_price: float = 0


class WorkReportWaste(BaseModel):
    description: str = "waste disposal"
    quantity: float = 1
    unit_price: float = 0


class WorkReportCreate(BaseModel):
    """Request body for POST /work-reports."""
    job_id: str | None = None
    client_id: str | None = None
    work_date: str | None = None
    total_hours: float = 0
    total_price: float = 0
    currency: str = "GBP"
    notes: str | None = None
    input_type: str = "manual"
    workers: list[WorkReportWorker] = Field(default_factory=list)
    activities: list[WorkReportActivity] = Field(default_factory=list)
    entries: list[WorkReportEntry] = Field(default_factory=list)
    materials: list[WorkReportMaterial] = Field(default_factory=list)
    waste: list[WorkReportWaste] = Field(default_factory=list)


class InvoiceFromWorkReportRequest(BaseModel):
    """Request body for POST /crm/invoices/from-work-report."""
    work_report_id: str
    due_date: str | None = None


class VoiceResolveRequest(BaseModel):
    utterance: str
    company_id: str | None = None
    client_id: str | None = None


class VoiceResolveResult(BaseModel):
    utterance: str
    resolved_intent: str | None
    confidence: float
    requires_confirmation: bool = True
    reason: str
    language_context: LanguageContext | None = None


class PendingVoiceAction(BaseModel):
    """A multi-turn voice action awaiting more info (Phase A5.2)."""
    id: str
    company_id: str
    user_id: str | None = None
    intent: str
    status: str = "needs_more_info"
    collected_data: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    last_question: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class VoiceExecuteRequest(BaseModel):
    utterance: str
    confirmed: bool = False
    client_id: str | None = None
    pending_action_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class VoiceExecuteResult(BaseModel):
    executed: bool
    resolved_intent: str | None
    requires_confirmation: bool
    message: str
    action: str | None = None           # e.g. "calendar.create"
    entity_id: str | None = None        # id of created/updated/deleted entity
    data: dict[str, Any] = Field(default_factory=dict)  # extracted entities / result payload
    status: str = "executed"  # executed|needs_more_info|cancelled|error
    missing_fields: list[str] = Field(default_factory=list)
    question: str | None = None
    pending_action_id: str | None = None
    language_context: LanguageContext | None = None


class CalendarSyncEventInput(BaseModel):
    """One event coming FROM the Android device during sync."""
    android_id: str | None = None       # device-local id (for mapping back)
    backend_id: str | None = None       # if known, the backend event id
    title: str
    description: str | None = None
    location: str | None = None
    start_at: datetime
    end_at: datetime | None = None
    all_day: bool = False
    updated_at: datetime | None = None  # device last-modified, for conflict resolution


class CalendarSyncRequest(BaseModel):
    events: list[CalendarSyncEventInput] = Field(default_factory=list)


class CalendarSyncOutcome(BaseModel):
    backend_id: str | None = None
    android_id: str | None = None
    action: str                          # created_on_backend|updated_backend|updated_android|created_on_android|conflict_backend_wins|conflict_android_wins|noop
    source: str                          # backend|android|android_import
    status: str = "ok"
    detail: str | None = None


class CalendarSyncResult(BaseModel):
    """Returned to the device: what it must apply locally + what backend did."""
    outcomes: list[CalendarSyncOutcome] = Field(default_factory=list)
    backend_events: list[CalendarEvent] = Field(default_factory=list)  # full current truth


class CalendarSyncLogEntry(BaseModel):
    id: str
    company_id: str
    event_id: str | None = None
    source: str
    action: str
    status: str
    detail: str | None = None
    created_at: datetime


class BiometricRegisterRequest(BaseModel):
    """Register a fingerprint hash for the calling user on a specific device."""
    device_id: str = Field(min_length=1)
    biometric_hash: str = Field(min_length=16, description="Salted SHA-256 of device biometric template")
    label: str | None = None


class BiometricEntry(BaseModel):
    id: str
    user_id: str
    device_id: str
    label: str | None = None
    is_active: bool
    created_at: datetime


class BackupScope(str, Enum):
    full = "full"
    personal = "personal"


class BackupStorageLocation(str, Enum):
    server = "server"
    local = "local"
    both = "both"


class BackupCreateRequest(BaseModel):
    """Request to create a pre-uninstall backup."""
    storage_location: BackupStorageLocation = BackupStorageLocation.both
    device_id: str = Field(min_length=1)


class BackupUserCredential(BaseModel):
    """Minimal credential record included in a backup."""
    user_id: str
    email: str
    display_name: str
    role: str
    biometric_hashes: list[str] = Field(default_factory=list)


class BackupManifest(BaseModel):
    """Backup payload returned to (and stored by) the Android client."""
    backup_id: str
    backup_version: str = "1.0"
    created_at: datetime
    created_by_user_id: str
    created_by_role: str
    backup_scope: BackupScope
    company_id: str
    company_legal_name: str
    users: list[BackupUserCredential]
    settings: dict[str, Any] = Field(default_factory=dict)
    db_reference: str | None = None
    restore_token: str | None = None
    restore_token_expires_at: datetime | None = None


class BackupRestoreInfo(BaseModel):
    """Minimal info the Android app needs to start a restore flow."""
    backup_id: str
    company_legal_name: str
    created_at: datetime
    backup_scope: BackupScope
    includes_db_reference: bool


class GoogleCalendarAccount(BaseModel):
    """Google Calendar OAuth account for a company (Phase G3). Backend-owned.
    Tokens are never serialized to clients — only status-level fields are exposed."""
    id: str
    company_id: str
    google_account_email: str | None = None
    google_calendar_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    scope: str | None = None
    status: str = "disconnected"  # disconnected|connected|needs_reauth
    auto_sync_enabled: bool = False
    last_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
