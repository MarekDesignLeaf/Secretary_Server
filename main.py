import os
import json
import uuid
import csv
import io
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
from openai import OpenAI
from datetime import datetime

app = FastAPI(title="Secretary CRM - DesignLeaf")

# ============================================================
# KONFIGURACE Z ENVIRONMENT PROMENNYCH
# Railway nastavi DATABASE_URL automaticky po pridani PostgreSQL
# OPENAI_API_KEY nastavit rucne v Railway Variables
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY neni nastaveny. AI funkce nebudou fungovat.")

ai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def parse_database_config():
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        # Railway format: postgresql://user:pass@host:port/dbname
        parsed = urlparse(database_url)
        return {
            "dbname": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password,
            "host": parsed.hostname,
            "port": str(parsed.port or 5432)
        }
    # Fallback pro lokalni vyvoj
    return {
        "dbname": os.getenv("DB_NAME", "secretary_db"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASS", ""),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432")
    }

DB_CONFIG = parse_database_config()

# Connection pool
db_pool = None

def init_pool():
    global db_pool
    try:
        db_pool = pool.ThreadedConnectionPool(2, 10, **DB_CONFIG)
        print(f"DB pool initialized: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    except Exception as e:
        print(f"DB pool init failed: {e}")

def get_db_conn():
    if db_pool:
        conn = db_pool.getconn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SET search_path TO crm, public")
        cur.close()
        return conn
    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("SET search_path TO crm, public")
    cur.close()
    return conn

def release_conn(conn):
    if db_pool:
        db_pool.putconn(conn)
    else:
        conn.close()

@app.on_event("startup")
async def startup():
    init_pool()
    # Auto-init schema pokud neexistuje
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('crm.clients')")
        if cur.fetchone()['to_regclass'] is None:
            print("Schema 'crm' neexistuje, spoustim inicializaci...")
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(schema_path):
                with open(schema_path, "r", encoding="utf-8") as f:
                    cur.execute(f.read())
                conn.commit()
                print("Schema inicializovano.")
            else:
                print(f"schema.sql nenalezen na {schema_path}")
        cur.close()
        release_conn(conn)
    except Exception as e:
        print(f"Schema check error: {e}")

@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        db_pool.closeall()

# ============================================================
# DB OPERACE
# ============================================================

def db_create_client(data: dict):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
        cur.execute(
            """INSERT INTO clients (client_code, client_type, display_name, email_primary, phone_primary, status)
               VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id""",
            (code, data.get("type", "domestic"), data["name"], data.get("email"), data.get("phone"))
        )
        cid = cur.fetchone()['id']
        conn.commit(); cur.close()
        return cid
    except Exception as e:
        conn.rollback()
        print(f"DB Error: {e}")
        return None
    finally:
        release_conn(conn)

def db_search_clients(query: str):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        s = f"%{query}%"
        cur.execute(
            """SELECT id, client_code, display_name, email_primary, phone_primary, status, is_commercial
               FROM clients WHERE deleted_at IS NULL
               AND (display_name ILIKE %s OR email_primary ILIKE %s OR phone_primary ILIKE %s
                    OR company_name ILIKE %s OR first_name ILIKE %s OR last_name ILIKE %s)
               ORDER BY display_name LIMIT 20""",
            (s, s, s, s, s, s)
        )
        res = cur.fetchall(); cur.close()
        return res
    except Exception as e:
        print(f"Search Error: {e}")
        return []
    finally:
        release_conn(conn)

# ============================================================
# API MODELS
# ============================================================

class ChatMessage(BaseModel):
    role: str
    content: str

class MessageRequest(BaseModel):
    text: str
    history: List[ChatMessage] = []
    context_entity_id: Optional[int] = None
    context_type: Optional[str] = None
    calendar_context: Optional[str] = None
    current_datetime: Optional[str] = None

class ImportRequest(BaseModel):
    table: str
    data: List[dict]

# ============================================================
# AI PROCESS
# ============================================================

@app.post("/process")
async def process_message(msg: MessageRequest):
    if not ai_client:
        return {"reply_cs": "AI neni nakonfigurovana. Nastavte OPENAI_API_KEY."}
    try:
        now = msg.current_datetime or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] Marek: {msg.text}")

        entity_context = ""
        if msg.context_entity_id and msg.context_type == "client":
            conn = get_db_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT display_name, email_primary, phone_primary FROM clients WHERE id = %s", (msg.context_entity_id,))
                row = cur.fetchone()
                if row:
                    entity_context = f"Marek se diva na klienta: {row['display_name']}, email: {row['email_primary']}, tel: {row['phone_primary']}"
                cur.close()
            finally:
                release_conn(conn)

        system_prompt = f"""Jsi inteligentni sekretarka firmy DesignLeaf (zahradnicke sluzby, Oxfordshire UK).
AKTUALNI CAS: {now}
KONTEXT: {entity_context or "Zadny."}
KALENDAR: {msg.calendar_context or "Neni k dispozici."}
HISTORIE: {len(msg.history)} zprav.
PRAVIDLA: Odpovez cesky, strucne, lidsky. Pro kontakty pouzij search_contacts. Pro kalendar add/modify/delete/list_calendar_event."""

        tools = [
            {"type": "function", "function": {"name": "add_calendar_event", "description": "Prida schuzku", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "start_time": {"type": "string"}, "duration": {"type": "integer"}}, "required": ["title", "start_time"]}}},
            {"type": "function", "function": {"name": "modify_calendar_event", "description": "Zmeni udalost", "parameters": {"type": "object", "properties": {"event_title": {"type": "string"}, "new_title": {"type": "string"}, "new_start_time": {"type": "string"}}, "required": ["event_title"]}}},
            {"type": "function", "function": {"name": "delete_calendar_event", "description": "Smaze udalost", "parameters": {"type": "object", "properties": {"event_title": {"type": "string"}}, "required": ["event_title"]}}},
            {"type": "function", "function": {"name": "list_calendar_events", "description": "Vypise kalendar", "parameters": {"type": "object", "properties": {"days": {"type": "integer"}}, "required": []}}},
            {"type": "function", "function": {"name": "search_contacts", "description": "Hleda v CRM i telefonu", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "call_contact", "description": "Zavola cislo", "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]}}},
            {"type": "function", "function": {"name": "create_client", "description": "Novy klient v CRM", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}}, "required": ["name"]}}},
            {"type": "function", "function": {"name": "create_task", "description": "Novy ukol", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "priority": {"type": "string", "enum": ["low","normal","high","urgent"]}}, "required": ["title"]}}},
            {"type": "function", "function": {"name": "send_email", "description": "Posle email", "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to","subject","body"]}}},
            {"type": "function", "function": {"name": "import_database", "description": "Import dat do CRM (vyzaduje potvrzeni)", "parameters": {"type": "object", "properties": {"table": {"type": "string"}, "source": {"type": "string"}}, "required": ["table"]}}}
        ]

        messages = [{"role": "system", "content": system_prompt}]
        for h in msg.history[-30:]:
            messages.append({"role": h.role, "content": h.content})
        if not msg.history or msg.history[-1].content != msg.text:
            messages.append({"role": "user", "content": msg.text})

        response = ai_client.chat.completions.create(model="gpt-4o", messages=messages, tools=tools)
        ai_msg = response.choices[0].message

        if ai_msg.tool_calls:
            call = ai_msg.tool_calls[0]
            args = json.loads(call.function.arguments)
            action = call.function.name.upper()
            print(f"  TOOL: {call.function.name} -> {args}")

            if action == "CREATE_CLIENT":
                db_create_client(args)
                return {"reply_cs": f"Hotovo, {args['name']} je v CRM.", "action_type": "REFRESH"}

            if action == "SEARCH_CONTACTS":
                q = args.get("query", "")
                crm = db_search_clients(q)
                return {"reply_cs": ai_msg.content or f"Hledam '{q}'...", "action_type": "SEARCH_CONTACTS",
                    "action_data": {"query": q, "crm_results": [dict(r) for r in crm]}, "is_question": True}

            if action == "IMPORT_DATABASE":
                return {"reply_cs": ai_msg.content or "Potvrd import na displeji.", "action_type": "IMPORT_DATABASE",
                    "action_data": args, "needs_confirmation": True, "is_question": True}

            return {"reply_cs": ai_msg.content or f"Provadim {call.function.name}...", "action_type": action, "action_data": args}

        reply = ai_msg.content or "Rozumim."
        return {"reply_cs": reply, "is_question": "?" in reply}
    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        return {"reply_cs": "Chyba na serveru."}

