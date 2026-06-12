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
               "show my calendar", "my schedule", "můj kalendář", "muj kalendar", "co je zitra", "co je zítra", "co je na zitrek", "co je na zítřek", "co mam zitra", "co mám zítra", "co je v kalendari", "co je v kalendář", "v kalendari", "v kalendář", "mam dnes", "mám dnes", "co je dnes", "co mam na", "co mám na", "schuzky", "schůzky", "otevři kalendář", "otevri kalendar", "ukaž kalendář", "ukaz kalendar", "zobraz kalendář", "zobraz kalendar", "ukaž rozvrh", "ukaz rozvrh", "můj rozvrh", "muj rozvrh", "co mě čeká", "co me ceka", "jaký mám program", "jaky mam program", "co mám naplánováno", "co mam naplanovano", "program na", "rozvrh na")
_WEEK_WORDS = ("tento týden", "tento tyden", "tenhle týden", "tenhle tyden", "this week", "celý týden", "cely tyden", "týdenní přehled", "tydenni prehled", "co mám tento týden", "ukaž týden", "ukaz tyden", "příští týden", "pristi tyden", "next week")

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
_LIST_TASKS = ("moje úkoly", "moje ukoly", "my tasks", "co mám za úkoly", "co mam za ukoly",
               "jaké mám úkoly", "jake mam ukoly", "zobraz úkoly", "zobraz ukoly",
               "ukaž úkoly", "ukaz ukoly", "seznam úkolů", "seznam ukolu", "co mám udělat",
               "co mam udelat", "list tasks", "show tasks", "nedokončené úkoly", "nedokoncene ukoly")
_COMPLETE_TASK = ("dokonči úkol", "dokonci ukol", "hotový úkol", "hotovy ukol", "splnil jsem",
                  "splněno", "splneno", "úkol hotovo", "ukol hotovo", "označ úkol", "oznac ukol",
                  "complete task", "mark task done", "dokončit úkol", "dokoncit ukol",
                  "uzavři úkol", "uzavri ukol", "hotovo úkol")
_CREATE_CLIENT = ("create client", "new client", "add client", "register client",
                  "vytvoř klienta", "vytvor klienta", "nový klient", "novy klient",
                  "novy zakaznik", "nový zákazník", "přidej klienta", "pridej klienta",
                  "zaevidovat klienta", "zaeviduj klienta", "zaloz klienta", "založ klienta",
                  "zapiš klienta", "zapis klienta", "novy kontakt klienta")
_CREATE_JOB = ("create job", "new job", "vytvoř zakázku", "vytvor zakazku", "nová zakázka",
               "nova zakazka", "založ zakázku", "zaloz zakazku", "přidej zakázku", "pridej zakazku",
               "zaeviduj zakázku", "zaeviduj zakazku", "nová zakázka pro", "zaloz zakazku pro")
_LOG_COMM = ("zaloguj hovor", "zaznamenej hovor", "zapiš hovor", "zapis hovor", "log hovor",
             "volal jsem", "telefonoval jsem", "zaloguj email", "zaznamenej email", "zapiš email",
             "zaznamenej komunikaci", "zaloguj komunikaci", "log call", "zaloguj sms", "poslal jsem sms",
             "psal jsem", "kontaktoval jsem", "mluvil jsem s")
_SEND_WHATSAPP = ("pošli whatsapp", "posli whatsapp", "napiš na whatsapp", "napis na whatsapp",
                  "pošli zprávu na whatsapp", "posli zpravu na whatsapp", "whatsapp zpráva",
                  "whatsapp zprava", "pošli whatsapp zprávu", "posli whatsapp zpravu",
                  "send whatsapp", "napiš whatsapp", "napis whatsapp", "zpráva přes whatsapp",
                  "zprava pres whatsapp", "pošli přes whatsapp", "posli pres whatsapp",
                  "odpověz na whatsapp", "odpovez na whatsapp", "odpověz mu", "odpovez mu",
                  "odpověz jí", "odpovez ji", "odepiš", "odepis", "reply on whatsapp")
_READ_WHATSAPP = ("přečti zprávy", "precti zpravy", "přečti mi zprávy", "precti mi zpravy",
                  "přečti zprávu", "precti zpravu", "přečti mi zprávu", "precti mi zpravu",
                  "přečti novou", "precti novou", "nová zpráva", "nova zprava",
                  "nové zprávy", "nove zpravy", "mám nové zprávy", "mam nove zpravy",
                  "zkontroluj zprávy", "zkontroluj zpravy", "přečti whatsapp", "precti whatsapp",
                  "nějaké zprávy", "nejake zpravy", "co mi přišlo", "co mi prislo",
                  "read messages", "check messages", "new messages", "read message")
