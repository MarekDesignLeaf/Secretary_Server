import os, json, uuid, csv, io, hashlib
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from urllib.parse import urlparse, urlencode
from fastapi import FastAPI, HTTPException, Query, Depends, Header, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn
from openai import OpenAI
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import base64
import imaplib
import jwt as pyjwt
import json
import smtplib
import urllib.request
import unicodedata
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from contextlib import contextmanager, asynccontextmanager

app = FastAPI(title="Secretary CRM - DesignLeaf v1.2a")

# === JWT CONFIG ===
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    print("FATAL: JWT_SECRET is not set. Refusing to start.")
    print("Set JWT_SECRET environment variable in Railway dashboard or .env file.")
    import sys; sys.exit(1)
print("JWT auth initialized — using environment secret")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_EXPIRE_MINUTES = 60 * 24  # 24 hours
JWT_REFRESH_EXPIRE_DAYS = 30
DEFAULT_TEMP_PASSWORD = "12345"
WEATHER_CACHE_TTL_SECONDS = 600
WEATHER_CACHE: Dict[str, Dict[str, Any]] = {}

MAIL_SEARCH_FETCH_LIMIT = 200

def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}

def get_mail_config() -> Dict[str, Any]:
    username = os.getenv("MAIL_IMAP_USERNAME") or os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USERNAME")
    password = os.getenv("MAIL_IMAP_PASSWORD") or os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")
    return {
        "imap_host": os.getenv("MAIL_IMAP_HOST") or os.getenv("IMAP_HOST"),
        "imap_port": int(os.getenv("MAIL_IMAP_PORT") or os.getenv("IMAP_PORT") or "993"),
        "imap_ssl": _env_bool("MAIL_IMAP_SSL", True),
        "imap_folder": os.getenv("MAIL_IMAP_FOLDER") or "INBOX",
        "username": username,
        "password": password,
        "smtp_host": os.getenv("MAIL_SMTP_HOST") or os.getenv("SMTP_HOST"),
        "smtp_port": int(os.getenv("MAIL_SMTP_PORT") or os.getenv("SMTP_PORT") or "465"),
        "smtp_ssl": _env_bool("MAIL_SMTP_SSL", True),
        "smtp_starttls": _env_bool("MAIL_SMTP_STARTTLS", False),
        "smtp_username": os.getenv("MAIL_SMTP_USERNAME") or os.getenv("SMTP_USERNAME") or username,
        "smtp_password": os.getenv("MAIL_SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD") or password,
        "from_email": os.getenv("MAIL_FROM") or username,
    }

def ensure_mail_reader_config(cfg: Dict[str, Any]):
    missing = [name for name in ("imap_host", "username", "password") if not cfg.get(name)]
    if missing:
        raise RuntimeError("Mailbox is not configured. Set MAIL_IMAP_HOST, MAIL_IMAP_USERNAME and MAIL_IMAP_PASSWORD on the server.")

def ensure_mail_sender_config(cfg: Dict[str, Any]):
    missing = [name for name in ("smtp_host", "smtp_username", "smtp_password", "from_email") if not cfg.get(name)]
    if missing:
        raise RuntimeError("SMTP is not configured. Set MAIL_SMTP_HOST, MAIL_SMTP_USERNAME, MAIL_SMTP_PASSWORD and MAIL_FROM on the server.")

def decode_mail_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()

def extract_mail_text(message) -> str:
    if message.is_multipart():
        html_fallback = ""
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            try:
                content = part.get_content()
            except Exception:
                continue
            if not isinstance(content, str):
                continue
            if content_type == "text/plain" and content.strip():
                return content.strip()
            if content_type == "text/html" and content.strip() and not html_fallback:
                html_fallback = re.sub(r"<[^>]+>", " ", content)
        return re.sub(r"\s+", " ", html_fallback).strip()
    try:
        content = message.get_content()
        return content.strip() if isinstance(content, str) else ""
    except Exception:
        return ""

def parse_mail_message(uid: str, raw: bytes, include_body: bool = True) -> Dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    sent_at = ""
    try:
        sent = parsedate_to_datetime(message.get("Date"))
        sent_at = sent.isoformat()
    except Exception:
        sent_at = message.get("Date") or ""
    body = extract_mail_text(message) if include_body else ""
    return {
        "uid": uid,
        "message_id": message.get("Message-ID") or "",
        "subject": decode_mail_header(message.get("Subject")),
        "from": decode_mail_header(message.get("From")),
        "to": decode_mail_header(message.get("To")),
        "reply_to": decode_mail_header(message.get("Reply-To")),
        "date": sent_at,
        "body": body,
        "summary": re.sub(r"\s+", " ", body).strip()[:350],
    }

def connect_imap_mailbox(cfg: Dict[str, Any]):
    ensure_mail_reader_config(cfg)
    if cfg["imap_ssl"]:
        mailbox = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
    else:
        mailbox = imaplib.IMAP4(cfg["imap_host"], cfg["imap_port"])
    mailbox.login(cfg["username"], cfg["password"])
    mailbox.select(cfg["imap_folder"], readonly=True)
    return mailbox

def fetch_mail_by_uid(uid: str) -> Optional[Dict[str, Any]]:
    cfg = get_mail_config()
    mailbox = connect_imap_mailbox(cfg)
    try:
        status, data = mailbox.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not data:
            return None
        for item in data:
            if isinstance(item, tuple):
                return parse_mail_message(uid, item[1], include_body=True)
        return None
    finally:
        try: mailbox.close()
        except Exception: pass
        try: mailbox.logout()
        except Exception: pass

def search_mail_messages(query: str = "", sender: str = "", unread_only: bool = False, limit: int = 5) -> List[Dict[str, Any]]:
    cfg = get_mail_config()
    mailbox = connect_imap_mailbox(cfg)
    try:
        criteria = "UNSEEN" if unread_only else "ALL"
        status, data = mailbox.uid("search", None, criteria)
        if status != "OK" or not data:
            return []
        uids = data[0].split()[-MAIL_SEARCH_FETCH_LIMIT:]
        needles = [n.lower() for n in [query.strip(), sender.strip()] if n.strip()]
        matches: List[Dict[str, Any]] = []
        for raw_uid in reversed(uids):
            uid = raw_uid.decode("ascii", errors="ignore")
            status, msg_data = mailbox.uid("fetch", uid, "(RFC822)")
            if status != "OK":
                continue
            for item in msg_data:
                if not isinstance(item, tuple):
                    continue
                parsed = parse_mail_message(uid, item[1], include_body=True)
                haystack = " ".join([
                    parsed.get("subject", ""),
                    parsed.get("from", ""),
                    parsed.get("to", ""),
                    parsed.get("body", ""),
                ]).lower()
                if needles and not all(n in haystack for n in needles):
                    continue
                matches.append(parsed)
                break
            if len(matches) >= max(1, min(limit, 20)):
                break
        return matches
    finally:
        try: mailbox.close()
        except Exception: pass
        try: mailbox.logout()
        except Exception: pass

def send_mail_reply(original: Dict[str, Any], body: str) -> Dict[str, Any]:
    cfg = get_mail_config()
    ensure_mail_sender_config(cfg)
    to_addr = original.get("reply_to") or original.get("from")
    if not to_addr:
        raise RuntimeError("Original email has no sender address to reply to.")
    subject = original.get("subject") or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}".strip()
    message = EmailMessage()
    message["From"] = cfg["from_email"]
    message["To"] = to_addr
    message["Subject"] = subject
    if original.get("message_id"):
        message["In-Reply-To"] = original["message_id"]
        message["References"] = original["message_id"]
    message.set_content(body.strip())
    if cfg["smtp_ssl"]:
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as smtp:
            smtp.login(cfg["smtp_username"], cfg["smtp_password"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as smtp:
            if cfg["smtp_starttls"]:
                smtp.starttls()
            smtp.login(cfg["smtp_username"], cfg["smtp_password"])
            smtp.send_message(message)
    return {"to": to_addr, "subject": subject}

# === AUTH MIDDLEWARE: Protect /crm/*, /process, /voice/*, /work-reports* ===
PROTECTED_PREFIXES = ["/crm/", "/plants/", "/mushrooms/", "/nature/", "/admin/", "/assistant/", "/process", "/voice/", "/work-reports"]
PUBLIC_PATHS = ["/health", "/auth/login", "/auth/refresh", "/docs", "/openapi.json", "/", "/onboarding/industry-groups", "/onboarding/industry-subtypes", "/onboarding/presets"]

PERMISSION_DEFINITIONS = [
    {"permission_code": "crm_read", "module_name": "crm", "name": "View CRM", "description": "Read clients, jobs, leads and invoices."},
    {"permission_code": "crm_write", "module_name": "crm", "name": "Edit CRM", "description": "Create and update CRM records."},
    {"permission_code": "crm_delete", "module_name": "crm", "name": "Delete CRM", "description": "Delete CRM records."},
    {"permission_code": "calendar_read", "module_name": "calendar", "name": "View calendar", "description": "Read calendar data and availability."},
    {"permission_code": "calendar_write", "module_name": "calendar", "name": "Edit calendar", "description": "Create and update calendar entries."},
    {"permission_code": "contacts_read", "module_name": "contacts", "name": "View contacts", "description": "Read synced contacts and client contact details."},
    {"permission_code": "contacts_write", "module_name": "contacts", "name": "Edit contacts", "description": "Create and update contact records."},
    {"permission_code": "contacts_manage", "module_name": "contacts", "name": "Manage contact sorting", "description": "Sort and categorize contacts (admin only)."},
    {"permission_code": "voice_commands", "module_name": "assistant", "name": "Voice commands", "description": "Use voice commands and guided voice workflows."},
    {"permission_code": "settings_access", "module_name": "settings", "name": "Settings access", "description": "Open and change application settings."},
    {"permission_code": "import_data", "module_name": "data", "name": "Import data", "description": "Run imports and ingest external data."},
    {"permission_code": "export_data", "module_name": "data", "name": "Export data", "description": "Export CRM and operational data."},
    {"permission_code": "manage_users", "module_name": "users", "name": "Manage users", "description": "Create users, edit rights and remove users."},
]

ROLE_PERMISSION_DEFAULTS = {
    "admin": {
        "crm_read": True,
        "crm_write": True,
        "crm_delete": True,
        "calendar_read": True,
        "calendar_write": True,
        "contacts_read": True,
        "contacts_write": True,
        "contacts_manage": True,
        "voice_commands": True,
        "settings_access": True,
        "import_data": True,
        "export_data": True,
        "manage_users": True,
    },
    "manager": {
        "crm_read": True,
        "crm_write": True,
        "crm_delete": False,
        "calendar_read": True,
        "calendar_write": True,
        "contacts_read": True,
        "contacts_write": True,
        "voice_commands": True,
        "settings_access": True,
        "import_data": True,
        "export_data": True,
        "manage_users": False,
    },
    "worker": {
        "crm_read": True,
        "crm_write": False,
        "crm_delete": False,
        "calendar_read": True,
        "calendar_write": True,
        "contacts_read": True,
        "contacts_write": False,
        "voice_commands": True,
        "settings_access": False,
        "import_data": False,
        "export_data": False,
        "manage_users": False,
    },
    "assistant": {
        "crm_read": True,
        "crm_write": True,
        "crm_delete": False,
        "calendar_read": True,
        "calendar_write": True,
        "contacts_read": True,
        "contacts_write": True,
        "voice_commands": True,
        "settings_access": True,
        "import_data": False,
        "export_data": False,
        "manage_users": False,
    },
    "viewer": {
        "crm_read": True,
        "crm_write": False,
        "crm_delete": False,
        "calendar_read": True,
        "calendar_write": False,
        "contacts_read": True,
        "contacts_write": False,
        "voice_commands": False,
        "settings_access": False,
        "import_data": False,
        "export_data": False,
        "manage_users": False,
    },
}

ALL_PERMISSION_CODES = [p["permission_code"] for p in PERMISSION_DEFINITIONS]

CLIENT_SERVICE_RATE_KEYS = [
    "garden_maintenance",
    "hedge_trimming",
    "arborist_works",
    "hourly_rate",
    "garden_waste_bulkbag",
    "minimum_charge",
]

VISIBLE_CLIENT_SERVICE_RATE_KEYS = [
    "garden_maintenance",
    "hedge_trimming",
    "arborist_works",
    "garden_waste_bulkbag",
    "minimum_charge",
]

SERVICE_RATE_DEFAULTS = {
    "garden_maintenance": 27.0,
    "hedge_trimming": 31.0,
    "arborist_works": 34.0,
    "hourly_rate": 27.0,
    "garden_waste_bulkbag": 55.0,
    "minimum_charge": 150.0,
}

DEFAULT_CONTACT_SECTIONS = [
    ("client",                  "Klienti",                    10),
    ("employee",                "Zaměstnanci",                20),
    ("subcontractor",           "Subdodavatelé",              30),
    ("equipment_vehicle_rental","Půjčovny",                   40),
    ("material_supplier",       "Dodavatelé materiálu",       50),
    ("private",                 "Soukromé kontakty",          60),
    ("other",                   "Ostatní",                   100),
]

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Skip public paths
    if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
        return await call_next(request)
    # Check if path requires auth
    needs_auth = any(path.startswith(p) for p in PROTECTED_PREFIXES)
    if not needs_auth:
        return await call_next(request)
    # Verify JWT
    auth_header = request.headers.get("authorization", "")
    if not auth_header:
        return JSONResponse(status_code=401, content={"detail": "Authorization header required"})
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else auth_header
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return JSONResponse(status_code=401, content={"detail": "Not an access token"})
        request.state.user = payload
    except pyjwt.ExpiredSignatureError:
        return JSONResponse(status_code=401, content={"detail": "Token expired"})
    except pyjwt.InvalidTokenError:
        return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    return await call_next(request)

def get_request_tenant_id(request: Request) -> int:
    """Extract tenant_id from authenticated request. Falls back to 1."""
    try: return request.state.user.get("tenant_id", 1)
    except: return 1

def get_request_user_payload(request: Request) -> dict:
    try:
        return request.state.user
    except Exception:
        raise HTTPException(401, "Authentication required")

def ensure_request_permissions(request: Request, *permission_codes) -> dict:
    user = get_request_user_payload(request)
    conn = get_db_conn()
    try:
        permissions = get_effective_permissions(conn, user["tenant_id"], user["user_id"], user.get("role"))
        if not all(permissions.get(code, False) for code in permission_codes):
            raise HTTPException(403, "Permission denied")
        return user
    finally:
        release_conn(conn)

def check_subscription_limit(conn, tenant_id, resource_type):
    """Check if tenant has reached subscription limit. Returns (ok, message)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM subscription_limits WHERE tenant_id=%s",(tenant_id,))
            limits = cur.fetchone()
            if not limits: return True, ""
            if resource_type == "clients":
                cur.execute("SELECT COUNT(*) as c FROM clients WHERE tenant_id=%s AND deleted_at IS NULL",(tenant_id,))
                count = cur.fetchone()["c"]
                max_val = limits.get("max_clients") or 999999
                if count >= max_val: return False, f"Client limit reached: {count}/{max_val}"
            elif resource_type == "users":
                cur.execute("SELECT COUNT(*) as c FROM users WHERE tenant_id=%s AND deleted_at IS NULL",(tenant_id,))
                count = cur.fetchone()["c"]
                max_val = limits.get("max_users") or 999999
                if count >= max_val: return False, f"User limit reached: {count}/{max_val}"
            elif resource_type == "jobs":
                cur.execute("SELECT COUNT(*) as c FROM jobs WHERE tenant_id=%s AND created_at >= date_trunc('month',now())",(tenant_id,))
                count = cur.fetchone()["c"]
                max_val = limits.get("max_jobs_per_month") or 999999
                if count >= max_val: return False, f"Monthly job limit reached: {count}/{max_val}"
    except: pass
    return True, ""

# === AUTO-CREATE TABLES ===
EXTRA_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
    task_type TEXT DEFAULT 'interni_poznamka', status TEXT DEFAULT 'novy',
    priority TEXT DEFAULT 'bezna', created_at TIMESTAMPTZ DEFAULT now(),
    deadline TEXT, planned_date TEXT, time_window_start TEXT, time_window_end TEXT,
    planned_start_at TIMESTAMPTZ, planned_end_at TIMESTAMPTZ,
    estimated_minutes INT, actual_minutes INT, created_by TEXT, assigned_to TEXT,
    assigned_user_id BIGINT, planning_note TEXT, reminder_for_assignee_only BOOLEAN DEFAULT TRUE,
    delegated_by TEXT, client_id BIGINT, client_name TEXT, job_id BIGINT,
    property_id BIGINT, property_address TEXT, is_recurring BOOLEAN DEFAULT FALSE,
    recurrence_rule TEXT, result TEXT, notes JSONB DEFAULT '[]',
    communication_method TEXT, source TEXT DEFAULT 'manualne',
    is_billable BOOLEAN DEFAULT FALSE, has_cost BOOLEAN DEFAULT FALSE,
    waiting_for_payment BOOLEAN DEFAULT FALSE, checklist JSONB DEFAULT '[]',
    is_completed BOOLEAN DEFAULT FALSE, calendar_sync_enabled BOOLEAN DEFAULT TRUE,
    tenant_id INT NOT NULL DEFAULT 1, updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS client_notes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id BIGINT NOT NULL, note TEXT NOT NULL, created_by TEXT DEFAULT 'Marek',
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS job_notes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id BIGINT NOT NULL, note TEXT NOT NULL, created_by TEXT DEFAULT 'Marek',
    note_type TEXT DEFAULT 'general', tenant_id INT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT now(), created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS task_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id TEXT NOT NULL, field_name TEXT NOT NULL, old_value TEXT, new_value TEXT,
    changed_by TEXT DEFAULT 'Marek', changed_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS activity_timeline (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL,
    description TEXT NOT NULL, user_name TEXT DEFAULT 'Marek',
    source_channel TEXT,
    details_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS photos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    filename TEXT NOT NULL, description TEXT, photo_type TEXT DEFAULT 'general',
    file_path TEXT, thumbnail_base64 TEXT, tenant_id INT NOT NULL DEFAULT 1,
    created_by TEXT DEFAULT 'Marek', created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS work_reports (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT DEFAULT 1, client_id BIGINT NOT NULL, property_id BIGINT,
    job_id BIGINT, work_date DATE NOT NULL, total_hours DECIMAL NOT NULL,
    total_price DECIMAL DEFAULT 0, currency TEXT DEFAULT 'GBP',
    notes TEXT, created_by BIGINT, input_type TEXT DEFAULT 'voice',
    status TEXT DEFAULT 'draft', created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS work_report_workers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_report_id BIGINT NOT NULL, user_id BIGINT,
    worker_name TEXT NOT NULL, hours DECIMAL NOT NULL,
    hourly_rate DECIMAL DEFAULT 0, total_price DECIMAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS work_report_entries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_report_id BIGINT NOT NULL, type TEXT NOT NULL,
    description TEXT, hours DECIMAL DEFAULT 0,
    unit_rate DECIMAL DEFAULT 0, total_price DECIMAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS work_report_materials (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_report_id BIGINT NOT NULL, material_name TEXT NOT NULL,
    quantity DECIMAL DEFAULT 0, unit TEXT DEFAULT 'ks',
    unit_price DECIMAL DEFAULT 0, total_price DECIMAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS work_report_waste (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_report_id BIGINT NOT NULL, quantity DECIMAL DEFAULT 0,
    unit TEXT DEFAULT 'bulkbag', unit_price DECIMAL DEFAULT 0,
    total_price DECIMAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS voice_sessions (
    id TEXT PRIMARY KEY, tenant_id INT DEFAULT 1,
    user_id BIGINT, session_type TEXT DEFAULT 'work_report',
    state TEXT DEFAULT 'init', dialog_step TEXT DEFAULT 'client',
    context JSONB DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(), expires_at TIMESTAMPTZ DEFAULT now() + interval '1 hour'
);
CREATE TABLE IF NOT EXISTS assistant_memory (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT NOT NULL DEFAULT 1,
    user_id BIGINT,
    memory_type TEXT NOT NULL DEFAULT 'long',
    content TEXT NOT NULL,
    normalized_content TEXT,
    source TEXT DEFAULT 'voice',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    forgotten_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS pricing_rules (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT DEFAULT 1, scope TEXT DEFAULT 'system',
    scope_id BIGINT, rule_type TEXT NOT NULL,
    rule_key TEXT, rate DECIMAL NOT NULL,
    currency TEXT DEFAULT 'GBP', created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tenant_default_rates (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT NOT NULL DEFAULT 1,
    rate_type TEXT NOT NULL,
    rate DECIMAL NOT NULL DEFAULT 0,
    currency TEXT DEFAULT 'GBP',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_tenant_default_rates UNIQUE (tenant_id, rate_type)
);
CREATE TABLE IF NOT EXISTS user_contact_sync (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT NOT NULL DEFAULT 1,
    user_id BIGINT NOT NULL,
    contact_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    phone_primary TEXT,
    email_primary TEXT,
    address TEXT,
    address_line1 TEXT,
    city TEXT,
    postcode TEXT,
    country TEXT,
    normalized_phone TEXT,
    normalized_email TEXT,
    is_client BOOLEAN NOT NULL DEFAULT FALSE,
    linked_client_id BIGINT,
    last_seen_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_user_contact_sync UNIQUE (tenant_id, user_id, contact_key)
);
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS contact_sections (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT NOT NULL DEFAULT 1,
    section_code TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order INT NOT NULL DEFAULT 100,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_contact_sections UNIQUE (tenant_id, section_code)
);
CREATE TABLE IF NOT EXISTS shared_contacts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT NOT NULL DEFAULT 1,
    section_code TEXT NOT NULL,
    display_name TEXT NOT NULL,
    company_name TEXT,
    phone_primary TEXT,
    email_primary TEXT,
    address TEXT,
    address_line1 TEXT,
    city TEXT,
    postcode TEXT,
    country TEXT,
    notes TEXT,
    source TEXT DEFAULT 'manual',
    normalized_phone TEXT,
    normalized_email TEXT,
    created_by BIGINT,
    updated_by BIGINT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS nature_recognition_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT NOT NULL DEFAULT 1,
    user_id BIGINT,
    recognition_type TEXT NOT NULL,
    language TEXT DEFAULT 'en',
    display_name TEXT,
    scientific_name TEXT,
    confidence NUMERIC(8,6),
    guidance TEXT,
    database_name TEXT,
    result_json JSONB DEFAULT '{}'::jsonb,
    photos_json JSONB DEFAULT '[]'::jsonb,
    captured_at TIMESTAMPTZ,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accuracy_meters DOUBLE PRECISION,
    location_source TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS nature_recognition_photos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    history_id BIGINT NOT NULL REFERENCES nature_recognition_history(id) ON DELETE CASCADE,
    tenant_id INT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    filename TEXT,
    photo_type TEXT DEFAULT 'capture',
    content_type TEXT,
    size_bytes INT,
    photo_data_url TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nature_recognition_photos_history_id
    ON nature_recognition_photos(history_id, sort_order);
DO $$ BEGIN
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_name TEXT;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_email TEXT;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_phone TEXT;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS description TEXT;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS notes TEXT;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS client_id BIGINT;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS job_id BIGINT;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS comm_type TEXT DEFAULT 'telefon';
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS job_id BIGINT;
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS notes TEXT;
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS source TEXT;
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS external_message_id TEXT;
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS source_phone TEXT;
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS target_phone TEXT;
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS conversation_key TEXT;
    ALTER TABLE communications ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE clients ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1;
    ALTER TABLE clients ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
    ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1;
    ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS user_id_ref TEXT;
    ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS source_channel TEXT;
    ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS details_json JSONB DEFAULT '{}'::jsonb;
    ALTER TABLE job_notes ADD COLUMN IF NOT EXISTS note_type TEXT DEFAULT 'general';
    ALTER TABLE job_notes ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1;
    ALTER TABLE job_notes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE photos ADD COLUMN IF NOT EXISTS photo_type TEXT DEFAULT 'general';
    ALTER TABLE photos ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS assigned_user_id BIGINT;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS assigned_to TEXT;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS planned_start_at TIMESTAMPTZ;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS planned_end_at TIMESTAMPTZ;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS handover_note TEXT;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS handed_over_by TEXT;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS handed_over_at TIMESTAMPTZ;
    ALTER TABLE jobs ADD COLUMN IF NOT EXISTS calendar_sync_enabled BOOLEAN DEFAULT TRUE;
    ALTER TABLE tasks ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1;
    ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assigned_user_id BIGINT;
    ALTER TABLE tasks ADD COLUMN IF NOT EXISTS planning_note TEXT;
    ALTER TABLE tasks ADD COLUMN IF NOT EXISTS planned_start_at TIMESTAMPTZ;
    ALTER TABLE tasks ADD COLUMN IF NOT EXISTS planned_end_at TIMESTAMPTZ;
    ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reminder_for_assignee_only BOOLEAN DEFAULT TRUE;
    ALTER TABLE tasks ADD COLUMN IF NOT EXISTS calendar_sync_enabled BOOLEAN DEFAULT TRUE;
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS quote_title TEXT;
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS valid_until DATE;
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS notes TEXT;
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS grand_total DECIMAL DEFAULT 0;
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft';
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS client_id BIGINT;
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1;
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE quotes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
    ALTER TABLE quote_items ADD COLUMN IF NOT EXISTS total DECIMAL DEFAULT 0;
    ALTER TABLE quote_items ADD COLUMN IF NOT EXISTS sort_order INT DEFAULT 0;
    ALTER TABLE user_contact_sync ADD COLUMN IF NOT EXISTS address TEXT;
    ALTER TABLE user_contact_sync ADD COLUMN IF NOT EXISTS address_line1 TEXT;
    ALTER TABLE user_contact_sync ADD COLUMN IF NOT EXISTS city TEXT;
    ALTER TABLE user_contact_sync ADD COLUMN IF NOT EXISTS postcode TEXT;
    ALTER TABLE user_contact_sync ADD COLUMN IF NOT EXISTS country TEXT;
    ALTER TABLE shared_contacts ADD COLUMN IF NOT EXISTS address TEXT;
    ALTER TABLE shared_contacts ADD COLUMN IF NOT EXISTS owner_user_id BIGINT DEFAULT NULL;
    ALTER TABLE shared_contacts ADD COLUMN IF NOT EXISTS address_line1 TEXT;
    ALTER TABLE shared_contacts ADD COLUMN IF NOT EXISTS city TEXT;
    ALTER TABLE shared_contacts ADD COLUMN IF NOT EXISTS postcode TEXT;
    ALTER TABLE shared_contacts ADD COLUMN IF NOT EXISTS country TEXT;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS idx_communications_tenant_client_time
    ON communications(tenant_id, client_id, sent_at DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_communications_source_external
    ON communications(tenant_id, source, external_message_id);
CREATE INDEX IF NOT EXISTS idx_assistant_memory_tenant_active
    ON assistant_memory(tenant_id, user_id, is_active, updated_at DESC);
UPDATE users
SET display_name = COALESCE(NULLIF(btrim(regexp_replace(display_name, '\\*+', ' ', 'g')), ''), email, display_name),
    updated_at = now()
WHERE display_name LIKE '%*%';
UPDATE clients
SET display_name = COALESCE(NULLIF(btrim(regexp_replace(display_name, '\\*+', ' ', 'g')), ''), display_name),
    updated_at = now()
WHERE display_name LIKE '%*%';
UPDATE shared_contacts
SET display_name = COALESCE(NULLIF(btrim(regexp_replace(display_name, '\\*+', ' ', 'g')), ''), display_name),
    updated_at = now()
WHERE display_name LIKE '%*%';
UPDATE user_contact_sync
SET display_name = COALESCE(NULLIF(btrim(regexp_replace(display_name, '\\*+', ' ', 'g')), ''), display_name),
    updated_at = now()
WHERE display_name LIKE '%*%';
UPDATE leads
SET contact_name = COALESCE(NULLIF(btrim(regexp_replace(contact_name, '\\*+', ' ', 'g')), ''), contact_name),
    updated_at = now()
WHERE contact_name LIKE '%*%';
"""

# === CONFIG ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if OPENAI_API_KEY:
    print(f"DEBUG: OPENAI_API_KEY loaded, prefix: {OPENAI_API_KEY[:4]}****")
else:
    print("DEBUG: OPENAI_API_KEY is empty in env!")

ai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# === WHATSAPP CONFIG ===
def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            trimmed = value.strip()
            if trimmed:
                return trimmed
    return default

WA_PHONE_ID = env_first("WHATSAPP_PHONE_NUMBER_ID", "WA_PHONE_ID", "META_WHATSAPP_PHONE_NUMBER_ID")
WA_ACCOUNT_ID = env_first("WHATSAPP_BUSINESS_ACCOUNT_ID", "WA_ACCOUNT_ID", "META_WHATSAPP_BUSINESS_ACCOUNT_ID")
WA_TOKEN = env_first("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_TOKEN", "META_ACCESS_TOKEN")
WA_VERIFY_TOKEN = env_first("WHATSAPP_VERIFY_TOKEN", default="designleaf_webhook_2026")
TWILIO_ACCOUNT_SID = env_first("TWILIO_ACCOUNT_SID", "TWILIO_SID")
TWILIO_AUTH_TOKEN = env_first("TWILIO_AUTH_TOKEN", "TWILIO_TOKEN")
TWILIO_WHATSAPP_FROM = env_first("TWILIO_WHATSAPP_FROM", "TWILIO_WHATSAPP_NUMBER", "TWILIO_WHATSAPP_SENDER")
PLANTNET_API_KEY = env_first(
    "PLANTNET_API_KEY",
    "PLANTNET_KEY",
    "PLANT_RECOGNITION_API_KEY",
    "PLANT_ID_API_KEY",
    "PLANTNET_PRIVATE_API_KEY",
)
PLANTNET_PROJECT = env_first("PLANTNET_PROJECT", default="all")
PLANT_HEALTH_API_KEY = env_first(
    "PLANT_HEALTH_API_KEY",
    "KINDWISE_PLANT_HEALTH_API_KEY",
    "PLANT_DISEASE_API_KEY",
    "PLANT_ID_API_KEY",
)
PLANT_HEALTH_API_URL = env_first(
    "PLANT_HEALTH_API_URL",
    default="https://api.plant.id/v3/health_assessment",
)
MUSHROOM_ID_API_KEY = env_first(
    "MUSHROOM_ID_API_KEY",
    "MUSHROOM_API_KEY",
    "MUSHROOMID_API_KEY",
    "MUSHROOM_RECOGNITION_API_KEY",
    "KINDWISE_MUSHROOM_API_KEY",
    "KINDWISE_API_KEY",
)
MUSHROOM_ID_API_URL = env_first(
    "MUSHROOM_ID_API_URL",
    "MUSHROOM_API_URL",
    "KINDWISE_MUSHROOM_API_URL",
    default="https://mushroom.kindwise.com/api/v1/identification",
)

def normalize_kindwise_endpoint(configured_url: str, suffix: str) -> str:
    base = (configured_url or "").strip().rstrip("/")
    if not base:
        return suffix
    if base.lower().endswith(suffix.lower()):
        return base
    if base.lower().endswith("/api/v1") or base.lower().endswith("/v3"):
        return f"{base}{suffix}"
    return base

PLANT_HEALTH_API_URL = normalize_kindwise_endpoint(PLANT_HEALTH_API_URL, "/health_assessment")
MUSHROOM_ID_API_URL = normalize_kindwise_endpoint(MUSHROOM_ID_API_URL, "/identification")

def get_wa_api_url() -> str:
    return f"https://graph.facebook.com/v21.0/{WA_PHONE_ID}/messages"

def get_twilio_messages_api_url() -> str:
    return f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"

def get_whatsapp_provider() -> str:
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM:
        return "twilio"
    if WA_TOKEN and WA_PHONE_ID:
        return "meta"
    return "none"

def get_nature_service_status() -> dict:
    return {
        "plant_recognition_configured": bool(PLANTNET_API_KEY),
        "plant_health_configured": bool(PLANT_HEALTH_API_KEY),
        "mushroom_recognition_configured": bool(MUSHROOM_ID_API_KEY),
        "plant_health_api_url": PLANT_HEALTH_API_URL,
        "mushroom_api_url": MUSHROOM_ID_API_URL,
    }

def normalize_language_code(language: Optional[str], default: str = "en") -> str:
    raw = (language or "").strip().lower()
    if not raw:
        return default
    aliases = {
        "en": "en",
        "en-gb": "en",
        "en-us": "en",
        "english": "en",
        "anglictina": "en",
        "angličtina": "en",
        "cs": "cs",
        "cs-cz": "cs",
        "czech": "cs",
        "cestina": "cs",
        "čeština": "cs",
        "pl": "pl",
        "pl-pl": "pl",
        "polish": "pl",
        "polski": "pl",
        "de": "de",
        "de-de": "de",
        "german": "de",
        "deutsch": "de",
        "fr": "fr",
        "fr-fr": "fr",
        "french": "fr",
        "francais": "fr",
        "français": "fr",
        "es": "es",
        "es-es": "es",
        "spanish": "es",
        "espanol": "es",
        "español": "es",
        "sk": "sk",
        "sk-sk": "sk",
        "slovak": "sk",
        "slovencina": "sk",
        "slovenčina": "sk",
        "ro": "ro",
        "ro-ro": "ro",
        "romanian": "ro",
    }
    if raw in aliases:
        return aliases[raw]
    raw_prefix = raw.split("-")[0]
    if raw_prefix in aliases:
        return aliases[raw_prefix]
    if raw_prefix in {"en", "cs", "pl", "de", "fr", "es", "sk", "ro"}:
        return raw_prefix
    return default

def tr_lang(lang: str, en: str, cs: str, pl: str) -> str:
    code = normalize_language_code(lang, default="en")
    return cs if code == "cs" else pl if code == "pl" else en

def plant_guidance_labels(language: str) -> dict:
    code = normalize_language_code(language, default="en")
    if code == "cs":
        return {
            "language_name": "Czech",
            "subject_fallback": "rostlina",
            "unknown": "neuvedeno",
            "description": "Popis",
            "needs": "Nároky",
            "best_place": "Vhodné místo",
            "note": "Poznámka",
            "instruction": "Piš česky. Buď stručný a praktický pro zahradníka.",
        }
    if code == "pl":
        return {
            "language_name": "Polish",
            "subject_fallback": "roślina",
            "unknown": "nie podano",
            "description": "Opis",
            "needs": "Wymagania",
            "best_place": "Najlepsze miejsce",
            "note": "Uwaga",
            "instruction": "Pisz po polsku. Bądź zwięzły i praktyczny dla ogrodnika.",
        }
    return {
        "language_name": "English",
        "subject_fallback": "the plant",
        "unknown": "unknown",
        "description": "Description",
        "needs": "Needs",
        "best_place": "Best place",
        "note": "Note",
        "instruction": "Write in English. Keep it concise and practical for a gardener.",
    }

def plant_health_labels(language: str) -> dict:
    code = normalize_language_code(language, default="en")
    if code == "cs":
        return {
            "finding": "Nález",
            "cause": "Příčina",
            "treatment": "Léčba",
            "prevention": "Prevence",
            "healthy": "Rostlina podle fotek působí spíš zdravě.",
            "instruction": "Piš česky. Buď stručný a praktický pro zahradníka. Zaměř se na diagnózu, léčbu a prevenci.",
        }
    if code == "pl":
        return {
            "finding": "Wniosek",
            "cause": "Przyczyna",
            "treatment": "Leczenie",
            "prevention": "Zapobieganie",
            "healthy": "Roślina na zdjęciach wygląda raczej zdrowo.",
            "instruction": "Pisz po polsku. Bądź zwięzły i praktyczny dla ogrodnika. Skup się na diagnozie, leczeniu i zapobieganiu.",
        }
    return {
        "finding": "Finding",
        "cause": "Cause",
        "treatment": "Treatment",
        "prevention": "Prevention",
        "healthy": "The plant looks rather healthy based on the photos.",
        "instruction": "Write in English. Keep it concise and practical for a gardener. Focus on diagnosis, treatment, and prevention.",
    }

def mushroom_guidance_labels(language: str) -> dict:
    code = normalize_language_code(language, default="en")
    if code == "cs":
        return {
            "subject_fallback": "houba",
            "unknown": "neuvedeno",
            "description": "Popis",
            "edibility": "Jedlost",
            "habitat": "Stanoviště",
            "warning": "Varování",
            "instruction": "Piš česky. Buď stručný a praktický. Vždy zdůrazni, že jedlost se nesmí potvrzovat jen podle fotografie.",
        }
    if code == "pl":
        return {
            "subject_fallback": "grzyb",
            "unknown": "nie podano",
            "description": "Opis",
            "edibility": "Jadalność",
            "habitat": "Siedlisko",
            "warning": "Ostrzeżenie",
            "instruction": "Pisz po polsku. Bądź zwięzły i praktyczny. Zawsze podkreślaj, że nie wolno potwierdzać jadalności wyłącznie na podstawie zdjęcia.",
        }
    return {
        "subject_fallback": "the mushroom",
        "unknown": "unknown",
        "description": "Description",
        "edibility": "Edibility",
        "habitat": "Habitat",
        "warning": "Warning",
        "instruction": "Write in English. Keep it concise and practical. Always stress that edibility must not be confirmed from a photo alone.",
    }

def flatten_mushroom_list(value) -> List[str]:
    items: List[str] = []

    def append(candidate) -> None:
        if candidate is None:
            return
        if isinstance(candidate, str):
            text = candidate.strip()
            if text:
                items.append(text)
            return
        if isinstance(candidate, (int, float)):
            items.append(str(candidate))
            return
        if isinstance(candidate, dict):
            primary = (
                candidate.get("name")
                or candidate.get("label")
                or candidate.get("title")
                or candidate.get("value")
                or candidate.get("common_name")
                or candidate.get("scientific_name")
                or candidate.get("text")
            )
            if isinstance(primary, (str, int, float)):
                append(primary)
                return
            for nested in candidate.values():
                append(nested)
            return
        if isinstance(candidate, list):
            for nested in candidate:
                append(nested)

    append(value)
    return list(dict.fromkeys(items))

def flatten_mushroom_text(value) -> Optional[str]:
    items = flatten_mushroom_list(value)
    if not items:
        return None
    return ", ".join(items)

def flatten_mushroom_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "psychoactive"}:
            return True
        if normalized in {"false", "no", "n", "0", "non-psychoactive", "not psychoactive"}:
            return False
        return None
    if isinstance(value, dict):
        for key in ("binary", "value", "is_psychoactive", "psychoactive"):
            parsed = flatten_mushroom_bool(value.get(key))
            if parsed is not None:
                return parsed
        for nested in value.values():
            parsed = flatten_mushroom_bool(nested)
            if parsed is not None:
                return parsed
    if isinstance(value, list):
        for nested in value:
            parsed = flatten_mushroom_bool(nested)
            if parsed is not None:
                return parsed
    return None

def extract_mushroom_suggestions(raw) -> List[dict]:
    candidates = []
    if isinstance(raw, dict):
        result = raw.get("result")
        if isinstance(result, dict):
            classification = result.get("classification")
            if isinstance(classification, dict):
                suggestions = classification.get("suggestions")
                if isinstance(suggestions, list):
                    candidates.extend([item for item in suggestions if isinstance(item, dict)])
            suggestions = result.get("suggestions")
            if isinstance(suggestions, list):
                candidates.extend([item for item in suggestions if isinstance(item, dict)])
        classification = raw.get("classification")
        if isinstance(classification, dict):
            suggestions = classification.get("suggestions")
            if isinstance(suggestions, list):
                candidates.extend([item for item in suggestions if isinstance(item, dict)])
        suggestions = raw.get("suggestions")
        if isinstance(suggestions, list):
            candidates.extend([item for item in suggestions if isinstance(item, dict)])
    if isinstance(raw, list):
        candidates.extend([item for item in raw if isinstance(item, dict)])
    return candidates

def flatten_plant_list(value) -> List[str]:
    items: List[str] = []

    def append(candidate) -> None:
        if candidate is None:
            return
        if isinstance(candidate, str):
            text = candidate.strip()
            if text:
                items.append(text)
            return
        if isinstance(candidate, (int, float)):
            items.append(str(candidate))
            return
        if isinstance(candidate, dict):
            primary = (
                candidate.get("scientificNameWithoutAuthor")
                or candidate.get("scientificName")
                or candidate.get("commonName")
                or candidate.get("common_name")
                or candidate.get("name")
                or candidate.get("label")
                or candidate.get("title")
                or candidate.get("value")
                or candidate.get("text")
            )
            if isinstance(primary, (str, int, float)):
                append(primary)
                return
            for nested in candidate.values():
                append(nested)
            return
        if isinstance(candidate, list):
            for nested in candidate:
                append(nested)

    append(value)
    return list(dict.fromkeys(items))

def flatten_plant_text(value) -> str:
    items = flatten_plant_list(value)
    return items[0] if items else ""

def normalize_plant_score(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return 0.0
    if isinstance(value, dict):
        for key in ("score", "probability", "value"):
            parsed = normalize_plant_score(value.get(key))
            if parsed > 0:
                return parsed
        for nested in value.values():
            parsed = normalize_plant_score(nested)
            if parsed > 0:
                return parsed
    if isinstance(value, list):
        for nested in value:
            parsed = normalize_plant_score(nested)
            if parsed > 0:
                return parsed
    return 0.0

def extract_plant_taxon_name(value) -> str:
    if isinstance(value, dict):
        return flatten_plant_text(
            value.get("scientificNameWithoutAuthor")
            or value.get("scientificName")
            or value.get("name")
            or value.get("label")
            or value.get("value")
            or value
        )
    return flatten_plant_text(value)

def flatten_treatment_items(treatment: dict) -> dict:
    treatment = treatment or {}
    return {
        "chemical": [str(item).strip() for item in (treatment.get("chemical") or []) if str(item).strip()],
        "biological": [str(item).strip() for item in (treatment.get("biological") or []) if str(item).strip()],
        "prevention": [str(item).strip() for item in (treatment.get("prevention") or []) if str(item).strip()],
    }

async def plantnet_identify(files: List[UploadFile], organs: List[str], language: str) -> dict:
    if not PLANTNET_API_KEY:
        raise HTTPException(503, tr_lang(
            language,
            "Plant recognition service is not configured.",
            "Služba pro rozpoznávání rostlin není nastavená.",
            "Usługa rozpoznawania roślin nie jest skonfigurowana."
        ))
    payload_files = []
    for index, upload in enumerate(files):
        content = await upload.read()
        if not content:
            raise HTTPException(400, tr_lang(
                language,
                f"Image {index + 1} is empty.",
                f"Obrázek {index + 1} je prázdný.",
                f"Obraz {index + 1} jest pusty."
            ))
        payload_files.append((
            "images",
            (
                upload.filename or f"plant_{index + 1}.jpg",
                content,
                upload.content_type or "image/jpeg",
            ),
        ))
        await upload.seek(0)
    lang_code = normalize_language_code(language, default="en")
    params = {"api-key": PLANTNET_API_KEY, "lang": lang_code, "include-related-images": "false", "nb-results": "5"}
    multipart_parts = list(payload_files)
    for organ in organs:
        multipart_parts.append(("organs", (None, organ or "auto")))
    url = f"https://my-api.plantnet.org/v2/identify/{PLANTNET_PROJECT}"
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params=params, files=multipart_parts)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        if exc.response.status_code in (401, 403):
            raise HTTPException(502, tr_lang(
                language,
                "Plant identification service rejected the API key.",
                "Služba pro rozpoznání rostlin odmítla API klíč.",
                "Usługa rozpoznawania roślin odrzuciła klucz API."
            ))
        raise HTTPException(502, tr_lang(
            language,
            f"Plant identification failed: {detail}",
            f"Rozpoznání rostliny selhalo: {detail}",
            f"Rozpoznanie rośliny nie powiodło się: {detail}"
        ))
    except httpx.HTTPError as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Plant identification network error: {exc}",
            f"Síťová chyba při rozpoznání rostliny: {exc}",
            f"Błąd sieci podczas rozpoznawania rośliny: {exc}"
        ))
    except Exception as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Plant identification request error: {exc}",
            f"Chyba požadavku při rozpoznání rostliny: {exc}",
            f"Błąd żądania podczas rozpoznawania rośliny: {exc}"
        ))

async def plant_health_assessment(files: List[UploadFile], language: str) -> dict:
    if not PLANT_HEALTH_API_KEY:
        raise HTTPException(503, tr_lang(
            language,
            "Plant disease service is not configured.",
            "Služba pro choroby rostlin není nastavená.",
            "Usługa chorób roślin nie jest skonfigurowana."
        ))
    encoded_images = []
    for index, upload in enumerate(files):
        content = await upload.read()
        if not content:
            raise HTTPException(400, tr_lang(
                language,
                f"Image {index + 1} is empty.",
                f"Obrázek {index + 1} je prázdný.",
                f"Obraz {index + 1} jest pusty."
            ))
        encoded_images.append(base64.b64encode(content).decode("ascii"))
        await upload.seek(0)
    lang_code = normalize_language_code(language, default="en")
    params = {
        "details": "description,treatment,common_names",
        "language": lang_code,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                PLANT_HEALTH_API_URL,
                params=params,
                headers={"Api-Key": PLANT_HEALTH_API_KEY},
                json={"images": encoded_images},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        if exc.response.status_code in (401, 403):
            raise HTTPException(502, tr_lang(
                language,
                "Plant disease service rejected the API key.",
                "Služba pro choroby rostlin odmítla API klíč.",
                "Usługa chorób roślin odrzuciła klucz API."
            ))
        raise HTTPException(502, tr_lang(
            language,
            f"Plant disease assessment failed: {detail}",
            f"Posouzení choroby rostliny selhalo: {detail}",
            f"Ocena choroby rośliny nie powiodła się: {detail}"
        ))
    except httpx.HTTPError as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Plant disease network error: {exc}",
            f"Síťová chyba při zjišťování choroby rostliny: {exc}",
            f"Błąd sieci podczas sprawdzania choroby rośliny: {exc}"
        ))
    except Exception as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Plant disease request error: {exc}",
            f"Chyba požadavku při zjišťování choroby rostliny: {exc}",
            f"Błąd żądania podczas sprawdzania choroby rośliny: {exc}"
        ))

async def mushroom_identify(files: List[UploadFile], language: str) -> dict:
    if not MUSHROOM_ID_API_KEY:
        raise HTTPException(503, tr_lang(
            language,
            "Mushroom recognition service is not configured.",
            "Služba pro rozpoznávání hub není nastavená.",
            "Usługa rozpoznawania grzybów nie jest skonfigurowana."
        ))
    encoded_images = []
    for index, upload in enumerate(files):
        content = await upload.read()
        if not content:
            raise HTTPException(400, tr_lang(
                language,
                f"Image {index + 1} is empty.",
                f"Obrázek {index + 1} je prázdný.",
                f"Obraz {index + 1} jest pusty."
            ))
        encoded_images.append(base64.b64encode(content).decode("ascii"))
        await upload.seek(0)
    params = {
        "details": "common_names,url,description,edibility,psychoactive,look_alikes,taxonomy,characteristics",
        "language": normalize_language_code(language, default="en"),
    }
    payload = {
        "images": encoded_images,
        "similar_images": True,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                MUSHROOM_ID_API_URL,
                params=params,
                headers={"Api-Key": MUSHROOM_ID_API_KEY},
                json=payload,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        if exc.response.status_code in (401, 403):
            raise HTTPException(502, tr_lang(
                language,
                "Mushroom recognition service rejected the API key.",
                "Služba pro rozpoznávání hub odmítla API klíč.",
                "Usługa rozpoznawania grzybów odrzuciła klucz API."
            ))
        raise HTTPException(502, tr_lang(
            language,
            f"Mushroom recognition failed: {detail}",
            f"Rozpoznání houby selhalo: {detail}",
            f"Rozpoznanie grzyba nie powiodło się: {detail}"
        ))
    except httpx.HTTPError as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Mushroom recognition network error: {exc}",
            f"Síťová chyba při rozpoznání houby: {exc}",
            f"Błąd sieci podczas rozpoznawania grzyba: {exc}"
        ))
    except Exception as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Mushroom recognition request error: {exc}",
            f"Chyba požadavku při rozpoznání houby: {exc}",
            f"Błąd żądania podczas rozpoznawania grzyba: {exc}"
        ))

def build_plant_guidance(language: str, display_name: str, scientific_name: str, family: str = "", genus: str = "") -> tuple[str, str]:
    labels = plant_guidance_labels(language)
    common_label = display_name or scientific_name or labels["subject_fallback"]
    if not ai_client:
        summary = tr_lang(
            language,
            f"Most likely match: {common_label}. Scientific name: {scientific_name}. Family: {family or labels['unknown']}.",
            f"Nejpravděpodobnější shoda: {common_label}. Vědecký název: {scientific_name}. Čeleď: {family or labels['unknown']}.",
            f"Najbardziej prawdopodobne dopasowanie: {common_label}. Nazwa naukowa: {scientific_name}. Rodzina: {family or labels['unknown']}."
        )
        return summary, summary
    prompt = (
        f"Plant identified as {common_label} ({scientific_name}). Family: {family or labels['unknown']}. Genus: {genus or labels['unknown']}.\n"
        f"{labels['instruction']}\n"
        f"Return exactly 4 short lines:\n"
        f"1. {labels['description']}: ...\n"
        f"2. {labels['needs']}: ...\n"
        f"3. {labels['best_place']}: ...\n"
        f"4. {labels['note']}: mention uncertainty if species can vary."
    )
    try:
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You write short practical plant summaries."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=220,
        )
        guidance = (response.choices[0].message.content or "").strip()
        spoken = tr_lang(
            language,
            f"It is most likely {common_label}. {guidance.splitlines()[0] if guidance else ''}",
            f"Nejspíš je to {common_label}. {guidance.splitlines()[0] if guidance else ''}",
            f"Najprawdopodobniej to {common_label}. {guidance.splitlines()[0] if guidance else ''}",
        ).strip()
        return guidance or common_label, spoken
    except Exception:
        fallback = tr_lang(
            language,
            f"Most likely match: {common_label}. Scientific name: {scientific_name}. Family: {family or labels['unknown']}.",
            f"Nejpravděpodobnější shoda: {common_label}. Vědecký název: {scientific_name}. Čeleď: {family or labels['unknown']}.",
            f"Najbardziej prawdopodobne dopasowanie: {common_label}. Nazwa naukowa: {scientific_name}. Rodzina: {family or labels['unknown']}."
        )
        return fallback, fallback

def build_plant_health_guidance(language: str, issue_name: str, description: str, treatment: dict, is_healthy: bool) -> tuple[str, str]:
    labels = plant_health_labels(language)
    treatment = flatten_treatment_items(treatment)
    if not issue_name and is_healthy:
        summary = tr_lang(
            language,
            "The plant looks healthy in the supplied photos. Keep monitoring new symptoms and maintain regular care.",
            "Rostlina na dodaných fotkách působí zdravě. Sleduj nové příznaky a pokračuj v běžné péči.",
            "Roślina na przesłanych zdjęciach wygląda zdrowo. Obserwuj nowe objawy i kontynuuj zwykłą pielęgnację."
        )
        return summary, summary
    treatment_payload = json.dumps(treatment, ensure_ascii=False)
    if not ai_client:
        fallback = tr_lang(
            language,
            f"Most likely issue: {issue_name}. Description: {description or 'No description available.'}",
            f"Nejpravděpodobnější problém: {issue_name}. Popis: {description or 'Popis není k dispozici.'}",
            f"Najbardziej prawdopodobny problem: {issue_name}. Opis: {description or 'Brak opisu.'}"
        )
        return fallback, fallback
    prompt = (
        f"Plant health assessment result.\n"
        f"Top issue: {issue_name or labels['healthy']}\n"
        f"Plant looks healthy: {'yes' if is_healthy else 'no'}\n"
        f"Description: {description or 'n/a'}\n"
        f"Treatment data JSON: {treatment_payload}\n"
        f"{labels['instruction']}\n"
        f"Return exactly 4 short lines:\n"
        f"1. {labels['finding']}: ...\n"
        f"2. {labels['cause']}: ...\n"
        f"3. {labels['treatment']}: ...\n"
        f"4. {labels['prevention']}: ...\n"
        f"If the plant looks healthy, mention that clearly and give practical monitoring advice."
    )
    try:
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You write short practical plant disease summaries and treatment guidance."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=260,
        )
        guidance = (response.choices[0].message.content or "").strip()
        spoken = tr_lang(
            language,
            f"Most likely issue: {issue_name}. {guidance.splitlines()[0] if guidance else ''}",
            f"Nejpravděpodobnější problém: {issue_name}. {guidance.splitlines()[0] if guidance else ''}",
            f"Najbardziej prawdopodobny problem: {issue_name}. {guidance.splitlines()[0] if guidance else ''}",
        ).strip()
        return guidance or issue_name or labels["healthy"], spoken
    except Exception:
        fallback = tr_lang(
            language,
            f"Most likely issue: {issue_name}. Description: {description or 'No description available.'}",
            f"Nejpravděpodobnější problém: {issue_name}. Popis: {description or 'Popis není k dispozici.'}",
            f"Najbardziej prawdopodobny problem: {issue_name}. Opis: {description or 'Brak opisu.'}"
        )
        return fallback, fallback

def build_mushroom_guidance(
    language: str,
    display_name: str,
    scientific_name: str,
    description: str,
    edibility: str,
    family: str,
    genus: str,
    look_alikes: List[str],
    psychoactive: Optional[bool],
) -> tuple[str, str]:
    labels = mushroom_guidance_labels(language)
    common_label = display_name or scientific_name or labels["subject_fallback"]
    lookalikes_text = ", ".join(look_alikes[:3]) if look_alikes else labels["unknown"]
    psychoactive_text = (
        tr_lang(language, "yes", "ano", "tak")
        if psychoactive is True else
        tr_lang(language, "no", "ne", "nie")
        if psychoactive is False else labels["unknown"]
    )
    if not ai_client:
        fallback = tr_lang(
            language,
            f"Most likely match: {common_label}. Scientific name: {scientific_name}. Edibility: {edibility or labels['unknown']}. Never confirm edibility from a photo alone.",
            f"Nejpravděpodobnější shoda: {common_label}. Vědecký název: {scientific_name}. Jedlost: {edibility or labels['unknown']}. Jedlost nikdy nepotvrzuj jen podle fotografie.",
            f"Najbardziej prawdopodobne dopasowanie: {common_label}. Nazwa naukowa: {scientific_name}. Jadalność: {edibility or labels['unknown']}. Nigdy nie potwierdzaj jadalności wyłącznie na podstawie zdjęcia."
        )
        return fallback, fallback
    prompt = (
        f"Mushroom identified as {common_label} ({scientific_name}).\n"
        f"Description: {description or labels['unknown']}\n"
        f"Edibility: {edibility or labels['unknown']}\n"
        f"Family: {family or labels['unknown']}\n"
        f"Genus: {genus or labels['unknown']}\n"
        f"Look-alikes: {lookalikes_text}\n"
        f"Psychoactive: {psychoactive_text}\n"
        f"{labels['instruction']}\n"
        f"Return exactly 4 short lines:\n"
        f"1. {labels['description']}: ...\n"
        f"2. {labels['edibility']}: ...\n"
        f"3. {labels['habitat']}: infer likely habitat if possible from taxonomy, otherwise say unknown.\n"
        f"4. {labels['warning']}: clearly state that photo recognition is not enough to confirm edibility and mention look-alikes if relevant."
    )
    try:
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You write short practical mushroom identification summaries with safety warnings."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=260,
        )
        guidance = (response.choices[0].message.content or "").strip()
        spoken = tr_lang(
            language,
            f"It is most likely {common_label}. Never confirm edibility from a photo alone.",
            f"Nejspíš je to {common_label}. Jedlost nikdy nepotvrzuj jen podle fotografie.",
            f"Najprawdopodobniej to {common_label}. Nigdy nie potwierdzaj jadalności wyłącznie na podstawie zdjęcia.",
        ).strip()
        return guidance or common_label, spoken
    except Exception:
        fallback = tr_lang(
            language,
            f"Most likely match: {common_label}. Scientific name: {scientific_name}. Edibility: {edibility or labels['unknown']}. Never confirm edibility from a photo alone.",
            f"Nejpravděpodobnější shoda: {common_label}. Vědecký název: {scientific_name}. Jedlost: {edibility or labels['unknown']}. Jedlost nikdy nepotvrzuj jen podle fotografie.",
            f"Najbardziej prawdopodobne dopasowanie: {common_label}. Nazwa naukowa: {scientific_name}. Jadalność: {edibility or labels['unknown']}. Nigdy nie potwierdzaj jadalności wyłącznie na podstawie zdjęcia."
        )
        return fallback, fallback

def map_plantnet_result(raw: dict, language: str, requested_organs: List[str]) -> dict:
    results = raw.get("results") or []
    if not results:
        raise HTTPException(404, tr_lang(
            language,
            "No matching plant was found. Try clearer photos of the whole plant, leaf, flower or fruit.",
            "Nebyla nalezena shoda. Zkus jasnější fotky celé rostliny, listu, květu nebo plodu.",
            "Nie znaleziono pasującej rośliny. Spróbuj wyraźniejszych zdjęć całej rośliny, liścia, kwiatu albo owocu."
        ))
    def suggestion(item: dict) -> dict:
        species = item.get("species") or {}
        common_names = flatten_plant_list(species.get("commonNames"))
        scientific_name = flatten_plant_text(
            species.get("scientificNameWithoutAuthor") or species.get("scientificName")
        )
        family = extract_plant_taxon_name(species.get("family"))
        genus = extract_plant_taxon_name(species.get("genus"))
        display_name = common_names[0] if common_names else scientific_name
        return {
            "display_name": display_name,
            "scientific_name": scientific_name,
            "common_names": common_names,
            "family": family,
            "genus": genus,
            "score": normalize_plant_score(item.get("score")),
        }
    top = suggestion(results[0])
    guidance, spoken = build_plant_guidance(language, top["display_name"], top["scientific_name"], top["family"] or "", top["genus"] or "")
    return {
        "database": "Pl@ntNet",
        "display_name": top["display_name"],
        "scientific_name": top["scientific_name"],
        "common_names": top["common_names"],
        "family": top["family"],
        "genus": top["genus"],
        "score": top["score"],
        "organs": requested_organs,
        "guidance": guidance,
        "spoken_summary": spoken,
        "suggestions": [suggestion(item) for item in results[:5]],
    }

def map_plant_health_result(raw: dict, language: str) -> dict:
    result = raw.get("result") or {}
    is_healthy = result.get("is_healthy") or {}
    suggestions_raw = ((result.get("disease") or {}).get("suggestions")) or []

    def health_suggestion(item: dict) -> dict:
        details = item.get("details") or {}
        treatment = flatten_treatment_items(details.get("treatment") or {})
        common_names = details.get("common_names") or []
        return {
            "name": item.get("name") or details.get("local_name") or "",
            "probability": float(item.get("probability") or 0.0),
            "common_names": common_names,
            "description": details.get("description"),
            "treatment": treatment,
            "classification": details.get("classification") or [],
        }

    suggestions = [health_suggestion(item) for item in suggestions_raw[:5]]
    preferred = next((item for item in suggestions_raw if not item.get("redundant")), suggestions_raw[0] if suggestions_raw else None)
    top = health_suggestion(preferred) if preferred else None
    healthy_binary = bool(is_healthy.get("binary"))
    healthy_probability = float(is_healthy.get("probability") or 0.0)
    guidance, spoken = build_plant_health_guidance(
        language,
        top["name"] if top else "",
        top.get("description") or "" if top else "",
        top.get("treatment") or {} if top else {},
        healthy_binary,
    )
    return {
        "database": "Plant.id Health Assessment",
        "is_healthy": healthy_binary,
        "health_probability": healthy_probability,
        "top_issue_name": top["name"] if top else None,
        "top_issue_common_names": top["common_names"] if top else [],
        "top_issue_probability": top["probability"] if top else 0.0,
        "top_issue_description": top["description"] if top else None,
        "guidance": guidance,
        "spoken_summary": spoken,
        "suggestions": suggestions,
    }

def map_mushroom_result(raw: dict, language: str) -> dict:
    suggestions_raw = extract_mushroom_suggestions(raw)
    if not suggestions_raw:
        raise HTTPException(404, tr_lang(
            language,
            "No matching mushroom was found. Try clearer photos of the whole mushroom, underside, and stem or base.",
            "Nebyla nalezena shoda. Zkus jasnější fotky celé houby, spodní strany a třeně nebo báze.",
            "Nie znaleziono dopasowania. Spróbuj wyraźniejszych zdjęć całego grzyba, spodu oraz trzonu lub podstawy."
        ))

    def mushroom_suggestion(item: dict) -> dict:
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        taxonomy = details.get("taxonomy") if isinstance(details.get("taxonomy"), dict) else {}
        common_names = flatten_mushroom_list(details.get("common_names"))
        look_alikes = flatten_mushroom_list(details.get("look_alikes"))
        characteristics = flatten_mushroom_list(details.get("characteristics"))
        name = (
            item.get("name")
            or flatten_mushroom_text(details.get("scientific_name"))
            or flatten_mushroom_text(details.get("name"))
            or ""
        )
        display_name = common_names[0] if common_names else name
        return {
            "name": name,
            "display_name": display_name,
            "common_names": common_names,
            "probability": normalize_plant_score(item.get("probability") or item.get("score")),
            "description": flatten_mushroom_text(details.get("description")),
            "url": flatten_mushroom_text(details.get("url")),
            "edibility": flatten_mushroom_text(details.get("edibility") or details.get("edible")),
            "psychoactive": flatten_mushroom_bool(details.get("psychoactive") or details.get("is_psychoactive")),
            "family": flatten_mushroom_text(taxonomy.get("family")),
            "genus": flatten_mushroom_text(taxonomy.get("genus")),
            "look_alikes": look_alikes,
            "characteristics": characteristics,
        }

    suggestions = [mushroom_suggestion(item) for item in suggestions_raw[:5] if isinstance(item, dict)]
    suggestions = [item for item in suggestions if item.get("name") or item.get("display_name")]
    if not suggestions:
        raise HTTPException(404, tr_lang(
            language,
            "No usable mushroom match was returned. Try clearer photos of the whole mushroom, underside, and stem or base.",
            "Nebyla vrácena použitelná shoda houby. Zkus jasnější fotky celé houby, spodní strany a třeně nebo báze.",
            "Nie zwrócono użytecznego dopasowania grzyba. Spróbuj wyraźniejszych zdjęć całego grzyba, spodu oraz trzonu lub podstawy."
        ))
    top = suggestions[0]
    guidance, spoken = build_mushroom_guidance(
        language,
        top["display_name"],
        top["name"],
        top.get("description") or "",
        top.get("edibility") or "",
        top.get("family") or "",
        top.get("genus") or "",
        top.get("look_alikes") or [],
        top.get("psychoactive"),
    )
    return {
        "database": "mushroom.id",
        "display_name": top["display_name"],
        "scientific_name": top["name"],
        "common_names": top["common_names"],
        "probability": top["probability"],
        "description": top["description"],
        "url": top["url"],
        "edibility": top["edibility"],
        "psychoactive": top["psychoactive"],
        "family": top["family"],
        "genus": top["genus"],
        "look_alikes": top["look_alikes"],
        "characteristics": top["characteristics"],
        "guidance": guidance,
        "spoken_summary": spoken,
        "suggestions": [
            {
                "name": item["name"],
                "common_names": item["common_names"],
                "probability": item["probability"],
                "description": item["description"],
                "url": item["url"],
                "edibility": item["edibility"],
                "psychoactive": item["psychoactive"],
                "family": item["family"],
                "genus": item["genus"],
                "look_alikes": item["look_alikes"],
                "characteristics": item["characteristics"],
            }
            for item in suggestions
        ],
    }

def parse_database_config():
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        p = urlparse(database_url)
        return {"dbname": p.path.lstrip("/"), "user": p.username, "password": p.password, "host": p.hostname, "port": str(p.port or 5432), "options": "-c search_path=crm,public"}
    return {"dbname": "secretary_db", "user": "postgres", "password": "", "host": "localhost", "port": "5432", "options": "-c search_path=crm,public"}

DB_CONFIG = parse_database_config()
db_pool = None

def init_pool():
    global db_pool
    try:
        db_pool = pool.ThreadedConnectionPool(2, 20, **DB_CONFIG)
        print(f"DB pool OK: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SET search_path TO crm, public")
            cur.execute(EXTRA_TABLES_SQL)
            conn.commit()
        db_pool.putconn(conn)
        print("Extra tables ready")
        # Seed missing roles
        try:
            conn2 = db_pool.getconn()
            with conn2.cursor() as cur:
                cur.execute("SET search_path TO crm, public")
                for rname, rdesc in [("admin","Full access"),("manager","Manage clients, jobs, planning"),("worker","Own tasks, attendance, photos"),("assistant","Wide access, append-only"),("viewer","Read-only access")]:
                    cur.execute("INSERT INTO roles (role_name, description) VALUES (%s,%s) ON CONFLICT (role_name) DO NOTHING",(rname,rdesc))
                conn2.commit()
            db_pool.putconn(conn2)
            print("Roles seeded")
        except Exception as e: print(f"Role seed: {e}")
        try:
            conn_perm = db_pool.getconn()
            with conn_perm.cursor() as cur:
                cur.execute("SET search_path TO crm, public")
            seed_permissions(conn_perm)
            db_pool.putconn(conn_perm)
            print("Permissions seeded")
        except Exception as e: print(f"Permission seed: {e}")
        # Seed default service rates
        try:
            conn3 = db_pool.getconn()
            with conn3.cursor() as cur:
                cur.execute("SET search_path TO crm, public")
                for rt, rate, desc in [
                    ("garden_maintenance", 27, "Garden maintenance: cleaning, weeding, planting, grass strimming"),
                    ("hedge_trimming", 31, "Hedge trimming & pruning"),
                    ("arborist_works", 34, "Arboristic works, tree surgeon"),
                    ("hourly_rate", 27, "Default hourly rate"),
                    ("hourly_cost", 15, "Internal hourly cost"),
                    ("garden_waste_bulkbag", 55, "Garden waste bulk bag"),
                    ("minimum_charge", 150, "Minimum charge per job"),
                ]:
                    cur.execute("""INSERT INTO tenant_default_rates (tenant_id, rate_type, rate, description, updated_at)
                        VALUES (1, %s, %s, %s, now())
                        ON CONFLICT (tenant_id, rate_type) DO UPDATE SET
                            rate = EXCLUDED.rate,
                            description = EXCLUDED.description,
                            updated_at = now()""", (rt, rate, desc))
                conn3.commit()
            db_pool.putconn(conn3)
            print("Service rates seeded")
        except Exception as e: print(f"Rate seed: {e}")
        try:
            conn_sections = db_pool.getconn()
            with conn_sections.cursor() as cur:
                cur.execute("SET search_path TO crm, public")
                for section_code, display_name, sort_order in DEFAULT_CONTACT_SECTIONS:
                    cur.execute("""INSERT INTO contact_sections (tenant_id, section_code, display_name, is_default, sort_order)
                        VALUES (1, %s, %s, TRUE, %s)
                        ON CONFLICT (tenant_id, section_code) DO UPDATE SET
                            display_name=EXCLUDED.display_name,
                            sort_order=EXCLUDED.sort_order,
                            is_active=TRUE,
                            updated_at=now()""",
                        (section_code, display_name, sort_order))
                conn_sections.commit()
            db_pool.putconn(conn_sections)
            print("Contact sections seeded")
        except Exception as e: print(f"Contact section seed: {e}")
    except Exception as e: print(f"DB pool FAIL: {e}")

def get_db_conn():
    if not db_pool: raise HTTPException(500, "DB pool not initialized")
    try:
        conn = db_pool.getconn()
    except pool.PoolError:
        # Pool exhausted — force close idle connections and retry
        print("WARN: Pool exhausted, resetting idle connections")
        try:
            for key in list(db_pool._used.keys()):
                try: db_pool.putconn(db_pool._used[key])
                except: pass
            conn = db_pool.getconn()
        except:
            raise HTTPException(503, "Database pool exhausted. Try again.")
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur: cur.execute("SET search_path TO crm, public")
    return conn

def release_conn(conn):
    if db_pool:
        try: db_pool.putconn(conn)
        except: pass
    else:
        try: conn.close()
        except: pass

@contextmanager
def db_conn():
    """Context manager that auto-releases connection back to pool."""
    c = get_db_conn()
    try:
        yield c
    finally:
        release_conn(c)

def complete_permission_map(values: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    src = values or {}
    return {code: bool(src.get(code, False)) for code in ALL_PERMISSION_CODES}

def default_permissions_for_role(role_name: Optional[str]) -> Dict[str, bool]:
    key = (role_name or "viewer").lower()
    return complete_permission_map(ROLE_PERMISSION_DEFAULTS.get(key, ROLE_PERMISSION_DEFAULTS["viewer"]))

def normalize_permission_payload(raw: Any) -> Dict[str, bool]:
    if not isinstance(raw, dict):
        return {}
    normalized = {}
    for code, value in raw.items():
        if code in ALL_PERMISSION_CODES:
            normalized[code] = bool(value)
    return normalized

def ensure_permission_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS permissions (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                permission_code TEXT NOT NULL UNIQUE,
                module_name TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
                allowed BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now(),
                CONSTRAINT uq_role_permissions UNIQUE (role_id, permission_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_permission_overrides (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
                allowed BOOLEAN NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now(),
                CONSTRAINT uq_user_permission_overrides UNIQUE (user_id, permission_id)
            )
        """)
    conn.commit()

def seed_permissions(conn):
    ensure_permission_tables(conn)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for permission in PERMISSION_DEFINITIONS:
            cur.execute("""
                INSERT INTO permissions (permission_code, module_name, name, description, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (permission_code) DO UPDATE SET
                    module_name = EXCLUDED.module_name,
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    updated_at = now()
            """, (
                permission["permission_code"],
                permission["module_name"],
                permission["name"],
                permission["description"],
            ))
        cur.execute("SELECT id, permission_code FROM permissions")
        permission_ids = {row["permission_code"]: row["id"] for row in cur.fetchall()}
        for role_name, defaults in ROLE_PERMISSION_DEFAULTS.items():
            cur.execute("SELECT id FROM roles WHERE role_name=%s", (role_name,))
            role_row = cur.fetchone()
            if not role_row:
                continue
            for permission_code in ALL_PERMISSION_CODES:
                permission_id = permission_ids.get(permission_code)
                if permission_id is None:
                    continue
                cur.execute("""
                    INSERT INTO role_permissions (role_id, permission_id, allowed, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (role_id, permission_id) DO UPDATE SET
                        allowed = EXCLUDED.allowed,
                        updated_at = now()
                """, (role_row["id"], permission_id, bool(defaults.get(permission_code, False))))
    conn.commit()

def load_permission_catalog(conn) -> List[Dict[str, Any]]:
    ensure_permission_tables(conn)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT permission_code, module_name, name, description
            FROM permissions
            ORDER BY module_name, id
        """)
        return [dict(row) for row in cur.fetchall()]

def load_role_permission_maps(conn) -> Dict[str, Dict[str, bool]]:
    ensure_permission_tables(conn)
    role_maps: Dict[str, Dict[str, bool]] = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT r.role_name, p.permission_code, rp.allowed
            FROM role_permissions rp
            JOIN roles r ON r.id = rp.role_id
            JOIN permissions p ON p.id = rp.permission_id
        """)
        for row in cur.fetchall():
            role_name = (row["role_name"] or "viewer").lower()
            role_maps.setdefault(role_name, {})[row["permission_code"]] = bool(row["allowed"])
    for role_name in ROLE_PERMISSION_DEFAULTS.keys():
        merged = default_permissions_for_role(role_name)
        merged.update(role_maps.get(role_name, {}))
        role_maps[role_name] = merged
    return role_maps

def load_user_permission_overrides(conn, tenant_id: int, user_ids: Optional[List[int]] = None) -> Dict[int, Dict[str, bool]]:
    ensure_permission_tables(conn)
    params: List[Any] = [tenant_id]
    user_filter = ""
    if user_ids:
        user_filter = " AND u.id = ANY(%s)"
        params.append(user_ids)
    overrides: Dict[int, Dict[str, bool]] = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT u.id AS user_id, p.permission_code, upo.allowed
            FROM user_permission_overrides upo
            JOIN users u ON u.id = upo.user_id
            JOIN permissions p ON p.id = upo.permission_id
            WHERE u.tenant_id = %s AND u.deleted_at IS NULL{user_filter}
        """, params)
        for row in cur.fetchall():
            overrides.setdefault(int(row["user_id"]), {})[row["permission_code"]] = bool(row["allowed"])
    return overrides

def get_effective_permissions(conn, tenant_id: int, user_id: int, role_name: Optional[str] = None) -> Dict[str, bool]:
    resolved_role = role_name
    if not resolved_role:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT r.role_name
                FROM users u
                LEFT JOIN roles r ON r.id = u.role_id
                WHERE u.id=%s AND u.tenant_id=%s AND u.deleted_at IS NULL
            """, (user_id, tenant_id))
            row = cur.fetchone()
            resolved_role = row["role_name"] if row else "viewer"
    role_permissions = load_role_permission_maps(conn).get((resolved_role or "viewer").lower(), default_permissions_for_role(resolved_role))
    overrides = load_user_permission_overrides(conn, tenant_id, [user_id]).get(user_id, {})
    effective = dict(role_permissions)
    effective.update(overrides)
    return complete_permission_map(effective)

def save_user_permission_overrides(conn, tenant_id: int, user_id: int, role_name: Optional[str], permissions: Dict[str, bool]):
    normalized = normalize_permission_payload(permissions)
    if not normalized:
        return
    role_defaults = load_role_permission_maps(conn).get((role_name or "viewer").lower(), default_permissions_for_role(role_name))
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT p.id, p.permission_code
            FROM permissions p
        """)
        permission_rows = {row["permission_code"]: row["id"] for row in cur.fetchall()}
        for code in ALL_PERMISSION_CODES:
            if code not in normalized:
                continue
            permission_id = permission_rows.get(code)
            if permission_id is None:
                continue
            requested = bool(normalized[code])
            default_value = bool(role_defaults.get(code, False))
            if requested == default_value:
                cur.execute("DELETE FROM user_permission_overrides WHERE user_id=%s AND permission_id=%s", (user_id, permission_id))
            else:
                cur.execute("""
                    INSERT INTO user_permission_overrides (user_id, permission_id, allowed, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (user_id, permission_id) DO UPDATE SET
                        allowed = EXCLUDED.allowed,
                        updated_at = now()
                """, (user_id, permission_id, requested))

def clear_user_permission_overrides(conn, user_id: int):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_permission_overrides WHERE user_id=%s", (user_id,))

def log_activity(conn, entity_type, entity_id, action, description, tenant_id=1, user_id=None, details=None, source_channel=None, user_name=None):
    resolved_user_name = user_name
    if not resolved_user_name:
        try:
            resolved_user_name = get_user_display_name(conn, tenant_id, user_id) if user_id else None
        except Exception:
            resolved_user_name = None
    if not resolved_user_name:
        resolved_user_name = str(user_id) if user_id else "system"
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO activity_timeline
            (entity_type, entity_id, action, description, user_name, tenant_id, user_id_ref, source_channel, details_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())""",
            (entity_type, str(entity_id), action,
             description[:500] if description else "",
             resolved_user_name,
             tenant_id,
             str(user_id) if user_id else None,
             source_channel,
             json.dumps(details or {}, ensure_ascii=False)))

def audit_request_event(request: Request, action: str, description: str, entity_type: str = "app_usage", entity_id: Any = "", details: Optional[dict] = None, source_channel: Optional[str] = None):
    try:
        user = get_request_user_payload(request)
        conn = get_db_conn()
        try:
            log_activity(
                conn,
                entity_type,
                entity_id or action,
                action,
                description,
                tenant_id=user["tenant_id"],
                user_id=user["user_id"],
                details=details,
                source_channel=source_channel,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_conn(conn)
    except Exception as e:
        print(f"audit_request_event failed: {e}")

# ========== TENANT CONFIG LOADER ==========
_tenant_config_cache = {}

def get_tenant_config(conn, tenant_id):
    """Load tenant configuration from DB. Cached per request cycle."""
    if tenant_id in _tenant_config_cache:
        return _tenant_config_cache[tenant_id]
    config = {"tenant_id": tenant_id, "found": False}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tenant_operating_profile WHERE tenant_id=%s", (tenant_id,))
            profile = cur.fetchone()
            if profile:
                config["found"] = True
                config["internal_language_mode"] = profile["internal_language_mode"]
                config["customer_language_mode"] = profile["customer_language_mode"]
                config["default_internal_lang"] = profile["default_internal_language_code"]
                config["default_customer_lang"] = profile["default_customer_language_code"]
                config["voice_input_strategy"] = profile["voice_input_strategy"]
                config["voice_output_strategy"] = profile["voice_output_strategy"]
                config["workspace_mode"] = profile["workspace_mode"]
                config["max_active_users"] = profile["max_active_users"]
            cur.execute("SELECT language_code, language_scope, is_default FROM tenant_languages WHERE tenant_id=%s AND is_active=true ORDER BY language_scope, sort_order", (tenant_id,))
            langs = [dict(r) for r in cur.fetchall()]
            config["languages"] = langs
            config["internal_langs"] = [l["language_code"] for l in langs if l["language_scope"]=="internal"]
            config["customer_langs"] = [l["language_code"] for l in langs if l["language_scope"]=="customer"]
            config["voice_input_langs"] = [l["language_code"] for l in langs if l["language_scope"]=="voice_input"]
            config["voice_output_langs"] = [l["language_code"] for l in langs if l["language_scope"]=="voice_output"]
            cur.execute("SELECT * FROM subscription_limits WHERE tenant_id=%s", (tenant_id,))
            limits = cur.fetchone()
            config["limits"] = dict(limits) if limits else None
            cur.execute("SELECT * FROM tenant_settings WHERE tenant_id=%s", (tenant_id,))
            settings = cur.fetchone()
            config["settings"] = dict(settings) if settings else None
    except Exception: pass
    _tenant_config_cache[tenant_id] = config
    return config

def resolve_response_language(config, request_lang=None):
    """Determine which language the AI should respond in."""
    if request_lang:
        code = request_lang.split("-")[0].lower()
        if code in ("cs","en","pl","de","fr","es","sk","ro"): return code
    if config.get("found"):
        return config.get("default_internal_lang", "en")
    return "en"

def resolve_customer_language(config, request_lang=None):
    """Determine default outgoing customer language."""
    if request_lang:
        return normalize_language_code(request_lang, default="en")
    if config.get("found"):
        return normalize_language_code(config.get("default_customer_lang", "en"), default="en")
    return "en"

def resolve_voice_language(config, request_lang=None):
    """Determine voice session language from config."""
    if request_lang and request_lang != "en":
        return request_lang.split("-")[0].lower()
    if config.get("found"):
        return config.get("default_internal_lang", "en")
    return "en"

def parse_planning_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue
    return None

def planning_window_from_values(start_value: Any = None, end_value: Any = None, date_value: Any = None) -> tuple[Optional[datetime], Optional[datetime]]:
    start_dt = parse_planning_datetime(start_value)
    end_dt = parse_planning_datetime(end_value)
    if not start_dt and date_value:
        date_dt = parse_planning_datetime(date_value)
        if date_dt:
            start_dt = date_dt.replace(hour=9, minute=0, second=0, microsecond=0)
            end_dt = start_dt + timedelta(hours=1)
    elif start_dt and not end_dt:
        end_dt = start_dt + timedelta(hours=1)
    return start_dt, end_dt

def format_planning_datetime(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%dT%H:%M:%S") if value else None

def clean_user_display_name(value: Optional[str]) -> str:
    cleaned = re.sub(r"\*+", " ", value or "")
    return re.sub(r"\s+", " ", cleaned).strip()

def clean_contact_display_name(value: Optional[str]) -> str:
    cleaned = re.sub(r"\*+", " ", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip()

def clean_user_row_display_name(row: dict) -> dict:
    row["display_name"] = clean_user_display_name(row.get("display_name")) or row.get("email") or row.get("display_name")
    return row

def get_user_display_name(conn, tenant_id: int, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    with conn.cursor() as cur:
        cur.execute("""SELECT display_name
            FROM users
            WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL""", (user_id, tenant_id))
        row = cur.fetchone()
    return clean_user_display_name(row["display_name"]) if row else None

def resolve_assigned_user(conn, tenant_id: int, assigned_user_id: Any = None, assigned_to: Any = None) -> tuple[Optional[int], Optional[str]]:
    if assigned_user_id not in (None, "", 0, "0"):
        with conn.cursor() as cur:
            cur.execute("""SELECT id, display_name
                FROM users
                WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL""", (assigned_user_id, tenant_id))
            row = cur.fetchone()
        if row:
            return int(row["id"]), clean_user_display_name(row["display_name"])
    assignee_text = (assigned_to or "").strip()
    if assignee_text:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, display_name
                FROM users
                WHERE tenant_id=%s AND deleted_at IS NULL AND (
                    display_name ILIKE %s OR email ILIKE %s OR COALESCE(phone,'') ILIKE %s
                )
                ORDER BY status='active' DESC, id
                LIMIT 1""", (tenant_id, f"%{assignee_text}%", f"%{assignee_text}%", f"%{assignee_text}%"))
            row = cur.fetchone()
        if row:
            return int(row["id"]), clean_user_display_name(row["display_name"])
        return None, assignee_text
    return None, None

def get_active_user_row(conn, tenant_id: int, user_id: Optional[int]) -> Optional[dict]:
    if not user_id:
        return None
    with conn.cursor() as cur:
        cur.execute("""
            SELECT u.id, u.display_name, u.email, COALESCE(r.role_name, 'viewer') AS role_name
            FROM users u
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE u.id=%s
              AND u.tenant_id=%s
              AND u.deleted_at IS NULL
              AND COALESCE(u.status, 'active')='active'
        """, (user_id, tenant_id))
        row = cur.fetchone()
    return clean_user_row_display_name(dict(row)) if row else None

def validate_active_user(conn, tenant_id: int, user_id: Optional[int], label: str = "assigned user") -> dict:
    row = get_active_user_row(conn, tenant_id, user_id)
    if not row:
        raise HTTPException(422, f"{label.capitalize()} must be an active user")
    return row

def validate_task_planning(task_payload: dict) -> tuple[Optional[datetime], Optional[datetime], Optional[str]]:
    planning_start, planning_end = planning_window_from_values(
        task_payload.get("planned_start_at"),
        task_payload.get("planned_end_at"),
        task_payload.get("planned_date") or task_payload.get("deadline"),
    )
    deadline = (task_payload.get("deadline") or "").strip() or None
    if not planning_start and not deadline:
        raise HTTPException(422, "Task must have planned_start_at or deadline")
    return planning_start, planning_end, deadline

def is_task_open_for_workflow(task_row: Optional[dict]) -> bool:
    if not task_row:
        return False
    if bool(task_row.get("is_completed")):
        return False
    return (task_row.get("status") or "novy") not in ("hotovo", "zruseno")

def next_business_day_at_nine(base_dt: Optional[datetime] = None) -> datetime:
    candidate = (base_dt or datetime.utcnow()).replace(hour=9, minute=0, second=0, microsecond=0)
    if candidate <= (base_dt or datetime.utcnow()):
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate

def merge_planning_note(existing_note: Optional[str], extra_note: str) -> str:
    existing = (existing_note or "").strip()
    extra = (extra_note or "").strip()
    if not existing:
        return extra
    if extra in existing:
        return existing
    return f"{existing}\n\n{extra}"

def get_default_hierarchy_user(conn, tenant_id: int) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT u.id, u.display_name, u.email, COALESCE(r.role_name, 'viewer') AS role_name
            FROM users u
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE u.tenant_id=%s
              AND u.deleted_at IS NULL
              AND COALESCE(u.status, 'active')='active'
            ORDER BY CASE
                WHEN COALESCE(r.role_name, '')='manager' THEN 0
                WHEN COALESCE(r.role_name, '')='admin' THEN 1
                ELSE 2
            END, u.id
            LIMIT 1
        """, (tenant_id,))
        row = cur.fetchone()
    return clean_user_row_display_name(dict(row)) if row else None

def get_valid_client_next_action_task(conn, tenant_id: int, client_id: int, task_id: Optional[str] = None) -> Optional[dict]:
    params: List[Any] = [tenant_id, client_id]
    sql = """
        SELECT t.id, t.title, t.client_id, t.job_id, t.assigned_user_id, t.assigned_to,
               t.status, COALESCE(t.is_completed, FALSE) AS is_completed,
               t.planned_start_at::text AS planned_start_at, t.deadline,
               u.display_name AS assignee_display_name
        FROM tasks t
        JOIN users u
          ON u.id = t.assigned_user_id
         AND u.tenant_id = t.tenant_id
         AND u.deleted_at IS NULL
         AND COALESCE(u.status, 'active')='active'
        WHERE t.tenant_id=%s
          AND t.client_id=%s
          AND t.job_id IS NULL
          AND COALESCE(t.is_completed, FALSE)=FALSE
          AND COALESCE(t.status, 'novy') NOT IN ('hotovo', 'zruseno')
          AND (t.planned_start_at IS NOT NULL OR NULLIF(COALESCE(t.deadline, ''), '') IS NOT NULL)
    """
    if task_id:
        sql += " AND t.id=%s"
        params.append(task_id)
    sql += " ORDER BY COALESCE(t.planned_start_at, t.created_at) ASC LIMIT 1"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return dict(row) if row else None

def get_valid_job_next_action_task(conn, tenant_id: int, job_id: int, task_id: Optional[str] = None) -> Optional[dict]:
    params: List[Any] = [tenant_id, job_id]
    sql = """
        SELECT t.id, t.title, t.client_id, t.job_id, t.assigned_user_id, t.assigned_to,
               t.status, COALESCE(t.is_completed, FALSE) AS is_completed,
               t.planned_start_at::text AS planned_start_at, t.deadline,
               u.display_name AS assignee_display_name
        FROM tasks t
        JOIN users u
          ON u.id = t.assigned_user_id
         AND u.tenant_id = t.tenant_id
         AND u.deleted_at IS NULL
         AND COALESCE(u.status, 'active')='active'
        WHERE t.tenant_id=%s
          AND t.job_id=%s
          AND COALESCE(t.is_completed, FALSE)=FALSE
          AND COALESCE(t.status, 'novy') NOT IN ('hotovo', 'zruseno')
          AND (t.planned_start_at IS NOT NULL OR NULLIF(COALESCE(t.deadline, ''), '') IS NOT NULL)
    """
    if task_id:
        sql += " AND t.id=%s"
        params.append(task_id)
    sql += " ORDER BY COALESCE(t.planned_start_at, t.created_at) ASC LIMIT 1"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return dict(row) if row else None

def validate_client_hierarchy(conn, tenant_id: int, client_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, display_name, owner_user_id, next_action_task_id, hierarchy_status
            FROM clients
            WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL
        """, (tenant_id, client_id))
        client_row = cur.fetchone()
    if not client_row:
        return {"valid": False, "issues": ["client_not_found"], "client": None, "next_action_task": None}
    client = dict(client_row)
    issues: List[str] = []
    owner_row = get_active_user_row(conn, tenant_id, client.get("owner_user_id"))
    if not owner_row:
        issues.append("missing_or_inactive_owner")
    next_action = None
    if client.get("next_action_task_id"):
        next_action = get_valid_client_next_action_task(conn, tenant_id, client_id, str(client["next_action_task_id"]))
    if not next_action:
        issues.append("missing_or_invalid_next_action")
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "client": client,
        "owner": owner_row,
        "next_action_task": next_action,
    }

def validate_job_hierarchy(conn, tenant_id: int, job_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, job_title, client_id, assigned_user_id, assigned_to, next_action_task_id, hierarchy_status
            FROM jobs
            WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL
        """, (tenant_id, job_id))
        job_row = cur.fetchone()
    if not job_row:
        return {"valid": False, "issues": ["job_not_found"], "job": None, "next_action_task": None}
    job = dict(job_row)
    issues: List[str] = []
    owner_row = get_active_user_row(conn, tenant_id, job.get("assigned_user_id"))
    if not owner_row:
        issues.append("missing_or_inactive_owner")
    next_action = None
    if job.get("next_action_task_id"):
        next_action = get_valid_job_next_action_task(conn, tenant_id, job_id, str(job["next_action_task_id"]))
    if not next_action:
        issues.append("missing_or_invalid_next_action")
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "job": job,
        "owner": owner_row,
        "next_action_task": next_action,
    }

def get_task_row(conn, tenant_id: int, task_id: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT *
            FROM tasks
            WHERE tenant_id=%s AND id=%s
        """, (tenant_id, str(task_id)))
        row = cur.fetchone()
    return dict(row) if row else None

def get_task_next_action_links(conn, tenant_id: int, task_id: str) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 'client' AS entity_type,
                   c.id AS entity_id,
                   c.display_name AS entity_name,
                   c.next_action_task_id,
                   c.owner_user_id,
                   c.id AS client_id,
                   NULL::BIGINT AS job_id
            FROM clients c
            WHERE c.tenant_id=%s
              AND c.deleted_at IS NULL
              AND c.next_action_task_id=%s
            UNION ALL
            SELECT 'job' AS entity_type,
                   j.id AS entity_id,
                   j.job_title AS entity_name,
                   j.next_action_task_id,
                   j.assigned_user_id AS owner_user_id,
                   j.client_id,
                   j.id AS job_id
            FROM jobs j
            WHERE j.tenant_id=%s
              AND j.deleted_at IS NULL
              AND j.next_action_task_id=%s
        """, (tenant_id, str(task_id), tenant_id, str(task_id)))
        rows = cur.fetchall()
    return [dict(row) for row in rows]

def set_client_next_action(conn, tenant_id: int, client_id: int, task_id: str) -> None:
    candidate = get_valid_client_next_action_task(conn, tenant_id, client_id, str(task_id))
    if not candidate:
        raise HTTPException(422, "Client next action must be an open planned task assigned to an active user")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE clients
            SET next_action_task_id=%s,
                hierarchy_status='valid',
                updated_at=now()
            WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL
        """, (str(task_id), tenant_id, client_id))

def set_job_next_action(conn, tenant_id: int, job_id: int, task_id: str) -> None:
    candidate = get_valid_job_next_action_task(conn, tenant_id, job_id, str(task_id))
    if not candidate:
        raise HTTPException(422, "Job next action must be an open planned task assigned to an active user")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE jobs
            SET next_action_task_id=%s,
                hierarchy_status='valid',
                updated_at=now()
            WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL
        """, (str(task_id), tenant_id, job_id))

def create_workflow_task(
    conn,
    tenant_id: int,
    task_payload: dict,
    *,
    actor_name: str,
    default_client_id: Optional[int] = None,
    default_client_name: Optional[str] = None,
    default_job_id: Optional[int] = None,
    default_property_id: Optional[int] = None,
    default_property_address: Optional[str] = None,
    source: str = "workflow",
) -> dict:
    if not isinstance(task_payload, dict):
        raise HTTPException(422, "Task payload is required")
    title = (task_payload.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "Task title is required")
    assigned_user_id, assigned_to = resolve_assigned_user(
        conn,
        tenant_id,
        task_payload.get("assigned_user_id"),
        task_payload.get("assigned_to"),
    )
    assignee = validate_active_user(conn, tenant_id, assigned_user_id, "task assignee")
    planning_start, planning_end, deadline = validate_task_planning(task_payload)
    task_id = str(task_payload.get("id") or uuid.uuid4())
    client_id = task_payload.get("client_id", default_client_id)
    client_name = task_payload.get("client_name", default_client_name)
    job_id = task_payload.get("job_id", default_job_id)
    property_id = task_payload.get("property_id", default_property_id)
    property_address = task_payload.get("property_address", default_property_address)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tasks (
                id,title,description,task_type,status,priority,deadline,planned_date,
                planned_start_at,planned_end_at,estimated_minutes,created_by,assigned_to,assigned_user_id,
                planning_note,reminder_for_assignee_only,delegated_by,client_id,client_name,job_id,property_id,property_address,
                is_recurring,recurrence_rule,communication_method,source,is_billable,has_cost,checklist,calendar_sync_enabled,tenant_id
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s
            ) RETURNING *
        """, (
            task_id,
            title,
            task_payload.get("description"),
            task_payload.get("task_type", "interni_poznamka"),
            task_payload.get("status", "novy"),
            task_payload.get("priority", "bezna"),
            deadline,
            task_payload.get("planned_date") or deadline,
            planning_start,
            planning_end,
            task_payload.get("estimated_minutes"),
            actor_name,
            assigned_to or assignee["display_name"],
            int(assignee["id"]),
            task_payload.get("planning_note"),
            task_payload.get("reminder_for_assignee_only", True),
            task_payload.get("delegated_by") or actor_name,
            client_id,
            client_name,
            job_id,
            property_id,
            property_address,
            task_payload.get("is_recurring", False),
            task_payload.get("recurrence_rule"),
            task_payload.get("communication_method"),
            task_payload.get("source") or source,
            task_payload.get("is_billable", False),
            task_payload.get("has_cost", False),
            json.dumps(task_payload.get("checklist", [])),
            task_payload.get("calendar_sync_enabled", True),
            tenant_id,
        ))
        row = cur.fetchone()
    return dict(row)

def resolve_replacement_task_for_link(
    conn,
    tenant_id: int,
    link: dict,
    current_task_id: str,
    *,
    replacement_task_id: Optional[str] = None,
    replacement_task_payload: Optional[dict] = None,
    actor_name: str,
    fallback_task: Optional[dict] = None,
) -> dict:
    if replacement_task_id:
        if str(replacement_task_id) == str(current_task_id):
            raise HTTPException(422, "Replacement task must be different from current next action")
        candidate = (
            get_valid_client_next_action_task(conn, tenant_id, int(link["entity_id"]), str(replacement_task_id))
            if link["entity_type"] == "client"
            else get_valid_job_next_action_task(conn, tenant_id, int(link["entity_id"]), str(replacement_task_id))
        )
        if not candidate:
            raise HTTPException(422, "Replacement task must be an open planned task linked to the same entity")
        return candidate
    if not replacement_task_payload:
        raise HTTPException(422, "Current next action cannot be completed without replacement_task_id or replacement_task_payload")
    payload = dict(replacement_task_payload)
    if fallback_task:
        payload.setdefault("client_id", fallback_task.get("client_id"))
        payload.setdefault("client_name", fallback_task.get("client_name"))
        payload.setdefault("job_id", fallback_task.get("job_id"))
        payload.setdefault("property_id", fallback_task.get("property_id"))
        payload.setdefault("property_address", fallback_task.get("property_address"))
    if link["entity_type"] == "client":
        payload["client_id"] = int(link["entity_id"])
        payload["job_id"] = None
    else:
        payload["job_id"] = int(link["entity_id"])
        payload.setdefault("client_id", link.get("client_id"))
    return create_workflow_task(
        conn,
        tenant_id,
        payload,
        actor_name=actor_name,
        default_client_id=payload.get("client_id"),
        default_client_name=payload.get("client_name"),
        default_job_id=payload.get("job_id"),
        default_property_id=payload.get("property_id"),
        default_property_address=payload.get("property_address"),
        source="replacement_workflow",
    )

def replace_next_action_links(
    conn,
    tenant_id: int,
    task_row: dict,
    *,
    replacement_task_id: Optional[str] = None,
    replacement_task_payload: Optional[dict] = None,
    actor_user_id: Optional[int] = None,
    actor_name: str,
) -> List[dict]:
    links = get_task_next_action_links(conn, tenant_id, str(task_row["id"]))
    replacements: List[dict] = []
    for link in links:
        replacement = resolve_replacement_task_for_link(
            conn,
            tenant_id,
            link,
            str(task_row["id"]),
            replacement_task_id=replacement_task_id,
            replacement_task_payload=replacement_task_payload,
            actor_name=actor_name,
            fallback_task=task_row,
        )
        if link["entity_type"] == "client":
            set_client_next_action(conn, tenant_id, int(link["entity_id"]), str(replacement["id"]))
            log_activity(
                conn,
                "client",
                int(link["entity_id"]),
                "change_next_action",
                f"Client next action changed from {task_row['id']} to {replacement['id']}",
                tenant_id=tenant_id,
                user_id=actor_user_id,
                source_channel="hierarchy",
                details={"before": str(task_row["id"]), "after": str(replacement["id"])},
            )
        else:
            set_job_next_action(conn, tenant_id, int(link["entity_id"]), str(replacement["id"]))
            log_activity(
                conn,
                "job",
                int(link["entity_id"]),
                "change_next_action",
                f"Job next action changed from {task_row['id']} to {replacement['id']}",
                tenant_id=tenant_id,
                user_id=actor_user_id,
                source_channel="hierarchy",
                details={"before": str(task_row["id"]), "after": str(replacement["id"])},
            )
        replacements.append({"link": link, "task": replacement})
    return replacements

def get_user_deactivation_blockers(conn, tenant_id: int, user_id: int) -> dict:
    blockers = {"clients": [], "jobs": [], "open_tasks": [], "next_action_tasks": []}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, display_name
            FROM clients
            WHERE tenant_id=%s AND deleted_at IS NULL AND owner_user_id=%s
            ORDER BY id
            LIMIT 20
        """, (tenant_id, user_id))
        blockers["clients"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""
            SELECT id, job_title
            FROM jobs
            WHERE tenant_id=%s AND deleted_at IS NULL AND assigned_user_id=%s
            ORDER BY id
            LIMIT 20
        """, (tenant_id, user_id))
        blockers["jobs"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""
            SELECT id, title, client_id, job_id
            FROM tasks
            WHERE tenant_id=%s
              AND assigned_user_id=%s
              AND COALESCE(is_completed, FALSE)=FALSE
              AND COALESCE(status, 'novy') NOT IN ('hotovo', 'zruseno')
            ORDER BY created_at DESC
            LIMIT 20
        """, (tenant_id, user_id))
        blockers["open_tasks"] = [dict(row) for row in cur.fetchall()]
        cur.execute("""
            SELECT t.id, t.title, c.id AS client_id, j.id AS job_id
            FROM tasks t
            LEFT JOIN clients c
              ON c.tenant_id=t.tenant_id
             AND c.deleted_at IS NULL
             AND c.next_action_task_id=t.id
            LEFT JOIN jobs j
              ON j.tenant_id=t.tenant_id
             AND j.deleted_at IS NULL
             AND j.next_action_task_id=t.id
            WHERE t.tenant_id=%s
              AND t.assigned_user_id=%s
              AND (c.id IS NOT NULL OR j.id IS NOT NULL)
            ORDER BY t.created_at DESC
            LIMIT 20
        """, (tenant_id, user_id))
        blockers["next_action_tasks"] = [dict(row) for row in cur.fetchall()]
    blockers["has_blockers"] = any(bool(blockers[key]) for key in ("clients", "jobs", "open_tasks", "next_action_tasks"))
    return blockers

def create_hierarchy_placeholder_task(
    conn,
    tenant_id: int,
    *,
    assigned_user_id: int,
    assigned_to: str,
    client_id: Optional[int] = None,
    client_name: Optional[str] = None,
    job_id: Optional[int] = None,
    property_id: Optional[int] = None,
    property_address: Optional[str] = None,
    created_by: str = "system_migration",
) -> dict:
    task_id = str(uuid.uuid4())
    planned_start = next_business_day_at_nine()
    planned_end = planned_start + timedelta(hours=1)
    placeholder_note = "Systémově vytvořený task při migraci hierarchie. Nutná ruční kontrola."
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tasks (
                id, title, description, task_type, status, priority, deadline, planned_date,
                planned_start_at, planned_end_at, created_by, assigned_to, assigned_user_id,
                planning_note, reminder_for_assignee_only, delegated_by,
                client_id, client_name, job_id, property_id, property_address,
                source, calendar_sync_enabled, tenant_id
            ) VALUES (
                %s, %s, %s, 'interni_poznamka', 'novy', 'vysoka', %s, %s,
                %s, %s, %s, %s, %s,
                %s, TRUE, %s,
                %s, %s, %s, %s, %s,
                'system_migration', TRUE, %s
            )
            RETURNING id, title, assigned_user_id, assigned_to, client_id, job_id,
                      planned_start_at::text AS planned_start_at, deadline
        """, (
            task_id,
            "Doplnit další krok",
            "Systémově vytvořený task při migraci hierarchie. Nutná ruční kontrola.",
            planned_start.strftime("%Y-%m-%d %H:%M:%S"),
            planned_start.strftime("%Y-%m-%d"),
            planned_start,
            planned_end,
            created_by,
            assigned_to,
            assigned_user_id,
            placeholder_note,
            created_by,
            client_id,
            client_name,
            job_id,
            property_id,
            property_address,
            tenant_id,
        ))
        row = cur.fetchone()
    return dict(row)

def build_blocked_user_deactivations(conn, tenant_id: int) -> List[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT u.id, u.display_name, u.email,
                EXISTS(SELECT 1 FROM clients c WHERE c.tenant_id=u.tenant_id AND c.deleted_at IS NULL AND c.owner_user_id=u.id) AS owns_clients,
                EXISTS(SELECT 1 FROM jobs j WHERE j.tenant_id=u.tenant_id AND j.deleted_at IS NULL AND j.assigned_user_id=u.id) AS owns_jobs,
                EXISTS(
                    SELECT 1 FROM tasks t
                    WHERE t.tenant_id=u.tenant_id
                      AND t.assigned_user_id=u.id
                      AND COALESCE(t.is_completed, FALSE)=FALSE
                      AND COALESCE(t.status, 'novy') NOT IN ('hotovo', 'zruseno')
                ) AS has_open_tasks,
                EXISTS(
                    SELECT 1
                    FROM clients c
                    JOIN tasks t ON t.id = c.next_action_task_id
                    WHERE c.tenant_id=u.tenant_id
                      AND c.deleted_at IS NULL
                      AND t.assigned_user_id=u.id
                ) OR EXISTS(
                    SELECT 1
                    FROM jobs j
                    JOIN tasks t ON t.id = j.next_action_task_id
                    WHERE j.tenant_id=u.tenant_id
                      AND j.deleted_at IS NULL
                      AND t.assigned_user_id=u.id
                ) AS owns_next_actions
            FROM users u
            WHERE u.tenant_id=%s
              AND u.deleted_at IS NULL
              AND COALESCE(u.status, 'active')='active'
            ORDER BY u.display_name, u.id
        """, (tenant_id,))
        rows = cur.fetchall()
    blocked = []
    for row in rows:
        item = dict(row)
        reasons = []
        if item.get("owns_clients"):
            reasons.append("client_owner")
        if item.get("owns_jobs"):
            reasons.append("job_owner")
        if item.get("has_open_tasks"):
            reasons.append("open_task_assignee")
        if item.get("owns_next_actions"):
            reasons.append("next_action_assignee")
        if reasons:
            item["reasons"] = reasons
            blocked.append(item)
    return blocked

def get_hierarchy_integrity_report(conn, tenant_id: int) -> dict:
    orphan_clients: List[dict] = []
    orphan_jobs: List[dict] = []
    orphan_tasks: List[dict] = []
    next_action_mismatches: List[dict] = []

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, display_name, owner_user_id, next_action_task_id, hierarchy_status
            FROM clients
            WHERE tenant_id=%s AND deleted_at IS NULL
            ORDER BY display_name, id
        """, (tenant_id,))
        client_rows = [dict(row) for row in cur.fetchall()]
        cur.execute("""
            SELECT id, job_title, client_id, assigned_user_id, assigned_to, next_action_task_id, hierarchy_status
            FROM jobs
            WHERE tenant_id=%s AND deleted_at IS NULL
            ORDER BY created_at DESC, id DESC
        """, (tenant_id,))
        job_rows = [dict(row) for row in cur.fetchall()]
        cur.execute("""
            SELECT t.id, t.title, t.client_id, t.job_id, t.assigned_user_id, t.assigned_to, t.status,
                   COALESCE(t.is_completed, FALSE) AS is_completed,
                   t.planned_start_at::text AS planned_start_at, t.deadline,
                   u.id AS active_user_id
            FROM tasks t
            LEFT JOIN users u
              ON u.id = t.assigned_user_id
             AND u.tenant_id = t.tenant_id
             AND u.deleted_at IS NULL
             AND COALESCE(u.status, 'active')='active'
            WHERE t.tenant_id=%s
            ORDER BY t.created_at DESC
        """, (tenant_id,))
        task_rows = [dict(row) for row in cur.fetchall()]

    for client in client_rows:
        validation = validate_client_hierarchy(conn, tenant_id, client["id"])
        if not validation["valid"]:
            item = {
                "id": client["id"],
                "display_name": client["display_name"],
                "owner_user_id": client.get("owner_user_id"),
                "next_action_task_id": client.get("next_action_task_id"),
                "issues": validation["issues"],
            }
            orphan_clients.append(item)
            if any("next_action" in issue for issue in validation["issues"]):
                next_action_mismatches.append({"entity_type": "client", **item})

    for job in job_rows:
        validation = validate_job_hierarchy(conn, tenant_id, job["id"])
        if not validation["valid"]:
            item = {
                "id": job["id"],
                "job_title": job["job_title"],
                "client_id": job.get("client_id"),
                "assigned_user_id": job.get("assigned_user_id"),
                "next_action_task_id": job.get("next_action_task_id"),
                "issues": validation["issues"],
            }
            orphan_jobs.append(item)
            if any("next_action" in issue for issue in validation["issues"]):
                next_action_mismatches.append({"entity_type": "job", **item})

    for task in task_rows:
        issues: List[str] = []
        if not task.get("assigned_user_id") or not task.get("active_user_id"):
            issues.append("missing_or_inactive_assignee")
        if not task.get("planned_start_at") and not ((task.get("deadline") or "").strip()):
            issues.append("missing_planning")
        if issues:
            orphan_tasks.append({
                "id": task["id"],
                "title": task["title"],
                "client_id": task.get("client_id"),
                "job_id": task.get("job_id"),
                "assigned_user_id": task.get("assigned_user_id"),
                "status": task.get("status"),
                "issues": issues,
            })

    blocked_user_deactivations = build_blocked_user_deactivations(conn, tenant_id)
    return {
        "tenant_id": tenant_id,
        "orphan_clients": orphan_clients,
        "orphan_jobs": orphan_jobs,
        "orphan_tasks": orphan_tasks,
        "blocked_user_deactivations": blocked_user_deactivations,
        "next_action_mismatches": next_action_mismatches,
        "summary": {
            "orphan_clients": len(orphan_clients),
            "orphan_jobs": len(orphan_jobs),
            "orphan_tasks": len(orphan_tasks),
            "blocked_user_deactivations": len(blocked_user_deactivations),
            "next_action_mismatches": len(next_action_mismatches),
        },
    }

def run_hierarchy_backfill(conn, tenant_id: int, actor_user_id: Optional[int] = None, dry_run: bool = True) -> dict:
    default_user = get_default_hierarchy_user(conn, tenant_id)
    if not default_user:
        raise HTTPException(422, "No active user is available for hierarchy backfill")

    actor_name = get_user_display_name(conn, tenant_id, actor_user_id) or "system_migration"
    updates = {"clients": [], "jobs": [], "tasks": []}
    client_owner_cache: Dict[int, dict] = {}

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, display_name, owner_user_id, next_action_task_id
            FROM clients
            WHERE tenant_id=%s AND deleted_at IS NULL
            ORDER BY id
        """, (tenant_id,))
        clients = [dict(row) for row in cur.fetchall()]

    for client in clients:
        active_owner = get_active_user_row(conn, tenant_id, client.get("owner_user_id"))
        next_action = get_valid_client_next_action_task(conn, tenant_id, client["id"], client.get("next_action_task_id"))
        fallback_task = next_action or get_valid_client_next_action_task(conn, tenant_id, client["id"])
        target_owner = active_owner
        if not target_owner and fallback_task:
            target_owner = get_active_user_row(conn, tenant_id, fallback_task.get("assigned_user_id"))
        if not target_owner:
            target_owner = default_user
        target_task = fallback_task
        if not target_task and not dry_run:
            target_task = create_hierarchy_placeholder_task(
                conn,
                tenant_id,
                assigned_user_id=int(target_owner["id"]),
                assigned_to=target_owner["display_name"],
                client_id=client["id"],
                client_name=client["display_name"],
                created_by=actor_name,
            )
        changes = {
            "owner_user_id": int(target_owner["id"]),
            "next_action_task_id": target_task["id"] if target_task else "placeholder_required",
            "placeholder_created": bool(target_task and target_task.get("id") not in (client.get("next_action_task_id"), None) and not fallback_task),
        }
        client_owner_cache[client["id"]] = target_owner
        needs_update = (
            client.get("owner_user_id") != changes["owner_user_id"] or
            str(client.get("next_action_task_id") or "") != str(changes["next_action_task_id"] or "")
        )
        if needs_update and not dry_run and target_task:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE clients
                    SET owner_user_id=%s, next_action_task_id=%s, hierarchy_status='valid', updated_at=now()
                    WHERE tenant_id=%s AND id=%s
                """, (changes["owner_user_id"], changes["next_action_task_id"], tenant_id, client["id"]))
            log_activity(
                conn, "client", client["id"], "hierarchy_backfill",
                f"Client hierarchy backfilled for {client['display_name']}",
                tenant_id=tenant_id, user_id=actor_user_id, source_channel="hierarchy_migration",
                details={"before": client, "after": changes},
            )
        if needs_update or changes["next_action_task_id"] == "placeholder_required":
            updates["clients"].append({
                "id": client["id"],
                "display_name": client["display_name"],
                "changes": changes,
            })

    job_owner_cache: Dict[int, dict] = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT j.id, j.job_title, j.client_id, j.property_id, j.assigned_user_id, j.next_action_task_id,
                   c.display_name AS client_name, p.property_name, p.address_line1
            FROM jobs j
            LEFT JOIN clients c ON c.id = j.client_id
            LEFT JOIN properties p ON p.id = j.property_id
            WHERE j.tenant_id=%s AND j.deleted_at IS NULL
            ORDER BY j.id
        """, (tenant_id,))
        jobs = [dict(row) for row in cur.fetchall()]

    for job in jobs:
        active_owner = get_active_user_row(conn, tenant_id, job.get("assigned_user_id"))
        next_action = get_valid_job_next_action_task(conn, tenant_id, job["id"], job.get("next_action_task_id"))
        fallback_task = next_action or get_valid_job_next_action_task(conn, tenant_id, job["id"])
        target_owner = active_owner
        if not target_owner and fallback_task:
            target_owner = get_active_user_row(conn, tenant_id, fallback_task.get("assigned_user_id"))
        if not target_owner and job.get("client_id"):
            target_owner = client_owner_cache.get(int(job["client_id"]))
        if not target_owner:
            target_owner = default_user
        job_owner_cache[job["id"]] = target_owner
        target_task = fallback_task
        if not target_task and not dry_run:
            target_task = create_hierarchy_placeholder_task(
                conn,
                tenant_id,
                assigned_user_id=int(target_owner["id"]),
                assigned_to=target_owner["display_name"],
                client_id=job.get("client_id"),
                client_name=job.get("client_name"),
                job_id=job["id"],
                property_id=job.get("property_id"),
                property_address=job.get("address_line1") or job.get("property_name"),
                created_by=actor_name,
            )
        changes = {
            "assigned_user_id": int(target_owner["id"]),
            "assigned_to": target_owner["display_name"],
            "next_action_task_id": target_task["id"] if target_task else "placeholder_required",
            "placeholder_created": bool(target_task and target_task.get("id") not in (job.get("next_action_task_id"), None) and not fallback_task),
        }
        needs_update = (
            job.get("assigned_user_id") != changes["assigned_user_id"] or
            (job.get("next_action_task_id") or "") != (changes["next_action_task_id"] or "")
        )
        if needs_update and not dry_run and target_task:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE jobs
                    SET assigned_user_id=%s,
                        assigned_to=%s,
                        next_action_task_id=%s,
                        hierarchy_status='valid',
                        updated_at=now()
                    WHERE tenant_id=%s AND id=%s
                """, (changes["assigned_user_id"], changes["assigned_to"], changes["next_action_task_id"], tenant_id, job["id"]))
            log_activity(
                conn, "job", job["id"], "hierarchy_backfill",
                f"Job hierarchy backfilled for {job['job_title']}",
                tenant_id=tenant_id, user_id=actor_user_id, source_channel="hierarchy_migration",
                details={"before": job, "after": changes},
            )
        if needs_update or changes["next_action_task_id"] == "placeholder_required":
            updates["jobs"].append({
                "id": job["id"],
                "job_title": job["job_title"],
                "changes": changes,
            })

    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.title, t.client_id, t.job_id, t.assigned_user_id, t.assigned_to, t.planning_note,
                   t.planned_start_at::text AS planned_start_at, t.planned_end_at::text AS planned_end_at, t.deadline,
                   u.id AS active_user_id
            FROM tasks t
            LEFT JOIN users u
              ON u.id = t.assigned_user_id
             AND u.tenant_id = t.tenant_id
             AND u.deleted_at IS NULL
             AND COALESCE(u.status, 'active')='active'
            WHERE t.tenant_id=%s
            ORDER BY t.created_at DESC
        """, (tenant_id,))
        tasks = [dict(row) for row in cur.fetchall()]

    for task in tasks:
        needs_assignee = not task.get("assigned_user_id") or not task.get("active_user_id")
        has_planning = bool(task.get("planned_start_at")) or bool((task.get("deadline") or "").strip())
        if not needs_assignee and has_planning:
            continue
        target_owner = None
        if task.get("job_id"):
            target_owner = job_owner_cache.get(int(task["job_id"]))
        if not target_owner and task.get("client_id"):
            target_owner = client_owner_cache.get(int(task["client_id"]))
        if not target_owner:
            target_owner = default_user
        planning_start = parse_planning_datetime(task.get("planned_start_at"))
        planning_end = parse_planning_datetime(task.get("planned_end_at"))
        deadline = (task.get("deadline") or "").strip() or None
        planning_note = task.get("planning_note")
        if not planning_start and not deadline:
            planning_start = next_business_day_at_nine()
            planning_end = planning_start + timedelta(hours=1)
            deadline = planning_start.strftime("%Y-%m-%d %H:%M:%S")
            planning_note = merge_planning_note(planning_note, "Systémově doplněný termín během migrace hierarchie. Nutná ruční kontrola.")
        changes = {
            "assigned_user_id": int(target_owner["id"]),
            "assigned_to": target_owner["display_name"],
            "planned_start_at": format_planning_datetime(planning_start),
            "planned_end_at": format_planning_datetime(planning_end),
            "deadline": deadline,
        }
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE tasks
                    SET assigned_user_id=%s,
                        assigned_to=%s,
                        planned_start_at=%s,
                        planned_end_at=%s,
                        deadline=%s,
                        planning_note=%s,
                        updated_at=now()
                    WHERE tenant_id=%s AND id=%s
                """, (
                    changes["assigned_user_id"],
                    changes["assigned_to"],
                    planning_start,
                    planning_end,
                    deadline,
                    planning_note,
                    tenant_id,
                    task["id"],
                ))
            log_activity(
                conn, "task", task["id"], "hierarchy_backfill",
                f"Task hierarchy backfilled for {task['title']}",
                tenant_id=tenant_id, user_id=actor_user_id, source_channel="hierarchy_migration",
                details={"before": task, "after": changes},
            )
        updates["tasks"].append({
            "id": task["id"],
            "title": task["title"],
            "changes": changes,
        })

    if not dry_run:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE clients
                SET hierarchy_status = CASE
                    WHEN owner_user_id IS NOT NULL AND next_action_task_id IS NOT NULL THEN 'valid'
                    ELSE 'invalid'
                END
                WHERE tenant_id=%s AND deleted_at IS NULL
            """, (tenant_id,))
            cur.execute("""
                UPDATE jobs
                SET hierarchy_status = CASE
                    WHEN assigned_user_id IS NOT NULL AND next_action_task_id IS NOT NULL THEN 'valid'
                    ELSE 'invalid'
                END
                WHERE tenant_id=%s AND deleted_at IS NULL
            """, (tenant_id,))

    return {
        "tenant_id": tenant_id,
        "dry_run": dry_run,
        "default_user": {"id": default_user["id"], "display_name": default_user["display_name"]},
        "updates": updates,
        "summary": {
            "clients": len(updates["clients"]),
            "jobs": len(updates["jobs"]),
            "tasks": len(updates["tasks"]),
        },
    }

def build_calendar_entry(
    entry_type: str,
    source: dict,
    current_user_id: Optional[int],
    job_title: Optional[str] = None,
) -> Optional[dict]:
    planned_start, planned_end = planning_window_from_values(
        source.get("planned_start_at"),
        source.get("planned_end_at"),
        source.get("planned_date") or source.get("start_date_planned") or source.get("deadline"),
    )
    if not planned_start:
        return None
    assigned_user_id = source.get("assigned_user_id")
    try:
        assigned_user_id = int(assigned_user_id) if assigned_user_id is not None else None
    except Exception:
        assigned_user_id = None
    assigned_to = source.get("assigned_to")
    is_assigned_to_current = bool(current_user_id and assigned_user_id and current_user_id == assigned_user_id)
    if entry_type == "task":
        display_mode = "reminder" if is_assigned_to_current else "info"
    else:
        display_mode = "assigned" if is_assigned_to_current else "shared"
    return {
        "entry_key": f"{entry_type}:{source.get('id')}",
        "entry_type": entry_type,
        "source_id": source.get("id"),
        "title": source.get("title") or source.get("job_title") or "",
        "client_name": source.get("client_name"),
        "job_title": job_title,
        "assigned_user_id": assigned_user_id,
        "assigned_to": assigned_to,
        "is_assigned_to_current": is_assigned_to_current,
        "display_mode": display_mode,
        "planned_start_at": format_planning_datetime(planned_start),
        "planned_end_at": format_planning_datetime(planned_end),
        "planned_date": source.get("planned_date") or source.get("start_date_planned") or source.get("deadline"),
        "description": source.get("planning_note") or source.get("handover_note"),
        "calendar_sync_enabled": bool(source.get("calendar_sync_enabled", True)),
        "reminder_for_assignee_only": bool(source.get("reminder_for_assignee_only", True)),
        "status": source.get("status") or source.get("job_status"),
    }

def encode_photo_data_url(content: bytes, filename: str, content_type: Optional[str] = None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    mime = content_type or {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"

def guess_mime_type(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")

def map_photo_row_to_job_photo(row: dict) -> dict:
    return {
        "id": row["id"],
        "job_id": int(row["entity_id"]),
        "url": row.get("file_path") or row.get("thumbnail_base64") or "",
        "description": row.get("description"),
        "photo_type": row.get("photo_type") or "general",
        "uploaded_by": row.get("created_by"),
        "uploaded_at": row.get("created_at"),
    }

def history_type_label(language: str, recognition_type: str) -> str:
    key = (recognition_type or "").lower()
    return {
        "plant_identification": tr_lang(language, "Plant", "Rostlina", "Roślina"),
        "plant_health_assessment": tr_lang(language, "Plant disease", "Choroba rostliny", "Choroba rośliny"),
        "mushroom_identification": tr_lang(language, "Mushroom", "Houba", "Grzyb"),
    }.get(key, recognition_type)

def map_nature_history_entry(row: dict, language: str) -> dict:
    result_json = row.get("result_json") or {}
    photos = row.get("photos_json") or []
    return {
        "id": row["id"],
        "recognition_type": row.get("recognition_type"),
        "recognition_label": history_type_label(language, row.get("recognition_type") or ""),
        "display_name": row.get("display_name") or "",
        "scientific_name": row.get("scientific_name") or "",
        "confidence": float(row.get("confidence") or 0.0),
        "guidance": row.get("guidance"),
        "database": row.get("database_name"),
        "captured_at": row.get("captured_at"),
        "created_at": row.get("created_at"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "accuracy_meters": row.get("accuracy_meters"),
        "location_source": row.get("location_source"),
        "owner_user_id": row.get("owner_user_id"),
        "owner_display_name": row.get("owner_display_name") or "",
        "owner_email": row.get("owner_email") or "",
        "photos": photos if isinstance(photos, list) else [],
        "result": result_json if isinstance(result_json, dict) else {},
    }

def map_admin_activity_entry(row: dict) -> dict:
    details = row.get("details_json") or {}
    return {
        "id": row["id"],
        "entity_type": row.get("entity_type") or "",
        "entity_id": row.get("entity_id") or "",
        "action": row.get("action") or "",
        "description": row.get("description") or "",
        "source_channel": row.get("source_channel") or "",
        "created_at": row.get("created_at"),
        "actor_user_id": int(row["actor_user_id"]) if row.get("actor_user_id") not in (None, "") else None,
        "actor_display_name": row.get("actor_display_name") or row.get("user_name") or "",
        "actor_email": row.get("actor_email") or "",
        "details": details if isinstance(details, dict) else {},
    }

async def build_history_photos(
    uploads: List[UploadFile],
    photo_types: Optional[List[str]] = None,
) -> List[dict]:
    photos = []
    for index, upload in enumerate(uploads):
        content = await upload.read()
        if content:
            filename = upload.filename or f"capture_{index + 1}.jpg"
            photos.append({
                "filename": filename,
                "photo_type": (photo_types[index] if photo_types and index < len(photo_types) else "capture") or "capture",
                "content_type": upload.content_type or guess_mime_type(filename),
                "size_bytes": len(content),
                "url": encode_photo_data_url(content, filename, upload.content_type),
            })
        await upload.seek(0)
    return photos

def store_nature_history(
    conn,
    tenant_id: int,
    user_id: Optional[int],
    recognition_type: str,
    language: str,
    result: dict,
    photos: List[dict],
    captured_at: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    accuracy_meters: Optional[float] = None,
    location_source: Optional[str] = None,
):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO nature_recognition_history
              (tenant_id, user_id, recognition_type, language, display_name, scientific_name,
               confidence, guidance, database_name, result_json, photos_json, captured_at,
               latitude, longitude, accuracy_meters, location_source)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)
             RETURNING id""",
            (
                tenant_id,
                user_id,
                recognition_type,
                language,
                result.get("display_name") or result.get("top_issue_name") or "",
                result.get("scientific_name") or "",
                result.get("score") or result.get("probability") or result.get("top_issue_probability") or result.get("health_probability") or 0.0,
                result.get("guidance"),
                result.get("database"),
                json.dumps(result, ensure_ascii=False),
                json.dumps(photos, ensure_ascii=False),
                captured_at,
                latitude,
                longitude,
                accuracy_meters,
                location_source,
            ),
        )
        history_id = cur.fetchone()[0]
        for index, photo in enumerate(photos or []):
            cur.execute(
                """INSERT INTO nature_recognition_photos
                  (history_id, tenant_id, sort_order, filename, photo_type, content_type, size_bytes, photo_data_url)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    history_id,
                    tenant_id,
                    index,
                    photo.get("filename"),
                    photo.get("photo_type") or "capture",
                    photo.get("content_type"),
                    photo.get("size_bytes"),
                    photo.get("url") or "",
                ),
            )

def map_audit_row_to_job_audit(row: dict) -> dict:
    return {
        "id": row["id"],
        "job_id": int(row["entity_id"]),
        "action_type": row.get("action"),
        "description": row.get("description") or "",
        "user_name": row.get("user_name"),
        "created_at": row.get("created_at"),
    }

# ========== TENANT GUARD ==========
def verify_tenant(conn, tenant_id):
    """Verify tenant exists and is active. Raises HTTPException if not."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, status FROM tenants WHERE id=%s", (tenant_id,))
        tenant = cur.fetchone()
        if not tenant:
            raise HTTPException(404, f"Tenant {tenant_id} not found")
        if tenant.get("status") and tenant["status"] not in ("active", "trial", "setup"):
            raise HTTPException(403, f"Tenant {tenant_id} is {tenant['status']}")
    return True

def verify_tenant_ownership(conn, tenant_id, table, record_id):
    """Verify a record belongs to the given tenant."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT tenant_id FROM {table} WHERE id=%s", (record_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Record {record_id} not found in {table}")
        if row["tenant_id"] != tenant_id:
            raise HTTPException(403, f"Access denied: record belongs to different tenant")
    return True

def audit_config_change(conn, tenant_id, action, detail):
    """Log configuration change to activity_timeline."""
    log_activity(conn, "tenant_config", str(tenant_id), action, detail, tenant_id=tenant_id)

def normalize_phone(phone: Optional[str]) -> str:
    raw = (phone or "").strip()
    return "".join(ch for ch in raw if ch.isdigit())

def normalize_whatsapp_phone(phone: Optional[str]) -> str:
    digits = normalize_phone(phone)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = "44" + digits[1:]
    return digits

def normalize_contact_phone(phone: Optional[str]) -> str:
    digits = normalize_phone(phone)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("44") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits

def normalize_communication_source(source: Optional[str]) -> str:
    raw = (source or "").strip().lower().replace(" ", "")
    aliases = {
        "wa": "whatsapp",
        "whatsup": "whatsapp",
        "whatsappmessage": "whatsapp",
        "text": "sms",
        "txt": "sms",
        "smsmessage": "sms",
        "phone": "telefon",
        "call": "telefon",
    }
    return aliases.get(raw, raw or "manual")

def parse_communication_timestamp(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 100000000000:
                timestamp = timestamp / 1000.0
            return datetime.fromtimestamp(timestamp, ZoneInfo("UTC"))
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):
            return parse_communication_timestamp(float(text))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return parsedate_to_datetime(text)
    except Exception:
        return None

def find_client_by_communication_phone(cur, tenant_id: int, phone: Optional[str]) -> Optional[Dict[str, Any]]:
    wanted = normalize_whatsapp_phone(phone)
    if not wanted:
        return None
    cur.execute("""
        SELECT id, display_name, phone_primary, phone_secondary
        FROM clients
        WHERE tenant_id=%s AND deleted_at IS NULL
    """, (tenant_id,))
    for row in cur.fetchall():
        if normalize_whatsapp_phone(row.get("phone_primary")) == wanted or normalize_whatsapp_phone(row.get("phone_secondary")) == wanted:
            return dict(row)
    return None

def upsert_communication_message(cur, tenant_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    source = normalize_communication_source(data.get("source") or data.get("comm_type"))
    comm_type = data.get("comm_type") or ("whatsapp" if source == "whatsapp" else "sms" if source == "sms" else source)
    direction = (data.get("direction") or "inbound").strip().lower()
    if direction.startswith("out"):
        direction = "outbound"
    elif direction.startswith("in"):
        direction = "inbound"
    message = data.get("message") or data.get("message_summary") or data.get("body") or ""
    source_phone = data.get("source_phone") or data.get("from")
    target_phone = data.get("target_phone") or data.get("to")
    peer_phone = data.get("phone")
    if peer_phone and not source_phone and direction == "inbound":
        source_phone = peer_phone
    if peer_phone and not target_phone and direction == "outbound":
        target_phone = peer_phone
    if not peer_phone:
        peer_phone = source_phone if direction == "inbound" else target_phone
    conversation_key = data.get("conversation_key") or normalize_whatsapp_phone(peer_phone)
    sent_at = parse_communication_timestamp(data.get("sent_at") or data.get("timestamp") or data.get("date_sent"))
    external_id = (str(data.get("external_message_id") or data.get("message_id") or data.get("sid") or "").strip() or None)
    if not external_id and source in ("sms", "whatsapp") and (message or peer_phone or sent_at):
        seed = f"{source}|{direction}|{source_phone or ''}|{target_phone or ''}|{sent_at.isoformat() if sent_at else ''}|{message}"
        external_id = "generated-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()

    client_id = data.get("client_id")
    client_match = None
    if client_id:
        cur.execute("SELECT id, display_name FROM clients WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL", (client_id, tenant_id))
        row = cur.fetchone()
        if row:
            client_match = dict(row)
        else:
            client_id = None
    if not client_id:
        client_match = find_client_by_communication_phone(cur, tenant_id, peer_phone)
        client_id = client_match["id"] if client_match else None

    subject = data.get("subject") or f"{source.upper()} {'od' if direction == 'inbound' else 'pro'} {peer_phone or ''}".strip()
    notes = data.get("notes")

    if external_id:
        cur.execute("""
            SELECT id FROM communications
            WHERE tenant_id=%s AND source=%s AND external_message_id=%s
            LIMIT 1
        """, (tenant_id, source, external_id))
        existing = cur.fetchone()
        if existing:
            cur.execute("""
                UPDATE communications
                SET client_id=COALESCE(%s, client_id),
                    job_id=COALESCE(%s, job_id),
                    comm_type=%s,
                    subject=%s,
                    message_summary=%s,
                    direction=%s,
                    notes=COALESCE(%s, notes),
                    sent_at=COALESCE(%s, sent_at),
                    source_phone=COALESCE(%s, source_phone),
                    target_phone=COALESCE(%s, target_phone),
                    conversation_key=COALESCE(%s, conversation_key),
                    imported_at=now()
                WHERE id=%s
                RETURNING id
            """, (client_id, data.get("job_id"), comm_type, subject, str(message)[:4000], direction,
                  notes, sent_at, source_phone, target_phone, conversation_key, existing["id"]))
            row = cur.fetchone()
            return {"id": row["id"], "created": False, "matched": bool(client_id)}

    cur.execute("""
        INSERT INTO communications (
            tenant_id, client_id, job_id, comm_type, source, external_message_id,
            source_phone, target_phone, conversation_key, subject, message_summary,
            direction, notes, sent_at, imported_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s, now()),now())
        RETURNING id
    """, (tenant_id, client_id, data.get("job_id"), comm_type, source, external_id,
          source_phone, target_phone, conversation_key, subject, str(message)[:4000],
          direction, notes, sent_at))
    row = cur.fetchone()
    return {"id": row["id"], "created": True, "matched": bool(client_id)}

def normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()

def build_contact_key(name: Optional[str], phone: Optional[str], email: Optional[str]) -> str:
    normalized_phone = normalize_contact_phone(phone)
    normalized_email = normalize_email(email)
    normalized_name = clean_contact_display_name(name).lower()
    return normalized_phone or normalized_email or normalized_name

def normalize_section_code(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    chars = []
    prev_sep = False
    for ch in raw:
        if ch.isalnum():
            chars.append(ch)
            prev_sep = False
        elif not prev_sep:
            chars.append("_")
            prev_sep = True
    return "".join(chars).strip("_")

def choose_preferred_value(*values):
    best = ""
    for value in values:
        candidate = (value or "").strip()
        if len(candidate) > len(best):
            best = candidate
    return best or None

def clean_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

def extract_contact_address_fields(data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    address_line1 = clean_optional_text(
        data.get("address_line1")
        or data.get("billing_address_line1")
        or data.get("street")
    )
    city = clean_optional_text(data.get("city") or data.get("billing_city"))
    postcode = clean_optional_text(data.get("postcode") or data.get("billing_postcode"))
    country = clean_optional_text(data.get("country") or data.get("billing_country"))
    formatted = clean_optional_text(data.get("address"))
    if not formatted:
        formatted = ", ".join(part for part in [address_line1, city, postcode, country] if part) or None
    return {
        "address": formatted,
        "address_line1": address_line1,
        "city": city,
        "postcode": postcode,
        "country": country,
    }

UK_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.IGNORECASE)
ADDRESS_LABEL_RE = re.compile(
    r"^\s*(?:address|addr|adresa|adres|billing address|client address|zakaznik|klient)\s*[:\-]\s*",
    re.IGNORECASE,
)
STREET_HINT_RE = re.compile(
    r"\b(street|st|road|rd|lane|ln|avenue|ave|drive|dr|close|cl|way|court|ct|"
    r"crescent|cres|place|pl|terrace|terr|gardens|gdns|green|grove|grv|park|"
    r"hill|row|mews|yard|house|flat|apartment|apt|unit)\b",
    re.IGNORECASE,
)
PHONE_IN_TEXT_RE = re.compile(r"(?:phone|tel|telephone|whatsapp|wa|from|to)\s*:?\s*(\+?\d[\d\s().-]{6,}\d)", re.IGNORECASE)

def normalize_postcode(value: Optional[str]) -> Optional[str]:
    match = UK_POSTCODE_RE.search(value or "")
    if not match:
        return None
    compact = re.sub(r"\s+", "", match.group(1).upper())
    if len(compact) <= 3:
        return compact
    return f"{compact[:-3]} {compact[-3:]}"

def clean_address_fragment(value: Optional[str]) -> str:
    text = (value or "").replace("\r", "\n")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b(?:phone|tel|telephone|whatsapp|wa)\s*:?\s*\+?\d[\d\s().-]{6,}\d\b", " ", text, flags=re.IGNORECASE)
    text = text.replace("\n", ", ")
    text = ADDRESS_LABEL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(,\s*){2,}", ", ", text)
    return text.strip(" ,.;")

def score_address_candidate(candidate: str) -> float:
    if not UK_POSTCODE_RE.search(candidate):
        return 0.0
    cleaned = clean_address_fragment(candidate)
    if len(cleaned) < 10 or len(cleaned) > 240:
        return 0.0
    score = 0.45
    if re.search(r"\b\d+[A-Z]?\b", cleaned, re.IGNORECASE):
        score += 0.18
    if STREET_HINT_RE.search(cleaned):
        score += 0.2
    if "," in cleaned:
        score += 0.08
    if ADDRESS_LABEL_RE.search(candidate):
        score += 0.08
    if re.search(r"\b(hours?|hodin|pytl|total|£|invoice|faktura)\b", cleaned, re.IGNORECASE):
        score -= 0.2
    return max(0.0, min(0.98, score))

def split_uk_address(raw_address: str, postcode: str) -> Dict[str, Optional[str]]:
    cleaned = clean_address_fragment(raw_address)
    cleaned = UK_POSTCODE_RE.sub("", cleaned).strip(" ,.;")
    parts = [part.strip(" ,.;") for part in cleaned.split(",") if part.strip(" ,.;")]
    city = None
    line1 = cleaned or None
    if len(parts) >= 2:
        city = parts[-1]
        line1 = ", ".join(parts[:-1]) or None
    elif len(parts) == 1:
        line1 = parts[0]
    return {
        "address": ", ".join(part for part in [line1, city, postcode, "GB"] if part),
        "address_line1": line1,
        "city": city,
        "postcode": postcode,
        "country": "GB",
    }

def extract_uk_address_from_text(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    raw_lines = [line.strip() for line in str(text).replace("\r", "\n").split("\n")]
    lines = [line for line in raw_lines if line]
    candidates: List[str] = []
    for index, line in enumerate(lines):
        if UK_POSTCODE_RE.search(line):
            candidates.append(line)
            for match in UK_POSTCODE_RE.finditer(line):
                start = max(0, match.start() - 140)
                end = min(len(line), match.end() + 80)
                candidates.append(line[start:end])
            if index > 0:
                candidates.append(f"{lines[index - 1]}, {line}")
            if index > 1:
                candidates.append(f"{lines[index - 2]}, {lines[index - 1]}, {line}")
    if not candidates:
        for chunk in re.split(r"[;\n]", str(text)):
            if UK_POSTCODE_RE.search(chunk):
                candidates.append(chunk)
    best = None
    best_score = 0.0
    for candidate in candidates:
        score = score_address_candidate(candidate)
        if score > best_score:
            best = candidate
            best_score = score
    if not best or best_score < 0.65:
        return None
    postcode = normalize_postcode(best)
    if not postcode:
        return None
    address = split_uk_address(best, postcode)
    if not address.get("address_line1"):
        return None
    address["raw"] = clean_address_fragment(best)
    address["confidence"] = round(best_score, 2)
    return address

def extract_whatsapp_phone_from_text(*parts: Optional[str]) -> Optional[str]:
    text = "\n".join(part for part in parts if part)
    match = PHONE_IN_TEXT_RE.search(text)
    return match.group(1) if match else None

def find_client_by_whatsapp_phone(cur, tenant_id: int, phone: Optional[str]) -> Optional[Dict[str, Any]]:
    normalized = normalize_whatsapp_phone(phone)
    if not normalized:
        return None
    cur.execute("""SELECT id, display_name, phone_primary, billing_address_line1, billing_city,
                          billing_postcode, billing_country
                   FROM clients
                   WHERE tenant_id=%s AND deleted_at IS NULL""", (tenant_id,))
    for row in cur.fetchall():
        client = dict(row)
        if normalize_whatsapp_phone(client.get("phone_primary")) == normalized:
            return client
    return None

def find_client_by_voice_name(cur, tenant_id: int, name: Optional[str]) -> Optional[Dict[str, Any]]:
    normalized_target = normalize_voice_name_key(name)
    if not normalized_target:
        return None
    target_tokens = [token for token in normalized_target.split() if token]
    cur.execute("""
        SELECT id, display_name, phone_primary, phone_secondary
        FROM clients
        WHERE tenant_id=%s AND deleted_at IS NULL
    """, (tenant_id,))
    ranked = []
    for row in cur.fetchall():
        client = dict(row)
        normalized_name = normalize_voice_name_key(client.get("display_name"))
        if not normalized_name:
            continue
        tokens = [token for token in normalized_name.split() if token]
        score = None
        if normalized_name == normalized_target:
            score = 0
        elif target_tokens and all(token in tokens for token in target_tokens):
            score = 10 + abs(len(tokens) - len(target_tokens))
        elif normalized_target in normalized_name or normalized_name in normalized_target:
            score = 20 + abs(len(normalized_name) - len(normalized_target))
        elif target_tokens:
            overlap = sum(1 for token in target_tokens if token in tokens)
            if overlap:
                score = 60 - (overlap * 5) + abs(len(tokens) - len(target_tokens))
        if score is None:
            continue
        ranked.append((
            score,
            1 if not (client.get("phone_primary") or client.get("phone_secondary")) else 0,
            abs(len(normalized_name) - len(normalized_target)),
            len(normalized_name),
            client,
        ))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:4])
    return ranked[0][4]

def client_info_score(row: Dict[str, Any]) -> int:
    keys = [
        "display_name", "first_name", "last_name", "company_name", "phone_primary",
        "email_primary", "billing_address_line1", "billing_city", "billing_postcode", "website"
    ]
    return sum(1 for key in keys if row.get(key))

def find_matching_clients(cur, tenant_id: int, normalized_phone: str, normalized_email: str):
    if not normalized_phone and not normalized_email:
        return []
    cur.execute("SELECT * FROM clients WHERE tenant_id=%s AND deleted_at IS NULL", (tenant_id,))
    rows = [dict(row) for row in cur.fetchall()]
    matches = []
    for row in rows:
        client_phone = normalize_contact_phone(row.get("phone_primary"))
        client_email = normalize_email(row.get("email_primary"))
        if (normalized_phone and client_phone == normalized_phone) or (normalized_email and client_email == normalized_email):
            matches.append(row)
    return matches

def load_selected_contact_rows(cur, tenant_id: int, normalized_phone: str, normalized_email: str, linked_client_id: Optional[int] = None):
    clauses = []
    params: List[Any] = [tenant_id]
    if linked_client_id:
        clauses.append("linked_client_id = %s")
        params.append(linked_client_id)
    if normalized_phone:
        clauses.append("normalized_phone = %s")
        params.append(normalized_phone)
    if normalized_email:
        clauses.append("normalized_email = %s")
        params.append(normalized_email)
    if not clauses:
        return []
    cur.execute(f"""SELECT * FROM user_contact_sync
        WHERE tenant_id=%s AND is_client=TRUE AND ({' OR '.join(clauses)})""", params)
    return [dict(row) for row in cur.fetchall()]

def merge_contact_rows_into_client(conn, tenant_id: int, primary_client_id: int, selected_rows: List[Dict[str, Any]], existing_client: Optional[Dict[str, Any]] = None):
    if not selected_rows:
        return
    with conn.cursor() as cur:
        if existing_client is None:
            cur.execute("SELECT * FROM clients WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL", (primary_client_id, tenant_id))
            existing_client = dict(cur.fetchone() or {})
        names = [clean_contact_display_name(row.get("display_name")) for row in selected_rows]
        phones = [row.get("phone_primary") for row in selected_rows]
        emails = [row.get("email_primary") for row in selected_rows]
        address_lines = [row.get("address_line1") or row.get("address") for row in selected_rows]
        cities = [row.get("city") for row in selected_rows]
        postcodes = [row.get("postcode") for row in selected_rows]
        countries = [row.get("country") for row in selected_rows]
        display_name = choose_preferred_value(clean_contact_display_name(existing_client.get("display_name")), *names)
        phone_primary = choose_preferred_value(existing_client.get("phone_primary"), *phones)
        email_primary = choose_preferred_value(existing_client.get("email_primary"), *emails)
        billing_address_line1 = choose_preferred_value(*address_lines) or existing_client.get("billing_address_line1")
        billing_city = choose_preferred_value(*cities) or existing_client.get("billing_city")
        billing_postcode = choose_preferred_value(*postcodes) or existing_client.get("billing_postcode")
        billing_country = choose_preferred_value(*countries) or existing_client.get("billing_country") or "GB"
        cur.execute("""UPDATE clients
            SET display_name=%s,
                phone_primary=%s,
                email_primary=%s,
                billing_address_line1=%s,
                billing_city=%s,
                billing_postcode=%s,
                billing_country=%s,
                source=COALESCE(source, 'synced_contact'),
                updated_at=now()
            WHERE id=%s AND tenant_id=%s""",
            (
                display_name,
                phone_primary,
                email_primary,
                billing_address_line1,
                billing_city,
                billing_postcode,
                billing_country,
                primary_client_id,
                tenant_id,
            ))

def reconcile_contact_selection(conn, tenant_id: int, user_id: int, contact_key: str):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM user_contact_sync
            WHERE tenant_id=%s AND user_id=%s AND contact_key=%s""", (tenant_id, user_id, contact_key))
        row = cur.fetchone()
        if not row:
            return None
        row = dict(row)
        linked_client_id = row.get("linked_client_id")
        normalized_phone = row.get("normalized_phone") or ""
        normalized_email = row.get("normalized_email") or ""
        if not row.get("is_client"):
            if linked_client_id:
                cur.execute("""SELECT COUNT(*) AS c FROM user_contact_sync
                    WHERE tenant_id=%s AND linked_client_id=%s AND is_client=TRUE""", (tenant_id, linked_client_id))
                selected_count = int((cur.fetchone() or {}).get("c") or 0)
                if selected_count == 0:
                    cur.execute("SELECT source FROM clients WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL", (linked_client_id, tenant_id))
                    client = cur.fetchone()
                    if client and (client.get("source") or "") == "synced_contact":
                        cur.execute("UPDATE clients SET deleted_at=now(), status='archived', updated_at=now() WHERE id=%s AND tenant_id=%s", (linked_client_id, tenant_id))
                cur.execute("""UPDATE user_contact_sync
                    SET linked_client_id=NULL, updated_at=now()
                    WHERE tenant_id=%s AND user_id=%s AND contact_key=%s""", (tenant_id, user_id, contact_key))
            return None

        selected_rows = load_selected_contact_rows(cur, tenant_id, normalized_phone, normalized_email, linked_client_id)
        matches = find_matching_clients(cur, tenant_id, normalized_phone, normalized_email)
        primary = max(matches, key=client_info_score) if matches else None
        if primary is None:
            code_seed = normalized_phone[-6:] if normalized_phone else uuid.uuid4().hex[:6].upper()
            code = f"CL-SYNC-{code_seed.upper()}"
            address = extract_contact_address_fields(row)
            cur.execute("""INSERT INTO clients (
                    tenant_id, client_code, client_type, display_name, phone_primary, email_primary,
                    billing_address_line1, billing_city, billing_postcode, billing_country, status, source
                )
                VALUES (%s,%s,'individual',%s,%s,%s,%s,%s,%s,%s,'active','synced_contact')
                RETURNING *""",
                (
                    tenant_id,
                    code,
                    clean_contact_display_name(row.get("display_name")) or "Client",
                    row.get("phone_primary"),
                    row.get("email_primary"),
                    address.get("address_line1") or address.get("address"),
                    address.get("city"),
                    address.get("postcode"),
                    address.get("country") or "GB",
                ))
            primary = dict(cur.fetchone())
        merge_contact_rows_into_client(conn, tenant_id, primary["id"], selected_rows, primary)
        for duplicate in matches:
            if duplicate["id"] == primary["id"]:
                continue
            if (duplicate.get("source") or "") == "synced_contact":
                cur.execute("""UPDATE clients
                    SET deleted_at=now(), status='archived', updated_at=now()
                    WHERE id=%s AND tenant_id=%s""", (duplicate["id"], tenant_id))
        cur.execute("""UPDATE user_contact_sync
            SET linked_client_id=%s, updated_at=now()
            WHERE tenant_id=%s AND is_client=TRUE
              AND (linked_client_id=%s OR normalized_phone=%s OR normalized_email=%s)""",
            (primary["id"], tenant_id, primary["id"], normalized_phone or None, normalized_email or None))
        return primary["id"]

def ensure_contact_section(cur, tenant_id: int, section_code: str):
    cur.execute("""SELECT section_code, display_name
        FROM contact_sections
        WHERE tenant_id=%s AND section_code=%s AND is_active=TRUE""", (tenant_id, section_code))
    section = cur.fetchone()
    if not section:
        raise HTTPException(404, "Contact section not found")
    return dict(section)

def find_shared_contact(cur, tenant_id: int, normalized_phone: str, normalized_email: str):
    if not normalized_phone and not normalized_email:
        return None
    clauses = []
    params: List[Any] = [tenant_id]
    if normalized_phone:
        clauses.append("normalized_phone=%s")
        params.append(normalized_phone)
    if normalized_email:
        clauses.append("normalized_email=%s")
        params.append(normalized_email)
    cur.execute(f"""SELECT *
        FROM shared_contacts
        WHERE tenant_id=%s AND deleted_at IS NULL AND ({' OR '.join(clauses)})
        ORDER BY updated_at DESC LIMIT 1""", params)
    row = cur.fetchone()
    return dict(row) if row else None

def merge_shared_contact(cur, tenant_id: int, user_id: Optional[int], data: Dict[str, Any], source: str = "manual"):
    section_code = normalize_section_code(data.get("section_code"))
    if not section_code:
        raise HTTPException(400, "section_code required")
    ensure_contact_section(cur, tenant_id, section_code)
    display_name = clean_contact_display_name(data.get("display_name") or data.get("name"))
    if not display_name:
        raise HTTPException(400, "display_name required")
    company_name = (data.get("company_name") or "").strip() or None
    phone_primary = (data.get("phone_primary") or data.get("phone") or "").strip() or None
    email_primary = (data.get("email_primary") or data.get("email") or "").strip() or None
    address_fields = extract_contact_address_fields(data)
    notes = (data.get("notes") or "").strip() or None
    normalized_phone = normalize_phone(phone_primary)
    normalized_email = normalize_email(email_primary)
    existing = find_shared_contact(cur, tenant_id, normalized_phone, normalized_email)
    if existing:
        cur.execute("""UPDATE shared_contacts
            SET section_code=%s,
                display_name=%s,
                company_name=%s,
                phone_primary=%s,
                email_primary=%s,
                address=%s,
                address_line1=%s,
                city=%s,
                postcode=%s,
                country=%s,
                notes=%s,
                source=%s,
                normalized_phone=%s,
                normalized_email=%s,
                updated_by=%s,
                updated_at=now()
            WHERE id=%s AND tenant_id=%s
            RETURNING *""",
            (
                section_code,
                choose_preferred_value(clean_contact_display_name(existing.get("display_name")), display_name),
                choose_preferred_value(existing.get("company_name"), company_name),
                choose_preferred_value(existing.get("phone_primary"), phone_primary),
                choose_preferred_value(existing.get("email_primary"), email_primary),
                address_fields.get("address") or existing.get("address"),
                address_fields.get("address_line1") or existing.get("address_line1"),
                address_fields.get("city") or existing.get("city"),
                address_fields.get("postcode") or existing.get("postcode"),
                address_fields.get("country") or existing.get("country"),
                choose_preferred_value(existing.get("notes"), notes),
                existing.get("source") or source,
                normalized_phone or existing.get("normalized_phone"),
                normalized_email or existing.get("normalized_email"),
                user_id,
                existing["id"],
                tenant_id,
            ))
        return dict(cur.fetchone()), False
    cur.execute("""INSERT INTO shared_contacts
        (tenant_id, section_code, display_name, company_name, phone_primary, email_primary,
         address, address_line1, city, postcode, country, notes, source,
         normalized_phone, normalized_email, created_by, updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *""",
        (
            tenant_id,
            section_code,
            display_name,
            company_name,
            phone_primary,
            email_primary,
            address_fields.get("address"),
            address_fields.get("address_line1"),
            address_fields.get("city"),
            address_fields.get("postcode"),
            address_fields.get("country"),
            notes,
            source,
            normalized_phone or None,
            normalized_email or None,
            user_id,
            user_id,
        ))
    return dict(cur.fetchone()), True

def run_startup_bootstrap():
    init_pool()
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('crm.clients')")
            res = cur.fetchone()
            if res is None or res.get('to_regclass') is None:
                schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
                if os.path.exists(schema_path):
                    with open(schema_path, "r", encoding="utf-8") as f: cur.execute(f.read())
                    conn.commit(); print("Schema initialized from schema.sql")
        release_conn(conn)
    except Exception as e: print(f"Schema check: {e}")

def ensure_contact_address_schema():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            for table in ("user_contact_sync", "shared_contacts"):
                for column in ("address", "address_line1", "city", "postcode", "country"):
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} TEXT")
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Contact address schema check: {e}")
    finally:
        release_conn(conn)

def close_db_pool():
    if db_pool: db_pool.closeall()

@asynccontextmanager
async def app_lifespan(_: FastAPI):
    run_startup_bootstrap()
    ensure_contact_address_schema()
    ensure_quote_items_table()
    ensure_hierarchy_workflow_schema()
    print(
        "Startup routes: "
        f"admin_activity_log={any(getattr(route, 'path', None) == '/admin/activity-log' for route in app.routes)} "
        f"nature_services_status={any(getattr(route, 'path', None) == '/nature/services/status' for route in app.routes)}"
    )
    yield
    close_db_pool()

app.router.lifespan_context = app_lifespan

# === MODELS ===
class ChatMessage(BaseModel):
    role: str; content: str

class MessageRequest(BaseModel):
    text: str; history: List[ChatMessage] = []
    context_entity_id: Optional[int] = None; context_type: Optional[str] = None
    calendar_context: Optional[str] = None; current_datetime: Optional[str] = None
    internal_language: Optional[str] = None; external_language: Optional[str] = None

# === AI PROCESS ===
@app.post("/process")
async def process_message(msg: MessageRequest, request: Request):
    try:
        now = msg.current_datetime or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        audit_request_event(
            request,
            action="assistant_query",
            description=msg.text,
            entity_type="assistant",
            entity_id=msg.context_entity_id or "general",
            details={
                "context_type": msg.context_type,
                "context_entity_id": msg.context_entity_id,
                "internal_language": msg.internal_language,
                "external_language": msg.external_language,
            },
            source_channel="text",
        )
        entity_ctx = ""
        if msg.context_entity_id and msg.context_type == "client":
            conn = get_db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT display_name,email_primary,phone_primary FROM clients WHERE id=%s", (msg.context_entity_id,))
                    r = cur.fetchone()
                    if r: entity_ctx = f"Marek se diva na klienta: {r['display_name']}"
            finally: release_conn(conn)

        tenant_id = get_request_tenant_id(request)
        request_user = getattr(request.state, "user", {}) or {}
        current_user_id = request_user.get("user_id")

        # Language from tenant config + request override
        tenant_conn = get_db_conn()
        try:
            tenant_config = get_tenant_config(tenant_conn, tenant_id)
        finally:
            release_conn(tenant_conn)
        lang = resolve_response_language(tenant_config, msg.internal_language)
        if lang == "cs":
            lang_instruction = "JAZYK: Odpovídej VÝHRADNĚ česky. Celá tvoje odpověď musí být v češtině. Nikdy nepřepínej do jiného jazyka. Uživatel může psát česky, anglicky nebo polsky — ty VŽDY odpovídáš POUZE česky."
        elif lang == "en":
            lang_instruction = "LANGUAGE: You MUST respond EXCLUSIVELY in English. Your entire response must be in English. Never switch to another language. The user may write in Czech, English or Polish — you ALWAYS respond ONLY in English."
        elif lang == "pl":
            lang_instruction = "JĘZYK: Odpowiadaj WYŁĄCZNIE po polsku. Cała twoja odpowiedź musi być po polsku. Nigdy nie przełączaj się na inny język. Użytkownik może pisać po czesku, angielsku lub polsku — ty ZAWSZE odpowiadasz TYLKO po polsku."
        else:
            lang_instruction = "LANGUAGE: Respond in English only."

        def tr(en: str, cs: str, pl: str) -> str:
            return cs if lang == "cs" else en if lang == "en" else pl if lang == "pl" else en

        def error_reply(e: Exception, prefix_en: str = "Error", prefix_cs: str = "Chyba", prefix_pl: str = "Błąd"):
            return {"reply_cs": f"{tr(prefix_en, prefix_cs, prefix_pl)}: {e}"}

        memory_command = extract_assistant_memory_command(msg.text)
        if memory_command:
            memory_action, memory_text, memory_type = memory_command
            memory_conn = get_db_conn()
            try:
                if memory_action == "remember":
                    remembered = remember_assistant_memory(memory_conn, tenant_id, current_user_id, memory_text, memory_type)
                    memory_conn.commit()
                    reply = tr(
                        f"I will remember: {memory_text}",
                        f"Zapamatovala jsem si: {memory_text}",
                        f"Zapamiętałam: {memory_text}",
                    )
                    return {"reply_cs": reply, "action_type": "MEMORY_REMEMBERED", "action_data": remembered}
                forgotten = forget_assistant_memory(memory_conn, tenant_id, current_user_id, memory_text)
                memory_conn.commit()
                if forgotten["count"] > 0:
                    reply = tr(
                        f"I forgot {forgotten['count']} matching memory item(s).",
                        f"Zapomněla jsem {forgotten['count']} odpovídající položek paměti.",
                        f"Zapomniałam {forgotten['count']} pasujących pozycji pamięci.",
                    )
                else:
                    reply = tr(
                        "I did not find a matching memory item to forget.",
                        "Nenašla jsem odpovídající položku paměti ke smazání.",
                        "Nie znalazłam pasującej pozycji pamięci do usunięcia.",
                    )
                return {"reply_cs": reply, "action_type": "MEMORY_FORGOTTEN", "action_data": forgotten}
            except Exception as e:
                memory_conn.rollback()
                return error_reply(e)
            finally:
                release_conn(memory_conn)

        if not ai_client:
            return {"reply_cs": tr("AI is not configured.", "AI neni nakonfigurovana.", "AI nie jest skonfigurowane.")}

        memory_ctx = "None."
        memory_conn = get_db_conn()
        try:
            memories = load_assistant_memories(memory_conn, tenant_id, current_user_id)
            # Also load recent session summaries (last 5)
            session_memories = []
            try:
                cur_mem = memory_conn.cursor()
                cur_mem.execute(
                    """SELECT content, created_at FROM crm.assistant_memory
                       WHERE tenant_id = %s AND user_id IS NOT DISTINCT FROM %s
                         AND memory_type = 'session' AND is_active = TRUE
                       ORDER BY created_at DESC LIMIT 5""",
                    (tenant_id, current_user_id)
                )
                session_memories = cur_mem.fetchall()
            except Exception:
                pass
            parts = []
            if memories:
                parts.append("Remembered facts:\n" + "\n".join(f"- {item['content']}" for item in memories))
            if session_memories:
                parts.append("Recent conversations:\n" + "\n".join(
                    f"- [{row[1].strftime('%Y-%m-%d %H:%M') if hasattr(row[1], 'strftime') else str(row[1])[:16]}] {row[0]}"
                    for row in session_memories
                ))
            if parts:
                memory_ctx = "\n\n".join(parts)
        except Exception as e:
            print(f"Memory load warning: {e}")
        finally:
            release_conn(memory_conn)

        system_prompt = f"""You are an intelligent VOICE secretary of DesignLeaf company (landscaping services, Oxfordshire UK).
{lang_instruction}
TIME: {now}. CONTEXT: {entity_ctx or 'None.'}
CALENDAR: {msg.calendar_context or 'None.'}
MEMORY:
{memory_ctx}
RULES:
- You are a VOICE assistant. The user speaks to you and you speak back. NEVER say you can only communicate via text. NEVER say you are a text-based AI. You ARE a voice assistant.
- Be concise, human, friendly. Remember conversation history.
- NEVER say 'executing...' or 'performing...' — always respond naturally describing what you did.
- To create a task use create_task. To change status, planning or assignment use update_task. To complete use complete_task.
- To list tasks use list_tasks.
- For jobs: create_job for new, update_job for status changes, planning and handover.
- For notes: add_note with entity_type 'client' or 'job'.
- For leads: create_lead.
- For calendar: list_calendar_events, add/modify/delete_calendar_event.
- For contacts: search_contacts, call_contact.
- For navigation, maps, route, directions, "navigace", "naviguj", "spust mapy", or "nawigacja", use start_navigation. Do not give instructions; return the action so Android opens maps directly.
- When the user says 'zapamatuj si', 'pamatuj si', 'remember', or 'zapamiętaj', save the fact with remember_memory.
- When the user says 'zapomeň', 'forget', or 'zapomnij', remove matching facts with forget_memory.
- When user asks 'what do I have to do' or 'my tasks', use list_tasks.
- When user says 'done' or 'completed' for a task, use complete_task.
- When user says 'work report', 'log work', 'enter hours', 'report work', use start_work_report.
- When user asks about weather, forecast, rain, temperature, wind, or whether to work outside, use get_weather.
- When user says 'napis na whatsapp', 'posli whatsapp', 'whatsapp message', use send_whatsapp. Pass the contact plus the message content naturally in the user's current/internal language; the server resolves the contact and translates the outgoing message to the customer's configured language before Android opens WhatsApp.
- For email inbox: use search_email to find messages, read_email to read a message or latest matching message aloud, and reply_email to answer an existing email thread.
- For new outbound email use send_email. For replies to an existing incoming email always use reply_email so the reply goes to the original sender.
- When user asks about clients database, how many clients, client statistics, sources, types, or any question about CRM data, use query_clients. Examples: 'kolik mam klientu', 'odkud jsou klienti', 'jaci klienti jsou aktivni', 'kolik mam zakazek', 'statistiky', 'prehled databaze'."""

        tools = [
            {"type":"function","function":{"name":"add_calendar_event","description":"Prida schuzku do kalendare","parameters":{"type":"object","properties":{"title":{"type":"string"},"start_time":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"duration":{"type":"integer","description":"minuty"}},"required":["title","start_time"]}}},
            {"type":"function","function":{"name":"modify_calendar_event","description":"Zmeni existujici udalost","parameters":{"type":"object","properties":{"event_title":{"type":"string"},"new_title":{"type":"string"},"new_start_time":{"type":"string"}},"required":["event_title"]}}},
            {"type":"function","function":{"name":"delete_calendar_event","description":"Smaze udalost","parameters":{"type":"object","properties":{"event_title":{"type":"string"}},"required":["event_title"]}}},
            {"type":"function","function":{"name":"list_calendar_events","description":"Precte kalendar na N dni","parameters":{"type":"object","properties":{"days":{"type":"integer","default":7}}}}},
            {"type":"function","function":{"name":"search_contacts","description":"Hleda v CRM klientech i telefonnich kontaktech","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
            {"type":"function","function":{"name":"call_contact","description":"Vytoci telefonni kontakt. Pokud neni zname cislo, vypln contact_name a Android kontakt dohleda v mobilu/CRM.","parameters":{"type":"object","properties":{"phone":{"type":"string"},"contact_name":{"type":"string"},"client_name":{"type":"string"}}}}},
            {"type":"function","function":{"name":"start_navigation","description":"Otevre navigaci/mapy v Android telefonu na kontakt, klienta nebo adresu. Pouzij pro prikazy typu naviguj, spust navigaci, spust mapy, route, directions, nawigacja.","parameters":{"type":"object","properties":{"target":{"type":"string","description":"Jmeno kontaktu/klienta nebo adresa"},"address":{"type":"string","description":"Adresa, pokud ji uz znas"},"client_name":{"type":"string","description":"Jmeno CRM klienta"},"contact_name":{"type":"string","description":"Jmeno telefonniho kontaktu"}}}}},
            {"type":"function","function":{"name":"send_email","description":"Posle email","parameters":{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["to","subject","body"]}}},
            {"type":"function","function":{"name":"search_email","description":"Vyhleda emaily ve schrankce podle textu, odesilatele nebo neprectenych zprav.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Text hledany v predmetu, odesilateli nebo tele emailu"},"sender":{"type":"string","description":"Jmeno nebo email odesilatele"},"unread_only":{"type":"boolean","default":False},"limit":{"type":"integer","default":5}}}}},
            {"type":"function","function":{"name":"read_email","description":"Precte posledni nebo konkretni email. Pouzij pro 'precti email', 'otevri email', 'precti posledni email od ...'.","parameters":{"type":"object","properties":{"uid":{"type":"string","description":"UID emailu z vysledku search_email"},"query":{"type":"string","description":"Text pro nalezeni emailu, pokud UID neni znamy"},"sender":{"type":"string","description":"Jmeno nebo email odesilatele"},"unread_only":{"type":"boolean","default":False}}}}},
            {"type":"function","function":{"name":"reply_email","description":"Odpovi na existujici email. Najde email podle UID nebo dotazu a posle odpoved puvodnimu odesilateli pres SMTP.","parameters":{"type":"object","properties":{"uid":{"type":"string","description":"UID emailu z vysledku search_email/read_email"},"query":{"type":"string","description":"Text pro nalezeni emailu, pokud UID neni znamy"},"sender":{"type":"string","description":"Jmeno nebo email odesilatele"},"body":{"type":"string","description":"Text odpovedi"}},"required":["body"]}}},
            {"type":"function","function":{"name":"create_client","description":"Vytvori noveho klienta v CRM","parameters":{"type":"object","properties":{"name":{"type":"string"},"email":{"type":"string"},"phone":{"type":"string"}},"required":["name"]}}},
            {"type":"function","function":{"name":"create_task","description":"Vytvori ukol. Pouzij pro: zavolat, email, schuzka, objednavka, kalkulace, kontrola, pripomenuti.","parameters":{"type":"object","properties":{"title":{"type":"string"},"description":{"type":"string"},"task_type":{"type":"string","enum":["volat","email","schuzka","objednat_material","vytvorit_kalkulaci","poslat_kalkulaci","navsteva_klienta","zamereni","realizace","kontrola","reklamace","pripomenout_se","interni_poznamka","fotodokumentace"]},"priority":{"type":"string","enum":["nizka","bezna","vysoka","urgentni","kriticka"]},"deadline":{"type":"string"},"planned_date":{"type":"string"},"planned_start_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"planned_end_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"assigned_to":{"type":"string"},"planning_note":{"type":"string"},"client_name":{"type":"string"}},"required":["title"]}}},
            {"type":"function","function":{"name":"create_job","description":"Vytvori novou zakazku","parameters":{"type":"object","properties":{"title":{"type":"string"},"client_name":{"type":"string"},"description":{"type":"string"},"start_date":{"type":"string"},"planned_start_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"planned_end_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"assigned_to":{"type":"string"},"handover_note":{"type":"string"}},"required":["title"]}}},
            {"type":"function","function":{"name":"add_note","description":"Prida poznamku ke klientovi nebo zakazce","parameters":{"type":"object","properties":{"entity_type":{"type":"string","enum":["client","job"]},"entity_name":{"type":"string"},"note":{"type":"string"}},"required":["entity_type","note"]}}},
            {"type":"function","function":{"name":"create_lead","description":"Vytvori novy lead/poptavku","parameters":{"type":"object","properties":{"name":{"type":"string"},"source":{"type":"string","enum":["checkatrade","web","telefon","doporuceni","jiny"]},"note":{"type":"string"}},"required":["name","source"]}}},
            {"type":"function","function":{"name":"update_task","description":"Zmeni stav, prioritu, vysledek, plan nebo prirazeni ukolu","parameters":{"type":"object","properties":{"title":{"type":"string","description":"Nazev ukolu k nalezeni"},"status":{"type":"string","enum":["novy","naplanovany","v_reseni","ceka_na_klienta","ceka_na_material","ceka_na_platbu","hotovo","zruseno","predano_dal"]},"priority":{"type":"string","enum":["nizka","bezna","vysoka","urgentni","kriticka"]},"result":{"type":"string","description":"Vysledek ukolu"},"assigned_to":{"type":"string"},"planned_date":{"type":"string"},"planned_start_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"planned_end_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"planning_note":{"type":"string"}},"required":["title"]}}},
            {"type":"function","function":{"name":"update_job","description":"Zmeni stav, plan nebo predani zakazky","parameters":{"type":"object","properties":{"title":{"type":"string","description":"Nazev zakazky"},"status":{"type":"string","enum":["nova","v_reseni","ceka_na_klienta","ceka_na_material","naplanovano","v_realizaci","dokonceno","vyfakturovano","uzavreno","pozastaveno","zruseno"]},"assigned_to":{"type":"string"},"planned_start_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"planned_end_at":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"handover_note":{"type":"string"}},"required":["title"]}}},
            {"type":"function","function":{"name":"list_tasks","description":"Vypise ukoly podle filtru","parameters":{"type":"object","properties":{"status":{"type":"string"},"client_name":{"type":"string"},"only_active":{"type":"boolean"}}}}},
            {"type":"function","function":{"name":"complete_task","description":"Dokonci ukol a zapise vysledek","parameters":{"type":"object","properties":{"title":{"type":"string"},"result":{"type":"string","description":"Co bylo udelano"}},"required":["title"]}}},
            {"type":"function","function":{"name":"start_work_report","description":"Spusti hlasovy work report dialog. Pouzij kdyz Marek rekne ze chce zadat praci, work report, zapsat hodiny, nahlasit co delali.","parameters":{"type":"object","properties":{}}}},
            {"type":"function","function":{"name":"get_weather","description":"Zjisti predpoved pocasi. Pouzij kdyz se uzivatel pta na pocasi, teplotu, dest, vitr. Muze se ptat na dnes, zitra, nebo na konkretni den.","parameters":{"type":"object","properties":{"location":{"type":"string","description":"Nazev mesta nebo GPS souradnice. Default: Didcot, Oxfordshire"},"days":{"type":"integer","description":"Pocet dni predpovedi (1-7)","default":3}}}}},
            {"type":"function","function":{"name":"send_whatsapp","description":"Otevre WhatsApp v mobilu s predvyplnenou zpravou pro kontakt nebo klienta. Message ma byt prirozeny obsah v internim jazyce uzivatele; server ji prelozi do nastaveneho jazyka komunikace se zakaznikem.","parameters":{"type":"object","properties":{"client_name":{"type":"string","description":"Jmeno klienta nebo kontaktu"},"contact_name":{"type":"string","description":"Jmeno kontaktu, pokud nejde o CRM klienta"},"phone":{"type":"string","description":"Telefonni cislo, pokud je zname"},"message":{"type":"string","description":"Text zpravy k odeslani"}},"required":["message"]}}},
            {"type":"function","function":{"name":"remember_memory","description":"Ulozi dlouhodobou nebo strednedobou pamet asistenta. Pouzij pro 'zapamatuj si ...', 'pamatuj si ...', 'remember ...'.","parameters":{"type":"object","properties":{"content":{"type":"string","description":"Presny obsah k zapamatovani"},"memory_type":{"type":"string","enum":["medium","long"],"default":"long"}},"required":["content"]}}},
            {"type":"function","function":{"name":"forget_memory","description":"Smaze odpovidajici polozky pameti asistenta. Pouzij pro 'zapomen ...', 'forget ...'.","parameters":{"type":"object","properties":{"query":{"type":"string","description":"Co se ma zapomenout"}},"required":["query"]}}},
            {"type":"function","function":{"name":"query_clients","description":"Dotaz na databazi klientu. Pouzij kdyz se uzivatel pta kolik ma klientu, odkud jsou, jake typy, statistiky CRM, prehled databaze, aktivni/neaktivni klienti, zdroje klientu.","parameters":{"type":"object","properties":{"question":{"type":"string","description":"Co chce uzivatel vedet o klientech/databazi. Napr: 'kolik klientu', 'statistiky', 'odkud jsou klienti', 'aktivni klienti', 'posledni klienti'"}},"required":["question"]}}},
        ]

        messages = [{"role":"system","content":system_prompt}]
        for h in msg.history[-30:]: messages.append({"role":h.role,"content":h.content})
        if not msg.history or msg.history[-1].content != msg.text:
            messages.append({"role":"user","content":msg.text})

        response = ai_client.chat.completions.create(model="gpt-4o", messages=messages, tools=tools)
        ai_msg = response.choices[0].message

        if ai_msg.tool_calls:
            call = ai_msg.tool_calls[0]
            args = json.loads(call.function.arguments)
            action = call.function.name.upper()
            print(f"  TOOL: {call.function.name} -> {args}")

            # === SERVER-SIDE ACTIONS (execute on server, return result) ===
            if action == "CREATE_CLIENT":
                conn = get_db_conn()
                try:
                    tenant_id = get_request_tenant_id(request)
                    actor_user_id = request.state.user.get("user_id")
                    actor_name = get_user_display_name(conn, tenant_id, actor_user_id) or "system"
                    owner = get_active_user_row(conn, tenant_id, actor_user_id) or get_default_hierarchy_user(conn, tenant_id)
                    if not owner:
                        return error_reply("No active owner is available for the new client.")
                    code = f"CL-{uuid.uuid4().hex[:6].upper()}"
                    with conn.cursor() as cur:
                        client_display_name = clean_contact_display_name(args["name"])
                        cur.execute("""INSERT INTO clients (
                                client_code,client_type,display_name,email_primary,phone_primary,status,tenant_id,owner_user_id,hierarchy_status
                            ) VALUES (%s,%s,%s,%s,%s,'active',%s,%s,'pending') RETURNING id,display_name""",
                            (code,"domestic",client_display_name,args.get("email"),args.get("phone"),tenant_id,int(owner["id"])))
                        client_row = dict(cur.fetchone())
                        cid = client_row['id']
                        first_action = create_workflow_task(
                            conn,
                            tenant_id,
                            {
                                "title": tr("Follow up with new client", "Navázat další krok s novým klientem", "Wykonać kolejny krok z nowym klientem"),
                                "description": args.get("note") or args.get("description"),
                                "assigned_user_id": int(owner["id"]),
                                "planned_start_at": next_business_day_at_nine().isoformat(),
                                "priority": "vysoka",
                                "planning_note": "Systémově vytvořený první krok z AI flow.",
                            },
                            actor_name=actor_name,
                            default_client_id=cid,
                            default_client_name=client_row.get("display_name") or client_display_name,
                            source="assistant_client_create",
                        )
                        set_client_next_action(conn, tenant_id, cid, str(first_action["id"]))
                        log_activity(
                            conn,
                            "client",
                            cid,
                            "create",
                            f"Klient {args['name']} vytvoren",
                            tenant_id=tenant_id,
                            user_id=actor_user_id,
                            source_channel="assistant",
                            details={"owner_user_id": int(owner["id"]), "next_action_task_id": str(first_action["id"])},
                        )
                        conn.commit()
                    return {"reply_cs": tr(
                        f"Client {args['name']} ({code}) is now in CRM.",
                        f"Klient {args['name']} ({code}) je v CRM.",
                        f"Klient {args['name']} ({code}) jest już w CRM."
                    ),"action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            if action == "SEARCH_CONTACTS":
                q = args.get("query","")
                crm = []
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        s = f"%{q}%"
                        cur.execute("SELECT id,client_code,display_name,email_primary,phone_primary FROM clients WHERE deleted_at IS NULL AND (display_name ILIKE %s OR email_primary ILIKE %s OR phone_primary ILIKE %s) LIMIT 10",(s,s,s))
                        crm = [dict(r) for r in cur.fetchall()]
                finally: release_conn(conn)
                return {"reply_cs":ai_msg.content or tr(
                    f"Searching for '{q}'...",
                    f"Hledam '{q}'...",
                    f"Szukam '{q}'..."
                ),"action_type":"SEARCH_CONTACTS","action_data":{"query":q,"crm_results":crm},"is_question":True}

            if action == "CREATE_TASK":
                t = args.get("title","Ukol")
                conn = get_db_conn()
                try:
                    tenant_id = get_request_tenant_id(request)
                    actor_user_id = request.state.user.get("user_id")
                    actor_name = get_user_display_name(conn, tenant_id, actor_user_id) or "system"
                    default_owner = get_active_user_row(conn, tenant_id, actor_user_id) or get_default_hierarchy_user(conn, tenant_id)
                    task_payload = dict(args)
                    task_payload.setdefault("assigned_user_id", default_owner["id"] if default_owner else None)
                    task_payload.setdefault("planned_start_at", next_business_day_at_nine().isoformat())
                    task_payload.setdefault("priority", "bezna")
                    task = create_workflow_task(
                        conn,
                        tenant_id,
                        task_payload,
                        actor_name=actor_name,
                        default_client_name=args.get("client_name"),
                        source="hlasovy_prikaz",
                    )
                    log_activity(
                        conn,
                        "task",
                        task["id"],
                        "create",
                        f"Ukol '{t}' vytvoren",
                        tenant_id=tenant_id,
                        user_id=actor_user_id,
                        source_channel="assistant",
                    )
                    conn.commit()
                    return {"reply_cs":tr(
                        f"I created a task: {t}.",
                        f"Vytvořila jsem úkol: {t}.",
                        f"Utworzyłam zadanie: {t}."
                    ),"action_type":"CREATE_TASK","action_data":task}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            if action == "CREATE_JOB":
                t = args.get("title","Zakazka")
                conn = get_db_conn()
                try:
                    code = f"JOB-{uuid.uuid4().hex[:6].upper()}"
                    tenant_id = get_request_tenant_id(request)
                    actor_user_id = request.state.user.get("user_id")
                    actor_name = get_user_display_name(conn, tenant_id, actor_user_id) or "system"
                    cname = args.get("client_name","")
                    cid = None
                    assigned_user_id, assigned_to = resolve_assigned_user(conn, tenant_id, None, args.get("assigned_to"))
                    owner = get_active_user_row(conn, tenant_id, assigned_user_id) or get_active_user_row(conn, tenant_id, actor_user_id) or get_default_hierarchy_user(conn, tenant_id)
                    if not owner:
                        return error_reply("No active owner is available for the new job.")
                    planning_start, planning_end = planning_window_from_values(
                        args.get("planned_start_at"), args.get("planned_end_at"), args.get("start_date")
                    )
                    with conn.cursor() as cur:
                        if cname:
                            cur.execute("SELECT id FROM clients WHERE tenant_id=%s AND display_name ILIKE %s AND deleted_at IS NULL LIMIT 1",(tenant_id, f"%{cname}%",))
                            row = cur.fetchone()
                            if row: cid = row['id']
                        cur.execute("""INSERT INTO jobs (
                                tenant_id,job_number,client_id,job_title,job_status,start_date_planned,
                                planned_start_at,planned_end_at,assigned_user_id,assigned_to,next_action_task_id,hierarchy_status,handover_note,
                                handed_over_by,handed_over_at,calendar_sync_enabled
                            ) VALUES (%s,%s,%s,%s,'nova',%s,%s,%s,%s,%s,NULL,'pending',%s,%s,%s,TRUE) RETURNING id""",
                            (tenant_id, code, cid, t, args.get("start_date"), planning_start, planning_end,
                             int(owner["id"]), assigned_to or owner["display_name"], args.get("handover_note"), actor_name,
                             datetime.utcnow() if (assigned_to or args.get("handover_note")) else None))
                        jid = cur.fetchone()['id']
                        first_action = create_workflow_task(
                            conn,
                            tenant_id,
                            {
                                "title": tr("Continue with the new job", "Pokračovat v nové zakázce", "Kontynuować nowe zlecenie"),
                                "description": args.get("description"),
                                "assigned_user_id": int(owner["id"]),
                                "planned_start_at": next_business_day_at_nine().isoformat(),
                                "priority": "vysoka",
                                "client_id": cid,
                                "client_name": cname or None,
                            },
                            actor_name=actor_name,
                            default_client_id=cid,
                            default_client_name=cname or None,
                            default_job_id=jid,
                            source="assistant_job_create",
                        )
                        set_job_next_action(conn, tenant_id, jid, str(first_action["id"]))
                        log_activity(
                            conn,
                            "job",
                            jid,
                            "create",
                            f"Zakazka '{t}' ({code}) vytvorena",
                            tenant_id=tenant_id,
                            user_id=actor_user_id,
                            source_channel="assistant",
                            details={"assigned_user_id": int(owner["id"]), "next_action_task_id": str(first_action["id"])},
                        )
                        conn.commit()
                    return {"reply_cs":tr(
                        f"Job {code}: {t} created.",
                        f"Zakázka {code}: {t} vytvořena.",
                        f"Zlecenie {code}: {t} zostało utworzone."
                    ),"action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            if action == "CREATE_LEAD":
                n = args.get("name","Lead")
                conn = get_db_conn()
                try:
                    code = f"LED-{uuid.uuid4().hex[:6].upper()}"
                    with conn.cursor() as cur:
                        cur.execute("""INSERT INTO leads (lead_code,lead_source,status,contact_name,contact_email,contact_phone,description)
                            VALUES (%s,%s,'new',%s,%s,%s,%s) RETURNING id""",
                            (code,args.get("source","jiny"),n,args.get("email"),args.get("phone"),args.get("note",args.get("description"))))
                        lid = cur.fetchone()['id']
                        log_activity(conn,"lead",lid,"create",f"Lead '{n}' z {args.get('source','?')}")
                        conn.commit()
                    return {"reply_cs":tr(
                        f"Lead {code} from {n} has been recorded.",
                        f"Lead {code} od {n} zaevidován.",
                        f"Lead {code} od {n} został zapisany."
                    ),"action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            if action == "START_WORK_REPORT":
                return {"reply_cs":tr("Starting work report.", "Spouštím work report dialog.", "Uruchamiam raport pracy."),
                        "action_type":"START_WORK_REPORT","action_data":{}}

            if action == "SEARCH_EMAIL":
                try:
                    query = str(args.get("query") or "").strip()
                    sender = str(args.get("sender") or "").strip()
                    unread_only = bool(args.get("unread_only") or False)
                    try:
                        limit = int(float(args.get("limit", 5)))
                    except Exception:
                        limit = 5
                    emails = search_mail_messages(query=query, sender=sender, unread_only=unread_only, limit=limit)
                    if not emails:
                        return {"reply_cs": tr(
                            "I did not find any matching emails.",
                            "Nenašla jsem žádné odpovídající e-maily.",
                            "Nie znalazłam pasujących e-maili."
                        )}
                    lines = []
                    for idx, email_row in enumerate(emails, start=1):
                        lines.append(
                            f"{idx}. UID {email_row.get('uid')}: {email_row.get('from') or 'Unknown'} — "
                            f"{email_row.get('subject') or tr('No subject', 'Bez předmětu', 'Bez tematu')}. "
                            f"{email_row.get('summary') or ''}"
                        )
                    return {"reply_cs": tr(
                        "I found these emails:\n" + "\n".join(lines),
                        "Našla jsem tyto e-maily:\n" + "\n".join(lines),
                        "Znalazłam te e-maile:\n" + "\n".join(lines)
                    )}
                except Exception as e:
                    return {"reply_cs": tr(
                        f"Email search is not available: {e}",
                        f"Vyhledávání e-mailů není dostupné: {e}",
                        f"Wyszukiwanie e-maili nie jest dostępne: {e}"
                    )}

            if action == "READ_EMAIL":
                try:
                    uid = str(args.get("uid") or "").strip()
                    email_row = fetch_mail_by_uid(uid) if uid else None
                    if not email_row:
                        emails = search_mail_messages(
                            query=str(args.get("query") or "").strip(),
                            sender=str(args.get("sender") or "").strip(),
                            unread_only=bool(args.get("unread_only") or False),
                            limit=1,
                        )
                        email_row = emails[0] if emails else None
                    if not email_row:
                        return {"reply_cs": tr(
                            "I could not find that email.",
                            "Ten e-mail jsem nenašla.",
                            "Nie znalazłam tego e-maila."
                        )}
                    body = (email_row.get("body") or "").strip()
                    if len(body) > 1800:
                        body = body[:1800].rstrip() + "..."
                    return {"reply_cs": tr(
                        f"Email from {email_row.get('from')}. Subject: {email_row.get('subject') or 'No subject'}. UID {email_row.get('uid')}. Text: {body}",
                        f"E-mail od {email_row.get('from')}. Předmět: {email_row.get('subject') or 'Bez předmětu'}. UID {email_row.get('uid')}. Text: {body}",
                        f"E-mail od {email_row.get('from')}. Temat: {email_row.get('subject') or 'Bez tematu'}. UID {email_row.get('uid')}. Treść: {body}"
                    )}
                except Exception as e:
                    return {"reply_cs": tr(
                        f"Email reading is not available: {e}",
                        f"Čtení e-mailů není dostupné: {e}",
                        f"Czytanie e-maili nie jest dostępne: {e}"
                    )}

            if action == "REPLY_EMAIL":
                try:
                    body = str(args.get("body") or "").strip()
                    if not body:
                        return {"reply_cs": tr("What should I write?", "Co mám napsat?", "Co mam napisać?"), "is_question": True}
                    uid = str(args.get("uid") or "").strip()
                    original = fetch_mail_by_uid(uid) if uid else None
                    if not original:
                        emails = search_mail_messages(
                            query=str(args.get("query") or "").strip(),
                            sender=str(args.get("sender") or "").strip(),
                            unread_only=False,
                            limit=1,
                        )
                        original = emails[0] if emails else None
                    if not original:
                        return {"reply_cs": tr(
                            "I could not find the email to reply to.",
                            "Nenašla jsem e-mail, na který mám odpovědět.",
                            "Nie znalazłam e-maila, na który mam odpowiedzieć."
                        )}
                    sent = send_mail_reply(original, body)
                    return {"reply_cs": tr(
                        f"Reply sent to {sent.get('to')}. Subject: {sent.get('subject')}.",
                        f"Odpověď odeslána na {sent.get('to')}. Předmět: {sent.get('subject')}.",
                        f"Odpowiedź wysłana do {sent.get('to')}. Temat: {sent.get('subject')}."
                    )}
                except Exception as e:
                    return {"reply_cs": tr(
                        f"Email reply is not available: {e}",
                        f"Odpověď na e-mail není dostupná: {e}",
                        f"Odpowiedź na e-mail nie jest dostępna: {e}"
                    )}

            if action == "GET_WEATHER":
                try:
                    import urllib.request, urllib.parse, urllib.error
                    loc = str(args.get("location") or "Didcot").strip() or "Didcot"
                    try:
                        days = int(float(args.get("days", 3)))
                    except Exception:
                        days = 3
                    days = max(1, min(days, 7))
                    cache_key = f"{loc.lower()}::{days}"
                    cached_entry = WEATHER_CACHE.get(cache_key)
                    now_ts = datetime.utcnow().timestamp()
                    if cached_entry and (now_ts - cached_entry.get("ts", 0)) < WEATHER_CACHE_TTL_SECONDS:
                        return {"reply_cs": cached_entry["reply"]}
                    # Geocode location
                    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(loc)}&count=1&language=en"
                    with urllib.request.urlopen(geo_url, timeout=5) as r:
                        geo = json.loads(r.read())
                    if not geo.get("results"):
                        return {"reply_cs": tr(
                            f"I couldn't find location '{loc}'. Try a different city name.",
                            f"Lokalitu '{loc}' jsem nenašel. Zkus jiný název města.",
                            f"Nie znalazłam lokalizacji '{loc}'. Spróbuj innej nazwy miasta."
                        )}
                    place = geo["results"][0]
                    lat, lon, name = place["latitude"], place["longitude"], place.get("name", loc)
                    # Fetch weather
                    wx_url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                        f"&hourly=temperature_2m,precipitation_probability,weathercode,windspeed_10m"
                        f"&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max"
                        f"&timezone=Europe/London&forecast_days={days}")
                    with urllib.request.urlopen(wx_url, timeout=8) as r:
                        wx = json.loads(r.read())
                    if wx.get("error") or not wx.get("daily"):
                        reason = wx.get("reason") or wx.get("error") or "unknown weather service error"
                        raise RuntimeError(reason)
                    # Format daily summary
                    wmo = {0:"☀️ Jasno",1:"🌤 Polojasno",2:"⛅ Oblačno",3:"☁️ Zataženo",45:"🌫 Mlha",48:"🌫 Námraza",
                           51:"🌧 Mrholení",53:"🌧 Mrholení",55:"🌧 Mrholení",56:"🌧 Mrz. mrholení",57:"🌧 Mrz. mrholení",
                           61:"🌧 Déšť",63:"🌧 Střed. déšť",65:"🌧 Silný déšť",66:"🌧 Mrz. déšť",67:"🌧 Mrz. déšť",
                           71:"🌨 Sněžení",73:"🌨 Sněžení",75:"🌨 Silný sníh",77:"🌨 Krupky",
                           80:"🌦 Přeháňky",81:"🌦 Přeháňky",82:"🌦 Silné přeháňky",
                           85:"🌨 Sněh. přeháňky",86:"🌨 Sněh. přeháňky",
                           95:"⛈ Bouřka",96:"⛈ Bouřka s kroupami",99:"⛈ Silná bouřka"}
                    daily = wx.get("daily",{})
                    lines = [tr(
                        f"📍 Weather — {name} ({days} days):",
                        f"📍 Počasí — {name} ({days} dní):",
                        f"📍 Pogoda — {name} ({days} dni):"
                    )]
                    daily_codes = daily.get("weathercode") or daily.get("weather_code") or []
                    daily_tmax = daily.get("temperature_2m_max") or []
                    daily_tmin = daily.get("temperature_2m_min") or []
                    daily_rain = daily.get("precipitation_sum") or []
                    daily_rain_prob = daily.get("precipitation_probability_max") or []
                    daily_wind = daily.get("windspeed_10m_max") or daily.get("wind_speed_10m_max") or []
                    for i, d in enumerate(daily.get("time",[])):
                        code = daily_codes[i] if i < len(daily_codes) else 0
                        tmax = daily_tmax[i] if i < len(daily_tmax) else None
                        tmin = daily_tmin[i] if i < len(daily_tmin) else None
                        rain = daily_rain[i] if i < len(daily_rain) else None
                        rain_prob = daily_rain_prob[i] if i < len(daily_rain_prob) else None
                        wind = daily_wind[i] if i < len(daily_wind) else None
                        desc = wmo.get(code, f"Kód {code}")
                        if tmin is not None and tmax is not None:
                            line = f"\n{d}: {desc}, {tmin:.0f}–{tmax:.0f}°C"
                        elif tmax is not None:
                            line = f"\n{d}: {desc}, {tmax:.0f}°C"
                        else:
                            line = f"\n{d}: {desc}"
                        if rain is not None and rain > 0:
                            line += tr(f", rain {rain:.1f}mm", f", déšť {rain:.1f}mm", f", deszcz {rain:.1f}mm")
                        if rain_prob is not None:
                            line += f" ({rain_prob:.0f}%)"
                        if wind is not None:
                            line += tr(f", wind {wind:.0f} km/h", f", vítr {wind:.0f} km/h", f", wiatr {wind:.0f} km/h")
                        lines.append(line)
                    # Add hourly for today
                    hourly = wx.get("hourly",{})
                    h_times = hourly.get("time",[])
                    h_temps = hourly.get("temperature_2m",[])
                    h_rain = hourly.get("precipitation_probability",[])
                    h_codes = hourly.get("weathercode") or hourly.get("weather_code") or []
                    if h_times:
                        lines.append(tr("\n⏰ Hourly forecast today:", "\n⏰ Hodinová předpověď dnes:", "\n⏰ Prognoza godzinowa na dziś:"))
                        today_str = daily.get("time",[""])[0]
                        for j, ht in enumerate(h_times[:24]):
                            if today_str in ht:
                                hour = ht.split("T")[1][:5]
                                if hour in ["06:00","09:00","12:00","15:00","18:00","21:00"]:
                                    t = h_temps[j] if j < len(h_temps) else None
                                    rp = h_rain[j] if j < len(h_rain) else 0
                                    cd = h_codes[j] if j < len(h_codes) else 0
                                    emoji = wmo.get(cd,"")[:2]
                                    lines.append(tr(
                                        f"  {hour} {emoji} {f'{t:.0f}°C' if t is not None else '?'} , rain {rp:.0f}%",
                                        f"  {hour} {emoji} {f'{t:.0f}°C' if t is not None else '?'} , déšť {rp:.0f}%",
                                        f"  {hour} {emoji} {f'{t:.0f}°C' if t is not None else '?'} , deszcz {rp:.0f}%"
                                    ))
                    reply = "\n".join(lines)
                    WEATHER_CACHE[cache_key] = {"reply": reply, "ts": now_ts}
                    return {"reply_cs": reply}
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        cached_entry = WEATHER_CACHE.get(cache_key)
                        if cached_entry:
                            return {"reply_cs": cached_entry["reply"] + tr(
                                "\n\nℹ️ Using the last saved weather because the weather service is busy.",
                                "\n\nℹ️ Používám poslední uložené počasí, protože weather služba je teď přetížená.",
                                "\n\nℹ️ Używam ostatnio zapisanej prognozy, ponieważ usługa pogody jest teraz przeciążona."
                            )}
                        return {"reply_cs": tr(
                            "Weather service is temporarily busy. Try again in a minute.",
                            "Služba počasí je dočasně přetížená. Zkus to znovu za chvíli.",
                            "Usługa pogody jest chwilowo przeciążona. Spróbuj ponownie za chwilę."
                        )}
                    raise
                except Exception as e:
                    return {"reply_cs": tr(
                        f"Failed to load weather: {e}",
                        f"Nepodařilo se načíst počasí: {e}",
                        f"Nie udało się pobrać pogody: {e}"
                    )}

            if action == "SEND_WHATSAPP":
                client_name = args.get("client_name") or args.get("contact_name") or args.get("name") or ""
                original_message = args.get("message") or ""
                phone = args.get("phone") or ""
                resolved_client_id = None
                if phone or client_name:
                    conn = get_db_conn()
                    try:
                        with conn.cursor() as cur:
                            if phone:
                                matched = find_client_by_communication_phone(cur, tenant_id, phone)
                                if matched:
                                    resolved_client_id = matched.get("id")
                                    client_name = matched.get("display_name") or client_name
                                    phone = matched.get("phone_primary") or matched.get("phone_secondary") or phone
                            if not phone and client_name:
                                matched = find_client_by_voice_name(cur, tenant_id, client_name)
                                if matched:
                                    resolved_client_id = matched.get("id")
                                    client_name = matched.get("display_name") or client_name
                                    phone = matched.get("phone_primary") or matched.get("phone_secondary") or phone
                    finally:
                        release_conn(conn)
                outgoing_language = resolve_customer_language(tenant_config, msg.external_language)
                translated_message = translate_customer_message(original_message, outgoing_language)
                return {
                    "reply_cs": tr(
                        f"Opening WhatsApp for {client_name or phone}.",
                        f"Otevírám WhatsApp pro {client_name or phone}.",
                        f"Otwieram WhatsApp dla {client_name or phone}.",
                    ),
                    "action_type": "SEND_WHATSAPP",
                    "action_data": {
                        "client_id": resolved_client_id,
                        "client_name": client_name,
                        "contact_name": args.get("contact_name") or client_name,
                        "phone": phone,
                        "message": translated_message,
                        "original_message": original_message,
                        "language": outgoing_language,
                    },
                }

            if action == "START_NAVIGATION":
                target = (
                    args.get("address")
                    or args.get("target")
                    or args.get("client_name")
                    or args.get("contact_name")
                    or args.get("name")
                    or ""
                )
                if not target:
                    return {"reply_cs": tr(
                        "Where should I navigate?",
                        "Kam mám spustit navigaci?",
                        "Dokąd mam uruchomić nawigację?",
                    )}
                return {
                    "reply_cs": tr(
                        f"Opening navigation to {target}.",
                        f"Spouštím navigaci na {target}.",
                        f"Uruchamiam nawigację do {target}.",
                    ),
                    "action_type": "START_NAVIGATION",
                    "action_data": {
                        "target": target,
                        "address": args.get("address") or "",
                        "client_name": args.get("client_name") or "",
                        "contact_name": args.get("contact_name") or "",
                    },
                }

            if action == "REMEMBER_MEMORY":
                content = args.get("content", "").strip()
                memory_type = args.get("memory_type", "long")
                if not content:
                    return {"reply_cs": tr("What should I remember?", "Co si mám zapamatovat?", "Co mam zapamiętać?")}
                conn = get_db_conn()
                try:
                    remembered = remember_assistant_memory(conn, tenant_id, current_user_id, content, memory_type)
                    conn.commit()
                    return {"reply_cs": tr(
                        f"I will remember: {content}",
                        f"Zapamatovala jsem si: {content}",
                        f"Zapamiętałam: {content}",
                    ), "action_type": "MEMORY_REMEMBERED", "action_data": remembered}
                except Exception as e:
                    conn.rollback()
                    return error_reply(e)
                finally:
                    release_conn(conn)

            if action == "FORGET_MEMORY":
                query = args.get("query", "").strip()
                if not query:
                    return {"reply_cs": tr("What should I forget?", "Co mám zapomenout?", "Co mam zapomnieć?")}
                conn = get_db_conn()
                try:
                    forgotten = forget_assistant_memory(conn, tenant_id, current_user_id, query)
                    conn.commit()
                    return {"reply_cs": tr(
                        f"I forgot {forgotten['count']} matching memory item(s).",
                        f"Zapomněla jsem {forgotten['count']} odpovídající položek paměti.",
                        f"Zapomniałam {forgotten['count']} pasujących pozycji pamięci.",
                    ), "action_type": "MEMORY_FORGOTTEN", "action_data": forgotten}
                except Exception as e:
                    conn.rollback()
                    return error_reply(e)
                finally:
                    release_conn(conn)

            if action == "QUERY_CLIENTS":
                question = args.get("question", "")
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        # Add source column if missing
                        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS source TEXT")
                        conn.commit()
                        stats = {}
                        cur.execute("SELECT COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL AND tenant_id=1")
                        stats["total_clients"] = cur.fetchone()["cnt"]
                        cur.execute("SELECT status, COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL AND tenant_id=1 GROUP BY status")
                        stats["by_status"] = {r["status"]: r["cnt"] for r in cur.fetchall()}
                        cur.execute("SELECT COALESCE(source,'unknown') as src, COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL AND tenant_id=1 GROUP BY COALESCE(source,'unknown')")
                        stats["by_source"] = {r["src"]: r["cnt"] for r in cur.fetchall()}
                        cur.execute("SELECT client_type, COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL AND tenant_id=1 GROUP BY client_type")
                        stats["by_type"] = {r["client_type"]: r["cnt"] for r in cur.fetchall()}
                        cur.execute("SELECT is_commercial, COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL AND tenant_id=1 GROUP BY is_commercial")
                        stats["commercial"] = {str(r["is_commercial"]): r["cnt"] for r in cur.fetchall()}
                        cur.execute("SELECT display_name, phone_primary, email_primary, COALESCE(source,'?') as source, created_at::text FROM clients WHERE deleted_at IS NULL AND tenant_id=1 ORDER BY created_at DESC LIMIT 5")
                        stats["recent_5"] = [dict(r) for r in cur.fetchall()]
                        cur.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL")
                        stats["total_jobs"] = cur.fetchone()["cnt"]
                        cur.execute("SELECT COUNT(*) as cnt FROM leads WHERE tenant_id=1")
                        stats["total_leads"] = cur.fetchone()["cnt"]
                        cur.execute("SELECT COUNT(*) as cnt FROM invoices WHERE tenant_id=1")
                        stats["total_invoices"] = cur.fetchone()["cnt"]
                        cur.execute("SELECT COUNT(*) as cnt FROM tasks WHERE tenant_id=1")
                        stats["total_tasks"] = cur.fetchone()["cnt"]
                    # Build human-readable answer
                    lines = [tr("📊 CRM database:", "📊 Databáze CRM:", "📊 Baza CRM:")]
                    lines.append(tr(
                        f"Total clients: {stats['total_clients']}",
                        f"Klientů celkem: {stats['total_clients']}",
                        f"Łącznie klientów: {stats['total_clients']}"
                    ))
                    if stats["by_status"]: lines.append(tr(f"By status: {', '.join(f'{k}={v}' for k,v in stats['by_status'].items())}", f"Podle stavu: {', '.join(f'{k}={v}' for k,v in stats['by_status'].items())}", f"Według statusu: {', '.join(f'{k}={v}' for k,v in stats['by_status'].items())}"))
                    if stats["by_source"]: lines.append(tr(f"By source: {', '.join(f'{k}={v}' for k,v in stats['by_source'].items())}", f"Podle zdroje: {', '.join(f'{k}={v}' for k,v in stats['by_source'].items())}", f"Według źródła: {', '.join(f'{k}={v}' for k,v in stats['by_source'].items())}"))
                    if stats["by_type"]: lines.append(tr(f"By type: {', '.join(f'{k}={v}' for k,v in stats['by_type'].items())}", f"Podle typu: {', '.join(f'{k}={v}' for k,v in stats['by_type'].items())}", f"Według typu: {', '.join(f'{k}={v}' for k,v in stats['by_type'].items())}"))
                    lines.append(tr(
                        f"Jobs: {stats['total_jobs']}, Leads: {stats['total_leads']}, Invoices: {stats['total_invoices']}, Tasks: {stats['total_tasks']}",
                        f"Zakázek: {stats['total_jobs']}, Leadů: {stats['total_leads']}, Faktur: {stats['total_invoices']}, Úkolů: {stats['total_tasks']}",
                        f"Zleceń: {stats['total_jobs']}, Leadów: {stats['total_leads']}, Faktur: {stats['total_invoices']}, Zadań: {stats['total_tasks']}"
                    ))
                    if stats["recent_5"]:
                        lines.append(tr("Last 5 clients:", "Posledních 5 klientů:", "Ostatnich 5 klientów:"))
                        for c in stats["recent_5"]:
                            lines.append(f"  - {c['display_name']} ({c.get('source','?')}) {c.get('phone_primary','')}")
                    return {"reply_cs": "\n".join(lines)}
                except Exception as e:
                    return {"reply_cs": tr(
                        f"Database query error: {e}",
                        f"Chyba při dotazu na databázi: {e}",
                        f"Błąd zapytania do bazy danych: {e}"
                    )}
                finally: release_conn(conn)

            if action == "ADD_NOTE":
                etype = args.get("entity_type","client")
                note = args.get("note","")
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        if etype == "client":
                            ename = args.get("entity_name","")
                            cur.execute("SELECT id FROM clients WHERE display_name ILIKE %s AND deleted_at IS NULL LIMIT 1",(f"%{ename}%",))
                            row = cur.fetchone()
                            if row:
                                cur.execute("INSERT INTO client_notes (client_id,note) VALUES (%s,%s)",(row['id'],note))
                                log_activity(conn,"client",row['id'],"note",f"Poznamka: {note[:50]}")
                            else: return {"reply_cs":tr(f"Client '{ename}' not found.", f"Klient '{ename}' nenalezen.", f"Nie znaleziono klienta '{ename}'.")}
                        elif etype == "job":
                            ename = args.get("entity_name","")
                            cur.execute("SELECT id FROM jobs WHERE job_title ILIKE %s AND deleted_at IS NULL LIMIT 1",(f"%{ename}%",))
                            row = cur.fetchone()
                            if row:
                                cur.execute("INSERT INTO job_notes (job_id,note) VALUES (%s,%s)",(row['id'],note))
                                log_activity(conn,"job",row['id'],"note",f"Poznamka: {note[:50]}")
                            else: return {"reply_cs":tr(f"Job '{ename}' not found.", f"Zakazka '{ename}' nenalezena.", f"Nie znaleziono zlecenia '{ename}'.")}
                        conn.commit()
                    return {"reply_cs":tr("Note added.", "Poznámka přidána.", "Notatka została dodana."),"action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            if action == "UPDATE_TASK":
                title_q = args.get("title","")
                conn = get_db_conn()
                try:
                    tenant_id = get_request_tenant_id(request)
                    with conn.cursor() as cur:
                        cur.execute("""SELECT id,title FROM tasks
                            WHERE tenant_id=%s AND title ILIKE %s AND is_completed=FALSE
                            ORDER BY created_at DESC LIMIT 1""",(tenant_id, f"%{title_q}%",))
                        row = cur.fetchone()
                        if not row: return {"reply_cs":tr(f"Task '{title_q}' not found.", f"Úkol '{title_q}' nenalezen.", f"Nie znaleziono zadania '{title_q}'.")}
                        task_links = get_task_next_action_links(conn, tenant_id, row["id"])
                        requested_status = args.get("status")
                        if task_links and requested_status in ("hotovo", "zruseno"):
                            return {"reply_cs": tr(
                                "This task is the current next step. Choose or create the replacement task first.",
                                "Tento úkol je aktuální další krok. Nejprve vyber nebo vytvoř náhradní úkol.",
                                "To zadanie jest bieżącym kolejnym krokiem. Najpierw wybierz lub utwórz zadanie zastępcze."
                            )}
                        sets = []; vals = []
                        if "status" in args: sets.append("status=%s"); vals.append(args["status"])
                        if "priority" in args: sets.append("priority=%s"); vals.append(args["priority"])
                        if "result" in args: sets.append("result=%s"); vals.append(args["result"])
                        if "assigned_to" in args:
                            assigned_user_id, assigned_to = resolve_assigned_user(conn, tenant_id, None, args.get("assigned_to"))
                            sets.append("assigned_to=%s"); vals.append(assigned_to)
                            sets.append("assigned_user_id=%s"); vals.append(assigned_user_id)
                            sets.append("delegated_by=%s"); vals.append(get_user_display_name(conn, tenant_id, request.state.user.get("user_id")))
                        if "planning_note" in args:
                            sets.append("planning_note=%s"); vals.append(args["planning_note"])
                        if "planned_date" in args:
                            sets.append("planned_date=%s"); vals.append(args["planned_date"])
                        if "planned_start_at" in args or "planned_end_at" in args or "planned_date" in args:
                            planning_start, planning_end = planning_window_from_values(
                                args.get("planned_start_at"), args.get("planned_end_at"), args.get("planned_date")
                            )
                            sets.append("planned_start_at=%s"); vals.append(planning_start)
                            sets.append("planned_end_at=%s"); vals.append(planning_end)
                        if sets:
                            sets.append("updated_at=now()"); vals.append(row['id'])
                            cur.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=%s",vals)
                            log_activity(conn,"task",row['id'],"update",f"Ukol '{row['title']}' upraven")
                            conn.commit()
                        changes = ", ".join([f"{k}={v}" for k,v in args.items() if k != "title"])
                    return {"reply_cs":tr(
                        f"Task '{row['title']}' updated: {changes}.",
                        f"Úkol '{row['title']}' upraven: {changes}.",
                        f"Zadanie '{row['title']}' zaktualizowano: {changes}."
                    ),"action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            if action == "UPDATE_JOB":
                title_q = args.get("title","")
                conn = get_db_conn()
                try:
                    tenant_id = get_request_tenant_id(request)
                    with conn.cursor() as cur:
                        cur.execute("""SELECT id,job_title,job_status FROM jobs
                            WHERE tenant_id=%s AND job_title ILIKE %s AND deleted_at IS NULL
                            ORDER BY created_at DESC LIMIT 1""",(tenant_id, f"%{title_q}%",))
                        row = cur.fetchone()
                        if not row: return {"reply_cs":tr(f"Job '{title_q}' not found.", f"Zakázka '{title_q}' nenalezena.", f"Nie znaleziono zlecenia '{title_q}'.")}
                        new_status = args.get("status",row['job_status'])
                        err = validate_state_transition(row['job_status'], new_status, JOB_TRANSITIONS, "Job")
                        if err: return {"reply_cs":tr(f"Invalid transition: {err}", f"Neplatný přechod: {err}", f"Nieprawidłowe przejście: {err}")}
                        sets = ["job_status=%s"]
                        vals = [new_status]
                        if "assigned_to" in args:
                            assigned_user_id, assigned_to = resolve_assigned_user(conn, tenant_id, None, args.get("assigned_to"))
                            sets.extend(["assigned_to=%s", "assigned_user_id=%s"])
                            vals.extend([assigned_to, assigned_user_id])
                            sets.extend(["handed_over_by=%s", "handed_over_at=%s"])
                            vals.extend([get_user_display_name(conn, tenant_id, request.state.user.get("user_id")), datetime.utcnow()])
                        if "handover_note" in args:
                            sets.append("handover_note=%s"); vals.append(args.get("handover_note"))
                            sets.extend(["handed_over_by=%s", "handed_over_at=%s"])
                            vals.extend([get_user_display_name(conn, tenant_id, request.state.user.get("user_id")), datetime.utcnow()])
                        if "planned_start_at" in args or "planned_end_at" in args:
                            planning_start, planning_end = planning_window_from_values(args.get("planned_start_at"), args.get("planned_end_at"))
                            sets.extend(["planned_start_at=%s", "planned_end_at=%s"])
                            vals.extend([planning_start, planning_end])
                        sets.append("updated_at=now()")
                        vals.append(row['id'])
                        cur.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=%s", vals)
                        log_activity(conn,"job",row['id'],"status_change",f"Zakazka '{row['job_title']}': {row['job_status']} -> {new_status}")
                        conn.commit()
                    return {"reply_cs":tr(
                        f"Job '{row['job_title']}' changed to: {new_status}.",
                        f"Zakázka '{row['job_title']}' změněna na: {new_status}.",
                        f"Zlecenie '{row['job_title']}' zmieniono na: {new_status}."
                    ),"action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            if action == "LIST_TASKS":
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        sql = "SELECT title,status,priority,client_name,deadline FROM tasks WHERE 1=1"
                        params = []
                        if args.get("only_active",True): sql += " AND is_completed=FALSE AND status NOT IN ('hotovo','zruseno')"
                        if args.get("status"): sql += " AND status=%s"; params.append(args["status"])
                        if args.get("client_name"): sql += " AND client_name ILIKE %s"; params.append(f"%{args['client_name']}%")
                        sql += " ORDER BY CASE priority WHEN 'kriticka' THEN 1 WHEN 'urgentni' THEN 2 WHEN 'vysoka' THEN 3 ELSE 4 END LIMIT 15"
                        cur.execute(sql,params)
                        rows = cur.fetchall()
                    if not rows: return {"reply_cs":tr("You have no active tasks.", "Nemáš žádné aktivní úkoly.", "Nie masz żadnych aktywnych zadań.")}
                    items = [
                        f"- {r['title']} ({r['priority']}, {r['status']})"
                        + (tr(" client: ", " klient: ", " klient: ") + str(r['client_name']) if r.get('client_name') else "")
                        + (tr(" due: ", " DL: ", " termin: ") + str(r['deadline']) if r.get('deadline') else "")
                        for r in rows
                    ]
                    return {"reply_cs":tr(
                        f"You have {len(rows)} tasks:\n" + "\n".join(items),
                        f"Máš {len(rows)} úkolů:\n" + "\n".join(items),
                        f"Masz {len(rows)} zadań:\n" + "\n".join(items)
                    ),"action_type":"LIST_TASKS"}
                finally: release_conn(conn)

            if action == "COMPLETE_TASK":
                title_q = args.get("title","")
                conn = get_db_conn()
                try:
                    tenant_id = get_request_tenant_id(request)
                    with conn.cursor() as cur:
                        cur.execute("SELECT id,title FROM tasks WHERE tenant_id=%s AND title ILIKE %s AND is_completed=FALSE ORDER BY created_at DESC LIMIT 1",(tenant_id, f"%{title_q}%",))
                        row = cur.fetchone()
                        if not row: return {"reply_cs":tr(f"Task '{title_q}' not found or is already done.", f"Úkol '{title_q}' nenalezen nebo už je hotový.", f"Zadanie '{title_q}' nie zostało znalezione albo jest już ukończone.")}
                        if get_task_next_action_links(conn, tenant_id, row["id"]):
                            return {"reply_cs": tr(
                                "This task is the current next step. Create or choose the replacement task before completing it.",
                                "Tento úkol je aktuální další krok. Před dokončením nejdřív vytvoř nebo vyber náhradní úkol.",
                                "To zadanie jest bieżącym kolejnym krokiem. Przed ukończeniem najpierw utwórz lub wybierz zadanie zastępcze."
                            )}
                        result = args.get("result", tr("Completed", "Dokončeno", "Ukończono"))
                        cur.execute("UPDATE tasks SET status='hotovo',is_completed=TRUE,result=%s,updated_at=now() WHERE id=%s",(result,row['id']))
                        log_activity(conn,"task",row['id'],"complete",f"Ukol '{row['title']}' dokoncen: {result}")
                        conn.commit()
                    return {"reply_cs":tr(
                        f"Task '{row['title']}' completed. Result: {result}",
                        f"Úkol '{row['title']}' dokončen. Výsledek: {result}",
                        f"Zadanie '{row['title']}' ukończone. Wynik: {result}"
                    ),"action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return error_reply(e)
                finally: release_conn(conn)

            # === CLIENT-SIDE ACTIONS (passthrough to Android) ===
            human = {
                "ADD_CALENDAR_EVENT": tr(f"Adding {args.get('title','')} to the calendar.", f"Zapisuji {args.get('title','')} do kalendáře.", f"Dodaję {args.get('title','')} do kalendarza."),
                "MODIFY_CALENDAR_EVENT": tr(f"Updating event {args.get('event_title','')}.", f"Měním událost {args.get('event_title','')}.", f"Zmieniam wydarzenie {args.get('event_title','')}."),
                "DELETE_CALENDAR_EVENT": tr(f"Deleting event {args.get('event_title','')}.", f"Mažu událost {args.get('event_title','')}.", f"Usuwam wydarzenie {args.get('event_title','')}."),
                "LIST_CALENDAR_EVENTS": tr("I'll check the calendar.", "Podívám se do kalendáře.", "Sprawdzę kalendarz."),
                "CALL_CONTACT": tr(
                    f"Dialing {args.get('contact_name') or args.get('client_name') or args.get('phone','')}.",
                    f"Vytáčím {args.get('contact_name') or args.get('client_name') or args.get('phone','')}.",
                    f"Wybieram {args.get('contact_name') or args.get('client_name') or args.get('phone','')}."
                ),
                "SEND_EMAIL": tr(f"Sending email to {args.get('to','')}.", f"Posílám email na {args.get('to','')}.", f"Wysyłam email na {args.get('to','')}."),
            }
            reply = ai_msg.content or human.get(action, tr("Done.", "Hotovo.", "Gotowe."))
            return {"reply_cs":reply,"action_type":action,"action_data":args}

        # No tool call — plain text reply
        reply = ai_msg.content or tr("Understood.", "Rozumím.", "Rozumiem.")
        # Fallback: if reply mentions work report but GPT didn't call tool, force it
        wr_kw = ["work report","výkaz","vykaz","nahlášení práce","nahlaseni prace","zapsat práci","zapsat praci","raport pracy","zahajuji proces"]
        if any(kw in (reply + " " + msg.text).lower() for kw in wr_kw):
            return {"reply_cs":reply,"action_type":"START_WORK_REPORT","action_data":{}}
        return {"reply_cs":reply,"is_question":"?" in reply}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"reply_cs":f"Error: {type(e).__name__}: {str(e)}"}

# ========== REST API: CLIENTS ==========
@app.get("/crm/clients")
async def get_clients(request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clients WHERE deleted_at IS NULL AND tenant_id=%s ORDER BY display_name",(tid,))
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/clients/search")
async def search_clients(q: str = Query(..., min_length=1)):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            s = f"%{q}%"
            cur.execute("SELECT * FROM clients WHERE deleted_at IS NULL AND (display_name ILIKE %s OR email_primary ILIKE %s OR phone_primary ILIKE %s OR client_code ILIKE %s) ORDER BY display_name LIMIT 20",(s,s,s,s))
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/clients/{client_id}")
async def get_client_detail(client_id: int, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clients WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL",(client_id, tid))
            cl = cur.fetchone()
            if not cl: raise HTTPException(404,"Klient nenalezen")
            cur.execute("SELECT * FROM properties WHERE client_id=%s AND deleted_at IS NULL",(client_id,))
            props = cur.fetchall()
            cur.execute("SELECT j.*,j.start_date_planned::text as start_date_planned FROM jobs j WHERE j.client_id=%s AND j.deleted_at IS NULL ORDER BY j.created_at DESC LIMIT 10",(client_id,))
            jobs = cur.fetchall()
            cur.execute("""
                SELECT id,client_id,job_id,comm_type,COALESCE(source, comm_type) AS source,
                       external_message_id,source_phone,target_phone,conversation_key,
                       subject,message_summary,sent_at::text,direction,notes,created_at::text,imported_at::text
                FROM communications
                WHERE client_id=%s AND tenant_id=%s
                ORDER BY COALESCE(sent_at, created_at) DESC, id DESC
                LIMIT 500
            """,(client_id, tid))
            comms = cur.fetchall()
            cur.execute("SELECT * FROM tasks WHERE client_id=%s AND is_completed=FALSE ORDER BY created_at DESC LIMIT 10",(client_id,))
            tasks = cur.fetchall()
            cur.execute("SELECT id,note,created_by,created_at::text FROM client_notes WHERE client_id=%s ORDER BY created_at DESC LIMIT 20",(client_id,))
            notes = cur.fetchall()
            service_rate_overrides = get_client_service_rate_overrides(conn, tid, client_id)
            return {"client":dict(cl),"properties":[dict(p) for p in props],"recent_jobs":[dict(j) for j in jobs],
                    "communications":[dict(c) for c in comms],"tasks":[dict(t) for t in tasks],"notes":[dict(n) for n in notes],
                    "service_rates": get_client_service_rates(conn, tid, client_id),
                    "service_rate_overrides": {k: v for k, v in service_rate_overrides.items() if k in VISIBLE_CLIENT_SERVICE_RATE_KEYS},
                    "has_individual_service_rates": bool(service_rate_overrides)}
    finally: release_conn(conn)

@app.post("/crm/clients")
async def api_create_client(data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        ok, msg = check_subscription_limit(conn, tid, "clients")
        if not ok:
            raise HTTPException(429, msg)
        owner_user_id = data.get("owner_user_id")
        first_action = data.get("first_action")
        actor_user_id = request.state.user.get("user_id")
        actor_name = get_user_display_name(conn, tid, actor_user_id) or "system"
        owner = validate_active_user(conn, tid, owner_user_id, "client owner")
        if not isinstance(first_action, dict):
            raise HTTPException(422, "Client must include first_action")
        display_name = clean_contact_display_name(data.get("name") or data.get("display_name"))
        if not display_name:
            raise HTTPException(400, "display_name required")
        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO clients (client_code,client_type,title,first_name,last_name,display_name,
                company_name,company_registration_no,vat_no,email_primary,email_secondary,
                phone_primary,phone_secondary,website,preferred_contact_method,
                billing_address_line1,billing_city,billing_postcode,billing_country,
                status,is_commercial,tenant_id,owner_user_id,hierarchy_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,'pending') RETURNING id,display_name""",
                (code,data.get("type",data.get("client_type","domestic")),
                 data.get("title"),data.get("first_name"),data.get("last_name"),
                 display_name,
                 data.get("company_name"),data.get("company_registration_no"),data.get("vat_no"),
                 data.get("email",data.get("email_primary")),data.get("email_secondary"),
                 data.get("phone",data.get("phone_primary")),data.get("phone_secondary"),
                 data.get("website"),data.get("preferred_contact_method","email"),
                 data.get("billing_address_line1"),data.get("billing_city"),
                 data.get("billing_postcode"),data.get("billing_country","GB"),
                 data.get("is_commercial",False),tid,int(owner["id"])))
            client_row = dict(cur.fetchone())
            cid = client_row["id"]
            next_action = create_workflow_task(
                conn,
                tid,
                first_action,
                actor_name=actor_name,
                default_client_id=cid,
                default_client_name=client_row.get("display_name") or display_name,
                source="client_first_action",
            )
            set_client_next_action(conn, tid, cid, str(next_action["id"]))
            validation = validate_client_hierarchy(conn, tid, cid)
            if not validation["valid"]:
                raise HTTPException(422, f"Client hierarchy invalid: {', '.join(validation['issues'])}")
            log_activity(
                conn,
                "client",
                cid,
                "create",
                f"Klient {display_name} vytvoren",
                tenant_id=tid,
                user_id=actor_user_id,
                source_channel="crm",
                details={
                    "owner_user_id": int(owner["id"]),
                    "next_action_task_id": str(next_action["id"]),
                },
            )
            conn.commit()
        return {
            "id": cid,
            "client_code": code,
            "status": "success",
            "owner_user_id": int(owner["id"]),
            "next_action_task_id": str(next_action["id"]),
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500,str(e))
    finally:
        release_conn(conn)

@app.put("/crm/clients/{client_id}")
async def update_client(client_id: int, data: dict, request: Request):
    conn = get_db_conn()
    try:
        tid = get_request_tenant_id(request)
        actor_user_id = request.state.user.get("user_id")
        sets = []; vals = []
        if "owner_user_id" in data:
            if data.get("owner_user_id") in (None, "", 0, "0"):
                raise HTTPException(422, "Client owner cannot be empty")
            owner = validate_active_user(conn, tid, data.get("owner_user_id"), "client owner")
            sets.append("owner_user_id=%s"); vals.append(int(owner["id"]))
        if "next_action_task_id" in data:
            if not data.get("next_action_task_id"):
                raise HTTPException(422, "Client next_action_task_id cannot be empty")
            candidate = get_valid_client_next_action_task(conn, tid, client_id, str(data["next_action_task_id"]))
            if not candidate:
                raise HTTPException(422, "Client next action must point to an open planned client task")
            sets.append("next_action_task_id=%s"); vals.append(str(data["next_action_task_id"]))
        for k in ["display_name","first_name","last_name","title","client_type","company_name","company_registration_no","vat_no","email_primary","email_secondary","phone_primary","phone_secondary","website","preferred_contact_method","billing_address_line1","billing_city","billing_postcode","billing_country","status","is_commercial"]:
            if k in data:
                sets.append(f"{k}=%s")
                vals.append(clean_contact_display_name(data[k]) if k == "display_name" else data[k])
        if not sets:
            raise HTTPException(400,"Zadna data")
        sets.append("updated_at=now()"); vals.extend([tid, client_id])
        with conn.cursor() as cur:
            cur.execute(f"UPDATE clients SET {','.join(sets)} WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL",vals)
            if cur.rowcount == 0:
                raise HTTPException(404, "Client not found")
            validation = validate_client_hierarchy(conn, tid, client_id)
            if not validation["valid"]:
                raise HTTPException(422, f"Client hierarchy invalid: {', '.join(validation['issues'])}")
            cur.execute("""
                UPDATE clients
                SET hierarchy_status='valid', updated_at=now()
                WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL
            """, (tid, client_id))
            log_activity(
                conn,
                "client",
                client_id,
                "update",
                f"Klient upraven: {list(data.keys())}",
                tenant_id=tid,
                user_id=actor_user_id,
                source_channel="crm",
            )
            conn.commit()
        return {"status":"updated"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500,str(e))
    finally:
        release_conn(conn)

@app.delete("/crm/clients/{client_id}")
async def archive_client(client_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE clients SET deleted_at=now(),status='archived' WHERE id=%s",(client_id,))
            log_activity(conn,"client",client_id,"archive","Klient archivovan")
            conn.commit()
        return {"status":"archived"}
    finally: release_conn(conn)

@app.post("/crm/clients/sync-contacts")
async def sync_contacts(data: dict, request: Request):
    """Store imported contacts per user and decide which of them should exist as shared server clients."""
    tenant_id = get_request_tenant_id(request)
    user_id = request.state.user.get("user_id")
    filter_uk = bool(data.get("filter_uk", False))
    contacts = data.get("contacts", [])
    if not user_id:
        raise HTTPException(401, "Authenticated user is required")
    if not contacts:
        raise HTTPException(400, "No contacts provided")

    def is_uk_number(phone: str) -> bool:
        clean = normalize_contact_phone(phone)
        return clean.startswith(("07", "01", "02"))

    conn = get_db_conn()
    errors = []
    try:
        with conn.cursor() as cur:
            seen_keys = set()
            for contact in contacts:
                name = clean_contact_display_name(contact.get("name") or contact.get("display_name"))
                phone = (contact.get("phone") or "").strip()
                email = (contact.get("email") or "").strip()
                if not name and not phone and not email:
                    continue
                if filter_uk and phone and not is_uk_number(phone):
                    continue
                contact_key = contact.get("contact_key") or build_contact_key(name, phone, email)
                if not contact_key or contact_key in seen_keys:
                    continue
                seen_keys.add(contact_key)
                selected_flag = contact.get("selected_as_client")
                address_fields = extract_contact_address_fields(contact)
                cur.execute("""INSERT INTO user_contact_sync
                    (
                        tenant_id, user_id, contact_key, display_name, phone_primary, email_primary,
                        address, address_line1, city, postcode, country,
                        normalized_phone, normalized_email, is_client, last_seen_at, updated_at
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s, FALSE),now(),now())
                    ON CONFLICT (tenant_id, user_id, contact_key) DO UPDATE SET
                        display_name=EXCLUDED.display_name,
                        phone_primary=EXCLUDED.phone_primary,
                        email_primary=EXCLUDED.email_primary,
                        address=EXCLUDED.address,
                        address_line1=EXCLUDED.address_line1,
                        city=EXCLUDED.city,
                        postcode=EXCLUDED.postcode,
                        country=EXCLUDED.country,
                        normalized_phone=EXCLUDED.normalized_phone,
                        normalized_email=EXCLUDED.normalized_email,
                        is_client=CASE WHEN %s IS NULL THEN user_contact_sync.is_client ELSE %s END,
                        last_seen_at=now(),
                        updated_at=now()""",
                    (
                        tenant_id,
                        user_id,
                        contact_key,
                        name or phone or email or "Contact",
                        phone or None,
                        email or None,
                        address_fields.get("address"),
                        address_fields.get("address_line1"),
                        address_fields.get("city"),
                        address_fields.get("postcode"),
                        address_fields.get("country"),
                        normalize_contact_phone(phone) or None,
                        normalize_email(email) or None,
                        selected_flag,
                        selected_flag,
                        selected_flag,
                    ))
            conn.commit()

        with conn.cursor() as cur:
            cur.execute("""SELECT contact_key FROM user_contact_sync
                WHERE tenant_id=%s AND user_id=%s""", (tenant_id, user_id))
            contact_keys = [row["contact_key"] for row in cur.fetchall()]

        for contact_key in contact_keys:
            try:
                reconcile_contact_selection(conn, tenant_id, user_id, contact_key)
            except Exception as reconcile_error:
                errors.append(f"{contact_key}: {reconcile_error}")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("""SELECT ucs.contact_key, ucs.display_name, ucs.phone_primary, ucs.email_primary,
                                  ucs.address, ucs.address_line1, ucs.city, ucs.postcode, ucs.country,
                                  ucs.is_client, ucs.linked_client_id, c.display_name AS linked_client_name
                FROM user_contact_sync ucs
                LEFT JOIN clients c ON c.id = ucs.linked_client_id AND c.deleted_at IS NULL
                WHERE ucs.tenant_id=%s AND ucs.user_id=%s
                ORDER BY LOWER(ucs.display_name), LOWER(COALESCE(ucs.email_primary,'')), LOWER(COALESCE(ucs.phone_primary,''))""",
                (tenant_id, user_id))
            rows = [dict(row) for row in cur.fetchall()]

        return {
            "total_contacts": len(rows),
            "selected_clients": sum(1 for row in rows if row.get("is_client")),
            "contacts": [
                {
                    "contact_key": row["contact_key"],
                    "name": row["display_name"],
                    "phone": row.get("phone_primary"),
                    "email": row.get("email_primary"),
                    "address": row.get("address"),
                    "address_line1": row.get("address_line1"),
                    "city": row.get("city"),
                    "postcode": row.get("postcode"),
                    "country": row.get("country"),
                    "billing_address_line1": row.get("address_line1") or row.get("address"),
                    "billing_city": row.get("city"),
                    "billing_postcode": row.get("postcode"),
                    "billing_country": row.get("country"),
                    "selected_as_client": bool(row.get("is_client")),
                    "linked_client_id": row.get("linked_client_id"),
                    "linked_client_name": row.get("linked_client_name"),
                }
                for row in rows
            ],
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.post("/crm/clients/{client_id}/notes")
async def add_client_note(client_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO client_notes (client_id,note,created_by) VALUES (%s,%s,%s) RETURNING id,note,created_by,created_at::text",
                (client_id,data.get("note",""),data.get("created_by","Marek")))
            note = dict(cur.fetchone())
            log_activity(conn,"client",client_id,"note",f"Poznamka: {data.get('note','')[:50]}")
            conn.commit()
        return note
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== REST API: JOBS ==========
@app.get("/crm/jobs")
async def get_jobs(request: Request, client_id: Optional[int] = None, status: Optional[str] = None):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """SELECT j.id,j.job_number,j.job_title,j.job_status,j.client_id,j.property_id,j.quote_id,
                j.start_date_planned::text,j.planned_start_at::text,j.planned_end_at::text,
                j.assigned_user_id,j.assigned_to,j.next_action_task_id,j.hierarchy_status,
                j.handover_note,j.handed_over_by,j.handed_over_at::text,
                COALESCE(j.calendar_sync_enabled, TRUE) AS calendar_sync_enabled,
                j.created_at::text,j.updated_at::text,c.display_name as client_name
                FROM jobs j
                LEFT JOIN clients c ON j.client_id=c.id
                WHERE j.deleted_at IS NULL AND j.tenant_id=%s"""
            params = [tid]
            if client_id: sql += " AND j.client_id=%s"; params.append(client_id)
            if status: sql += " AND j.job_status=%s"; params.append(status)
            sql += " ORDER BY j.created_at DESC"
            cur.execute(sql,params); return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/jobs/{job_id}")
async def get_job_detail(job_id: int, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT *
                FROM jobs WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL""",(job_id, tid))
            job = cur.fetchone()
            if not job: raise HTTPException(404)
            cur.execute("""SELECT *
                FROM tasks WHERE job_id=%s AND tenant_id=%s ORDER BY created_at DESC""",(job_id, tid))
            tasks = cur.fetchall()
            cur.execute("""SELECT id,job_id,note,note_type,created_by,created_at::text,updated_at::text
                FROM job_notes WHERE job_id=%s AND tenant_id=%s ORDER BY created_at DESC""",(job_id, tid))
            notes = cur.fetchall()
            cur.execute("""SELECT id,entity_type,entity_id,filename,description,photo_type,file_path,thumbnail_base64,
                    created_by,created_at::text
                FROM photos
                WHERE tenant_id=%s AND entity_type='job' AND entity_id=%s
                ORDER BY created_at DESC""", (tid, str(job_id)))
            photos = [map_photo_row_to_job_photo(dict(r)) for r in cur.fetchall()]
            cur.execute("""SELECT id,entity_id,action,description,user_name,created_at::text
                FROM activity_timeline
                WHERE tenant_id=%s AND entity_type='job' AND entity_id=%s
                ORDER BY created_at DESC""", (tid, str(job_id)))
            audit_rows = [map_audit_row_to_job_audit(dict(r)) for r in cur.fetchall()]
            return {
                "job":dict(job),
                "tasks":[dict(t) for t in tasks],
                "notes":[dict(n) for n in notes],
                "photos": photos,
                "audit_log": audit_rows,
            }
    finally: release_conn(conn)

@app.post("/crm/jobs")
async def create_job(data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        ok, msg = check_subscription_limit(conn, tid, "jobs")
        if not ok:
            raise HTTPException(429, msg)
        code = f"JOB-{uuid.uuid4().hex[:6].upper()}"
        first_action = data.get("first_action")
        if not isinstance(first_action, dict):
            raise HTTPException(422, "Job must include first_action")
        actor_user_id = request.state.user.get("user_id")
        actor_name = get_user_display_name(conn, tid, actor_user_id) or "system"
        assigned_user_id, assigned_to = resolve_assigned_user(conn, tid, data.get("assigned_user_id"), data.get("assigned_to"))
        owner = validate_active_user(conn, tid, assigned_user_id, "job owner")
        planning_start, planning_end = planning_window_from_values(
            data.get("planned_start_at"), data.get("planned_end_at"), data.get("start_date")
        )
        handover_note = (data.get("handover_note") or "").strip() or None
        handover_by = actor_name
        handover_at = datetime.utcnow() if (handover_note or assigned_to) else None
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO jobs (
                    tenant_id, job_number, client_id, property_id, job_title, job_status,
                    start_date_planned, planned_start_at, planned_end_at,
                    assigned_user_id, assigned_to, next_action_task_id, hierarchy_status,
                    handover_note, handed_over_by, handed_over_at, calendar_sync_enabled
                ) VALUES (%s,%s,%s,%s,%s,'nova',%s,%s,%s,%s,%s,NULL,'pending',%s,%s,%s,%s) RETURNING id""",
                (
                    tid,
                    code,
                    data.get("client_id"),
                    data.get("property_id",data.get("client_id")),
                    data.get("title","Zakazka"),
                    data.get("start_date"),
                    planning_start,
                    planning_end,
                    int(owner["id"]),
                    assigned_to or owner["display_name"],
                    handover_note,
                    handover_by,
                    handover_at,
                    data.get("calendar_sync_enabled", True),
                ))
            jid = cur.fetchone()['id']
            next_action = create_workflow_task(
                conn,
                tid,
                first_action,
                actor_name=actor_name,
                default_client_id=data.get("client_id"),
                default_client_name=data.get("client_name"),
                default_job_id=jid,
                default_property_id=data.get("property_id", data.get("client_id")),
                default_property_address=data.get("property_address"),
                source="job_first_action",
            )
            set_job_next_action(conn, tid, jid, str(next_action["id"]))
            validation = validate_job_hierarchy(conn, tid, jid)
            if not validation["valid"]:
                raise HTTPException(422, f"Job hierarchy invalid: {', '.join(validation['issues'])}")
            log_activity(
                conn,
                "job",
                jid,
                "create",
                f"Zakazka {code} vytvorena",
                tenant_id=tid,
                user_id=actor_user_id,
                source_channel="crm",
                details={
                    "assigned_user_id": int(owner["id"]),
                    "next_action_task_id": str(next_action["id"]),
                },
            )
            conn.commit()
        return {
            "id": jid,
            "job_number": code,
            "status": "created",
            "assigned_user_id": int(owner["id"]),
            "next_action_task_id": str(next_action["id"]),
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500,str(e))
    finally:
        release_conn(conn)

@app.put("/crm/jobs/{job_id}")
async def update_job(job_id: int, data: dict, request: Request):
    conn = get_db_conn()
    try:
        tid = get_request_tenant_id(request)
        actor_user_id = request.state.user.get("user_id")
        with conn.cursor() as cur:
            # Validate state transition if status is being changed
            if "job_status" in data:
                cur.execute("SELECT job_status FROM jobs WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL",(tid, job_id))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404,"Job not found")
                err = validate_state_transition(row["job_status"], data["job_status"], JOB_TRANSITIONS, "Job")
                if err:
                    raise HTTPException(422, err)
            sets = []; vals = []
            if "assigned_user_id" in data or "assigned_to" in data:
                assigned_user_id, assigned_to = resolve_assigned_user(conn, tid, data.get("assigned_user_id"), data.get("assigned_to"))
                owner = validate_active_user(conn, tid, assigned_user_id, "job owner")
                sets.append("assigned_user_id=%s"); vals.append(int(owner["id"]))
                sets.append("assigned_to=%s"); vals.append(assigned_to or owner["display_name"])
                sets.append("handed_over_by=%s"); vals.append(get_user_display_name(conn, tid, actor_user_id))
                sets.append("handed_over_at=%s"); vals.append(datetime.utcnow())
            if "next_action_task_id" in data:
                if not data.get("next_action_task_id"):
                    raise HTTPException(422, "Job next_action_task_id cannot be empty")
                candidate = get_valid_job_next_action_task(conn, tid, job_id, str(data["next_action_task_id"]))
                if not candidate:
                    raise HTTPException(422, "Job next action must point to an open planned job task")
                sets.append("next_action_task_id=%s"); vals.append(str(data["next_action_task_id"]))
            if "handover_note" in data:
                sets.append("handover_note=%s"); vals.append((data.get("handover_note") or "").strip() or None)
                sets.append("handed_over_by=%s"); vals.append(get_user_display_name(conn, tid, actor_user_id))
                sets.append("handed_over_at=%s"); vals.append(datetime.utcnow())
            if "planned_start_at" in data or "planned_end_at" in data or "start_date_planned" in data:
                planning_start, planning_end = planning_window_from_values(
                    data.get("planned_start_at"), data.get("planned_end_at"), data.get("start_date_planned")
                )
                if "planned_start_at" in data or "start_date_planned" in data:
                    sets.append("planned_start_at=%s"); vals.append(planning_start)
                if "planned_end_at" in data or "planned_start_at" in data:
                    sets.append("planned_end_at=%s"); vals.append(planning_end)
            for k in ["job_title","job_status","start_date_planned","calendar_sync_enabled"]:
                if k in data: sets.append(f"{k}=%s"); vals.append(data[k])
            if not sets:
                raise HTTPException(400)
            sets.append("updated_at=now()"); vals.extend([tid, job_id])
            cur.execute(f"UPDATE jobs SET {','.join(sets)} WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL",vals)
            if cur.rowcount == 0:
                raise HTTPException(404, "Job not found")
            validation = validate_job_hierarchy(conn, tid, job_id)
            if not validation["valid"]:
                raise HTTPException(422, f"Job hierarchy invalid: {', '.join(validation['issues'])}")
            cur.execute("""
                UPDATE jobs
                SET hierarchy_status='valid', updated_at=now()
                WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL
            """, (tid, job_id))
            log_activity(
                conn,
                "job",
                job_id,
                "update",
                f"Zakazka upravena: {list(data.keys())}",
                tenant_id=tid,
                user_id=actor_user_id,
                source_channel="crm",
            )
            conn.commit()
        return {"status":"updated"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500,str(e))
    finally:
        release_conn(conn)

# ========== REST API: TASKS ==========
@app.get("/crm/tasks")
async def get_tasks(request: Request, status: Optional[str]=None, client_id: Optional[int]=None, job_id: Optional[int]=None, completed: Optional[bool]=None):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """SELECT * FROM tasks WHERE tenant_id=%s"""; params = [tid]
            if status: sql += " AND status=%s"; params.append(status)
            if client_id: sql += " AND client_id=%s"; params.append(client_id)
            if job_id: sql += " AND job_id=%s"; params.append(job_id)
            if completed is not None: sql += " AND is_completed=%s"; params.append(completed)
            sql += " ORDER BY CASE priority WHEN 'kriticka' THEN 1 WHEN 'urgentni' THEN 2 WHEN 'vysoka' THEN 3 WHEN 'bezna' THEN 4 ELSE 5 END, created_at DESC"
            cur.execute(sql,params); return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/tasks")
async def api_create_task(data: dict, request: Request):
    conn = get_db_conn()
    try:
        tenant_id = get_request_tenant_id(request)
        actor_user_id = request.state.user.get("user_id")
        actor_name = get_user_display_name(conn, tenant_id, actor_user_id) or "system"
        task = create_workflow_task(
            conn,
            tenant_id,
            data,
            actor_name=actor_name,
            default_client_id=data.get("client_id"),
            default_client_name=data.get("client_name"),
            default_job_id=data.get("job_id"),
            default_property_id=data.get("property_id"),
            default_property_address=data.get("property_address"),
            source=data.get("source") or "manualne",
        )
        if data.get("set_as_next_action") is True:
            if task.get("job_id"):
                set_job_next_action(conn, tenant_id, int(task["job_id"]), str(task["id"]))
            elif task.get("client_id"):
                set_client_next_action(conn, tenant_id, int(task["client_id"]), str(task["id"]))
            else:
                raise HTTPException(422, "set_as_next_action requires client_id or job_id")
        with conn.cursor() as cur:
            if task.get("job_id"):
                validation = validate_job_hierarchy(conn, tenant_id, int(task["job_id"]))
                if not validation["valid"] and data.get("set_as_next_action") is True:
                    raise HTTPException(422, f"Job hierarchy invalid: {', '.join(validation['issues'])}")
            elif task.get("client_id"):
                validation = validate_client_hierarchy(conn, tenant_id, int(task["client_id"]))
                if not validation["valid"] and data.get("set_as_next_action") is True:
                    raise HTTPException(422, f"Client hierarchy invalid: {', '.join(validation['issues'])}")
            log_activity(
                conn,
                "task",
                task["id"],
                "create",
                f"Ukol '{data.get('title','')}' vytvoren",
                tenant_id=tenant_id,
                user_id=actor_user_id,
                source_channel="crm",
            )
            conn.commit()
        return task
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500,str(e))
    finally:
        release_conn(conn)

@app.put("/crm/tasks/{task_id}")
async def update_task(task_id: str, data: dict, request: Request):
    conn = get_db_conn()
    try:
        sets = []; vals = []
        tenant_id = get_request_tenant_id(request)
        actor_user_id = request.state.user.get("user_id")
        actor_name = get_user_display_name(conn, tenant_id, actor_user_id) or "system"
        current_task = get_task_row(conn, tenant_id, task_id)
        if not current_task:
            raise HTTPException(404, "Task not found")
        new_assigned_user_id = current_task.get("assigned_user_id")
        new_assigned_to = current_task.get("assigned_to")
        if "assigned_user_id" in data or "assigned_to" in data:
            assigned_user_id, assigned_to = resolve_assigned_user(conn, tenant_id, data.get("assigned_user_id"), data.get("assigned_to"))
            assignee = validate_active_user(conn, tenant_id, assigned_user_id, "task assignee")
            sets.append("assigned_to=%s"); vals.append(assigned_to)
            sets.append("assigned_user_id=%s"); vals.append(int(assignee["id"]))
            sets.append("delegated_by=%s"); vals.append(actor_name)
            new_assigned_user_id = int(assignee["id"])
            new_assigned_to = assigned_to
        else:
            validate_active_user(conn, tenant_id, current_task.get("assigned_user_id"), "task assignee")
        merged_planning_payload = {
            "planned_start_at": data.get("planned_start_at", current_task.get("planned_start_at")),
            "planned_end_at": data.get("planned_end_at", current_task.get("planned_end_at")),
            "planned_date": data.get("planned_date", current_task.get("planned_date")),
            "deadline": data.get("deadline", current_task.get("deadline")),
        }
        planning_start, planning_end, deadline = validate_task_planning(merged_planning_payload)
        if "planned_start_at" in data or "planned_end_at" in data or "planned_date" in data or "deadline" in data:
            if "planned_start_at" in data or "planned_date" in data or "deadline" in data:
                sets.append("planned_start_at=%s"); vals.append(planning_start)
            if "planned_end_at" in data or "planned_start_at" in data:
                sets.append("planned_end_at=%s"); vals.append(planning_end)
            if "deadline" in data:
                sets.append("deadline=%s"); vals.append(deadline)
        for k in ["title","description","task_type","status","priority","deadline","result","is_completed","actual_minutes","planned_date","planning_note","reminder_for_assignee_only","calendar_sync_enabled"]:
            if k in data and not (k == "deadline" and ("planned_start_at" in data or "planned_end_at" in data or "planned_date" in data or "deadline" in data)):
                sets.append(f"{k}=%s"); vals.append(data[k])
        if "notes" in data:
            sets.append("notes=%s"); vals.append(json.dumps(data["notes"]))
        if "checklist" in data:
            sets.append("checklist=%s"); vals.append(json.dumps(data["checklist"]))
        will_close = bool(data.get("is_completed") is True or data.get("status") in ("hotovo", "zruseno"))
        links = get_task_next_action_links(conn, tenant_id, task_id)
        if will_close and links:
            replace_next_action_links(
                conn,
                tenant_id,
                current_task,
                replacement_task_id=data.get("replacement_task_id"),
                replacement_task_payload=data.get("replacement_task_payload"),
                actor_user_id=actor_user_id,
                actor_name=actor_name,
            )
        if not sets:
            raise HTTPException(400, "No changes provided")
        sets.append("updated_at=now()"); vals.extend([tenant_id, task_id])
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tasks SET {','.join(sets)} WHERE tenant_id=%s AND id=%s",vals)
            if cur.rowcount == 0:
                raise HTTPException(404, "Task not found")
            for link in links:
                validation = (
                    validate_client_hierarchy(conn, tenant_id, int(link["entity_id"]))
                    if link["entity_type"] == "client"
                    else validate_job_hierarchy(conn, tenant_id, int(link["entity_id"]))
                )
                if not validation["valid"]:
                    raise HTTPException(422, f"{link['entity_type'].capitalize()} hierarchy invalid: {', '.join(validation['issues'])}")
            log_activity(
                conn,
                "task",
                task_id,
                "update",
                f"Ukol upraven: {list(data.keys())}",
                tenant_id=tenant_id,
                user_id=actor_user_id,
                source_channel="crm",
            )
            conn.commit()
        return {"status":"updated"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500,str(e))
    finally:
        release_conn(conn)

@app.get("/crm/calendar-feed")
async def get_calendar_feed(request: Request, days: int = 30):
    user = ensure_request_permissions(request, "calendar_read")
    tenant_id = user["tenant_id"]
    current_user_id = user.get("user_id")
    conn = get_db_conn()
    try:
        entries = []
        cutoff = datetime.utcnow() - timedelta(days=1)
        with conn.cursor() as cur:
            cur.execute("""SELECT j.id, j.job_title, j.job_status, j.client_id, c.display_name AS client_name,
                    j.start_date_planned::text, j.planned_start_at::text, j.planned_end_at::text,
                    j.assigned_user_id, j.assigned_to, j.handover_note, COALESCE(j.calendar_sync_enabled, TRUE) AS calendar_sync_enabled
                FROM jobs j
                LEFT JOIN clients c ON c.id = j.client_id
                WHERE j.tenant_id=%s AND j.deleted_at IS NULL
                ORDER BY COALESCE(j.planned_start_at, j.created_at) DESC""", (tenant_id,))
            for row in cur.fetchall():
                entry = build_calendar_entry("job", dict(row), current_user_id)
                if not entry:
                    continue
                start_dt = parse_planning_datetime(entry.get("planned_start_at"))
                if start_dt and start_dt >= cutoff and start_dt <= datetime.utcnow() + timedelta(days=days):
                    entries.append(entry)

            cur.execute("""SELECT t.id, t.title, t.status, t.client_id, t.client_name, t.job_id,
                    j.job_title, t.deadline, t.planned_date, t.planned_start_at::text, t.planned_end_at::text,
                    t.assigned_user_id, t.assigned_to, t.planning_note,
                    COALESCE(t.reminder_for_assignee_only, TRUE) AS reminder_for_assignee_only,
                    COALESCE(t.calendar_sync_enabled, TRUE) AS calendar_sync_enabled
                FROM tasks t
                LEFT JOIN jobs j ON j.id = t.job_id
                WHERE t.tenant_id=%s AND COALESCE(t.is_completed, FALSE)=FALSE
                ORDER BY COALESCE(t.planned_start_at, t.created_at) DESC""", (tenant_id,))
            for row in cur.fetchall():
                entry = build_calendar_entry("task", dict(row), current_user_id, row.get("job_title"))
                if not entry:
                    continue
                start_dt = parse_planning_datetime(entry.get("planned_start_at"))
                if start_dt and start_dt >= cutoff and start_dt <= datetime.utcnow() + timedelta(days=days):
                    entries.append(entry)

        entries.sort(key=lambda item: item.get("planned_start_at") or "")
        return entries
    finally:
        release_conn(conn)

@app.delete("/crm/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    conn = get_db_conn()
    try:
        tenant_id = get_request_tenant_id(request)
        actor_user_id = request.state.user.get("user_id")
        if get_task_next_action_links(conn, tenant_id, task_id):
            raise HTTPException(422, "Current next action task cannot be deleted")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE tenant_id=%s AND id=%s",(tenant_id, task_id))
            if cur.rowcount == 0:
                raise HTTPException(404, "Task not found")
            log_activity(
                conn,
                "task",
                task_id,
                "delete",
                "Ukol smazan",
                tenant_id=tenant_id,
                user_id=actor_user_id,
                source_channel="crm",
            )
            conn.commit()
        return {"status":"deleted"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

# ========== REST API: LEADS ==========
@app.get("/crm/leads")
async def get_leads(request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,lead_code,lead_source,contact_name,contact_email,contact_phone,description,notes,status,client_id,job_id,received_at::text,updated_at::text FROM leads WHERE tenant_id=%s ORDER BY received_at DESC",(tid,))
            return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/leads")
async def create_lead(request: Request, data: dict):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        code = f"LED-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (tenant_id,lead_code,lead_source,status,contact_name,contact_email,contact_phone,description,notes)
                VALUES (%s,%s,%s,'new',%s,%s,%s,%s,%s) RETURNING id,lead_code,status,received_at::text""",
                (tid,code,data.get("lead_source",data.get("source","jiny")),data.get("contact_name",data.get("name")),
                 data.get("contact_email",data.get("email")),data.get("contact_phone",data.get("phone")),
                 data.get("description"),data.get("notes")))
            lead = dict(cur.fetchone())
            log_activity(conn,"lead",lead['id'],"create",f"Lead {code} vytvoren")
            conn.commit()
        return lead
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.get("/crm/leads/{lead_id}")
async def get_lead_detail(lead_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id=%s",(lead_id,))
            lead = cur.fetchone()
            if not lead: raise HTTPException(404)
            return dict(lead)
    finally: release_conn(conn)

@app.put("/crm/leads/{lead_id}")
async def update_lead(lead_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if "status" in data:
                cur.execute("SELECT status FROM leads WHERE id=%s",(lead_id,))
                row = cur.fetchone()
                if not row: raise HTTPException(404,"Lead not found")
                err = validate_state_transition(row["status"], data["status"], LEAD_TRANSITIONS, "Lead")
                if err: raise HTTPException(422, err)
            sets = []; vals = []
            for k in ["status","lead_source","contact_name","contact_email","contact_phone","description","notes"]:
                if k in data: sets.append(f"{k}=%s"); vals.append(data[k])
            if not sets: raise HTTPException(400)
            sets.append("updated_at=now()"); vals.append(lead_id)
            cur.execute(f"UPDATE leads SET {','.join(sets)} WHERE id=%s",vals)
            log_activity(conn,"lead",lead_id,"update",f"Lead upraven: {list(data.keys())}")
            conn.commit()
        return {"status":"updated"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.post("/crm/leads/{lead_id}/convert-to-client")
async def convert_lead_to_client(lead_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id=%s",(lead_id,))
            lead = cur.fetchone()
            if not lead: raise HTTPException(404,"Lead nenalezen")
            name = clean_contact_display_name(data.get("name") or lead.get("contact_name")) or "Nový klient"
            email = data.get("email",lead.get("contact_email"))
            phone = data.get("phone",lead.get("contact_phone"))
            code = f"CL-{uuid.uuid4().hex[:6].upper()}"
            cur.execute("INSERT INTO clients (client_code,client_type,display_name,email_primary,phone_primary,status) VALUES (%s,'domestic',%s,%s,%s,'active') RETURNING id",
                (code,name,email,phone))
            cid = cur.fetchone()['id']
            cur.execute("UPDATE leads SET status='preveden_na_klienta',client_id=%s,updated_at=now() WHERE id=%s",(cid,lead_id))
            log_activity(conn,"lead",lead_id,"convert","Lead preveden na klienta "+code)
            log_activity(conn,"client",cid,"create","Klient vytvoren z leadu "+lead.get('lead_code',''))
            conn.commit()
        return {"client_id":cid,"client_code":code,"status":"converted"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.post("/crm/leads/{lead_id}/convert-to-job")
async def convert_lead_to_job(lead_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id=%s",(lead_id,))
            lead = cur.fetchone()
            if not lead: raise HTTPException(404)
            jcode = f"JOB-{uuid.uuid4().hex[:6].upper()}"
            title = data.get("title","Zakázka z leadu "+lead.get('lead_code',''))
            client_id = data.get("client_id",lead.get("client_id"))
            cur.execute("INSERT INTO jobs (job_number,client_id,job_title,job_status) VALUES (%s,%s,%s,'nova') RETURNING id",
                (jcode,client_id,title))
            jid = cur.fetchone()['id']
            cur.execute("UPDATE leads SET status='preveden_na_zakazku',job_id=%s,updated_at=now() WHERE id=%s",(jid,lead_id))
            log_activity(conn,"lead",lead_id,"convert","Lead preveden na zakazku "+jcode)
            log_activity(conn,"job",jid,"create","Zakazka vytvorena z leadu "+lead.get('lead_code',''))
            conn.commit()
        return {"job_id":jid,"job_number":jcode,"status":"converted"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== REST API: INVOICES ==========
@app.get("/crm/invoices")
async def get_invoices(request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT i.id,i.invoice_number,i.client_id,c.display_name as client_name,i.grand_total,i.status,i.due_date::text,i.created_at::text FROM invoices i LEFT JOIN clients c ON i.client_id=c.id WHERE i.tenant_id=%s ORDER BY i.created_at DESC",(tid,))
            return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/invoices")
async def create_invoice(data: dict):
    conn = get_db_conn()
    try:
        code = f"INV-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("INSERT INTO invoices (invoice_number,client_id,grand_total,status,due_date) VALUES (%s,%s,%s,'draft',%s) RETURNING id,invoice_number,status",
                (code,data.get("client_id"),data.get("grand_total",0),data.get("due_date")))
            inv = dict(cur.fetchone())
            log_activity(conn,"invoice",inv['id'],"create",f"Faktura {code}")
            conn.commit()
        return inv
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.get("/crm/contact-sections")
async def get_contact_sections(request: Request):
    user = ensure_request_permissions(request, "contacts_read")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT section_code, display_name, is_default, sort_order
                FROM contact_sections
                WHERE tenant_id=%s AND is_active=TRUE
                ORDER BY sort_order, LOWER(display_name)""", (user["tenant_id"],))
            return [dict(row) for row in cur.fetchall()]
    finally:
        release_conn(conn)

@app.post("/crm/contact-sections")
async def create_contact_section(data: dict, request: Request):
    user = ensure_request_permissions(request, "contacts_write")
    tenant_id = user["tenant_id"]
    display_name = clean_contact_display_name(data.get("display_name"))
    if not display_name:
        raise HTTPException(400, "display_name required")
    section_code = normalize_section_code(data.get("section_code") or display_name)
    if not section_code:
        raise HTTPException(400, "section_code required")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM contact_sections WHERE tenant_id=%s", (tenant_id,))
            max_sort = int((cur.fetchone() or {}).get("max_sort") or 0)
            explicit_sort = data.get("sort_order")
            sort_val = int(explicit_sort) if explicit_sort is not None else (max_sort + 10)
            cur.execute("""INSERT INTO contact_sections (tenant_id, section_code, display_name, is_default, sort_order, is_active, updated_at)
                VALUES (%s,%s,%s,FALSE,%s,TRUE,now())
                ON CONFLICT (tenant_id, section_code) DO UPDATE SET
                    display_name=EXCLUDED.display_name,
                    sort_order=EXCLUDED.sort_order,
                    is_active=TRUE,
                    updated_at=now()
                RETURNING section_code, display_name, is_default, sort_order""",
                (tenant_id, section_code, display_name, sort_val))
            row = dict(cur.fetchone())
            conn.commit()
            return row
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.get("/crm/contacts")
async def get_shared_contacts(request: Request):
    user = ensure_request_permissions(request, "contacts_read")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT c.id, c.section_code, s.display_name AS section_name, c.display_name, c.company_name,
                                  c.phone_primary, c.email_primary, c.address, c.address_line1, c.city, c.postcode,
                                  c.country, c.notes, c.source, c.created_at::text, c.updated_at::text
                FROM shared_contacts c
                LEFT JOIN contact_sections s ON s.tenant_id=c.tenant_id AND s.section_code=c.section_code
                WHERE c.tenant_id=%s AND c.deleted_at IS NULL
                  AND (c.section_code NOT IN ('private','other')
                       OR c.owner_user_id IS NULL
                       OR c.owner_user_id=%s)
                ORDER BY s.sort_order, LOWER(c.display_name), c.id""", (user["tenant_id"], user.get("user_id")))
            return [dict(row) for row in cur.fetchall()]
    finally:
        release_conn(conn)

@app.post("/crm/contacts")
async def create_shared_contact(data: dict, request: Request):
    user = ensure_request_permissions(request, "contacts_write")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            row, created = merge_shared_contact(cur, user["tenant_id"], user["user_id"], data, source=data.get("source") or "manual")
            log_activity(conn, "shared_contact", row["id"], "create" if created else "merge", f"Contact {row['display_name']} saved", tenant_id=user["tenant_id"], user_id=user["user_id"])
            conn.commit()
            return row
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.put("/crm/contacts/{contact_id}")
async def update_shared_contact(contact_id: int, data: dict, request: Request):
    user = ensure_request_permissions(request, "contacts_write")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM shared_contacts WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL", (contact_id, user["tenant_id"]))
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(404, "Contact not found")
            existing = dict(existing)
            section_code = normalize_section_code(data.get("section_code") or existing.get("section_code"))
            ensure_contact_section(cur, user["tenant_id"], section_code)
            display_name = clean_contact_display_name(data.get("display_name") or existing.get("display_name"))
            if not display_name:
                raise HTTPException(400, "display_name required")
            phone_primary = (data.get("phone_primary") or existing.get("phone_primary") or "").strip() or None
            email_primary = (data.get("email_primary") or existing.get("email_primary") or "").strip() or None
            address_fields = extract_contact_address_fields({**existing, **data})
            cur.execute("""UPDATE shared_contacts
                SET section_code=%s,
                    display_name=%s,
                    company_name=%s,
                    phone_primary=%s,
                    email_primary=%s,
                    address=%s,
                    address_line1=%s,
                    city=%s,
                    postcode=%s,
                    country=%s,
                    notes=%s,
                    normalized_phone=%s,
                    normalized_email=%s,
                    updated_by=%s,
                    updated_at=now()
                WHERE id=%s AND tenant_id=%s
                RETURNING *""",
                (
                    section_code,
                    display_name,
                    (data.get("company_name") if "company_name" in data else existing.get("company_name")) or None,
                    phone_primary,
                    email_primary,
                    address_fields.get("address"),
                    address_fields.get("address_line1"),
                    address_fields.get("city"),
                    address_fields.get("postcode"),
                    address_fields.get("country"),
                    (data.get("notes") if "notes" in data else existing.get("notes")) or None,
                    normalize_phone(phone_primary) or None,
                    normalize_email(email_primary) or None,
                    user["user_id"],
                    contact_id,
                    user["tenant_id"],
                ))
            row = dict(cur.fetchone())
            log_activity(conn, "shared_contact", row["id"], "update", f"Contact {row['display_name']} updated", tenant_id=user["tenant_id"], user_id=user["user_id"])
            conn.commit()
            return row
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.delete("/crm/contacts/{contact_id}")
async def delete_shared_contact(contact_id: int, request: Request):
    user = ensure_request_permissions(request, "contacts_write")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE shared_contacts
                SET deleted_at=now(), updated_at=now(), updated_by=%s
                WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL
                RETURNING id, display_name""", (user["user_id"], contact_id, user["tenant_id"]))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Contact not found")
            log_activity(conn, "shared_contact", row["id"], "archive", f"Contact {row['display_name']} archived", tenant_id=user["tenant_id"], user_id=user["user_id"])
            conn.commit()
            return {"status": "archived", "id": row["id"]}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.get("/crm/contacts/duplicates")
async def find_contact_duplicates(request: Request):
    """Find duplicate contacts in shared_contacts (same phone or similar name)."""
    user = ensure_request_permissions(request, "contacts_read")
    tenant_id = user.get("tenant_id", 1)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Find same normalized_phone
            cur.execute("""
                SELECT a.id as id1, a.display_name as name1, a.phone_primary as phone1,
                       a.section_code as section1,
                       b.id as id2, b.display_name as name2, b.phone_primary as phone2,
                       b.section_code as section2,
                       'same_phone' as reason
                FROM shared_contacts a
                JOIN shared_contacts b ON b.normalized_phone = a.normalized_phone
                    AND b.id > a.id
                    AND b.tenant_id = a.tenant_id
                WHERE a.tenant_id=%s AND a.deleted_at IS NULL AND b.deleted_at IS NULL
                  AND a.normalized_phone IS NOT NULL AND a.normalized_phone != ''
                LIMIT 50
            """, (tenant_id,))
            phone_dupes = [dict(r) for r in cur.fetchall()]

            # Find similar names (same first token, length similar)
            cur.execute("""
                SELECT a.id as id1, a.display_name as name1, a.phone_primary as phone1,
                       a.section_code as section1,
                       b.id as id2, b.display_name as name2, b.phone_primary as phone2,
                       b.section_code as section2,
                       'similar_name' as reason
                FROM shared_contacts a
                JOIN shared_contacts b ON b.id > a.id AND b.tenant_id = a.tenant_id
                WHERE a.tenant_id=%s AND a.deleted_at IS NULL AND b.deleted_at IS NULL
                  AND a.normalized_phone IS DISTINCT FROM b.normalized_phone
                  AND lower(split_part(a.display_name,' ',1)) = lower(split_part(b.display_name,' ',1))
                  AND abs(length(a.display_name) - length(b.display_name)) < 8
                  AND similarity(lower(a.display_name), lower(b.display_name)) > 0.6
                LIMIT 50
            """, (tenant_id,))
            name_dupes = [dict(r) for r in cur.fetchall()]

        all_dupes = phone_dupes + name_dupes
        return {"duplicates": all_dupes, "count": len(all_dupes)}
    except Exception as e:
        conn.rollback()
        # similarity() requires pg_trgm extension - fallback without it
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT a.id as id1, a.display_name as name1, a.phone_primary as phone1,
                           a.section_code as section1,
                           b.id as id2, b.display_name as name2, b.phone_primary as phone2,
                           b.section_code as section2,
                           'same_phone' as reason
                    FROM shared_contacts a
                    JOIN shared_contacts b ON b.normalized_phone = a.normalized_phone
                        AND b.id > a.id AND b.tenant_id = a.tenant_id
                    WHERE a.tenant_id=%s AND a.deleted_at IS NULL AND b.deleted_at IS NULL
                      AND a.normalized_phone IS NOT NULL AND a.normalized_phone != ''
                    LIMIT 50
                """, (tenant_id,))
                phone_dupes = [dict(r) for r in cur.fetchall()]
            return {"duplicates": phone_dupes, "count": len(phone_dupes)}
        except Exception as e2:
            raise HTTPException(500, str(e2))
    finally:
        release_conn(conn)


@app.get("/crm/contacts/sort-session")
async def get_contacts_for_sorting(
    request: Request,
    sort_by: str = "name",
    phone_prefix: str = "+44",
    limit: int = 500
):
    """Return existing classified phones so Android can skip already-sorted contacts."""
    user = ensure_request_permissions(request, "contacts_manage")
    tenant_id = user.get("tenant_id", 1)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Phones already in clients table
            cur.execute(
                "SELECT phone_primary FROM clients WHERE tenant_id=%s AND deleted_at IS NULL AND phone_primary IS NOT NULL",
                (tenant_id,)
            )
            client_phones = {r["phone_primary"] for r in cur.fetchall()}

            # Phones already in shared_contacts
            cur.execute(
                "SELECT phone_primary, section_code FROM shared_contacts WHERE tenant_id=%s AND deleted_at IS NULL AND phone_primary IS NOT NULL",
                (tenant_id,)
            )
            shared_phones = {r["phone_primary"]: r["section_code"] for r in cur.fetchall()}

        already_sorted = {
            **{p: "client" for p in client_phones},
            **shared_phones
        }
        return {
            "sort_by": sort_by,
            "phone_prefix": phone_prefix,
            "already_sorted": already_sorted,
            "sections": [[code, name, order] for code, name, order in DEFAULT_CONTACT_SECTIONS]
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)


@app.post("/crm/contacts/assign-section")
async def assign_contact_section(data: dict, request: Request):
    """Assign a phone contact to a section.
    If section_code == 'client': creates/updates record in clients table.
    Otherwise: creates/updates record in shared_contacts table.
    """
    user = ensure_request_permissions(request, "contacts_manage")
    tenant_id = user.get("tenant_id", 1)
    user_id = user.get("user_id")
    conn = get_db_conn()
    try:
        display_name = (data.get("display_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        section_code = (data.get("section_code") or "other").strip()
        contact_id = data.get("contact_id")

        if not display_name or not phone:
            raise HTTPException(400, "display_name and phone required")

        import re as _re
        norm_phone = _re.sub(r"[^\d+]", "", phone)
        if norm_phone.startswith("0") and len(norm_phone) >= 10:
            norm_phone = "+44" + norm_phone[1:]

        with conn.cursor() as cur:
            if section_code == "client":
                # Route into clients table
                # Parse display_name into first/last
                parts = display_name.strip().split(" ", 1)
                first_name = parts[0] if parts else display_name
                last_name = parts[1] if len(parts) > 1 else ""

                # Check if already exists by phone
                cur.execute(
                    "SELECT id FROM clients WHERE tenant_id=%s AND phone_primary=%s AND deleted_at IS NULL LIMIT 1",
                    (tenant_id, phone)
                )
                existing = cur.fetchone()
                if existing:
                    result = {"id": existing["id"], "section_code": "client",
                              "display_name": display_name, "already_existed": True}
                else:
                    # Generate client code
                    cur.execute(
                        "SELECT COUNT(*) as cnt FROM clients WHERE tenant_id=%s", (tenant_id,)
                    )
                    cnt = (cur.fetchone() or {}).get("cnt", 0)
                    client_code = f"CLI-{str(cnt + 1).zfill(5)}"

                    cur.execute(
                        """INSERT INTO clients
                           (tenant_id, client_code, client_type, first_name, last_name,
                            display_name, phone_primary, status, source, created_at, updated_at)
                           VALUES (%s,%s,'domestic',%s,%s,%s,%s,'active','voice_sort',now(),now())
                           RETURNING id, display_name, client_code""",
                        (tenant_id, client_code, first_name, last_name,
                         display_name, phone)
                    )
                    row = cur.fetchone()
                    result = dict(row) if row else {}
                    result["section_code"] = "client"
            else:
                # Route into shared_contacts table
                if contact_id:
                    cur.execute(
                        """UPDATE shared_contacts SET section_code=%s, updated_at=now()
                           WHERE id=%s AND tenant_id=%s
                           RETURNING id, section_code, display_name""",
                        (section_code, contact_id, tenant_id)
                    )
                    row = cur.fetchone()
                    result = dict(row) if row else {"section_code": section_code}
                else:
                    cur.execute(
                        """INSERT INTO shared_contacts
                           (tenant_id, section_code, display_name, phone_primary,
                            normalized_phone, source, created_at, updated_at)
                           VALUES (%s,%s,%s,%s,%s,'voice_sort',now(),now())
                           ON CONFLICT DO NOTHING
                           RETURNING id, section_code, display_name""",
                        (tenant_id, section_code, display_name, phone, norm_phone)
                    )
                    row = cur.fetchone()
                    if not row:
                        cur.execute(
                            """UPDATE shared_contacts SET section_code=%s, updated_at=now()
                               WHERE tenant_id=%s AND normalized_phone=%s
                               RETURNING id, section_code, display_name""",
                            (section_code, tenant_id, norm_phone)
                        )
                        row = cur.fetchone()
                    result = dict(row) if row else {"section_code": section_code}

            # Audit
            try:
                entity = "client" if section_code == "client" else "shared_contact"
                cur.execute(
                    """INSERT INTO audit_log (tenant_id, user_id, action, entity_type, description, created_at)
                       VALUES (%s,%s,'assign_contact_section',%s,%s,now())""",
                    (tenant_id, user_id, entity, f"Assigned {display_name} to {section_code}")
                )
            except Exception:
                pass
            conn.commit()
        return result
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)


@app.post("/crm/contacts/merge")
async def merge_shared_contacts(data: dict, request: Request):
    """Merge two shared contacts — keep primary, delete secondary, transfer section."""
    user = ensure_request_permissions(request, "contacts_manage")
    tenant_id = user.get("tenant_id", 1)
    user_id = user.get("user_id")
    conn = get_db_conn()
    try:
        primary_id = data.get("primary_id")
        secondary_id = data.get("secondary_id")
        if not primary_id or not secondary_id:
            raise HTTPException(400, "primary_id and secondary_id required")

        with conn.cursor() as cur:
            # Load both
            cur.execute(
                "SELECT * FROM shared_contacts WHERE id IN (%s,%s) AND tenant_id=%s",
                (primary_id, secondary_id, tenant_id)
            )
            rows = {r["id"]: dict(r) for r in cur.fetchall()}
            if len(rows) < 2:
                raise HTTPException(404, "One or both contacts not found")

            primary = rows[primary_id]
            secondary = rows[secondary_id]

            # Merge: fill missing fields from secondary into primary
            merge_fields = ["phone_primary", "email_primary", "address", "company_name", "notes", "normalized_phone"]
            updates = {}
            for field in merge_fields:
                if not primary.get(field) and secondary.get(field):
                    updates[field] = secondary[field]

            if updates:
                set_clause = ", ".join(f"{k}=%s" for k in updates)
                cur.execute(
                    f"UPDATE shared_contacts SET {set_clause}, updated_at=now() WHERE id=%s AND tenant_id=%s",
                    list(updates.values()) + [primary_id, tenant_id]
                )

            # Soft delete secondary
            cur.execute(
                "UPDATE shared_contacts SET deleted_at=now() WHERE id=%s AND tenant_id=%s",
                (secondary_id, tenant_id)
            )

            # Audit
            try:
                cur.execute(
                    """INSERT INTO audit_log (tenant_id, user_id, action, entity_type, description, created_at)
                       VALUES (%s,%s,'merge_contacts','shared_contact',%s,now())""",
                    (tenant_id, user_id,
                     f"Merged {secondary.get('display_name')} into {primary.get('display_name')}")
                )
            except Exception:
                pass
            conn.commit()

        return {"merged": True, "primary_id": primary_id, "deleted_id": secondary_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)


@app.post("/crm/contacts/import")
async def import_shared_contacts(data: dict, request: Request):
    user = ensure_request_permissions(request, "contacts_write")
    contacts = data.get("contacts", [])
    if not contacts:
        raise HTTPException(400, "No contacts provided")
    conn = get_db_conn()
    imported = 0
    merged = 0
    errors = []
    try:
        with conn.cursor() as cur:
            for contact in contacts:
                if not contact.get("selected"):
                    continue
                try:
                    row, created = merge_shared_contact(cur, user["tenant_id"], user["user_id"], contact, source="phone_import")
                    if created:
                        imported += 1
                    else:
                        merged += 1
                except Exception as row_error:
                    errors.append(str(row_error))
            conn.commit()
        return {"imported": imported, "merged": merged, "errors": errors}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

def map_work_entry_type_to_service_rate(entry_type):
    low = str(entry_type or "").strip().lower().replace(" ", "_")
    if low in ("pruning", "hedge_trimming", "hedge", "trimming", "trim"):
        return "hedge_trimming"
    if low in ("arborist", "arborist_works", "tree", "tree_surgeon", "tree-surgeon", "tree_surgery"):
        return "arborist_works"
    if low in ("waste", "garden_waste_bulkbag", "bulkbag"):
        return "garden_waste_bulkbag"
    return "garden_maintenance" if low in ("maintenance", "garden_maintenance", "") else "hourly_rate"

@app.post("/crm/invoices/from-work-report")
async def create_invoice_from_work_report(data: dict, request: Request):
    """Create an invoice from a work report. Calculates profit using worker costs."""
    tid = get_request_tenant_id(request)
    wr_id = data.get("work_report_id")
    if not wr_id: raise HTTPException(400, "work_report_id required")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Get work report
            cur.execute("SELECT * FROM work_reports WHERE id=%s AND tenant_id=%s", (wr_id, tid))
            wr = cur.fetchone()
            if not wr: raise HTTPException(404, "Work report not found")
            # Check not already invoiced
            cur.execute("SELECT id FROM invoices WHERE work_report_id=%s AND tenant_id=%s", (wr_id, tid))
            if cur.fetchone(): raise HTTPException(409, "Work report already invoiced")
            # Get entries
            cur.execute("SELECT * FROM work_report_entries WHERE work_report_id=%s", (wr_id,))
            entries = cur.fetchall()
            # Get workers with costs
            cur.execute("SELECT w.*, u.hourly_cost FROM work_report_workers w LEFT JOIN users u ON LOWER(u.display_name)=LOWER(w.worker_name) AND u.tenant_id=%s WHERE w.work_report_id=%s", (tid, wr_id))
            workers = cur.fetchall()
            # Create invoice
            code = f"INV-{uuid.uuid4().hex[:6].upper()}"
            due_date = data.get("due_date")
            if not due_date:
                import datetime as dt
                due_date = (dt.date.today() + dt.timedelta(days=30)).isoformat()
            grand_total = float(wr["total_price"] or 0)
            cur.execute("""INSERT INTO invoices (invoice_number,client_id,grand_total,status,due_date,work_report_id,tenant_id,job_id,created_by,notes)
                VALUES (%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s) RETURNING id,invoice_number""",
                (code, wr["client_id"], grand_total, due_date, wr_id, tid, wr.get("job_id"), wr.get("created_by"),
                 f"Generated from work report #{wr_id} on {wr['work_date']}"))
            inv = dict(cur.fetchone())
            # Create invoice items from entries
            total_cost = 0
            for i, e in enumerate(entries):
                hrs = float(e.get("hours",0) or 0)
                rate = float(e.get("unit_rate",0) or 0)
                if rate == 0:
                    rate_type = map_work_entry_type_to_service_rate(e.get("type"))
                    rate = get_effective_rate(conn, tid, client_id=wr.get("client_id"), rate_type=rate_type)
                item_total = float(e.get("total_price",0) or hrs * rate)
                cur.execute("""INSERT INTO invoice_items (invoice_id,description,quantity,unit_price,total,sort_order,tenant_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (inv["id"], f"{e.get('type','work')} — {hrs}h", hrs, rate, item_total, i+1, tid))
            # Calculate profit from workers
            for w in workers:
                cost_per_h = float(w.get("hourly_cost",0) or 0)
                hrs = float(w.get("hours",0) or 0)
                total_cost += cost_per_h * hrs
            profit = grand_total - total_cost
            inv["grand_total"] = grand_total
            inv["total_cost"] = total_cost
            inv["profit"] = profit
            inv["profit_margin"] = round((profit / grand_total * 100), 1) if grand_total > 0 else 0
            inv["status"] = "draft"
            inv["work_report_id"] = wr_id
            log_activity(conn, "invoice", inv["id"], "create", f"Faktura {code} z výkazu #{wr_id}, zisk {profit:.0f} GBP ({inv['profit_margin']}%)")
            conn.commit()
        return inv
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

# === USER RATES ===
@app.get("/crm/users/{user_id}/rates")
async def get_user_rates(user_id: int, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,display_name,hourly_rate,hourly_cost FROM users WHERE id=%s AND tenant_id=%s", (user_id, tid))
            u = cur.fetchone()
            if not u: raise HTTPException(404, "User not found")
            return dict(u)
    finally: release_conn(conn)

@app.put("/crm/users/{user_id}/rates")
async def update_user_rates(user_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET hourly_rate=%s, hourly_cost=%s, updated_at=now() WHERE id=%s AND tenant_id=%s RETURNING id,display_name,hourly_rate,hourly_cost",
                (data.get("hourly_rate",0), data.get("hourly_cost",0), user_id, tid))
            u = cur.fetchone()
            if not u: raise HTTPException(404, "User not found")
            log_activity(conn, "user", user_id, "update_rates", f"Rate: {u['hourly_rate']}, Cost: {u['hourly_cost']}")
            conn.commit()
        return dict(u)
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

# === CLIENT RATE ===
@app.put("/crm/clients/{client_id}/rate")
async def update_client_rate(client_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            hourly_rate = float(data.get("default_hourly_rate", 0) or 0)
            cur.execute("UPDATE clients SET default_hourly_rate=%s, updated_at=now() WHERE id=%s AND tenant_id=%s RETURNING id,display_name,default_hourly_rate",
                (hourly_rate, client_id, tid))
            c = cur.fetchone()
            if not c: raise HTTPException(404, "Client not found")
            cur.execute("""DELETE FROM pricing_rules
                WHERE tenant_id=%s AND scope='client' AND scope_id=%s
                  AND rule_type='service_rate' AND rule_key IN ('hourly_rate','garden_maintenance')""",
                (tid, client_id))
            if hourly_rate > 0:
                for rule_key in ("hourly_rate", "garden_maintenance"):
                    cur.execute("""INSERT INTO pricing_rules (tenant_id, scope, scope_id, rule_type, rule_key, rate)
                        VALUES (%s,'client',%s,'service_rate',%s,%s)""",
                        (tid, client_id, rule_key, hourly_rate))
            log_activity(conn, "client", client_id, "update_rate", f"Default rate: {c['default_hourly_rate']} GBP/h")
            conn.commit()
        return dict(c)
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.get("/crm/clients/{client_id}/service-rates")
async def get_client_service_rates_api(client_id: int, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM clients WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL", (client_id, tid))
            if not cur.fetchone(): raise HTTPException(404, "Client not found")
        overrides = get_client_service_rate_overrides(conn, tid, client_id)
        return {
            "client_id": client_id,
            "service_rates": get_client_service_rates(conn, tid, client_id),
            "service_rate_overrides": {k: v for k, v in overrides.items() if k in VISIBLE_CLIENT_SERVICE_RATE_KEYS},
            "has_individual_service_rates": bool(overrides),
        }
    finally: release_conn(conn)

@app.put("/crm/clients/{client_id}/service-rates")
async def update_client_service_rates(client_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        normalized = {}
        for key, value in (data or {}).items():
            if key not in CLIENT_SERVICE_RATE_KEYS:
                continue
            try:
                rate = float(value)
            except (TypeError, ValueError):
                continue
            if rate > 0:
                normalized[key] = rate
        if "garden_maintenance" in normalized and "hourly_rate" not in normalized:
            normalized["hourly_rate"] = normalized["garden_maintenance"]
        if "hourly_rate" in normalized and "garden_maintenance" not in normalized:
            normalized["garden_maintenance"] = normalized["hourly_rate"]
        with conn.cursor() as cur:
            cur.execute("SELECT id, display_name FROM clients WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL", (client_id, tid))
            client = cur.fetchone()
            if not client: raise HTTPException(404, "Client not found")
            cur.execute("""DELETE FROM pricing_rules
                WHERE tenant_id=%s AND scope='client' AND scope_id=%s
                  AND rule_type='service_rate' AND rule_key = ANY(%s)""",
                (tid, client_id, CLIENT_SERVICE_RATE_KEYS))
            for rule_key, rate in normalized.items():
                cur.execute("""INSERT INTO pricing_rules (tenant_id, scope, scope_id, rule_type, rule_key, rate)
                    VALUES (%s,'client',%s,'service_rate',%s,%s)""",
                    (tid, client_id, rule_key, rate))
            legacy_hourly = normalized.get("hourly_rate") or normalized.get("garden_maintenance") or 0
            cur.execute("UPDATE clients SET default_hourly_rate=%s, updated_at=now() WHERE id=%s AND tenant_id=%s",
                (legacy_hourly, client_id, tid))
            if normalized:
                log_activity(conn, "client", client_id, "update_service_rates", f"Individual service rates updated for {client['display_name']}")
            else:
                log_activity(conn, "client", client_id, "reset_service_rates", f"Individual service rates cleared for {client['display_name']}")
            conn.commit()
        overrides = get_client_service_rate_overrides(conn, tid, client_id)
        return {
            "client_id": client_id,
            "service_rates": get_client_service_rates(conn, tid, client_id),
            "service_rate_overrides": {k: v for k, v in overrides.items() if k in VISIBLE_CLIENT_SERVICE_RATE_KEYS},
            "has_individual_service_rates": bool(overrides),
        }
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

# === TENANT DEFAULT RATES ===
@app.get("/tenant/default-rates/{tenant_id}")
async def get_default_rates(tenant_id: int, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rate_type, rate, currency, description FROM tenant_default_rates WHERE tenant_id=%s ORDER BY id", (tid,))
            rows = cur.fetchall()
            return {r["rate_type"]: {"rate": float(r["rate"]), "currency": r["currency"], "description": r["description"]} for r in rows}
    finally: release_conn(conn)

@app.put("/tenant/default-rates/{tenant_id}")
async def update_default_rates(tenant_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            for rate_type, value in data.items():
                rate_val = value if isinstance(value, (int, float)) else value.get("rate", 0)
                cur.execute("""INSERT INTO tenant_default_rates (tenant_id, rate_type, rate, updated_at)
                    VALUES (%s, %s, %s, now()) ON CONFLICT (tenant_id, rate_type)
                    DO UPDATE SET rate=%s, updated_at=now()""", (tid, rate_type, rate_val, rate_val))
            log_activity(conn, "tenant", tid, "update_rates", f"Updated {len(data)} default rates")
            conn.commit()
            cur.execute("SELECT rate_type, rate, currency FROM tenant_default_rates WHERE tenant_id=%s", (tid,))
            return {r["rate_type"]: float(r["rate"]) for r in cur.fetchall()}
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

def get_client_service_rate_overrides(conn, tid, client_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT rule_key, rate
            FROM pricing_rules
            WHERE tenant_id=%s AND scope='client' AND scope_id=%s
              AND rule_type='service_rate' AND rule_key = ANY(%s)""",
            (tid, client_id, CLIENT_SERVICE_RATE_KEYS))
        return {row["rule_key"]: float(row["rate"]) for row in cur.fetchall() if row.get("rule_key")}

def get_client_service_rates(conn, tid, client_id):
    return {
        key: get_effective_rate(conn, tid, client_id=client_id, rate_type=key)
        for key in VISIBLE_CLIENT_SERVICE_RATE_KEYS
    }

def get_effective_rate(conn, tid, user_id=None, client_id=None, rate_type="hourly_rate"):
    """Get rate with fallback: user > client > tenant default."""
    with conn.cursor() as cur:
        if user_id:
            cur.execute(f"SELECT {rate_type} FROM users WHERE id=%s AND tenant_id=%s", (user_id, tid))
            r = cur.fetchone()
            if r and float(r[rate_type] or 0) > 0: return float(r[rate_type])
        if client_id:
            cur.execute("""SELECT rate
                FROM pricing_rules
                WHERE tenant_id=%s AND scope='client' AND scope_id=%s
                  AND rule_type='service_rate' AND rule_key=%s
                ORDER BY created_at DESC LIMIT 1""", (tid, client_id, rate_type))
            r = cur.fetchone()
            if r and float(r["rate"] or 0) > 0: return float(r["rate"])
        if client_id and rate_type == "hourly_rate":
            cur.execute("SELECT default_hourly_rate FROM clients WHERE id=%s AND tenant_id=%s", (client_id, tid))
            r = cur.fetchone()
            if r and float(r["default_hourly_rate"] or 0) > 0: return float(r["default_hourly_rate"])
        cur.execute("SELECT rate FROM tenant_default_rates WHERE tenant_id=%s AND rate_type=%s", (tid, rate_type))
        r = cur.fetchone()
        if r and float(r["rate"] or 0) > 0: return float(r["rate"])
    return SERVICE_RATE_DEFAULTS.get(rate_type, 0.0)

# === BATCH INVOICE FROM WORK REPORTS ===
@app.post("/crm/invoices/batch-from-work-reports")
async def batch_invoice_from_work_reports(data: dict, request: Request):
    """Create invoices from multiple work reports. Returns list of created invoices."""
    tid = get_request_tenant_id(request)
    wr_ids = data.get("work_report_ids", [])
    if not wr_ids: raise HTTPException(400, "work_report_ids required")
    results = []
    errors = []
    for wr_id in wr_ids:
        try:
            from starlette.requests import Request as SR
            inv = await create_invoice_from_work_report({"work_report_id": wr_id, "due_date": data.get("due_date")}, request)
            results.append(inv)
        except HTTPException as e:
            errors.append({"work_report_id": wr_id, "error": e.detail})
        except Exception as e:
            errors.append({"work_report_id": wr_id, "error": str(e)})
    return {"created": results, "errors": errors, "total_created": len(results), "total_errors": len(errors)}

@app.get("/crm/communications")
async def get_communications(request: Request, client_id: Optional[int]=None, job_id: Optional[int]=None, comm_type: Optional[str]=None):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """SELECT c.id, c.client_id, c.job_id, c.comm_type, COALESCE(c.source, c.comm_type) AS source,
                     c.external_message_id, c.source_phone, c.target_phone, c.conversation_key,
                     c.subject, c.message_summary, c.sent_at::text, c.direction, c.notes,
                     c.created_at::text, c.imported_at::text,
                     cl.display_name as client_name, j.job_title as job_title
                     FROM communications c
                     LEFT JOIN clients cl ON c.client_id = cl.id
                     LEFT JOIN jobs j ON c.job_id = j.id
                     WHERE c.tenant_id=%s"""
            params = [tid]
            if client_id: sql += " AND c.client_id=%s"; params.append(client_id)
            if job_id: sql += " AND c.job_id=%s"; params.append(job_id)
            if comm_type: sql += " AND c.comm_type=%s"; params.append(comm_type)
            sql += " ORDER BY COALESCE(c.sent_at, c.created_at) DESC, c.id DESC LIMIT 1000"
            cur.execute(sql, params); return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/communications")
async def log_communication(request: Request, data: dict):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            comm = upsert_communication_message(cur, tid, {
                **data,
                "source": data.get("source") or data.get("comm_type", "manual"),
                "message": data.get("message", data.get("message_summary", "")),
            })
            if data.get("client_id"):
                log_activity(conn,"client",data["client_id"],"communication",f"{data.get('comm_type','')}: {data.get('subject','')}")
            conn.commit()
        return comm
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.post("/crm/communications/import")
async def import_communications(request: Request, data: dict):
    user = ensure_request_permissions(request, "crm_write")
    tenant_id = user["tenant_id"]
    source = normalize_communication_source(data.get("source") or data.get("comm_type") or "sms")
    messages = data.get("messages") or []
    if not isinstance(messages, list):
        raise HTTPException(422, "messages must be a list")
    messages = messages[:10000]
    conn = get_db_conn()
    imported = updated = matched = unmatched = 0
    try:
        with conn.cursor() as cur:
            for item in messages:
                if not isinstance(item, dict):
                    continue
                result = upsert_communication_message(cur, tenant_id, {**item, "source": item.get("source") or source})
                if result.get("created"):
                    imported += 1
                else:
                    updated += 1
                if result.get("matched"):
                    matched += 1
                else:
                    unmatched += 1
            log_activity(conn, "communication", 0, "import", f"Import {source}: {imported} new, {updated} updated", tenant_id=tenant_id, user_id=user.get("user_id"))
            conn.commit()
        return {"status": "ok", "summary": {"source": source, "scanned": len(messages), "imported": imported, "updated": updated, "matched": matched, "unmatched": unmatched}}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.post("/crm/communications/provider-history-import")
async def import_provider_communication_history(request: Request, data: dict):
    user = ensure_request_permissions(request, "crm_write")
    tenant_id = user["tenant_id"]
    provider = get_whatsapp_provider()
    if provider != "twilio":
        return {"status": "ok", "summary": {"provider": provider, "scanned": 0, "imported": 0, "updated": 0, "matched": 0, "unmatched": 0, "message": "Server history import is available only for Twilio history. Meta WhatsApp cannot fetch old phone chat history."}}
    try:
        limit = max(1, min(int(data.get("limit", 1000)), 5000))
    except Exception:
        limit = 1000
    messages = []
    next_url = f"{get_twilio_messages_api_url()}?{urlencode({'PageSize': min(limit, 1000)})}"
    auth = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode("utf-8")).decode("ascii")
    while next_url and len(messages) < limit:
        req = urllib.request.Request(next_url, headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read().decode("utf-8"))
        for msg in page.get("messages", []):
            from_raw = (msg.get("from") or "").replace("whatsapp:", "")
            to_raw = (msg.get("to") or "").replace("whatsapp:", "")
            is_whatsapp = str(msg.get("from", "")).startswith("whatsapp:") or str(msg.get("to", "")).startswith("whatsapp:")
            direction = "outbound" if str(msg.get("direction", "")).startswith("outbound") else "inbound"
            messages.append({
                "source": "whatsapp" if is_whatsapp else "sms",
                "external_message_id": msg.get("sid"),
                "source_phone": from_raw,
                "target_phone": to_raw,
                "phone": from_raw if direction == "inbound" else to_raw,
                "direction": direction,
                "message": msg.get("body") or "",
                "sent_at": msg.get("date_sent") or msg.get("date_created"),
            })
            if len(messages) >= limit:
                break
        uri = page.get("next_page_uri")
        next_url = f"https://api.twilio.com{uri}" if uri and len(messages) < limit else None
    conn = get_db_conn()
    imported = updated = matched = unmatched = 0
    try:
        with conn.cursor() as cur:
            for item in messages:
                result = upsert_communication_message(cur, tenant_id, item)
                imported += 1 if result.get("created") else 0
                updated += 0 if result.get("created") else 1
                matched += 1 if result.get("matched") else 0
                unmatched += 0 if result.get("matched") else 1
            log_activity(conn, "communication", 0, "provider_import", f"Twilio import: {imported} new, {updated} updated", tenant_id=tenant_id, user_id=user.get("user_id"))
            conn.commit()
        return {"status": "ok", "summary": {"provider": provider, "scanned": len(messages), "imported": imported, "updated": updated, "matched": matched, "unmatched": unmatched}}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.post("/crm/communications/whatsapp-address-sync")
async def sync_whatsapp_addresses(request: Request, data: dict):
    user = ensure_request_permissions(request, "crm_write")
    tenant_id = user["tenant_id"]
    actor_user_id = user.get("user_id")
    apply_changes = bool(data.get("apply", False))
    overwrite = bool(data.get("overwrite", False))
    try:
        limit = int(data.get("limit", 2000))
    except Exception:
        limit = 2000
    limit = max(1, min(limit, 10000))
    conn = get_db_conn()
    results = []
    scanned = 0
    candidates = 0
    updated = 0
    skipped = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id, c.client_id, c.job_id, c.comm_type, c.subject, c.message_summary,
                       c.direction, c.notes, c.sent_at::text, c.created_at::text,
                       cl.display_name AS client_name,
                       cl.billing_address_line1, cl.billing_city, cl.billing_postcode, cl.billing_country
                FROM communications c
                LEFT JOIN clients cl ON cl.id=c.client_id AND cl.tenant_id=c.tenant_id AND cl.deleted_at IS NULL
                WHERE c.tenant_id=%s
                  AND (
                    LOWER(COALESCE(c.comm_type,'')) IN ('whatsapp', 'wa')
                    OR COALESCE(c.subject,'') ILIKE '%%whatsapp%%'
                    OR COALESCE(c.subject,'') ILIKE '%%wa %%'
                    OR COALESCE(c.notes,'') ILIKE '%%whatsapp%%'
                  )
                ORDER BY COALESCE(c.sent_at, c.created_at) DESC NULLS LAST, c.id DESC
                LIMIT %s
            """, (tenant_id, limit))
            rows = [dict(row) for row in cur.fetchall()]
            scanned = len(rows)
            for row in rows:
                text = "\n".join(
                    part for part in [
                        row.get("subject"),
                        row.get("message_summary"),
                        row.get("notes"),
                    ]
                    if part
                )
                address = extract_uk_address_from_text(text)
                if not address:
                    continue
                candidates += 1
                client = None
                if row.get("client_id") and row.get("client_name"):
                    client = {
                        "id": row.get("client_id"),
                        "display_name": row.get("client_name"),
                        "billing_address_line1": row.get("billing_address_line1"),
                        "billing_city": row.get("billing_city"),
                        "billing_postcode": row.get("billing_postcode"),
                        "billing_country": row.get("billing_country"),
                    }
                if not client:
                    phone = extract_whatsapp_phone_from_text(row.get("subject"), row.get("notes"), row.get("message_summary"))
                    client = find_client_by_whatsapp_phone(cur, tenant_id, phone)
                result = {
                    "communication_id": row.get("id"),
                    "client_id": client.get("id") if client else None,
                    "client_name": client.get("display_name") if client else None,
                    "address": address,
                    "action": "preview",
                    "reason": None,
                }
                if not client:
                    result["action"] = "skipped"
                    result["reason"] = "no_client_match"
                    skipped += 1
                    results.append(result)
                    continue
                existing_address = any(
                    client.get(key)
                    for key in ["billing_address_line1", "billing_city", "billing_postcode"]
                )
                if existing_address and not overwrite:
                    result["action"] = "skipped"
                    result["reason"] = "client_already_has_address"
                    skipped += 1
                    results.append(result)
                    continue
                new_line1 = address.get("address_line1") if overwrite or not client.get("billing_address_line1") else client.get("billing_address_line1")
                new_city = address.get("city") if overwrite or not client.get("billing_city") else client.get("billing_city")
                new_postcode = address.get("postcode") if overwrite or not client.get("billing_postcode") else client.get("billing_postcode")
                new_country = address.get("country") if overwrite or not client.get("billing_country") else client.get("billing_country")
                result["action"] = "updated" if apply_changes else "would_update"
                result["new_values"] = {
                    "billing_address_line1": new_line1,
                    "billing_city": new_city,
                    "billing_postcode": new_postcode,
                    "billing_country": new_country,
                }
                if apply_changes:
                    cur.execute("""UPDATE clients
                        SET billing_address_line1=%s,
                            billing_city=%s,
                            billing_postcode=%s,
                            billing_country=%s,
                            updated_at=now()
                        WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL""",
                        (new_line1, new_city, new_postcode, new_country, client["id"], tenant_id))
                    note = (
                        f"Address imported from WhatsApp communication #{row.get('id')}: "
                        f"{address.get('address')} (confidence {address.get('confidence')})"
                    )
                    cur.execute(
                        "INSERT INTO client_notes (client_id,note,created_by) VALUES (%s,%s,%s)",
                        (client["id"], note[:1000], get_user_display_name(conn, tenant_id, actor_user_id) or "system")
                    )
                    log_activity(
                        conn,
                        "client",
                        client["id"],
                        "whatsapp_address_import",
                        f"Address imported from WhatsApp: {address.get('address')}",
                        tenant_id=tenant_id,
                        user_id=actor_user_id,
                        details={"communication_id": row.get("id"), "address": address, "overwrite": overwrite},
                        source_channel="whatsapp",
                    )
                    updated += 1
                results.append(result)
            if apply_changes:
                conn.commit()
        return {
            "apply": apply_changes,
            "overwrite": overwrite,
            "summary": {
                "scanned": scanned,
                "address_candidates": candidates,
                "updated": updated,
                "skipped": skipped,
                "would_update": sum(1 for item in results if item.get("action") == "would_update"),
            },
            "results": results[:300],
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

# ========== REST API: PHOTOS ==========
@app.get("/crm/photos")
async def get_photos(request: Request, entity_type: Optional[str]=None, entity_id: Optional[str]=None):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id,entity_type,entity_id,filename,description,thumbnail_base64,created_by,created_at::text FROM photos WHERE tenant_id=%s"
            params = [tid]
            if entity_type: sql += " AND entity_type=%s"; params.append(entity_type)
            if entity_id: sql += " AND entity_id=%s"; params.append(entity_id)
            sql += " ORDER BY created_at DESC"
            cur.execute(sql, params); return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/photos")
async def add_photo(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO photos (entity_type,entity_id,filename,description,file_path,thumbnail_base64,created_by,tenant_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,1) RETURNING id,filename,created_at::text""",
                (data.get("entity_type","job"),data.get("entity_id","0"),data.get("filename","photo.jpg"),
                 data.get("description"),data.get("file_path"),data.get("thumbnail_base64"),data.get("created_by","Marek")))
            photo = dict(cur.fetchone())
            log_activity(conn,data.get("entity_type","job"),str(data.get("entity_id","0")),"photo",f"Foto: {data.get('filename','')}")
            conn.commit()
        return photo
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== REST API: PROPERTIES, WASTE ==========
@app.get("/crm/properties")
async def get_properties(client_id: Optional[int]=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if client_id: cur.execute("SELECT id,client_id,property_code,property_name,address_line1,city,postcode,status FROM properties WHERE client_id=%s AND deleted_at IS NULL",(client_id,))
            else: cur.execute("SELECT id,client_id,property_code,property_name,address_line1,city,postcode,status FROM properties WHERE deleted_at IS NULL ORDER BY created_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/waste")
async def get_waste():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT wl.id,wt.name as waste_type,wl.quantity,wl.unit,wl.load_date::text FROM waste_loads wl JOIN waste_types wt ON wl.waste_type_id=wt.id ORDER BY wl.load_date DESC")
            return cur.fetchall()
    finally: release_conn(conn)

# ========== ACTIVITY TIMELINE ==========
@app.get("/crm/timeline")
async def get_timeline(entity_type: Optional[str]=None, entity_id: Optional[str]=None, limit: int=50):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM activity_timeline WHERE 1=1"; params = []
            if entity_type: sql += " AND entity_type=%s"; params.append(entity_type)
            if entity_id: sql += " AND entity_id=%s"; params.append(entity_id)
            sql += f" ORDER BY created_at DESC LIMIT {limit}"
            cur.execute(sql,params); return cur.fetchall()
    finally: release_conn(conn)

# ========== IMPORT / EXPORT ==========
@app.post("/crm/import")
async def import_data(data: dict):
    table = data.get("table","clients")
    rows = data.get("data",[])
    if table not in ("clients","properties","jobs"): raise HTTPException(400,"Nepovolena tabulka")
    conn = get_db_conn(); imported = 0; errors = []
    try:
        with conn.cursor() as cur:
            for i,row in enumerate(rows):
                try:
                    if table == "clients":
                        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
                        cur.execute("INSERT INTO clients (client_code,client_type,display_name,email_primary,phone_primary,status) VALUES (%s,%s,%s,%s,%s,'active')",
                            (code,row.get("type","domestic"),clean_contact_display_name(row.get("name") or row.get("display_name")) or row.get("phone", row.get("phone_primary")) or "Client",row.get("email",row.get("email_primary")),row.get("phone",row.get("phone_primary"))))
                    imported += 1
                except Exception as e: errors.append(f"Row {i+1}: {e}")
        conn.commit()
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)
    return {"imported":imported,"errors":errors,"total":len(rows)}

@app.get("/crm/export/csv")
async def export_csv():
    conn = get_db_conn()
    try:
        out = io.StringIO()
        with conn.cursor() as cur:
            cur.execute("SELECT id,client_code,display_name,email_primary,phone_primary,status FROM clients WHERE deleted_at IS NULL ORDER BY display_name")
            rows = cur.fetchall()
        if rows:
            w = csv.DictWriter(out,fieldnames=rows[0].keys()); w.writeheader(); w.writerows([dict(r) for r in rows])
        out.seek(0)
        return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":f"attachment; filename=export_{datetime.now().strftime('%Y%m%d')}.csv"})
    finally: release_conn(conn)

# ========== SYSTEM ==========
@app.get("/system/settings")
async def get_settings():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL"); cc = cur.fetchone()['cnt']
            cur.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL"); jc = cur.fetchone()['cnt']
            cur.execute("SELECT COUNT(*) as cnt FROM tasks"); tc = cur.fetchone()['cnt']
            cur.execute("SELECT COUNT(*) as cnt FROM leads"); lc = cur.fetchone()['cnt']
            cur.execute("SELECT COUNT(*) as cnt FROM users WHERE deleted_at IS NULL"); uc = cur.fetchone()['cnt']
            cur.execute("SELECT workspace_mode, max_active_users FROM tenant_operating_profile WHERE tenant_id=1")
            op = cur.fetchone() or {}
            cur.execute("SELECT max_users FROM subscription_limits WHERE tenant_id=1")
            sl = cur.fetchone() or {}
            return {"company_name":"DesignLeaf","version":"1.2a","database":"PostgreSQL",
                    "clients_count":cc,"jobs_count":jc,"tasks_count":tc,"leads_count":lc,
                    "users_count":uc,
                    "workspace_mode":op.get("workspace_mode","solo"),
                    "max_active_users":sl.get("max_users", op.get("max_active_users",1)),
                    "ai_configured":bool(OPENAI_API_KEY),"environment":os.getenv("RAILWAY_ENVIRONMENT","local")}
    except Exception as e: return {"company_name":"DesignLeaf","version":"1.2a","error":str(e)}
    finally: release_conn(conn)


@app.get("/debug/db-schema")
async def db_schema():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_schema,table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema IN ('crm','public') ORDER BY 1,2,ordinal_position")
            return {"columns": [dict(r) for r in cur.fetchall()]}
    finally: release_conn(conn)


@app.post("/debug/repair-schema")
async def repair_schema():
    conn = get_db_conn()
    results = []
    try:
        with conn.cursor() as cur:
            for sql in ["ALTER TABLE clients ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE clients ADD COLUMN IF NOT EXISTS company_registration_no TEXT","ALTER TABLE clients ADD COLUMN IF NOT EXISTS vat_no TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_name TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_email TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_phone TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS description TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS notes TEXT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS job_id BIGINT","ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()","ALTER TABLE leads ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE communications ADD COLUMN IF NOT EXISTS comm_type TEXT DEFAULT 'telefon'","ALTER TABLE communications ADD COLUMN IF NOT EXISTS job_id BIGINT","ALTER TABLE communications ADD COLUMN IF NOT EXISTS notes TEXT","ALTER TABLE communications ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE jobs ALTER COLUMN property_id DROP NOT NULL","ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE properties ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1","ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS user_id_ref TEXT","ALTER TABLE tasks ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE photos ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE waste_loads ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE client_notes ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE job_notes ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1","ALTER TABLE task_history ADD COLUMN IF NOT EXISTS tenant_id INT NOT NULL DEFAULT 1"]:
                try:
                    cur.execute(sql)
                    results.append({"sql": sql[:60], "ok": True})
                except Exception as e:
                    results.append({"sql": sql[:60], "ok": False, "err": str(e)})
                    conn.rollback()
            for sql in ["CREATE TABLE IF NOT EXISTS roles (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, role_name TEXT NOT NULL UNIQUE, description TEXT, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS users (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, role_id BIGINT, first_name TEXT NOT NULL, last_name TEXT NOT NULL, display_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT, status TEXT DEFAULT 'active', password_hash TEXT DEFAULT '', must_change_password BOOLEAN DEFAULT FALSE, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(), deleted_at TIMESTAMPTZ)","CREATE TABLE IF NOT EXISTS audit_log (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, user_id BIGINT, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, old_values JSONB, new_values JSONB, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS quotes (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, quote_number TEXT UNIQUE, client_id BIGINT, status TEXT DEFAULT 'draft', total NUMERIC(12,2) DEFAULT 0, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS tenants (id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS migration_log (id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ DEFAULT now())"]:
                try:
                    cur.execute(sql)
                    results.append({"sql": "CREATE TABLE OK", "ok": True})
                except Exception as e:
                    results.append({"sql": "CREATE TABLE FAIL", "ok": False, "err": str(e)})
                    conn.rollback()
            try:
                cur.execute("INSERT INTO tenants (name,slug) VALUES ('DesignLeaf','designleaf') ON CONFLICT (slug) DO NOTHING")
                cur.execute("INSERT INTO roles (role_name,description) VALUES ('admin','Full access') ON CONFLICT (role_name) DO NOTHING")
                cur.execute("INSERT INTO migration_log (filename) VALUES ('001_full_repair.sql') ON CONFLICT (filename) DO NOTHING")
                results.append({"sql": "SEED data", "ok": True})
            except Exception as e:
                results.append({"sql": "SEED", "ok": False, "err": str(e)})
                conn.rollback()
            for t in ['work_report_waste','work_report_materials','work_report_entries','work_report_workers','work_reports','voice_sessions','tasks','task_history','activity_timeline','photos','client_notes','job_notes','pricing_rules']:
                try:
                    cur.execute("DROP TABLE IF EXISTS public." + t + " CASCADE")
                    results.append({"sql": "DROP public." + t, "ok": True})
                except Exception as e:
                    results.append({"sql": "DROP public." + t, "ok": False, "err": str(e)})
                    conn.rollback()
            conn.commit()
            return {"status": "REPAIR COMPLETE", "results": results}
    except Exception as e:
        conn.rollback()
        return {"status": "REPAIR FAILED: " + str(e), "results": results}
    finally: release_conn(conn)



# /debug/seed-admin REMOVED — security risk (was public, no auth)

@app.get("/debug/test-voice")
async def test_voice():
    """Test voice session input to see actual error"""
    conn = get_db_conn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # Create session
            sid = str(uuid.uuid4())
            ctx = json.dumps({"language":"cs","work_date":"2026-04-02"})
            cur.execute("INSERT INTO voice_sessions (id,tenant_id,session_type,state,dialog_step,context) VALUES (%s,1,'work_report','active','client',%s)",(sid,ctx))
            conn.commit()
            # Now try to read it back like voice_session_input does
            cur.execute("SELECT * FROM voice_sessions WHERE id=%s AND state='active' FOR UPDATE",(sid,))
            sess = cur.fetchone()
            if not sess: return {"error":"no session found"}
            raw = sess['context']
            return {"raw_type":str(type(raw)),"raw_value":str(raw)[:200],"sess_keys":list(dict(sess).keys()),"tenant_id":sess['tenant_id'],"step":sess['dialog_step']}
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        try: conn.rollback()
        except: pass
        return {"error":str(e),"type":type(e).__name__,"traceback":tb[-500:]}
    finally:
        try: release_conn(conn)
        except: pass

# ========== QUOTES (Nabídky) ==========
def ensure_quote_items_table():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS quote_items (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                quote_id bigint NOT NULL REFERENCES quotes(id),
                description text NOT NULL,
                quantity numeric(10,2) NOT NULL DEFAULT 1,
                unit_price numeric(12,2) NOT NULL DEFAULT 0,
                total numeric(12,2) NOT NULL DEFAULT 0,
                sort_order int NOT NULL DEFAULT 0
            )""")
            conn.commit()
    except: conn.rollback()
    finally: release_conn(conn)

def ensure_hierarchy_workflow_schema():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE clients ADD COLUMN IF NOT EXISTS owner_user_id BIGINT;
                ALTER TABLE clients ADD COLUMN IF NOT EXISTS next_action_task_id TEXT;
                ALTER TABLE clients ADD COLUMN IF NOT EXISTS hierarchy_status TEXT NOT NULL DEFAULT 'valid';
                ALTER TABLE jobs ADD COLUMN IF NOT EXISTS next_action_task_id TEXT;
                ALTER TABLE jobs ADD COLUMN IF NOT EXISTS hierarchy_status TEXT NOT NULL DEFAULT 'valid';
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_clients_tenant_owner ON clients (tenant_id, owner_user_id) WHERE deleted_at IS NULL")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_clients_tenant_next_action ON clients (tenant_id, next_action_task_id) WHERE deleted_at IS NULL")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_tenant_assigned_user ON jobs (tenant_id, assigned_user_id) WHERE deleted_at IS NULL")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_tenant_next_action ON jobs (tenant_id, next_action_task_id) WHERE deleted_at IS NULL")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_tenant_assigned_user ON tasks (tenant_id, assigned_user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_tenant_client_open ON tasks (tenant_id, client_id, is_completed)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_tenant_job_open ON tasks (tenant_id, job_id, is_completed)")
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'fk_clients_owner_user'
                    ) THEN
                        ALTER TABLE clients
                        ADD CONSTRAINT fk_clients_owner_user
                        FOREIGN KEY (owner_user_id) REFERENCES users(id)
                        ON DELETE RESTRICT;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'fk_clients_next_action_task'
                    ) THEN
                        ALTER TABLE clients
                        ADD CONSTRAINT fk_clients_next_action_task
                        FOREIGN KEY (next_action_task_id) REFERENCES tasks(id)
                        ON DELETE RESTRICT;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'fk_jobs_next_action_task'
                    ) THEN
                        ALTER TABLE jobs
                        ADD CONSTRAINT fk_jobs_next_action_task
                        FOREIGN KEY (next_action_task_id) REFERENCES tasks(id)
                        ON DELETE RESTRICT;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'fk_jobs_assigned_user'
                    ) THEN
                        ALTER TABLE jobs
                        ADD CONSTRAINT fk_jobs_assigned_user
                        FOREIGN KEY (assigned_user_id) REFERENCES users(id)
                        ON DELETE RESTRICT;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'fk_tasks_assigned_user'
                    ) THEN
                        ALTER TABLE tasks
                        ADD CONSTRAINT fk_tasks_assigned_user
                        FOREIGN KEY (assigned_user_id) REFERENCES users(id)
                        ON DELETE RESTRICT;
                    END IF;
                EXCEPTION WHEN OTHERS THEN
                    NULL;
                END $$;
            """)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_conn(conn)

@app.get("/crm/quotes")
async def list_quotes(tenant_id: int=1, client_id: Optional[int]=None, status: Optional[str]=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT q.*,c.display_name as client_name FROM quotes q LEFT JOIN clients c ON q.client_id=c.id WHERE 1=1"
            params = []
            if client_id: sql += " AND q.client_id=%s"; params.append(client_id)
            if status: sql += " AND q.status=%s"; params.append(status)
            sql += " ORDER BY q.created_at DESC"
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)
@app.get("/crm/quotes/{quote_id}")
async def get_quote(quote_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT q.*,c.display_name as client_name FROM quotes q LEFT JOIN clients c ON q.client_id=c.id WHERE q.id=%s",(quote_id,))
            q = cur.fetchone()
            if not q: raise HTTPException(404,"Quote not found")
            q = dict(q)
            cur.execute("SELECT * FROM quote_items WHERE quote_id=%s ORDER BY sort_order",(quote_id,))
            q['items'] = [dict(r) for r in cur.fetchall()]
            return q
    finally: release_conn(conn)

@app.post("/crm/quotes")
async def create_quote(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(CAST(SUBSTRING(quote_number FROM 5) AS INT)),0)+1 as next_num FROM quotes")
            num = cur.fetchone()['next_num']
            qn = f"QTE-{num:06d}"
            cur.execute("INSERT INTO quotes (quote_number,client_id,quote_title,status,grand_total) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (qn,data["client_id"],data.get("quote_title","Nabidka"),data.get("status","draft"),data.get("grand_total",0)))
            qid = cur.fetchone()['id']; conn.commit()
        return {"id":qid,"quote_number":qn,"status":"created"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.put("/crm/quotes/{quote_id}")
async def update_quote(quote_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            fields,vals = [],[]
            for k in ["quote_title","status","grand_total"]:
                if k in data: fields.append(f"{k}=%s"); vals.append(data[k])
            if fields: vals.append(quote_id); cur.execute(f"UPDATE quotes SET {','.join(fields)},updated_at=now() WHERE id=%s",vals); conn.commit()
        return {"id":quote_id,"status":"updated"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.post("/crm/quotes/{quote_id}/items")
async def add_quote_item(quote_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            qty=data.get("quantity",1); price=data.get("unit_price",0); total=round(float(qty)*float(price),2)
            cur.execute("INSERT INTO quote_items (quote_id,description,quantity,unit_price,total,sort_order) VALUES (%s,%s,%s,%s,%s,(SELECT COALESCE(MAX(sort_order),0)+1 FROM quote_items WHERE quote_id=%s)) RETURNING id",
                (quote_id,data.get("description",""),qty,price,total,quote_id))
            iid = cur.fetchone()['id']
            cur.execute("UPDATE quotes SET grand_total=(SELECT COALESCE(SUM(total),0) FROM quote_items WHERE quote_id=%s),updated_at=now() WHERE id=%s",(quote_id,quote_id)); conn.commit()
        return {"id":iid,"status":"created"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.delete("/crm/quotes/{quote_id}/items/{item_id}")
async def delete_quote_item(quote_id: int, item_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM quote_items WHERE id=%s AND quote_id=%s",(item_id,quote_id))
            cur.execute("UPDATE quotes SET grand_total=(SELECT COALESCE(SUM(total),0) FROM quote_items WHERE quote_id=%s),updated_at=now() WHERE id=%s",(quote_id,quote_id)); conn.commit()
        return {"status":"deleted"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.post("/crm/quotes/{quote_id}/approve")
async def approve_quote(quote_id: int, data: dict = {}):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE quotes SET status='schvaleno',updated_at=now() WHERE id=%s RETURNING client_id,property_id,quote_title",(quote_id,))
            q = cur.fetchone()
            if not q: raise HTTPException(404)
            result = {"quote_id":quote_id,"status":"schvaleno"}
            if data.get("create_job",False):
                cur.execute("SELECT COALESCE(MAX(CAST(SUBSTRING(job_number FROM 5) AS INT)),0)+1 FROM jobs")
                jnum = cur.fetchone()[0]; jn = f"JOB-{jnum:06d}"
                cur.execute("INSERT INTO jobs (job_number,client_id,property_id,job_title,job_status,quote_id) VALUES (%s,%s,%s,%s,'nova',%s) RETURNING id",
                    (jn,q['client_id'],q['property_id'],q['quote_title'],quote_id))
                result["job_id"]=cur.fetchone()['id']; result["job_number"]=jn
            conn.commit()
        return result
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)
@app.put("/crm/invoices/{invoice_id}")
async def update_invoice(invoice_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if "status" in data:
                cur.execute("SELECT status FROM invoices WHERE id=%s",(invoice_id,))
                row = cur.fetchone()
                if not row: raise HTTPException(404,"Invoice not found")
                err = validate_state_transition(row["status"], data["status"], INVOICE_TRANSITIONS, "Invoice")
                if err: raise HTTPException(422, err)
            fields = []
            vals = []
            for k in ["status","grand_total","due_date","notes"]:
                if k in data:
                    fields.append(f"{k}=%s"); vals.append(data[k])
            if not fields: raise HTTPException(400,"No fields to update")
            vals.append(invoice_id)
            cur.execute(f"UPDATE invoices SET {','.join(fields)},updated_at=now() WHERE id=%s",vals)
            conn.commit()
        return {"id":invoice_id,"status":"updated"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== JOB NOTES ==========
@app.post("/crm/jobs/{job_id}/notes")
async def add_job_note(job_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            note_type = (data.get("note_type") or "general").strip() or "general"
            created_by = data.get("created_by") or get_user_display_name(conn, tid, request.state.user.get("user_id")) or "system"
            cur.execute("""INSERT INTO job_notes (job_id,note,note_type,created_by,tenant_id)
                VALUES (%s,%s,%s,%s,%s)
                RETURNING id,job_id,note,note_type,created_by,created_at::text,updated_at::text""",
                (job_id, data.get("note",""), note_type, created_by, tid))
            note = dict(cur.fetchone())
            log_activity(conn, "job", job_id, "note", f"{note_type}: {(data.get('note','') or '')[:120]}", tenant_id=tid, user_id=request.state.user.get("user_id"))
            conn.commit()
        return note
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.get("/crm/jobs/{job_id}/photos")
async def get_job_photos(job_id: int, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,entity_type,entity_id,filename,description,photo_type,file_path,thumbnail_base64,
                    created_by,created_at::text
                FROM photos
                WHERE tenant_id=%s AND entity_type='job' AND entity_id=%s
                ORDER BY created_at DESC""", (tid, str(job_id)))
            return [map_photo_row_to_job_photo(dict(r)) for r in cur.fetchall()]
    finally:
        release_conn(conn)

@app.post("/crm/jobs/{job_id}/photos")
async def upload_job_photo(
    job_id: int,
    request: Request,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    photo_type: Optional[str] = Form(None),
):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        content = await file.read()
        if not content:
            raise HTTPException(400, "Photo file is empty")
        data_url = encode_photo_data_url(content, file.filename or "photo.jpg", file.content_type)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO photos
                (entity_type,entity_id,filename,description,photo_type,file_path,thumbnail_base64,created_by,tenant_id)
                VALUES ('job',%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id,entity_type,entity_id,filename,description,photo_type,file_path,thumbnail_base64,created_by,created_at::text""",
                (
                    str(job_id),
                    file.filename or f"job_{job_id}_{uuid.uuid4().hex[:8]}.jpg",
                    description,
                    (photo_type or "general").strip() or "general",
                    data_url,
                    data_url,
                    get_user_display_name(conn, tid, request.state.user.get("user_id")) or "system",
                    tid,
                ))
            photo = map_photo_row_to_job_photo(dict(cur.fetchone()))
            log_activity(conn, "job", job_id, "photo_upload", f"Photo uploaded: {photo['photo_type']}", tenant_id=tid, user_id=request.state.user.get("user_id"))
            conn.commit()
        return photo
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.post("/plants/identify")
async def identify_plant(
    request: Request,
    images: List[UploadFile] = File(...),
    organs_json: Optional[str] = Form(None),
    language: Optional[str] = Form("en"),
    captured_at: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    accuracy_meters: Optional[float] = Form(None),
    location_source: Optional[str] = Form(None),
):
    user = ensure_request_permissions(request, "crm_read")
    tenant_id = user["tenant_id"]
    if not images:
        raise HTTPException(400, tr_lang(
            language,
            "No plant images uploaded.",
            "Nebyly nahrány žádné fotografie rostliny.",
            "Nie przesłano żadnych zdjęć rośliny."
        ))
    if len(images) > 5:
        raise HTTPException(400, tr_lang(
            language,
            "A maximum of 5 images is supported.",
            "Podporováno je maximálně 5 fotografií.",
            "Obsługiwanych jest maksymalnie 5 zdjęć."
        ))
    try:
        raw_organs = json.loads(organs_json) if organs_json else []
    except Exception:
        raise HTTPException(400, tr_lang(
            language,
            "Invalid photo type payload.",
            "Neplatná data typů fotografií.",
            "Nieprawidłowe dane typów zdjęć."
        ))
    organs = []
    for index in range(len(images)):
        organ = (raw_organs[index] if index < len(raw_organs) else "auto") or "auto"
        organs.append(str(organ).strip().lower() or "auto")
    try:
        plantnet_raw = await plantnet_identify(images, organs, language or "en")
        result = map_plantnet_result(plantnet_raw, language or "en", organs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Plant recognition response could not be processed: {exc}",
            f"Odpověď rozpoznávání rostliny nešla zpracovat: {exc}",
            f"Odpowiedź rozpoznawania rośliny nie mogła zostać przetworzona: {exc}"
        ))

    try:
        history_photos = await build_history_photos(images, organs)
        conn = get_db_conn()
        try:
            store_nature_history(
                conn,
                tenant_id,
                user.get("user_id"),
                "plant_identification",
                language or "en",
                result,
                history_photos,
                captured_at=captured_at,
                latitude=latitude,
                longitude=longitude,
                accuracy_meters=accuracy_meters,
                location_source=location_source,
            )
            log_activity(
                conn,
                "plant_identification",
                uuid.uuid4().hex[:12],
                "identify",
                f"Plant identified as {result['scientific_name'] or result['display_name']}",
                tenant_id=tenant_id,
                user_id=user.get("user_id"),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"Plant recognition history/logging failed: {exc}")
        finally:
            release_conn(conn)
    except Exception as exc:
        print(f"Plant recognition post-processing failed: {exc}")
    return result

@app.post("/plants/health-assessment")
async def assess_plant_health(
    request: Request,
    images: List[UploadFile] = File(...),
    language: Optional[str] = Form("en"),
    captured_at: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    accuracy_meters: Optional[float] = Form(None),
    location_source: Optional[str] = Form(None),
):
    user = ensure_request_permissions(request, "crm_read")
    tenant_id = user["tenant_id"]
    if not images:
        raise HTTPException(400, tr_lang(
            language,
            "No plant images uploaded.",
            "Nebyly nahrány žádné fotografie rostliny.",
            "Nie przesłano żadnych zdjęć rośliny."
        ))
    if len(images) > 5:
        raise HTTPException(400, tr_lang(
            language,
            "A maximum of 5 images is supported.",
            "Podporováno je maximálně 5 fotografií.",
            "Obsługiwanych jest maksymalnie 5 zdjęć."
        ))
    health_raw = await plant_health_assessment(images, language or "en")
    result = map_plant_health_result(health_raw, language or "en")
    history_photos = await build_history_photos(images)
    conn = get_db_conn()
    try:
        store_nature_history(
            conn,
            tenant_id,
            user.get("user_id"),
            "plant_health_assessment",
            language or "en",
            result,
            history_photos,
            captured_at=captured_at,
            latitude=latitude,
            longitude=longitude,
            accuracy_meters=accuracy_meters,
            location_source=location_source,
        )
        log_activity(
            conn,
            "plant_health_assessment",
            uuid.uuid4().hex[:12],
            "assess",
            f"Plant health assessed as {result.get('top_issue_name') or 'healthy'}",
            tenant_id=tenant_id,
            user_id=user.get("user_id"),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return result

@app.post("/mushrooms/identify")
async def identify_mushroom(
    request: Request,
    images: List[UploadFile] = File(...),
    language: Optional[str] = Form("en"),
    captured_at: Optional[str] = Form(None),
    latitude: Optional[float] = Form(None),
    longitude: Optional[float] = Form(None),
    accuracy_meters: Optional[float] = Form(None),
    location_source: Optional[str] = Form(None),
):
    user = ensure_request_permissions(request, "crm_read")
    tenant_id = user["tenant_id"]
    if not images:
        raise HTTPException(400, tr_lang(
            language,
            "No mushroom images uploaded.",
            "Nebyly nahrány žádné fotografie houby.",
            "Nie przesłano żadnych zdjęć grzyba."
        ))
    if len(images) > 5:
        raise HTTPException(400, tr_lang(
            language,
            "A maximum of 5 images is supported.",
            "Podporováno je maximálně 5 fotografií.",
            "Obsługiwanych jest maksymalnie 5 zdjęć."
        ))
    mushroom_raw = await mushroom_identify(images, language or "en")
    try:
        result = map_mushroom_result(mushroom_raw, language or "en")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, tr_lang(
            language,
            f"Mushroom recognition response could not be processed: {exc}",
            f"Odpověď rozpoznávání houby nešla zpracovat: {exc}",
            f"Nie udało się przetworzyć odpowiedzi rozpoznawania grzyba: {exc}"
        ))
    history_photos = await build_history_photos(images)
    conn = get_db_conn()
    try:
        try:
            store_nature_history(
                conn,
                tenant_id,
                user.get("user_id"),
                "mushroom_identification",
                language or "en",
                result,
                history_photos,
                captured_at=captured_at,
                latitude=latitude,
                longitude=longitude,
                accuracy_meters=accuracy_meters,
                location_source=location_source,
            )
            log_activity(
                conn,
                "mushroom_identification",
                uuid.uuid4().hex[:12],
                "identify",
                f"Mushroom identified as {result['scientific_name'] or result['display_name']}",
                tenant_id=tenant_id,
                user_id=user.get("user_id"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
    finally:
        release_conn(conn)
    return result

@app.get("/nature/history")
async def get_nature_history(
    request: Request,
    limit: int = 30,
    recognition_type: Optional[str] = None,
    language: Optional[str] = None,
):
    user = ensure_request_permissions(request, "crm_read")
    tenant_id = user["tenant_id"]
    conn = get_db_conn()
    try:
        permissions = get_effective_permissions(conn, tenant_id, user["user_id"], user.get("role"))
        can_view_all = permissions.get("manage_users", False)
        with conn.cursor() as cur:
            sql = """SELECT h.id, h.recognition_type, h.display_name, h.scientific_name, h.confidence, h.guidance,
                            h.database_name, h.result_json, h.photos_json, h.captured_at::text, h.created_at::text,
                            h.latitude, h.longitude, h.accuracy_meters, h.location_source,
                            h.user_id AS owner_user_id, COALESCE(u.display_name, '') AS owner_display_name,
                            COALESCE(u.email, '') AS owner_email
                     FROM nature_recognition_history h
                     LEFT JOIN users u ON u.id = h.user_id AND u.tenant_id = h.tenant_id
                     WHERE h.tenant_id=%s"""
            params = [tenant_id]
            if not can_view_all:
                sql += " AND h.user_id=%s"
                params.append(user["user_id"])
            if recognition_type:
                sql += " AND h.recognition_type=%s"
                params.append(recognition_type)
            sql += " ORDER BY COALESCE(h.captured_at, h.created_at) DESC LIMIT %s"
            params.append(max(1, min(limit, 100)))
            cur.execute(sql, params)
            return [map_nature_history_entry(dict(row), language or "en") for row in cur.fetchall()]
    finally:
        release_conn(conn)

@app.get("/nature/services/status")
async def get_nature_services_status(request: Request):
    ensure_request_permissions(request, "crm_read")
    return get_nature_service_status()

@app.get("/admin/hierarchy-integrity")
async def get_admin_hierarchy_integrity(request: Request):
    user = ensure_request_permissions(request, "manage_users")
    conn = get_db_conn()
    try:
        return get_hierarchy_integrity_report(conn, user["tenant_id"])
    finally:
        release_conn(conn)

@app.post("/admin/hierarchy-integrity/backfill")
async def backfill_admin_hierarchy_integrity(request: Request, data: Optional[dict] = None):
    user = ensure_request_permissions(request, "manage_users")
    payload = data or {}
    dry_run = not bool(payload.get("apply"))
    conn = get_db_conn()
    try:
        result = run_hierarchy_backfill(conn, user["tenant_id"], actor_user_id=user.get("user_id"), dry_run=dry_run)
        if not dry_run:
            log_activity(
                conn,
                "hierarchy_integrity",
                user["tenant_id"],
                "backfill",
                "Hierarchy backfill applied",
                tenant_id=user["tenant_id"],
                user_id=user.get("user_id"),
                source_channel="admin",
                details=result["summary"],
            )
            conn.commit()
        else:
            conn.rollback()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.get("/admin/activity-log")
async def get_admin_activity_log(
    request: Request,
    limit: int = 200,
    actor_user_id: Optional[int] = None,
):
    ensure_request_permissions(request, "manage_users")
    admin = request.state.user
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """SELECT a.id, a.entity_type, a.entity_id, a.action, a.description, a.user_name,
                            a.source_channel, a.details_json, a.created_at::text,
                            a.user_id_ref AS actor_user_id,
                            COALESCE(u.display_name, a.user_name, '') AS actor_display_name,
                            COALESCE(u.email, '') AS actor_email
                     FROM activity_timeline a
                     LEFT JOIN users u
                       ON u.id::text = a.user_id_ref
                      AND u.tenant_id = a.tenant_id
                     WHERE a.tenant_id=%s"""
            params = [admin["tenant_id"]]
            if actor_user_id is not None:
                sql += " AND a.user_id_ref=%s"
                params.append(str(actor_user_id))
            sql += " ORDER BY a.created_at DESC LIMIT %s"
            params.append(max(1, min(limit, 1000)))
            cur.execute(sql, params)
            return [map_admin_activity_entry(dict(row)) for row in cur.fetchall()]
    finally:
        release_conn(conn)

@app.post("/crm/jobs/{job_id}/audit")
async def add_job_audit(job_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            user_name = get_user_display_name(conn, tid, request.state.user.get("user_id")) or "system"
            cur.execute("""INSERT INTO activity_timeline
                (entity_type, entity_id, action, description, user_name, tenant_id, user_id_ref, created_at)
                VALUES ('job', %s, %s, %s, %s, %s, %s, now())
                RETURNING id,entity_id,action,description,user_name,created_at::text""",
                (
                    str(job_id),
                    data.get("action_type", "update"),
                    data.get("description", ""),
                    user_name,
                    tid,
                    str(request.state.user.get("user_id") or ""),
                ))
            row = map_audit_row_to_job_audit(dict(cur.fetchone()))
            conn.commit()
        return row
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

# ========== INVOICE ITEMS ==========
@app.get("/crm/invoices/{invoice_id}/items")
async def get_invoice_items(invoice_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoice_items WHERE invoice_id=%s ORDER BY sort_order,id",(invoice_id,))
            return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)

@app.post("/crm/invoices/{invoice_id}/items")
async def add_invoice_item(invoice_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        qty = float(data.get("quantity",1))
        price = float(data.get("unit_price",0))
        total = qty * price
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO invoice_items (tenant_id,invoice_id,description,quantity,unit_price,total,sort_order)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, invoice_id, data.get("description","Item"), qty, price, total, data.get("sort_order",0)))
            iid = cur.fetchone()["id"]
            # Recalculate invoice grand_total
            cur.execute("SELECT COALESCE(SUM(total),0) as s FROM invoice_items WHERE invoice_id=%s",(invoice_id,))
            new_total = cur.fetchone()["s"]
            cur.execute("UPDATE invoices SET grand_total=%s,updated_at=now() WHERE id=%s",(new_total,invoice_id))
            log_activity(conn,"invoice",str(invoice_id),"item_added",f"Item: {data.get('description','')} £{total:.2f}")
            conn.commit()
        return {"id":iid,"total":float(total),"invoice_grand_total":float(new_total)}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.delete("/crm/invoices/{invoice_id}/items/{item_id}")
async def delete_invoice_item(invoice_id: int, item_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM invoice_items WHERE id=%s AND invoice_id=%s",(item_id,invoice_id))
            cur.execute("SELECT COALESCE(SUM(total),0) as s FROM invoice_items WHERE invoice_id=%s",(invoice_id,))
            new_total = cur.fetchone()["s"]
            cur.execute("UPDATE invoices SET grand_total=%s,updated_at=now() WHERE id=%s",(new_total,invoice_id))
            log_activity(conn,"invoice",str(invoice_id),"item_deleted",f"Item {item_id} removed")
            conn.commit()
        return {"status":"deleted","invoice_grand_total":float(new_total)}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== PAYMENTS ==========
@app.get("/crm/invoices/{invoice_id}/payments")
async def get_payments(invoice_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM payments WHERE invoice_id=%s ORDER BY payment_date DESC",(invoice_id,))
            return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)

@app.post("/crm/invoices/{invoice_id}/payments")
async def add_payment(invoice_id: int, data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        amount = float(data.get("amount",0))
        if amount <= 0: raise HTTPException(400,"Amount must be > 0")
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO payments (tenant_id,invoice_id,amount,payment_date,payment_method,reference,notes,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, invoice_id, amount, data.get("payment_date",datetime.now().strftime("%Y-%m-%d")),
                 data.get("payment_method","bank_transfer"), data.get("reference"), data.get("notes"), data.get("created_by")))
            pid = cur.fetchone()["id"]
            # Check total paid vs grand_total
            cur.execute("SELECT COALESCE(SUM(amount),0) as paid FROM payments WHERE invoice_id=%s",(invoice_id,))
            total_paid = cur.fetchone()["paid"]
            cur.execute("SELECT grand_total,status FROM invoices WHERE id=%s",(invoice_id,))
            inv = cur.fetchone()
            if inv:
                gt = float(inv["grand_total"] or 0)
                if float(total_paid) >= gt and gt > 0:
                    cur.execute("UPDATE invoices SET status='uhrazena',updated_at=now() WHERE id=%s",(invoice_id,))
                elif float(total_paid) > 0:
                    cur.execute("UPDATE invoices SET status='castecne_uhrazena',updated_at=now() WHERE id=%s",(invoice_id,))
            log_activity(conn,"invoice",str(invoice_id),"payment",f"Payment £{amount:.2f} ({data.get('payment_method','bank_transfer')})")
            conn.commit()
        return {"id":pid,"amount":amount,"total_paid":float(total_paid)}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== NOTIFICATIONS ==========
@app.get("/crm/notifications")
async def get_notifications(request: Request, user_id: Optional[int]=None, unread_only: bool=False):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM notifications WHERE tenant_id=%s"
            params = [tid]
            if user_id: sql += " AND user_id=%s"; params.append(user_id)
            if unread_only: sql += " AND is_read=false"
            sql += " ORDER BY created_at DESC LIMIT 50"
            cur.execute(sql, params); return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)

@app.post("/crm/notifications")
async def create_notification(data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO notifications (tenant_id,user_id,title,body,notification_type,entity_type,entity_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, data.get("user_id"), data.get("title","Notifikace"), data.get("body"),
                 data.get("notification_type","info"), data.get("entity_type"), data.get("entity_id")))
            nid = cur.fetchone()["id"]; conn.commit()
        return {"id":nid,"status":"created"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.put("/crm/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE notifications SET is_read=true,read_at=now() WHERE id=%s",(notification_id,))
            conn.commit()
        return {"status":"read"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== MANUAL WORK REPORT ==========
@app.post("/work-reports")
async def create_work_report(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO work_reports (tenant_id,client_id,work_date,total_hours,total_price,notes,input_type,status,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (data.get("tenant_id",1),data.get("client_id"),data.get("work_date"),
                 data.get("total_hours",0),data.get("total_price",0),data.get("notes"),
                 data.get("input_type","manual"),data.get("status","draft"),data.get("created_by")))
            rid = cur.fetchone()['id']; conn.commit()
        return {"id":rid,"status":"created"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== WORK REPORTS ==========
@app.get("/work-reports")
async def get_work_reports(tenant_id: int = 1):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT wr.id,wr.client_id,c.display_name as client_name,wr.work_date::text,
                wr.total_hours,wr.total_price,wr.currency,wr.notes,wr.status,wr.input_type,wr.created_at::text
                FROM work_reports wr LEFT JOIN clients c ON wr.client_id=c.id
                WHERE wr.tenant_id=%s ORDER BY wr.work_date DESC LIMIT 50""",(tenant_id,))
            reports = [dict(r) for r in cur.fetchall()]
            for rpt in reports:
                rid = rpt['id']
                cur.execute("SELECT worker_name,hours,hourly_rate,total_price FROM work_report_workers WHERE work_report_id=%s",(rid,))
                rpt['workers'] = [dict(w) for w in cur.fetchall()]
                cur.execute("SELECT type,hours,unit_rate,total_price FROM work_report_entries WHERE work_report_id=%s",(rid,))
                rpt['entries'] = [dict(e) for e in cur.fetchall()]
                cur.execute("SELECT quantity,unit_price,total_price FROM work_report_waste WHERE work_report_id=%s",(rid,))
                rpt['waste'] = [dict(w) for w in cur.fetchall()]
                cur.execute("SELECT material_name,quantity,unit_price,total_price FROM work_report_materials WHERE work_report_id=%s",(rid,))
                rpt['materials'] = [dict(m) for m in cur.fetchall()]
            return reports
    finally: release_conn(conn)

@app.get("/work-reports/{report_id}")
async def get_work_report(report_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT wr.*,c.display_name as client_name FROM work_reports wr
                LEFT JOIN clients c ON wr.client_id=c.id WHERE wr.id=%s""",(report_id,))
            rpt = cur.fetchone()
            if not rpt: raise HTTPException(404)
            rpt = dict(rpt)
            cur.execute("SELECT * FROM work_report_workers WHERE work_report_id=%s",(report_id,))
            rpt['workers'] = [dict(w) for w in cur.fetchall()]
            cur.execute("SELECT * FROM work_report_entries WHERE work_report_id=%s",(report_id,))
            rpt['entries'] = [dict(e) for e in cur.fetchall()]
            cur.execute("SELECT * FROM work_report_waste WHERE work_report_id=%s",(report_id,))
            rpt['waste'] = [dict(w) for w in cur.fetchall()]
            cur.execute("SELECT * FROM work_report_materials WHERE work_report_id=%s",(report_id,))
            rpt['materials'] = [dict(m) for m in cur.fetchall()]
            return rpt
    finally: release_conn(conn)

# ========== AUTH: JWT ==========

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def create_token(user_id: int, tenant_id: int, role: str, token_type: str = "access") -> str:
    if token_type == "access":
        exp = datetime.utcnow() + timedelta(minutes=JWT_ACCESS_EXPIRE_MINUTES)
    else:
        exp = datetime.utcnow() + timedelta(days=JWT_REFRESH_EXPIRE_DAYS)
    payload = {"user_id": user_id, "tenant_id": tenant_id, "role": role, "type": token_type, "exp": exp, "iat": datetime.utcnow()}
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

async def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Optional auth dependency. Returns user dict or None if no token."""
    if not authorization: return None
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    try:
        payload = decode_token(token)
        if payload.get("type") != "access": raise HTTPException(401, "Not an access token")
        return payload
    except HTTPException: return None

async def require_auth(authorization: str = Header(...)) -> dict:
    """Required auth dependency. Raises 401 if no valid token."""
    if not authorization: raise HTTPException(401, "Authorization header required")
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    payload = decode_token(token)
    if payload.get("type") != "access": raise HTTPException(401, "Not an access token")
    return payload

def require_role(*roles):
    """Factory for role-based access. Usage: Depends(require_role('admin','manager'))"""
    async def checker(user: dict = Depends(require_auth)):
        if user.get("role") not in roles:
            raise HTTPException(403, f"Role '{user.get('role')}' not authorized. Required: {roles}")
        return user
    return checker

def require_permission(*permission_codes):
    """Factory for permission-based access."""
    async def checker(user: dict = Depends(require_auth)):
        conn = get_db_conn()
        try:
            permissions = get_effective_permissions(conn, user["tenant_id"], user["user_id"], user.get("role"))
            if not all(permissions.get(code, False) for code in permission_codes):
                raise HTTPException(403, "Permission denied")
            return user
        finally:
            release_conn(conn)
    return checker

@app.get("/assistant/memory")
async def list_assistant_memory(request: Request, limit: int = 100):
    user = get_request_user_payload(request)
    tenant_id = user.get("tenant_id", 1)
    user_id = user.get("user_id")
    safe_limit = max(1, min(int(limit or 100), 200))
    conn = get_db_conn()
    try:
        return load_assistant_memories(conn, tenant_id, user_id, safe_limit)
    finally:
        release_conn(conn)

@app.post("/assistant/memory")
async def create_assistant_memory(data: dict, request: Request):
    user = get_request_user_payload(request)
    tenant_id = user.get("tenant_id", 1)
    user_id = user.get("user_id")
    content = str(data.get("content") or data.get("text") or "").strip()
    memory_type = data.get("memory_type") or data.get("type") or "long"
    if not content:
        raise HTTPException(400, "content required")
    conn = get_db_conn()
    try:
        remembered = remember_assistant_memory(conn, tenant_id, user_id, content, memory_type)
        conn.commit()
        return remembered
    except Exception:
        conn.rollback()
        raise
    finally:
        release_conn(conn)

@app.delete("/assistant/memory/{memory_id}")
async def delete_assistant_memory(memory_id: int, request: Request):
    user = get_request_user_payload(request)
    tenant_id = user.get("tenant_id", 1)
    user_id = user.get("user_id")
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE assistant_memory
                SET is_active=FALSE, forgotten_at=now(), updated_at=now()
                WHERE id=%s
                  AND tenant_id=%s
                  AND (user_id IS NULL OR user_id IS NOT DISTINCT FROM %s)
                  AND is_active=TRUE
                RETURNING id, content
                """,
                (memory_id, tenant_id, user_id),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Memory item not found")
        conn.commit()
        return {"status": "deleted", "item": dict(row)}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        release_conn(conn)

@app.post("/auth/login")
async def auth_login(data: dict):
    email = data.get("email","").strip().lower()
    password = data.get("password","").strip()
    if not email or not password: raise HTTPException(400, "Email and password required")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT u.id, u.tenant_id, u.display_name, u.email, u.password_hash, u.status, u.is_owner, u.is_assistant,
                COALESCE(u.must_change_password, FALSE) AS must_change_password,
                r.role_name FROM users u LEFT JOIN roles r ON u.role_id=r.id
                WHERE LOWER(u.email)=%s AND u.deleted_at IS NULL""", (email,))
            user = cur.fetchone()
            if not user: raise HTTPException(401, "Invalid credentials")
            if user["status"] and user["status"] not in ("active","setup"):
                raise HTTPException(403, f"Account is {user['status']}")
            if not user["password_hash"] or not verify_password(password, user["password_hash"]):
                raise HTTPException(401, "Invalid credentials")
            role = user["role_name"] or "viewer"
            permissions = get_effective_permissions(conn, user["tenant_id"], user["id"], role)
            access = create_token(user["id"], user["tenant_id"], role, "access")
            refresh = create_token(user["id"], user["tenant_id"], role, "refresh")
            log_activity(conn, "user", str(user["id"]), "login", f"{user['display_name']} logged in", tenant_id=user["tenant_id"], user_id=user["id"])
            conn.commit()
        return {
            "access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "must_change_password": bool(user["must_change_password"]),
            "user": {"id": user["id"], "display_name": user["display_name"], "email": user["email"],
                     "role": role, "tenant_id": user["tenant_id"], "is_owner": user["is_owner"], "permissions": permissions,
                     "must_change_password": bool(user["must_change_password"])}
        }
    finally: release_conn(conn)

@app.post("/auth/refresh")
async def auth_refresh(data: dict):
    token = data.get("refresh_token","")
    if not token: raise HTTPException(400, "refresh_token required")
    payload = decode_token(token)
    if payload.get("type") != "refresh": raise HTTPException(401, "Not a refresh token")
    access = create_token(payload["user_id"], payload["tenant_id"], payload["role"], "access")
    return {"access_token": access, "token_type": "bearer"}

@app.get("/auth/me")
async def auth_me(user: dict = Depends(require_auth)):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT u.id, u.tenant_id, u.display_name, u.email, u.phone, u.status, u.is_owner, u.is_assistant,
                COALESCE(u.must_change_password, FALSE) AS must_change_password,
                u.preferred_language_code, r.role_name FROM users u LEFT JOIN roles r ON u.role_id=r.id WHERE u.id=%s""", (user["user_id"],))
            u = cur.fetchone()
            if not u: raise HTTPException(404, "User not found")
        payload = dict(u)
        payload["permissions"] = get_effective_permissions(conn, u["tenant_id"], u["id"], u.get("role_name"))
        return payload
    finally: release_conn(conn)

@app.get("/auth/permissions")
async def auth_list_permissions(admin: dict = Depends(require_permission("manage_users"))):
    conn = get_db_conn()
    try:
        return load_permission_catalog(conn)
    finally: release_conn(conn)

@app.get("/auth/roles")
async def auth_list_roles(admin: dict = Depends(require_permission("manage_users"))):
    conn = get_db_conn()
    try:
        catalog = load_permission_catalog(conn)
        role_maps = load_role_permission_maps(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT role_name, description FROM roles ORDER BY id")
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            role_name = (row.get("role_name") or "viewer").lower()
            row["permissions"] = complete_permission_map(role_maps.get(role_name, default_permissions_for_role(role_name)))
            row["permission_details"] = catalog
        return rows
    finally: release_conn(conn)

@app.get("/auth/users")
async def auth_list_users(admin: dict = Depends(require_permission("manage_users"))):
    conn = get_db_conn()
    try:
        role_maps = load_role_permission_maps(conn)
        user_overrides = load_user_permission_overrides(conn, admin["tenant_id"])
        with conn.cursor() as cur:
            cur.execute("""SELECT u.id, u.display_name, u.email, u.phone, u.status,
                COALESCE(u.must_change_password, FALSE) AS must_change_password,
                r.role_name, u.created_at
                FROM users u LEFT JOIN roles r ON u.role_id=r.id
                WHERE u.tenant_id=%s AND u.deleted_at IS NULL
                ORDER BY u.id""", (admin["tenant_id"],))
            rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            clean_user_row_display_name(row)
            user_id = int(row["id"])
            role_name = (row.get("role_name") or "viewer").lower()
            role_permissions = complete_permission_map(role_maps.get(role_name, default_permissions_for_role(role_name)))
            overrides = normalize_permission_payload(user_overrides.get(user_id, {}))
            effective = dict(role_permissions)
            effective.update(overrides)
            row["role_permissions"] = role_permissions
            row["user_permission_overrides"] = overrides
            row["permissions"] = complete_permission_map(effective)
        return rows
    finally: release_conn(conn)

@app.get("/auth/first-login-users")
async def auth_first_login_users(tenant_id: int = 1):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.id, u.display_name, u.email
                FROM users u
                WHERE u.tenant_id=%s
                  AND u.deleted_at IS NULL
                  AND COALESCE(u.must_change_password, FALSE)=TRUE
                  AND COALESCE(u.status, 'active') IN ('active','setup')
                ORDER BY LOWER(COALESCE(u.display_name, u.email)), u.id
            """, (tenant_id,))
            return [clean_user_row_display_name(dict(r)) for r in cur.fetchall()]
    finally:
        release_conn(conn)

@app.put("/auth/users/{user_id}")
async def auth_update_user(user_id: int, data: dict, admin: dict = Depends(require_permission("manage_users"))):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT u.id, r.role_name
                FROM users u
                LEFT JOIN roles r ON r.id = u.role_id
                WHERE u.id=%s AND u.tenant_id=%s AND u.deleted_at IS NULL""",
                (user_id, admin["tenant_id"]))
            user_row = cur.fetchone()
            if not user_row: raise HTTPException(404, "User not found")
            updates, params = [], []
            resolved_role = user_row["role_name"] or "viewer"
            if "display_name" in data:
                updates.append("display_name=%s"); params.append(clean_user_display_name(data["display_name"]))
            if "phone" in data:
                updates.append("phone=%s"); params.append(data["phone"])
            if "status" in data and data["status"] in ("active","inactive"):
                if data["status"] == "inactive":
                    blockers = get_user_deactivation_blockers(conn, admin["tenant_id"], user_id)
                    if blockers["has_blockers"]:
                        log_activity(
                            conn,
                            "user",
                            user_id,
                            "blocked_deactivation",
                            f"Blocked user deactivation for {user_id}",
                            tenant_id=admin["tenant_id"],
                            user_id=admin["user_id"],
                            source_channel="settings",
                            details=blockers,
                        )
                        conn.commit()
                        raise HTTPException(422, "User cannot be deactivated while holding active hierarchy responsibilities")
                updates.append("status=%s"); params.append(data["status"])
            if "role" in data:
                cur.execute("SELECT id FROM roles WHERE role_name=%s", (data["role"],))
                row = cur.fetchone()
                if not row: raise HTTPException(400, f"Unknown role: {data['role']}")
                updates.append("role_id=%s"); params.append(row["id"])
                resolved_role = data["role"]
            if data.get("reset_password_to_default") is True:
                updates.append("password_hash=%s"); params.append(hash_password(DEFAULT_TEMP_PASSWORD))
                updates.append("must_change_password=TRUE")
            if updates:
                updates.append("updated_at=now()")
                params += [user_id, admin["tenant_id"]]
                cur.execute(f"UPDATE users SET {','.join(updates)} WHERE id=%s AND tenant_id=%s", params)
            permissions_payload = normalize_permission_payload(data.get("permissions"))
            if permissions_payload:
                save_user_permission_overrides(conn, admin["tenant_id"], user_id, resolved_role, permissions_payload)
            elif data.get("reset_permission_overrides") is True:
                clear_user_permission_overrides(conn, user_id)
            if not updates and not permissions_payload and data.get("reset_permission_overrides") is not True:
                raise HTTPException(400, "Nothing to update")
            log_activity(
                conn,
                "user",
                user_id,
                "update_user",
                f"Updated user {user_id}",
                tenant_id=admin["tenant_id"],
                user_id=admin["user_id"],
                details={
                    "display_name": data.get("display_name"),
                    "phone": data.get("phone"),
                    "role": data.get("role"),
                    "status": data.get("status"),
                    "permissions_changed": bool(permissions_payload),
                    "reset_password_to_default": data.get("reset_password_to_default") is True,
                    "reset_permission_overrides": data.get("reset_permission_overrides") is True,
                },
                source_channel="settings",
            )
            conn.commit()
            return {"ok": True}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.delete("/auth/users/{user_id}")
async def auth_delete_user(user_id: int, admin: dict = Depends(require_permission("manage_users"))):
    if user_id == admin["user_id"]: raise HTTPException(400, "Cannot delete yourself")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL",
                (user_id, admin["tenant_id"]))
            if not cur.fetchone(): raise HTTPException(404, "User not found")
            blockers = get_user_deactivation_blockers(conn, admin["tenant_id"], user_id)
            if blockers["has_blockers"]:
                log_activity(
                    conn,
                    "user",
                    user_id,
                    "blocked_delete",
                    f"Blocked user delete for {user_id}",
                    tenant_id=admin["tenant_id"],
                    user_id=admin["user_id"],
                    source_channel="settings",
                    details=blockers,
                )
                conn.commit()
                raise HTTPException(422, "User cannot be deleted while holding active hierarchy responsibilities")
            cur.execute("UPDATE users SET deleted_at=now(), status='inactive' WHERE id=%s AND tenant_id=%s", (user_id, admin["tenant_id"]))
            log_activity(conn, "user", user_id, "delete_user", f"Deleted user {user_id}", tenant_id=admin["tenant_id"], user_id=admin["user_id"], source_channel="settings")
            conn.commit()
        return {"ok": True}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.post("/auth/register")
async def auth_register(data: dict, admin: dict = Depends(require_permission("manage_users"))):
    """Admin-only: register new user."""
    email = data.get("email","").strip().lower()
    password = data.get("password","").strip() or DEFAULT_TEMP_PASSWORD
    display_name = clean_user_display_name(data.get("display_name",""))
    if not email or not display_name:
        raise HTTPException(400, "email and display_name required")
    conn = get_db_conn()
    try:
        ok, msg = check_subscription_limit(conn, admin["tenant_id"], "users")
        if not ok: raise HTTPException(429, msg)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE LOWER(email)=%s AND deleted_at IS NULL", (email,))
            if cur.fetchone(): raise HTTPException(409, "Email already registered")
            role_name = data.get("role","worker")
            cur.execute("SELECT id FROM roles WHERE role_name=%s", (role_name,))
            role_row = cur.fetchone()
            role_id = role_row["id"] if role_row else None
            cur.execute("""INSERT INTO users (tenant_id, role_id, first_name, last_name, display_name, email, phone, password_hash, must_change_password, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'active') RETURNING id""",
                (admin["tenant_id"], role_id, data.get("first_name",""), data.get("last_name",""),
                 display_name, email, data.get("phone",""), hash_password(password)))
            uid = cur.fetchone()["id"]
            permissions_payload = normalize_permission_payload(data.get("permissions"))
            if permissions_payload:
                save_user_permission_overrides(conn, admin["tenant_id"], uid, role_name, permissions_payload)
            log_activity(conn, "user", str(uid), "register", f"User {display_name} registered by admin", tenant_id=admin["tenant_id"], user_id=admin["user_id"])
            conn.commit()
        return {
            "id": uid,
            "email": email,
            "display_name": display_name,
            "role": role_name,
            "must_change_password": True,
            "temporary_password": DEFAULT_TEMP_PASSWORD,
            "permissions": get_effective_permissions(conn, admin["tenant_id"], uid, role_name)
        }
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.put("/auth/change-password")
async def auth_change_password(data: dict, user: dict = Depends(require_auth)):
    old_pw = data.get("old_password","")
    new_pw = data.get("new_password","")
    if not old_pw or not new_pw: raise HTTPException(400, "old_password and new_password required")
    if len(new_pw) < 6: raise HTTPException(400, "Password must be at least 6 characters")
    if new_pw == DEFAULT_TEMP_PASSWORD: raise HTTPException(400, "Choose a new password different from the default password")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE id=%s", (user["user_id"],))
            u = cur.fetchone()
            if not u or not verify_password(old_pw, u["password_hash"]):
                raise HTTPException(401, "Old password incorrect")
            cur.execute("UPDATE users SET password_hash=%s, must_change_password=FALSE, updated_at=now() WHERE id=%s", (hash_password(new_pw), user["user_id"]))
            conn.commit()
        return {"status": "password_changed"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

# ========== ONBOARDING ==========

VALID_LEGAL_TYPES = {"sole_trader","ltd","partnership","other"}
VALID_LANGUAGE_MODES = {"single","multi"}
VALID_WORKSPACE_MODES = {"solo","team","business"}
VALID_LANGUAGE_SCOPES = {"internal","customer","voice_input","voice_output"}

# ========== STATE TRANSITION RULES (Blueprint v2 Section 4) ==========
JOB_TRANSITIONS = {
    "nova":              ["v_reseni","pozastaveno","zruseno"],
    "v_reseni":          ["ceka_na_klienta","ceka_na_material","naplanovano","pozastaveno","zruseno"],
    "ceka_na_klienta":   ["v_reseni","naplanovano","pozastaveno","zruseno"],
    "ceka_na_material":  ["naplanovano","v_reseni","pozastaveno","zruseno"],
    "naplanovano":       ["v_realizaci","pozastaveno","zruseno"],
    "v_realizaci":       ["dokonceno","pozastaveno","zruseno"],
    "dokonceno":         ["vyfakturovano","pozastaveno"],
    "vyfakturovano":     ["uzavreno"],
    "pozastaveno":       ["nova","v_reseni","naplanovano","v_realizaci","zruseno"],
    "uzavreno":          [],
    "zruseno":           [],
}
LEAD_TRANSITIONS = {
    "new":                    ["kvalifikovany","zamitnuto"],
    "kvalifikovany":          ["nabidka_odeslana","zamitnuto"],
    "nabidka_odeslana":       ["schvaleno","zamitnuto"],
    "schvaleno":              ["preveden_na_klienta","preveden_na_zakazku"],
    "zamitnuto":              ["new"],
    "preveden_na_klienta":    [],
    "preveden_na_zakazku":    [],
}
INVOICE_TRANSITIONS = {
    "draft":                ["odeslana","stornována"],
    "odeslana":             ["castecne_uhrazena","uhrazena","po_splatnosti","stornována"],
    "castecne_uhrazena":    ["uhrazena","po_splatnosti","stornována"],
    "uhrazena":             [],
    "po_splatnosti":        ["castecne_uhrazena","uhrazena","stornována"],
    "stornována":           [],
}
# Normalize aliases
INVOICE_TRANSITIONS["částečně_uhrazená"] = INVOICE_TRANSITIONS["castecne_uhrazena"]
INVOICE_TRANSITIONS["odeslaná"] = INVOICE_TRANSITIONS["odeslana"]

def validate_state_transition(current_status, new_status, rules, entity_name="entity"):
    """Validate state transition. Returns None if OK, error string if invalid."""
    if current_status == new_status: return None
    allowed = rules.get(current_status)
    if allowed is None: return None  # unknown current state — allow (backward compat)
    if new_status not in allowed:
        return f"{entity_name}: transition '{current_status}' → '{new_status}' not allowed. Allowed: {allowed}"
    return None
WORKSPACE_DEFAULTS = {
    "solo":     {"max_users":1,  "max_clients":500,  "max_jobs":100,  "max_voice":600},
    "team":     {"max_users":10, "max_clients":2000, "max_jobs":500,  "max_voice":3000},
    "business": {"max_users":30, "max_clients":10000,"max_jobs":2000, "max_voice":10000},
}
LANGUAGE_PRESETS = {
    "single_single": {
        "label": "One internal + One customer language",
        "internal_language_mode": "single", "customer_language_mode": "single",
        "languages": [
            {"code":"en","scope":"internal","is_default":True},
            {"code":"en","scope":"customer","is_default":True},
            {"code":"en","scope":"voice_input","is_default":True},
            {"code":"en","scope":"voice_output","is_default":True}
        ]
    },
    "multi_single": {
        "label": "Multiple internal + One customer language",
        "internal_language_mode": "multi", "customer_language_mode": "single",
        "languages": [
            {"code":"cs","scope":"internal","is_default":True},
            {"code":"en","scope":"internal","is_default":False},
            {"code":"en","scope":"customer","is_default":True},
            {"code":"cs","scope":"voice_input","is_default":True},
            {"code":"en","scope":"voice_input","is_default":False},
            {"code":"en","scope":"voice_output","is_default":True}
        ]
    },
    "single_multi": {
        "label": "One internal + Multiple customer languages",
        "internal_language_mode": "single", "customer_language_mode": "multi",
        "languages": [
            {"code":"en","scope":"internal","is_default":True},
            {"code":"en","scope":"customer","is_default":True},
            {"code":"cs","scope":"customer","is_default":False},
            {"code":"pl","scope":"customer","is_default":False},
            {"code":"en","scope":"voice_input","is_default":True},
            {"code":"en","scope":"voice_output","is_default":True},
            {"code":"cs","scope":"voice_output","is_default":False}
        ]
    },
    "multi_multi": {
        "label": "Multiple internal + Multiple customer languages",
        "internal_language_mode": "multi", "customer_language_mode": "multi",
        "languages": [
            {"code":"cs","scope":"internal","is_default":True},
            {"code":"en","scope":"internal","is_default":False},
            {"code":"pl","scope":"internal","is_default":False},
            {"code":"en","scope":"customer","is_default":True},
            {"code":"cs","scope":"customer","is_default":False},
            {"code":"pl","scope":"customer","is_default":False},
            {"code":"cs","scope":"voice_input","is_default":True},
            {"code":"en","scope":"voice_input","is_default":False},
            {"code":"pl","scope":"voice_input","is_default":False},
            {"code":"en","scope":"voice_output","is_default":True},
            {"code":"cs","scope":"voice_output","is_default":False}
        ]
    }
}

@app.get("/onboarding/presets")
async def get_onboarding_presets():
    return {
        "workspace_modes": {k: {"label": k.capitalize(), "defaults": v} for k,v in WORKSPACE_DEFAULTS.items()},
        "language_presets": LANGUAGE_PRESETS,
        "legal_types": list(VALID_LEGAL_TYPES),
        "available_languages": [
            {"code":"en","name":"English"},
            {"code":"cs","name":"Čeština"},
            {"code":"pl","name":"Polski"},
            {"code":"de","name":"Deutsch"},
            {"code":"fr","name":"Français"},
            {"code":"es","name":"Español"},
            {"code":"sk","name":"Slovenčina"},
            {"code":"ro","name":"Română"}
        ]
    }

@app.get("/onboarding/industry-groups")
async def get_industry_groups():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,code,name,sort_order FROM industry_groups ORDER BY sort_order")
            return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)

@app.get("/tenant/config/{tenant_id}")
async def get_tenant_config_endpoint(tenant_id: int):
    conn = get_db_conn()
    try:
        verify_tenant(conn, tenant_id)
        config = get_tenant_config(conn, tenant_id)
        if not config.get("found"):
            raise HTTPException(404, "Tenant config not found. Run onboarding first.")
        # Soft limit warnings
        warnings = []
        limits = config.get("limits")
        if limits:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as c FROM clients WHERE tenant_id=%s AND deleted_at IS NULL",(tenant_id,))
                client_count = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM users WHERE tenant_id=%s AND deleted_at IS NULL",(tenant_id,))
                user_count = cur.fetchone()["c"]
            if limits.get("max_clients") and client_count >= limits["max_clients"] * 0.9:
                warnings.append(f"Approaching client limit: {client_count}/{limits['max_clients']}")
            if limits.get("max_users") and user_count >= limits["max_users"]:
                warnings.append(f"User limit reached: {user_count}/{limits['max_users']}")
        config["warnings"] = warnings
        return config
    finally: release_conn(conn)

@app.put("/tenant/config/{tenant_id}/languages")
async def update_tenant_languages_endpoint(tenant_id: int, data: dict, user: dict = Depends(require_permission("manage_users"))):
    conn = get_db_conn()
    try:
        if user["tenant_id"] != tenant_id:
            raise HTTPException(403, "Permission denied")
        verify_tenant(conn, tenant_id)
        default_customer_lang = data.get("default_customer_language_code")
        default_internal_lang = data.get("default_internal_language_code")
        if not default_customer_lang and not default_internal_lang:
            raise HTTPException(400, "No language update requested")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT internal_language_mode, customer_language_mode,
                    default_internal_language_code, default_customer_language_code
                FROM tenant_operating_profile
                WHERE tenant_id=%s
            """, (tenant_id,))
            profile = cur.fetchone()
            if not profile:
                raise HTTPException(404, "Tenant config not found")
            resolved_internal = default_internal_lang or profile["default_internal_language_code"]
            resolved_customer = default_customer_lang or profile["default_customer_language_code"]
            cur.execute("""
                UPDATE tenant_operating_profile
                SET default_internal_language_code=%s,
                    default_customer_language_code=%s,
                    updated_at=now()
                WHERE tenant_id=%s
            """, (resolved_internal, resolved_customer, tenant_id))

            def upsert_default_language(scope: str, code: str):
                cur.execute("""
                    UPDATE tenant_languages
                    SET is_default = CASE WHEN language_code=%s THEN true ELSE false END
                    WHERE tenant_id=%s AND language_scope=%s
                """, (code, tenant_id, scope))
                cur.execute("""
                    INSERT INTO tenant_languages (tenant_id, language_code, language_scope, is_default, is_active, sort_order)
                    VALUES (%s, %s, %s, true, true, 1)
                    ON CONFLICT (tenant_id, language_code, language_scope) DO UPDATE SET
                        is_default=true,
                        is_active=true
                """, (tenant_id, code, scope))

            if default_internal_lang:
                upsert_default_language("internal", resolved_internal)
                upsert_default_language("voice_input", resolved_internal)
            if default_customer_lang:
                upsert_default_language("customer", resolved_customer)
                upsert_default_language("voice_output", resolved_customer)
            log_activity(
                conn,
                "tenant_config",
                tenant_id,
                "update_languages",
                "Updated tenant language settings",
                tenant_id=tenant_id,
                user_id=user["user_id"],
                details={
                    "default_internal_language_code": resolved_internal,
                    "default_customer_language_code": resolved_customer,
                },
                source_channel="settings",
            )
            conn.commit()
            _tenant_config_cache.pop(tenant_id, None)
        return get_tenant_config(conn, tenant_id)
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        release_conn(conn)

@app.get("/onboarding/industry-subtypes/{group_id}")
async def get_industry_subtypes(group_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,code,name,sort_order FROM industry_subtypes WHERE industry_group_id=%s ORDER BY sort_order",(group_id,))
            return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)

@app.get("/onboarding/status/{tenant_id}")
async def get_onboarding_status(tenant_id: int):
    conn = get_db_conn()
    try:
        verify_tenant(conn, tenant_id)
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,slug,status,legal_type,country_code,timezone,currency FROM tenants WHERE id=%s",(tenant_id,))
            tenant = cur.fetchone()
            if not tenant: raise HTTPException(404,"Tenant not found")
            cur.execute("SELECT * FROM tenant_settings WHERE tenant_id=%s",(tenant_id,))
            settings = cur.fetchone()
            cur.execute("SELECT * FROM tenant_operating_profile WHERE tenant_id=%s",(tenant_id,))
            profile = cur.fetchone()
            cur.execute("SELECT language_code,language_scope,is_default FROM tenant_languages WHERE tenant_id=%s ORDER BY language_scope,sort_order",(tenant_id,))
            languages = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT tip.*, ig.code as group_code, ig.name as group_name, ist.code as subtype_code, ist.name as subtype_name FROM tenant_industry_profile tip LEFT JOIN industry_groups ig ON tip.industry_group_id=ig.id LEFT JOIN industry_subtypes ist ON tip.industry_subtype_id=ist.id WHERE tip.tenant_id=%s",(tenant_id,))
            industry = cur.fetchone()
            cur.execute("SELECT * FROM subscription_limits WHERE tenant_id=%s",(tenant_id,))
            limits = cur.fetchone()
            return {
                "tenant": dict(tenant),
                "settings": dict(settings) if settings else None,
                "operating_profile": dict(profile) if profile else None,
                "languages": languages,
                "industry": dict(industry) if industry else None,
                "subscription_limits": dict(limits) if limits else None,
                "is_complete": all([settings, profile, languages, industry, limits])
            }
    finally: release_conn(conn)

@app.post("/onboarding/company-setup")
async def company_setup(data: dict):
    # --- VALIDATION ---
    errors = []
    company_name = data.get("company_name","").strip()
    if not company_name: errors.append("company_name is required")
    legal_type = data.get("legal_type","sole_trader")
    if legal_type not in VALID_LEGAL_TYPES: errors.append(f"legal_type must be one of {VALID_LEGAL_TYPES}")
    workspace_mode = data.get("workspace_mode","solo")
    if workspace_mode not in VALID_WORKSPACE_MODES: errors.append(f"workspace_mode must be one of {VALID_WORKSPACE_MODES}")
    internal_language_mode = data.get("internal_language_mode","single")
    if internal_language_mode not in VALID_LANGUAGE_MODES: errors.append(f"internal_language_mode must be one of {VALID_LANGUAGE_MODES}")
    customer_language_mode = data.get("customer_language_mode","single")
    if customer_language_mode not in VALID_LANGUAGE_MODES: errors.append(f"customer_language_mode must be one of {VALID_LANGUAGE_MODES}")
    default_internal_lang = data.get("default_internal_language_code","en")
    default_customer_lang = data.get("default_customer_language_code","en")
    industry_group_id = data.get("industry_group_id")
    industry_subtype_id = data.get("industry_subtype_id")
    max_active_users = data.get("max_active_users", WORKSPACE_DEFAULTS.get(workspace_mode,{}).get("max_users",1))
    tenant_id = data.get("tenant_id", 1)
    languages = data.get("languages", [])
    # Validate language entries
    for lang_entry in languages:
        scope = lang_entry.get("scope","")
        if scope not in VALID_LANGUAGE_SCOPES:
            errors.append(f"Invalid language_scope: {scope}")
    if errors: raise HTTPException(422, {"errors": errors})

    conn = get_db_conn()
    try:
        verify_tenant(conn, tenant_id)
        with conn.cursor() as cur:
            # 1. UPDATE TENANT (idempotent — update existing)
            cur.execute("""UPDATE tenants SET
                name=%s, legal_type=%s, company_registration_no=%s, vat_no=%s,
                phone=%s, email=%s, website=%s, country_code=%s, timezone=%s, currency=%s,
                updated_at=now()
                WHERE id=%s""",
                (company_name, legal_type, data.get("company_registration_no"),
                 data.get("vat_no"), data.get("phone"), data.get("email"),
                 data.get("website"), data.get("country_code","GB"),
                 data.get("timezone","Europe/London"), data.get("currency","GBP"),
                 tenant_id))

            # 2. TENANT SETTINGS (upsert — one per tenant)
            cur.execute("SELECT id FROM tenant_settings WHERE tenant_id=%s",(tenant_id,))
            if cur.fetchone():
                cur.execute("""UPDATE tenant_settings SET
                    date_format=%s, time_format=%s, voice_enabled=%s, updated_at=now()
                    WHERE tenant_id=%s""",
                    (data.get("date_format","DD/MM/YYYY"), data.get("time_format","24h"),
                     data.get("voice_enabled",True), tenant_id))
            else:
                cur.execute("""INSERT INTO tenant_settings (tenant_id, date_format, time_format, voice_enabled)
                    VALUES (%s,%s,%s,%s)""",
                    (tenant_id, data.get("date_format","DD/MM/YYYY"), data.get("time_format","24h"),
                     data.get("voice_enabled",True)))

            # 3. TENANT OPERATING PROFILE (upsert — one per tenant)
            cur.execute("SELECT id FROM tenant_operating_profile WHERE tenant_id=%s",(tenant_id,))
            if cur.fetchone():
                cur.execute("""UPDATE tenant_operating_profile SET
                    internal_language_mode=%s, customer_language_mode=%s,
                    default_internal_language_code=%s, default_customer_language_code=%s,
                    voice_input_strategy=%s, voice_output_strategy=%s,
                    workspace_mode=%s, max_active_users=%s,
                    industry_group_id=%s, industry_subtype_id=%s, updated_at=now()
                    WHERE tenant_id=%s""",
                    (internal_language_mode, customer_language_mode,
                     default_internal_lang, default_customer_lang,
                     data.get("voice_input_strategy","auto_detect"),
                     data.get("voice_output_strategy","customer_default"),
                     workspace_mode, max_active_users,
                     industry_group_id, industry_subtype_id, tenant_id))
            else:
                cur.execute("""INSERT INTO tenant_operating_profile
                    (tenant_id, internal_language_mode, customer_language_mode,
                     default_internal_language_code, default_customer_language_code,
                     voice_input_strategy, voice_output_strategy,
                     workspace_mode, max_active_users, industry_group_id, industry_subtype_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, internal_language_mode, customer_language_mode,
                     default_internal_lang, default_customer_lang,
                     data.get("voice_input_strategy","auto_detect"),
                     data.get("voice_output_strategy","customer_default"),
                     workspace_mode, max_active_users,
                     industry_group_id, industry_subtype_id))

            # 4. TENANT LANGUAGES (replace — delete old, insert new)
            if languages:
                cur.execute("DELETE FROM tenant_languages WHERE tenant_id=%s",(tenant_id,))
                for i, lang_entry in enumerate(languages):
                    cur.execute("""INSERT INTO tenant_languages
                        (tenant_id, language_code, language_scope, is_default, sort_order)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (tenant_id, language_code, language_scope) DO NOTHING""",
                        (tenant_id, lang_entry.get("code","en"), lang_entry.get("scope","internal"),
                         lang_entry.get("is_default",False), i+1))

            # 5. TENANT INDUSTRY PROFILE (upsert primary)
            if industry_group_id:
                cur.execute("SELECT id FROM tenant_industry_profile WHERE tenant_id=%s AND is_primary=true",(tenant_id,))
                if cur.fetchone():
                    cur.execute("""UPDATE tenant_industry_profile SET
                        industry_group_id=%s, industry_subtype_id=%s, updated_at=now()
                        WHERE tenant_id=%s AND is_primary=true""",
                        (industry_group_id, industry_subtype_id, tenant_id))
                else:
                    cur.execute("""INSERT INTO tenant_industry_profile
                        (tenant_id, industry_group_id, industry_subtype_id, is_primary)
                        VALUES (%s,%s,%s,true)""",
                        (tenant_id, industry_group_id, industry_subtype_id))

            # 6. SUBSCRIPTION LIMITS (upsert based on workspace_mode)
            ws = WORKSPACE_DEFAULTS.get(workspace_mode, WORKSPACE_DEFAULTS["solo"])
            cur.execute("SELECT id FROM subscription_limits WHERE tenant_id=%s",(tenant_id,))
            if cur.fetchone():
                cur.execute("""UPDATE subscription_limits SET
                    max_users=%s, max_clients=%s, max_jobs_per_month=%s, max_voice_minutes=%s, updated_at=now()
                    WHERE tenant_id=%s""",
                    (max_active_users, ws["max_clients"], ws["max_jobs"], ws["max_voice"], tenant_id))
            else:
                cur.execute("""INSERT INTO subscription_limits
                    (tenant_id, max_users, max_clients, max_jobs_per_month, max_voice_minutes)
                    VALUES (%s,%s,%s,%s,%s)""",
                    (tenant_id, max_active_users, ws["max_clients"], ws["max_jobs"], ws["max_voice"]))

            # 7. AUDIT LOG
            audit_config_change(conn, tenant_id, "onboarding_setup",
                f"Company: {company_name}, mode: {workspace_mode}, legal: {legal_type}, "
                f"int_lang: {internal_language_mode}/{default_internal_lang}, "
                f"cust_lang: {customer_language_mode}/{default_customer_lang}")

            conn.commit()
        return {"status":"ok","tenant_id":tenant_id,"company_name":company_name,"workspace_mode":workspace_mode}
    except HTTPException: raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.post("/session/summarize")
async def summarize_session(request: Request):
    """Summarize a completed dialog session and store as long-term memory."""
    try:
        body = await request.json()
        history = body.get("history", [])          # [{role, content}, ...]
        user_id = body.get("user_id")
        tenant_id = body.get("tenant_id", 1)
        internal_language = body.get("internal_language", "cs")

        if not history or len(history) < 2:
            return {"stored": False, "reason": "too_short"}

        if not ai_client:
            return {"stored": False, "reason": "no_ai"}

        # Build readable transcript
        transcript = "
".join(
            f"{'User' if m.get('role') == 'user' else 'Assistant'}: {m.get('content', '')}"
            for m in history
            if m.get('content', '').strip()
        )

        # Ask GPT to summarize
        lang_label = {"cs": "Czech", "en": "English", "pl": "Polish"}.get(internal_language[:2], "English")
        summary_resp = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Summarize the following voice assistant conversation in {lang_label}. "
                        f"Focus on: what was decided, what was created/edited, important names/dates/numbers, "
                        f"and any open tasks or follow-ups. Be concise (3-6 sentences max). "
                        f"Start with the date/time if mentioned."
                    )
                },
                {"role": "user", "content": transcript}
            ],
            max_tokens=300
        )
        summary = (summary_resp.choices[0].message.content or "").strip()
        if not summary:
            return {"stored": False, "reason": "empty_summary"}

        # Store as session memory (type='session')
        conn = get_db_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO crm.assistant_memory
                   (tenant_id, user_id, memory_type, content, source, is_active)
                   VALUES (%s, %s, 'session', %s, 'voice_dialog', TRUE)""",
                (tenant_id, user_id, summary)
            )
            # Keep only last 10 session memories per user to avoid bloat
            cur.execute(
                """DELETE FROM crm.assistant_memory
                   WHERE tenant_id = %s AND user_id IS NOT DISTINCT FROM %s
                     AND memory_type = 'session'
                     AND id NOT IN (
                         SELECT id FROM crm.assistant_memory
                         WHERE tenant_id = %s AND user_id IS NOT DISTINCT FROM %s
                           AND memory_type = 'session'
                         ORDER BY created_at DESC LIMIT 10
                     )""",
                (tenant_id, user_id, tenant_id, user_id)
            )
            conn.commit()
            return {"stored": True, "summary": summary}
        except Exception as e:
            conn.rollback()
            return {"stored": False, "error": str(e)}
        finally:
            release_conn(conn)
    except Exception as e:
        return {"stored": False, "error": str(e)}

@app.post("/translate")
async def translate_message(request: Request):
    """Translate a customer-facing message to the configured customer language."""
    try:
        body = await request.json()
        text = (body.get("text") or "").strip()
        target_language = (body.get("target_language") or "en").strip()
        if not text:
            return {"translated": "", "target_language": target_language}
        translated = translate_customer_message(text, target_language)
        return {"translated": translated, "target_language": target_language}
    except Exception as e:
        return {"translated": body.get("text", "") if "body" in dir() else "", "error": str(e)}

@app.get("/health")
async def health():
    try:
        conn = get_db_conn(); release_conn(conn)
        return {"status":"ok","version":"1.2a","ai":bool(OPENAI_API_KEY)}
    except: return {"status":"error"}

@app.get("/debug/test-ai")
async def test_ai():
    if not ai_client: return {"status":"error","message":"OPENAI_API_KEY not set"}
    try:
        r = ai_client.chat.completions.create(model="gpt-4o",messages=[{"role":"user","content":"Rekni ahoj"}],max_tokens=20)
        return {"status":"ok","response":r.choices[0].message.content}
    except Exception as e: return {"status":"error","message":str(e)}

@app.get("/debug/schema-audit")
async def schema_audit():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT table_schema, table_name FROM information_schema.tables 
                WHERE table_schema IN ('crm','public') AND table_type='BASE TABLE' ORDER BY 1,2""")
            tables = cur.fetchall()
            cur.execute("""SELECT table_schema, table_name, column_name, data_type, 
                is_nullable, column_default FROM information_schema.columns 
                WHERE table_schema IN ('crm','public') ORDER BY table_schema, table_name, ordinal_position""")
            columns = cur.fetchall()
            cur.execute("""SELECT tc.table_schema, tc.table_name, kcu.column_name,
                ccu.table_name AS ref_table, ccu.column_name AS ref_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema IN ('crm','public')""")
            fks = cur.fetchall()
            cur.execute("""SELECT schemaname, tablename, indexname FROM pg_indexes 
                WHERE schemaname IN ('crm','public') ORDER BY 1,2,3""")
            indexes = cur.fetchall()
            return {"tables":[dict(t) for t in tables], "columns":[dict(c) for c in columns],
                    "foreign_keys":[dict(f) for f in fks], "indexes":[dict(i) for i in indexes]}
    finally: release_conn(conn)

# ========== PRICING ENGINE ==========
def map_pricing_rule_to_service_rate(rule_type, rule_key=None):
    if rule_type == "waste_rate":
        return "garden_waste_bulkbag"
    if rule_type != "task_rate":
        return None
    low = str(rule_key or "").strip().lower().replace(" ", "_")
    if low in ("pruning", "hedge", "hedge_trimming", "trim", "trimming"):
        return "hedge_trimming"
    if low in ("arborist", "arborist_works", "tree", "tree_surgeon", "tree-surgeon", "tree_surgery"):
        return "arborist_works"
    return "garden_maintenance"

def resolve_rate(conn, tenant_id, rule_type, rule_key=None, job_id=None, client_id=None):
    """Priority: job → client → system default"""
    with conn.cursor() as cur:
        if job_id:
            cur.execute("SELECT rate FROM pricing_rules WHERE tenant_id=%s AND scope='job' AND scope_id=%s AND rule_type=%s AND (rule_key=%s OR rule_key IS NULL) ORDER BY rule_key DESC NULLS LAST LIMIT 1",
                (tenant_id,job_id,rule_type,rule_key))
            r = cur.fetchone()
            if r: return float(r['rate'])
        if client_id:
            cur.execute("SELECT rate FROM pricing_rules WHERE tenant_id=%s AND scope='client' AND scope_id=%s AND rule_type=%s AND (rule_key=%s OR rule_key IS NULL) ORDER BY rule_key DESC NULLS LAST LIMIT 1",
                (tenant_id,client_id,rule_type,rule_key))
            r = cur.fetchone()
            if r: return float(r['rate'])
        cur.execute("SELECT rate FROM pricing_rules WHERE tenant_id=%s AND scope='system' AND rule_type=%s AND (rule_key=%s OR rule_key IS NULL) ORDER BY rule_key DESC NULLS LAST LIMIT 1",
            (tenant_id,rule_type,rule_key))
        r = cur.fetchone()
        if r: return float(r['rate'])
    mapped_service_rate = map_pricing_rule_to_service_rate(rule_type, rule_key)
    if mapped_service_rate:
        rate = get_effective_rate(conn, tenant_id, client_id=client_id, rate_type=mapped_service_rate)
        if rate > 0:
            return rate
    defaults = {"worker_rate":35.0,"task_rate":35.0,"waste_rate":80.0,"material_price":0.0}
    return defaults.get(rule_type, 0.0)

# ========== DIALOG STATE MACHINE ==========
DIALOG_STEPS = ["client","client_create_name","date","workers","total_hours","entries","validate_hours","waste","materials","notes","summary","confirm"]
VALID_TRANSITIONS = {
    "client": ["client","client_create_name","date"],
    "client_create_name": ["client_create_name","client","date"],
    "date": ["date","workers"],
    "workers": ["workers","date","total_hours"],
    "total_hours": ["total_hours","entries"],
    "entries": ["entries","validate_hours","waste"],
    "validate_hours": ["validate_hours","waste","entries"],
    "materials": ["materials","notes"],
    "waste": ["waste","materials"],
    "notes": ["notes","summary"],
    "summary": ["summary","confirm","date","client","workers","total_hours","entries","materials","waste","notes"],
    "confirm": ["confirm"],
}
def validate_transition(current_step, next_step):
    return next_step in VALID_TRANSITIONS.get(current_step, [])
DIALOG_PROMPTS = {
    "client": {"en":"Which client did you work for? You can also say 'new client'.","cs":"U kterého klienta jsi pracoval? Můžeš také říct 'nový klient'.","pl":"U którego klienta pracowałeś? Możesz też powiedzieć 'nowy klient'."},
    "client_create_name": {"en":"What is the new client name?","cs":"Jak se jmenuje nový klient?","pl":"Jak nazywa się nowy klient?"},
    "date": {"en":"Which date is this work report for? Say a date like 2026-04-18 or 18.04.2026.","cs":"Na jaké datum je tento výkaz práce? Řekni datum například 2026-04-18 nebo 18.04.2026.","pl":"Na jaką datę jest ten raport pracy? Powiedz datę na przykład 2026-04-18 albo 18.04.2026."},
    "workers": {"en":"Who worked? (names)","cs":"Kdo pracoval? (jména)","pl":"Kto pracował? (imiona)"},
    "total_hours": {"en":"How many hours total?","cs":"Kolik hodin celkem?","pl":"Ile godzin łącznie?"},
    "entries": {"en":"How many hours pruning?","cs":"Kolik hodin prořez?","pl":"Ile godzin przycinanie?"},
    "validate_hours": {"en":"Hours don't match total. Fix entries or total.","cs":"Hodiny nesedí s celkem. Oprav položky nebo celkem.","pl":"Godziny się nie zgadzają. Popraw pozycje lub sumę."},
    "materials": {"en":"Any materials used? (name, quantity, price) or 'no'","cs":"Použili jste materiál? (název, množství, cena) nebo 'ne'","pl":"Czy użyto materiałów? (nazwa, ilość, cena) lub 'nie'"},
    "waste": {"en":"How many bulk bags of waste? (number or 'none')","cs":"Kolik pytlů odpadu? (číslo nebo 'žádný')","pl":"Ile worków odpadów? (liczba lub 'żaden')"},
    "notes": {"en":"Any notes? (or 'no')","cs":"Chceš přidat poznámku? (nebo 'ne')","pl":"Chcesz dodać notatkę? (lub 'nie')"},
    "summary": {"en":"Here is the day summary. Say 'another day' to add more dates, 'confirm' to save all reports, or 'edit [field]' to change.","cs":"Tady je shrnutí dne. Řekni 'další den' pro přidání dalšího data, 'potvrdit' pro uložení všech reportů nebo 'oprav [pole]' pro změnu.","pl":"Oto podsumowanie dnia. Powiedz 'kolejny dzień', aby dodać następną datę, 'potwierdź', aby zapisać wszystkie raporty, albo 'popraw [pole]', aby coś zmienić."},
    "confirm": {"en":"Work reports saved.","cs":"Reporty uloženy.","pl":"Raporty zapisane."},
}
def get_prompt(step, lang="en"):
    return DIALOG_PROMPTS.get(step,{}).get(lang, DIALOG_PROMPTS.get(step,{}).get("en",""))

def parse_new_client_command(text: str) -> tuple[bool, Optional[str]]:
    raw = (text or "").strip()
    low = raw.lower()
    prefixes = [
        "new client", "create client", "add client",
        "novy klient", "nový klient", "vytvor klienta", "vytvoř klienta", "pridat klienta", "přidat klienta",
        "nowy klient", "utworz klienta", "utwórz klienta", "dodaj klienta",
    ]
    for prefix in prefixes:
        if low == prefix:
            return True, None
        if low.startswith(prefix + " "):
            remainder = raw[len(prefix):].strip(" :-,")
            for leading in ("named ", "called ", "jmenem ", "s nazvem ", "s názvem ", "o nazwie "):
                if remainder.lower().startswith(leading):
                    remainder = remainder[len(leading):].strip()
                    break
            return True, remainder or None
    return False, None

def create_voice_work_report_client(conn, tenant_id: int, actor_user_id: int, client_name: str, lang: str) -> dict:
    name = clean_contact_display_name(client_name)
    if not name:
        raise HTTPException(422, "Client name is required")
    owner = validate_active_user(conn, tenant_id, actor_user_id, "client owner")
    actor_name = get_user_display_name(conn, tenant_id, actor_user_id) or owner["display_name"] or "system"
    code = f"CL-{uuid.uuid4().hex[:6].upper()}"
    planning_dt = next_business_day_at_nine()
    planning_note = tr_lang(
        lang,
        "System placeholder task created from voice work report. Review and replace with the real next action.",
        "Systémový placeholder úkol vytvořený z hlasového výkazu práce. Zkontroluj ho a nahraď skutečným dalším krokem.",
        "Systemowe zadanie zastępcze utworzone z głosowego raportu pracy. Sprawdź je i zastąp właściwym kolejnym krokiem."
    )
    action_title = tr_lang(lang, "Fill in next action", "Doplnit další krok", "Uzupełnić kolejny krok")
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO clients
            (client_code, display_name, status, tenant_id, owner_user_id, hierarchy_status)
            VALUES (%s,%s,'active',%s,%s,'pending')
            RETURNING id, display_name""",
            (code, name, tenant_id, int(owner["id"])))
        client = cur.fetchone()
    next_action = create_workflow_task(
        conn,
        tenant_id,
        {
            "title": action_title,
            "assigned_user_id": int(owner["id"]),
            "assigned_to": owner["display_name"],
            "planned_start_at": format_planning_datetime(planning_dt),
            "deadline": planning_dt.strftime("%Y-%m-%d"),
            "priority": "vysoka",
            "planning_note": planning_note,
            "client_id": int(client["id"]),
            "client_name": client["display_name"],
        },
        actor_name=actor_name,
        default_client_id=int(client["id"]),
        default_client_name=client["display_name"],
        source="voice_client_create",
    )
    set_client_next_action(conn, tenant_id, int(client["id"]), str(next_action["id"]))
    validation = validate_client_hierarchy(conn, tenant_id, int(client["id"]))
    if not validation["valid"]:
        raise HTTPException(422, f"Client hierarchy invalid: {', '.join(validation['issues'])}")
    log_activity(
        conn,
        "client",
        int(client["id"]),
        "create",
        f"Voice-created client {client['display_name']}",
        tenant_id=tenant_id,
        user_id=actor_user_id,
        source_channel="voice",
        details={"next_action_task_id": str(next_action["id"])},
    )
    return {"id": int(client["id"]), "display_name": client["display_name"], "next_action_task_id": str(next_action["id"])}

WORK_REPORT_DAY_FIELDS = [
    "work_date", "workers", "total_hours", "entries", "materials", "waste", "notes",
    "grand_total", "_entry_sub", "_entry_type_name"
]

def parse_voice_work_date(text: str) -> Optional[str]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        now = datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/London")))
    except Exception:
        now = datetime.now()
    low = raw.lower().strip()
    normalized_words = unicodedata.normalize("NFKD", low).encode("ascii", "ignore").decode("ascii")
    normalized_words = "".join(ch if ch.isalnum() else " " for ch in normalized_words)
    tokens = set(normalized_words.split())
    if low in {"today", "dnes", "dneska", "dzisiaj"} or tokens.intersection({"today", "dnes", "dneska", "dzisiaj"}):
        return now.strftime("%Y-%m-%d")
    if low in {"yesterday", "vcera", "včera", "wczoraj"} or tokens.intersection({"yesterday", "vcera", "wczoraj"}):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if low in {"tomorrow", "zitra", "zítra", "jutro"} or tokens.intersection({"tomorrow", "zitra", "jutro"}):
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cleaned = raw.replace(" ", "")
    formats = ["%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    for fmt in ("%d.%m.", "%d/%m", "%d-%m"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            parsed = parsed.replace(year=now.year)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

VOICE_NUMBER_WORDS = {
    # Czech, normalized without diacritics.
    "nula": 0, "jeden": 1, "jedna": 1, "jedno": 1, "prvni": 1,
    "dva": 2, "dve": 2, "druhy": 2, "druha": 2,
    "tri": 3, "treti": 3, "tree": 3, "free": 3, "true": 3, "try": 3,
    "ctyri": 4, "ctvrty": 4, "four": 4, "for": 4,
    "pet": 5, "pat": 5, "pad": 5, "pete": 5, "paty": 5, "sest": 6, "sesty": 6, "sedm": 7,
    "osmy": 8, "osm": 8, "devet": 9, "devaty": 9, "deset": 10,
    "jedenact": 11, "dvanact": 12, "trinact": 13, "ctrnact": 14,
    "patnact": 15, "sestnact": 16, "sedmnact": 17, "osmnact": 18,
    "devatenact": 19,
    # English.
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
    # Polish, normalized without diacritics.
    "jeden": 1, "jedna": 1, "dwa": 2, "dwie": 2, "trzy": 3,
    "cztery": 4, "piec": 5, "szesc": 6, "siedem": 7, "osiem": 8,
    "dziewiec": 9, "dziesiec": 10, "jedenascie": 11, "dwanascie": 12,
    "trzynascie": 13, "czternascie": 14, "pietnascie": 15,
    "szesnascie": 16, "siedemnascie": 17, "osiemnascie": 18,
    "dziewietnascie": 19,
}

VOICE_NUMBER_TENS = {
    "dvacet": 20, "tricet": 30, "ctyricet": 40, "padesat": 50,
    "sedesat": 60, "sedmdesat": 70, "osmdesat": 80, "devadesat": 90,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "dwadziescia": 20, "trzydziesci": 30, "czterdziesci": 40,
    "piecdziesiat": 50, "szescdziesiat": 60, "siedemdziesiat": 70,
    "osiemdziesiat": 80, "dziewiecdziesiat": 90,
}

VOICE_NUMBER_UNITS = {
    "h", "hodin", "hodina", "hodiny", "hodinu", "hodinama", "cas", "casy",
    "hour", "hours", "godzin", "godzina", "godziny", "godzine",
    "pytel", "pytle", "pytlu", "pytel", "pytle", "bags", "bag", "bulkbag",
    "liber", "pound", "pounds", "ks", "kus", "kusu", "pieces", "piece",
    "krat", "x", "razy", "times",
}

VOICE_NUMBER_FILLERS = {
    "a", "and", "plus", "minus", "asi", "cca", "okolo", "zhruba", "about",
    "approximately", "kolem", "circa", "oraz", "i",
}

VOICE_NEGATIVE_WORDS = {
    "ne", "nee", "neee", "nah", "non", "no", "nope", "none", "nothing", "nic", "zadny", "zadna",
    "zadne", "zadnej", "zaden", "zadnou", "bez", "nie", "brak", "zaden",
    "nemam", "nemame", "neni", "nepouzili", "nepouzito", "nebyl", "nebylo",
    "skip", "preskoc", "preskocit", "dalsi", "dal", "dalej", "zadnych",
    "zadneho", "zadne", "zaden", "ani", "niczego",
}

VOICE_POSITIVE_WORDS = {
    "ano", "jo", "jasne", "potvrdit", "potvrzuji", "souhlasim", "ulozit",
    "yes", "yeah", "yep", "confirm", "save", "tak", "potwierdz", "zapisz",
}

def normalize_voice_text(text: str) -> str:
    raw = unicodedata.normalize("NFKD", (text or "").strip().lower())
    ascii_text = raw.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9,.\-+]+", " ", ascii_text).strip()

def voice_tokens(text: str) -> list[str]:
    normalized = normalize_voice_text(text)
    return [token for token in re.split(r"[\s,.;:!?]+", normalized) if token]

def extract_assistant_memory_command(text: str) -> Optional[tuple[str, str, str]]:
    raw = (text or "").strip()
    if not raw:
        return None
    remember_patterns = [
        r"^\s*(zapamatuj si|pamatuj si|uloz si|ulož si|zapis si|zapiš si|remember that|remember|zapamietaj|zapamiętaj)\s+(.+)$",
    ]
    forget_patterns = [
        r"^\s*(zapomen na|zapomeň na|zapomen|zapomeň|smaz z pameti|smaž z paměti|forget that|forget|zapomnij)\s+(.+)$",
    ]
    for pattern in remember_patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if match:
            content = match.group(2).strip()
            memory_type = "medium" if any(token in normalize_voice_text(raw) for token in ["strednedobe", "stredni", "medium"]) else "long"
            return ("remember", content, memory_type) if content else None
    for pattern in forget_patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if match:
            query = match.group(2).strip()
            return ("forget", query, "long") if query else None
    return None

def load_assistant_memories(conn, tenant_id: int, user_id: Optional[int], limit: int = 30) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, memory_type, content, updated_at
            FROM assistant_memory
            WHERE tenant_id=%s
              AND is_active=TRUE
              AND (user_id IS NULL OR user_id IS NOT DISTINCT FROM %s)
            ORDER BY updated_at DESC, id DESC
            LIMIT %s
            """,
            (tenant_id, user_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]

def remember_assistant_memory(conn, tenant_id: int, user_id: Optional[int], content: str, memory_type: str = "long") -> dict:
    clean_content = (content or "").strip()
    if not clean_content:
        raise ValueError("memory content is empty")
    clean_type = memory_type if memory_type in {"medium", "long"} else "long"
    normalized = normalize_voice_text(clean_content)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id
            FROM assistant_memory
            WHERE tenant_id=%s
              AND user_id IS NOT DISTINCT FROM %s
              AND normalized_content=%s
              AND is_active=TRUE
            LIMIT 1
            """,
            (tenant_id, user_id, normalized),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE assistant_memory SET updated_at=now(), memory_type=%s WHERE id=%s RETURNING id, content, memory_type",
                (clean_type, existing["id"]),
            )
            row = cur.fetchone()
            return {"id": row["id"], "content": row["content"], "memory_type": row["memory_type"], "created": False}
        cur.execute(
            """
            INSERT INTO assistant_memory (tenant_id, user_id, memory_type, content, normalized_content, source)
            VALUES (%s, %s, %s, %s, %s, 'voice')
            RETURNING id, content, memory_type
            """,
            (tenant_id, user_id, clean_type, clean_content, normalized),
        )
        row = cur.fetchone()
        return {"id": row["id"], "content": row["content"], "memory_type": row["memory_type"], "created": True}

def forget_assistant_memory(conn, tenant_id: int, user_id: Optional[int], query: str) -> dict:
    clean_query = (query or "").strip()
    if not clean_query:
        raise ValueError("memory forget query is empty")
    normalized_query = normalize_voice_text(clean_query)
    forget_all = normalized_query in {"vse", "vsechno", "vsetko", "all", "everything", "wszystko"}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if forget_all:
            cur.execute(
                """
                UPDATE assistant_memory
                SET is_active=FALSE, forgotten_at=now(), updated_at=now()
                WHERE tenant_id=%s
                  AND user_id IS NOT DISTINCT FROM %s
                  AND is_active=TRUE
                RETURNING id, content
                """,
                (tenant_id, user_id),
            )
        else:
            cur.execute(
                """
                UPDATE assistant_memory
                SET is_active=FALSE, forgotten_at=now(), updated_at=now()
                WHERE tenant_id=%s
                  AND user_id IS NOT DISTINCT FROM %s
                  AND is_active=TRUE
                  AND (normalized_content LIKE %s OR content ILIKE %s)
                RETURNING id, content
                """,
                (tenant_id, user_id, f"%{normalized_query}%", f"%{clean_query}%"),
            )
        rows = [dict(row) for row in cur.fetchall()]
        return {"count": len(rows), "items": rows[:10]}

def is_voice_negative_response(text: str, *, include_zero: bool = False) -> bool:
    normalized = normalize_voice_text(text)
    if not normalized:
        return False
    tokens = voice_tokens(text)
    compact = " ".join(tokens)
    if compact in VOICE_NEGATIVE_WORDS:
        return True
    if tokens and (tokens[0] in VOICE_NEGATIVE_WORDS or any(token in VOICE_NEGATIVE_WORDS for token in tokens)):
        return True
    if include_zero:
        parsed = parse_voice_number(text)
        if parsed == 0:
            return True
    return False

def is_voice_positive_response(text: str) -> bool:
    tokens = voice_tokens(text)
    if not tokens:
        return False
    compact = " ".join(tokens)
    return compact in VOICE_POSITIVE_WORDS or any(token in VOICE_POSITIVE_WORDS for token in tokens)

def _parse_voice_number_tokens(tokens: list[str]) -> Optional[float]:
    current = 0.0
    total = 0.0
    found = False
    for token in tokens:
        if not token or token in VOICE_NUMBER_UNITS or token in VOICE_NUMBER_FILLERS:
            continue
        if token in {"pul", "pol", "half"}:
            current += 0.5
            found = True
            continue
        if token in {"sto", "hundred", "setka"}:
            current = (current or 1) * 100
            found = True
            continue
        value = VOICE_NUMBER_WORDS.get(token)
        if value is not None:
            current += value
            found = True
            continue
        value = VOICE_NUMBER_TENS.get(token)
        if value is not None:
            current += value
            found = True
            continue
        return None
    if not found:
        return None
    return total + current

def parse_voice_number(text: str) -> Optional[float]:
    normalized = normalize_voice_text(text)
    if not normalized:
        return None
    numeric = re.search(r"[-+]?\d+(?:[,.]\d+)?", normalized)
    if numeric:
        try:
            return float(numeric.group(0).replace(",", "."))
        except ValueError:
            return None
    tokens = voice_tokens(text)
    if not tokens:
        return None
    half_tokens = {"pul", "pol", "half"}
    if any(token in half_tokens for token in tokens):
        half_index = next(i for i, token in enumerate(tokens) if token in half_tokens)
        base_tokens = [token for token in tokens[:half_index] if token not in VOICE_NUMBER_FILLERS]
        if not base_tokens:
            return 0.5
        base = _parse_voice_number_tokens(base_tokens)
        if base is not None:
            return base + 0.5
    parsed = _parse_voice_number_tokens(tokens)
    if parsed is not None:
        return parsed
    numberish = set(VOICE_NUMBER_WORDS) | set(VOICE_NUMBER_TENS) | {"sto", "hundred", "setka"} | half_tokens | VOICE_NUMBER_FILLERS | VOICE_NUMBER_UNITS
    best: Optional[float] = None
    for start in range(len(tokens)):
        if tokens[start] not in numberish:
            continue
        window = []
        for token in tokens[start:]:
            if token not in numberish:
                break
            window.append(token)
            candidate = _parse_voice_number_tokens(window)
            if candidate is not None:
                best = candidate
        if best is not None:
            return best
    return None

def extract_current_report_day(ctx: dict) -> dict:
    day = {
        "client_id": ctx.get("client_id"),
        "client_name": ctx.get("client_name"),
        "job_id": ctx.get("job_id"),
        "work_date": ctx.get("work_date"),
        "workers": ctx.get("workers") or [],
        "total_hours": ctx.get("total_hours", 0),
        "entries": ctx.get("entries") or [],
        "materials": ctx.get("materials") or [],
        "waste": ctx.get("waste") or {"qty": 0, "rate": 0, "total": 0},
        "notes": ctx.get("notes"),
        "grand_total": ctx.get("grand_total", 0),
    }
    return json.loads(json.dumps(day, ensure_ascii=False))

def reset_current_report_day(ctx: dict, work_date: Optional[str] = None):
    preserved = {
        "client_id": ctx.get("client_id"),
        "client_name": ctx.get("client_name"),
        "job_id": ctx.get("job_id"),
        "language": ctx.get("language", "en"),
        "report_days": ctx.get("report_days") or [],
    }
    for key in WORK_REPORT_DAY_FIELDS:
        ctx.pop(key, None)
    ctx.update(preserved)
    ctx["work_date"] = work_date or datetime.now().strftime("%Y-%m-%d")
    return ctx

def append_current_report_day(ctx: dict) -> dict:
    day = extract_current_report_day(ctx)
    ctx.setdefault("report_days", []).append(day)
    return day

def normalize_voice_name_key(value: Optional[str]) -> str:
    raw = unicodedata.normalize("NFKD", (value or "").strip().lower())
    ascii_text = raw.encode("ascii", "ignore").decode("ascii")
    chars = []
    prev_space = False
    for ch in ascii_text:
        if ch.isalnum():
            chars.append(ch)
            prev_space = False
        elif not prev_space:
            chars.append(" ")
            prev_space = True
    return "".join(chars).strip()

def split_voice_worker_fragments(text: str) -> List[str]:
    normalized = normalize_voice_name_key(text)
    if not normalized:
        return []
    for separator in (" and ", " a ", " i ", " plus ", "&", "+", ";", "/"):
        normalized = normalized.replace(separator, ",")
    fragments = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(fragments) != 1:
        return fragments
    return fragments

def match_voice_workers(conn, tenant_id: int, text: str, client_id=None, job_id=None) -> tuple[list, list]:
    with conn.cursor() as cur:
        cur.execute("""SELECT id, display_name, email
            FROM users
            WHERE tenant_id=%s AND deleted_at IS NULL AND COALESCE(status,'active')='active'
            ORDER BY display_name, id""", (tenant_id,))
        user_rows = [dict(row) for row in cur.fetchall()]
    if not user_rows:
        return [], []
    users = []
    for row in user_rows:
        display_name = clean_user_display_name(row.get("display_name")) or row.get("email") or f"User {row['id']}"
        normalized_name = normalize_voice_name_key(display_name)
        normalized = normalize_voice_name_key(f"{display_name} {row.get('email') or ''}")
        tokens = [token for token in normalized.split() if token]
        users.append({
            "id": row["id"],
            "display_name": display_name,
            "normalized_name": normalized_name,
            "normalized": normalized,
            "tokens": tokens,
        })

    def build_worker(user_row: dict) -> dict:
        rate = resolve_rate(conn, tenant_id, "worker_rate", rule_key=str(user_row["id"]), job_id=job_id, client_id=client_id)
        return {"name": user_row["display_name"], "user_id": user_row["id"], "hours": 0, "rate": rate, "total": 0}

    def find_best_user(fragment: str):
        normalized_fragment = normalize_voice_name_key(fragment)
        if not normalized_fragment:
            return None
        exact = [u for u in users if u["normalized_name"] == normalized_fragment or u["normalized"] == normalized_fragment]
        if len(exact) == 1:
            return exact[0]
        token_exact = [u for u in users if normalized_fragment in u["tokens"]]
        if len(token_exact) == 1:
            return token_exact[0]
        contains = [
            u for u in users
            if normalized_fragment in u["normalized"]
            or u["normalized_name"] in normalized_fragment
            or u["normalized"] in normalized_fragment
        ]
        if len(contains) == 1:
            return contains[0]
        if len(contains) > 1:
            contains.sort(key=lambda item: (abs(len(item["normalized"]) - len(normalized_fragment)), len(item["normalized"])))
            best = contains[0]
            if len(contains) == 1 or abs(len(contains[1]["normalized"]) - len(normalized_fragment)) != abs(len(best["normalized"]) - len(normalized_fragment)):
                return best
        return None

    matched_workers = []
    seen_user_ids = set()
    not_found = []
    fragments = split_voice_worker_fragments(text)
    for fragment in fragments:
        user_row = find_best_user(fragment)
        if user_row:
            if user_row["id"] not in seen_user_ids:
                matched_workers.append(build_worker(user_row))
                seen_user_ids.add(user_row["id"])
            continue
        token_parts = [part for part in normalize_voice_name_key(fragment).split() if part]
        token_matches = []
        unresolved_tokens = []
        for token in token_parts:
            token_user = find_best_user(token)
            if token_user:
                if token_user["id"] not in seen_user_ids and token_user["id"] not in {item["id"] for item in token_matches}:
                    token_matches.append(token_user)
            else:
                unresolved_tokens.append(token)
        if token_matches and not unresolved_tokens:
            for token_user in token_matches:
                matched_workers.append(build_worker(token_user))
                seen_user_ids.add(token_user["id"])
        else:
            not_found.append(fragment.strip())
    return matched_workers, not_found

def generate_batch_summary(ctx, lang="en"):
    days = list(ctx.get("report_days") or [])
    current = extract_current_report_day(ctx)
    if current.get("entries"):
        days.append(current)
    if not days:
        return generate_summary(ctx, lang)
    header = (
        f"Multi-day work report for {ctx.get('client_name', '?')}"
        if lang == "en" else
        f"Vícedenní výkaz práce pro {ctx.get('client_name', '?')}"
        if lang == "cs" else
        f"Wielodniowy raport pracy dla {ctx.get('client_name', '?')}"
    )
    lines = [header]
    total_hours = 0.0
    grand_total = 0.0
    for index, day in enumerate(days, start=1):
        lines.append("")
        day_label = (
            f"Day {index}: {day.get('work_date', '?')}"
            if lang == "en" else
            f"Den {index}: {day.get('work_date', '?')}"
            if lang == "cs" else
            f"Dzień {index}: {day.get('work_date', '?')}"
        )
        lines.append(day_label)
        lines.append(generate_summary(day, lang))
        total_hours += float(day.get("total_hours") or 0)
        grand_total += float(day.get("grand_total") or 0)
    footer = (
        f"Combined total: {total_hours:.2f} h, £{grand_total:.2f}"
        if lang == "en" else
        f"Celkem: {total_hours:.2f} h, £{grand_total:.2f}"
        if lang == "cs" else
        f"Razem: {total_hours:.2f} h, £{grand_total:.2f}"
    )
    lines.extend(["", footer])
    return "\n".join(lines)

def generate_batch_whatsapp(ctx, lang="en"):
    days = list(ctx.get("report_days") or [])
    current = extract_current_report_day(ctx)
    if current.get("entries"):
        days.append(current)
    if not days:
        return generate_whatsapp(ctx)
    greeting = (
        f"Hello {ctx.get('client_name','')},\n\nHere is the summary of the work completed across multiple days:\n"
        if lang == "en" else
        f"Dobrý den {ctx.get('client_name','')},\n\nTady je shrnutí práce za více dní:\n"
        if lang == "cs" else
        f"Dzień dobry {ctx.get('client_name','')},\n\nOto podsumowanie prac z kilku dni:\n"
    )
    lines = [greeting]
    grand_total = 0.0
    for day in days:
        lines.append(f"{day.get('work_date')}: £{float(day.get('grand_total') or 0):.2f}")
        for entry in day.get("entries") or []:
            lines.append(f"- {entry.get('type','Work')}: {entry.get('hours',0)}h = £{float(entry.get('total') or 0):.2f}")
        grand_total += float(day.get("grand_total") or 0)
        lines.append("")
    lines.append(f"Total: £{grand_total:.2f}")
    lines.append("\nMarek\nDesignLeaf\n07395 813008")
    return "\n".join(lines)

def save_voice_work_report_day(conn, tenant_id: int, actor_user_id: Optional[int], day_ctx: dict) -> int:
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO work_reports (tenant_id,client_id,job_id,work_date,total_hours,total_price,notes,created_by,input_type,status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'voice','confirmed') RETURNING id""",
            (
                tenant_id,
                day_ctx.get("client_id"),
                day_ctx.get("job_id"),
                day_ctx.get("work_date", datetime.now().strftime("%Y-%m-%d")),
                day_ctx.get("total_hours", 0),
                day_ctx.get("grand_total", 0),
                day_ctx.get("notes"),
                actor_user_id,
            ))
        rid = cur.fetchone()['id']
        for w in day_ctx.get("workers", []):
            cur.execute("INSERT INTO work_report_workers (work_report_id,user_id,worker_name,hours,hourly_rate,total_price) VALUES (%s,%s,%s,%s,%s,%s)",
                (rid, w.get("user_id"), w["name"], w["hours"], w["rate"], w["total"]))
        for e in day_ctx.get("entries", []):
            cur.execute("INSERT INTO work_report_entries (work_report_id,type,hours,unit_rate,total_price) VALUES (%s,%s,%s,%s,%s)",
                (rid, e["type"], e["hours"], e["rate"], e["total"]))
        for m in day_ctx.get("materials", []):
            cur.execute("INSERT INTO work_report_materials (work_report_id,material_name,quantity,unit_price,total_price) VALUES (%s,%s,%s,%s,%s)",
                (rid, m["name"], m["qty"], m["price"], m["total"]))
        waste = day_ctx.get("waste", {})
        if waste.get("qty", 0) > 0:
            cur.execute("INSERT INTO work_report_waste (work_report_id,quantity,unit,unit_price,total_price) VALUES (%s,%s,'bulkbag',%s,%s)",
                (rid, waste["qty"], waste["rate"], waste["total"]))
    log_activity(conn, "work_report", str(rid), "create", f"Work report £{day_ctx.get('grand_total',0):.2f} for {day_ctx.get('client_name','?')}")
    return rid

def generate_summary(ctx, lang="en"):
    c = ctx
    lines = []
    client = c.get("client_name","?")
    lines.append(f"Client: {client}" if lang=="en" else f"Klient: {client}" if lang=="cs" else f"Klient: {client}")
    lines.append(f"Date: {c.get('work_date','today')}")
    lines.append(f"Total hours: {c.get('total_hours',0)}")
    for w in c.get("workers",[]):
        lines.append(f"  {w.get('name','?')}: {w.get('hours',0)}h × £{w.get('rate',35)}/h = £{w.get('total',0):.2f}")
    for e in c.get("entries",[]):
        lines.append(f"  {e.get('type','work')}: {e.get('hours',0)}h × £{e.get('rate',35)}/h = £{e.get('total',0):.2f}")
    for m in c.get("materials",[]):
        lines.append(f"  Material: {m.get('name','?')} {m.get('qty',0)} × £{m.get('price',0)} = £{m.get('total',0):.2f}")
    waste = c.get("waste",{})
    if waste.get("qty",0) > 0:
        lines.append(f"  Waste: {waste['qty']} bags × £{waste.get('rate',80)} = £{waste.get('total',0):.2f}")
    lines.append(f"TOTAL: £{c.get('grand_total',0):.2f}")
    if c.get("notes"): lines.append(f"Notes: {c['notes']}")
    return "\n".join(lines)

def generate_whatsapp(ctx):
    c = ctx
    lines = [f"Hello {c.get('client_name','')},", "", "Here is the summary of today's work:", ""]
    for e in c.get("entries",[]):
        lines.append(f"{e.get('type','Work')}: {e.get('hours',0)} hours × £{e.get('rate',35):.0f} = £{e.get('total',0):.2f}")
    waste = c.get("waste",{})
    if waste.get("qty",0) > 0:
        lines.append(f"\nGarden waste disposal: {waste['qty']} bulk bags × £{waste.get('rate',80):.0f} = £{waste.get('total',0):.2f}")
    for m in c.get("materials",[]):
        lines.append(f"Material - {m.get('name','')}: {m.get('qty',0)} × £{m.get('price',0):.2f} = £{m.get('total',0):.2f}")
    lines.append(f"\nTotal: £{c.get('grand_total',0):.2f}")
    lines.append(f"\nMarek\nDesignLeaf\n07395 813008")
    return "\n".join(lines)

# ========== VOICE SESSION API ==========
@app.post("/voice/session/start")
async def voice_session_start(data: dict, request: Request):
    conn = get_db_conn()
    try:
        sid = str(uuid.uuid4())
        tenant_id = data.get("tenant_id",1)
        tenant_config = get_tenant_config(conn, tenant_id)
        lang = resolve_voice_language(tenant_config, data.get("language"))
        actor_user_id = request.state.user.get("user_id")
        with conn.cursor() as cur:
            ctx = json.dumps({
                "language": lang,
                "work_date": data.get("work_date", datetime.now().strftime("%Y-%m-%d")),
                "report_days": [],
            })
            cur.execute("INSERT INTO voice_sessions (id,tenant_id,user_id,session_type,state,dialog_step,context) VALUES (%s,%s,%s,'work_report','active','client',%s)",
                (sid,tenant_id,actor_user_id,ctx))
            conn.commit()
        audit_request_event(
            request,
            action="voice_session_start",
            description="Started voice session",
            entity_type="voice_session",
            entity_id=sid,
            details={"language": lang, "work_date": data.get("work_date")},
            source_channel="voice",
        )
        return {"session_id":sid,"step":"client","prompt":get_prompt("client",lang)}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.post("/voice/session/input")
async def voice_session_input(data: dict, request: Request):
    sid = data.get("session_id")
    text = data.get("text","").strip()
    if not sid: raise HTTPException(400,"session_id required")
    if text:
        audit_request_event(
            request,
            action="voice_session_input",
            description=text,
            entity_type="voice_session",
            entity_id=sid,
            details={"tenant_id": data.get("tenant_id", 1)},
            source_channel="voice",
        )
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM voice_sessions WHERE id=%s AND state='active' FOR UPDATE",(sid,))
            sess = cur.fetchone()
            if not sess:
                conn.rollback(); release_conn(conn)
                return {"step":"error","prompt":"Session not found or expired","error":"no_session"}
            # Tenant isolation check
            req_tenant = data.get("tenant_id", 1)
            if sess["tenant_id"] != req_tenant:
                raise HTTPException(403, "Tenant mismatch")
            _raw = sess['context']
            ctx = _raw if isinstance(_raw, dict) else (json.loads(_raw) if _raw else {})
            original_ctx = dict(ctx)
            step = sess['dialog_step']
            lang = ctx.get("language","en")
            tenant_id = sess['tenant_id']
            next_step = step
            reply = ""
            error = None

            # === STEP: CLIENT ===
            if step == "client":
                wants_new_client, provided_new_client_name = parse_new_client_command(text)
                if wants_new_client and provided_new_client_name:
                    actor_user_id = sess.get("user_id")
                    if not actor_user_id:
                        raise HTTPException(422, "Voice session has no user for client creation")
                    created_client = create_voice_work_report_client(conn, tenant_id, int(actor_user_id), provided_new_client_name, lang)
                    ctx["client_id"] = created_client["id"]
                    ctx["client_name"] = created_client["display_name"]
                    next_step = "date"
                    reply = (
                        f"Created client {created_client['display_name']}. {get_prompt('date',lang)}"
                        if lang == "en" else
                        f"Vytvořil jsem klienta {created_client['display_name']}. {get_prompt('date',lang)}"
                        if lang == "cs" else
                        f"Utworzyłem klienta {created_client['display_name']}. {get_prompt('date',lang)}"
                    )
                elif wants_new_client:
                    next_step = "client_create_name"
                    reply = get_prompt("client_create_name", lang)
                else:
                    cur.execute("SELECT id,display_name FROM clients WHERE tenant_id=%s AND deleted_at IS NULL AND (display_name ILIKE %s OR client_code ILIKE %s) LIMIT 5",
                        (tenant_id,f"%{text}%",f"%{text}%"))
                    matches = cur.fetchall()
                    if len(matches) == 1:
                        ctx["client_id"] = matches[0]['id']; ctx["client_name"] = matches[0]['display_name']
                        next_step = "date"; reply = f"{matches[0]['display_name']}. {get_prompt('date',lang)}"
                    elif len(matches) > 1:
                        names = ", ".join([m['display_name'] for m in matches])
                        reply = f"Found: {names}. Which one?" if lang=="en" else f"Nalezeni: {names}. Který?" if lang=="cs" else f"Znalezieni: {names}. Który?"
                    else:
                        reply = (
                            "Client not found. Try again or say 'new client'."
                            if lang=="en" else
                            "Klient nenalezen. Zkus to znovu nebo řekni 'nový klient'."
                            if lang=="cs" else
                            "Klient nie znaleziony. Spróbuj ponownie albo powiedz 'nowy klient'."
                        )

            elif step == "client_create_name":
                actor_user_id = sess.get("user_id")
                if not actor_user_id:
                    raise HTTPException(422, "Voice session has no user for client creation")
                created_client = create_voice_work_report_client(conn, tenant_id, int(actor_user_id), text, lang)
                ctx["client_id"] = created_client["id"]
                ctx["client_name"] = created_client["display_name"]
                next_step = "date"
                reply = (
                    f"Created client {created_client['display_name']}. {get_prompt('date',lang)}"
                    if lang == "en" else
                    f"Vytvořil jsem klienta {created_client['display_name']}. {get_prompt('date',lang)}"
                    if lang == "cs" else
                    f"Utworzyłem klienta {created_client['display_name']}. {get_prompt('date',lang)}"
                )

            # === STEP: DATE ===
            elif step == "date":
                parsed_date = parse_voice_work_date(text)
                if not parsed_date:
                    reply = (
                        "Invalid date. Say a date like 2026-04-18 or 18.04.2026."
                        if lang == "en" else
                        "Neplatné datum. Řekni datum jako 2026-04-18 nebo 18.04.2026."
                        if lang == "cs" else
                        "Nieprawidłowa data. Powiedz datę jak 2026-04-18 albo 18.04.2026."
                    )
                else:
                    reset_current_report_day(ctx, parsed_date)
                    next_step = "workers"
                    reply = (
                        f"Date {parsed_date}. {get_prompt('workers',lang)}"
                        if lang == "en" else
                        f"Datum {parsed_date}. {get_prompt('workers',lang)}"
                        if lang == "cs" else
                        f"Data {parsed_date}. {get_prompt('workers',lang)}"
                    )

            # === STEP: WORKERS ===
            elif step == "workers":
                workers, not_found = match_voice_workers(conn, tenant_id, text, client_id=ctx.get("client_id"), job_id=ctx.get("job_id"))
                if workers and not not_found:
                    ctx["workers"] = workers; next_step = "total_hours"
                    reply = f"{len(workers)} workers. {get_prompt('total_hours',lang)}"
                elif workers and not_found:
                    ctx["workers"] = workers
                    nf = ", ".join(not_found)
                    reply = f"Not found in system: {nf}. Found: {len(workers)}. Add more or say 'continue'." if lang=="en" else f"Nenalezeni: {nf}. Nalezeno: {len(workers)}. Přidej další nebo řekni 'pokračuj'." if lang=="cs" else f"Nie znaleziono: {nf}. Znaleziono: {len(workers)}. Dodaj kolejne albo powiedz 'dalej'."
                elif "continu" in text.lower() or "pokrac" in text.lower() or "dalej" in text.lower():
                    if ctx.get("workers"):
                        next_step = "total_hours"; reply = get_prompt("total_hours",lang)
                    else:
                        reply = "No workers added. Try again." if lang=="en" else "Žádní pracovníci. Zkus znovu." if lang=="cs" else "Nie dodano pracowników. Spróbuj ponownie."
                else:
                    reply = "No workers found in system. Try first names or say them one by one." if lang=="en" else "Žádní pracovníci nenalezeni. Zkus křestní jména nebo je řekni po jednom." if lang=="cs" else "Nie znaleziono pracowników. Spróbuj podać imiona albo powiedz je po kolei."

            # === STEP: TOTAL HOURS ===
            elif step == "total_hours":
                hrs = parse_voice_number(text)
                if hrs is not None:
                    ctx["total_hours"] = hrs
                    # Distribute equally if multiple workers
                    wc = len(ctx.get("workers",[]))
                    if wc > 0:
                        per = round(hrs / wc, 2)
                        for w in ctx["workers"]: w["hours"] = per; w["total"] = round(per * w["rate"],2)
                    ctx["_entry_sub"] = "pruning"; ctx["entries"] = []; next_step = "entries"; reply = f"{hrs}h. " + get_prompt("entries",lang)
                else:
                    reply = "Invalid number." if lang=="en" else "Neplatné číslo." if lang=="cs" else "Nieprawidłowa liczba."

            # === STEP: ENTRIES (pruning -> maintenance -> additional if needed) ===
            elif step == "entries":
                sub = ctx.get("_entry_sub","pruning")
                low = text.lower().strip()
                def _parse_hours(t):
                    parsed = parse_voice_number(t)
                    if parsed is None:
                        raise ValueError("invalid voice number")
                    return parsed
                if not ctx.get("entries"): ctx["entries"] = []

                if sub == "pruning":
                    try:
                        h = _parse_hours(low)
                        rate = resolve_rate(conn,tenant_id,"task_rate",rule_key="pruning",job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                        if h > 0: ctx["entries"].append({"type":"pruning","hours":h,"rate":rate,"total":round(h*rate,2)})
                        ctx["_entry_sub"] = "maintenance"
                        reply = "Kolik hodin údržba?" if lang=="cs" else "How many hours maintenance?" if lang=="en" else "Ile godzin konserwacja?"
                    except:
                        reply = "Neplatné číslo. Kolik hodin prořez?" if lang=="cs" else "Invalid number. Hours pruning?"

                elif sub == "maintenance":
                    try:
                        h = _parse_hours(low)
                        rate = resolve_rate(conn,tenant_id,"task_rate",rule_key="maintenance",job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                        if h > 0: ctx["entries"].append({"type":"maintenance","hours":h,"rate":rate,"total":round(h*rate,2)})
                        sofar = sum(e["hours"] for e in ctx["entries"])
                        total = ctx.get("total_hours",0)
                        remaining = round(total - sofar, 2)
                        if remaining <= 0.01:
                            ctx.pop("_entry_sub",None)
                            next_step = "waste"; reply = get_prompt("waste",lang)
                        else:
                            ctx["_entry_sub"] = "additional_type"
                            reply = f"Zbývá {remaining}h. Jaký další typ práce?" if lang=="cs" else f"{remaining}h left. What other type of work?"
                    except:
                        reply = "Neplatné číslo. Kolik hodin údržba?" if lang=="cs" else "Invalid number. Hours maintenance?"

                elif sub == "additional_type":
                    ctx["_entry_type_name"] = text.strip()
                    ctx["_entry_sub"] = "additional_hours"
                    reply = f"{text.strip()} — kolik hodin?" if lang=="cs" else f"{text.strip()} — how many hours?"

                elif sub == "additional_hours":
                    try:
                        h = _parse_hours(low)
                        etype = ctx.pop("_entry_type_name","other")
                        rate = resolve_rate(conn,tenant_id,"task_rate",rule_key=etype.lower(),job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                        if h > 0: ctx["entries"].append({"type":etype,"hours":h,"rate":rate,"total":round(h*rate,2)})
                        sofar = sum(e["hours"] for e in ctx["entries"])
                        total = ctx.get("total_hours",0)
                        remaining = round(total - sofar, 2)
                        if abs(remaining) <= 0.01:
                            ctx.pop("_entry_sub",None)
                            next_step = "waste"; reply = get_prompt("waste",lang)
                        elif remaining > 0:
                            ctx["_entry_sub"] = "additional_type"
                            reply = f"Zbývá {remaining}h. Jaký další typ práce?" if lang=="cs" else f"{remaining}h left. What other type?"
                        else:
                            # Presazeno - reset
                            ctx["entries"] = []
                            ctx["_entry_sub"] = "pruning"
                            reply = "Součet přesahuje celkem. Začínám znovu. Kolik hodin prořez?" if lang=="cs" else "Sum exceeds total. Starting over. Hours pruning?"
                    except:
                        reply = "Neplatné číslo. Kolik hodin?" if lang=="cs" else "Invalid number. How many hours?"

            # === STEP: VALIDATE HOURS ===
            elif step == "validate_hours":
                ctx["total_hours"] = sum(e["hours"] for e in ctx.get("entries",[]))
                next_step = "waste"; reply = get_prompt("waste",lang)

            # === STEP: MATERIALS ===
            elif step == "materials":
                if is_voice_negative_response(text, include_zero=True):
                    ctx["materials"] = []
                else:
                    mats = []
                    parts = re.findall(r'(\w[\w\s]*?)\s+([\d.,]+)\s*[x×]?\s*£?([\d.,]+)?', text)
                    for mname, mqty, mprice in parts:
                        q = float(mqty.replace(",","."))
                        p = float(mprice.replace(",",".")) if mprice else 0
                        mats.append({"name":mname.strip(),"qty":q,"price":p,"total":round(q*p,2)})
                    if not mats and not is_voice_negative_response(text, include_zero=True):
                        mats.append({"name":text,"qty":1,"price":0,"total":0})
                    ctx["materials"] = mats
                next_step = "notes"; reply = get_prompt("notes",lang)

            # === STEP: WASTE ===
            elif step == "waste":
                if is_voice_negative_response(text, include_zero=True):
                    ctx["waste"] = {"qty":0,"rate":0,"total":0}
                else:
                    qty = parse_voice_number(text)
                    if qty is not None:
                        rate = resolve_rate(conn,tenant_id,"waste_rate",job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                        ctx["waste"] = {"qty":qty,"rate":rate,"total":round(qty*rate,2)}
                    else:
                        ctx["waste"] = {"qty":0,"rate":0,"total":0}
                next_step = "materials"; reply = get_prompt("materials",lang)

            # === STEP: NOTES ===
            elif step == "notes":
                if not is_voice_negative_response(text) and text.strip() != "":
                    ctx["notes"] = text
                # Calculate grand total
                gt = sum(e.get("total",0) for e in ctx.get("entries",[]))
                gt += ctx.get("waste",{}).get("total",0)
                gt += sum(m.get("total",0) for m in ctx.get("materials",[]))
                ctx["grand_total"] = round(gt,2)
                next_step = "summary"
                reply = generate_summary(ctx,lang) + "\n\n" + get_prompt("summary",lang)

            # === STEP: SUMMARY (edit or confirm) ===
            elif step == "summary":
                low = text.lower()
                normalized_low = normalize_voice_text(text)
                # POTVRDIT
                if is_voice_positive_response(text) or any(x in normalized_low for x in ["confirm","potvrdit","potwierdz","yes","ano","tak","ulozit","save"]):
                    next_step = "confirm"
                elif any(x in low for x in ["another day","next day","add day","další den","dalsi den","další datum","dalsi datum","kolejny dzień","kolejny dzien","następny dzień","nastepny dzien"]):
                    completed_day = append_current_report_day(ctx)
                    reset_current_report_day(ctx)
                    next_step = "date"
                    reply = (
                        f"Day {completed_day.get('work_date')} added. {get_prompt('date', lang)}"
                        if lang == "en" else
                        f"Den {completed_day.get('work_date')} přidán. {get_prompt('date', lang)}"
                        if lang == "cs" else
                        f"Dzień {completed_day.get('work_date')} dodany. {get_prompt('date', lang)}"
                    )
                # ZRUSIT / SMAZAT
                elif is_voice_negative_response(text) or any(x in normalized_low for x in ["zrusit","smazat","cancel","delete","storno","konec","stop"]):
                    cur.execute("UPDATE voice_sessions SET state='cancelled',updated_at=now() WHERE id=%s",(sid,))
                    conn.commit()
                    reply = "Report zrušen." if lang=="cs" else "Report cancelled." if lang=="en" else "Raport anulowany."
                    return {"session_id":sid,"step":"done","prompt":reply}
                # OPRAVIT
                elif any(low.startswith(x) for x in ["edit","oprav","popraw","zmen","změ","uprav"]):
                    _step_map = {"client":"client","klient":"client","klienta":"client",
                        "date":"date","datum":"date","data":"date","den":"date","dzień":"date","dzien":"date",
                        "worker":"workers","pracovn":"workers","kdo":"workers",
                        "hour":"total_hours","hodin":"total_hours","celkem":"total_hours","total":"total_hours","godzin":"total_hours",
                        "entr":"entries","polozk":"entries","položk":"entries","rozpad":"entries","práce":"entries","prace":"entries","typ":"entries",
                        "waste":"waste","odpad":"waste","pytl":"waste",
                        "mater":"materials","materiál":"materials",
                        "note":"notes","pozn":"notes","poznám":"notes"}
                    _found = False
                    for _kw, _target in _step_map.items():
                        if _kw in low:
                            next_step = _target; reply = get_prompt(_target,lang); _found = True; break
                    if not _found:
                        reply = "Co opravit? (klienta/datum/pracovníky/hodiny/položky/odpad/materiál/poznámku)" if lang=="cs" else "What to edit? (client/date/workers/hours/entries/waste/materials/notes)"
                else:
                    reply = "Řekni 'další den', 'potvrdit', 'oprav [co]' nebo 'zrušit'." if lang=="cs" else "Say 'another day', 'confirm', 'edit [field]', or 'cancel'."

            # === STEP: CONFIRM → save to DB ===
            if next_step == "confirm" and step != "confirm":
                # VALIDATION: block save without workers or entries
                if not ctx.get("workers"):
                    next_step = "workers"; reply = "Cannot save without workers. " + get_prompt("workers",lang)
                elif not ctx.get("entries"):
                    next_step = "entries"; reply = "Cannot save without work entries. " + get_prompt("entries",lang)
                elif abs(sum(e["hours"] for e in ctx.get("entries",[])) - ctx.get("total_hours",0)) > 0.01:
                    ctx["total_hours"] = sum(e["hours"] for e in ctx.get("entries",[])); next_step = "confirm"
                else:
                  try:
                    pending_days = list(ctx.get("report_days") or [])
                    pending_days.append(extract_current_report_day(ctx))
                    report_ids = []
                    for day_ctx in pending_days:
                        rid = save_voice_work_report_day(conn, tenant_id, sess.get("user_id"), day_ctx)
                        report_ids.append(rid)
                    ctx["saved_work_report_ids"] = report_ids
                    cur.execute("UPDATE voice_sessions SET state='completed',context=%s,updated_at=now() WHERE id=%s",(json.dumps(ctx),sid))
                    conn.commit()
                    whatsapp = generate_batch_whatsapp(ctx, lang)
                    reply = get_prompt("confirm",lang)
                    return {
                        "session_id":sid,
                        "step":"done",
                        "prompt":reply,
                        "work_report_id":report_ids[-1],
                        "work_report_ids":report_ids,
                        "whatsapp_message":whatsapp,
                        "summary":generate_batch_summary(ctx,lang)
                    }
                  except Exception as e:
                    conn.rollback(); raise HTTPException(500,f"Save error: {e}")

            # === AUDIT: structured voice step log ===
            audit_details = json.dumps({
                "step": step, "next_step": next_step,
                "input_length": len(text),
                "input_preview": text[:50].replace("\n", " "),
                "has_numbers": parse_voice_number(text) is not None
            })
            log_activity(conn, "voice_session", sid, "voice_input", audit_details, tenant_id=tenant_id, user_id=sess.get("user_id"))

            # === VALIDATE TRANSITION ===
            if not validate_transition(step, next_step) and next_step != step:
                next_step = step
                ctx = dict(original_ctx)
                reply = "Invalid step transition. " + get_prompt(step, lang)

            # === UPDATE SESSION ===
            cur.execute("UPDATE voice_sessions SET dialog_step=%s,context=%s,updated_at=now() WHERE id=%s",(next_step,json.dumps(ctx),sid))
            conn.commit()
        return {"session_id":sid,"step":next_step,"prompt":reply,"context":ctx}
    except HTTPException: conn.rollback(); raise
    except Exception as e:
        import traceback; traceback.print_exc()
        try: conn.rollback()
        except: pass
        return {"step":"error","prompt":f"Voice error: {type(e).__name__}: {e}","error":str(e)}
    finally:
        try: release_conn(conn)
        except: pass

@app.post("/voice/session/resume")
async def voice_session_resume(data: dict):
    sid = data.get("session_id")
    tenant_id = data.get("tenant_id", 1)
    user_id = data.get("user_id")
    if not sid: raise HTTPException(400, "session_id required")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM voice_sessions WHERE id=%s AND tenant_id=%s", (sid, tenant_id))
            sess = cur.fetchone()
            if not sess: raise HTTPException(404, "Session not found")
            if user_id and sess.get("user_id") and sess["user_id"] != user_id:
                raise HTTPException(403, "Access denied — session belongs to another user")
            if sess["state"] == "completed":
                return {"session_id": sid, "step": "done", "prompt": "Session already completed."}
            _raw2 = sess["context"]
            ctx = _raw2 if isinstance(_raw2, dict) else (json.loads(_raw2) if _raw2 else {})
            lang = ctx.get("language", "en")
            step = sess["dialog_step"]
            cur.execute("UPDATE voice_sessions SET state='active', expires_at=now()+interval '1 hour', updated_at=now() WHERE id=%s AND state != 'completed'", (sid,))
            if cur.rowcount == 0:
                return {"session_id": sid, "step": "done", "prompt": "Session already completed or locked."}
            log_activity(conn, "voice_session", sid, "resume", f"Session resumed at step={step}", tenant_id=tenant_id, user_id=user_id)
            conn.commit()
        return {"session_id": sid, "step": step, "prompt": get_prompt(step, lang), "context": ctx}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.get("/pricing-rules")
async def list_pricing_rules(tenant_id: int=1):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pricing_rules WHERE tenant_id=%s ORDER BY scope,rule_type",(tenant_id,))
            return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)

@app.post("/pricing-rules")
async def create_pricing_rule(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pricing_rules (tenant_id,scope,scope_id,rule_type,rule_key,rate) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (data.get("tenant_id",1),data.get("scope","system"),data.get("scope_id"),data.get("rule_type"),data.get("rule_key"),data.get("rate",0)))
            pid = cur.fetchone()['id']; conn.commit()
        return {"id":pid,"status":"created"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== WHATSAPP CLOUD API ==========

def translate_customer_message(message: str, target_language: str = "en") -> str:
    source_text = (message or "").strip()
    if not source_text:
        return ""
    normalized_target_language = normalize_language_code(target_language, default="en")
    translated = source_text
    if ai_client:
        try:
            target_label = {
                "en": "English",
                "cs": "Czech",
                "pl": "Polish",
            }.get(normalized_target_language, "English")
            translation = ai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Translate the following customer message to {target_label}. "
                            f"Return ONLY the translated message, nothing else. "
                            f"Preserve meaning and tone. If already in {target_label}, return it as-is."
                        ),
                    },
                    {"role": "user", "content": source_text},
                ],
                max_tokens=500,
            )
            candidate = (translation.choices[0].message.content or "").strip()
            if candidate:
                translated = candidate
        except Exception:
            translated = source_text
    return translated

def wa_send_message(to_phone: str, message: str, target_language: str = "en"):
    """Send WhatsApp message via configured provider. By default translates to customer language."""
    provider = get_whatsapp_provider()
    if provider == "none":
        return {
            "error": "WhatsApp not configured",
            "config_error": True,
            "missing": {
                "WHATSAPP_ACCESS_TOKEN": not bool(WA_TOKEN),
                "WHATSAPP_PHONE_NUMBER_ID": not bool(WA_PHONE_ID),
                "TWILIO_ACCOUNT_SID": not bool(TWILIO_ACCOUNT_SID),
                "TWILIO_AUTH_TOKEN": not bool(TWILIO_AUTH_TOKEN),
                "TWILIO_WHATSAPP_FROM": not bool(TWILIO_WHATSAPP_FROM),
            },
        }
    normalized_phone = normalize_whatsapp_phone(to_phone)
    if len(normalized_phone) < 8:
        return {
            "error": "Invalid WhatsApp phone number",
            "meta_status": 400,
            "detail": f"Phone '{to_phone}' could not be normalized to a valid international number.",
        }

    normalized_target_language = normalize_language_code(target_language, default="en")
    translated = translate_customer_message(message, normalized_target_language)
    if provider == "twilio":
        sender = TWILIO_WHATSAPP_FROM.strip()
        if not sender.startswith("whatsapp:"):
            sender = f"whatsapp:{sender}"
        form = urlencode({
            "To": f"whatsapp:+{normalized_phone}",
            "From": sender,
            "Body": translated,
        }).encode("utf-8")
        basic_auth = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode("utf-8")).decode("ascii")
        req = urllib.request.Request(
            get_twilio_messages_api_url(),
            data=form,
            method="POST",
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode())
                result["translated_text"] = translated
                result["original_text"] = message
                result["target_language"] = normalized_target_language
                result["normalized_phone"] = normalized_phone
                result["provider"] = "twilio"
                return result
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            twilio_error = None
            try:
                twilio_error = json.loads(body)
            except Exception:
                twilio_error = None
            human_error = "Twilio WhatsApp request failed"
            if e.code == 401:
                human_error = "Twilio authorization failed"
            elif e.code == 403:
                human_error = "Twilio access forbidden"
            elif e.code == 400 and isinstance(twilio_error, dict):
                human_error = f"Twilio rejected the request: {twilio_error.get('message') or 'invalid request'}"
            return {
                "error": human_error,
                "meta_status": e.code,
                "detail": body,
                "meta_error": twilio_error,
                "provider": "twilio",
                "config_hint": "Check Railway env vars TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM." if e.code in (400, 401, 403) else None,
            }
        except Exception as e:
            return {"error": str(e), "provider": "twilio"}

    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalized_phone,
        "type": "text",
        "text": {"body": translated}
    }).encode("utf-8")
    req = urllib.request.Request(get_wa_api_url(), data=payload, method="POST",
        headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
            result["translated_text"] = translated
            result["original_text"] = message
            result["target_language"] = normalized_target_language
            result["normalized_phone"] = normalized_phone
            result["provider"] = "meta"
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        meta_error = None
        try:
            parsed = json.loads(body)
            meta_error = parsed.get("error") if isinstance(parsed, dict) else None
        except Exception:
            meta_error = None
        human_error = f"WhatsApp API {e.code}"
        if e.code == 401:
            human_error = "WhatsApp authorization failed"
        elif e.code == 403:
            human_error = "WhatsApp access forbidden"
        elif e.code == 400 and meta_error:
            meta_message = meta_error.get("message") if isinstance(meta_error, dict) else None
            human_error = f"WhatsApp rejected the request: {meta_message or 'invalid request'}"
        return {
            "error": human_error,
            "meta_status": e.code,
            "detail": body,
            "meta_error": meta_error,
            "provider": "meta",
            "config_hint": "Check Railway env vars WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID." if e.code in (401, 403) else None,
        }
    except Exception as e:
        return {"error": str(e), "provider": "meta"}

def wa_find_client_by_phone(conn, phone: str):
    """Find CRM client by phone number"""
    clean = phone.replace("+","").replace(" ","").replace("-","")
    with conn.cursor() as cur:
        cur.execute("SELECT id,display_name,phone_primary FROM clients WHERE REPLACE(REPLACE(REPLACE(phone_primary,'+',''),' ',''),'-','') = %s LIMIT 1", (clean,))
        return cur.fetchone()

@app.get("/whatsapp/webhook")
async def wa_verify(request: Request):
    """Meta webhook verification"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == WA_VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(403, "Verification failed")

@app.post("/whatsapp/webhook")
async def wa_incoming(request: Request):
    """Receive incoming WhatsApp messages"""
    body = await request.json()
    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    sender = msg.get("from", "")
                    msg_type = msg.get("type", "")
                    text = msg.get("text", {}).get("body", "") if msg_type == "text" else f"[{msg_type}]"
                    conn = get_db_conn()
                    try:
                        client = wa_find_client_by_phone(conn, sender)
                        client_id = client["id"] if client else None
                        client_name = client["display_name"] if client else None
                        # Auto-create client from unknown WhatsApp number
                        if not client:
                            wa_name = ""
                            for contact in value.get("contacts", []):
                                if contact.get("wa_id") == sender:
                                    profile = contact.get("profile", {})
                                    wa_name = profile.get("name", "")
                            display = clean_contact_display_name(wa_name) or f"WhatsApp +{sender}"
                            code = f"CL-WA-{sender[-6:]}"
                            with conn.cursor() as cur:
                                cur.execute("""INSERT INTO clients (client_code,client_type,display_name,phone_primary,source,status,tenant_id)
                                    VALUES (%s,'individual',%s,%s,'whatsapp','new',1) ON CONFLICT DO NOTHING RETURNING id""",
                                    (code, display, f"+{sender}"))
                                row = cur.fetchone()
                                if row:
                                    client_id = row["id"]
                                    client_name = display
                                    log_activity(conn,"client",client_id,"auto_create",f"Klient vytvoren z WhatsApp: {display}")
                        with conn.cursor() as cur:
                            upsert_communication_message(cur, 1, {
                                "client_id": client_id,
                                "comm_type": "whatsapp",
                                "source": "whatsapp",
                                "external_message_id": msg.get("id"),
                                "source_phone": f"+{sender}",
                                "phone": f"+{sender}",
                                "subject": f"WA od {client_name or '+'+sender}",
                                "message": text[:500],
                                "direction": "inbound",
                                "notes": f"Phone: +{sender}",
                                "sent_at": msg.get("timestamp"),
                            })
                        log_activity(conn,"communication",0,"whatsapp_in",f"WhatsApp od +{sender}: {text[:100]}")
                        conn.commit()
                    finally: release_conn(conn)
    except Exception as e:
        import traceback; traceback.print_exc()
    return {"status": "ok"}

@app.post("/whatsapp/send")
async def wa_send(request: Request, data: dict):
    """Send WhatsApp message to client"""
    to = data.get("to", "")
    message = data.get("message", "")
    client_id = data.get("client_id")
    tenant_id = get_request_tenant_id(request)
    if not to or not message:
        raise HTTPException(400, "to and message required")
    conn = get_db_conn()
    try:
        tenant_config = get_tenant_config(conn, tenant_id)
        outgoing_language = resolve_customer_language(tenant_config, data.get("language"))
    finally:
        release_conn(conn)
    result = wa_send_message(to, message, outgoing_language)
    if "error" in result:
        status_code = 500 if result.get("config_error") else (result.get("meta_status") or 502)
        raise HTTPException(status_code, result)
    conn = get_db_conn()
    try:
        translated = result.get("translated_text", message)
        with conn.cursor() as cur:
            upsert_communication_message(cur, tenant_id, {
                "client_id": client_id,
                "comm_type": "whatsapp",
                "source": "whatsapp",
                "target_phone": to,
                "phone": to,
                "subject": "WA zpráva",
                "message": translated[:500],
                "direction": "outbound",
                "notes": f"To: {to} | Original: {message[:200]} | Language: {outgoing_language}",
            })
        log_activity(conn,"communication",0,"whatsapp_out",f"WhatsApp na {to} ({outgoing_language}): {translated[:100]}", tenant_id=tenant_id)
        conn.commit()
    finally: release_conn(conn)
    return {"status": "sent", "translated": translated, "original": message, "language": outgoing_language}

@app.get("/whatsapp/status")
async def wa_status():
    return {
        "configured": get_whatsapp_provider() != "none",
        "provider": get_whatsapp_provider(),
        "phone_id": WA_PHONE_ID[:6]+"..." if WA_PHONE_ID else None,
        "business_account_id": WA_ACCOUNT_ID[:6]+"..." if WA_ACCOUNT_ID else None,
        "token_present": bool(WA_TOKEN),
        "twilio_account_sid": TWILIO_ACCOUNT_SID[:6]+"..." if TWILIO_ACCOUNT_SID else None,
        "twilio_sender": TWILIO_WHATSAPP_FROM or None,
    }

# ========== SYSTEM ==========
@app.get("/")
async def root():
    return {"app":"Secretary DesignLeaf","version":"1.2a","ai_configured":bool(OPENAI_API_KEY),"docs":"/docs"}

if __name__ == "__main__":
    port = int(os.getenv("PORT",8000))
    uvicorn.run(app,host="0.0.0.0",port=port)
