"""Repository contracts and safe in-memory implementation for foundation tests.

The clean API talks to repository methods rather than legacy global database
helpers. A production adapter can implement the same methods against Postgres.
"""

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
    CRMUpdateRequest,
    FirstCompanyCreate,
    FirstInstallCreate,
    FirstInstallResult,
    InvoiceFromWorkReportRequest,
    LanguageScope,
    LanguageSettings,
    NoteCreateRequest,
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantActivityOverrideRequest,
    TenantActivityPricing,
    TenantLanguage,
    TenantLanguageChoice,
    TenantOperatingProfile,
    TenantIndustry,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarSyncEventInput,
    CalendarSyncOutcome,
    CalendarSyncLogEntry,
    PendingVoiceAction,
    UserAccount,
    WorkReportCreate,
)
from .language import normalize_language_code
from .security import hash_password, verify_password


class InMemorySecretaryRepository:
    def __init__(self) -> None:
        self.companies: dict[str, CompanyProfile] = {}
        self.company_settings: dict[str, CompanyOperatingSettings] = {}
        self.tenant_operating_profiles: dict[str, TenantOperatingProfile] = {}
        self.tenant_industries: dict[str, list] = {}  # Phase A1: company_id -> list[TenantIndustry]
        self.calendar_events: dict[str, CalendarEvent] = {}  # Phase A3: event_id -> CalendarEvent
        self.calendar_sync_log: list = []  # Phase A5: list[CalendarSyncLogEntry]
        self.pending_voice_actions: dict[str, PendingVoiceAction] = {}  # Phase A5.2
        self.tenant_languages: dict[tuple[str, LanguageScope, str], TenantLanguage] = {}
        self.tenant_configuration: dict[str, dict] = {}
        self.users: dict[str, UserAccount] = {}
        self.password_hashes: dict[str, str] = {}
        self.tenant_pricing: dict[tuple[str, str], TenantActivityPricing] = {}
        self.crm: dict[str, dict[str, CRMRecord]] = {
            name: {}
            for name in (
                "clients",
                "jobs",
                "tasks",
                "quotes",
                "invoices",
                "communications",
                "work_reports",
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

    def create_first_admin(self, *, company_id: str, email: str, display_name: str, password: str, preferred_language_code: str | None = None, first_name: str | None = None, last_name: str | None = None, phone: str | None = None) -> UserAccount:
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

    def create_first_install(self, payload: FirstInstallCreate, *, activity_defaults: dict | None = None) -> FirstInstallResult:
        if self.companies:
            raise ValueError("First company already exists")
        if any(user.role == Role.owner for user in self.users.values()):
            raise ValueError("First admin already exists")
        company = self.create_first_company(
            FirstCompanyCreate(
                legal_name=payload.company_name,
                trading_name=payload.company_name,
                legal_type=payload.company_legal_type,
                default_country=payload.country,
                default_currency=payload.currency,
                timezone=payload.timezone,
                phone=payload.phone,
                website=payload.website,
                workspace_mode=payload.workspace_mode,
                industry_group=payload.primary_industry or (payload.selected_industries[0] if payload.selected_industries else payload.industry_group),
                industry_subtype=payload.primary_subtype or (payload.selected_subtypes[0] if payload.selected_subtypes else payload.industry_subtype),
                default_internal_language_code=payload.default_internal_language_code,
                default_customer_language_code=payload.default_customer_language_code,
            )
        )
        admin = self.create_first_admin(
            company_id=company.id,
            email=payload.first_admin_email,
            display_name=payload.first_admin_display_name,
            password=payload.first_admin_password,
            preferred_language_code=payload.default_internal_language_code,
            first_name=payload.first_admin_first_name,
            last_name=payload.first_admin_last_name,
            phone=payload.phone,
        )
        # Phase A1: persist ALL selected industries (multi-industry), not just primary.
        resolved_primary = payload.primary_industry or (
            payload.selected_industries[0] if payload.selected_industries else payload.industry_group
        )
        resolved_subtype = payload.primary_subtype or (
            payload.selected_subtypes[0] if payload.selected_subtypes else payload.industry_subtype
        )
        all_industries = list(payload.selected_industries) if payload.selected_industries else []
        if resolved_primary and resolved_primary not in all_industries:
            all_industries.insert(0, resolved_primary)
        if all_industries:
            self.set_tenant_industries(
                company.id,
                [
                    TenantIndustry(
                        industry_code=code,
                        subtype_code=resolved_subtype if code == resolved_primary else None,
                        is_primary=(code == resolved_primary),
                    )
                    for code in all_industries
                ],
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

    def create_user(self, company_id: str, email: str, password: str, display_name: str,
                    role: str = "worker", first_name: str | None = None,
                    last_name: str | None = None, phone: str | None = None,
                    preferred_language_code: str | None = None) -> UserAccount:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        if any(u.email == email.lower() for u in self.users.values()):
            raise ValueError("Email already exists")
        try:
            user_role = Role(role)
        except ValueError:
            user_role = Role.worker
        user = UserAccount(
            id=str(uuid4()),
            company_id=company_id,
            email=email.lower(),
            display_name=display_name,
            role=user_role,
            permissions=sorted(ROLE_PERMISSIONS[user_role]),
            preferred_language_code=normalize_language_code(preferred_language_code) if preferred_language_code else None,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            must_change_password=True,
        )
        self.users[user.id] = user
        self.password_hashes[user.id] = hash_password(password)
        return user

    def update_user(self, user_id: str, company_id: str, **fields) -> UserAccount:
        user = self.users.get(user_id)
        if not user or user.company_id != company_id:
            raise KeyError("User not found")
        updates = {}
        if "display_name" in fields and fields["display_name"] is not None:
            updates["display_name"] = fields["display_name"]
        if "role" in fields and fields["role"] is not None:
            try:
                new_role = Role(fields["role"])
                updates["role"] = new_role
                updates["permissions"] = sorted(ROLE_PERMISSIONS[new_role])
            except ValueError:
                pass
        for k in ("first_name", "last_name", "phone", "preferred_language_code", "is_active"):
            if k in fields and fields[k] is not None:
                updates[k] = fields[k]
        updated = user.model_copy(update=updates)
        self.users[user_id] = updated
        return updated

    def delete_user(self, user_id: str, company_id: str) -> bool:
        user = self.users.get(user_id)
        if not user or user.company_id != company_id:
            raise KeyError("User not found")
        updated = user.model_copy(update={"is_active": False})
        self.users[user_id] = updated
        return True

    def change_password(self, user_id: str, current_password: str, new_password: str) -> bool:
        user = self.users.get(user_id)
        if not user:
            raise KeyError("User not found")
        if not verify_password(current_password, self.password_hashes.get(user_id, "")):
            return False
        self.password_hashes[user_id] = hash_password(new_password)
        updated = user.model_copy(update={"must_change_password": False})
        self.users[user_id] = updated
        return True

    def reset_user_password(self, user_id: str, new_password: str) -> None:
        user = self.users.get(user_id)
        if not user:
            raise KeyError("User not found")
        self.password_hashes[user_id] = hash_password(new_password)
        updated = user.model_copy(update={"must_change_password": True})
        self.users[user_id] = updated

    def wipe_all_data(self) -> None:
        """Delete ALL tenant/user/company data. After this call bootstrap_status returns is_ready=False."""
        self.companies.clear()
        self.company_settings.clear()
        self.tenant_operating_profiles.clear()
        self.tenant_languages.clear()
        self.tenant_configuration.clear()
        self.users.clear()
        self.password_hashes.clear()
        self.tenant_pricing.clear()
        for module_dict in self.crm.values():
            module_dict.clear()

    def list_roles(self) -> dict[str, list[str]]:
        return {role.value: sorted(permission.value for permission in permissions) for role, permissions in ROLE_PERMISSIONS.items()}

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
        self.companies[company_id] = current.model_copy(
            update={
                "legal_name": identity.legal_name,
                "trading_name": identity.trading_name,
                "legal_type": identity.legal_type,
                "phone": identity.phone,
                "website": identity.website,
            }
        )
        return identity

    def update_company_industry(self, company_id: str, industry_group: str | None, industry_subtype: str | None) -> TenantOperatingProfile:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        current = self.get_tenant_operating_profile(company_id)
        updated = current.model_copy(update={
            "industry_group": industry_group,
            "industry_subtype": industry_subtype,
        })
        self.tenant_operating_profiles[company_id] = updated
        cfg = self.tenant_configuration.setdefault(company_id, {})
        cfg["industry_group"] = industry_group
        cfg["industry_subtype"] = industry_subtype
        # Phase A1: keep multi-industry list in sync with the legacy single field.
        if industry_group:
            self.tenant_industries[company_id] = [
                TenantIndustry(
                    industry_code=industry_group,
                    subtype_code=industry_subtype,
                    is_primary=True,
                )
            ]
        return updated

    def get_tenant_industries(self, company_id: str) -> list[TenantIndustry]:
        """Phase A1: return all industries for a tenant.

        Falls back to the legacy single industry_group when no multi-industry
        rows exist yet (backward compatibility with old data)."""
        if company_id not in self.companies:
            raise KeyError("Company not found")
        existing = self.tenant_industries.get(company_id)
        if existing:
            return list(existing)
        # Backward compat: synthesize from legacy single field
        profile = self.get_tenant_operating_profile(company_id)
        if profile.industry_group:
            return [
                TenantIndustry(
                    industry_code=profile.industry_group,
                    subtype_code=profile.industry_subtype,
                    is_primary=True,
                )
            ]
        return []

    def set_tenant_industries(
        self, company_id: str, industries: list[TenantIndustry]
    ) -> list[TenantIndustry]:
        """Phase A1: replace the full set of industries for a tenant.

        Ensures exactly one primary (first one if none flagged) and mirrors the
        primary back into the legacy industry_group/subtype fields for
        compatibility with older readers."""
        if company_id not in self.companies:
            raise KeyError("Company not found")

        # Deduplicate by industry_code, preserve order
        seen: set[str] = set()
        cleaned: list[TenantIndustry] = []
        for ind in industries:
            if not ind.industry_code or ind.industry_code in seen:
                continue
            seen.add(ind.industry_code)
            cleaned.append(
                TenantIndustry(
                    industry_code=ind.industry_code,
                    subtype_code=ind.subtype_code,
                    is_primary=bool(ind.is_primary),
                )
            )

        # Ensure exactly one primary
        primaries = [i for i in cleaned if i.is_primary]
        if cleaned and not primaries:
            cleaned[0].is_primary = True
        elif len(primaries) > 1:
            first = True
            for i in cleaned:
                if i.is_primary and not first:
                    i.is_primary = False
                if i.is_primary:
                    first = False

        self.tenant_industries[company_id] = cleaned

        # Mirror primary into legacy single fields
        primary = next((i for i in cleaned if i.is_primary), None)
        legacy_group = primary.industry_code if primary else None
        legacy_subtype = primary.subtype_code if primary else None
        current = self.get_tenant_operating_profile(company_id)
        self.tenant_operating_profiles[company_id] = current.model_copy(update={
            "industry_group": legacy_group,
            "industry_subtype": legacy_subtype,
        })
        cfg = self.tenant_configuration.setdefault(company_id, {})
        cfg["industry_group"] = legacy_group
        cfg["industry_subtype"] = legacy_subtype
        return cleaned

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
        return [
            language
            for (tenant_id, _, _), language in self.tenant_languages.items()
            if tenant_id == company_id
        ]

    def replace_tenant_languages(self, company_id: str, languages: list[TenantLanguageChoice]) -> list[TenantLanguage]:
        if company_id not in self.companies:
            raise KeyError("Company not found")
        self.tenant_languages = {
            key: value
            for key, value in self.tenant_languages.items()
            if key[0] != company_id
        }
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

    def get_client_language(self, company_id: str, client_id: str) -> ClientLanguageSettings:
        record = self.crm["clients"].get(client_id)
        if not record or record.company_id != company_id:
            raise KeyError("Client not found")
        profile = self.get_tenant_operating_profile(company_id)
        preferred = normalize_language_code(record.preferred_language_code, profile.default_customer_language_code)
        return ClientLanguageSettings(
            client_id=client_id,
            preferred_language_code=preferred,
            resolved_language_code=preferred,
        )

    def set_client_language(self, company_id: str, client_id: str, language_code: str) -> ClientLanguageSettings:
        record = self.crm["clients"].get(client_id)
        if not record or record.company_id != company_id:
            raise KeyError("Client not found")
        normalized = normalize_language_code(language_code, self.get_tenant_operating_profile(company_id).default_customer_language_code)
        self.crm["clients"][client_id] = record.model_copy(update={"preferred_language_code": normalized})
        return ClientLanguageSettings(
            client_id=client_id,
            preferred_language_code=normalized,
            resolved_language_code=normalized,
        )

    def get_client_preferred_language_code(self, company_id: str, client_id: str | None) -> str | None:
        if not client_id:
            return None
        record = self.crm["clients"].get(client_id)
        if not record or record.company_id != company_id:
            return None
        return record.preferred_language_code

    def list_users(self, company_id: str) -> list[UserAccount]:
        return [user for user in self.users.values() if user.company_id == company_id]

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
        return [record for record in self.crm[module].values() if record.company_id == company_id]

    def get_crm_record(self, module: str, record_id: str, company_id: str) -> CRMRecord | None:
        if module not in self.crm:
            raise KeyError("Unknown CRM module")
        record = self.crm[module].get(record_id)
        if record and record.company_id == company_id:
            return record
        return None

    def update_crm_record(self, module: str, record_id: str, company_id: str, payload: CRMUpdateRequest) -> CRMRecord:
        if module not in self.crm:
            raise KeyError("Unknown CRM module")
        record = self.crm[module].get(record_id)
        if not record or record.company_id != company_id:
            raise KeyError("Record not found")
        updates: dict = {}
        if payload.name is not None:
            updates["name"] = payload.name
        if payload.status is not None:
            updates["status"] = payload.status
        if payload.data is not None:
            merged = {**record.data, **payload.data}
            updates["data"] = merged
        updates["updated_at"] = datetime.now(timezone.utc)
        updated = record.model_copy(update=updates)
        self.crm[module][record_id] = updated
        return updated

    def delete_crm_record(self, module: str, record_id: str, company_id: str) -> bool:
        """Soft-delete: sets status='deleted' and updated_at. Never hard-deletes."""
        if module not in self.crm:
            raise KeyError("Unknown CRM module")
        record = self.crm[module].get(record_id)
        if not record or record.company_id != company_id:
            raise KeyError("Record not found")
        updated = record.model_copy(update={"status": "deleted", "updated_at": datetime.now(timezone.utc)})
        self.crm[module][record_id] = updated
        return True

    def add_crm_note(self, module: str, record_id: str, company_id: str, note: NoteCreateRequest, author_id: str) -> CRMRecord:
        """Append a timestamped note to data['notes'] list."""
        if module not in self.crm:
            raise KeyError("Unknown CRM module")
        record = self.crm[module].get(record_id)
        if not record or record.company_id != company_id:
            raise KeyError("Record not found")
        notes = list(record.data.get("notes", []))
        notes.append({
            "id": str(uuid4()),
            "content": note.content,
            "author_id": author_id,
            "author_name": note.author_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        merged_data = {**record.data, "notes": notes}
        updated = record.model_copy(update={"data": merged_data, "updated_at": datetime.now(timezone.utc)})
        self.crm[module][record_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Work Reports
    # ------------------------------------------------------------------

    def create_work_report(self, company_id: str, payload: WorkReportCreate) -> CRMRecord:
        data = payload.model_dump()
        work_date = data.get("work_date") or datetime.now(timezone.utc).date().isoformat()
        name = f"Work Report {work_date}"
        data["invoiced"] = False
        record = CRMRecord(
            id=str(uuid4()),
            company_id=company_id,
            name=name,
            status="open",
            data=data,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.crm["work_reports"][record.id] = record
        return record

    def list_work_reports(self, company_id: str) -> list[CRMRecord]:
        return [
            r for r in self.crm["work_reports"].values()
            if r.company_id == company_id and r.status != "deleted"
        ]

    def get_work_report(self, record_id: str, company_id: str) -> CRMRecord | None:
        record = self.crm["work_reports"].get(record_id)
        if record and record.company_id == company_id:
            return record
        return None

    def create_invoice_from_work_report(
        self, company_id: str, request: InvoiceFromWorkReportRequest, user_id: str
    ) -> CRMRecord:
        from datetime import date, timedelta
        wr = self.get_work_report(request.work_report_id, company_id)
        if not wr:
            raise KeyError("Work report not found")
        if wr.data.get("invoiced"):
            raise ValueError("Work report already invoiced")

        workers_data = wr.data.get("workers", [])
        entries = wr.data.get("entries", [])
        materials = wr.data.get("materials", [])
        waste = wr.data.get("waste", [])

        line_items = []
        calculated_total = 0.0


        # ── resolve tenant default labour rate ───────────────────────────────
        tenant_labour_rate: float | None = None
        try:
            all_pricing = self.list_tenant_pricing(company_id)
            # Priority: exact 'labour'/'labor' code, then any 'hourly' method
            _LABOUR_CODES = {"labour", "labor", "general_labour", "hourly_labour", "default_labour"}
            for tp in all_pricing:
                if tp.activity_code.lower() in _LABOUR_CODES and tp.rate and tp.rate > 0:
                    tenant_labour_rate = float(tp.rate)
                    break
            if tenant_labour_rate is None:
                for tp in all_pricing:
                    if "hourly" in tp.selected_pricing_method_code.lower() and tp.rate and tp.rate > 0:
                        tenant_labour_rate = float(tp.rate)
                        break
        except Exception:
            pass

        pricing_warnings: list[str] = []
        # Workers (from voice or manual work reports)
        for w in workers_data:
            hours = float(w.get("hours", 0))
            raw_rate = float(w.get("hourly_rate", 0))
            # Rate resolution: 1) worker.hourly_rate  2) tenant labour rate  3) 0.0 + warning
            if raw_rate > 0:
                resolved_rate = raw_rate
            elif tenant_labour_rate is not None:
                resolved_rate = tenant_labour_rate
            else:
                resolved_rate = 0.0
                pricing_warnings.append(
                    f"No hourly rate for {w.get('worker_name', 'Worker')} — set via tenant pricing"
                )
            subtotal = round(hours * resolved_rate, 2)
            calculated_total += subtotal
            if hours > 0:
                line_items.append({
                    "description": f"Labour – {w.get('worker_name', 'Worker')}",
                    "quantity": hours,
                    "unit_price": resolved_rate,
                    "subtotal": subtotal,
                })
        for e in entries:
            hours = float(e.get("hours", 0))
            rate = float(e.get("unit_rate", 0))
            subtotal = hours * rate
            calculated_total += subtotal
            line_items.append({
                "description": e.get("description") or e.get("entry_type", "work"),
                "quantity": hours,
                "unit_price": rate,
                "subtotal": subtotal,
            })
        for m in materials:
            qty = float(m.get("quantity", 1))
            price = float(m.get("unit_price", 0))
            subtotal = qty * price
            calculated_total += subtotal
            line_items.append({
                "description": m.get("material_name", "Material"),
                "quantity": qty,
                "unit_price": price,
                "subtotal": subtotal,
            })
        for w in waste:
            qty = float(w.get("quantity", 1))
            price = float(w.get("unit_price", 0))
            subtotal = qty * price
            calculated_total += subtotal
            line_items.append({
                "description": w.get("description", "Waste disposal"),
                "quantity": qty,
                "unit_price": price,
                "subtotal": subtotal,
            })

        due_date = request.due_date or (date.today() + timedelta(days=30)).isoformat()
        total = wr.data.get("total_price") or calculated_total
        client_id = wr.data.get("client_id")

        client_name = ""
        if client_id:
            client_rec = self.crm["clients"].get(client_id)
            if client_rec:
                client_name = f" - {client_rec.name}"

        invoice_data = {
            "pricing_warnings": pricing_warnings,
            "work_report_id": wr.id,
            "client_id": client_id,
            "job_id": wr.data.get("job_id"),
            "line_items": line_items,
            "total": total,
            "currency": wr.data.get("currency", "GBP"),
            "due_date": due_date,
            "created_by": user_id,
        }

        invoice_name = f"Invoice{client_name} ({wr.data.get('work_date', 'N/A')})"
        invoice = CRMRecord(
            id=str(uuid4()),
            company_id=company_id,
            name=invoice_name,
            status="draft",
            data=invoice_data,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.crm["invoices"][invoice.id] = invoice

        updated_data = {**wr.data, "invoiced": True, "invoice_id": invoice.id}
        updated_wr = wr.model_copy(update={
            "data": updated_data,
            "updated_at": datetime.now(timezone.utc),
        })
        self.crm["work_reports"][wr.id] = updated_wr
        return invoice

    # ------------------------------------------------------------------
    # Biometrics (in-memory stubs)
    # ------------------------------------------------------------------

    def save_biometric(
        self, bio_id: str, user_id: str, device_id: str, biometric_hash: str, label: str | None = None
    ) -> None:
        if not hasattr(self, "_biometrics"):
            self._biometrics: dict[tuple, dict] = {}
        self._biometrics[(user_id, device_id)] = {
            "id": bio_id,
            "user_id": user_id,
            "device_id": device_id,
            "biometric_hash": biometric_hash,
            "label": label,
            "is_active": True,
        }

    def get_biometric_hashes(self, user_id: str) -> list[str]:
        if not hasattr(self, "_biometrics"):
            return []
        return [
            v["biometric_hash"]
            for (uid, _), v in self._biometrics.items()
            if uid == user_id and v["is_active"]
        ]

    def deactivate_biometric(self, user_id: str, device_id: str) -> bool:
        if not hasattr(self, "_biometrics"):
            return False
        key = (user_id, device_id)
        if key in self._biometrics and self._biometrics[key]["is_active"]:
            self._biometrics[key]["is_active"] = False
            return True
        return False

    # ------------------------------------------------------------------
    # Backup manifests (in-memory stubs)
    # ------------------------------------------------------------------

    def save_backup_manifest(
        self, backup_id: str, company_id: str, created_by_user_id: str,
        created_by_role: str, backup_scope: str, includes_db_reference: bool,
        storage_location: str, restore_token: str | None, restore_token_expires_at,
        payload: dict,
    ) -> None:
        if not hasattr(self, "_backup_manifests"):
            self._backup_manifests: dict[str, dict] = {}
        self._backup_manifests[backup_id] = {
            "id": backup_id,
            "company_id": company_id,
            "created_by_user_id": created_by_user_id,
            "created_by_role": created_by_role,
            "backup_scope": backup_scope,
            "includes_db_reference": includes_db_reference,
            "storage_location": storage_location,
            "restore_token": restore_token,
            "restore_token_expires_at": restore_token_expires_at,
            "payload": payload,
            "created_at": datetime.now(timezone.utc),
        }

    def list_backup_manifests(self, company_id: str) -> list:
        from .models import BackupRestoreInfo, BackupScope
        if not hasattr(self, "_backup_manifests"):
            return []
        results = []
        for row in self._backup_manifests.values():
            if row["company_id"] == company_id:
                results.append(BackupRestoreInfo(
                    backup_id=row["id"],
                    company_legal_name="",
                    created_at=row["created_at"],
                    backup_scope=BackupScope(row["backup_scope"]),
                    includes_db_reference=row["includes_db_reference"],
                ))
        return results

    def get_backup_manifest_by_token(self, token: str) -> dict | None:
        if not hasattr(self, "_backup_manifests"):
            return None
        for row in self._backup_manifests.values():
            if row.get("restore_token") == token:
                return row
        return None

    # ------------------------------------------------------------------
    # Phase A2: voice session persistence
    # ------------------------------------------------------------------

    def save_voice_session(self, session: dict) -> None:
        """Persist a voice session dict (keyed by its 'id')."""
        if not hasattr(self, "_voice_sessions"):
            self._voice_sessions: dict[str, dict] = {}
        self._voice_sessions[session["id"]] = dict(session)

    def load_voice_session(self, session_id: str) -> dict | None:
        """Load a voice session dict by id, or None if not found."""
        if not hasattr(self, "_voice_sessions"):
            return None
        sess = self._voice_sessions.get(session_id)
        return dict(sess) if sess else None

    # ------------------------------------------------------------------
    # Phase A3: calendar events
    # ------------------------------------------------------------------

    def list_calendar_events(
        self, company_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[CalendarEvent]:
        events = [e for e in self.calendar_events.values() if e.company_id == company_id]
        if start is not None:
            events = [e for e in events if e.start_at >= start]
        if end is not None:
            events = [e for e in events if e.start_at <= end]
        return sorted(events, key=lambda e: e.start_at)

    def get_calendar_event(self, event_id: str, company_id: str) -> CalendarEvent | None:
        event = self.calendar_events.get(event_id)
        if not event or event.company_id != company_id:
            return None
        return event

    def create_calendar_event(
        self, company_id: str, payload: CalendarEventCreate, created_by: str | None = None
    ) -> CalendarEvent:
        now = datetime.now(timezone.utc)
        event = CalendarEvent(
            id=str(uuid4()),
            company_id=company_id,
            title=payload.title,
            description=payload.description,
            location=payload.location,
            start_at=payload.start_at,
            end_at=payload.end_at,
            all_day=payload.all_day,
            client_id=payload.client_id,
            job_id=payload.job_id,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.calendar_events[event.id] = event
        return event

    def update_calendar_event(
        self, event_id: str, company_id: str, payload: CalendarEventUpdate
    ) -> CalendarEvent:
        event = self.calendar_events.get(event_id)
        if not event or event.company_id != company_id:
            raise KeyError("Calendar event not found")
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        updates["updated_at"] = datetime.now(timezone.utc)
        updated = event.model_copy(update=updates)
        self.calendar_events[event_id] = updated
        return updated

    def delete_calendar_event(self, event_id: str, company_id: str) -> bool:
        event = self.calendar_events.get(event_id)
        if not event or event.company_id != company_id:
            return False
        del self.calendar_events[event_id]
        return True

    # ------------------------------------------------------------------
    # Phase A5: calendar sync log + synchronization
    # ------------------------------------------------------------------

    def add_calendar_sync_log(
        self, company_id: str, event_id: str | None, source: str,
        action: str, status: str = "ok", detail: str | None = None,
    ) -> CalendarSyncLogEntry:
        entry = CalendarSyncLogEntry(
            id=str(uuid4()),
            company_id=company_id,
            event_id=event_id,
            source=source,
            action=action,
            status=status,
            detail=detail,
            created_at=datetime.now(timezone.utc),
        )
        self.calendar_sync_log.append(entry)
        return entry

    def list_calendar_sync_log(self, company_id: str, limit: int = 100) -> list[CalendarSyncLogEntry]:
        rows = [e for e in self.calendar_sync_log if e.company_id == company_id]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[:limit]

    def sync_calendar_events(
        self, company_id: str, device_events: list[CalendarSyncEventInput]
    ) -> list[CalendarSyncOutcome]:
        """Reconcile device events with backend (backend = source of truth).

        Rules:
          - exists in both (by backend_id): newest updated_at wins
          - exists only in backend: device must create it locally (created_on_android)
          - exists only on device: import to backend (source=android_import)
        """
        outcomes: list[CalendarSyncOutcome] = []
        backend_now = {e.id: e for e in self.calendar_events.values() if e.company_id == company_id}
        seen_backend_ids: set[str] = set()

        for dev in device_events:
            # Matched by backend_id
            if dev.backend_id and dev.backend_id in backend_now:
                seen_backend_ids.add(dev.backend_id)
                be = backend_now[dev.backend_id]
                dev_ts = dev.updated_at
                be_ts = be.updated_at
                if dev_ts and dev_ts > be_ts:
                    # device newer → update backend
                    upd = CalendarEventUpdate(
                        title=dev.title, description=dev.description, location=dev.location,
                        start_at=dev.start_at, end_at=dev.end_at, all_day=dev.all_day,
                    )
                    self.update_calendar_event(be.id, company_id, upd)
                    self.add_calendar_sync_log(company_id, be.id, "android", "conflict_android_wins")
                    outcomes.append(CalendarSyncOutcome(
                        backend_id=be.id, android_id=dev.android_id,
                        action="conflict_android_wins", source="android",
                        detail="device updated_at newer; backend updated",
                    ))
                else:
                    # backend newer or equal → device must take backend copy
                    self.add_calendar_sync_log(company_id, be.id, "backend", "conflict_backend_wins")
                    outcomes.append(CalendarSyncOutcome(
                        backend_id=be.id, android_id=dev.android_id,
                        action="conflict_backend_wins", source="backend",
                        detail="backend updated_at newer or equal; device must adopt backend copy",
                    ))
            else:
                # device-only → import to backend, mark source android_import
                created = self.create_calendar_event(
                    company_id,
                    CalendarEventCreate(
                        title=dev.title, description=dev.description, location=dev.location,
                        start_at=dev.start_at, end_at=dev.end_at, all_day=dev.all_day,
                    ),
                    created_by=None,
                )
                self.add_calendar_sync_log(
                    company_id, created.id, "android_import", "created_on_backend",
                    detail="device-only event imported to backend",
                )
                outcomes.append(CalendarSyncOutcome(
                    backend_id=created.id, android_id=dev.android_id,
                    action="created_on_backend", source="android_import",
                    detail="device-only event imported",
                ))

        # backend-only events the device didn't send → device must create them locally
        for be_id, be in backend_now.items():
            if be_id not in seen_backend_ids:
                self.add_calendar_sync_log(company_id, be_id, "backend", "created_on_android")
                outcomes.append(CalendarSyncOutcome(
                    backend_id=be_id, android_id=None,
                    action="created_on_android", source="backend",
                    detail="backend-only event; device must create locally",
                ))
        return outcomes

    # ------------------------------------------------------------------
    # Phase A5.2: pending voice actions (multi-turn slot filling)
    # ------------------------------------------------------------------

    def create_pending_action(self, action: PendingVoiceAction) -> PendingVoiceAction:
        self.pending_voice_actions[action.id] = action
        return action

    def get_pending_action(self, action_id: str, company_id: str) -> PendingVoiceAction | None:
        a = self.pending_voice_actions.get(action_id)
        if not a or a.company_id != company_id:
            return None
        return a

    def update_pending_action(self, action: PendingVoiceAction) -> PendingVoiceAction:
        from datetime import datetime as _dt, timezone as _tz
        action.updated_at = _dt.now(_tz.utc)
        self.pending_voice_actions[action.id] = action
        return action
