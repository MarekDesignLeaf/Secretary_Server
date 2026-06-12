"""Adaptive voice-alias learning (per spec "Model uceni aliasu").

When the parser returns an unknown intent, the user can teach an alias:
  unknown phrase -> known command.

Adaptivity: an alias is stored EVEN IF the target intent is not executable yet.
- target currently supported  -> status ACTIVE  (works immediately)
- target not supported yet     -> status PENDING (activates once the module ships)

This never lets voice bypass workflow: the alias only rewrites the phrase to an
existing command; that command still goes through normal validation/permissions.
"""
from __future__ import annotations

import unicodedata

# Intents the backend can actually execute today. Keep in sync with voice.py
# execution branches. When a new module ships, add its intent here and pending
# aliases pointing at it auto-activate via refresh.
SUPPORTED_INTENTS = {
    "calendar.create", "calendar.list", "calendar.delete", "calendar.update", "calendar.sync",
    "client.create", "task.create", "task.list", "task.complete", "job.create", "job.list", "job.change_status", "comm.log", "comm.list", "whatsapp.send", "whatsapp.read", "weather.get", "client.set_address", "client.find", "contacts.import",
}

# Intents the system knows about but cannot execute yet (planned). Used to
# validate that a learning target is a real future command, not gibberish.
PLANNED_INTENTS = {
    "client.find", "client.note", "client.archive",
    "task.assign",
    "lead.create", "lead.convert",
    "quote.create", "quote.send", "quote.approve",
    "invoice.create", "invoice.status", "invoice.list",
    "material.order", "material.check",
    "report.jobs",
}

ALL_KNOWN_INTENTS = SUPPORTED_INTENTS | PLANNED_INTENTS

_CANCEL_WORDS = ("omyl", "neplatny prikaz", "neplatny", "zrus", "zrusit",
                 "cancel", "nic", "nech to byt", "stop", "to nic")


def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def normalize(text: str) -> str:
    return " ".join(strip_diacritics((text or "").lower()).split())


def is_cancel(text: str) -> bool:
    n = normalize(text)
    return any(w in n for w in _CANCEL_WORDS)


def resolve_target_intent(answer: str):
    """Map the user's answer ('vytvor fakturu') to a known intent string.
    Tries the live parser first, then keyword matching against known intents.
    Returns an intent string or None."""
    from secretary_clean.core.voice_intents import parse_intent
    parsed = parse_intent(answer)
    if parsed and parsed.intent:
        return parsed.intent
    # Keyword fallback for planned modules the parser doesn't handle yet.
    n = normalize(answer)
    KW = {
        "faktur": "invoice.create", "ucet": "invoice.create",
        "nabidk": "quote.create", "cenovou nabidku": "quote.create",
        "zakazk": "job.create", "zakazku": "job.create",
        "lead": "lead.create", "poptavk": "lead.create",
        "material": "material.order", "objednej": "material.order",
        "report": "report.jobs", "report zakazek": "report.jobs",
        "komunikac": "comm.log", "hovor": "comm.log",
    }
    for kw, intent in KW.items():
        if kw in n:
            return intent
    return None


def status_for(intent: str) -> str:
    return "ACTIVE" if intent in SUPPORTED_INTENTS else "PENDING"


def is_known(intent: str) -> bool:
    return intent in ALL_KNOWN_INTENTS
