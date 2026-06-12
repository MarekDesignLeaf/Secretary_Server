"""AI intent detection — fallback when the deterministic parser can't classify.

Maps a free-form utterance to one of the known intents + entities using an LLM,
so any natural phrasing works ("intuitive variations"). Requires OPENAI_API_KEY;
without it returns None (caller keeps the deterministic-only behaviour).

The execute layer caches successful classifications per tenant (learning), so a
phrasing the AI resolved once is recognized instantly next time.
"""
from __future__ import annotations

import json
import os

# Intent catalogue shown to the model: intent -> (description, entity keys).
# Keep in sync with the execution branches in api/routes/voice.py.
INTENT_CATALOGUE: dict[str, tuple[str, list[str]]] = {
    "calendar.create": ("create a meeting/appointment", ["date", "time", "person", "title"]),
    "calendar.list": ("list upcoming meetings", ["date", "range"]),
    "calendar.update": ("move/reschedule a meeting to another day/time", ["person", "date", "time"]),
    "calendar.delete": ("cancel/delete a meeting", ["person", "date"]),
    "calendar.sync": ("sync calendar with Google", []),
    "task.create": ("create a task / to-do", ["title", "person", "date", "time"]),
    "task.list": ("list open tasks", []),
    "task.complete": ("mark a task as done", ["person", "title"]),
    "client.create": ("create a new client/customer", ["name", "phone", "address"]),
    "client.find": ("find a client/contact and read their details", ["query"]),
    "client.set_address": ("set a client's address from their message", ["person"]),
    "contacts.import": ("import phone contacts into the CRM", []),
    "work_report.start": ("start a work report (what was done on a job)", []),
    "job.create": ("create a job/work order for a client", ["title", "person"]),
    "job.list": ("list active jobs", []),
    "job.change_status": ("change a job's status", ["status", "person"]),
    "comm.log": ("log a phone call/email/communication", ["person", "comm_type"]),
    "comm.list": ("show communication history", ["person"]),
    "whatsapp.send": ("send a WhatsApp message", ["person", "message"]),
    "whatsapp.read": ("read incoming WhatsApp messages", ["person"]),
    "weather.get": ("weather forecast", ["date", "place", "week", "hourly"]),
}


def is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def supported() -> set[str]:
    return set(INTENT_CATALOGUE)


def classify(utterance: str, language: str | None = None) -> dict | None:
    """Return {"intent": str, "entities": dict, "confidence": float} or None."""
    if not utterance.strip() or not is_configured():
        return None
    catalogue = "\n".join(
        f"- {name}: {desc} (entities: {', '.join(keys) or 'none'})"
        for name, (desc, keys) in INTENT_CATALOGUE.items())
    system = (
        "You are the intent router for a voice-controlled business assistant. "
        "Map the user's utterance to exactly ONE intent from the list, or null if "
        "none fits. Extract any entities you can. Dates as YYYY-MM-DD, times as "
        "HH:MM (24h) when present; otherwise omit. Reply as JSON: "
        '{"intent": "<name|null>", "entities": {...}, "confidence": 0..1}.')
    user = f"Intents:\n{catalogue}\n\nUtterance: {utterance}"
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_INTENT_MODEL", "gpt-4o-mini"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001 — AI is best-effort
        return None

    intent = data.get("intent")
    if intent not in INTENT_CATALOGUE:
        return None
    if float(data.get("confidence") or 0) < 0.55:
        return None
    entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
    # Normalise date+time into start_at the way the deterministic parser does.
    if entities.get("date"):
        t = entities.get("time")
        entities["start_at"] = (f"{entities['date']}T{t}:00Z" if t
                                else f"{entities['date']}T00:00:00Z")
    return {"intent": intent, "entities": entities,
            "confidence": float(data.get("confidence") or 0)}