# ============================================================
# CRM ENDPOINTS
# ============================================================

@app.get("/crm/clients")
async def get_clients():
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, client_code, display_name, email_primary, phone_primary, status, is_commercial FROM clients WHERE deleted_at IS NULL ORDER BY created_at DESC")
        return cur.fetchall()
    finally:
        release_conn(conn)

@app.get("/crm/clients/search")
async def search_clients(q: str = Query(..., min_length=1)):
    return db_search_clients(q)

@app.get("/crm/clients/{client_id}")
async def get_client_detail(client_id: int):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, client_code, display_name, email_primary, phone_primary, status, is_commercial FROM clients WHERE id = %s AND deleted_at IS NULL", (client_id,))
        cl = cur.fetchone()
        if not cl: raise HTTPException(404, "Klient nenalezen")
        cur.execute("SELECT id, client_id, property_code, property_name, address_line1, city, postcode, status FROM properties WHERE client_id = %s AND deleted_at IS NULL", (client_id,))
        props = cur.fetchall()
        cur.execute("SELECT id, job_number, job_title, job_status, start_date_planned::text FROM jobs WHERE client_id = %s AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 10", (client_id,))
        jobs = cur.fetchall()
        cur.execute("SELECT id, subject, message_summary, sent_at::text, direction FROM communications WHERE client_id = %s ORDER BY created_at DESC LIMIT 10", (client_id,))
        comms = cur.fetchall()
        return {"client": dict(cl), "properties": [dict(p) for p in props], "recent_jobs": [dict(j) for j in jobs], "communications": [dict(c) for c in comms]}
    finally:
        release_conn(conn)

