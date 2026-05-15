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
    FirstCompanyCreate,
    FirstInstallCreate,
    FirstInstallResult,
    LanguageScope,
    LanguageSettings,
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantActivityOverrideRequest,
    TenantActivityPricing,
    TenantLanguage,
    TenantLanguageChoice,
    TenantOperatingProfile,
    UserAccount,
)
from .language import normalize_language_code
from .security import hash_password, verify_password


class InMemorySecretaryRepository:
    def __init__(self) -> None:
        self.companies: dict[str, CompanyProfile] = {}
        self.company_settings: dict[str, CompanyOperatingSettings] = {}
        self.tenant_operating_profiles: dict[str, TenantOperatingProfile] = {}
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

    def create_first_install(self, payload: FirstInstallCreate) -> FirstInstallResult:
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
                industry_group=payload.industry_group,
                industry_subtype=payload.industry_subtype,
                default_internal_language_code=payload.internal_company_language,
                default_customer_language_code=payload.default_customer_language,
            )
        )
        admin = self.create_first_admin(
            company_id=company.id,
            email=payload.first_admin_email,
            display_name=payload.first_admin_display_name,
            password=payload.first_admin_password,
            preferred_language_code=payload.internal_company_language,
            first_name=payload.first_admin_first_name,
            last_name=payload.first_admin_last_name,
            phone=payload.phone,
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
