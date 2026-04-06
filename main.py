import os, json, uuid, csv, io, hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query, Depends, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
from openai import OpenAI
from datetime import datetime, timedelta
import jwt as pyjwt
import json
import urllib.request

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

# === AUTH MIDDLEWARE: Protect /crm/*, /process, /voice/*, /work-reports* ===
PROTECTED_PREFIXES = ["/crm/", "/process", "/voice/", "/work-reports"]
PUBLIC_PATHS = ["/health", "/auth/login", "/auth/refresh", "/docs", "/openapi.json", "/", "/onboarding/industry-groups", "/onboarding/industry-subtypes", "/onboarding/presets"]

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
    estimated_minutes INT, actual_minutes INT, created_by TEXT, assigned_to TEXT,
    delegated_by TEXT, client_id BIGINT, client_name TEXT, job_id BIGINT,
    property_id BIGINT, property_address TEXT, is_recurring BOOLEAN DEFAULT FALSE,
    recurrence_rule TEXT, result TEXT, notes JSONB DEFAULT '[]',
    communication_method TEXT, source TEXT DEFAULT 'manualne',
    is_billable BOOLEAN DEFAULT FALSE, has_cost BOOLEAN DEFAULT FALSE,
    waiting_for_payment BOOLEAN DEFAULT FALSE, checklist JSONB DEFAULT '[]',
    is_completed BOOLEAN DEFAULT FALSE, updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS client_notes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id BIGINT NOT NULL, note TEXT NOT NULL, created_by TEXT DEFAULT 'Marek',
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS job_notes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id BIGINT NOT NULL, note TEXT NOT NULL, created_by TEXT DEFAULT 'Marek',
    created_at TIMESTAMPTZ DEFAULT now()
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
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS photos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    filename TEXT NOT NULL, description TEXT,
    file_path TEXT, thumbnail_base64 TEXT,
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
CREATE TABLE IF NOT EXISTS pricing_rules (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id INT DEFAULT 1, scope TEXT DEFAULT 'system',
    scope_id BIGINT, rule_type TEXT NOT NULL,
    rule_key TEXT, rate DECIMAL NOT NULL,
    currency TEXT DEFAULT 'GBP', created_at TIMESTAMPTZ DEFAULT now()
);
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
    ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
    ALTER TABLE clients ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1;
    ALTER TABLE clients ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
    ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS tenant_id INT DEFAULT 1;
    ALTER TABLE activity_timeline ADD COLUMN IF NOT EXISTS user_id_ref TEXT;
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
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
"""

# === CONFIG ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# === WHATSAPP CONFIG ===
WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
WA_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "designleaf_webhook_2026")
WA_API_URL = f"https://graph.facebook.com/v21.0/{WA_PHONE_ID}/messages"

def parse_database_config():
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        p = urlparse(database_url)
        return {"dbname": p.path.lstrip("/"), "user": p.username, "password": p.password, "host": p.hostname, "port": str(p.port or 5432)}
    return {"dbname": "secretary_db", "user": "postgres", "password": "", "host": "localhost", "port": "5432"}

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
    except Exception as e: print(f"DB pool FAIL: {e}")

def get_db_conn():
    conn = db_pool.getconn() if db_pool else psycopg2.connect(**DB_CONFIG)
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

from contextlib import contextmanager
@contextmanager
def db_conn():
    """Context manager that auto-releases connection back to pool."""
    c = get_db_conn()
    try:
        yield c
    finally:
        release_conn(c)

def log_activity(conn, entity_type, entity_id, action, description, tenant_id=1, user_id=None, details=None):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO activity_timeline
            (entity_type, entity_id, action, description, user_name, tenant_id, user_id_ref, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())""",
            (entity_type, str(entity_id), action,
             description[:500] if description else "",
             str(user_id) if user_id else "system",
             tenant_id,
             str(user_id) if user_id else None))

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

def resolve_voice_language(config, request_lang=None):
    """Determine voice session language from config."""
    if request_lang and request_lang != "en":
        return request_lang.split("-")[0].lower()
    if config.get("found"):
        return config.get("default_internal_lang", "en")
    return "en"

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

@app.on_event("startup")
async def startup():
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

