import os, json, uuid, csv, io
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
from openai import OpenAI
from datetime import datetime

app = FastAPI(title="Secretary CRM - DesignLeaf v1.2a")

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
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
"""

# === CONFIG ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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
        db_pool = pool.ThreadedConnectionPool(2, 10, **DB_CONFIG)
        print(f"DB pool OK: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SET search_path TO crm, public")
            cur.execute(EXTRA_TABLES_SQL)
            conn.commit()
        db_pool.putconn(conn)
        print("Extra tables ready")
    except Exception as e: print(f"DB pool FAIL: {e}")

def get_db_conn():
    conn = db_pool.getconn() if db_pool else psycopg2.connect(**DB_CONFIG)
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur: cur.execute("SET search_path TO crm, public")
    return conn

def release_conn(conn):
    if db_pool: db_pool.putconn(conn)
    else: conn.close()

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

        system_prompt = f"""Jsi inteligentni sekretarka firmy DesignLeaf (zahradnicke sluzby, Oxfordshire UK).
CAS: {now}. KONTEXT: {entity_ctx or 'Zadny.'}
KALENDAR: {msg.calendar_context or 'Neni.'}
PRAVIDLA:
- Odpovez cesky, strucne, lidsky. Pamatuj historii.
- NIKDY nerci 'provadim...' — vzdy odpovez lidsky co jsi udelala.
- Pro vytvoreni ukolu pouzij create_task. Pro zmenu stavu update_task. Pro dokonceni complete_task.
- Pro seznam ukolu pouzij list_tasks.
- Pro zakazky: create_job pro novou, update_job pro zmenu stavu.
- Pro poznamky: add_note s entity_type 'client' nebo 'job'.
- Pro leady: create_lead.
- Pro kalendar: list_calendar_events, add/modify/delete_calendar_event.
- Pro kontakty: search_contacts, call_contact.
- Kdyz se Marek zepta 'co mam delat' nebo 'jake mam ukoly', pouzij list_tasks.
- Kdyz rekne 'hotovo' nebo 'splneno' u ukolu, pouzij complete_task.
- Kdyz rekne 'work report', 'zapsat praci', 'zadat hodiny', 'report prace', 'nahlasit praci', pouzij start_work_report."""

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
async def get_clients():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clients WHERE deleted_at IS NULL ORDER BY display_name")
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
async def api_create_client(data: dict):
    conn = get_db_conn()
    try:
        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO clients (client_code,client_type,title,first_name,last_name,display_name,
                company_name,company_registration_no,vat_no,email_primary,email_secondary,
                phone_primary,phone_secondary,website,preferred_contact_method,
                billing_address_line1,billing_city,billing_postcode,billing_country,
                status,is_commercial,tenant_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,1) RETURNING id""",
                (code,data.get("type",data.get("client_type","domestic")),
                 data.get("title"),data.get("first_name"),data.get("last_name"),
                 data.get("name",data.get("display_name","")),
                 data.get("company_name"),data.get("company_registration_no"),data.get("vat_no"),
                 data.get("email",data.get("email_primary")),data.get("email_secondary"),
                 data.get("phone",data.get("phone_primary")),data.get("phone_secondary"),
                 data.get("website"),data.get("preferred_contact_method","email"),
                 data.get("billing_address_line1"),data.get("billing_city"),
                 data.get("billing_postcode"),data.get("billing_country","GB"),
                 data.get("is_commercial",False)))
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
async def get_jobs(client_id: Optional[int] = None, status: Optional[str] = None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT j.id,j.job_number,j.job_title,j.job_status,j.client_id,j.property_id,j.quote_id,j.start_date_planned::text,j.created_at::text,j.updated_at::text,c.display_name as client_name FROM jobs j LEFT JOIN clients c ON j.client_id=c.id WHERE j.deleted_at IS NULL"
            params = []
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
async def create_job(data: dict):
    conn = get_db_conn()
    try:
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
        sets = []; vals = []
        for k in ["job_title","job_status","start_date_planned"]:
            if k in data: sets.append(f"{k}=%s"); vals.append(data[k])
        if not sets: raise HTTPException(400)
        sets.append("updated_at=now()"); vals.append(job_id)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=%s AND deleted_at IS NULL",vals)
            log_activity(conn,"job",job_id,"update",f"Zakazka upravena: {list(data.keys())}")
            conn.commit()
        return {"status":"updated"}
    except HTTPException: raise
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== REST API: TASKS ==========
@app.get("/crm/tasks")
async def get_tasks(status: Optional[str]=None, client_id: Optional[int]=None, job_id: Optional[int]=None, completed: Optional[bool]=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM tasks WHERE 1=1"; params = []
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
async def get_leads():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,lead_code,lead_source,contact_name,contact_email,contact_phone,description,notes,status,client_id,job_id,received_at::text,updated_at::text FROM leads ORDER BY received_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/leads")
async def create_lead(data: dict):
    conn = get_db_conn()
    try:
        code = f"LED-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO leads (lead_code,lead_source,status,contact_name,contact_email,contact_phone,description)
                VALUES (%s,%s,'new',%s,%s,%s,%s) RETURNING id,lead_code,status,received_at::text""",
                (code,data.get("source","jiny"),data.get("name",data.get("contact_name")),
                 data.get("email",data.get("contact_email")),data.get("phone",data.get("contact_phone")),
                 data.get("description")))
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
        sets = []; vals = []
        for k in ["status","lead_source","contact_name","contact_email","contact_phone","description","notes"]:
            if k in data: sets.append(f"{k}=%s"); vals.append(data[k])
        if not sets: raise HTTPException(400)
        sets.append("updated_at=now()"); vals.append(lead_id)
        with conn.cursor() as cur:
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
async def get_invoices():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT i.id,i.invoice_number,i.client_id,c.display_name as client_name,i.grand_total,i.status,i.due_date::text,i.created_at::text FROM invoices i LEFT JOIN clients c ON i.client_id=c.id ORDER BY i.created_at DESC")
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

# ========== REST API: COMMUNICATIONS ==========
@app.get("/crm/communications")
async def get_communications(client_id: Optional[int]=None, job_id: Optional[int]=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id,client_id,job_id,comm_type,subject,message_summary,sent_at::text,direction,notes,created_at::text FROM communications WHERE 1=1"
            params = []
            if client_id: sql += " AND client_id=%s"; params.append(client_id)
            if job_id: sql += " AND job_id=%s"; params.append(job_id)
            sql += " ORDER BY created_at DESC LIMIT 50"
            cur.execute(sql, params); return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/communications")
async def log_communication(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO communications (client_id,job_id,comm_type,subject,message_summary,direction,notes,sent_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,now()) RETURNING id,comm_type,subject,direction""",
                (data.get("client_id"),data.get("job_id"),data.get("comm_type","telefon"),
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
async def get_photos(entity_type: Optional[str]=None, entity_id: Optional[str]=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id,entity_type,entity_id,filename,description,created_by,created_at::text FROM photos WHERE 1=1"
            params = []
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
            cur.execute("""INSERT INTO photos (entity_type,entity_id,filename,description,file_path,created_by)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,filename,created_at::text""",
                (data.get("entity_type","job"),data.get("entity_id","0"),data.get("filename","photo.jpg"),
                 data.get("description"),data.get("file_path"),data.get("created_by","Marek")))
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


@app.post("/debug/seed-admin")
async def seed_admin():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO roles (role_name,description) VALUES ('admin','Full access') ON CONFLICT (role_name) DO NOTHING")
            cur.execute("SELECT id FROM roles WHERE role_name='admin'")
            rid = cur.fetchone()['id']
            cur.execute("""INSERT INTO users (tenant_id,role_id,first_name,last_name,display_name,email,phone,status,password_hash)
                VALUES (1,%s,'Marek','Sima','Marek Sima','marek@designleaf.co.uk','+44 7XXX','active','not_set')
                ON CONFLICT (email) DO UPDATE SET display_name='Marek Sima',role_id=%s RETURNING id,display_name,email""",(rid,rid))
            user = dict(cur.fetchone())
            conn.commit()
            return {"status":"ok","user":user}
    except Exception as e:
        conn.rollback()
        return {"status":"error","message":str(e)}
    finally: release_conn(conn)
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
DIALOG_STEPS = ["client","workers","total_hours","entries","validate_hours","materials","waste","notes","summary","confirm"]
VALID_TRANSITIONS = {
    "client": ["client","workers"],
    "workers": ["workers","total_hours"],
    "total_hours": ["total_hours","entries"],
    "entries": ["entries","validate_hours","materials"],
    "validate_hours": ["validate_hours","materials","entries"],
    "materials": ["materials","waste"],
    "waste": ["waste","notes"],
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
    "entries": {"en":"How many hours for each type of work? (e.g. pruning 4, maintenance 2)","cs":"Kolik hodin na každý typ práce? (např. prořez 4, údržba 2)","pl":"Ile godzin na każdy rodzaj pracy?"},
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
        lang = data.get("language","en")
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
    conn.autocommit = False
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
                    hrs = float(text.replace(",",".").replace("hours","").replace("hodin","").replace("godzin","").strip())
                    ctx["total_hours"] = hrs
                    # Distribute equally if multiple workers
                    wc = len(ctx.get("workers",[]))
                    if wc > 0:
                        per = round(hrs / wc, 2)
                        for w in ctx["workers"]: w["hours"] = per; w["total"] = round(per * w["rate"],2)
                    next_step = "entries"; reply = f"{hrs}h. {get_prompt('entries',lang)}"
                except: reply = "Invalid number." if lang=="en" else "Neplatné číslo." if lang=="cs" else "Nieprawidłowa liczba."

            # === STEP: ENTRIES (work breakdown) ===
            elif step == "entries":
                import re
                entries = []
                parts = re.findall(r'(\w[\w\s]*?)\s+([\d.,]+)', text)
                for ptype, phrs in parts:
                    h = float(phrs.replace(",","."))
                    rate = resolve_rate(conn,tenant_id,"task_rate",rule_key=ptype.strip().lower(),job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                    entries.append({"type":ptype.strip(),"hours":h,"rate":rate,"total":round(h*rate,2)})
                if not entries and text.lower() not in ("no","ne","nie","skip"):
                    entries.append({"type":"general","hours":ctx.get("total_hours",0),"rate":resolve_rate(conn,tenant_id,"task_rate",job_id=ctx.get("job_id"),client_id=ctx.get("client_id")),"total":0})
                    entries[0]["total"] = round(entries[0]["hours"]*entries[0]["rate"],2)
                ctx["entries"] = entries
                # Validate hours
                entry_sum = sum(e["hours"] for e in entries)
                total = ctx.get("total_hours",0)
                if abs(entry_sum - total) > 0.01 and entries:
                    next_step = "validate_hours"
                    reply = f"Entries sum={entry_sum}h but total={total}h. " + get_prompt("validate_hours",lang)
                else:
                    next_step = "materials"; reply = get_prompt("materials",lang)

            # === STEP: VALIDATE HOURS ===
            elif step == "validate_hours":
                if "total" in text.lower() or "celkem" in text.lower() or "suma" in text.lower():
                    ctx["total_hours"] = sum(e["hours"] for e in ctx.get("entries",[]))
                next_step = "materials"; reply = get_prompt("materials",lang)

            # === STEP: MATERIALS ===
            elif step == "materials":
                if text.lower() in ("no","ne","nie","none","zadny","żaden","skip"):
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
                next_step = "waste"; reply = get_prompt("waste",lang)

            # === STEP: WASTE ===
            elif step == "waste":
                if text.lower() in ("no","ne","nie","none","zadny","żaden","0","skip"):
                    ctx["waste"] = {"qty":0,"rate":0,"total":0}
                else:
                    try:
                        qty = float(text.replace(",",".").split()[0])
                        rate = resolve_rate(conn,tenant_id,"waste_rate",job_id=ctx.get("job_id"),client_id=ctx.get("client_id"))
                        ctx["waste"] = {"qty":qty,"rate":rate,"total":round(qty*rate,2)}
                    except: ctx["waste"] = {"qty":0,"rate":0,"total":0}
                next_step = "notes"; reply = get_prompt("notes",lang)

            # === STEP: NOTES ===
            elif step == "notes":
                if text.lower() not in ("no","ne","nie","skip",""):
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
                if low in ("confirm","potvrdit","potwierdź","yes","ano","tak"):
                    next_step = "confirm"
                elif low.startswith("edit") or low.startswith("oprav") or low.startswith("popraw"):
                    # Go back to the mentioned step
                    for s in ["client","workers","total_hours","entries","materials","waste","notes"]:
                        if s in low or (s=="total_hours" and ("hour" in low or "hodin" in low or "godzin" in low)):
                            next_step = s; reply = get_prompt(s,lang); break
                    else:
                        reply = "What to edit? (client/workers/hours/entries/materials/waste/notes)"
                else:
                    reply = "Say 'confirm' to save or 'edit [field]'." if lang=="en" else "Řekni 'potvrdit' nebo 'oprav [pole]'."

            # === STEP: CONFIRM → save to DB ===
            if next_step == "confirm" and step != "confirm":
                # VALIDATION: block save without workers or entries
                if not ctx.get("workers"):
                    next_step = "workers"; reply = "Cannot save without workers. " + get_prompt("workers",lang)
                elif not ctx.get("entries"):
                    next_step = "entries"; reply = "Cannot save without work entries. " + get_prompt("entries",lang)
                elif abs(sum(e["hours"] for e in ctx.get("entries",[])) - ctx.get("total_hours",0)) > 0.01:
                    next_step = "validate_hours"; reply = "Hours mismatch. " + get_prompt("validate_hours",lang)
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

# ========== WORK REPORT CRUD ==========
@app.get("/work-reports")
async def list_work_reports(tenant_id: int=1, client_id: Optional[int]=None, status: Optional[str]=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT wr.*,c.display_name as client_name FROM work_reports wr LEFT JOIN clients c ON wr.client_id=c.id WHERE wr.tenant_id=%s"
            params = [tenant_id]
            if client_id: sql += " AND wr.client_id=%s"; params.append(client_id)
            if status: sql += " AND wr.status=%s"; params.append(status)
            sql += " ORDER BY wr.work_date DESC"
            cur.execute(sql,params); return [dict(r) for r in cur.fetchall()]
    finally: release_conn(conn)

@app.get("/work-reports/{report_id}")
async def get_work_report(report_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT wr.*,c.display_name as client_name FROM work_reports wr LEFT JOIN clients c ON wr.client_id=c.id WHERE wr.id=%s",(report_id,))
            rpt = cur.fetchone()
            if not rpt: raise HTTPException(404)
            cur.execute("SELECT * FROM work_report_workers WHERE work_report_id=%s",(report_id,))
            workers = cur.fetchall()
            cur.execute("SELECT * FROM work_report_entries WHERE work_report_id=%s",(report_id,))
            entries = cur.fetchall()
            cur.execute("SELECT * FROM work_report_materials WHERE work_report_id=%s",(report_id,))
            materials = cur.fetchall()
            cur.execute("SELECT * FROM work_report_waste WHERE work_report_id=%s",(report_id,))
            waste = cur.fetchall()
            return {"report":dict(rpt),"workers":[dict(r) for r in workers],"entries":[dict(r) for r in entries],"materials":[dict(r) for r in materials],"waste":[dict(r) for r in waste]}
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

# ========== SYSTEM ==========
@app.get("/")
async def root():
    return {"app":"Secretary DesignLeaf","version":"1.2a","ai_configured":bool(OPENAI_API_KEY),"docs":"/docs"}

if __name__ == "__main__":
    port = int(os.getenv("PORT",8000))
    uvicorn.run(app,host="0.0.0.0",port=port)
