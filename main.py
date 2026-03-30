import os
import json
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List, Any
import uvicorn
from openai import OpenAI
from datetime import datetime

app = FastAPI(title="Secretary CRM - MARK SIMA")

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY)

DB_CONFIG = {
    "dbname": "secretary_db",
    "user": "postgres",
    "password": "",
    "host": "localhost",
    "port": "5432"
}

def get_db_conn():
    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("SET search_path TO crm, public")
    cur.close()
    return conn

# --- CRM OPERACE ---

def db_create_client(data: dict):
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        code = f"CL-{uuid.uuid4().hex[:6].upper()}"
        cur.execute(
            """INSERT INTO clients (client_code, client_type, display_name, email_primary, phone_primary, status)
               VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id""",
            (code, data.get("type", "domestic"), data["name"], data.get("email"), data.get("phone"))
        )
        cid = cur.fetchone()['id']
        conn.commit(); cur.close(); conn.close()
        return cid
    except Exception as e:
        print(f"DB Error: {e}")
        return None

# --- API MODELS ---

class ChatMessage(BaseModel):
    role: str
    content: str

class MessageRequest(BaseModel):
    text: str
    history: List[ChatMessage] = []
    context_entity_id: Optional[int] = None
    context_type: Optional[str] = None
    calendar_context: Optional[str] = None

# --- ENDPOINTS ---

@app.post("/process")
async def process_message(msg: MessageRequest):
    try:
        print(f"Marek: {msg.text}")

        system_prompt = f"""Jsi inteligentní sekretářka firmy MARK SIMA.
        Máš 3-vrstvou paměť a plný přístup k telefonu i CRM.

        TVÁ PAMĚŤ:
        1. Krátkodobá: Celá historie konverzace (již proběhlo: {len(msg.history)} zpráv).
        2. Kontextová: Marek se dívá na {msg.context_type} ID {msg.context_entity_id}.
        3. Dlouhodobá: PostgreSQL CRM (91 tabulek).

        PRAVIDLA:
        - Mluv lidsky, žádné robotické názvy.
        - Pokud hledáš v kontaktech, použij tool 'search_contacts'.
        - Pokud najdeš číslo, nabídni volání přes 'call_contact'.
        - Pokud Marek odpoví na tvou otázku, pochop to z historie."""

        tools = [
            {"type": "function", "function": {"name": "add_calendar_event", "description": "Schůzka do kalendáře", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "start_time": {"type": "string"}}, "required": ["title", "start_time"]}}},
            {"type": "function", "function": {"name": "search_contacts", "description": "Hledá v kontaktech mobilu", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "call_contact", "description": "Vytočí telefonní číslo", "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]}}},
            {"type": "function", "function": {"name": "create_client", "description": "Vytvoří klienta v CRM", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}}, "required": ["name"]}}}
        ]

        messages = [{"role": "system", "content": system_prompt}]
        for h in msg.history[-10:]: messages.append({"role": h.role, "content": h.content})
        if not msg.history or msg.history[-1].content != msg.text: messages.append({"role": "user", "content": msg.text})

        response = client.chat.completions.create(model="gpt-4o", messages=messages, tools=tools)
        ai_msg = response.choices[0].message

        if ai_msg.tool_calls:
            call = ai_msg.tool_calls[0]
            args = json.loads(call.function.arguments)
            action_name = call.function.name.upper()

            if action_name == "CREATE_CLIENT":
                db_create_client(args)
                return {"reply_cs": f"Hotovo, klient {args['name']} je v CRM.", "action_type": "REFRESH"}

            human_replies = {
                "SEARCH_CONTACTS": f"Hned se podívám na '{args.get('query')}' v tvých kontaktech...",
                "ADD_CALENDAR_EVENT": f"Píšu ti {args.get('title')} do kalendáře.",
                "CALL_CONTACT": f"Vytáčím číslo {args.get('phone')}..."
            }

            return {
                "reply_cs": human_replies.get(action_name, ai_msg.content or "Momentík..."),
                "action_type": action_name,
                "action_data": args,
                "is_question": action_name == "SEARCH_CONTACTS"
            }

        reply = ai_msg.content or "Rozumím."
        return {"reply_cs": reply, "is_question": "?" in reply}

    except Exception as e:
        print(f"Error: {e}"); return {"reply_cs": "Chyba na serveru."}

@app.get("/crm/clients")
async def get_clients():
    conn = get_db_conn(); cur = conn.cursor()
    cur.execute("SELECT id, display_name, email_primary, phone_primary, status FROM clients WHERE deleted_at IS NULL ORDER BY created_at DESC")
    res = cur.fetchall(); cur.close(); conn.close()
    return res

@app.post("/crm/clients")
async def api_create_client(data: dict):
    cid = db_create_client(data)
    return {"id": cid, "status": "success"}

@app.get("/system/settings")
async def get_settings():
    return {
        "company_name": "MARK SIMA",
        "database": "PostgreSQL Online",
        "active_modules": 91,
        "version": "2.0.1 PRO"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


