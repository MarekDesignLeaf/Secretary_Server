"""Pre-built voice synonym dictionary (Voice Command Learning — Phase 1, §4).

Two composable layers turn a small hand-maintained table into a large synonym
surface:

  * ACTION_SYNONYMS  — verb stems → canonical action (create/update/delete/list)
  * OBJECT_SYNONYMS  — noun  stems → canonical object (client/task/job/...)

`compose_intent()` finds an action stem AND an object stem in the normalized
text and looks up COMPOSITION[(action, object)] → intent_code. This is the
SECOND-best builtin signal (below an explicit whole-phrase match in the intent
registry) and never fires unless both halves are present.

NO HTTP, NO AI — deterministic, pure functions. `normalize()` uses the same
case-fold + diacritics-strip semantics as the rest of the codebase
(alias_learning.normalize, voice.py::_strip_diacritics) so behaviour is
identical everywhere.
"""
from __future__ import annotations

import re
import unicodedata

# Confidence a pure action+object composition earns (just under an explicit
# registry phrase match at 0.95 — both are in the HIGH band).
COMPOSITION_CONFIDENCE = 0.9

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)

# Common STT mishears, applied word-by-word on the already-normalized text.
# Keep small and obvious; extend as live logs reveal real errors.
_STT_FIXES = {
    "klijenta": "klienta",
    "klijent": "klient",
    "kontaktu": "kontakt",
    "zakazek": "zakazku",
    "vikaz": "vykaz",
    "faktoru": "fakturu",
}


def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """case-fold · strip diacritics · strip punctuation · collapse spaces ·
    fix common STT errors. Returns the canonical comparison form."""
    s = strip_diacritics((text or "").lower())
    s = _PUNCT.sub(" ", s)
    words = [_STT_FIXES.get(w, w) for w in s.split()]
    return " ".join(words)


# ── Action layer (verb stems, already diacritics-free) ────────────────────────
# Stems are matched as substrings of normalized words, so Czech declensions are
# covered ("vytvor", "vytvorit", "vytvorim" all contain "vytvor").
ACTION_SYNONYMS: dict[str, list[str]] = {
    "create": [
        "vytvor", "zaloz", "pridej", "udelej", "novy", "nova", "nove", "novou",
        "zapis", "naplanuj", "naplanovat", "zaeviduj", "zadej", "domluv",
        "create", "add", "make", "new", "set up", "setup", "schedule", "register",
    ],
    "update": [
        "uprav", "zmen", "prepis", "posun", "presun", "presunout", "aktualizuj",
        "oprav", "zmenit",
        "update", "change", "edit", "move", "reschedule", "modify",
    ],
    "delete": [
        "smaz", "zrus", "odstran", "vymaz", "odvolej", "anuluj",
        "delete", "remove", "cancel",
    ],
    "list": [
        "ukaz", "vypis", "zobraz", "co mam", "seznam", "prehled", "vyhledej",
        "najdi", "otevri", "jake mam", "co je",
        "list", "show", "display", "find", "search", "what",
    ],
}

# ── Object layer (noun stems) ─────────────────────────────────────────────────
OBJECT_SYNONYMS: dict[str, list[str]] = {
    "client": ["klient", "klienta", "klientku", "zakaznik", "zakaznika",
               "kontakt", "client", "customer", "contact"],
    "task": ["ukol", "ukoly", "task", "todo", "to do"],
    "job": ["zakazk", "zakazku", "zakazky", "job", "order", "gig"],
    "calendar": ["schuzk", "schuzku", "udalost", "kalendar", "termin",
                 "meeting", "appointment", "event", "calendar"],
    "invoice": ["faktur", "fakturu", "ucet", "invoice"],
    "quote": ["nabidk", "nabidku", "cenovou nabidku", "quote", "estimate"],
    "work_report": ["vykaz", "pracovni vykaz", "work report", "timesheet"],
}

# ── action + object → intent code (only implemented intents) ──────────────────
COMPOSITION: dict[tuple[str, str], str] = {
    ("create", "client"): "client.create",
    ("list", "client"): "client.find",
    ("create", "task"): "task.create",
    ("list", "task"): "task.list",
    ("create", "job"): "job.create",
    ("list", "job"): "job.list",
    ("create", "calendar"): "calendar.create",
    ("list", "calendar"): "calendar.list",
    ("update", "calendar"): "calendar.update",
    ("delete", "calendar"): "calendar.delete",
    ("create", "invoice"): "invoice.from_work_report",
    ("create", "quote"): "quote.create",
    ("create", "work_report"): "work_report.start",
    ("list", "work_report"): "work_report.start",
}


def _present(normalized: str, stems: list[str]) -> bool:
    return any(stem in normalized for stem in stems)


def detected_actions(normalized: str) -> list[str]:
    return [a for a, stems in ACTION_SYNONYMS.items() if _present(normalized, stems)]


def detected_objects(normalized: str) -> list[str]:
    return [o for o, stems in OBJECT_SYNONYMS.items() if _present(normalized, stems)]


def compose_intent(normalized: str) -> list[tuple[str, float]]:
    """Return distinct (intent_code, confidence) candidates implied by every
    (action, object) pair present in the normalized text. Empty if no pair maps
    to a known intent."""
    actions = detected_actions(normalized)
    objects = detected_objects(normalized)
    seen: dict[str, float] = {}
    for a in actions:
        for o in objects:
            intent = COMPOSITION.get((a, o))
            if intent and intent not in seen:
                seen[intent] = COMPOSITION_CONFIDENCE
    return list(seen.items())