_LIST_COMM = ("historie komunikace", "historie hovoru", "historie hovorů", "co jsme řešili",
              "co jsme resili", "komunikace s", "zobraz komunikaci", "ukaz komunikaci",
              "posledni komunikace", "poslední komunikace", "communication history")
_COMM_TYPE_MAP = (
    (("hovor", "volal", "telefon", "call"), "hovor"),
    (("email", "mail"), "email"),
    (("sms", "zpráv", "zprav"), "sms"),
    (("schůzk", "schuzk", "osobně", "osobne", "meeting"), "schůzka"),
)
_LIST_JOBS = ("zobraz zakázky", "zobraz zakazky", "moje zakázky", "moje zakazky", "seznam zakázek",
              "seznam zakazek", "ukaž zakázky", "ukaz zakazky", "jaké mám zakázky", "jake mam zakazky",
              "co mám za zakázky", "co mam za zakazky", "list jobs", "show jobs", "zakázky na",
              "aktivní zakázky", "aktivni zakazky")
_CHANGE_JOB_STATUS = ("změň stav zakázky", "zmen stav zakazky", "změň zakázku na", "zmen zakazku na",
                      "stav zakázky", "stav zakazky", "nastav zakázku", "nastav zakazku",
                      "zakázka je", "zakazka je", "je v realizaci", "je dokončeno", "je hotová", "change job status", "zakázku dokončeno",
                      "zakazku dokonceno", "dokonči zakázku", "dokonci zakazku")
