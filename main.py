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

TASKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT DEFAULT 'interni_poznamka',
    status TEXT DEFAULT 'novy',
    priority TEXT DEFAULT 'bezna',
    created_at TIMESTAMPTZ DEFAULT now(),
    deadline TEXT,
    planned_date TEXT,
    time_window_start TEXT,
    time_window_end TEXT,
    estimated_minutes INTEGER,
    actual_minutes INTEGER,
    created_by TEXT,
    assigned_to TEXT,
    delegated_by TEXT,
    client_id BIGINT,
    client_name TEXT,
    job_id BIGINT,
    property_id BIGINT,
    property_address TEXT,
    is_recurring BOOLEAN DEFAULT FALSE,
    recurrence_rule TEXT,
    result TEXT,
    notes JSONB DEFAULT '[]',
    communication_method TEXT,
    source TEXT DEFAULT 'manualne',
    is_billable BOOLEAN DEFAULT FALSE,
    has_cost BOOLEAN DEFAULT FALSE,
    waiting_for_payment BOOLEAN DEFAULT FALSE,
    checklist JSONB DEFAULT '[]',
    is_completed BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT now()
);
"""

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
print(f"OPENAI_API_KEY present: {bool(OPENAI_API_KEY)} (length: {len(OPENAI_API_KEY)})")
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY neni nastaveny!")
ai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def parse_database_config():
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        parsed = urlparse(database_url)
        return {"dbname": parsed.path.lstrip("/"), "user": parsed.username, "password": parsed.password, "host": parsed.hostname, "port": str(parsed.port or 5432)}
    return {"dbname": os.getenv("DB_NAME", "secretary_db"), "user": os.getenv("DB_USER", "postgres"), "password": os.getenv("DB_PASS", ""), "host": os.getenv("DB_HOST", "localhost"), "port": os.getenv("DB_PORT", "5432")}

DB_CONFIG = parse_database_config()
db_pool = None

def init_pool():
    global db_pool
    try:
        db_pool = pool.ThreadedConnectionPool(2, 10, **DB_CONFIG)
        print(f"DB pool OK: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute(TASKS_TABLE_SQL)
            conn.commit()
        db_pool.putconn(conn)
        print("Tasks table ready")
    except Exception as e:
        print(f"DB pool FAIL: {e}")

def get_db_conn():
    if db_pool:
        conn = db_pool.getconn()
    else:
        conn = psycopg2.connect(**DB_CONFIG)
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur:
        cur.execute("SET search_path TO crm, public")
    return conn

def release_conn(conn):
    if db_pool: db_pool.putconn(conn)
    else: conn.close()

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
                    with open(schema_path, "r", encoding="utf-8") as f:
                        cur.execute(f.read())
                    conn.commit()
                    print("Schema initialized")
        release_conn(conn)
    except Exception as e:
        print(f"Schema check: {e}")

@app.on_event("shutdown")
async def shutdown():
    if db_pool: db_pool.closeall()

def db_create_client(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            code = f"CL-{uuid.uuid4().hex[:6].upper()}"
            cur.execute("INSERT INTO clients (client_code, client_type, display_name, email_primary, phone_primary, status) VALUES (%s,%s,%s,%s,%s,'active') RETURNING id",
                (code, data.get("type","domestic"), data["name"], data.get("email"), data.get("phone")))
            cid = cur.fetchone()['id']; conn.commit(); return cid
    except Exception as e: conn.rollback(); print(f"DB: {e}"); return None
    finally: release_conn(conn)

def db_search_clients(query: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            s = f"%{query}%"
            cur.execute("SELECT id, client_code, display_name, email_primary, phone_primary, status, is_commercial FROM clients WHERE deleted_at IS NULL AND (display_name ILIKE %s OR email_primary ILIKE %s OR phone_primary ILIKE %s) ORDER BY display_name LIMIT 20", (s,s,s))
            return cur.fetchall()
    except Exception as e: print(f"Search: {e}"); return []
    finally: release_conn(conn)

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
    internal_language: Optional[str] = None
    external_language: Optional[str] = None

class ImportRequest(BaseModel):
    table: str
    data: List[dict]

@app.post("/process")
async def process_message(msg: MessageRequest):
    if not ai_client:
        return {"reply_cs": "AI neni nakonfigurovana. Nastavte OPENAI_API_KEY v Railway Variables."}
    try:
        now = msg.current_datetime or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] Marek: {msg.text}")
        entity_context = ""
        if msg.context_entity_id and msg.context_type == "client":
            conn = get_db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT display_name, email_primary, phone_primary FROM clients WHERE id = %s", (msg.context_entity_id,))
                    row = cur.fetchone()
                    if row: entity_context = f"Marek se diva na klienta: {row['display_name']}, email: {row.get('email_primary','')}, tel: {row.get('phone_primary','')}"
            finally: release_conn(conn)
        system_prompt = f"""Jsi inteligentni sekretarka firmy DesignLeaf (zahradnicke sluzby, Oxfordshire UK).
