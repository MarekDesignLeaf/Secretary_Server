"""PostgreSQL-backed SecretaryRepository using psycopg2.

Implements the same interface as InMemorySecretaryRepository, persisting data
to the Railway PostgreSQL instance via the clean_* tables defined in schema.sql.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from secretary_clean.core.language import normalize_language_code
from secretary_clean.core.models import (
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
from secretary_clean.core.security import (
    hash_password,
    hash_reset_token,
    reset_token_expiry,
    verify_password,
)

logger = logging.getLogger(__name__)

# CRM module name -> table name
_CRM_TABLES: dict[str, str] = {
    "clients": "clean_clients",
    "jobs": "clean_jobs",
    "tasks": "clean_tasks",
    "quotes": "clean_quotes",
    "invoices": "clean_invoices",
    "communications": "clean_communications",
    "work_reports": "clean_work_reports",
}

_VALID_CRM_MODULES = set(_CRM_TABLES.keys())


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class PostgresSecretaryRepository:
    """Full PostgreSQL implementation of the Secretary repository contract."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=database_url,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        # Register UUID adapter so UUID columns come back as strings
        psycopg2.extras.register_uuid()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _conn(self):
        """Get a connection from the pool (use as context manager)."""
        return _PooledConnection(self._pool)

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def bootstrap_status(self) -> BootstrapStatus:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies")
                needs_first_company = cur.fetchone()["n"] == 0
                cur.execute("SELECT COUNT(*) AS n FROM clean_users WHERE role = %s", (Role.owner.value,))
                needs_first_admin = cur.fetchone()["n"] == 0
        return BootstrapStatus(
            needs_first_company=needs_first_company,
            needs_first_admin=needs_first_admin,
            is_ready=not needs_first_company and not needs_first_admin,
        )

    # ------------------------------------------------------------------
    # First company / first install
    # ------------------------------------------------------------------

    def create_first_company(self, payload: FirstCompanyCreate) -> CompanyProfile:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies")
                if cur.fetchone()["n"] > 0:
                    raise ValueError("First company already exists")

            payload_data = payload.model_dump()
            default_internal_language_code = normalize_language_code(
                payload_data.pop("default_internal_language_code", None)
            )
            default_customer_language_code = normalize_language_code(
                payload_data.pop("default_customer_language_code", None)
            )
            workspace_mode = payload_data.pop("workspace_mode", "single_company")
            company_id = str(uuid4())

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clean_companies
                        (id, legal_name, trading_name, legal_type,
                         default_country, default_currency, timezone,
                         phone, website, industry_group, industry_subtype)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        company_id,
                        payload_data["legal_name"],
                        payload_data.get("trading_name"),
                        payload_data.get("legal_type"),
                        payload_data.get("default_country", "GB"),
                        payload_data.get("default_currency", "GBP"),
                        payload_data.get("timezone", "Europe/London"),
                        payload_data.get("phone"),
                        payload_data.get("website"),
                        payload_data.get("industry_group"),
                        payload_data.get("industry_subtype"),
                    ),
                )

                # Company operating settings
                cur.execute(
                    """
                    INSERT INTO clean_company_operating_settings (company_id, workspace_mode)
                    VALUES (%s, %s)
                    ON CONFLICT (company_id) DO UPDATE SET workspace_mode = EXCLUDED.workspace_mode
                    """,
                    (company_id, workspace_mode),
                )

                # Tenant operating profile
                cur.execute(
                    """
                    INSERT INTO tenant_operating_profile
                        (company_id, workspace_mode, industry_group, industry_subtype,
                         default_internal_language_code, default_customer_language_code)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id) DO UPDATE SET
                        workspace_mode = EXCLUDED.workspace_mode,
                        industry_group = EXCLUDED.industry_group,
                        industry_subtype = EXCLUDED.industry_subtype,
                        default_internal_language_code = EXCLUDED.default_internal_language_code,
                        default_customer_language_code = EXCLUDED.default_customer_language_code
                    """,
                    (
                        company_id,
                        workspace_mode,
                        payload_data.get("industry_group"),
                        payload_data.get("industry_subtype"),
                        default_internal_language_code,
                        default_customer_language_code,
                    ),
                )

                # Tenant configuration
                cur.execute(
                    """
                    INSERT INTO clean_tenant_configuration
                        (company_id, workspace_mode, industry_group, industry_subtype, phone, website)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id) DO UPDATE SET
                        workspace_mode = EXCLUDED.workspace_mode,
                        industry_group = EXCLUDED.industry_group,
                        industry_subtype = EXCLUDED.industry_subtype,
                        phone = EXCLUDED.phone,
                        website = EXCLUDED.website
                    """,
                    (
                        company_id,
                        workspace_mode,
                        payload_data.get("industry_group"),
                        payload_data.get("industry_subtype"),
                        payload_data.get("phone"),
                        payload_data.get("website"),
                    ),
                )

            conn.commit()
            self._seed_default_languages_conn(
                conn, company_id, default_internal_language_code, default_customer_language_code
            )
            conn.commit()

        return self.get_company(company_id)

    def create_first_admin(
        self,
        *,
        company_id: str,
        email: str,
        display_name: str,
        password: str,
        preferred_language_code: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
    ) -> UserAccount:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute("SELECT COUNT(*) AS n FROM clean_users WHERE role = %s", (Role.owner.value,))
                if cur.fetchone()["n"] > 0:
                    raise ValueError("First admin already exists")

                user_id = str(uuid4())
                normalized_email = email.lower()
                normalized_lang = (
                    normalize_language_code(preferred_language_code)
                    if preferred_language_code
                    else None
                )
                password_hash = hash_password(password)

                cur.execute(
                    """
                    INSERT INTO clean_users
                        (id, company_id, email, display_name, role, is_active,
                         preferred_language_code, first_name, last_name, phone, password_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        user_id,
                        company_id,
                        normalized_email,
                        display_name,
                        Role.owner.value,
                        True,
                        normalized_lang,
                        first_name,
                        last_name,
                        phone,
                        password_hash,
                    ),
                )
            conn.commit()

        return self._build_user_account(user_id)

    def create_first_install(
        self,
        payload: FirstInstallCreate,
        *,
        activity_defaults: dict[str, str] | None = None,
    ) -> FirstInstallResult:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies")
                if cur.fetchone()["n"] > 0:
                    raise ValueError("First company already exists")
                cur.execute("SELECT COUNT(*) AS n FROM clean_users WHERE role = %s", (Role.owner.value,))
                if cur.fetchone()["n"] > 0:
                    raise ValueError("First admin already exists")

        industry_group = payload.primary_industry or (
            payload.selected_industries[0] if payload.selected_industries else None
        )
        industry_subtype = payload.primary_subtype or (
            payload.selected_subtypes[0] if payload.selected_subtypes else None
        )

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

        # Store tenant industry profile in tenant_operating_profile extra fields
        # (no separate table; we store it in the JSONB-free approach via tenant_operating_profile)
        # We save industry profile into tenant_configuration extended fields.
        # Since there's no dedicated industry_profile table, we persist the profile
        # by updating tenant_operating_profile's industry_group/industry_subtype columns
        # which are already set. The TenantIndustryProfile is reconstructed on read
        # from the operating profile.

        # Seed tenant_activity_pricing
        if payload.selected_activities and activity_defaults:
            now = datetime.now(timezone.utc)
            with self._conn() as conn:
                with conn.cursor() as cur:
                    for activity_code in payload.selected_activities:
                        default_method = activity_defaults.get(activity_code)
                        if default_method:
                            cur.execute(
                                """
                                INSERT INTO clean_tenant_activity_pricing
                                    (company_id, activity_code, is_active,
                                     selected_pricing_method_code, updated_at)
                                VALUES (%s,%s,%s,%s,%s)
                                ON CONFLICT (company_id, activity_code) DO NOTHING
                                """,
                                (company.id, activity_code, True, default_method, now),
                            )
                conn.commit()

        return FirstInstallResult(
            company=company,
            admin=admin,
            bootstrap_status=self.bootstrap_status(),
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self, email: str, password: str) -> UserAccount | None:
        normalized = email.lower()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, password_hash FROM clean_users WHERE email = %s AND is_active = TRUE",
                    (normalized,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return self._build_user_account(str(row["id"]))

    def get_user(self, user_id: str) -> UserAccount | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM clean_users WHERE id = %s", (user_id,))
                row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> UserAccount | None:
        normalized = email.lower()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM clean_users WHERE email = %s", (normalized,))
                row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_user(row)

    def list_users(self, company_id: str) -> list[UserAccount]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM clean_users WHERE company_id = %s", (company_id,))
                rows = cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    def list_roles(self) -> dict[str, list[str]]:
        return {role.value: sorted(p.value for p in permissions) for role, permissions in ROLE_PERMISSIONS.items()}

    # ------------------------------------------------------------------
    # Company
    # ------------------------------------------------------------------

    def get_company(self, company_id: str) -> CompanyProfile | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM clean_companies WHERE id = %s", (company_id,))
                row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_company(row)

    def update_company(self, company_id: str, profile: CompanyProfile) -> CompanyProfile:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute(
                    """
                    UPDATE clean_companies SET
                        legal_name = %s, trading_name = %s, legal_type = %s,
                        default_country = %s, default_currency = %s, timezone = %s,
                        phone = %s, website = %s, industry_group = %s, industry_subtype = %s,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        profile.legal_name,
                        profile.trading_name,
                        profile.legal_type,
                        profile.default_country,
                        profile.default_currency,
                        profile.timezone,
                        profile.phone,
                        profile.website,
                        profile.industry_group,
                        profile.industry_subtype,
                        company_id,
                    ),
                )
            conn.commit()
        return self.get_company(company_id)

    def get_company_settings(self, company_id: str) -> CompanyOperatingSettings:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM clean_company_operating_settings WHERE company_id = %s",
                    (company_id,),
                )
                row = cur.fetchone()
        if row is None:
            return CompanyOperatingSettings()
        return CompanyOperatingSettings(
            workspace_mode=row["workspace_mode"],
            quote_prefix=row["quote_prefix"],
            invoice_prefix=row["invoice_prefix"],
            default_tax_rate_percent=float(row["default_tax_rate_percent"]),
            require_quote_acceptance_before_invoice=row["require_quote_acceptance_before_invoice"],
        )

    def update_company_settings(
        self, company_id: str, settings: CompanyOperatingSettings
    ) -> CompanyOperatingSettings:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute(
                    """
                    INSERT INTO clean_company_operating_settings
                        (company_id, workspace_mode, quote_prefix, invoice_prefix,
                         default_tax_rate_percent, require_quote_acceptance_before_invoice)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id) DO UPDATE SET
                        workspace_mode = EXCLUDED.workspace_mode,
                        quote_prefix = EXCLUDED.quote_prefix,
                        invoice_prefix = EXCLUDED.invoice_prefix,
                        default_tax_rate_percent = EXCLUDED.default_tax_rate_percent,
                        require_quote_acceptance_before_invoice = EXCLUDED.require_quote_acceptance_before_invoice,
                        updated_at = now()
                    """,
                    (
                        company_id,
                        settings.workspace_mode,
                        settings.quote_prefix,
                        settings.invoice_prefix,
                        settings.default_tax_rate_percent,
                        settings.require_quote_acceptance_before_invoice,
                    ),
                )
                # Keep tenant_operating_profile workspace_mode in sync
                cur.execute(
                    """
                    UPDATE tenant_operating_profile
                    SET workspace_mode = %s, updated_at = now()
                    WHERE company_id = %s
                    """,
                    (settings.workspace_mode, company_id),
                )
            conn.commit()
        return settings

    def get_company_legal_identity(self, company_id: str) -> CompanyLegalIdentity:
        company = self.get_company(company_id)
        if company is None:
            raise KeyError("Company not found")
        return CompanyLegalIdentity(
            legal_name=company.legal_name,
            trading_name=company.trading_name,
            legal_type=company.legal_type,
            registered_address=None,
            phone=company.phone,
            website=company.website,
        )

    def update_company_legal_identity(
        self, company_id: str, identity: CompanyLegalIdentity
    ) -> CompanyLegalIdentity:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute(
                    """
                    UPDATE clean_companies SET
                        legal_name = %s, trading_name = %s, legal_type = %s,
                        phone = %s, website = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        identity.legal_name,
                        identity.trading_name,
                        identity.legal_type,
                        identity.phone,
                        identity.website,
                        company_id,
                    ),
                )
            conn.commit()
        return identity

    # ------------------------------------------------------------------
    # Tenant operating profile
    # ------------------------------------------------------------------

    def get_tenant_operating_profile(self, company_id: str) -> TenantOperatingProfile:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute(
                    "SELECT * FROM tenant_operating_profile WHERE company_id = %s",
                    (company_id,),
                )
                row = cur.fetchone()
        if row is None:
            return TenantOperatingProfile(company_id=company_id)
        return self._row_to_tenant_profile(row)

    def update_tenant_operating_profile(
        self, company_id: str, settings: LanguageSettings
    ) -> TenantOperatingProfile:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")

        current = self.get_tenant_operating_profile(company_id)
        updated_data = settings.model_dump()
        updated_data["default_internal_language_code"] = normalize_language_code(
            updated_data["default_internal_language_code"]
        )
        updated_data["default_customer_language_code"] = normalize_language_code(
            updated_data["default_customer_language_code"]
        )
        profile_dict = current.model_dump()
        profile_dict.update(updated_data)
        profile = TenantOperatingProfile(**profile_dict)

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tenant_operating_profile
                        (company_id, workspace_mode, industry_group, industry_subtype,
                         internal_language_mode, customer_language_mode,
                         default_internal_language_code, default_customer_language_code,
                         voice_input_strategy, voice_output_strategy,
                         auto_translate_customer_to_internal, auto_translate_internal_to_customer)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id) DO UPDATE SET
                        workspace_mode = EXCLUDED.workspace_mode,
                        industry_group = EXCLUDED.industry_group,
                        industry_subtype = EXCLUDED.industry_subtype,
                        internal_language_mode = EXCLUDED.internal_language_mode,
                        customer_language_mode = EXCLUDED.customer_language_mode,
                        default_internal_language_code = EXCLUDED.default_internal_language_code,
                        default_customer_language_code = EXCLUDED.default_customer_language_code,
                        voice_input_strategy = EXCLUDED.voice_input_strategy,
                        voice_output_strategy = EXCLUDED.voice_output_strategy,
                        auto_translate_customer_to_internal = EXCLUDED.auto_translate_customer_to_internal,
                        auto_translate_internal_to_customer = EXCLUDED.auto_translate_internal_to_customer,
                        updated_at = now()
                    """,
                    (
                        company_id,
                        profile.workspace_mode,
                        profile.industry_group,
                        profile.industry_subtype,
                        profile.internal_language_mode.value,
                        profile.customer_language_mode.value,
                        profile.default_internal_language_code,
                        profile.default_customer_language_code,
                        profile.voice_input_strategy.value,
                        profile.voice_output_strategy.value,
                        profile.auto_translate_customer_to_internal,
                        profile.auto_translate_internal_to_customer,
                    ),
                )
            conn.commit()

        self._seed_default_languages_with_conn_factory(
            company_id,
            profile.default_internal_language_code,
            profile.default_customer_language_code,
        )
        return profile

    # ------------------------------------------------------------------
    # Tenant languages
    # ------------------------------------------------------------------

    def _seed_default_languages_conn(
        self, conn, company_id: str, internal_code: str, customer_code: str
    ) -> None:
        scopes_and_codes = [
            (LanguageScope.internal.value, internal_code),
            (LanguageScope.customer.value, customer_code),
            (LanguageScope.voice_input.value, customer_code),
            (LanguageScope.voice_output.value, customer_code),
        ]
        with conn.cursor() as cur:
            for scope, code in scopes_and_codes:
                cur.execute(
                    """
                    INSERT INTO tenant_languages
                        (company_id, language_code, language_scope, is_enabled, is_default)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, language_scope, language_code) DO UPDATE SET
                        is_enabled = TRUE,
                        is_default = TRUE,
                        updated_at = now()
                    """,
                    (company_id, code, scope, True, True),
                )

    def _seed_default_languages_with_conn_factory(
        self, company_id: str, internal_code: str, customer_code: str
    ) -> None:
        with self._conn() as conn:
            self._seed_default_languages_conn(conn, company_id, internal_code, customer_code)
            conn.commit()

    def list_tenant_languages(self, company_id: str) -> list[TenantLanguage]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM tenant_languages WHERE company_id = %s",
                    (company_id,),
                )
                rows = cur.fetchall()
        return [self._row_to_tenant_language(r) for r in rows]

    def replace_tenant_languages(
        self, company_id: str, languages: list[TenantLanguageChoice]
    ) -> list[TenantLanguage]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute("DELETE FROM tenant_languages WHERE company_id = %s", (company_id,))
                for language in languages:
                    normalized = normalize_language_code(language.language_code)
                    cur.execute(
                        """
                        INSERT INTO tenant_languages
                            (company_id, language_code, language_scope, is_enabled, is_default)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (company_id, language_scope, language_code) DO UPDATE SET
                            is_enabled = EXCLUDED.is_enabled,
                            is_default = EXCLUDED.is_default,
                            updated_at = now()
                        """,
                        (
                            company_id,
                            normalized,
                            language.language_scope.value,
                            language.is_enabled,
                            language.is_default,
                        ),
                    )
            conn.commit()
        return self.list_tenant_languages(company_id)

    # ------------------------------------------------------------------
    # Client language helpers
    # ------------------------------------------------------------------

    def get_client_language(self, company_id: str, client_id: str) -> ClientLanguageSettings:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT preferred_language_code, company_id FROM clean_clients WHERE id = %s",
                    (client_id,),
                )
                row = cur.fetchone()
        if row is None or str(row["company_id"]) != company_id:
            raise KeyError("Client not found")
        profile = self.get_tenant_operating_profile(company_id)
        preferred = normalize_language_code(
            row["preferred_language_code"], profile.default_customer_language_code
        )
        return ClientLanguageSettings(
            client_id=client_id,
            preferred_language_code=preferred,
            resolved_language_code=preferred,
        )

    def set_client_language(
        self, company_id: str, client_id: str, language_code: str
    ) -> ClientLanguageSettings:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT company_id FROM clean_clients WHERE id = %s",
                    (client_id,),
                )
                row = cur.fetchone()
        if row is None or str(row["company_id"]) != company_id:
            raise KeyError("Client not found")
        profile = self.get_tenant_operating_profile(company_id)
        normalized = normalize_language_code(
            language_code, profile.default_customer_language_code
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_clients SET preferred_language_code = %s, updated_at = now() WHERE id = %s",
                    (normalized, client_id),
                )
            conn.commit()
        return ClientLanguageSettings(
            client_id=client_id,
            preferred_language_code=normalized,
            resolved_language_code=normalized,
        )

    def get_client_preferred_language_code(
        self, company_id: str, client_id: str | None
    ) -> str | None:
        if not client_id:
            return None
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT preferred_language_code, company_id FROM clean_clients WHERE id = %s",
                    (client_id,),
                )
                row = cur.fetchone()
        if row is None or str(row["company_id"]) != company_id:
            return None
        return row["preferred_language_code"]

    # ------------------------------------------------------------------
    # Tenant activity pricing
    # ------------------------------------------------------------------

    def save_tenant_pricing(
        self,
        company_id: str,
        activity_code: str,
        request: TenantActivityOverrideRequest,
    ) -> TenantActivityPricing:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clean_tenant_activity_pricing
                        (company_id, activity_code, is_active,
                         selected_pricing_method_code, rate, custom_name,
                         enabled_additional_charge_codes, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, activity_code) DO UPDATE SET
                        is_active = TRUE,
                        selected_pricing_method_code = EXCLUDED.selected_pricing_method_code,
                        rate = EXCLUDED.rate,
                        custom_name = EXCLUDED.custom_name,
                        enabled_additional_charge_codes = EXCLUDED.enabled_additional_charge_codes,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        company_id,
                        activity_code,
                        True,
                        request.selected_pricing_method_code,
                        request.rate,
                        request.custom_name,
                        request.enabled_additional_charge_codes or [],
                        now,
                    ),
                )
            conn.commit()
        return TenantActivityPricing(
            company_id=company_id,
            activity_code=activity_code,
            is_active=True,
            selected_pricing_method_code=request.selected_pricing_method_code,
            rate=request.rate,
            custom_name=request.custom_name,
            enabled_additional_charge_codes=request.enabled_additional_charge_codes or [],
            updated_at=now,
        )

    def reset_tenant_pricing(self, company_id: str, activity_code: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM clean_tenant_activity_pricing WHERE company_id = %s AND activity_code = %s",
                    (company_id, activity_code),
                )
            conn.commit()
        return True

    def list_tenant_pricing(self, company_id: str) -> list[TenantActivityPricing]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM clean_tenant_activity_pricing WHERE company_id = %s",
                    (company_id,),
                )
                rows = cur.fetchall()
        return [
            TenantActivityPricing(
                company_id=str(r["company_id"]),
                activity_code=r["activity_code"],
                is_active=r["is_active"],
                selected_pricing_method_code=r["selected_pricing_method_code"],
                rate=float(r["rate"]) if r["rate"] is not None else None,
                custom_name=r["custom_name"],
                enabled_additional_charge_codes=list(r["enabled_additional_charge_codes"] or []),
                updated_at=_ensure_utc(r["updated_at"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # CRM
    # ------------------------------------------------------------------

    def create_crm_record(
        self, module: str, company_id: str, name: str, data: dict
    ) -> CRMRecord:
        if module not in _VALID_CRM_MODULES:
            raise KeyError("Unknown CRM module")
        table = _CRM_TABLES[module]
        record_id = str(uuid4())
        status = data.pop("status", "open") if isinstance(data, dict) else "open"
        preferred_language_code = data.pop("preferred_language_code", None) if isinstance(data, dict) else None
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {table} (id, company_id, name, status, data)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (record_id, company_id, name, status, json.dumps(data)),
                )
                # Store preferred_language_code for clients
                if module == "clients" and preferred_language_code is not None:
                    cur.execute(
                        "UPDATE clean_clients SET preferred_language_code = %s WHERE id = %s",
                        (preferred_language_code, record_id),
                    )
            conn.commit()
        return CRMRecord(
            id=record_id,
            company_id=company_id,
            name=name,
            status=status,
            data=data,
            preferred_language_code=preferred_language_code,
        )

    def list_crm_records(self, module: str, company_id: str) -> list[CRMRecord]:
        if module not in _VALID_CRM_MODULES:
            raise KeyError("Unknown CRM module")
        table = _CRM_TABLES[module]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {table} WHERE company_id = %s ORDER BY created_at",
                    (company_id,),
                )
                rows = cur.fetchall()
        return [self._row_to_crm_record(r, module) for r in rows]

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------

    def create_password_reset_token(
        self, user: UserAccount, plain_token: str
    ) -> PasswordResetToken:
        token_id = str(uuid4())
        token_hash = hash_reset_token(plain_token)
        expires_at = reset_token_expiry()
        created_at = datetime.now(timezone.utc)
        # Store tokens in a simple table if it exists, otherwise use a lightweight
        # in-memory fallback tracked per connection. Since schema.sql has no
        # password_reset_tokens table we use clean_voice_command_logs as storage
        # is optional. Instead we persist to an in-memory dict in this process.
        # For production correctness we upsert into a helper table created by migration.
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clean_password_reset_tokens
                        (id, user_id, email, token_hash, expires_at, used_at, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        token_id,
                        user.id,
                        user.email,
                        token_hash,
                        expires_at,
                        None,
                        created_at,
                    ),
                )
            conn.commit()
        return PasswordResetToken(
            id=token_id,
            user_id=user.id,
            email=user.email,
            token_hash=token_hash,
            expires_at=expires_at,
            used_at=None,
            created_at=created_at,
        )

    def verify_password_reset_token(self, plain_token: str) -> PasswordResetToken | None:
        token_hash = hash_reset_token(plain_token)
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM clean_password_reset_tokens
                    WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s
                    """,
                    (token_hash, now),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return PasswordResetToken(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            email=row["email"],
            token_hash=row["token_hash"],
            expires_at=_ensure_utc(row["expires_at"]),
            used_at=_ensure_utc(row["used_at"]),
            created_at=_ensure_utc(row["created_at"]),
        )

    def mark_password_reset_token_used(self, token_id: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_password_reset_tokens SET used_at = now() WHERE id = %s",
                    (token_id,),
                )
            conn.commit()

    def reset_user_password(self, user_id: str, new_password: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM clean_users WHERE id = %s", (user_id,)
                )
                if cur.fetchone()["n"] == 0:
                    raise KeyError("User not found")
                cur.execute(
                    "UPDATE clean_users SET password_hash = %s, updated_at = now() WHERE id = %s",
                    (hash_password(new_password), user_id),
                )
            conn.commit()

    def admin_recovery_reset_password(self, email: str, new_password: str) -> UserAccount:
        user = self.get_user_by_email(email)
        if not user:
            raise KeyError("User not found")
        if user.role not in (Role.owner, Role.admin):
            raise PermissionError("Recovery is only available for owner/admin accounts")
        self.reset_user_password(user.id, new_password)
        return user

    # ------------------------------------------------------------------
    # Private row-to-model helpers
    # ------------------------------------------------------------------

    def _build_user_account(self, user_id: str) -> UserAccount:
        return self.get_user(user_id)

    def _row_to_user(self, row: dict) -> UserAccount:
        role = Role(row["role"])
        return UserAccount(
            id=str(row["id"]),
            company_id=str(row["company_id"]),
            email=row["email"],
            display_name=row["display_name"],
            role=role,
            permissions=sorted(ROLE_PERMISSIONS[role]),
            preferred_language_code=row.get("preferred_language_code"),
            first_name=row.get("first_name"),
            last_name=row.get("last_name"),
            phone=row.get("phone"),
            is_active=bool(row["is_active"]),
        )

    def _row_to_company(self, row: dict) -> CompanyProfile:
        return CompanyProfile(
            id=str(row["id"]),
            legal_name=row["legal_name"],
            trading_name=row.get("trading_name"),
            legal_type=row.get("legal_type"),
            default_country=row["default_country"],
            default_currency=row["default_currency"],
            timezone=row["timezone"],
            phone=row.get("phone"),
            website=row.get("website"),
            industry_group=row.get("industry_group"),
            industry_subtype=row.get("industry_subtype"),
        )

    def _row_to_tenant_profile(self, row: dict) -> TenantOperatingProfile:
        from secretary_clean.core.models import LanguageMode, VoiceLanguageStrategy
        return TenantOperatingProfile(
            company_id=str(row["company_id"]),
            workspace_mode=row["workspace_mode"],
            industry_group=row.get("industry_group"),
            industry_subtype=row.get("industry_subtype"),
            internal_language_mode=LanguageMode(row["internal_language_mode"]),
            customer_language_mode=LanguageMode(row["customer_language_mode"]),
            default_internal_language_code=row["default_internal_language_code"],
            default_customer_language_code=row["default_customer_language_code"],
            voice_input_strategy=VoiceLanguageStrategy(row["voice_input_strategy"]),
            voice_output_strategy=VoiceLanguageStrategy(row["voice_output_strategy"]),
            auto_translate_customer_to_internal=row["auto_translate_customer_to_internal"],
            auto_translate_internal_to_customer=row["auto_translate_internal_to_customer"],
        )

    def _row_to_tenant_language(self, row: dict) -> TenantLanguage:
        return TenantLanguage(
            company_id=str(row["company_id"]),
            language_code=row["language_code"],
            language_scope=LanguageScope(row["language_scope"]),
            is_enabled=bool(row["is_enabled"]),
            is_default=bool(row["is_default"]),
        )

    def _row_to_crm_record(self, row: dict, module: str) -> CRMRecord:
        data = row["data"] if isinstance(row["data"], dict) else {}
        preferred_language_code = row.get("preferred_language_code") if module == "clients" else None
        return CRMRecord(
            id=str(row["id"]),
            company_id=str(row["company_id"]),
            name=row["name"],
            status=row.get("status", "open"),
            data=data,
            preferred_language_code=preferred_language_code,
        )


# ------------------------------------------------------------------
# Connection pool context manager
# ------------------------------------------------------------------

class _PooledConnection:
    """Simple context manager that checks a connection out of the pool and returns it."""

    def __init__(self, pool: ThreadedConnectionPool) -> None:
        self._pool = pool
        self._conn = None

    def __enter__(self):
        self._conn = self._pool.getconn()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self._pool.putconn(self._conn)
        return False
