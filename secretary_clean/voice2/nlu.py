"""voice2 NLU: normalization, multi-command segmentation, context inheritance.

Pure and testable — NO HTTP, NO DB, NO AI. Builds on the proven primitives in
core.voice_intents (dates, times, persons) and core.voice_synonyms (normalize).

Why segmentation lives here and not in the parser: the parser answers "what does
THIS clause mean"; segmentation answers "how many things did the user actually
ask for". Keeping them apart lets one spoken sentence like

    "vytvoř zakázku pro Nováka na úterý a přidej mu úkol zavolat
     a pak označ zakázku plot jako dokončenou"

become an ordered queue of three commands that share context (Novák, úterý).
"""
from __future__ import annotations

import re

from secretary_clean.core import voice_intents as vi
from secretary_clean.core import voice_synonyms as vsyn

# ── segmentation ──────────────────────────────────────────────────────────────
# Strong separators: always start a new clause.
_STRONG_SEPS = (
    " a pak ", " a potom ", " potom ", " a nakonec ", " nakonec ",
    " a taky ", " a take ", " a jeste ", " jeste ",
    " and then ", " then ", " and also ", " also ",
    " az to bude ", " az to bude tak ", " a az to bude ",
)
# Weak separator: "a"/"and" splits ONLY when followed by a new action verb —
# otherwise it is a value enumeration ("cement a písek") and must not split.
_WEAK_SEPS = (" a ", " and ", ", ")

# Verbs that signal a NEW command after a weak separator. Diacritics-stripped,
# prefix-matched so declensions ("označ", "označit") still hit.
_ACTION_VERB_PREFIXES = (
    "vytvor", "vytvoř", "zaloz", "pridej", "přidej", "udelej", "udělej", "zapis",
    "zaznamenej", "zaeviduj", "zmen", "změn", "nastav", "oznac", "označ",
    "dokonci", "dokonč", "uzavri", "uzavři", "zrus", "zruš", "smaz", "smaž",
    "presun", "přesuň", "posun", "prelozh", "najdi", "vyhledej", "ukaz", "ukaž",
    "zobraz", "precti", "přečti", "posli", "pošli", "napis", "napiš", "odpovez",
    "zaloguj", "importuj", "synchronizuj", "naplanuj", "napl", "domluv",
    "vystav", "fakturuj", "vyfakturuj", "prepni", "přepni", "otevri", "otevři",
    "doplnh", "dopln", "vygeneruj", "spust", "zacni", "začni", "prirad", "přiřaď",
    "create", "add", "make", "new", "set", "mark", "complete", "finish", "close",
    "cancel", "delete", "remove", "move", "reschedule", "find", "show", "list",
    "read", "send", "write", "reply", "log", "import", "sync", "schedule",
    "book", "issue", "invoice", "assign", "open", "start", "switch",
)


def _norm(s: str) -> str:
    return vsyn.normalize(s)


def _starts_with_action(clause: str) -> bool:
    words = _norm(clause).split()
    if not words:
        return False
    first = words[0]
    # allow one filler word before the verb ("pak mu přidej…" handled by strong
    # seps; here e.g. "mu zavolej" is not a new command)
    return any(first.startswith(p) for p in (_norm(p) for p in _ACTION_VERB_PREFIXES))


def segment(utterance: str) -> list[str]:
    """Split one spoken sentence into an ordered list of command clauses.

    Guarantees: at least one segment; segments keep their ORIGINAL text (with
    diacritics) so downstream entity extraction is unharmed; value enumerations
    joined by "a"/"and" are never split.
    """
    text = " ".join((utterance or "").split())
    if not text:
        return [""]

    # Pass 1 — strong separators (match on the normalized text, cut the raw one).
    pieces = [text]
    for sep in _STRONG_SEPS:
        nxt: list[str] = []
        for piece in pieces:
            nxt.extend(_split_keeping_raw(piece, sep))
        pieces = nxt

    # Pass 2 — weak separators, only before a new action verb.
    out: list[str] = []
    for piece in pieces:
        out.extend(_weak_split(piece))
    return [p for p in (s.strip(" ,.") for s in out) if p] or [text]


def _split_keeping_raw(raw: str, sep_norm: str) -> list[str]:
    """Split `raw` wherever its normalized form contains `sep_norm` (normalized
    comparison, raw output)."""
    norm = _norm(raw)
    sep = _norm(sep_norm).strip()
    if not sep or f" {sep} " not in f" {norm} ":
        return [raw]
    # Walk word-by-word so raw/normalized indexes stay aligned.
    raw_words = raw.split()
    norm_words = norm.split()
    sep_words = sep.split()
    if len(raw_words) != len(norm_words):     # normalization changed word count —
        return [raw]                           # play safe, don't split
    parts, start, i = [], 0, 0
    while i <= len(norm_words) - len(sep_words):
        if norm_words[i:i + len(sep_words)] == sep_words:
            if i > start:
                parts.append(" ".join(raw_words[start:i]))
            start = i + len(sep_words)
            i = start
        else:
            i += 1
    parts.append(" ".join(raw_words[start:]))
    return [p for p in parts if p.strip()]