@app.on_event("shutdown")
async def shutdown():
    if db_pool: db_pool.closeall()

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
async def process_message(msg: MessageRequest):
    if not ai_client: return {"reply_cs": "AI neni nakonfigurovana."}
    try:
        now = msg.current_datetime or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entity_ctx = ""
        if msg.context_entity_id and msg.context_type == "client":
            conn = get_db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT display_name,email_primary,phone_primary FROM clients WHERE id=%s", (msg.context_entity_id,))
                    r = cur.fetchone()
                    if r: entity_ctx = f"Marek se diva na klienta: {r['display_name']}"
            finally: release_conn(conn)

        # Language from tenant config + request override
        tenant_config = get_tenant_config(conn if 'conn' in dir() else get_db_conn(), 1)
        lang = resolve_response_language(tenant_config, msg.internal_language)
        if lang == "cs":
            lang_instruction = "JAZYK: Odpovídej VÝHRADNĚ česky. Celá tvoje odpověď musí být v češtině. Nikdy nepřepínej do jiného jazyka. Uživatel může psát česky, anglicky nebo polsky — ty VŽDY odpovídáš POUZE česky."
        elif lang == "en":
            lang_instruction = "LANGUAGE: You MUST respond EXCLUSIVELY in English. Your entire response must be in English. Never switch to another language. The user may write in Czech, English or Polish — you ALWAYS respond ONLY in English."
        elif lang == "pl":
            lang_instruction = "JĘZYK: Odpowiadaj WYŁĄCZNIE po polsku. Cała twoja odpowiedź musi być po polsku. Nigdy nie przełączaj się na inny język. Użytkownik może pisać po czesku, angielsku lub polsku — ty ZAWSZE odpowiadasz TYLKO po polsku."
        else:
            lang_instruction = "LANGUAGE: Respond in English only."

        system_prompt = f"""You are an intelligent VOICE secretary of DesignLeaf company (landscaping services, Oxfordshire UK).
{lang_instruction}
TIME: {now}. CONTEXT: {entity_ctx or 'None.'}
CALENDAR: {msg.calendar_context or 'None.'}
RULES:
- You are a VOICE assistant. The user speaks to you and you speak back. NEVER say you can only communicate via text. NEVER say you are a text-based AI. You ARE a voice assistant.
- Be concise, human, friendly. Remember conversation history.
- NEVER say 'executing...' or 'performing...' — always respond naturally describing what you did.
- To create a task use create_task. To change status use update_task. To complete use complete_task.
- To list tasks use list_tasks.
- For jobs: create_job for new, update_job for status change.
- For notes: add_note with entity_type 'client' or 'job'.
- For leads: create_lead.
- For calendar: list_calendar_events, add/modify/delete_calendar_event.
- For contacts: search_contacts, call_contact.
- When user asks 'what do I have to do' or 'my tasks', use list_tasks.
- When user says 'done' or 'completed' for a task, use complete_task.
- When user says 'work report', 'log work', 'enter hours', 'report work', use start_work_report.
- When user asks about weather, forecast, rain, temperature, wind, or whether to work outside, use get_weather.
- When user says 'napis na whatsapp', 'posli whatsapp', 'whatsapp message', use send_whatsapp. Find client by name, get their phone, send message."""

        tools = [
            {"type":"function","function":{"name":"add_calendar_event","description":"Prida schuzku do kalendare","parameters":{"type":"object","properties":{"title":{"type":"string"},"start_time":{"type":"string","description":"ISO format YYYY-MM-DDTHH:MM:SS"},"duration":{"type":"integer","description":"minuty"}},"required":["title","start_time"]}}},
            {"type":"function","function":{"name":"modify_calendar_event","description":"Zmeni existujici udalost","parameters":{"type":"object","properties":{"event_title":{"type":"string"},"new_title":{"type":"string"},"new_start_time":{"type":"string"}},"required":["event_title"]}}},
            {"type":"function","function":{"name":"delete_calendar_event","description":"Smaze udalost","parameters":{"type":"object","properties":{"event_title":{"type":"string"}},"required":["event_title"]}}},
            {"type":"function","function":{"name":"list_calendar_events","description":"Precte kalendar na N dni","parameters":{"type":"object","properties":{"days":{"type":"integer","default":7}}}}},
            {"type":"function","function":{"name":"search_contacts","description":"Hleda v CRM klientech i telefonnich kontaktech","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
            {"type":"function","function":{"name":"call_contact","description":"Vytoci telefonni cislo","parameters":{"type":"object","properties":{"phone":{"type":"string"}},"required":["phone"]}}},
            {"type":"function","function":{"name":"send_email","description":"Posle email","parameters":{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["to","subject","body"]}}},
            {"type":"function","function":{"name":"create_client","description":"Vytvori noveho klienta v CRM","parameters":{"type":"object","properties":{"name":{"type":"string"},"email":{"type":"string"},"phone":{"type":"string"}},"required":["name"]}}},
            {"type":"function","function":{"name":"create_task","description":"Vytvori ukol. Pouzij pro: zavolat, email, schuzka, objednavka, kalkulace, kontrola, pripomenuti.","parameters":{"type":"object","properties":{"title":{"type":"string"},"description":{"type":"string"},"task_type":{"type":"string","enum":["volat","email","schuzka","objednat_material","vytvorit_kalkulaci","poslat_kalkulaci","navsteva_klienta","zamereni","realizace","kontrola","reklamace","pripomenout_se","interni_poznamka","fotodokumentace"]},"priority":{"type":"string","enum":["nizka","bezna","vysoka","urgentni","kriticka"]},"deadline":{"type":"string"},"assigned_to":{"type":"string"},"client_name":{"type":"string"}},"required":["title"]}}},
            {"type":"function","function":{"name":"create_job","description":"Vytvori novou zakazku","parameters":{"type":"object","properties":{"title":{"type":"string"},"client_name":{"type":"string"},"description":{"type":"string"},"start_date":{"type":"string"}},"required":["title"]}}},
            {"type":"function","function":{"name":"add_note","description":"Prida poznamku ke klientovi nebo zakazce","parameters":{"type":"object","properties":{"entity_type":{"type":"string","enum":["client","job"]},"entity_name":{"type":"string"},"note":{"type":"string"}},"required":["entity_type","note"]}}},
            {"type":"function","function":{"name":"create_lead","description":"Vytvori novy lead/poptavku","parameters":{"type":"object","properties":{"name":{"type":"string"},"source":{"type":"string","enum":["checkatrade","web","telefon","doporuceni","jiny"]},"note":{"type":"string"}},"required":["name","source"]}}},
            {"type":"function","function":{"name":"update_task","description":"Zmeni stav, prioritu nebo vysledek ukolu","parameters":{"type":"object","properties":{"title":{"type":"string","description":"Nazev ukolu k nalezeni"},"status":{"type":"string","enum":["novy","naplanovany","v_reseni","ceka_na_klienta","ceka_na_material","ceka_na_platbu","hotovo","zruseno","predano_dal"]},"priority":{"type":"string","enum":["nizka","bezna","vysoka","urgentni","kriticka"]},"result":{"type":"string","description":"Vysledek ukolu"}},"required":["title"]}}},
            {"type":"function","function":{"name":"update_job","description":"Zmeni stav zakazky","parameters":{"type":"object","properties":{"title":{"type":"string","description":"Nazev zakazky"},"status":{"type":"string","enum":["nova","v_reseni","ceka_na_klienta","ceka_na_material","naplanovano","v_realizaci","dokonceno","vyfakturovano","uzavreno","pozastaveno","zruseno"]}},"required":["title"]}}},
            {"type":"function","function":{"name":"list_tasks","description":"Vypise ukoly podle filtru","parameters":{"type":"object","properties":{"status":{"type":"string"},"client_name":{"type":"string"},"only_active":{"type":"boolean"}}}}},
            {"type":"function","function":{"name":"complete_task","description":"Dokonci ukol a zapise vysledek","parameters":{"type":"object","properties":{"title":{"type":"string"},"result":{"type":"string","description":"Co bylo udelano"}},"required":["title"]}}},
            {"type":"function","function":{"name":"start_work_report","description":"Spusti hlasovy work report dialog. Pouzij kdyz Marek rekne ze chce zadat praci, work report, zapsat hodiny, nahlasit co delali.","parameters":{"type":"object","properties":{}}}},
            {"type":"function","function":{"name":"get_weather","description":"Zjisti predpoved pocasi. Pouzij kdyz se uzivatel pta na pocasi, teplotu, dest, vitr. Muze se ptat na dnes, zitra, nebo na konkretni den.","parameters":{"type":"object","properties":{"location":{"type":"string","description":"Nazev mesta nebo GPS souradnice. Default: Didcot, Oxfordshire"},"days":{"type":"integer","description":"Pocet dni predpovedi (1-7)","default":3}}}}},
            {"type":"function","function":{"name":"send_whatsapp","description":"Posle WhatsApp zpravu klientovi. Pouzij kdyz uzivatel rekne 'napis na whatsapp', 'posli whatsapp zpravu', 'whatsapp message'.","parameters":{"type":"object","properties":{"client_name":{"type":"string","description":"Jmeno klienta"},"message":{"type":"string","description":"Text zpravy k odeslani"}},"required":["client_name","message"]}}},
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
                    code = f"CL-{uuid.uuid4().hex[:6].upper()}"
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO clients (client_code,client_type,display_name,email_primary,phone_primary,status) VALUES (%s,%s,%s,%s,%s,'active') RETURNING id",
                            (code,"domestic",args["name"],args.get("email"),args.get("phone")))
                        cid = cur.fetchone()['id']
                        log_activity(conn,"client",cid,"create",f"Klient {args['name']} vytvoren")
                        conn.commit()
                    return {"reply_cs":f"Klient {args['name']} ({code}) je v CRM.","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
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
                return {"reply_cs":ai_msg.content or f"Hledam '{q}'...","action_type":"SEARCH_CONTACTS","action_data":{"query":q,"crm_results":crm},"is_question":True}

            if action == "CREATE_TASK":
                t = args.get("title","Ukol")
                conn = get_db_conn()
                try:
                    tid = str(uuid.uuid4())
                    with conn.cursor() as cur:
                        cur.execute("""INSERT INTO tasks (id,title,description,task_type,status,priority,deadline,
                            assigned_to,client_name,created_by,source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                            (tid,t,args.get("description"),args.get("task_type","interni_poznamka"),"novy",
                             args.get("priority","bezna"),args.get("deadline"),args.get("assigned_to"),
                             args.get("client_name"),"Marek","hlasovy_prikaz"))
                        task = dict(cur.fetchone())
                        log_activity(conn,"task",tid,"create",f"Ukol '{t}' vytvoren")
                        conn.commit()
                    return {"reply_cs":f"Vytvořila jsem úkol: {t}.","action_type":"CREATE_TASK","action_data":task}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
                finally: release_conn(conn)

            if action == "CREATE_JOB":
                t = args.get("title","Zakazka")
                conn = get_db_conn()
                try:
                    code = f"JOB-{uuid.uuid4().hex[:6].upper()}"
                    cname = args.get("client_name","")
                    cid = None
                    with conn.cursor() as cur:
                        if cname:
                            cur.execute("SELECT id FROM clients WHERE display_name ILIKE %s AND deleted_at IS NULL LIMIT 1",(f"%{cname}%",))
                            row = cur.fetchone()
                            if row: cid = row['id']
                        cur.execute("INSERT INTO jobs (job_number,client_id,job_title,job_status,start_date_planned) VALUES (%s,%s,%s,'nova',%s) RETURNING id",
                            (code,cid,t,args.get("start_date")))
                        jid = cur.fetchone()['id']
                        log_activity(conn,"job",jid,"create",f"Zakazka '{t}' ({code}) vytvorena")
                        conn.commit()
                    return {"reply_cs":f"Zakázka {code}: {t} vytvořena.","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
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
                    return {"reply_cs":f"Lead {code} od {n} zaevidován.","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
                finally: release_conn(conn)

            if action == "START_WORK_REPORT":
                lang_map = {"cs-CZ":"cs","en-GB":"en","pl-PL":"pl"}
                lang = lang_map.get(msg.internal_language,"en")
                return {"reply_cs":"Spouštím work report dialog." if lang=="cs" else "Starting work report." if lang=="en" else "Uruchamiam raport pracy.",
                        "action_type":"START_WORK_REPORT","action_data":{}}

            if action == "GET_WEATHER":
                try:
                    import urllib.request, urllib.parse
                    loc = args.get("location","Didcot")
                    days = min(args.get("days",3), 7)
                    # Geocode location
                    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(loc)}&count=1&language=en"
                    with urllib.request.urlopen(geo_url, timeout=5) as r:
                        geo = json.loads(r.read())
                    if not geo.get("results"):
                        return {"reply_cs": f"Lokalitu '{loc}' jsem nenašel. Zkus jiný název města."}
                    place = geo["results"][0]
                    lat, lon, name = place["latitude"], place["longitude"], place.get("name", loc)
                    # Fetch weather
                    wx_url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                        f"&hourly=temperature_2m,precipitation_probability,weathercode,windspeed_10m"
                        f"&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max"
                        f"&timezone=Europe/London&forecast_days={days}")
                    with urllib.request.urlopen(wx_url, timeout=8) as r:
                        wx = json.loads(r.read())
                    # Format daily summary
                    wmo = {0:"☀️ Jasno",1:"🌤 Polojasno",2:"⛅ Oblačno",3:"☁️ Zataženo",45:"🌫 Mlha",48:"🌫 Námraza",
                           51:"🌧 Mrholení",53:"🌧 Mrholení",55:"🌧 Mrholení",56:"🌧 Mrz. mrholení",57:"🌧 Mrz. mrholení",
                           61:"🌧 Déšť",63:"🌧 Střed. déšť",65:"🌧 Silný déšť",66:"🌧 Mrz. déšť",67:"🌧 Mrz. déšť",
                           71:"🌨 Sněžení",73:"🌨 Sněžení",75:"🌨 Silný sníh",77:"🌨 Krupky",
                           80:"🌦 Přeháňky",81:"🌦 Přeháňky",82:"🌦 Silné přeháňky",
                           85:"🌨 Sněh. přeháňky",86:"🌨 Sněh. přeháňky",
                           95:"⛈ Bouřka",96:"⛈ Bouřka s kroupami",99:"⛈ Silná bouřka"}
                    daily = wx.get("daily",{})
                    lines = [f"📍 Počasí — {name} ({days} dní):"]
                    for i, d in enumerate(daily.get("time",[])):
                        code = daily["weathercode"][i] if daily.get("weathercode") else 0
                        tmax = daily.get("temperature_2m_max",[None])[i]
                        tmin = daily.get("temperature_2m_min",[None])[i]
                        rain = daily.get("precipitation_sum",[None])[i]
                        rain_prob = daily.get("precipitation_probability_max",[None])[i]
                        wind = daily.get("windspeed_10m_max",[None])[i]
                        desc = wmo.get(code, f"Kód {code}")
                        line = f"\n{d}: {desc}, {tmin:.0f}–{tmax:.0f}°C"
                        if rain and rain > 0: line += f", déšť {rain:.1f}mm"
                        if rain_prob: line += f" ({rain_prob}%)"
                        if wind: line += f", vítr {wind:.0f} km/h"
                        lines.append(line)
                    # Add hourly for today
                    hourly = wx.get("hourly",{})
                    h_times = hourly.get("time",[])
                    h_temps = hourly.get("temperature_2m",[])
                    h_rain = hourly.get("precipitation_probability",[])
                    h_codes = hourly.get("weathercode",[])
                    if h_times:
                        lines.append("\n⏰ Hodinová předpověď dnes:")
                        today_str = daily.get("time",[""])[0]
                        for j, ht in enumerate(h_times[:24]):
                            if today_str in ht:
                                hour = ht.split("T")[1][:5]
                                if hour in ["06:00","09:00","12:00","15:00","18:00","21:00"]:
                                    t = h_temps[j] if j < len(h_temps) else "?"
                                    rp = h_rain[j] if j < len(h_rain) else 0
                                    cd = h_codes[j] if j < len(h_codes) else 0
                                    emoji = wmo.get(cd,"")[:2]
                                    lines.append(f"  {hour} {emoji} {t:.0f}°C, déšť {rp}%")
                    reply = "\n".join(lines)
                    return {"reply_cs": reply}
                except Exception as e:
                    return {"reply_cs": f"Nepodařilo se načíst počasí: {e}"}

            if action == "SEND_WHATSAPP":
                client_name = args.get("client_name","")
                message = args.get("message","")
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id,display_name,phone_primary FROM clients WHERE display_name ILIKE %s AND deleted_at IS NULL LIMIT 1", (f"%{client_name}%",))
                        client = cur.fetchone()
                    if not client:
                        return {"reply_cs": f"Klienta '{client_name}' jsem nenašel v CRM."}
                    phone = client["phone_primary"]
                    if not phone:
                        return {"reply_cs": f"Klient {client['display_name']} nemá telefonní číslo."}
                    result = wa_send_message(phone, message)
                    if "error" in result:
                        return {"reply_cs": f"WhatsApp se nepodařilo odeslat: {result.get('error','')}"}
                    with conn.cursor() as cur:
                        cur.execute("""INSERT INTO communications (tenant_id,client_id,comm_type,subject,message_summary,direction,notes,sent_at)
                            VALUES (1,%s,'whatsapp','WA zpráva',%s,'outbound',%s,now())""",
                            (client["id"], message[:500], f"To: {phone}"))
                    log_activity(conn,"communication",0,"whatsapp_out",f"WhatsApp na {client['display_name']}: {message[:100]}")
                    conn.commit()
                    return {"reply_cs": f"✅ WhatsApp zpráva odeslána klientovi {client['display_name']} na {phone}.", "action_type":"WHATSAPP_SENT","action_data":{"client_name":client["display_name"],"phone":phone}}
                except Exception as e:
                    return {"reply_cs": f"Chyba při odesílání WhatsApp: {e}"}
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
                            else: return {"reply_cs":f"Klient '{ename}' nenalezen."}
                        elif etype == "job":
                            ename = args.get("entity_name","")
                            cur.execute("SELECT id FROM jobs WHERE job_title ILIKE %s AND deleted_at IS NULL LIMIT 1",(f"%{ename}%",))
                            row = cur.fetchone()
                            if row:
                                cur.execute("INSERT INTO job_notes (job_id,note) VALUES (%s,%s)",(row['id'],note))
                                log_activity(conn,"job",row['id'],"note",f"Poznamka: {note[:50]}")
                            else: return {"reply_cs":f"Zakazka '{ename}' nenalezena."}
                        conn.commit()
                    return {"reply_cs":f"Poznámka přidána.","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
                finally: release_conn(conn)

            if action == "UPDATE_TASK":
                title_q = args.get("title","")
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id,title FROM tasks WHERE title ILIKE %s AND is_completed=FALSE ORDER BY created_at DESC LIMIT 1",(f"%{title_q}%",))
                        row = cur.fetchone()
                        if not row: return {"reply_cs":f"Úkol '{title_q}' nenalezen."}
                        sets = []; vals = []
                        if "status" in args: sets.append("status=%s"); vals.append(args["status"])
                        if "priority" in args: sets.append("priority=%s"); vals.append(args["priority"])
                        if "result" in args: sets.append("result=%s"); vals.append(args["result"])
                        if sets:
                            sets.append("updated_at=now()"); vals.append(row['id'])
                            cur.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=%s",vals)
                            log_activity(conn,"task",row['id'],"update",f"Ukol '{row['title']}' upraven")
                            conn.commit()
                        changes = ", ".join([f"{k}={v}" for k,v in args.items() if k != "title"])
                    return {"reply_cs":f"Úkol '{row['title']}' upraven: {changes}.","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
                finally: release_conn(conn)

            if action == "UPDATE_JOB":
                title_q = args.get("title","")
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id,job_title,job_status FROM jobs WHERE job_title ILIKE %s AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",(f"%{title_q}%",))
                        row = cur.fetchone()
                        if not row: return {"reply_cs":f"Zakázka '{title_q}' nenalezena."}
                        new_status = args.get("status",row['job_status'])
                        err = validate_state_transition(row['job_status'], new_status, JOB_TRANSITIONS, "Job")
                        if err: return {"reply_cs":f"Neplatný přechod: {err}"}
                        cur.execute("UPDATE jobs SET job_status=%s,updated_at=now() WHERE id=%s",(new_status,row['id']))
                        log_activity(conn,"job",row['id'],"status_change",f"Zakazka '{row['job_title']}': {row['job_status']} -> {new_status}")
                        conn.commit()
                    return {"reply_cs":f"Zakázka '{row['job_title']}' změněna na: {new_status}.","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
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
                    if not rows: return {"reply_cs":"Nemáš žádné aktivní úkoly."}
                    items = [f"- {r['title']} ({r['priority']}, {r['status']})" + (f" klient: {r['client_name']}" if r.get('client_name') else "") + (f" DL: {r['deadline']}" if r.get('deadline') else "") for r in rows]
                    return {"reply_cs":f"Máš {len(rows)} úkolů:\n" + "\n".join(items),"action_type":"LIST_TASKS"}
                finally: release_conn(conn)

            if action == "COMPLETE_TASK":
                title_q = args.get("title","")
                conn = get_db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id,title FROM tasks WHERE title ILIKE %s AND is_completed=FALSE ORDER BY created_at DESC LIMIT 1",(f"%{title_q}%",))
                        row = cur.fetchone()
                        if not row: return {"reply_cs":f"Úkol '{title_q}' nenalezen nebo už je hotový."}
                        result = args.get("result","Dokončeno")
                        cur.execute("UPDATE tasks SET status='hotovo',is_completed=TRUE,result=%s,updated_at=now() WHERE id=%s",(result,row['id']))
                        log_activity(conn,"task",row['id'],"complete",f"Ukol '{row['title']}' dokoncen: {result}")
                        conn.commit()
                    return {"reply_cs":f"Úkol '{row['title']}' dokončen. Výsledek: {result}","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
                finally: release_conn(conn)

            # === CLIENT-SIDE ACTIONS (passthrough to Android) ===
            human = {
                "ADD_CALENDAR_EVENT": f"Zapisuji {args.get('title','')} do kalendáře.",
                "MODIFY_CALENDAR_EVENT": f"Měním událost {args.get('event_title','')}.",
                "DELETE_CALENDAR_EVENT": f"Mažu událost {args.get('event_title','')}.",
                "LIST_CALENDAR_EVENTS": "Podívám se do kalendáře.",
                "CALL_CONTACT": f"Vytáčím {args.get('phone','')}.",
                "SEND_EMAIL": f"Posílám email na {args.get('to','')}.",
            }
            reply = ai_msg.content or human.get(action, f"Hotovo.")
            return {"reply_cs":reply,"action_type":action,"action_data":args}

        # No tool call — plain text reply
        reply = ai_msg.content or "Rozumím."
        # Fallback: if reply mentions work report but GPT didn't call tool, force it
        wr_kw = ["work report","výkaz","vykaz","nahlášení práce","nahlaseni prace","zapsat práci","zapsat praci","raport pracy","zahajuji proces"]
        if any(kw in (reply + " " + msg.text).lower() for kw in wr_kw):
            return {"reply_cs":reply,"action_type":"START_WORK_REPORT","action_data":{}}
        return {"reply_cs":reply,"is_question":"?" in reply}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"reply_cs":f"Chyba: {type(e).__name__}: {str(e)}"}

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
async def get_client_detail(client_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clients WHERE id=%s AND deleted_at IS NULL",(client_id,))
            cl = cur.fetchone()
            if not cl: raise HTTPException(404,"Klient nenalezen")
            cur.execute("SELECT * FROM properties WHERE client_id=%s AND deleted_at IS NULL",(client_id,))
            props = cur.fetchall()
            cur.execute("SELECT j.*,j.start_date_planned::text as start_date_planned FROM jobs j WHERE j.client_id=%s AND j.deleted_at IS NULL ORDER BY j.created_at DESC LIMIT 10",(client_id,))
            jobs = cur.fetchall()
            cur.execute("SELECT id,client_id,job_id,comm_type,subject,message_summary,sent_at::text,direction,notes,created_at::text FROM communications WHERE client_id=%s ORDER BY created_at DESC LIMIT 10",(client_id,))
            comms = cur.fetchall()
            cur.execute("SELECT * FROM tasks WHERE client_id=%s AND is_completed=FALSE ORDER BY created_at DESC LIMIT 10",(client_id,))
            tasks = cur.fetchall()
            cur.execute("SELECT id,note,created_by,created_at::text FROM client_notes WHERE client_id=%s ORDER BY created_at DESC LIMIT 20",(client_id,))
            notes = cur.fetchall()
            return {"client":dict(cl),"properties":[dict(p) for p in props],"recent_jobs":[dict(j) for j in jobs],
                    "communications":[dict(c) for c in comms],"tasks":[dict(t) for t in tasks],"notes":[dict(n) for n in notes]}
    finally: release_conn(conn)

@app.post("/crm/clients")
async def api_create_client(data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        ok, msg = check_subscription_limit(conn, tid, "clients")
        if not ok: raise HTTPException(429, msg)
        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO clients (client_code,client_type,title,first_name,last_name,display_name,
                company_name,company_registration_no,vat_no,email_primary,email_secondary,
                phone_primary,phone_secondary,website,preferred_contact_method,
                billing_address_line1,billing_city,billing_postcode,billing_country,
                status,is_commercial,tenant_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s) RETURNING id""",
                (code,data.get("type",data.get("client_type","domestic")),
                 data.get("title"),data.get("first_name"),data.get("last_name"),
                 data.get("name",data.get("display_name","")),
                 data.get("company_name"),data.get("company_registration_no"),data.get("vat_no"),
                 data.get("email",data.get("email_primary")),data.get("email_secondary"),
                 data.get("phone",data.get("phone_primary")),data.get("phone_secondary"),
                 data.get("website"),data.get("preferred_contact_method","email"),
                 data.get("billing_address_line1"),data.get("billing_city"),
                 data.get("billing_postcode"),data.get("billing_country","GB"),
                 data.get("is_commercial",False),tid))
            cid = cur.fetchone()['id']
            log_activity(conn,"client",cid,"create",f"Klient {data.get('name',data.get('display_name',''))} vytvoren")
            conn.commit()
        return {"id":cid,"client_code":code,"status":"success"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.put("/crm/clients/{client_id}")
async def update_client(client_id: int, data: dict):
    conn = get_db_conn()
    try:
        sets = []; vals = []
        for k in ["display_name","first_name","last_name","title","client_type","company_name","company_registration_no","vat_no","email_primary","email_secondary","phone_primary","phone_secondary","website","preferred_contact_method","billing_address_line1","billing_city","billing_postcode","billing_country","status","is_commercial"]:
            if k in data: sets.append(f"{k}=%s"); vals.append(data[k])
        if not sets: raise HTTPException(400,"Zadna data")
        sets.append("updated_at=now()"); vals.append(client_id)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE clients SET {','.join(sets)} WHERE id=%s AND deleted_at IS NULL",vals)
            log_activity(conn,"client",client_id,"update",f"Klient upraven: {list(data.keys())}")
            conn.commit()
        return {"status":"updated"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

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
async def sync_contacts(data: dict):
    """Sync phone contacts to CRM. Creates new clients for unknown numbers, skips existing."""
    contacts = data.get("contacts", [])
    if not contacts: raise HTTPException(400, "No contacts")
    conn = get_db_conn()
    created = 0; skipped = 0; errors = []
    try:
        with conn.cursor() as cur:
            for c in contacts:
                name = c.get("name","").strip()
                phone = c.get("phone","").strip()
                email = c.get("email","")
                if not name or not phone: continue
                clean = phone.replace("+","").replace(" ","").replace("-","")
                cur.execute("SELECT id FROM clients WHERE REPLACE(REPLACE(REPLACE(phone_primary,'+',''),' ',''),'-','') = %s AND deleted_at IS NULL LIMIT 1", (clean,))
                if cur.fetchone():
                    skipped += 1; continue
                code = f"CL-PH-{clean[-6:]}"
                try:
                    cur.execute("""INSERT INTO clients (client_code,client_type,display_name,phone_primary,email_primary,source,status,tenant_id)
                        VALUES (%s,'individual',%s,%s,%s,'phone_sync','active',1) ON CONFLICT DO NOTHING RETURNING id""",
                        (code, name, phone, email if email else None))
                    row = cur.fetchone()
                    if row:
                        created += 1
                        log_activity(conn,"client",row["id"],"sync",f"Kontakt importovan z telefonu: {name}")
                except Exception as e:
                    errors.append(f"{name}: {e}")
            conn.commit()
        return {"created": created, "skipped": skipped, "errors": errors, "total": len(contacts)}
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

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
            sql = "SELECT j.id,j.job_number,j.job_title,j.job_status,j.client_id,j.property_id,j.quote_id,j.start_date_planned::text,j.created_at::text,j.updated_at::text,c.display_name as client_name FROM jobs j LEFT JOIN clients c ON j.client_id=c.id WHERE j.deleted_at IS NULL AND j.tenant_id=%s"
            params = [tid]
            if client_id: sql += " AND j.client_id=%s"; params.append(client_id)
            if status: sql += " AND j.job_status=%s"; params.append(status)
            sql += " ORDER BY j.created_at DESC"
            cur.execute(sql,params); return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/jobs/{job_id}")
async def get_job_detail(job_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id=%s AND deleted_at IS NULL",(job_id,))
            job = cur.fetchone()
            if not job: raise HTTPException(404)
            cur.execute("SELECT * FROM tasks WHERE job_id=%s ORDER BY created_at DESC",(job_id,))
            tasks = cur.fetchall()
            cur.execute("SELECT id,note,created_by,created_at::text FROM job_notes WHERE job_id=%s ORDER BY created_at DESC",(job_id,))
            notes = cur.fetchall()
            return {"job":dict(job),"tasks":[dict(t) for t in tasks],"notes":[dict(n) for n in notes]}
    finally: release_conn(conn)

@app.post("/crm/jobs")
async def create_job(data: dict, request: Request):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        ok, msg = check_subscription_limit(conn, tid, "jobs")
        if not ok: raise HTTPException(429, msg)
        code = f"JOB-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("INSERT INTO jobs (job_number,client_id,property_id,job_title,job_status,start_date_planned) VALUES (%s,%s,%s,%s,'nova',%s) RETURNING id",
                (code,data.get("client_id"),data.get("property_id",data.get("client_id")),data.get("title","Zakazka"),data.get("start_date")))
            jid = cur.fetchone()['id']
            log_activity(conn,"job",jid,"create",f"Zakazka {code} vytvorena")
            conn.commit()
        return {"id":jid,"job_number":code,"status":"created"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.put("/crm/jobs/{job_id}")
async def update_job(job_id: int, data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Validate state transition if status is being changed
            if "job_status" in data:
                cur.execute("SELECT job_status FROM jobs WHERE id=%s AND deleted_at IS NULL",(job_id,))
                row = cur.fetchone()
                if not row: raise HTTPException(404,"Job not found")
                err = validate_state_transition(row["job_status"], data["job_status"], JOB_TRANSITIONS, "Job")
                if err: raise HTTPException(422, err)
            sets = []; vals = []
            for k in ["job_title","job_status","start_date_planned"]:
                if k in data: sets.append(f"{k}=%s"); vals.append(data[k])
            if not sets: raise HTTPException(400)
            sets.append("updated_at=now()"); vals.append(job_id)
            cur.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=%s AND deleted_at IS NULL",vals)
            log_activity(conn,"job",job_id,"update",f"Zakazka upravena: {list(data.keys())}")
            conn.commit()
        return {"status":"updated"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== REST API: TASKS ==========
@app.get("/crm/tasks")
async def get_tasks(request: Request, status: Optional[str]=None, client_id: Optional[int]=None, job_id: Optional[int]=None, completed: Optional[bool]=None):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM tasks WHERE tenant_id=%s"; params = [tid]
            if status: sql += " AND status=%s"; params.append(status)
            if client_id: sql += " AND client_id=%s"; params.append(client_id)
            if job_id: sql += " AND job_id=%s"; params.append(job_id)
            if completed is not None: sql += " AND is_completed=%s"; params.append(completed)
            sql += " ORDER BY CASE priority WHEN 'kriticka' THEN 1 WHEN 'urgentni' THEN 2 WHEN 'vysoka' THEN 3 WHEN 'bezna' THEN 4 ELSE 5 END, created_at DESC"
            cur.execute(sql,params); return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/tasks")
async def api_create_task(data: dict):
    conn = get_db_conn()
    try:
        tid = data.get("id",str(uuid.uuid4()))
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO tasks (id,title,description,task_type,status,priority,deadline,planned_date,
                estimated_minutes,created_by,assigned_to,client_id,client_name,job_id,property_id,property_address,
                is_recurring,recurrence_rule,communication_method,source,is_billable,has_cost,checklist)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (tid,data.get("title",""),data.get("description"),data.get("task_type","interni_poznamka"),
                 data.get("status","novy"),data.get("priority","bezna"),data.get("deadline"),data.get("planned_date"),
                 data.get("estimated_minutes"),data.get("created_by","Marek"),data.get("assigned_to"),
                 data.get("client_id"),data.get("client_name"),data.get("job_id"),data.get("property_id"),
                 data.get("property_address"),data.get("is_recurring",False),data.get("recurrence_rule"),
                 data.get("communication_method"),data.get("source","manualne"),
                 data.get("is_billable",False),data.get("has_cost",False),json.dumps(data.get("checklist",[]))))
            task = dict(cur.fetchone())
            log_activity(conn,"task",tid,"create",f"Ukol '{data.get('title','')}' vytvoren")
            conn.commit()
        return task
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.put("/crm/tasks/{task_id}")
async def update_task(task_id: str, data: dict):
    conn = get_db_conn()
    try:
        sets = []; vals = []
        for k in ["title","description","task_type","status","priority","deadline","assigned_to","result","is_completed","actual_minutes","planned_date"]:
            if k in data: sets.append(f"{k}=%s"); vals.append(data[k])
        if "notes" in data: sets.append("notes=%s"); vals.append(json.dumps(data["notes"]))
        if "checklist" in data: sets.append("checklist=%s"); vals.append(json.dumps(data["checklist"]))
        sets.append("updated_at=now()"); vals.append(task_id)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=%s",vals)
            log_activity(conn,"task",task_id,"update",f"Ukol upraven: {list(data.keys())}")
            conn.commit()
        return {"status":"updated"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.delete("/crm/tasks/{task_id}")
async def delete_task(task_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id=%s",(task_id,))
            log_activity(conn,"task",task_id,"delete","Ukol smazan")
            conn.commit()
        return {"status":"deleted"}
    finally: release_conn(conn)

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
            name = data.get("name",lead.get("contact_name","Nový klient"))
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
            # Get client default rate
            cur.execute("SELECT default_hourly_rate FROM clients WHERE id=%s AND tenant_id=%s", (wr["client_id"], tid))
            cl = cur.fetchone()
            client_rate = float(cl["default_hourly_rate"] or 0) if cl else 0
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
                if rate == 0 and client_rate > 0: rate = client_rate
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
            cur.execute("UPDATE clients SET default_hourly_rate=%s, updated_at=now() WHERE id=%s AND tenant_id=%s RETURNING id,display_name,default_hourly_rate",
                (data.get("default_hourly_rate",0), client_id, tid))
            c = cur.fetchone()
            if not c: raise HTTPException(404, "Client not found")
            log_activity(conn, "client", client_id, "update_rate", f"Default rate: {c['default_hourly_rate']} GBP/h")
            conn.commit()
        return dict(c)
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

def get_effective_rate(conn, tid, user_id=None, client_id=None, rate_type="hourly_rate"):
    """Get rate with fallback: user > client > tenant default."""
    with conn.cursor() as cur:
        if user_id:
            cur.execute(f"SELECT {rate_type} FROM users WHERE id=%s AND tenant_id=%s", (user_id, tid))
            r = cur.fetchone()
            if r and float(r[rate_type] or 0) > 0: return float(r[rate_type])
        if client_id and rate_type == "hourly_rate":
            cur.execute("SELECT default_hourly_rate FROM clients WHERE id=%s AND tenant_id=%s", (client_id, tid))
            r = cur.fetchone()
            if r and float(r["default_hourly_rate"] or 0) > 0: return float(r["default_hourly_rate"])
        cur.execute("SELECT rate FROM tenant_default_rates WHERE tenant_id=%s AND rate_type=%s", (tid, rate_type))
        r = cur.fetchone()
        return float(r["rate"]) if r else 0

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
            sql = """SELECT c.id, c.client_id, c.job_id, c.comm_type, c.subject, c.message_summary,
                     c.sent_at::text, c.direction, c.notes, c.created_at::text,
                     cl.display_name as client_name, j.job_title as job_title
                     FROM communications c
                     LEFT JOIN clients cl ON c.client_id = cl.id
                     LEFT JOIN jobs j ON c.job_id = j.id
                     WHERE c.tenant_id=%s"""
            params = [tid]
            if client_id: sql += " AND c.client_id=%s"; params.append(client_id)
            if job_id: sql += " AND c.job_id=%s"; params.append(job_id)
            if comm_type: sql += " AND c.comm_type=%s"; params.append(comm_type)
            sql += " ORDER BY c.created_at DESC LIMIT 100"
            cur.execute(sql, params); return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/communications")
async def log_communication(request: Request, data: dict):
    tid = get_request_tenant_id(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO communications (tenant_id,client_id,job_id,comm_type,subject,message_summary,direction,notes,sent_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()) RETURNING id,comm_type,subject,direction""",
                (tid, data.get("client_id"),data.get("job_id"),data.get("comm_type","telefon"),
                 data.get("subject"),data.get("message",data.get("message_summary","")),
                 data.get("direction","outbound"),data.get("notes")))
            comm = dict(cur.fetchone())
            if data.get("client_id"):
                log_activity(conn,"client",data["client_id"],"communication",f"{comm.get('comm_type','')}: {comm.get('subject','')}")
            conn.commit()
        return comm
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

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
                            (code,row.get("type","domestic"),row.get("name",row.get("display_name","")),row.get("email",row.get("email_primary")),row.get("phone",row.get("phone_primary"))))
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
            return {"company_name":"DesignLeaf","version":"1.2a","database":"PostgreSQL",
                    "clients_count":cc,"jobs_count":jc,"tasks_count":tc,"leads_count":lc,
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
            for sql in ["CREATE TABLE IF NOT EXISTS roles (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, role_name TEXT NOT NULL UNIQUE, description TEXT, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS users (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, role_id BIGINT, first_name TEXT NOT NULL, last_name TEXT NOT NULL, display_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT, status TEXT DEFAULT 'active', password_hash TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(), deleted_at TIMESTAMPTZ)","CREATE TABLE IF NOT EXISTS audit_log (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, user_id BIGINT, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, old_values JSONB, new_values JSONB, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS quotes (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tenant_id INT DEFAULT 1, quote_number TEXT UNIQUE, client_id BIGINT, status TEXT DEFAULT 'draft', total NUMERIC(12,2) DEFAULT 0, created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS tenants (id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT now())","CREATE TABLE IF NOT EXISTS migration_log (id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ DEFAULT now())"]:
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
@app.on_event("startup")
async def ensure_quote_items():
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
            cur.execute("INSERT INTO job_notes (job_id,note,created_by,tenant_id) VALUES (%s,%s,%s,%s) RETURNING id",
                (job_id, data.get("note",""), data.get("created_by","system"), tid))
            nid = cur.fetchone()['id']; conn.commit()
        return {"id":nid,"status":"created"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

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

@app.post("/auth/login")
async def auth_login(data: dict):
    email = data.get("email","").strip().lower()
    password = data.get("password","").strip()
    if not email or not password: raise HTTPException(400, "Email and password required")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT u.id, u.tenant_id, u.display_name, u.email, u.password_hash, u.status, u.is_owner, u.is_assistant,
                r.role_name FROM users u LEFT JOIN roles r ON u.role_id=r.id
                WHERE LOWER(u.email)=%s AND u.deleted_at IS NULL""", (email,))
            user = cur.fetchone()
            if not user: raise HTTPException(401, "Invalid credentials")
            if user["status"] and user["status"] not in ("active","setup"):
                raise HTTPException(403, f"Account is {user['status']}")
            if not user["password_hash"] or not verify_password(password, user["password_hash"]):
                raise HTTPException(401, "Invalid credentials")
            role = user["role_name"] or "viewer"
            access = create_token(user["id"], user["tenant_id"], role, "access")
            refresh = create_token(user["id"], user["tenant_id"], role, "refresh")
            log_activity(conn, "user", str(user["id"]), "login", f"{user['display_name']} logged in", tenant_id=user["tenant_id"], user_id=user["id"])
            conn.commit()
        return {
            "access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "user": {"id": user["id"], "display_name": user["display_name"], "email": user["email"],
                     "role": role, "tenant_id": user["tenant_id"], "is_owner": user["is_owner"]}
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
                u.preferred_language_code, r.role_name FROM users u LEFT JOIN roles r ON u.role_id=r.id WHERE u.id=%s""", (user["user_id"],))
            u = cur.fetchone()
            if not u: raise HTTPException(404, "User not found")
        return dict(u)
    finally: release_conn(conn)

@app.post("/auth/register")
async def auth_register(data: dict, admin: dict = Depends(require_role("admin"))):
    """Admin-only: register new user."""
    email = data.get("email","").strip().lower()
    password = data.get("password","").strip()
    display_name = data.get("display_name","").strip()
    if not email or not password or not display_name:
        raise HTTPException(400, "email, password, display_name required")
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
            cur.execute("""INSERT INTO users (tenant_id, role_id, first_name, last_name, display_name, email, phone, password_hash, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'active') RETURNING id""",
                (admin["tenant_id"], role_id, data.get("first_name",""), data.get("last_name",""),
                 display_name, email, data.get("phone",""), hash_password(password)))
            uid = cur.fetchone()["id"]
            log_activity(conn, "user", str(uid), "register", f"User {display_name} registered by admin", tenant_id=admin["tenant_id"], user_id=admin["user_id"])
            conn.commit()
        return {"id": uid, "email": email, "display_name": display_name, "role": role_name}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.put("/auth/change-password")
async def auth_change_password(data: dict, user: dict = Depends(require_auth)):
    old_pw = data.get("old_password","")
    new_pw = data.get("new_password","")
    if not old_pw or not new_pw: raise HTTPException(400, "old_password and new_password required")
    if len(new_pw) < 6: raise HTTPException(400, "Password must be at least 6 characters")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE id=%s", (user["user_id"],))
            u = cur.fetchone()
            if not u or not verify_password(old_pw, u["password_hash"]):
                raise HTTPException(401, "Old password incorrect")
            cur.execute("UPDATE users SET password_hash=%s, updated_at=now() WHERE id=%s", (hash_password(new_pw), user["user_id"]))
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
    "team":     {"max_users":5,  "max_clients":2000, "max_jobs":500,  "max_voice":3000},
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
    defaults = {"worker_rate":35.0,"task_rate":35.0,"waste_rate":80.0,"material_price":0.0}
    return defaults.get(rule_type, 0.0)

# ========== DIALOG STATE MACHINE ==========
DIALOG_STEPS = ["client","workers","total_hours","entries","validate_hours","waste","materials","notes","summary","confirm"]
VALID_TRANSITIONS = {
    "client": ["client","workers"],
    "workers": ["workers","total_hours"],
    "total_hours": ["total_hours","entries"],
    "entries": ["entries","validate_hours","waste"],
    "validate_hours": ["validate_hours","waste","entries"],
    "materials": ["materials","notes"],
    "waste": ["waste","materials"],
    "notes": ["notes","summary"],
    "summary": ["summary","confirm","client","workers","total_hours","entries","materials","waste","notes"],
    "confirm": ["confirm"],
}
def validate_transition(current_step, next_step):
    return next_step in VALID_TRANSITIONS.get(current_step, [])
DIALOG_PROMPTS = {
    "client": {"en":"Which client did you work for?","cs":"U kterého klienta jsi pracoval?","pl":"U którego klienta pracowałeś?"},
    "workers": {"en":"Who worked? (names)","cs":"Kdo pracoval? (jména)","pl":"Kto pracował? (imiona)"},
    "total_hours": {"en":"How many hours total?","cs":"Kolik hodin celkem?","pl":"Ile godzin łącznie?"},
    "entries": {"en":"How many hours pruning?","cs":"Kolik hodin prořez?","pl":"Ile godzin przycinanie?"},
    "validate_hours": {"en":"Hours don't match total. Fix entries or total.","cs":"Hodiny nesedí s celkem. Oprav položky nebo celkem.","pl":"Godziny się nie zgadzają. Popraw pozycje lub sumę."},
    "materials": {"en":"Any materials used? (name, quantity, price) or 'no'","cs":"Použili jste materiál? (název, množství, cena) nebo 'ne'","pl":"Czy użyto materiałów? (nazwa, ilość, cena) lub 'nie'"},
    "waste": {"en":"How many bulk bags of waste? (number or 'none')","cs":"Kolik pytlů odpadu? (číslo nebo 'žádný')","pl":"Ile worków odpadów? (liczba lub 'żaden')"},
    "notes": {"en":"Any notes? (or 'no')","cs":"Chceš přidat poznámku? (nebo 'ne')","pl":"Chcesz dodać notatkę? (lub 'nie')"},
    "summary": {"en":"Here is the summary. Say 'confirm' to save or 'edit [field]' to change.","cs":"Tady je shrnutí. Řekni 'potvrdit' pro uložení nebo 'oprav [pole]' pro změnu.","pl":"Oto podsumowanie. Powiedz 'potwierdź' aby zapisać lub 'popraw [pole]' aby zmienić."},
    "confirm": {"en":"Work report saved.","cs":"Report uložen.","pl":"Raport zapisany."},
}
def get_prompt(step, lang="en"):
    return DIALOG_PROMPTS.get(step,{}).get(lang, DIALOG_PROMPTS.get(step,{}).get("en",""))

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
async def voice_session_start(data: dict):
    conn = get_db_conn()
    try:
        sid = str(uuid.uuid4())
        tenant_id = data.get("tenant_id",1)
        tenant_config = get_tenant_config(conn, tenant_id)
        lang = resolve_voice_language(tenant_config, data.get("language"))
        with conn.cursor() as cur:
            ctx = json.dumps({"language":lang,"work_date":data.get("work_date",datetime.now().strftime("%Y-%m-%d"))})
            cur.execute("INSERT INTO voice_sessions (id,tenant_id,user_id,session_type,state,dialog_step,context) VALUES (%s,%s,%s,'work_report','active','client',%s)",
                (sid,tenant_id,data.get("user_id"),ctx))
            conn.commit()
        return {"session_id":sid,"step":"client","prompt":get_prompt("client",lang)}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.post("/voice/session/input")
async def voice_session_input(data: dict):
    sid = data.get("session_id")
    text = data.get("text","").strip()
    if not sid: raise HTTPException(400,"session_id required")
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
                cur.execute("SELECT id,display_name FROM clients WHERE tenant_id=%s AND deleted_at IS NULL AND (display_name ILIKE %s OR client_code ILIKE %s) LIMIT 5",
                    (tenant_id,f"%{text}%",f"%{text}%"))
                matches = cur.fetchall()
                if len(matches) == 1:
                    ctx["client_id"] = matches[0]['id']; ctx["client_name"] = matches[0]['display_name']
                    next_step = "workers"; reply = f"{matches[0]['display_name']}. {get_prompt('workers',lang)}"
                elif len(matches) > 1:
                    names = ", ".join([m['display_name'] for m in matches])
                    reply = f"Found: {names}. Which one?" if lang=="en" else f"Nalezeni: {names}. Který?" if lang=="cs" else f"Znalezieni: {names}. Który?"
                else:
                    reply = "Client not found. Try again." if lang=="en" else "Klient nenalezen. Zkus znovu." if lang=="cs" else "Klient nie znaleziony. Spróbuj ponownie."

            # === STEP: WORKERS ===
            elif step == "workers":
                names = [n.strip() for n in text.replace(" and ",",").replace(" a ",",").replace(" i ",",").split(",") if n.strip()]
                workers = []; not_found = []
                for name in names:
                    cur.execute("SELECT id,display_name FROM users WHERE tenant_id=%s AND display_name ILIKE %s AND deleted_at IS NULL LIMIT 1",(tenant_id,f"%{name}%"))
                    u = cur.fetchone()
                    if u:
                        rate = resolve_rate(conn,tenant_id,"worker_rate",rule_key=str(u['id']),job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                        workers.append({"name":u['display_name'],"user_id":u['id'],"hours":0,"rate":rate,"total":0})
                    else:
                        not_found.append(name)
                if workers and not not_found:
                    ctx["workers"] = workers; next_step = "total_hours"
                    reply = f"{len(workers)} workers. {get_prompt('total_hours',lang)}"
                elif workers and not_found:
                    ctx["workers"] = workers
                    nf = ", ".join(not_found)
                    reply = f"Not found in system: {nf}. Found: {len(workers)}. Add more or say 'continue'." if lang=="en" else f"Nenalezeni: {nf}. Nalezeno: {len(workers)}. Přidej další nebo řekni 'pokračuj'."
                elif "continu" in text.lower() or "pokrac" in text.lower() or "dalej" in text.lower():
                    if ctx.get("workers"):
                        next_step = "total_hours"; reply = get_prompt("total_hours",lang)
                    else:
                        reply = "No workers added. Try again." if lang=="en" else "Žádní pracovníci. Zkus znovu."
                else:
                    reply = "No workers found in system. Use exact names." if lang=="en" else "Žádní pracovníci nenalezeni. Použij přesná jména." if lang=="cs" else "Nie znaleziono pracowników. Użyj dokładnych imion."

            # === STEP: TOTAL HOURS ===
            elif step == "total_hours":
                try:
                    _num_words = {"nula":0,"jedna":1,"jeden":1,"jedno":1,"dva":2,"dve":2,"tri":3,"tři":3,"ctyri":4,"čtyři":4,"pet":5,"pět":5,"sest":6,"šest":6,"sedm":7,"osm":8,"devet":9,"devět":9,"deset":10,"jedenact":11,"dvanact":12,
                        "zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,
                        "jeden a půl":1.5,"jedna a půl":1.5,"dva a půl":2.5,"dvě a půl":2.5,"tři a půl":3.5,"čtyři a půl":4.5,"pět a půl":5.5,"šest a půl":6.5,"sedm a půl":7.5,"osm a půl":8.5,
                        "půl":0.5,"half":0.5}
                    _clean = text.lower().replace("hours","").replace("hodin","").replace("hodiny","").replace("hodinu","").replace("godzin","").strip()
                    if _clean in _num_words:
                        hrs = _num_words[_clean]
                    else:
                        hrs = float(_clean.replace(",","."))
                    ctx["total_hours"] = hrs
                    # Distribute equally if multiple workers
                    wc = len(ctx.get("workers",[]))
                    if wc > 0:
                        per = round(hrs / wc, 2)
                        for w in ctx["workers"]: w["hours"] = per; w["total"] = round(per * w["rate"],2)
                    ctx["_entry_sub"] = "pruning"; ctx["entries"] = []; next_step = "entries"; reply = f"{hrs}h. " + get_prompt("entries",lang)
                except: reply = "Invalid number." if lang=="en" else "Neplatné číslo." if lang=="cs" else "Nieprawidłowa liczba."

            # === STEP: ENTRIES (pruning -> maintenance -> additional if needed) ===
            elif step == "entries":
                _nw = {"nula":0,"jedna":1,"jeden":1,"dva":2,"dve":2,"dvě":2,"tri":3,"tři":3,"ctyri":4,"čtyři":4,"pet":5,"pět":5,"sest":6,"šest":6,"sedm":7,"osm":8,"devet":9,"devět":9,"deset":10,"půl":0.5,"half":0.5,
                    "zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
                sub = ctx.get("_entry_sub","pruning")
                low = text.lower().strip()
                def _parse_hours(t):
                    t2 = t.lower().replace("hodin","").replace("hodiny","").replace("hodinu","").replace("hours","").replace("h","").replace(",",".").strip()
                    if t2 in _nw: return _nw[t2]
                    return float(t2)
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
                if any(text.lower().startswith(x) for x in ("no","ne","nie","none","zadny","żaden","skip","přeskoč","preskoc","dalsi","další","zadny","žádný")):
                    ctx["materials"] = []
                else:
                    import re
                    mats = []
                    parts = re.findall(r'(\w[\w\s]*?)\s+([\d.,]+)\s*[x×]?\s*£?([\d.,]+)?', text)
                    for mname, mqty, mprice in parts:
                        q = float(mqty.replace(",","."))
                        p = float(mprice.replace(",",".")) if mprice else 0
                        mats.append({"name":mname.strip(),"qty":q,"price":p,"total":round(q*p,2)})
                    if not mats and text.lower() not in ("no","ne","nie","none","skip"):
                        mats.append({"name":text,"qty":1,"price":0,"total":0})
                    ctx["materials"] = mats
                next_step = "notes"; reply = get_prompt("notes",lang)

            # === STEP: WASTE ===
            elif step == "waste":
                if any(text.lower().startswith(x) for x in ("no","ne","nie","none","zadny","żaden","0","skip","přeskoč","preskoc","dalsi","další")):
                    ctx["waste"] = {"qty":0,"rate":0,"total":0}
                else:
                    try:
                        qty = float(text.replace(",",".").split()[0])
                        rate = resolve_rate(conn,tenant_id,"waste_rate",job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                        ctx["waste"] = {"qty":qty,"rate":rate,"total":round(qty*rate,2)}
                    except: ctx["waste"] = {"qty":0,"rate":0,"total":0}
                next_step = "materials"; reply = get_prompt("materials",lang)

            # === STEP: NOTES ===
            elif step == "notes":
                if not any(text.lower().startswith(x) for x in ("no","ne","nie","skip","přeskoč","preskoc","dalsi","další")) and text.strip() != "":
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
                # POTVRDIT
                if any(x in low for x in ["confirm","potvrdit","potwierdź","yes","ano","tak","uložit","ulozit","save"]):
                    next_step = "confirm"
                # ZRUSIT / SMAZAT
                elif any(x in low for x in ["zrušit","zrusit","smazat","cancel","delete","storno","konec","stop"]):
                    cur.execute("UPDATE voice_sessions SET state='cancelled',updated_at=now() WHERE id=%s",(sid,))
                    conn.commit()
                    reply = "Report zrušen." if lang=="cs" else "Report cancelled." if lang=="en" else "Raport anulowany."
                    return {"session_id":sid,"step":"done","prompt":reply}
                # OPRAVIT
                elif any(low.startswith(x) for x in ["edit","oprav","popraw","zmen","změ","uprav"]):
                    _step_map = {"client":"client","klient":"client","klienta":"client",
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
                        reply = "Co opravit? (klient/pracovniky/hodiny/polozky/odpad/material/poznamku)" if lang=="cs" else "What to edit?"
                else:
                    reply = "Řekni 'potvrdit', 'oprav [co]', nebo 'zrušit'." if lang=="cs" else "Say 'confirm', 'edit [field]', or 'cancel'."

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
                    cur.execute("""INSERT INTO work_reports (tenant_id,client_id,job_id,work_date,total_hours,total_price,notes,created_by,input_type,status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'voice','confirmed') RETURNING id""",
                        (tenant_id,ctx.get("client_id"),ctx.get("job_id"),ctx.get("work_date",datetime.now().strftime("%Y-%m-%d")),
                         ctx.get("total_hours",0),ctx.get("grand_total",0),ctx.get("notes"),sess.get("user_id")))
                    rid = cur.fetchone()['id']
                    for w in ctx.get("workers",[]):
                        cur.execute("INSERT INTO work_report_workers (work_report_id,user_id,worker_name,hours,hourly_rate,total_price) VALUES (%s,%s,%s,%s,%s,%s)",
                            (rid,w.get("user_id"),w["name"],w["hours"],w["rate"],w["total"]))
                    for e in ctx.get("entries",[]):
                        cur.execute("INSERT INTO work_report_entries (work_report_id,type,hours,unit_rate,total_price) VALUES (%s,%s,%s,%s,%s)",
                            (rid,e["type"],e["hours"],e["rate"],e["total"]))
                    for m in ctx.get("materials",[]):
                        cur.execute("INSERT INTO work_report_materials (work_report_id,material_name,quantity,unit_price,total_price) VALUES (%s,%s,%s,%s,%s)",
                            (rid,m["name"],m["qty"],m["price"],m["total"]))
                    waste = ctx.get("waste",{})
                    if waste.get("qty",0) > 0:
                        cur.execute("INSERT INTO work_report_waste (work_report_id,quantity,unit,unit_price,total_price) VALUES (%s,%s,'bulkbag',%s,%s)",
                            (rid,waste["qty"],waste["rate"],waste["total"]))
                    log_activity(conn,"work_report",str(rid),"create",f"Work report £{ctx.get('grand_total',0):.2f} for {ctx.get('client_name','?')}")
                    cur.execute("UPDATE voice_sessions SET state='completed',context=%s,updated_at=now() WHERE id=%s",(json.dumps(ctx),sid))
                    conn.commit()
                    whatsapp = generate_whatsapp(ctx)
                    reply = get_prompt("confirm",lang)
                    return {"session_id":sid,"step":"done","prompt":reply,"work_report_id":rid,"whatsapp_message":whatsapp,"summary":generate_summary(ctx,lang)}
                  except Exception as e:
                    conn.rollback(); raise HTTPException(500,f"Save error: {e}")

            # === AUDIT: structured voice step log ===
            audit_details = json.dumps({
                "step": step, "next_step": next_step,
                "input_length": len(text),
                "input_preview": text[:50].replace("\n", " "),
                "has_numbers": any(c.isdigit() for c in text) or any(x in text.lower() for x in ["half","quarter","one","two","three"])
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

def wa_send_message(to_phone: str, message: str):
    """Send WhatsApp message via Cloud API. Auto-translates to English."""
    if not WA_TOKEN or not WA_PHONE_ID:
        return {"error": "WhatsApp not configured"}
    # Auto-translate to English using GPT
    translated = message
    if ai_client:
        try:
            tr = ai_client.chat.completions.create(model="gpt-4o-mini", messages=[
                {"role":"system","content":"Translate the following message to English. Return ONLY the translation, nothing else. If already in English, return as-is."},
                {"role":"user","content":message}
            ], max_tokens=500)
            translated = tr.choices[0].message.content.strip()
        except: translated = message
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": to_phone.replace("+","").replace(" ",""),
        "type": "text",
        "text": {"body": translated}
    }).encode("utf-8")
    req = urllib.request.Request(WA_API_URL, data=payload, method="POST",
        headers={"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
            result["translated_text"] = translated
            result["original_text"] = message
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"WhatsApp API {e.code}", "detail": body}
    except Exception as e:
        return {"error": str(e)}

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
                            display = wa_name or f"WhatsApp +{sender}"
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
                            cur.execute("""INSERT INTO communications (tenant_id,client_id,comm_type,subject,message_summary,direction,notes,sent_at)
                                VALUES (1,%s,'whatsapp',%s,%s,'inbound',%s,now())""",
                                (client_id, f"WA od {client_name or '+'+sender}", text[:500], f"Phone: +{sender}"))
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
    if not to or not message:
        raise HTTPException(400, "to and message required")
    result = wa_send_message(to, message)
    if "error" in result:
        raise HTTPException(502, result)
    conn = get_db_conn()
    try:
        translated = result.get("translated_text", message)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO communications (tenant_id,client_id,comm_type,subject,message_summary,direction,notes,sent_at)
                VALUES (1,%s,'whatsapp','WA zpráva',%s,'outbound',%s,now())""",
                (client_id, translated[:500], f"To: {to} | Original: {message[:200]}"))
        log_activity(conn,"communication",0,"whatsapp_out",f"WhatsApp na {to}: {translated[:100]}")
        conn.commit()
    finally: release_conn(conn)
    return {"status": "sent", "translated": translated, "original": message}

@app.get("/whatsapp/status")
async def wa_status():
    return {"configured": bool(WA_TOKEN and WA_PHONE_ID), "phone_id": WA_PHONE_ID[:6]+"..." if WA_PHONE_ID else None}

# ========== SYSTEM ==========
@app.get("/")
async def root():
    return {"app":"Secretary DesignLeaf","version":"1.2a","ai_configured":bool(OPENAI_API_KEY),"docs":"/docs"}

if __name__ == "__main__":
    port = int(os.getenv("PORT",8000))
    uvicorn.run(app,host="0.0.0.0",port=port)