@app.post("/crm/clients")
async def api_create_client(data: dict):
    cid = db_create_client(data)
    if cid: return {"id": cid, "status": "success"}
    raise HTTPException(500, "Nelze vytvorit")

@app.get("/crm/properties")
async def get_properties(client_id: Optional[int] = None):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        if client_id: cur.execute("SELECT id, client_id, property_code, property_name, address_line1, city, postcode, status FROM properties WHERE client_id = %s AND deleted_at IS NULL", (client_id,))
        else: cur.execute("SELECT id, client_id, property_code, property_name, address_line1, city, postcode, status FROM properties WHERE deleted_at IS NULL ORDER BY created_at DESC")
        return cur.fetchall()
    finally:
        release_conn(conn)

@app.get("/crm/jobs")
async def get_jobs(client_id: Optional[int] = None):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        if client_id: cur.execute("SELECT id, job_number, job_title, job_status, start_date_planned::text FROM jobs WHERE client_id = %s AND deleted_at IS NULL", (client_id,))
        else: cur.execute("SELECT id, job_number, job_title, job_status, start_date_planned::text FROM jobs WHERE deleted_at IS NULL ORDER BY created_at DESC")
        return cur.fetchall()
    finally:
        release_conn(conn)

@app.get("/crm/waste")
async def get_waste():
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT wl.id, wt.name as waste_type, wl.quantity, wl.unit, wl.load_date::text FROM waste_loads wl JOIN waste_types wt ON wl.waste_type_id = wt.id ORDER BY wl.load_date DESC")
        return cur.fetchall()
    finally:
        release_conn(conn)

@app.get("/crm/invoices")
async def get_invoices():
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, invoice_number, client_id, grand_total, status, due_date::text FROM invoices ORDER BY created_at DESC")
        return cur.fetchall()
    finally:
        release_conn(conn)

@app.get("/crm/leads")
async def get_leads():
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, lead_code, status, received_at::text FROM leads ORDER BY received_at DESC")
        return cur.fetchall()
    finally:
        release_conn(conn)

# ============================================================
# IMPORT / EXPORT
# ============================================================