# Map spoken status words -> canonical job status (blueprint S4)
_JOB_STATUS_MAP = (
    (("dokončeno", "dokonceno", "hotovo", "hotová", "hotova", "completed", "done"), "dokončeno"),
    (("v realizaci", "realizace", "probíhá", "probiha", "in progress", "active"), "v_realizaci"),
    (("naplánováno", "naplanovano", "scheduled", "naplánovaná"), "naplánováno"),
    (("čeká na materiál", "ceka na material", "waiting material", "na materiál"), "čeká_na_materiál"),
    (("čeká na klienta", "ceka na klienta", "waiting client", "na klienta"), "čeká_na_klienta"),
    (("vyfakturováno", "vyfakturovano", "invoiced", "fakturováno"), "vyfakturováno"),
    (("uzavřeno", "uzavreno", "closed", "uzavřená"), "uzavřeno"),
    (("zrušeno", "zruseno", "cancelled", "zrušená"), "zrušeno"),
    (("nová", "nova", "new"), "nová"),
)
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
    if (_has(low, _LIST_WORDS) or _has(low, _WEEK_WORDS)
            or (_has(low, _NEXT_WORDS) and ("calendar" in low or "kalendář" in low or "kalendar" in low))):
        date_iso = parse_date(low, base)
        win = time_window(low)
        is_next = _has(low, _NEXT_WORDS)
        # Week view: "this week" / "next week" -> a 7-day range starting Monday.
        rng = None
        if _has(low, _WEEK_WORDS):
            rng = "next_week" if ("příští" in low or "pristi" in low or "next week" in low) else "this_week"
        return ParsedIntent(
            intent="calendar.list",
            confidence=0.8,
            entities={"date": date_iso, "window": win, "next": is_next, "range": rng},
            requires_confirmation=False,
            reason="Read-only calendar query.",
        )

    # ----- TASK COMPLETE (before create: "dokonči úkol" must not match create) -----
    if _has(low, _COMPLETE_TASK):
        person = extract_person(utterance)
        return ParsedIntent(
            intent="task.complete",
            confidence=0.75,
            entities={"person": person, "raw": utterance},
            requires_confirmation=True,
            reason="Task completion; confirmation required.",
        )

    # ----- TASK LIST (read-only) -----
    if _has(low, _LIST_TASKS):
        return ParsedIntent(
            intent="task.list",
            confidence=0.8,
            entities={},
            requires_confirmation=False,
            reason="Read-only task query.",
        )

    # ----- TASK CREATE -----
    if _has(low, _CREATE_TASK):
        person = extract_person(utterance)
        # Extract the task title = text after the matched create-task phrase.
        title = None
        for kw in _CREATE_TASK:
            pos = low.find(kw)
            if pos >= 0:
                title = utterance[pos + len(kw):].strip(" :,-")
                break
        title = title or None
        # "vytvoř úkol na úterý ..." — the date must become the planned date,
        # otherwise the task never shows in the calendar / Today screen.
        date_iso = parse_date(utterance)
        hhmm = parse_time(utterance)
        start = _combine(date_iso, hhmm) if date_iso else None
        return ParsedIntent(
            intent="task.create",
            confidence=0.75,
            entities={"person": person, "raw": utterance, "title": title,
                      "date": date_iso, "time": hhmm, "start_at": start},
            requires_confirmation=True,
            reason="Task creation; confirmation required.",
        )

    # ----- WHATSAPP READ (read-only, before send so "přečti" wins) -----
    if _has(low, _READ_WHATSAPP):
        person = extract_person(utterance)
        return ParsedIntent(
            intent="whatsapp.read",
            confidence=0.8,
            entities={"person": person},
            requires_confirmation=False,
            reason="Read-only inbox query.",
        )

    # ----- WHATSAPP SEND (before comm log/list) -----
    if _has(low, _SEND_WHATSAPP):
        person = extract_person(utterance)
        # message text = only after an explicit content marker ("ze"/"rekni"/"text").
        # Otherwise leave empty and ask via slot-filling (more reliable than guessing).
        msg_text = None
        import re as _re
        m = _re.search(r"\b(že|ze|řekni mu|rekni mu|řekni jí|rekni ji|s textem|text)\b\s+(.+)$", utterance, flags=_re.IGNORECASE)
        if m:
            msg_text = m.group(2).strip()
        return ParsedIntent(
            intent="whatsapp.send",
            confidence=0.75,
            entities={"person": person, "message": msg_text, "raw": utterance},
            requires_confirmation=True,
            reason="Outbound WhatsApp message; confirmation required.",
        )

    # ----- COMMUNICATION LIST (read-only) -----
    if _has(low, _LIST_COMM):
        person = extract_person(utterance)
        return ParsedIntent(
            intent="comm.list",
            confidence=0.8,
            entities={"person": person},
            requires_confirmation=False,
            reason="Read-only communication query.",
        )

    # ----- COMMUNICATION LOG -----
    if _has(low, _LOG_COMM):
        comm_type = "hovor"
        for words, canonical in _COMM_TYPE_MAP:
            if any(w in low for w in words):
                comm_type = canonical
                break
        person = extract_person(utterance)
        return ParsedIntent(
            intent="comm.log",
            confidence=0.75,
            entities={"comm_type": comm_type, "person": person, "raw": utterance},
            requires_confirmation=True,
            reason="Communication log; confirmation required.",
        )

    # ----- JOB CHANGE STATUS (before create) -----
    if _has(low, _CHANGE_JOB_STATUS):
        new_status = None
        for words, canonical in _JOB_STATUS_MAP:
            if any(w in low for w in words):
                new_status = canonical
                break
        # "dokonči/dokonci zakázku" implies completed even without explicit status word
        if new_status is None and ("dokonč" in low or "dokonc" in low):
            new_status = "dokončeno"
        person = extract_person(utterance)
        return ParsedIntent(
            intent="job.change_status",
            confidence=0.75,
            entities={"new_status": new_status, "person": person, "raw": utterance},
            requires_confirmation=True,
            reason="Job status change; confirmation required.",
        )

    # ----- JOB LIST (read-only) -----
    if _has(low, _LIST_JOBS):
        return ParsedIntent(
            intent="job.list",
            confidence=0.8,
            entities={},
            requires_confirmation=False,
            reason="Read-only job query.",
        )

    # ----- JOB CREATE (before client: "zakazku pro Jana" must not match client) -----
    if _has(low, _CREATE_JOB):
        import re as _re
        title = _re.sub(r".*(create job|new job|vytvoř zakázku|vytvor zakazku|nová zakázka|nova zakazka|založ zakázku|zaloz zakazku|přidej zakázku|pridej zakazku|zaeviduj zakázku|zaeviduj zakazku)\s*",
                        "", utterance, flags=_re.IGNORECASE).strip(" :,-")
        # optional client after "pro"
        client = None
        m = _re.search(r"\bpro\s+(.+)$", title, flags=_re.IGNORECASE)
        if m:
            client = m.group(1).strip()
            title = title[:m.start()].strip(" :,-")
        return ParsedIntent(
            intent="job.create",
            confidence=0.75 if title else 0.5,
            entities={"title": title or None, "client": client, "raw": utterance},
            requires_confirmation=True,
            reason="Job creation; confirmation required.",
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