def _weak_split(raw: str) -> list[str]:
    raw_words = raw.split()
    norm_words = _norm(raw).split()
    if len(raw_words) != len(norm_words) or len(raw_words) < 3:
        return [raw]
    parts, start = [], 0
    i = 1
    while i < len(raw_words) - 1:
        w = norm_words[i]
        if w in ("a", "and") and _starts_with_action(" ".join(raw_words[i + 1:])):
            parts.append(" ".join(raw_words[start:i]))
            start = i + 1
        i += 1
    parts.append(" ".join(raw_words[start:]))
    return [p for p in parts if p.strip()]


# ── shared context between segments ───────────────────────────────────────────
# Anaphora tokens that refer to the previously mentioned person/entity.
_ANAPHORA_PERSON = ("mu", "ji", "jemu", "jí", "pro nej", "pro něj", "pro ni",
                    "him", "her", "them")
_ANAPHORA_ENTITY = ("tu zakazku", "tu zakázku", "te zakazce", "té zakázce",
                    "to", "tam", "the job", "it", "that job")


class SegmentContext:
    """Carries resolved values forward through the command queue. A later
    segment inherits person/client/date it did not name itself; an explicit
    new value overwrites the inherited one."""

    def __init__(self) -> None:
        self.person: str | None = None
        self.client: str | None = None
        self.client_id: str | None = None
        self.date: str | None = None
        self.last_entity: tuple[str, str] | None = None   # (kind, id)
        self.last_title: str | None = None

    def absorb(self, intent: str | None, data: dict, entity_kind: str | None = None,
               entity_id: str | None = None) -> None:
        if data.get("person"):
            self.person = data["person"]
        if data.get("client"):
            self.client = data["client"]
        if data.get("client_id"):
            self.client_id = data["client_id"]
        if data.get("date"):
            self.date = data["date"]
        if data.get("title"):
            self.last_title = data["title"]
        if entity_kind and entity_id:
            self.last_entity = (entity_kind, entity_id)

    def enrich(self, text: str, data: dict) -> dict:
        """Fill missing slots in `data` from context when the segment refers
        back anaphorically (or names nothing at all)."""
        norm = f" {_norm(text)} "
        refers_person = any(f" {_norm(t)} " in norm for t in _ANAPHORA_PERSON)
        refers_entity = any(_norm(t) in norm for t in _ANAPHORA_ENTITY)
        d = dict(data)
        if (refers_person or not d.get("person")) and self.person and not d.get("person"):
            # inherit only when the segment either points back or names nobody
            if refers_person or refers_entity or True:
                d.setdefault("person", self.person)
        if self.client and not d.get("client") and (refers_person or refers_entity):
            d["client"] = self.client
        if self.client_id and refers_entity and not d.get("client_id"):
            d["client_id"] = self.client_id
        if self.date and not d.get("date") and refers_entity:
            d["date"] = self.date
        if refers_entity and self.last_entity and not d.get("entity_ref"):
            d["entity_ref"] = {"kind": self.last_entity[0], "id": self.last_entity[1]}
            if self.last_title and not d.get("target_hint"):
                d["target_hint"] = self.last_title
        return d


# ── light entity extraction (shared with alias/AI paths) ─────────────────────
def entities_from_text(intent: str | None, text: str) -> dict:
    """Best-effort entities for a phrase resolved by alias/AI where the
    deterministic parser did not re-derive them."""
    d: dict = {"raw": text}
    person = vi.extract_person(text)
    if person:
        d["person"] = person
    date_iso = vi.parse_date(text)
    if date_iso:
        d["date"] = date_iso
        t = vi.parse_time(text)
        d["start_at"] = f"{date_iso}T{t}:00Z" if t else f"{date_iso}T00:00:00Z"
    return d


_CONFIRM_WORDS = ("ano", "jo", "potvrdit", "potvrzuji", "potvrzuju", "uloz",
                  "ulozit", "hotovo", "jed", "proved", "yes", "ok", "okay",
                  "confirm", "do it", "go", "sure")


def is_confirm(text: str) -> bool:
    norm = _norm(text)
    return any(norm == w or norm.startswith(w + " ") for w in
               (_norm(w) for w in _CONFIRM_WORDS))