@app.post("/crm/import")
async def import_data(req: ImportRequest):
    if req.table not in ("clients", "properties", "jobs"):
        raise HTTPException(400, "Tabulka neni povolena")
    conn = get_db_conn()
    imported = 0; errors = []
    try:
        cur = conn.cursor()
        for i, row in enumerate(req.data):
            try:
                if req.table == "clients":
                    cur.execute("INSERT INTO clients (client_code, client_type, display_name, email_primary, phone_primary, status) VALUES (%s, %s, %s, %s, %s, 'active')",
                        (f"CL-{uuid.uuid4().hex[:6].upper()}", row.get("type", "domestic"), row.get("name", row.get("display_name", "")), row.get("email", row.get("email_primary")), row.get("phone", row.get("phone_primary"))))
                elif req.table == "properties":
                    cur.execute("INSERT INTO properties (client_id, property_code, property_name, property_type, address_line1, city, postcode, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')",
                        (row.get("client_id", 1), f"PR-{uuid.uuid4().hex[:6].upper()}", row.get("property_name", ""), row.get("property_type", "residential"), row.get("address_line1", ""), row.get("city", ""), row.get("postcode", "")))
                elif req.table == "jobs":
                    cur.execute("INSERT INTO jobs (job_number, client_id, property_id, job_title, job_status) VALUES (%s, %s, %s, %s, %s)",
                        (f"JB-{uuid.uuid4().hex[:6].upper()}", row.get("client_id", 1), row.get("property_id", 1), row.get("job_title", row.get("title", "")), row.get("status", "draft")))
                imported += 1
            except Exception as e:
                errors.append(f"Radek {i+1}: {str(e)}")
        conn.commit()
    except Exception as e:
        conn.rollback(); raise HTTPException(500, str(e))
    finally:
        release_conn(conn)
    return {"imported": imported, "errors": errors, "total": len(req.data)}

@app.post("/crm/import/csv")
async def import_csv(file: UploadFile = File(...), table: str = Query("clients")):
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    return await import_data(ImportRequest(table=table, data=[dict(r) for r in reader]))

@app.get("/crm/export/csv")
async def export_csv():
    conn = get_db_conn()
    try:
        out = io.StringIO(); cur = conn.cursor()
        out.write("=== KLIENTI ===\n")
        cur.execute("SELECT id, client_code, display_name, email_primary, phone_primary, status FROM clients WHERE deleted_at IS NULL ORDER BY display_name")
        rows = cur.fetchall()
        if rows:
            w = csv.DictWriter(out, fieldnames=rows[0].keys()); w.writeheader(); w.writerows([dict(r) for r in rows])
        out.write("\n\n=== NEMOVITOSTI ===\n")
        cur.execute("SELECT id, property_code, property_name, address_line1, city, postcode FROM properties WHERE deleted_at IS NULL")
        rows = cur.fetchall()
        if rows:
            w = csv.DictWriter(out, fieldnames=rows[0].keys()); w.writeheader(); w.writerows([dict(r) for r in rows])
        out.write("\n\n=== ZAKAZKY ===\n")
        cur.execute("SELECT id, job_number, job_title, job_status FROM jobs WHERE deleted_at IS NULL")
        rows = cur.fetchall()
        if rows:
            w = csv.DictWriter(out, fieldnames=rows[0].keys()); w.writeheader(); w.writerows([dict(r) for r in rows])
        out.seek(0)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=export_{ts}.csv"})
    finally:
        release_conn(conn)

# ============================================================
# SYSTEM
# ============================================================

@app.get("/system/settings")
async def get_settings():
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL")
        cc = cur.fetchone()['cnt']
        cur.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL")
        jc = cur.fetchone()['cnt']
        return {"company_name": "DesignLeaf", "version": "1.0a", "database": "PostgreSQL", "clients_count": cc, "jobs_count": jc, "environment": os.getenv("RAILWAY_ENVIRONMENT", "local")}
    except Exception as e:
        return {"company_name": "DesignLeaf", "version": "1.0a", "error": str(e)}
    finally:
        release_conn(conn)

@app.get("/health")
async def health():
    try:
        conn = get_db_conn(); release_conn(conn)
        return {"status": "ok", "version": "1.0a"}
    except:
        return {"status": "error"}

@app.get("/")
async def root():
    return {"app": "Secretary DesignLeaf", "version": "1.0a", "docs": "/docs"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
