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

app = FastAPI(title="Secretary CRM - DesignLeaf")

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

def log_activity(conn, entity_type, entity_id, action, description):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO activity_timeline (entity_type,entity_id,action,description) VALUES (%s,%s,%s,%s)",
            (entity_type, str(entity_id), action, description))

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
PRAVIDLA: Odpovez cesky, strucne, lidsky. Pamatuj historii. NIKDY nerci 'provadim...' — vzdy odpovez lidsky co jsi udelala."""

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
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO jobs (job_number,job_title,job_status,start_date_planned) VALUES (%s,%s,'nova',%s) RETURNING id",
                            (code,t,args.get("start_date")))
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
                        cur.execute("INSERT INTO leads (lead_code,lead_source,status) VALUES (%s,%s,'new') RETURNING id",(code,args.get("source","jiny")))
                        lid = cur.fetchone()['id']
                        log_activity(conn,"lead",lid,"create",f"Lead '{n}' z {args.get('source','?')}")
                        conn.commit()
                    return {"reply_cs":f"Lead {code} od {n} zaevidován.","action_type":"REFRESH"}
                except Exception as e: conn.rollback(); return {"reply_cs":f"Chyba: {e}"}
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
            cur.execute("SELECT id,client_code,display_name,email_primary,phone_primary,status,is_commercial FROM clients WHERE deleted_at IS NULL ORDER BY display_name")
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/clients/search")
async def search_clients(q: str = Query(..., min_length=1)):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            s = f"%{q}%"
            cur.execute("SELECT id,client_code,display_name,email_primary,phone_primary,status,is_commercial FROM clients WHERE deleted_at IS NULL AND (display_name ILIKE %s OR email_primary ILIKE %s OR phone_primary ILIKE %s OR client_code ILIKE %s) ORDER BY display_name LIMIT 20",(s,s,s,s))
            return cur.fetchall()
    finally: release_conn(conn)

@app.get("/crm/clients/{client_id}")
async def get_client_detail(client_id: int):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,client_code,display_name,email_primary,phone_primary,status,is_commercial FROM clients WHERE id=%s AND deleted_at IS NULL",(client_id,))
            cl = cur.fetchone()
            if not cl: raise HTTPException(404,"Klient nenalezen")
            cur.execute("SELECT id,property_code,property_name,address_line1,city,postcode,status FROM properties WHERE client_id=%s AND deleted_at IS NULL",(client_id,))
            props = cur.fetchall()
            cur.execute("SELECT id,job_number,job_title,job_status,start_date_planned::text FROM jobs WHERE client_id=%s AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 10",(client_id,))
            jobs = cur.fetchall()
            cur.execute("SELECT id,subject,message_summary,sent_at::text,direction FROM communications WHERE client_id=%s ORDER BY created_at DESC LIMIT 10",(client_id,))
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
            cur.execute("INSERT INTO clients (client_code,client_type,display_name,email_primary,phone_primary,status) VALUES (%s,%s,%s,%s,%s,'active') RETURNING id",
                (code,data.get("type","domestic"),data.get("name",""),data.get("email"),data.get("phone")))
            cid = cur.fetchone()['id']
            log_activity(conn,"client",cid,"create",f"Klient {data.get('name','')} vytvoren")
            conn.commit()
        return {"id":cid,"client_code":code,"status":"success"}
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

@app.put("/crm/clients/{client_id}")
async def update_client(client_id: int, data: dict):
    conn = get_db_conn()
    try:
        sets = []; vals = []
        for k in ["display_name","email_primary","phone_primary","status","billing_address_line1","billing_city","billing_postcode"]:
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
            sql = "SELECT id,job_number,job_title,job_status,client_id,start_date_planned::text FROM jobs WHERE deleted_at IS NULL"
            params = []
            if client_id: sql += " AND client_id=%s"; params.append(client_id)
            if status: sql += " AND job_status=%s"; params.append(status)
            sql += " ORDER BY created_at DESC"
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
            cur.execute("SELECT id,lead_code,lead_source,status,received_at::text FROM leads ORDER BY received_at DESC")
            return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/leads")
async def create_lead(data: dict):
    conn = get_db_conn()
    try:
        code = f"LED-{uuid.uuid4().hex[:6].upper()}"
        with conn.cursor() as cur:
            cur.execute("INSERT INTO leads (lead_code,lead_source,status) VALUES (%s,%s,'new') RETURNING id,lead_code,status,received_at::text",
                (code,data.get("source","jiny")))
            lead = dict(cur.fetchone())
            log_activity(conn,"lead",lead['id'],"create",f"Lead {code} vytvoren")
            conn.commit()
        return lead
    except Exception as e: conn.rollback(); raise HTTPException(500,str(e))
    finally: release_conn(conn)

# ========== REST API: INVOICES ==========
@app.get("/crm/invoices")
async def get_invoices():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,invoice_number,client_id,grand_total,status,due_date::text FROM invoices ORDER BY created_at DESC")
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
async def get_communications(client_id: Optional[int]=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if client_id: cur.execute("SELECT id,client_id,subject,message_summary,sent_at::text,direction FROM communications WHERE client_id=%s ORDER BY created_at DESC",(client_id,))
            else: cur.execute("SELECT id,client_id,subject,message_summary,sent_at::text,direction FROM communications ORDER BY created_at DESC LIMIT 50")
            return cur.fetchall()
    finally: release_conn(conn)

@app.post("/crm/communications")
async def log_communication(data: dict):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO communications (client_id,subject,message_summary,direction,sent_at) VALUES (%s,%s,%s,%s,now()) RETURNING id",
                (data.get("client_id"),data.get("subject"),data.get("message",data.get("message_summary","")),data.get("direction","outbound")))
            cid = cur.fetchone()['id']; conn.commit()
        return {"id":cid,"status":"logged"}
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
            return {"company_name":"DesignLeaf","version":"1.1a","database":"PostgreSQL",
                    "clients_count":cc,"jobs_count":jc,"tasks_count":tc,"leads_count":lc,
                    "ai_configured":bool(OPENAI_API_KEY),"environment":os.getenv("RAILWAY_ENVIRONMENT","local")}
    except Exception as e: return {"company_name":"DesignLeaf","version":"1.1a","error":str(e)}
    finally: release_conn(conn)

@app.get("/health")
async def health():
    try:
        conn = get_db_conn(); release_conn(conn)
        return {"status":"ok","version":"1.1a","ai":bool(OPENAI_API_KEY)}
    except: return {"status":"error"}

@app.get("/debug/test-ai")
async def test_ai():
    if not ai_client: return {"status":"error","message":"OPENAI_API_KEY not set"}
    try:
        r = ai_client.chat.completions.create(model="gpt-4o",messages=[{"role":"user","content":"Rekni ahoj"}],max_tokens=20)
        return {"status":"ok","response":r.choices[0].message.content}
    except Exception as e: return {"status":"error","message":str(e)}

@app.get("/")
async def root():
    return {"app":"Secretary DesignLeaf","version":"1.1a","ai_configured":bool(OPENAI_API_KEY),"docs":"/docs"}

if __name__ == "__main__":
    port = int(os.getenv("PORT",8000))
    uvicorn.run(app,host="0.0.0.0",port=port)
