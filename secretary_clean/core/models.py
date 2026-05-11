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
    },
    Role.manager: {
        Permission.catalogue_read,
        Permission.pricing_manage,
        Permission.crm_manage,
        Permission.language_manage,
        Permission.voice_execute,
    },
    Role.staff: {Permission.catalogue_read, Permission.crm_manage, Permission.voice_execute},
    Role.accountant: {Permission.catalogue_read, Permission.crm_manage},
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
    internal_company_language: str = "en-GB"
    default_customer_language: str = "en-GB"
    workspace_mode: str = "single_company"
    industry_group: str | None = None
    industry_subtype: str | None = None
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


class VoiceExecuteRequest(BaseModel):
    utterance: str
    confirmed: bool = False
    client_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class VoiceExecuteResult(BaseModel):
    executed: bool
    resolved_intent: str | None
    requires_confirmation: bool
    message: str
    language_context: LanguageContext | None = None
