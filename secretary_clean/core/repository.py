"""Repository contracts and safe in-memory implementation for foundation tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .models import (
    BootstrapStatus,
    ClientLanguageSettings,
    CompanyLegalIdentity,
    CompanyOperatingSettings,
    CompanyProfile,
    CRMRecord,
    FirstCompanyCreate,
    FirstInstallCreate,
    FirstInstallResult,
    LanguageScope,
    LanguageSettings,
    PasswordResetToken,
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantActivityOverrideRequest,
    TenantActivityPricing,
    TenantIndustryProfile,
    TenantLanguage,
    TenantLanguageChoice,
    TenantOperatingProfile,
    UserAccount,
)
from .language import normalize_language_code
from .security import hash_password, hash_reset_token, reset_token_expiry, verify_password


class InMemorySecretaryRepository:
    def __init__(self) -> None:
        self.password_reset_tokens: dict[str, PasswordResetToken] = {}
        self.companies: dict[str, CompanyProfile] = {}
        self.company_settings: dict[str, CompanyOperatingSettings] = {}
        self.tenant_operating_profiles: dict[str, TenantOperatingProfile] = {}
        self.tenant_languages: dict[tuple[str, LanguageScope, str], TenantLanguage] = {}
        self.tenant_configuration: dict[str, dict] = {}
        self.users: dict[str, UserAccount] = {}
        self.password_hashes: dict[str, str] = {}
        self.tenant_industry_profiles: dict[str, TenantIndustryProfile] = {}
        self.tenant_pricing: dict[tuple[str, str], TenantActivityPricing] = {}
        self.crm: dict[str, dict[str, CRMRecord]] = {
            name: {}
            for name in (
                "clients", "jobs", "tasks", "quotes",
                "invoices", "communications", "work_reports",
            )
        }

    def bootstrap_status(self) -> BootstrapStatus:
        needs_first_company = not self.companies
        needs_first_admin = not any(user.role == Role.owner for user in self.users.values())
        return BootstrapStatus(
            needs_first_company=needs_first_company,
            needs_first_admin=needs_first_admin,
            is_ready=not needs_first_company and not needs_first_admin,
        )

    def create_first_company(self, payload: FirstCompanyCreate) -> CompanyProfile:
        if self.companies:
            raise ValueError("First company already exists")
        payload_data = payload.model_dump()
        default_internal_language_code = normalize_language_code(
            payload_data.pop("default_internal_language_code", None)
        )
        default_customer_language_code = normalize_language_code(
            payload_data.pop("default_customer_language_code", None)
        )
        workspace_mode = payload_data.pop("workspace_mode", "single_company")
        industry_group = payload_data.get("industry_group")
        industry_subtype = payload_data.get("industry_subtype")
        company = CompanyProfile(id=str(uuid4()), **payload_data)
        self.companies[company.id] = company
        self.company_settings[company.id] = CompanyOperatingSettings(workspace_mode=workspace_mode)
        self.tenant_operating_profiles[company.id] = TenantOperatingProfile(
            company_id=company.id,
            workspace_mode=workspace_mode,
            industry_group=industry_group,
            industry_subtype=industry_subtype,
            default_internal_language_code=default_internal_language_code,
            default_customer_language_code=default_customer_language_code,
        )
        self.tenant_configuration[company.id] = {
            "workspace_mode": workspace_mode,
            "industry_group": industry_group,
            "industry_subtype": industry_subtype,
            "phone": company.phone,
            "website": company.website,
        }
        self._seed_default_languages(company.id, default_internal_language_code, default_customer_language_code)
        return company

    def create_first_admin(self, *, company_id: str, email: str, display_name: str, password: str,
                           preferred_language_code: str | None = None, first_name: str | None = None,
                           last_name: str | None = None, phone: str | None = None) -> UserAccount:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        if any(user.role == Role.owner for user in self.users.values()):
            raise ValueError("First admin already exists")
        user = UserAccount(
            id=str(uuid4()),
            company_id=company_id,
            email=email.lower(),
            display_name=display_name,
            role=Role.owner,
            permissions=sorted(ROLE_PERMISSIONS[Role.owner]),
            preferred_language_code=normalize_language_code(preferred_language_code) if preferred_language_code else None,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
        )
        self.users[user.id] = user
        self.password_hashes[user.id] = hash_password(password)
        return user

    def create_first_install(
        self,
        payload: FirstInstallCreate,
        *,
        activity_defaults: dict[str, str] | None = None,
    ) -> FirstInstallResult:
        """Create first company and admin. Optionally seed tenant_activity_pricing.

        activity_defaults maps activity_code -> default_pricing_method_code,
        sourced from the catalogue loaded at startup.
        """
        if self.companies:
            raise ValueError("First company already exists")
        if any(user.role == Role.owner for user in self.users.values()):
            raise ValueError("First admin already exists")
        industry_group = payload.primary_industry or (payload.selected_industries[0] if payload.selected_industries else None)
        industry_subtype = payload.primary_subtype or (payload.selected_subtypes[0] if payload.selected_subtypes else None)
        company = self.create_first_company(
            FirstCompanyCreate(
                legal_name=payload.company_name,
                trading_name=payload.company_name,
                legal_type=payload.company_legal_type,
                default_country=payload.country or "GB",
                default_currency=payload.currency or "GBP",
                timezone=payload.timezone or "Europe/London",
                phone=payload.phone,
                website=payload.website,
                workspace_mode=payload.workspace_mode or "single_company",
                industry_group=industry_group,
                industry_subtype=industry_subtype,
                default_internal_language_code=payload.default_internal_language_code or "en-GB",
                default_customer_language_code=payload.default_customer_language_code or "en-GB",
            )
        )
        admin = self.create_first_admin(
            company_id=company.id,
            email=payload.first_admin_email,
            display_name=payload.first_admin_display_name,
            password=payload.first_admin_password,
            preferred_language_code=payload.default_internal_language_code or "en-GB",
            first_name=payload.first_admin_first_name,
            last_name=payload.first_admin_last_name,
            phone=payload.phone,
        )
        # Store selected industry/subtype profile
        self.tenant_industry_profiles[company.id] = TenantIndustryProfile(
            company_id=company.id,
            selected_industry_codes=list(payload.selected_industries),
            selected_subtype_codes=list(payload.selected_subtypes),
            primary_industry_code=payload.primary_industry,
            primary_subtype_code=payload.primary_subtype,
        )
        # Seed tenant_activity_pricing for each selected activity using system defaults
        if payload.selected_activities and activity_defaults:
            now = datetime.now(timezone.utc)
            for activity_code in payload.selected_activities:
                default_method = activity_defaults.get(activity_code)
                if default_method:
                    key = (company.id, activity_code)
                    self.tenant_pricing[key] = TenantActivityPricing(
                        company_id=company.id,
                        activity_code=activity_code,
                        is_active=True,
                        selected_pricing_method_code=default_method,
                        updated_at=now,
                    )
        return FirstInstallResult(
            company=company,
            admin=admin,
            bootstrap_status=self.bootstrap_status(),
        )

    def authenticate(self, email: str, password: str) -> UserAccount | None:
        normalized = email.lower()
        for user in self.users.values():
            if user.email == normalized and user.is_active:
                if verify_password(password, self.password_hashes[user.id]):
                    return user
        return None

    def get_user(self, user_id: str) -> UserAccount | None:
        return self.users.get(user_id)

    def get_user_by_email(self, email: str) -> UserAccount | None:
        normalized = email.lower()
        return next((u for u in self.users.values() if u.email == normalized), None)

    def list_roles(self) -> dict[str, list[str]]:
        return {role.value: sorted(p.value for p in permissions) for role, permissions in ROLE_PERMISSIONS.items()}

    def get_company(self, company_id: str) -> CompanyProfile | None:
        return self.companies.get(company_id)

    def update_company(self, company_id: str, profile: CompanyProfile) -> CompanyProfile:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        self.companies[company_id] = profile.model_copy(update={"id": company_id})
        return self.companies[company_id]

    def get_company_settings(self, company_id: str) -> CompanyOperatingSettings:
        return self.company_settings.setdefault(company_id, CompanyOperatingSettings())

    def update_company_settings(self, company_id: str, settings: CompanyOperatingSettings) -> CompanyOperatingSettings:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        self.company_settings[company_id] = settings
        profile = self.get_tenant_operating_profile(company_id)
        self.tenant_operating_profiles[company_id] = profile.model_copy(update={"workspace_mode": settings.workspace_mode})
        return settings

    def get_company_legal_identity(self, company_id: str) -> CompanyLegalIdentity:
        company = self.companies[company_id]
        return CompanyLegalIdentity(
            legal_name=company.legal_name,
            trading_name=company.trading_name,
            legal_type=company.legal_type,
            registered_address=None,
            phone=company.phone,
            website=company.website,
        )

    def update_company_legal_identity(self, company_id: str, identity: CompanyLegalIdentity) -> CompanyLegalIdentity:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        current = self.companies[company_id]
        self.companies[company_id] = current.model_copy(update={
            "legal_name": identity.legal_name,
            "trading_name": identity.trading_name,
            "legal_type": identity.legal_type,
            "phone": identity.phone,
            "website": identity.website,
        })
        return identity

    def get_tenant_operating_profile(self, company_id: str) -> TenantOperatingProfile:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        return self.tenant_operating_profiles.setdefault(
            company_id, TenantOperatingProfile(company_id=company_id)
        )

    def update_tenant_operating_profile(self, company_id: str, settings: LanguageSettings) -> TenantOperatingProfile:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        current = self.get_tenant_operating_profile(company_id)
        profile = current.model_copy(update=settings.model_dump())
        profile.default_internal_language_code = normalize_language_code(profile.default_internal_language_code)
        profile.default_customer_language_code = normalize_language_code(profile.default_customer_language_code)
        self.tenant_operating_profiles[company_id] = profile
        self._seed_default_languages(
            company_id, profile.default_internal_language_code, profile.default_customer_language_code
        )
        return profile

    def _seed_default_languages(self, company_id: str, internal_code: str, customer_code: str) -> None:
        for scope, code in (
            (LanguageScope.internal, internal_code),
            (LanguageScope.customer, customer_code),
            (LanguageScope.voice_input, customer_code),
            (LanguageScope.voice_output, customer_code),
        ):
            key = (company_id, scope, code)
            self.tenant_languages[key] = TenantLanguage(
                company_id=company_id,
                language_code=code,
                language_scope=scope,
                is_enabled=True,
                is_default=True,
            )

    def list_tenant_languages(self, company_id: str) -> list[TenantLanguage]:
        return [lang for (tid, _, _), lang in self.tenant_languages.items() if tid == company_id]

    def replace_tenant_languages(self, company_id: str, languages: list[TenantLanguageChoice]) -> list[TenantLanguage]:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        self.tenant_languages = {k: v for k, v in self.tenant_languages.items() if k[0] != company_id}
        for language in languages:
            normalized = TenantLanguage(
                company_id=company_id,
                language_code=normalize_language_code(language.language_code),
                language_scope=language.language_scope,
                is_enabled=language.is_enabled,
                is_default=language.is_default,
            )
            self.tenant_languages[(company_id, normalized.language_scope, normalized.language_code)] = normalized
        return self.list_tenant_languages(company_id)

    def get_client_language(self, company_id: str, client_id: str):
        from .models import ClientLanguageSettings
        record = self.crm["clients"].get(client_id)
        if not record or record.company_id != company_id:
            raise KeyError("Client not found")
        profile = self.get_tenant_operating_profile(company_id)
        preferred = normalize_language_code(record.preferred_language_code, profile.default_customer_language_code)
        return ClientLanguageSettings(client_id=client_id, preferred_language_code=preferred, resolved_language_code=preferred)

    def set_client_language(self, company_id: str, client_id: str, language_code: str):
        from .models import ClientLanguageSettings
        record = self.crm["clients"].get(client_id)
        if not record or record.company_id != company_id:
            raise KeyError("Client not found")
        normalized = normalize_language_code(language_code, self.get_tenant_operating_profile(company_id).default_customer_language_code)
        self.crm["clients"][client_id] = record.model_copy(update={"preferred_language_code": normalized})
        return ClientLanguageSettings(client_id=client_id, preferred_language_code=normalized, resolved_language_code=normalized)

    def get_client_preferred_language_code(self, company_id: str, client_id: str | None) -> str | None:
        if not client_id:
            return None
        record = self.crm["clients"].get(client_id)
        if not record or record.company_id != company_id:
            return None
        return record.preferred_language_code

    def list_users(self, company_id: str) -> list[UserAccount]:
        return [u for u in self.users.values() if u.company_id == company_id]

    def save_tenant_pricing(self, company_id: str, activity_code: str, request: TenantActivityOverrideRequest) -> TenantActivityPricing:
        override = TenantActivityPricing(
            company_id=company_id,
            activity_code=activity_code,
            selected_pricing_method_code=request.selected_pricing_method_code,
            rate=request.rate,
            custom_name=request.custom_name,
            enabled_additional_charge_codes=request.enabled_additional_charge_codes,
            updated_at=datetime.now(timezone.utc),
        )
        self.tenant_pricing[(company_id, activity_code)] = override
        return override

    def reset_tenant_pricing(self, company_id: str, activity_code: str) -> bool:
        self.tenant_pricing.pop((company_id, activity_code), None)
        return True

    def list_tenant_pricing(self, company_id: str) -> list[TenantActivityPricing]:
        return [item for (tenant, _), item in self.tenant_pricing.items() if tenant == company_id]

    def create_crm_record(self, module: str, company_id: str, name: str, data: dict) -> CRMRecord:
        if module not in self.crm:
            raise KeyError("Unknown CRM module")
        record = CRMRecord(id=str(uuid4()), company_id=company_id, name=name, data=data)
        self.crm[module][record.id] = record
        return record

    def list_crm_records(self, module: str, company_id: str) -> list[CRMRecord]:
        if module not in self.crm:
            raise KeyError("Unknown CRM module")
        return [r for r in self.crm[module].values() if r.company_id == company_id]

    # ── Password reset ──────────────────────────────────────────────────────

    def create_password_reset_token(self, user: UserAccount, plain_token: str) -> PasswordResetToken:
        token = PasswordResetToken(
            id=str(uuid4()),
            user_id=user.id,
            email=user.email,
            token_hash=hash_reset_token(plain_token),
            expires_at=reset_token_expiry(),
            used_at=None,
            created_at=datetime.now(timezone.utc),
        )
        self.password_reset_tokens[token.id] = token
        return token

    def verify_password_reset_token(self, plain_token: str) -> PasswordResetToken | None:
        """Return the token record if valid, unexpired, and unused."""
        token_hash = hash_reset_token(plain_token)
        now = datetime.now(timezone.utc)
        for token in self.password_reset_tokens.values():
            expires = token.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if token.token_hash == token_hash and token.used_at is None and expires > now:
                return token
        return None

    def mark_password_reset_token_used(self, token_id: str) -> None:
        if token_id in self.password_reset_tokens:
            existing = self.password_reset_tokens[token_id]
            self.password_reset_tokens[token_id] = existing.model_copy(
                update={"used_at": datetime.now(timezone.utc)}
            )

    def reset_user_password(self, user_id: str, new_password: str) -> None:
        if user_id not in self.users:
            raise KeyError("User not found")
        self.password_hashes[user_id] = hash_password(new_password)

    def admin_recovery_reset_password(self, email: str, new_password: str) -> UserAccount:
        """Reset password for owner/admin by email. Emergency recovery only."""
        user = self.get_user_by_email(email)
        if not user:
            raise KeyError("User not found")
        if user.role not in (Role.owner, Role.admin):
            raise PermissionError("Recovery is only available for owner/admin accounts")
        self.reset_user_password(user.id, new_password)
        return user
