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
    CRMCreateRequest,
    CRMRecord,
    CRMUpdateRequest,
    FirstCompanyCreate,
    FirstInstallCreate,
    FirstInstallResult,
    InvoiceFromWorkReportRequest,
    LanguageScope,
    LanguageSettings,
    NoteCreateRequest,
    PasswordResetToken,
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TenantActivityOverrideRequest,
    TenantActivityPricing,
    TenantIndustryProfile,
    TenantIndustry,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarSyncEventInput,
    CalendarSyncOutcome,
    CalendarSyncLogEntry,
    GoogleCalendarAccount,
    PendingVoiceAction,
    VoiceCommandAlias,
    VoiceLearningEvent,
    VoicePendingLearning,
    TenantLanguage,
    TenantLanguageChoice,
    TenantOperatingProfile,
    UserAccount,
    WorkReportCreate,
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
    "leads": "clean_leads",
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

                # Recovery: a previous half-install (or partial wipe) may have
                # left a user row with this e-mail. A plain INSERT would hit the
                # unique e-mail constraint and brick onboarding with a 500 —
                # since no owner exists (checked above), ADOPT that row instead.
                cur.execute("SELECT id FROM clean_users WHERE email = %s", (normalized_email,))
                leftover = cur.fetchone()
                if leftover:
                    user_id = str(leftover["id"])
                    cur.execute(
                        """
                        UPDATE clean_users SET
                            company_id = %s, display_name = %s, role = %s,
                            is_active = TRUE, preferred_language_code = %s,
                            first_name = %s, last_name = %s, phone = %s,
                            password_hash = %s, must_change_password = FALSE,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (company_id, display_name, Role.owner.value, normalized_lang,
                         first_name, last_name, phone, password_hash, user_id),
                    )
                    logger.warning(
                        "first-admin adopted leftover user %s (%s)", user_id, normalized_email)
                else:
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

    def _adopt_orphan_company(self, company_id: str, payload: FirstCompanyCreate) -> CompanyProfile:
        """Recovery for half-installed DB: company row exists but no owner user.
        Overwrites the orphaned company identity with the new first-install data
        and (re)upserts operating settings, profile, configuration and languages."""
        payload_data = payload.model_dump()
        default_internal_language_code = normalize_language_code(
            payload_data.pop("default_internal_language_code", None)
        )
        default_customer_language_code = normalize_language_code(
            payload_data.pop("default_customer_language_code", None)
        )
        workspace_mode = payload_data.pop("workspace_mode", "single_company")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE clean_companies SET
                        legal_name = %s, trading_name = %s, legal_type = %s,
                        default_country = %s, default_currency = %s, timezone = %s,
                        phone = %s, website = %s,
                        industry_group = %s, industry_subtype = %s,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
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
                        company_id,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO clean_company_operating_settings (company_id, workspace_mode)
                    VALUES (%s, %s)
                    ON CONFLICT (company_id) DO UPDATE SET workspace_mode = EXCLUDED.workspace_mode
                    """,
                    (company_id, workspace_mode),
                )
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
                        company_id, workspace_mode,
                        payload_data.get("industry_group"),
                        payload_data.get("industry_subtype"),
                        default_internal_language_code,
                        default_customer_language_code,
                    ),
                )
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
                        company_id, workspace_mode,
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
        logger.warning("first-install adopted orphaned company %s (no owner existed)", company_id)
        return self.get_company(company_id)

    def create_first_install(
        self,
        payload: FirstInstallCreate,
        *,
        activity_defaults: dict[str, str] | None = None,
    ) -> FirstInstallResult:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_users WHERE role = %s", (Role.owner.value,))
                if cur.fetchone()["n"] > 0:
                    raise ValueError("First admin already exists")
                # Recovery path: a previous install may have created the company
                # but failed before the owner was created. In that half-installed
                # state we ADOPT the orphaned company instead of bricking
                # onboarding with 409 forever.
                cur.execute("SELECT id FROM clean_companies ORDER BY created_at ASC LIMIT 1")
                row = cur.fetchone()
                orphan_company_id = str(row["id"]) if row else None

        industry_group = payload.primary_industry or (
            payload.selected_industries[0] if payload.selected_industries else payload.industry_group
        )
        industry_subtype = payload.primary_subtype or (
            payload.selected_subtypes[0] if payload.selected_subtypes else payload.industry_subtype
        )

        first_company_payload = FirstCompanyCreate(
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
        if orphan_company_id is None:
            company = self.create_first_company(first_company_payload)
        else:
            company = self._adopt_orphan_company(orphan_company_id, first_company_payload)

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

        # Phase A1: persist ALL selected industries (multi-industry), not just primary.
        all_industries = list(payload.selected_industries) if payload.selected_industries else []
        if industry_group and industry_group not in all_industries:
            all_industries.insert(0, industry_group)
        if all_industries:
            now = datetime.now(timezone.utc)
            with self._conn() as conn:
                with conn.cursor() as cur:
                    for idx, ind_code in enumerate(all_industries):
                        # primary = the resolved industry_group, or first if none matched
                        is_primary = (ind_code == industry_group) if industry_group else (idx == 0)
                        sub = industry_subtype if ind_code == industry_group else None
                        cur.execute(
                            """
                            INSERT INTO clean_tenant_industries
                                (id, company_id, industry_code, subtype_code, is_primary, created_at)
                            VALUES (gen_random_uuid(), %s, %s, %s, %s, %s)
                            ON CONFLICT (company_id, industry_code) DO NOTHING
                            """,
                            (company.id, ind_code, sub, is_primary, now),
                        )
                conn.commit()

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

    # ------------------------------------------------------------------
    # Assistant memory (permanent "zapamatuj si" entries)
    # ------------------------------------------------------------------

    def list_assistant_memory(self, company_id: str, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, memory_type, content, updated_at, created_by, company_id "
                    "FROM clean_assistant_memory WHERE company_id = %s "
                    "ORDER BY updated_at DESC LIMIT %s",
                    (company_id, limit),
                )
                rows = cur.fetchall()
        return [
            {
                "id": str(r["id"]),
                "company_id": str(r["company_id"]),
                "created_by": str(r["created_by"]) if r["created_by"] else None,
                "memory_type": r["memory_type"],
                "content": r["content"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def add_assistant_memory(
        self, company_id: str, created_by: str | None, content: str, memory_type: str = "long"
    ) -> dict:
        memory_id = str(uuid4())
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_assistant_memory "
                    "(id, company_id, created_by, memory_type, content) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING updated_at",
                    (memory_id, company_id, created_by, memory_type, content),
                )
                updated_at = cur.fetchone()["updated_at"]
            conn.commit()
        return {
            "id": memory_id,
            "company_id": company_id,
            "created_by": created_by,
            "memory_type": memory_type,
            "content": content,
            "updated_at": updated_at,
        }

    def delete_assistant_memory(self, memory_id: str, company_id: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM clean_assistant_memory WHERE id = %s AND company_id = %s",
                    (memory_id, company_id),
                )
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Activity log (admin-visible "who did what")
    # ------------------------------------------------------------------

    def log_activity(
        self,
        company_id: str,
        actor_user_id: str | None,
        entity_type: str,
        entity_id: str,
        action: str,
        description: str,
        source_channel: str = "app",
        details: dict | None = None,
    ) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_activity_log "
                    "(id, company_id, actor_user_id, entity_type, entity_id, action, "
                    " description, source_channel, details) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(uuid4()), company_id, actor_user_id, entity_type, entity_id,
                        action, description, source_channel, json.dumps(details or {}),
                    ),
                )
            conn.commit()

    def list_activity_log(
        self, company_id: str, limit: int = 200, actor_user_id: str | None = None
    ) -> list[dict]:
        sql = (
            "SELECT a.id, a.entity_type, a.entity_id, a.action, a.description, "
            "       a.source_channel, a.created_at, a.actor_user_id, a.details, "
            "       COALESCE(u.display_name, '') AS actor_display_name, "
            "       COALESCE(u.email, '') AS actor_email "
            "FROM clean_activity_log a "
            "LEFT JOIN clean_users u ON u.id = a.actor_user_id "
            "WHERE a.company_id = %s"
        )
        params: list = [company_id]
        if actor_user_id:
            sql += " AND a.actor_user_id = %s"
            params.append(actor_user_id)
        sql += " ORDER BY a.created_at DESC LIMIT %s"
        params.append(limit)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        out = []
        for r in rows:
            details = r["details"]
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    details = {}
            out.append({
                "id": str(r["id"]),
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "action": r["action"],
                "description": r["description"],
                "source_channel": r["source_channel"],
                "created_at": r["created_at"],
                "actor_user_id": str(r["actor_user_id"]) if r["actor_user_id"] else None,
                "actor_display_name": r["actor_display_name"],
                "actor_email": r["actor_email"],
                "details": details if isinstance(details, dict) else {},
            })
        return out

    def create_user(self, company_id: str, email: str, password: str, display_name: str,
                    role: str = "worker", first_name: str | None = None,
                    last_name: str | None = None, phone: str | None = None,
                    preferred_language_code: str | None = None) -> UserAccount:
        from secretary_clean.core.security import hash_password
        from secretary_clean.core.language import normalize_language_code
        try:
            user_role = Role(role)
        except ValueError:
            # Unknown role string falls back to the lowest general role.
            # (Role.worker does not exist; valid roles: owner/admin/manager/staff/accountant.)
            user_role = Role.staff
        normalized_email = email.lower()
        # check duplicate
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_users WHERE email = %s", (normalized_email,))
                if cur.fetchone()["n"] > 0:
                    raise ValueError("Email already exists")
        user_id = str(uuid4())
        password_hash = hash_password(password)
        norm_lang = normalize_language_code(preferred_language_code) if preferred_language_code else None
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO clean_users
                        (id, company_id, email, display_name, role, is_active,
                         preferred_language_code, first_name, last_name, phone, password_hash,
                         must_change_password)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (user_id, company_id, normalized_email, display_name, user_role.value,
                     True, norm_lang, first_name, last_name, phone, password_hash, True),
                )
            conn.commit()
        return self._build_user_account(user_id)

    def update_user(self, user_id: str, company_id: str, **fields) -> UserAccount:
        user = self.get_user(user_id)
        if not user or user.company_id != company_id:
            raise KeyError("User not found")
        set_parts = []
        values = []
        field_map = {"display_name": "display_name", "first_name": "first_name",
                     "last_name": "last_name", "phone": "phone",
                     "preferred_language_code": "preferred_language_code", "is_active": "is_active"}
        for k, col in field_map.items():
            if k in fields and fields[k] is not None:
                set_parts.append(f"{col} = %s")
                values.append(fields[k])
        if "role" in fields and fields["role"] is not None:
            try:
                Role(fields["role"])
                set_parts.append("role = %s")
                values.append(fields["role"])
            except ValueError:
                pass
        if not set_parts:
            return user
        values.append(user_id)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE clean_users SET {', '.join(set_parts)} WHERE id = %s", values)
            conn.commit()
        return self._build_user_account(user_id)

    def delete_user(self, user_id: str, company_id: str) -> bool:
        user = self.get_user(user_id)
        if not user or user.company_id != company_id:
            raise KeyError("User not found")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE clean_users SET is_active = FALSE WHERE id = %s", (user_id,))
            conn.commit()
        return True

    def change_password(self, user_id: str, current_password: str, new_password: str) -> bool:
        from secretary_clean.core.security import hash_password, verify_password
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM clean_users WHERE id = %s", (user_id,))
                row = cur.fetchone()
        if not row:
            raise KeyError("User not found")
        if not verify_password(current_password, row["password_hash"]):
            return False
        new_hash = hash_password(new_password)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_users SET password_hash = %s, must_change_password = FALSE, updated_at = now() WHERE id = %s",
                    (new_hash, user_id),
                )
            conn.commit()
        return True

    # ------------------------------------------------------------------
    # Helper row mappers (restored — were accidentally removed)
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
            must_change_password=bool(row.get("must_change_password", False)),
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
            created_at=_ensure_utc(row.get("created_at")),
            updated_at=_ensure_utc(row.get("updated_at")),
        )

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

    def update_company_industry(
        self, company_id: str, industry_group: str | None, industry_subtype: str | None
    ) -> TenantOperatingProfile:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute(
                    """
                    UPDATE clean_companies
                    SET industry_group = %s, industry_subtype = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (industry_group, industry_subtype, company_id),
                )
                cur.execute(
                    """
                    INSERT INTO tenant_operating_profile (company_id, industry_group, industry_subtype)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (company_id) DO UPDATE SET
                        industry_group = EXCLUDED.industry_group,
                        industry_subtype = EXCLUDED.industry_subtype
                    """,
                    (company_id, industry_group, industry_subtype),
                )
                # Phase A1: keep multi-industry table in sync with legacy single field.
                cur.execute(
                    "DELETE FROM clean_tenant_industries WHERE company_id = %s", (company_id,)
                )
                if industry_group:
                    cur.execute(
                        """
                        INSERT INTO clean_tenant_industries
                            (id, company_id, industry_code, subtype_code, is_primary)
                        VALUES (gen_random_uuid(), %s, %s, %s, TRUE)
                        ON CONFLICT (company_id, industry_code) DO NOTHING
                        """,
                        (company_id, industry_group, industry_subtype),
                    )
            conn.commit()
        return self.get_tenant_operating_profile(company_id)

    def get_tenant_industries(self, company_id: str) -> list[TenantIndustry]:
        """Phase A1: return all industries for a tenant; fall back to legacy field."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute(
                    """
                    SELECT industry_code, subtype_code, is_primary
                    FROM clean_tenant_industries
                    WHERE company_id = %s
                    ORDER BY is_primary DESC, created_at ASC
                    """,
                    (company_id,),
                )
                rows = cur.fetchall()
                if rows:
                    return [
                        TenantIndustry(
                            industry_code=r["industry_code"],
                            subtype_code=r["subtype_code"],
                            is_primary=r["is_primary"],
                        )
                        for r in rows
                    ]
                # Backward compat: synthesize from legacy single field
                cur.execute(
                    "SELECT industry_group, industry_subtype FROM clean_companies WHERE id = %s",
                    (company_id,),
                )
                legacy = cur.fetchone()
        if legacy and legacy["industry_group"]:
            return [
                TenantIndustry(
                    industry_code=legacy["industry_group"],
                    subtype_code=legacy["industry_subtype"],
                    is_primary=True,
                )
            ]
        return []

    def set_tenant_industries(
        self, company_id: str, industries: list[TenantIndustry]
    ) -> list[TenantIndustry]:
        """Phase A1: replace the full set of industries; mirror primary to legacy fields."""
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

        primary = next((i for i in cleaned if i.is_primary), None)
        legacy_group = primary.industry_code if primary else None
        legacy_subtype = primary.subtype_code if primary else None

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM clean_companies WHERE id = %s", (company_id,))
                if cur.fetchone()["n"] == 0:
                    raise KeyError("Company not found")
                cur.execute(
                    "DELETE FROM clean_tenant_industries WHERE company_id = %s", (company_id,)
                )
                for ind in cleaned:
                    cur.execute(
                        """
                        INSERT INTO clean_tenant_industries
                            (id, company_id, industry_code, subtype_code, is_primary)
                        VALUES (gen_random_uuid(), %s, %s, %s, %s)
                        ON CONFLICT (company_id, industry_code) DO NOTHING
                        """,
                        (company_id, ind.industry_code, ind.subtype_code, ind.is_primary),
                    )
                # Mirror primary into legacy single fields
                cur.execute(
                    """
                    UPDATE clean_companies
                    SET industry_group = %s, industry_subtype = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (legacy_group, legacy_subtype, company_id),
                )
                cur.execute(
                    """
                    INSERT INTO tenant_operating_profile (company_id, industry_group, industry_subtype)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (company_id) DO UPDATE SET
                        industry_group = EXCLUDED.industry_group,
                        industry_subtype = EXCLUDED.industry_subtype
                    """,
                    (company_id, legacy_group, legacy_subtype),
                )
            conn.commit()
        return cleaned

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
                    f"SELECT * FROM {table} WHERE company_id = %s AND status != 'deleted' ORDER BY created_at",
                    (company_id,),
                )
                rows = cur.fetchall()
        return [self._row_to_crm_record(r, module) for r in rows]

    def find_duplicate_client(self, company_id: str, name: str | None = None,
                              phone: str | None = None, email: str | None = None):
        """Return an existing client that looks like a duplicate, or None.
        Match priority (blueprint S9): phone, then email, then exact name."""
        def norm_phone(p):
            return "".join(ch for ch in (p or "") if ch.isdigit())
        def norm_txt(s):
            return " ".join((s or "").lower().split())
        np, ne, nn = norm_phone(phone), norm_txt(email), norm_txt(name)
        for rec in self.list_crm_records("clients", company_id):
            d = rec.data or {}
            if np and norm_phone(d.get("phone")) == np:
                return rec
            if ne and norm_txt(d.get("email")) == ne:
                return rec
            if nn and norm_txt(rec.name) == nn:
                return rec
        return None

    def get_crm_record(self, module: str, record_id: str, company_id: str) -> CRMRecord | None:
        if module not in _VALID_CRM_MODULES:
            raise KeyError("Unknown CRM module")
        table = _CRM_TABLES[module]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {table} WHERE id = %s AND company_id = %s",
                    (record_id, company_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        return self._row_to_crm_record(row, module)

    def update_crm_record(self, module: str, record_id: str, company_id: str, payload: CRMUpdateRequest) -> CRMRecord:
        if module not in _VALID_CRM_MODULES:
            raise KeyError("Unknown CRM module")
        table = _CRM_TABLES[module]
        # Fetch current record first (tenant-safe)
        existing = self.get_crm_record(module, record_id, company_id)
        if not existing:
            raise KeyError("Record not found")
        set_clauses = []
        params: list = []
        if payload.name is not None:
            set_clauses.append("name = %s")
            params.append(payload.name)
        if payload.status is not None:
            set_clauses.append("status = %s")
            params.append(payload.status)
        if payload.data is not None:
            # Merge: existing data || new data (jsonb merge)
            set_clauses.append("data = data || %s::jsonb")
            params.append(json.dumps(payload.data))
        set_clauses.append("updated_at = now()")
        params.extend([record_id, company_id])
        set_sql = ", ".join(set_clauses)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {table} SET {set_sql} WHERE id = %s AND company_id = %s RETURNING *",
                    params,
                )
                row = cur.fetchone()
            conn.commit()
        return self._row_to_crm_record(row, module)

    def delete_crm_record(self, module: str, record_id: str, company_id: str) -> bool:
        """Soft-delete: sets status='deleted'. Never hard-deletes for audit safety."""
        if module not in _VALID_CRM_MODULES:
            raise KeyError("Unknown CRM module")
        table = _CRM_TABLES[module]
        existing = self.get_crm_record(module, record_id, company_id)
        if not existing:
            raise KeyError("Record not found")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {table} SET status = 'deleted', updated_at = now() WHERE id = %s AND company_id = %s",
                    (record_id, company_id),
                )
            conn.commit()
        return True

    def add_crm_note(self, module: str, record_id: str, company_id: str, note: NoteCreateRequest, author_id: str) -> CRMRecord:
        """Append a timestamped note to data['notes'] array in JSONB."""
        if module not in _VALID_CRM_MODULES:
            raise KeyError("Unknown CRM module")
        table = _CRM_TABLES[module]
        existing = self.get_crm_record(module, record_id, company_id)
        if not existing:
            raise KeyError("Record not found")
        note_entry = {
            "id": str(uuid4()),
            "content": note.content,
            "author_id": author_id,
            "author_name": note.author_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Append to notes array within JSONB, creating it if absent
                cur.execute(
                    f"""
                    UPDATE {table}
                    SET data = jsonb_set(
                        data,
                        '{{notes}}',
                        coalesce(data->'notes', '[]'::jsonb) || %s::jsonb
                    ),
                    updated_at = now()
                    WHERE id = %s AND company_id = %s
                    RETURNING *
                    """,
                    (json.dumps([note_entry]), record_id, company_id),
                )
                row = cur.fetchone()
            conn.commit()
        return self._row_to_crm_record(row, module)

    # ------------------------------------------------------------------
    # Work Reports
    # ------------------------------------------------------------------

    def create_work_report(self, company_id: str, payload: WorkReportCreate) -> CRMRecord:
        data = payload.model_dump()
        work_date = data.get("work_date") or datetime.now(timezone.utc).date().isoformat()
        name = f"Work Report {work_date}"
        data["invoiced"] = False
        record_id = str(uuid4())
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_work_reports (id, company_id, name, status, data) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (record_id, company_id, name, "open", json.dumps(data)),
                )
            conn.commit()
        return self.get_crm_record("work_reports", record_id, company_id)

    def list_work_reports(self, company_id: str) -> list[CRMRecord]:
        return self.list_crm_records("work_reports", company_id)

    def get_work_report(self, record_id: str, company_id: str) -> CRMRecord | None:
        return self.get_crm_record("work_reports", record_id, company_id)

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
        activities = wr.data.get("activities", [])
        entries = wr.data.get("entries", [])
        materials = wr.data.get("materials", [])
        waste = wr.data.get("waste", [])

        line_items = []
        calculated_total = 0.0

        # ── activities (catalogue work + its set price) ──────────────────────
        from secretary_clean.core import invoicing as _inv
        _rate_by_code: dict[str, float] = {}
        try:
            for tp in self.list_tenant_pricing(company_id):
                if tp.rate and tp.rate > 0:
                    _rate_by_code[tp.activity_code] = float(tp.rate)
        except Exception:
            pass
        _act_items, _act_total, _act_warnings = _inv.activity_line_items(activities, _rate_by_code)
        line_items.extend(_act_items)
        calculated_total += _act_total

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

        pricing_warnings: list[str] = list(_act_warnings)
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

        # Try to resolve client name
        client_name = ""
        if client_id:
            client_rec = self.get_crm_record("clients", client_id, company_id)
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
        invoice_id = str(uuid4())

        with self._conn() as conn:
            with conn.cursor() as cur:
                # Create invoice
                cur.execute(
                    "INSERT INTO clean_invoices (id, company_id, name, status, data) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (invoice_id, company_id, invoice_name, "draft", json.dumps(invoice_data)),
                )
                # Mark work report as invoiced
                cur.execute(
                    """
                    UPDATE clean_work_reports
                    SET data = data || %s::jsonb, updated_at = now()
                    WHERE id = %s AND company_id = %s
                    """,
                    (json.dumps({"invoiced": True, "invoice_id": invoice_id}),
                     wr.id, company_id),
                )
            conn.commit()

        return self.get_crm_record("invoices", invoice_id, company_id)

    # ------------------------------------------------------------------
    # Wipe
    # ------------------------------------------------------------------

    def wipe_all_data(self) -> None:
        """Delete ALL tenant/user/company data. Preserves catalogue tables.
        After this call GET /bootstrap/status returns is_ready=False.
        Only truncates tables that actually exist — safe against schema differences."""
        candidates = [
            "clean_tenant_activity_pricing",
            "clean_backup_manifests",
            "clean_user_biometrics",
            "clean_password_reset_tokens",
            "clean_refresh_tokens",
            "clean_communications",
            "clean_work_reports",
            "clean_invoices",
            "clean_quotes",
            "clean_tasks",
            "clean_jobs",
            "clean_clients",
            "clean_permissions",
            "clean_users",
            "tenant_languages",
            "clean_tenant_configuration",
            "tenant_operating_profile",
            "clean_company_operating_settings",
            "clean_companies",
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(%s)",
                    (candidates,),
                )
                existing = {row[0] for row in cur.fetchall()}
                for table in candidates:
                    if table in existing:
                        cur.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
                    else:
                        logger.warning("wipe_all_data: table %s not found, skipping", table)
            conn.commit()
        logger.info("wipe_all_data: done. wiped=%s", [t for t in candidates if t in existing])

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------

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
                    "UPDATE clean_users SET password_hash = %s, must_change_password = TRUE, updated_at = now() WHERE id = %s",
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

    def list_company_ids(self) -> list[str]:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                # Pool default cursor is RealDictCursor — rows are dicts.
                cur.execute("SELECT id FROM clean_companies ORDER BY created_at")
                rows = cur.fetchall()
        return [str(r["id"]) for r in rows]

    # ------------------------------------------------------------------
    # Tenant service rates (clean_tenant_service_rates)
    # ------------------------------------------------------------------

    @staticmethod
    def _service_rate_row(row: dict) -> dict:
        return {
            "id": str(row["id"]),
            "company_id": str(row["company_id"]),
            "rate_type": row["rate_type"],
            "description": row["description"],
            "rate": float(row["rate"]),
            "currency": row["currency"],
            "is_builtin": row["is_builtin"],
            "is_active": row["is_active"],
            "sort_order": row["sort_order"],
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def list_tenant_service_rates(self, company_id: str) -> list[dict]:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM clean_tenant_service_rates
                    WHERE company_id = %s AND is_active = TRUE
                    ORDER BY sort_order, rate_type
                    """,
                    (company_id,),
                )
                rows = cur.fetchall()
        return [self._service_rate_row(dict(r)) for r in rows]

    def create_tenant_service_rate(
        self, company_id: str, rate_type: str, description: str = "",
        rate: float = 0.0, currency: str = "GBP", is_builtin: bool = False,
    ) -> dict:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, is_active FROM clean_tenant_service_rates "
                    "WHERE company_id = %s AND rate_type = %s",
                    (company_id, rate_type),
                )
                existing = cur.fetchone()
                if existing and existing["is_active"]:
                    raise ValueError(f"Rate type '{rate_type}' already exists")
                if existing:
                    # Re-activate a soft-deleted rate type instead of violating
                    # the (company_id, rate_type) unique constraint.
                    cur.execute(
                        """
                        UPDATE clean_tenant_service_rates
                        SET description = %s, rate = %s, currency = %s,
                            is_builtin = %s, is_active = TRUE, updated_at = now()
                        WHERE id = %s
                        RETURNING *
                        """,
                        (description, rate, currency, is_builtin, existing["id"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO clean_tenant_service_rates
                            (id, company_id, rate_type, description, rate, currency,
                             is_builtin, is_active, sort_order)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE,
                                COALESCE((SELECT MAX(sort_order) FROM clean_tenant_service_rates
                                          WHERE company_id = %s), 0) + 1)
                        RETURNING *
                        """,
                        (str(uuid4()), company_id, rate_type, description, rate,
                         currency, is_builtin, company_id),
                    )
                row = cur.fetchone()
            conn.commit()
        return self._service_rate_row(dict(row))

    def set_tenant_service_rate_amounts(
        self, company_id: str, amounts: dict[str, float]
    ) -> list[dict]:
        """Bulk rate update for existing rate types; unknown keys are ignored."""
        if amounts:
            with _PooledConnection(self._pool) as conn:
                with conn.cursor() as cur:
                    for rate_type, rate in amounts.items():
                        cur.execute(
                            """
                            UPDATE clean_tenant_service_rates
                            SET rate = %s, updated_at = now()
                            WHERE company_id = %s AND rate_type = %s AND is_active = TRUE
                            """,
                            (float(rate), company_id, rate_type),
                        )
                conn.commit()
        return self.list_tenant_service_rates(company_id)

    def delete_tenant_service_rate(self, company_id: str, rate_type: str) -> None:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, is_builtin, is_active FROM clean_tenant_service_rates "
                    "WHERE company_id = %s AND rate_type = %s",
                    (company_id, rate_type),
                )
                row = cur.fetchone()
                if not row or not row["is_active"]:
                    raise KeyError(f"Rate type '{rate_type}' not found")
                if row["is_builtin"]:
                    raise ValueError("Built-in rate types cannot be deleted")
                cur.execute(
                    "UPDATE clean_tenant_service_rates "
                    "SET is_active = FALSE, updated_at = now() WHERE id = %s",
                    (row["id"],),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Biometrics
    # ------------------------------------------------------------------

    def save_biometric(
        self,
        bio_id: str,
        user_id: str,
        device_id: str,
        biometric_hash: str,
        label: str | None = None,
    ) -> None:
        """Upsert a biometric hash for a user/device pair."""
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clean_user_biometrics
                        (id, user_id, device_id, biometric_hash, label, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, TRUE, now(), now())
                    ON CONFLICT (user_id, device_id)
                    DO UPDATE SET
                        biometric_hash = EXCLUDED.biometric_hash,
                        label = EXCLUDED.label,
                        is_active = TRUE,
                        updated_at = now()
                    """,
                    (bio_id, user_id, device_id, biometric_hash, label),
                )
            conn.commit()

    def get_biometric_hashes(self, user_id: str) -> list[str]:
        """Return all active biometric hashes for a user."""
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT biometric_hash FROM clean_user_biometrics "
                    "WHERE user_id = %s AND is_active = TRUE",
                    (user_id,),
                )
                rows = cur.fetchall()
        return [r["biometric_hash"] for r in rows]

    def deactivate_biometric(self, user_id: str, device_id: str) -> bool:
        """Deactivate a biometric entry. Returns True if a row was affected."""
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_user_biometrics SET is_active = FALSE, updated_at = now() "
                    "WHERE user_id = %s AND device_id = %s AND is_active = TRUE",
                    (user_id, device_id),
                )
                affected = cur.rowcount
            conn.commit()
        return affected > 0

    # ------------------------------------------------------------------
    # Backup manifests
    # ------------------------------------------------------------------

    def save_backup_manifest(
        self,
        backup_id: str,
        company_id: str,
        created_by_user_id: str,
        created_by_role: str,
        backup_scope: str,
        includes_db_reference: bool,
        storage_location: str,
        restore_token: str | None,
        restore_token_expires_at,
        payload: dict,
    ) -> None:
        import json as _json
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clean_backup_manifests
                        (id, company_id, created_by_user_id, created_by_role,
                         backup_scope, includes_db_reference, storage_location,
                         restore_token, restore_token_expires_at, payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (
                        backup_id, company_id, created_by_user_id, created_by_role,
                        backup_scope, includes_db_reference, storage_location,
                        restore_token, restore_token_expires_at,
                        psycopg2.extras.Json(payload),
                    ),
                )
            conn.commit()

    def list_backup_manifests(self, company_id: str) -> list[dict]:
        """Return summary rows for all server-stored backups for a company."""
        from secretary_clean.core.models import BackupRestoreInfo, BackupScope
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, company_id, created_by_role, backup_scope,
                           includes_db_reference, created_at
                    FROM clean_backup_manifests
                    WHERE company_id = %s
                    ORDER BY created_at DESC
                    """,
                    (company_id,),
                )
                rows = cur.fetchall()
        results = []
        for r in rows:
            results.append(BackupRestoreInfo(
                backup_id=str(r["id"]),
                company_legal_name="",
                created_at=r["created_at"],
                backup_scope=BackupScope(r["backup_scope"]),
                includes_db_reference=bool(r["includes_db_reference"]),
            ))
        return results

    def get_backup_manifest_by_token(self, token: str) -> dict | None:
        """Return the full manifest row for a restore token."""
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, company_id, created_by_user_id, created_by_role,
                           backup_scope, includes_db_reference,
                           restore_token_expires_at, payload
                    FROM clean_backup_manifests
                    WHERE restore_token = %s
                    """,
                    (token,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Phase A2: voice session persistence (JSONB, survives restart)
    # ------------------------------------------------------------------

    def save_voice_session(self, session: dict) -> None:
        """Upsert a voice session as JSONB keyed by its 'id'."""
        sid = session["id"]
        company_id = session["company_id"]
        user_id = session.get("user_id")
        state = session.get("state", "active")
        step = session.get("step", "client")
        payload = json.dumps(session)
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clean_voice_sessions
                        (id, company_id, user_id, state, step, data, touched_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
                    ON CONFLICT (id) DO UPDATE SET
                        state = EXCLUDED.state,
                        step = EXCLUDED.step,
                        data = EXCLUDED.data,
                        touched_at = now()
                    """,
                    (sid, company_id, user_id, state, step, payload),
                )
            conn.commit()

    def load_voice_session(self, session_id: str) -> dict | None:
        """Load a voice session dict by id, or None if not found."""
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT data FROM clean_voice_sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        data = row["data"]
        # psycopg2 returns JSONB as dict already; tolerate str just in case
        if isinstance(data, str):
            return json.loads(data)
        return dict(data)

    # ------------------------------------------------------------------
    # Phase A3: calendar events
    # ------------------------------------------------------------------

    def _row_to_calendar_event(self, row: dict) -> CalendarEvent:
        return CalendarEvent(
            id=str(row["id"]),
            company_id=str(row["company_id"]),
            title=row["title"],
            description=row.get("description"),
            location=row.get("location"),
            start_at=row["start_at"],
            end_at=row.get("end_at"),
            all_day=row["all_day"],
            client_id=row.get("client_id"),
            job_id=row.get("job_id"),
            created_by=str(row["created_by"]) if row.get("created_by") else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_calendar_events(
        self, company_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[CalendarEvent]:
        clauses = ["company_id = %s"]
        params: list = [company_id]
        if start is not None:
            clauses.append("start_at >= %s")
            params.append(start)
        if end is not None:
            clauses.append("start_at <= %s")
            params.append(end)
        where = " AND ".join(clauses)
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM clean_calendar_events WHERE {where} ORDER BY start_at ASC",
                    tuple(params),
                )
                rows = cur.fetchall()
        return [self._row_to_calendar_event(r) for r in rows]

    def get_calendar_event(self, event_id: str, company_id: str) -> CalendarEvent | None:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM clean_calendar_events WHERE id = %s AND company_id = %s",
                    (event_id, company_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        return self._row_to_calendar_event(row)

    def create_calendar_event(
        self, company_id: str, payload: CalendarEventCreate, created_by: str | None = None
    ) -> CalendarEvent:
        event_id = str(uuid4())
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO clean_calendar_events
                        (id, company_id, title, description, location,
                         start_at, end_at, all_day, client_id, job_id, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (
                        event_id, company_id, payload.title, payload.description,
                        payload.location, payload.start_at, payload.end_at,
                        payload.all_day, payload.client_id, payload.job_id, created_by,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return self._row_to_calendar_event(row)

    def update_calendar_event(
        self, event_id: str, company_id: str, payload: CalendarEventUpdate
    ) -> CalendarEvent:
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not updates:
            existing = self.get_calendar_event(event_id, company_id)
            if not existing:
                raise KeyError("Calendar event not found")
            return existing
        set_parts = ", ".join(f"{col} = %s" for col in updates)
        params = list(updates.values()) + [event_id, company_id]
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""
                    UPDATE clean_calendar_events
                    SET {set_parts}, updated_at = now()
                    WHERE id = %s AND company_id = %s
                    RETURNING *
                    """,
                    tuple(params),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise KeyError("Calendar event not found")
        return self._row_to_calendar_event(row)

    def delete_calendar_event(self, event_id: str, company_id: str) -> bool:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM clean_calendar_events WHERE id = %s AND company_id = %s",
                    (event_id, company_id),
                )
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Phase A5: calendar sync log + synchronization
    # ------------------------------------------------------------------

    def add_calendar_sync_log(
        self, company_id: str, event_id: str | None, source: str,
        action: str, status: str = "ok", detail: str | None = None,
    ) -> CalendarSyncLogEntry:
        log_id = str(uuid4())
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO clean_calendar_sync_log
                        (id, company_id, event_id, source, action, status, detail)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (log_id, company_id, event_id, source, action, status, detail),
                )
                row = cur.fetchone()
            conn.commit()
        return CalendarSyncLogEntry(
            id=str(row["id"]), company_id=str(row["company_id"]),
            event_id=row.get("event_id"), source=row["source"], action=row["action"],
            status=row["status"], detail=row.get("detail"), created_at=row["created_at"],
        )

    def list_calendar_sync_log(self, company_id: str, limit: int = 100) -> list[CalendarSyncLogEntry]:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM clean_calendar_sync_log
                    WHERE company_id = %s ORDER BY created_at DESC LIMIT %s
                    """,
                    (company_id, limit),
                )
                rows = cur.fetchall()
        return [
            CalendarSyncLogEntry(
                id=str(r["id"]), company_id=str(r["company_id"]),
                event_id=r.get("event_id"), source=r["source"], action=r["action"],
                status=r["status"], detail=r.get("detail"), created_at=r["created_at"],
            )
            for r in rows
        ]

    def sync_calendar_events(
        self, company_id: str, device_events: list[CalendarSyncEventInput]
    ) -> list[CalendarSyncOutcome]:
        """Reconcile device events with backend (backend = source of truth)."""
        outcomes: list[CalendarSyncOutcome] = []
        backend_list = self.list_calendar_events(company_id)
        backend_now = {e.id: e for e in backend_list}
        seen_backend_ids: set[str] = set()

        for dev in device_events:
            if dev.backend_id and dev.backend_id in backend_now:
                seen_backend_ids.add(dev.backend_id)
                be = backend_now[dev.backend_id]
                if dev.updated_at and dev.updated_at > be.updated_at:
                    self.update_calendar_event(
                        be.id, company_id,
                        CalendarEventUpdate(
                            title=dev.title, description=dev.description, location=dev.location,
                            start_at=dev.start_at, end_at=dev.end_at, all_day=dev.all_day,
                        ),
                    )
                    self.add_calendar_sync_log(company_id, be.id, "android", "conflict_android_wins")
                    outcomes.append(CalendarSyncOutcome(
                        backend_id=be.id, android_id=dev.android_id,
                        action="conflict_android_wins", source="android",
                        detail="device updated_at newer; backend updated",
                    ))
                else:
                    self.add_calendar_sync_log(company_id, be.id, "backend", "conflict_backend_wins")
                    outcomes.append(CalendarSyncOutcome(
                        backend_id=be.id, android_id=dev.android_id,
                        action="conflict_backend_wins", source="backend",
                        detail="backend updated_at newer or equal; device must adopt backend copy",
                    ))
            else:
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

        for be_id in backend_now:
            if be_id not in seen_backend_ids:
                self.add_calendar_sync_log(company_id, be_id, "backend", "created_on_android")
                outcomes.append(CalendarSyncOutcome(
                    backend_id=be_id, android_id=None,
                    action="created_on_android", source="backend",
                    detail="backend-only event; device must create locally",
                ))
        return outcomes


    # ------------------------------------------------------------------
    # Phase A5.2: pending voice actions (JSONB, survives restart)
    # ------------------------------------------------------------------

    def _row_to_pending(self, row: dict) -> PendingVoiceAction:
        cd = row["collected_data"]
        mf = row["missing_fields"]
        if isinstance(cd, str): cd = json.loads(cd)
        if isinstance(mf, str): mf = json.loads(mf)
        return PendingVoiceAction(
            id=str(row["id"]), company_id=str(row["company_id"]),
            user_id=str(row["user_id"]) if row.get("user_id") else None,
            intent=row["intent"], status=row["status"],
            collected_data=cd or {}, missing_fields=mf or [],
            last_question=row.get("last_question"),
            created_at=row["created_at"], updated_at=row["updated_at"],
            expires_at=row.get("expires_at"),
        )

    def create_pending_action(self, action: PendingVoiceAction) -> PendingVoiceAction:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_pending_voice_actions (id, company_id, user_id, intent, status, collected_data, missing_fields, last_question, expires_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
                    (action.id, action.company_id, action.user_id, action.intent, action.status, json.dumps(action.collected_data), json.dumps(action.missing_fields), action.last_question, action.expires_at),
                )
            conn.commit()
        return action

    def get_pending_action(self, action_id: str, company_id: str) -> PendingVoiceAction | None:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM clean_pending_voice_actions WHERE id = %s AND company_id = %s", (action_id, company_id))
                row = cur.fetchone()
        return self._row_to_pending(row) if row else None

    def update_pending_action(self, action: PendingVoiceAction) -> PendingVoiceAction:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_pending_voice_actions SET status=%s, collected_data=%s::jsonb, missing_fields=%s::jsonb, last_question=%s, updated_at=now() WHERE id=%s",
                    (action.status, json.dumps(action.collected_data), json.dumps(action.missing_fields), action.last_question, action.id),
                )
            conn.commit()
        return action

    # ------------------------------------------------------------------
    # Voice Command Learning (Phase 2/3): aliases, learning events, pending
    # learnings. An alias is a phrase→intent translation only; permission is
    # re-checked at execution by the caller.
    # ------------------------------------------------------------------
    def _row_to_voice_alias(self, row: dict) -> VoiceCommandAlias:
        return VoiceCommandAlias(
            id=str(row["id"]), company_id=str(row["company_id"]),
            user_id=str(row["user_id"]) if row.get("user_id") else None,
            raw_phrase=row["raw_phrase"], normalized_phrase=row["normalized_phrase"],
            target_intent=row["target_intent"], language_code=row.get("language_code"),
            status=row["status"], confidence=float(row["confidence"]),
            source=row["source"],
            created_by=str(row["created_by"]) if row.get("created_by") else None,
            created_at=row["created_at"], updated_at=row["updated_at"],
            last_used_at=row.get("last_used_at"), use_count=row["use_count"],
            is_global=row["is_global"])

    def create_voice_alias(self, alias: VoiceCommandAlias) -> VoiceCommandAlias:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_voice_command_aliases (id, company_id, user_id, raw_phrase, normalized_phrase, target_intent, language_code, status, confidence, source, created_by, last_used_at, use_count, is_global) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (alias.id, alias.company_id, alias.user_id, alias.raw_phrase,
                     alias.normalized_phrase, alias.target_intent, alias.language_code,
                     alias.status, alias.confidence, alias.source, alias.created_by,
                     alias.last_used_at, alias.use_count, alias.is_global))
            conn.commit()
        return alias

    def get_voice_alias(self, alias_id: str, company_id: str) -> VoiceCommandAlias | None:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM clean_voice_command_aliases WHERE id=%s AND (company_id=%s OR is_global=TRUE)",
                    (alias_id, company_id))
                row = cur.fetchone()
        return self._row_to_voice_alias(row) if row else None

    def list_voice_aliases(self, company_id: str, status: str | None = None,
                           include_global: bool = True) -> list[VoiceCommandAlias]:
        clause = "(company_id=%s" + (" OR is_global=TRUE" if include_global else "") + ")"
        params: list = [company_id]
        if status is not None:
            clause += " AND status=%s"
            params.append(status)
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM clean_voice_command_aliases WHERE {clause} ORDER BY created_at",
                    tuple(params))
                rows = cur.fetchall()
        return [self._row_to_voice_alias(r) for r in rows]

    def find_voice_alias(self, company_id: str, normalized_phrase: str,
                         user_id: str | None = None) -> VoiceCommandAlias | None:
        # user-scoped first, then company-wide, then global; ACTIVE before PENDING.
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT * FROM clean_voice_command_aliases
                       WHERE normalized_phrase=%s AND status IN ('ACTIVE','PENDING')
                         AND (company_id=%s OR is_global=TRUE)
                       ORDER BY
                         (CASE WHEN company_id=%s AND user_id=%s THEN 0
                               WHEN company_id=%s AND user_id IS NULL THEN 1
                               WHEN is_global THEN 2 ELSE 3 END),
                         (status <> 'ACTIVE'), created_at
                       LIMIT 1""",
                    (normalized_phrase, company_id, company_id, user_id, company_id))
                row = cur.fetchone()
        return self._row_to_voice_alias(row) if row else None

    def update_voice_alias(self, alias: VoiceCommandAlias) -> VoiceCommandAlias:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_voice_command_aliases SET raw_phrase=%s, normalized_phrase=%s, target_intent=%s, language_code=%s, status=%s, confidence=%s, last_used_at=%s, use_count=%s, is_global=%s, updated_at=now() WHERE id=%s",
                    (alias.raw_phrase, alias.normalized_phrase, alias.target_intent,
                     alias.language_code, alias.status, alias.confidence,
                     alias.last_used_at, alias.use_count, alias.is_global, alias.id))
            conn.commit()
        return alias

    def touch_voice_alias(self, alias_id: str, company_id: str) -> VoiceCommandAlias | None:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_voice_command_aliases SET use_count=use_count+1, last_used_at=now() WHERE id=%s AND (company_id=%s OR is_global=TRUE)",
                    (alias_id, company_id))
            conn.commit()
        return self.get_voice_alias(alias_id, company_id)

    def activate_pending_voice_aliases(self, implemented_intents) -> list[VoiceCommandAlias]:
        intents = list(implemented_intents)
        if not intents:
            return []
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "UPDATE clean_voice_command_aliases SET status='ACTIVE', updated_at=now() WHERE status='PENDING' AND target_intent = ANY(%s) RETURNING *",
                    (intents,))
                rows = cur.fetchall()
            conn.commit()
        return [self._row_to_voice_alias(r) for r in rows]

    def _row_to_learning_event(self, row: dict) -> VoiceLearningEvent:
        return VoiceLearningEvent(
            id=str(row["id"]), company_id=str(row["company_id"]),
            user_id=str(row["user_id"]) if row.get("user_id") else None,
            raw_input=row["raw_input"], normalized_input=row.get("normalized_input"),
            resolved_intent=row.get("resolved_intent"),
            resolution_type=row["resolution_type"],
            confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
            was_executed=row["was_executed"], was_confirmed=row["was_confirmed"],
            created_alias_id=str(row["created_alias_id"]) if row.get("created_alias_id") else None,
            created_at=row["created_at"], metadata=row.get("metadata") or {})

    def record_voice_learning_event(self, event: VoiceLearningEvent) -> VoiceLearningEvent:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_voice_learning_events (id, company_id, user_id, raw_input, normalized_input, resolved_intent, resolution_type, confidence, was_executed, was_confirmed, created_alias_id, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (event.id, event.company_id, event.user_id, event.raw_input,
                     event.normalized_input, event.resolved_intent, event.resolution_type,
                     event.confidence, event.was_executed, event.was_confirmed,
                     event.created_alias_id, json.dumps(event.metadata)))
            conn.commit()
        return event

    def list_voice_learning_events(self, company_id: str, limit: int = 100) -> list[VoiceLearningEvent]:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM clean_voice_learning_events WHERE company_id=%s ORDER BY created_at DESC LIMIT %s",
                    (company_id, limit))
                rows = cur.fetchall()
        return [self._row_to_learning_event(r) for r in rows]

    def _row_to_pending_learning(self, row: dict) -> VoicePendingLearning:
        return VoicePendingLearning(
            id=str(row["id"]), company_id=str(row["company_id"]),
            user_id=str(row["user_id"]) if row.get("user_id") else None,
            unknown_phrase=row["unknown_phrase"],
            normalized_unknown_phrase=row.get("normalized_unknown_phrase"),
            state=row["state"], attempt_count=row["attempt_count"],
            created_at=row["created_at"], expires_at=row.get("expires_at"),
            metadata=row.get("metadata") or {})

    def create_voice_pending_learning(self, pl: VoicePendingLearning) -> VoicePendingLearning:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_voice_pending_learnings (id, company_id, user_id, unknown_phrase, normalized_unknown_phrase, state, attempt_count, expires_at, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (pl.id, pl.company_id, pl.user_id, pl.unknown_phrase,
                     pl.normalized_unknown_phrase, pl.state, pl.attempt_count,
                     pl.expires_at, json.dumps(pl.metadata)))
            conn.commit()
        return pl

    def get_voice_pending_learning(self, pl_id: str, company_id: str) -> VoicePendingLearning | None:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM clean_voice_pending_learnings WHERE id=%s AND company_id=%s",
                    (pl_id, company_id))
                row = cur.fetchone()
        return self._row_to_pending_learning(row) if row else None

    def get_active_voice_pending_learning(self, company_id: str, user_id: str | None):
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM clean_voice_pending_learnings WHERE company_id=%s AND user_id IS NOT DISTINCT FROM %s AND state IN ('WAITING_FOR_TARGET','WAITING_FOR_CONFIRMATION') ORDER BY created_at DESC LIMIT 1",
                    (company_id, user_id))
                row = cur.fetchone()
        return self._row_to_pending_learning(row) if row else None

    def update_voice_pending_learning(self, pl: VoicePendingLearning) -> VoicePendingLearning:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clean_voice_pending_learnings SET state=%s, attempt_count=%s, expires_at=%s, metadata=%s::jsonb WHERE id=%s",
                    (pl.state, pl.attempt_count, pl.expires_at, json.dumps(pl.metadata), pl.id))
            conn.commit()
        return pl

    # ------------------------------------------------------------------
    # Phase G3: Google Calendar account / sync log (tokens never logged)
    # ------------------------------------------------------------------

    def _row_to_gaccount(self, row: dict) -> GoogleCalendarAccount:
        return GoogleCalendarAccount(
            id=str(row["id"]), company_id=str(row["company_id"]),
            google_account_email=row.get("google_account_email"),
            google_calendar_id=row.get("google_calendar_id"),
            access_token=row.get("access_token"),
            refresh_token=row.get("refresh_token"),
            token_expires_at=row.get("token_expires_at"),
            scope=row.get("scope"), status=row["status"],
            auto_sync_enabled=row["auto_sync_enabled"],
            last_sync_at=row.get("last_sync_at"),
            created_at=row["created_at"], updated_at=row["updated_at"])

    def get_google_account(self, company_id: str):
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM clean_google_calendar_accounts WHERE company_id = %s", (company_id,))
                row = cur.fetchone()
        return self._row_to_gaccount(row) if row else None

    def upsert_google_account(self, account):
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_google_calendar_accounts (id, company_id, google_account_email, google_calendar_id, access_token, refresh_token, token_expires_at, scope, status, auto_sync_enabled, last_sync_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (company_id) DO UPDATE SET google_account_email=EXCLUDED.google_account_email, google_calendar_id=EXCLUDED.google_calendar_id, access_token=EXCLUDED.access_token, refresh_token=EXCLUDED.refresh_token, token_expires_at=EXCLUDED.token_expires_at, scope=EXCLUDED.scope, status=EXCLUDED.status, auto_sync_enabled=EXCLUDED.auto_sync_enabled, last_sync_at=EXCLUDED.last_sync_at, updated_at=now()",
                    (account.id, account.company_id, account.google_account_email, account.google_calendar_id, account.access_token, account.refresh_token, account.token_expires_at, account.scope, account.status, account.auto_sync_enabled, account.last_sync_at))
            conn.commit()
        return account

    def add_google_sync_log(self, company_id, direction, action, status, backend_event_id=None, google_event_id=None, detail=None):
        import uuid as _uuid
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_google_calendar_sync_log (id, company_id, direction, action, backend_event_id, google_event_id, status, detail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (str(_uuid.uuid4()), company_id, direction, action, backend_event_id, google_event_id, status, detail))
            conn.commit()

    def list_google_sync_log(self, company_id: str, limit: int = 50) -> list:
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM clean_google_calendar_sync_log WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (company_id, limit))
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_google_mapping(self, company_id: str, backend_event_id: str):
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM clean_google_calendar_mappings WHERE company_id = %s AND backend_event_id = %s", (company_id, backend_event_id))
                row = cur.fetchone()
        return dict(row) if row else None

    def set_google_mapping(self, company_id: str, backend_event_id: str, google_event_id: str):
        import uuid as _uuid
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clean_google_calendar_mappings (id, company_id, backend_event_id, google_event_id) VALUES (%s,%s,%s,%s) ON CONFLICT (company_id, backend_event_id) DO UPDATE SET google_event_id=EXCLUDED.google_event_id",
                    (str(_uuid.uuid4()), company_id, backend_event_id, google_event_id))
            conn.commit()
        return {"company_id": company_id, "backend_event_id": backend_event_id, "google_event_id": google_event_id}

    def list_google_mappings(self, company_id: str):
        with _PooledConnection(self._pool) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM clean_google_calendar_mappings WHERE company_id = %s", (company_id,))
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def delete_google_mapping(self, company_id: str, backend_event_id: str):
        with _PooledConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM clean_google_calendar_mappings WHERE company_id = %s AND backend_event_id = %s", (company_id, backend_event_id))
            conn.commit()


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