AKTUALNI CAS: {now}
KONTEXT: {entity_context or "Zadny."}
KALENDAR: {msg.calendar_context or "Neni k dispozici."}
HISTORIE: {len(msg.history)} zprav.
PRAVIDLA: Odpovez cesky, strucne, lidsky. Pamatuj si celou historii. Pro kontakty search_contacts. Pro kalendar add/modify/delete/list_calendar_event."""
        tools = [
            {"type":"function","function":{"name":"add_calendar_event","description":"Prida schuzku","parameters":{"type":"object","properties":{"title":{"type":"string"},"start_time":{"type":"string"},"duration":{"type":"integer"}},"required":["title","start_time"]}}},
            {"type":"function","function":{"name":"modify_calendar_event","description":"Zmeni udalost","parameters":{"type":"object","properties":{"event_title":{"type":"string"},"new_title":{"type":"string"},"new_start_time":{"type":"string"}},"required":["event_title"]}}},
            {"type":"function","function":{"name":"delete_calendar_event","description":"Smaze udalost","parameters":{"type":"object","properties":{"event_title":{"type":"string"}},"required":["event_title"]}}},
            {"type":"function","function":{"name":"list_calendar_events","description":"Vypise kalendar","parameters":{"type":"object","properties":{"days":{"type":"integer"}},"required":[]}}},
            {"type":"function","function":{"name":"search_contacts","description":"Hleda v CRM i telefonu","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
            {"type":"function","function":{"name":"call_contact","description":"Zavola cislo","parameters":{"type":"object","properties":{"phone":{"type":"string"}},"required":["phone"]}}},
            {"type":"function","function":{"name":"create_client","description":"Novy klient","parameters":{"type":"object","properties":{"name":{"type":"string"},"email":{"type":"string"},"phone":{"type":"string"}},"required":["name"]}}},
            {"type":"function","function":{"name":"create_task","description":"Vytvori novy ukol/task. Pouzij pro vse co se ma udelat, zavolat, objednat, naplanovany atd.","parameters":{"type":"object","properties":{"title":{"type":"string","description":"Nazev ukolu"},"description":{"type":"string","description":"Detailni popis co se ma udelat"},"task_type":{"type":"string","enum":["volat","email","schuzka","zmenit_schuzku","zrusit_schuzku","objednat_material","vytvorit_kalkulaci","poslat_kalkulaci","navsteva_klienta","zamereni","realizace","kontrola","reklamace","pripomenout_se","interni_poznamka","fotodokumentace"],"description":"Typ ukolu"},"priority":{"type":"string","enum":["nizka","bezna","vysoka","urgentni","kriticka"]},"deadline":{"type":"string","description":"Deadline ve formatu YYYY-MM-DD"},"assigned_to":{"type":"string","description":"Komu je prirazen"},"client_name":{"type":"string","description":"Jmeno klienta pokud souvisi"},"status":{"type":"string","enum":["novy","ceka","naplanovany","v_reseni","ceka_na_klienta","ceka_na_material","ceka_na_platbu"]}},"required":["title"]}}},
            {"type":"function","function":{"name":"send_email","description":"Posle email","parameters":{"type":"object","properties":{"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["to","subject","body"]}}},
            {"type":"function","function":{"name":"import_database","description":"Import dat (vyzaduje potvrzeni)","parameters":{"type":"object","properties":{"table":{"type":"string"},"source":{"type":"string"}},"required":["table"]}}}
        ]
        messages = [{"role":"system","content":system_prompt}]
        for h in msg.history[-30:]:
            messages.append({"role":h.role,"content":h.content})
        if not msg.history or msg.history[-1].content != msg.text:
            messages.append({"role":"user","content":msg.text})
        response = ai_client.chat.completions.create(model="gpt-4o", messages=messages, tools=tools)
        ai_msg = response.choices[0].message
        if ai_msg.tool_calls:
            call = ai_msg.tool_calls[0]
            args = json.loads(call.function.arguments)
            action = call.function.name.upper()
            print(f"  TOOL: {call.function.name} -> {args}")
            if action == "CREATE_CLIENT":
                db_create_client(args); return {"reply_cs": f"Hotovo, {args['name']} je v CRM.", "action_type": "REFRESH"}
            if action == "SEARCH_CONTACTS":
                q = args.get("query",""); crm = db_search_clients(q)
                return {"reply_cs": ai_msg.content or f"Hledam '{q}'...", "action_type":"SEARCH_CONTACTS", "action_data":{"query":q,"crm_results":[dict(r) for r in crm]}, "is_question":True}
            if action == "IMPORT_DATABASE":
                return {"reply_cs": ai_msg.content or "Potvrd import.", "action_type":"IMPORT_DATABASE", "action_data":args, "needs_confirmation":True, "is_question":True}
            if action == "CREATE_TASK":
                title = args.get("title", "Ukol")
                return {"reply_cs": f"Vytvořila jsem úkol: {title}.", "action_type":"CREATE_TASK", "action_data":args}
            return {"reply_cs": ai_msg.content or f"Provadim {call.function.name}...", "action_type":action, "action_data":args}
        reply = ai_msg.content or "Rozumim."
        return {"reply_cs": reply, "is_question": "?" in reply}
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"PROCESS ERROR: {error_msg}")
        import traceback; traceback.print_exc()
        return {"reply_cs": f"Chyba: {error_msg}"}

@app.get("/crm/clients")
async def get_clients():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, client_code, display_name, email_primary, phone_primary, status, is_commercial FROM clients WHERE deleted_at IS NULL ORDER BY created_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/clients/search")
async def search_clients(q: str = Query(..., min_length=1)):
    return db_search_clients(q)

@app.get("/crm/clients/{client_id}")
async def get_client_detail(client_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, client_code, display_name, email_primary, phone_primary, status, is_commercial FROM clients WHERE id=%s AND deleted_at IS NULL", (client_id,))
            cl = cur.fetchone()
            if not cl: raise HTTPException(404)
            cur.execute("SELECT id, client_id, property_code, property_name, address_line1, city, postcode, status FROM properties WHERE client_id=%s AND deleted_at IS NULL", (client_id,))
            props = cur.fetchall()
            cur.execute("SELECT id, job_number, job_title, job_status, start_date_planned::text FROM jobs WHERE client_id=%s AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 10", (client_id,))
            jobs = cur.fetchall()
            cur.execute("SELECT id, subject, message_summary, sent_at::text, direction FROM communications WHERE client_id=%s ORDER BY created_at DESC LIMIT 10", (client_id,))
            comms = cur.fetchall()
            return {"client":dict(cl), "properties":[dict(p) for p in props], "recent_jobs":[dict(j) for j in jobs], "communications":[dict(c) for c in comms]}
    finally: release_conn(conn)

@app.post("/crm/clients")
async def api_create_client(data: dict):
    cid = db_create_client(data)
    if cid: return {"id": cid, "status": "success"}
    raise HTTPException(500, "Nelze vytvorit")

@app.get("/crm/properties")
async def get_properties(client_id: Optional[int] = None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if client_id: cur.execute("SELECT id, client_id, property_code, property_name, address_line1, city, postcode, status FROM properties WHERE client_id=%s AND deleted_at IS NULL", (client_id,))
            else: cur.execute("SELECT id, client_id, property_code, property_name, address_line1, city, postcode, status FROM properties WHERE deleted_at IS NULL ORDER BY created_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/jobs")
async def get_jobs(client_id: Optional[int] = None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if client_id: cur.execute("SELECT id, job_number, job_title, job_status, start_date_planned::text FROM jobs WHERE client_id=%s AND deleted_at IS NULL", (client_id,))
            else: cur.execute("SELECT id, job_number, job_title, job_status, start_date_planned::text FROM jobs WHERE deleted_at IS NULL ORDER BY created_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/waste")
async def get_waste():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT wl.id, wt.name as waste_type, wl.quantity, wl.unit, wl.load_date::text FROM waste_loads wl JOIN waste_types wt ON wl.waste_type_id=wt.id ORDER BY wl.load_date DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/invoices")
async def get_invoices():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, invoice_number, client_id, grand_total, status, due_date::text FROM invoices ORDER BY created_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/leads")
async def get_leads():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, lead_code, status, received_at::text FROM leads ORDER BY received_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/import")
async def import_data(req: ImportRequest):
    if req.table not in ("clients","properties","jobs"): raise HTTPException(400)
    conn = get_db_conn(); imported = 0; errors = []
    try:
        with conn.cursor() as cur:
            for i, row in enumerate(req.data):
                try:
                    if req.table == "clients":
                        cur.execute("INSERT INTO clients (client_code,client_type,display_name,email_primary,phone_primary,status) VALUES (%s,%s,%s,%s,%s,'active')",
                            (f"CL-{uuid.uuid4().hex[:6].upper()}", row.get("type","domestic"), row.get("name",row.get("display_name","")), row.get("email",row.get("email_primary")), row.get("phone",row.get("phone_primary"))))
                    imported += 1
                except Exception as e: errors.append(f"Row {i+1}: {e}")
        conn.commit()
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)
    return {"imported": imported, "errors": errors, "total": len(req.data)}

@app.get("/crm/export/csv")
async def export_csv():
    conn = get_db_conn()
    try:
        out = io.StringIO(); cur = conn.cursor()
        cur.execute("SELECT id, client_code, display_name, email_primary, phone_primary, status FROM clients WHERE deleted_at IS NULL ORDER BY display_name")
        rows = cur.fetchall()
        if rows:
            w = csv.DictWriter(out, fieldnames=rows[0].keys()); w.writeheader(); w.writerows([dict(r) for r in rows])
        out.seek(0)
        return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=export_{datetime.now().strftime('%Y%m%d')}.csv"})
    finally: release_conn(conn)

@app.get("/system/settings")
async def get_settings():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM clients WHERE deleted_at IS NULL"); cc = cur.fetchone()['cnt']
            cur.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL"); jc = cur.fetchone()['cnt']
            return {"company_name":"DesignLeaf", "version":"1.0a", "database":"PostgreSQL", "clients_count":cc, "jobs_count":jc, "ai_configured": bool(OPENAI_API_KEY), "environment": os.getenv("RAILWAY_ENVIRONMENT","local")}
    except Exception as e: return {"company_name":"DesignLeaf", "version":"1.0a", "error":str(e)}
    finally: release_conn(conn)

@app.get("/debug/test-ai")
async def test_ai():
    if not ai_client: return {"status":"error", "message":"OPENAI_API_KEY not set", "key_length": len(OPENAI_API_KEY)}
    try:
        r = ai_client.chat.completions.create(model="gpt-4o", messages=[{"role":"user","content":"Rekni ahoj"}], max_tokens=20)
        return {"status":"ok", "response": r.choices[0].message.content, "key_length": len(OPENAI_API_KEY)}
    except Exception as e:
        return {"status":"error", "message": f"{type(e).__name__}: {str(e)}", "key_length": len(OPENAI_API_KEY)}

@app.get("/crm/tasks")
async def get_tasks(status: Optional[str] = None, completed: Optional[bool] = None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM tasks WHERE 1=1"
            params = []
            if status: sql += " AND status = %s"; params.append(status)
            if completed is not None: sql += " AND is_completed = %s"; params.append(completed)
            sql += " ORDER BY created_at DESC"
            cur.execute(sql, params)
            return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/tasks")
async def create_task(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            tid = data.get("id", str(uuid.uuid4()))
            cur.execute("""INSERT INTO tasks (id,title,description,task_type,status,priority,deadline,planned_date,
                time_window_start,time_window_end,estimated_minutes,created_by,assigned_to,delegated_by,
                client_id,client_name,job_id,property_id,property_address,is_recurring,recurrence_rule,
                communication_method,source,is_billable,has_cost,checklist)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (tid, data.get("title",""), data.get("description"), data.get("task_type","interni_poznamka"),
                 data.get("status","novy"), data.get("priority","bezna"), data.get("deadline"),
                 data.get("planned_date"), data.get("time_window_start"), data.get("time_window_end"),
                 data.get("estimated_minutes"), data.get("created_by","Marek"), data.get("assigned_to"),
                 data.get("delegated_by"), data.get("client_id"), data.get("client_name"),
                 data.get("job_id"), data.get("property_id"), data.get("property_address"),
                 data.get("is_recurring",False), data.get("recurrence_rule"),
                 data.get("communication_method"), data.get("source","manualne"),
                 data.get("is_billable",False), data.get("has_cost",False),
                 json.dumps(data.get("checklist",[]))))
            conn.commit()
            return {"id": tid, "status": "created"}
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.put("/crm/tasks/{task_id}")
async def update_task(task_id: str, data: dict):
    conn = get_db_conn()
    try:
        sets = []; vals = []
        for k in ["title","description","task_type","status","priority","deadline","assigned_to","result","is_completed","actual_minutes"]:
            if k in data: sets.append(f"{k} = %s"); vals.append(data[k])
        if "notes" in data: sets.append("notes = %s"); vals.append(json.dumps(data["notes"]))
        if "checklist" in data: sets.append("checklist = %s"); vals.append(json.dumps(data["checklist"]))
        sets.append("updated_at = now()")
        vals.append(task_id)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s", vals)
            conn.commit()
            return {"status": "updated"}
    except Exception as e: conn.rollback(); raise HTTPException(500, str(e))
    finally: release_conn(conn)

@app.delete("/crm/tasks/{task_id}")
async def delete_task(task_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
            conn.commit()
            return {"status": "deleted"}
    finally: release_conn(conn)

@app.get("/health")
async def health():
    try:
        conn = get_db_conn(); release_conn(conn)
        return {"status":"ok", "version":"1.0a", "ai": bool(OPENAI_API_KEY)}
    except: return {"status":"error"}

@app.get("/")
async def root():
    return {"app":"Secretary DesignLeaf", "version":"1.0a", "ai_configured": bool(OPENAI_API_KEY), "docs":"/docs"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
