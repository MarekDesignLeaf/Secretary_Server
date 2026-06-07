"""Voice intent parsing (Phase A5).

Pure, testable logic that turns a free-text utterance (Czech or English) into a
structured intent + extracted entities. NO HTTP, NO AI — deterministic rules.

The HTTP layer (api/routes/voice.py) calls parse_intent() and then executes the
resolved intent against the repository. This module does not decide permissions
or perform side effects.

Supported intent families:
  calendar.list      — "what do I have tomorrow / this afternoon / next"
  calendar.create    — "create meeting tomorrow at 10 with John"
  calendar.update    — "move meeting with John to Friday at 14"
  calendar.delete    — "cancel tomorrow's meeting with John"
  task.create        — "create task for Daniel ..."
  client.create      — "create client John Smith"
  work_report.start  — "create work report" (hand off to voice session flow)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, time


@dataclass
class ParsedIntent:
    intent: str | None                  # e.g. "calendar.create" or None
    confidence: float
    entities: dict = field(default_factory=dict)
    requires_confirmation: bool = False
    reason: str = ""


# ── weekday maps (en + cs) ────────────────────────────────────────────────────
_WEEKDAYS = {
    "monday": 0, "mon": 0, "pondělí": 0, "pondeli": 0,
    "tuesday": 1, "tue": 1, "úterý": 1, "utery": 1,
    "wednesday": 2, "wed": 2, "středa": 2, "streda": 2,
    "thursday": 3, "thu": 3, "čtvrtek": 3, "ctvrtek": 3,
    "friday": 4, "fri": 4, "pátek": 4, "patek": 4,
    "saturday": 5, "sat": 5, "sobota": 5,
    "sunday": 6, "sun": 6, "neděle": 6, "nedele": 6,
}


def _now(tz=timezone.utc) -> datetime:
    return datetime.now(tz)


def parse_date(text: str, base: datetime | None = None) -> str | None:
    """Return ISO date (YYYY-MM-DD) for relative/absolute date phrases, else None."""
    base = base or _now()
    today = base.date()
    low = text.strip().lower()

    if any(w in low for w in ("today", "dnes", "dzisiaj")):
        return today.isoformat()
    if any(w in low for w in ("tomorrow", "zítra", "zitra", "jutro")):
        return (today + timedelta(days=1)).isoformat()
    if any(w in low for w in ("yesterday", "včera", "vcera")):
        return (today - timedelta(days=1)).isoformat()
    if "day after tomorrow" in low or "pozítří" in low or "pozitri" in low:
        return (today + timedelta(days=2)).isoformat()

    # weekday name → next occurrence (incl. today+7 if same weekday)
    for name, wd in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", low):
            delta = (wd - today.weekday()) % 7
            delta = delta or 7  # "friday" means the upcoming friday, not today
            return (today + timedelta(days=delta)).isoformat()

    # ISO 2026-06-01
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # European 1.6.2026 / 01/06/2026
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def parse_time(text: str) -> str | None:
    """Return HH:MM (24h) for a time phrase, else None."""
    low = text.strip().lower()
    # explicit HH:MM
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", low)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return f"{h:02d}:{mn:02d}"
    # "at 10", "v 10", "at 2pm", "ve 14"
    m = re.search(r"\b(?:at|v|ve|o)\s+(\d{1,2})\s*(am|pm)?\b", low)
    if m:
        h = int(m.group(1))
        ap = m.group(2)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return f"{h:02d}:00"
    # bare "2pm" / "10am"
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", low)
    if m:
        h = int(m.group(1))
        if m.group(2) == "pm" and h < 12:
            h += 12
        if m.group(2) == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    return None


def time_window(text: str) -> tuple[str, str] | None:
    """Return (start_hh:mm, end_hh:mm) for a part-of-day phrase, else None."""
    low = text.lower()
    if any(w in low for w in ("morning", "ráno", "rano", "dopoledne")):
        return ("06:00", "12:00")
    if any(w in low for w in ("afternoon", "odpoledne", "odpo")):
        return ("12:00", "18:00")
    if any(w in low for w in ("evening", "večer", "vecer")):
        return ("18:00", "23:59")
    return None


def extract_person(text: str) -> str | None:
    """Extract a person name after 'with' / 's' / 'se' / 'for' / 'pro'."""
    # "with John", "s Johnem", "for Daniel", "pro Daniela"
    m = re.search(r"\b(?:with|for|pro|se|s)\s+([A-ZÁ-Ž][\wá-ž]+(?:\s+[A-ZÁ-Ž][\wá-ž]+)?)", text)
    if m:
        return m.group(1).strip()
    return None


def _combine(date_iso: str, hhmm: str | None) -> str:
    """Combine date + optional time into ISO 8601 UTC string."""
    if hhmm:
        return f"{date_iso}T{hhmm}:00Z"
    return f"{date_iso}T00:00:00Z"


# ── intent keyword sets ───────────────────────────────────────────────────────
_LIST_WORDS = ("what do i have", "what's on", "whats on", "co mám", "co mam",
               "what is next", "next in my calendar", "co je dál", "co je dal",
               "show my calendar", "my schedule", "můj kalendář", "muj kalendar", "co je zitra", "co je zítra", "co je na zitrek", "co je na zítřek", "co mam zitra", "co mám zítra", "co je v kalendari", "co je v kalendář", "v kalendari", "v kalendář", "mam dnes", "mám dnes", "co je dnes", "co mam na", "co mám na", "schuzky", "schůzky")
_CREATE_CAL = ("create meeting", "create appointment", "new meeting", "schedule",
               "vytvoř schůzku", "vytvor schuzku", "nová schůzka", "nova schuzka",
               "naplánuj", "naplanuj", "create event", "add event", "přidej událost", "přidej schůzku", "pridej schuzku", "přidej schuzku", "pridej schůzku", "přidej termín", "pridej termin", "domluv schůzku", "domluv schuzku", "zapiš schůzku", "zapis schuzku")
_UPDATE_CAL = ("move", "reschedule", "change", "přesuň", "presun", "přesunout",
               "změň termín", "zmen termin", "reschedule")
_SYNC_CAL = ("synchronizuj kalendar", "synchronizuj kalendář", "sesynchronizuj kalendar",
             "sesynchronizuj kalendář", "synchronizace kalendare", "synchronizuj google",
             "sync kalendar", "sync calendar", "synchronize calendar", "aktualizuj kalendar",
             "synchronizuj s googlem", "nahraj do kalendare", "synchronizuj udalosti")
_DELETE_CAL = ("cancel", "delete appointment", "delete meeting", "remove meeting",
               "zruš", "zrus", "smaž schůzku", "smaz schuzku", "odstraň událost",
               "odstran udalost", "zrus schuzku", "zruš schůzku", "zrus termin",
               "zruš termín", "vymaz schuzku", "vymaž schůzku", "odvolej schuzku",
               "odvolej schůzku", "smaz termin", "smaž termín")
_CREATE_TASK = ("create task", "new task", "add task", "vytvoř úkol", "vytvor ukol",
                "nový úkol", "novy ukol", "přidej úkol", "pridej ukol", "přidej úkol",
                "zaloz ukol", "založ úkol", "zapiš úkol", "zapis ukol", "udelej ukol",
                "udělej úkol", "novy task", "pridej task", "dej ukol", "zadej ukol",
                "zadej úkol", "novy pozadavek", "vytvoř task")
_CREATE_CLIENT = ("create client", "new client", "add client", "register client",
                  "vytvoř klienta", "vytvor klienta", "nový klient", "novy klient",
                  "novy zakaznik", "nový zákazník", "přidej klienta", "pridej klienta",
                  "zaevidovat klienta", "zaeviduj klienta", "zaloz klienta", "založ klienta",
                  "zapiš klienta", "zapis klienta", "novy kontakt klienta")
_CREATE_WR = ("create work report", "new work report", "work report",
              "vytvoř report", "vytvor report", "pracovní výkaz", "pracovni vykaz")
_NEXT_WORDS = ("next", "dál", "dal", "nejbliž", "nejbliz")


def _has(low: str, words) -> bool:
    return any(w in low for w in words)


def parse_intent(utterance: str, base: datetime | None = None) -> ParsedIntent:
    """Main entry point. Deterministically classify an utterance."""
    low = " ".join(utterance.lower().split())

    # ----- CALENDAR SYNC (Google) - check first, very specific -----
    if _has(low, _SYNC_CAL):
        return ParsedIntent(
            intent="calendar.sync",
            confidence=0.9,
            entities={},
            requires_confirmation=False,
            reason="Voice-triggered Google Calendar sync.",
        )

    # ----- CALENDAR DELETE (check before create/update) -----
    if _has(low, _DELETE_CAL):
        date_iso = parse_date(low, base)
        person = extract_person(utterance)
        return ParsedIntent(
            intent="calendar.delete",
            confidence=0.8,
            entities={"date": date_iso, "person": person},
            requires_confirmation=True,
            reason="Calendar cancellation is destructive; confirmation required.",
        )

    # ----- CALENDAR UPDATE -----
    if _has(low, _UPDATE_CAL) and (_has(low, ("meeting", "schůz", "schuz", "appointment", "event", "událost", "udalost")) or extract_person(utterance)):
        date_iso = parse_date(low, base)
        hhmm = parse_time(low)
        person = extract_person(utterance)
        return ParsedIntent(
            intent="calendar.update",
            confidence=0.75,
            entities={"date": date_iso, "time": hhmm, "person": person,
                      "new_start": _combine(date_iso, hhmm) if date_iso else None},
            requires_confirmation=True,
            reason="Calendar modification; confirmation required.",
        )

    # ----- CALENDAR CREATE -----
    if _has(low, _CREATE_CAL):
        date_iso = parse_date(low, base)
        hhmm = parse_time(low)
        person = extract_person(utterance)
        # No generic default title — leave title empty so the slot-filler asks
        # "with whom / what name" when neither person nor explicit title is given.
        title = None
        if person:
            title = f"Schuzka s {person}"
        start = _combine(date_iso, hhmm) if date_iso else None
        return ParsedIntent(
            intent="calendar.create",
            confidence=0.8 if start else 0.5,
            entities={"date": date_iso, "time": hhmm, "person": person,
                      "title": title, "start_at": start},
            requires_confirmation=True,
            reason="Calendar creation; confirmation required.",
        )

    # ----- CALENDAR LIST -----
    if _has(low, _LIST_WORDS) or (_has(low, _NEXT_WORDS) and ("calendar" in low or "kalendář" in low or "kalendar" in low)):
        date_iso = parse_date(low, base)
        win = time_window(low)
        is_next = _has(low, _NEXT_WORDS)
        return ParsedIntent(
            intent="calendar.list",
            confidence=0.8,
            entities={"date": date_iso, "window": win, "next": is_next},
            requires_confirmation=False,
            reason="Read-only calendar query.",
        )

    # ----- TASK CREATE -----
    if _has(low, _CREATE_TASK):
        person = extract_person(utterance)
        return ParsedIntent(
            intent="task.create",
            confidence=0.75,
            entities={"person": person, "raw": utterance},
            requires_confirmation=True,
            reason="Task creation; confirmation required.",
        )

    # ----- CLIENT CREATE -----
    if _has(low, _CREATE_CLIENT):
        # name = everything after the create-client phrase
        name = re.sub(r".*(create client|new client|add client|vytvoř klienta|vytvor klienta|nový klient|novy klient)\s*",
                      "", utterance, flags=re.IGNORECASE).strip()
        return ParsedIntent(
            intent="client.create",
            confidence=0.75 if name else 0.5,
            entities={"name": name or None, "raw": utterance},
            requires_confirmation=True,
            reason="Client creation; confirmation required.",
        )

    # ----- WORK REPORT (hand off to voice session) -----
    if _has(low, _CREATE_WR):
        return ParsedIntent(
            intent="work_report.start",
            confidence=0.7,
            entities={},
            requires_confirmation=False,
            reason="Work report uses the multi-turn voice session flow.",
        )

    return ParsedIntent(
        intent=None,
        confidence=0.0,
        entities={},
        requires_confirmation=False,
        reason="No backend intent matched; no action taken.",
    )
