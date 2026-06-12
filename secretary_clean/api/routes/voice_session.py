"""Voice session flow: Voice → Work Report draft.

Endpoints
---------
POST /api/v1/voice/session/start   – open a new dialog session
POST /api/v1/voice/session/input   – advance the dialog one turn
POST /api/v1/voice/session/resume  – reload a paused session

Session is stored in-process (module-level dict). Sessions expire after
SESSION_TTL_SECONDS of inactivity. On confirm the session calls
repository.create_work_report() and returns the persisted CRMRecord id.

This module intentionally does NOT implement:
  - WhatsApp messaging
  - AI / LLM translation
  - Invoicing (next phase)
  - Admin / pricing management
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.models import (
    Permission,
    UserAccount,
    WorkReportCreate,
    WorkReportWorker,
)
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/voice/session", tags=["voice session"])

# ── persistent session store (Phase A2) ──────────────────────────────────────
# Sessions are persisted via the repository (DB-backed in production), so they
# survive server restart / redeploy. The in-process dict is gone.
SESSION_TTL_SECONDS = 3600  # 1 hour


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _create_session(repository, company_id: str, user_id: str, language: str, work_date: str) -> dict:
    sid = str(uuid.uuid4())
    session = {
        "id": sid,
        "company_id": company_id,
        "user_id": user_id,
        "state": "active",
        "step": "client",
        "language": language,
        "work_date": work_date,
        "client_id": None,
        "client_name": None,
        "workers": [],          # list of {user_id, display_name, hours, hourly_rate}
        "total_hours": 0.0,
        "notes": None,
        "saved_work_report_id": None,
        "created_at": _now().isoformat(),
        "touched_at": _now().isoformat(),
    }
    repository.save_voice_session(session)
    return session


def _get_session(repository, session_id: str, company_id: str) -> dict:
    sess = repository.load_voice_session(session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    if sess["company_id"] != company_id:
        raise HTTPException(403, "Session belongs to a different tenant")
    touched = datetime.fromisoformat(sess["touched_at"])
    if (_now() - touched).total_seconds() > SESSION_TTL_SECONDS:
        sess["state"] = "expired"
        repository.save_voice_session(sess)
        raise HTTPException(410, "Session expired")
    return sess


def _touch(sess: dict) -> None:
    sess["touched_at"] = _now().isoformat()


# ── dialog prompts ────────────────────────────────────────────────────────────

_PROMPTS: dict[str, dict[str, str]] = {
    "client": {
        "en": "Which client? Say a name or 'new client'.",
        "cs": "Který klient? Řekni jméno nebo 'nový klient'.",
        "pl": "Który klient? Podaj nazwę lub powiedz 'nowy klient'.",
    },
    "client_name": {
        "en": "What is the name of the new client?",
        "cs": "Jak se nový klient jmenuje?",
        "pl": "Jak nazywa się nowy klient?",
    },
    "date": {
        "en": "What date? (e.g. today, yesterday, 2026-05-28)",
        "cs": "Které datum? (např. dnes, včera, 28.05.2026)",
        "pl": "Jaka data? (np. dzisiaj, wczoraj, 28.05.2026)",
    },
    "workers": {
        "en": "Who worked? Say one or more names, then 'done'.",
        "cs": "Kdo pracoval? Řekni jedno nebo více jmen, pak 'hotovo'.",
        "pl": "Kto pracował? Podaj jedno lub więcej imion, potem 'gotowe'.",
    },
    "total_hours": {
        "en": "How many total hours?",
        "cs": "Kolik hodin celkem?",
        "pl": "Ile godzin łącznie?",
    },
    "notes": {
        "en": "Any notes? (or say 'skip')",
        "cs": "Nějaké poznámky? (nebo řekni 'přeskočit')",
        "pl": "Jakieś uwagi? (lub powiedz 'pomiń')",
    },
    "summary": {
        "en": "Say 'confirm' to save, 'edit client/date/workers/hours/notes', or 'cancel'.",
        "cs": "Řekni 'potvrdit' pro uložení, 'oprav klienta/datum/pracovníky/hodiny/poznámku' nebo 'zrušit'.",
        "pl": "Powiedz 'potwierdź' żeby zapisać, 'edytuj klienta/datę/pracowników/godziny/notatki' lub 'anuluj'.",
    },
}


def _prompt(step: str, lang: str) -> str:
    variants = _PROMPTS.get(step, {})
    return variants.get(lang, variants.get("en", f"[{step}]"))


# ── text helpers ──────────────────────────────────────────────────────────────

_NEGATIVE = {"no", "none", "skip", "0", "zero", "nothing",
             "ne", "nic", "přeskočit", "preskocit",
             "nie", "nic", "pomiń", "pomin"}

_CONFIRM_WORDS = {"confirm", "yes", "save", "ok", "done",
                  "potvrdit", "ano", "uložit", "ulozit",
                  "potwierdź", "tak", "zapisz"}

_CANCEL_WORDS = {"cancel", "stop", "abort", "discard",
                 "zrusit", "zrus", "smazat", "omyl", "konec",
                 "neplatny prikaz", "anuluj", "przerwij"}


def _norm(s: str) -> str:
    """Lowercase, strip diacritics, collapse separators — STT output differs
    from stored names exactly in these (smoke test FAIL: 'SMOKE-Novák' vs
    'smoke novak')."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def _is_cancel(text: str) -> bool:
    norm = _norm(text)
    if not norm:
        return False
    if norm in _CANCEL_WORDS:
        return True
    # Short utterances containing a cancel word ("tak to zruš") count too;
    # long ones do not, so dictated notes can still mention the words.
    return len(norm.split()) <= 4 and any(w in norm for w in _CANCEL_WORDS)


def _is_negative(text: str) -> bool:
    return text.strip().lower() in _NEGATIVE


def _parse_hours(text: str) -> float | None:
    text = text.strip().lower()
    # "half" → 0.5, "quarter" → 0.25
    text = text.replace("half", "0.5").replace("quarter", "0.25")
    text = text.replace(",", ".")
    # extract first number
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1))
    return None


def _parse_date(text: str) -> str | None:
    """Return ISO date string or None."""
    today = _now().date()
    low = text.strip().lower()
    if low in ("today", "dnes", "dzisiaj", "heute"):
        return today.isoformat()
    if low in ("yesterday", "včera", "wczoraj", "gestern"):
        return (today - timedelta(days=1)).isoformat()
    # ISO: 2026-05-28
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # European: 28.05.2026 or 28/05/2026
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


# "nový klient" with or without diacritics, optionally followed by the name.
_NEW_CLIENT_RE = re.compile(
    r"^\s*(new\s+client|nov[yý]\s+klient|nowego\s+klienta|nowy\s+klient|new|nov[yý]|nowy)\b[\s,:-]*",
    re.IGNORECASE)


def _find_clients(name: str, repository, company_id: str) -> list:
    """Return CRMRecord list matching the name, diacritics/separator-insensitive."""
    records = [r for r in repository.list_crm_records("clients", company_id)
               if r.status != "deleted"]
    q = _norm(name)
    if not q:
        return []
    hits = []
    for r in records:
        n = _norm(r.name)
        if q in n or all(token in n for token in q.split()):
            hits.append(r)
    return hits


def _find_users(text: str, repository, company_id: str) -> tuple[list, list]:
    """Return (matched_users, not_found_tokens)."""
    users = repository.list_users(company_id)
    # split on , ; & and the words "and"/"a"/"i" — NOT a char class (old bug shredded names containing a/n/d)
    tokens = [t.strip() for t in re.split(r"[,;&]+|\band\b|\ba\b|\bi\b", text, flags=re.IGNORECASE) if t.strip()]
    matched, not_found = [], []
    for token in tokens:
        q = _norm(token)
        hits = [u for u in users
                if q in _norm(u.display_name)
                or (u.first_name and q in _norm(u.first_name))
                or (u.last_name and q in _norm(u.last_name))]
        if hits:
            matched.append(hits[0])
        else:
            not_found.append(token)
    return matched, not_found


def _build_summary(sess: dict) -> str:
    lang = sess["language"]
    workers = ", ".join(w["display_name"] for w in sess["workers"]) or "—"
    if lang == "cs":
        return (
            f"Klient: {sess['client_name'] or '—'}\n"
            f"Datum: {sess['work_date']}\n"
            f"Pracovníci: {workers}\n"
            f"Celkem hodin: {sess['total_hours']}\n"
            f"Poznámka: {sess['notes'] or '—'}"
        )
    if lang == "pl":
        return (
            f"Klient: {sess['client_name'] or '—'}\n"
            f"Data: {sess['work_date']}\n"
            f"Pracownicy: {workers}\n"
            f"Łącznie godzin: {sess['total_hours']}\n"
            f"Uwagi: {sess['notes'] or '—'}"
        )
    return (
        f"Client: {sess['client_name'] or '—'}\n"
        f"Date: {sess['work_date']}\n"
        f"Workers: {workers}\n"
        f"Total hours: {sess['total_hours']}\n"
        f"Notes: {sess['notes'] or '—'}"
    )


# ── Pydantic I/O models ───────────────────────────────────────────────────────

class SessionStartRequest(BaseModel):
    language: str = "en"
    work_date: str | None = None


class SessionInputRequest(BaseModel):
    session_id: str
    text: str


class SessionResumeRequest(BaseModel):
    session_id: str


class SessionResponse(BaseModel):
    session_id: str
    step: str
    prompt: str
    work_report_id: str | None = None
    summary: str | None = None


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/start", response_model=SessionResponse)
def voice_session_start(
    payload: SessionStartRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Start a new voice Work Report session."""
    work_date = payload.work_date or _now().date().isoformat()
    lang = payload.language[:2] if len(payload.language) >= 2 else "en"
    sess = _create_session(repository, user.company_id, user.id, lang, work_date)
    return SessionResponse(
        session_id=sess["id"],
        step="client",
        prompt=_prompt("client", lang),
    )


@router.post("/input", response_model=SessionResponse)
def voice_session_input(
    payload: SessionInputRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Advance the voice dialog by one turn."""
    sess = _get_session(repository, payload.session_id, user.company_id)
    if sess["state"] != "active":
        raise HTTPException(409, f"Session is {sess['state']}, not active")

    text = payload.text.strip()
    low = text.lower()
    step = sess["step"]
    lang = sess["language"]
    reply = ""
    next_step = step

    # ── CANCEL works at EVERY step, not only in summary ──────────────────────
    if _is_cancel(text):
        sess["state"] = "cancelled"
        _touch(sess)
        repository.save_voice_session(sess)
        return SessionResponse(
            session_id=sess["id"],
            step="done",
            prompt=("Session cancelled. Nothing was saved." if lang == "en" else
                    "Zrušeno. Nic se neuložilo." if lang == "cs" else
                    "Anulowano. Nic nie zapisano."),
        )

    # ── CLIENT ────────────────────────────────────────────────────────────────
    if step == "client":
        # Diacritics-insensitive: STT often yields "novy klient" for "nový klient".
        new_match = _NEW_CLIENT_RE.match(text)
        wants_new = bool(new_match)
        remainder = text[new_match.end():].strip() if new_match else ""
        if wants_new and not remainder:
            next_step = "client_name"
            reply = _prompt("client_name", lang)
        else:
            name_q = remainder if wants_new else text
            matches = _find_clients(name_q, repository, user.company_id)
            if len(matches) == 1:
                sess["client_id"] = matches[0].id
                sess["client_name"] = matches[0].name
                next_step = "date"
                reply = f"{matches[0].name}. " + _prompt("date", lang)
            elif len(matches) > 1:
                names = ", ".join(m.name for m in matches[:5])
                reply = (
                    f"Found: {names}. Which one?"
                    if lang == "en" else
                    f"Nalezeni: {names}. Který?"
                    if lang == "cs" else
                    f"Znaleziono: {names}. Który?"
                )
            elif wants_new:
                # "new client SomeName"
                sess["client_name"] = name_q.title()
                sess["client_id"] = None  # no CRM record yet, name only
                next_step = "date"
                reply = f"'{sess['client_name']}' noted. " + _prompt("date", lang)
            else:
                reply = (
                    "Client not found. Try again or say 'new client'."
                    if lang == "en" else
                    "Klient nenalezen. Zkus znovu nebo řekni 'nový klient'."
                    if lang == "cs" else
                    "Klient nie znaleziony. Spróbuj ponownie albo powiedz 'nowy klient'."
                )

    # ── CLIENT NAME (follow-up after 'new client') ───────────────────────────
    elif step == "client_name":
        sess["client_name"] = text.strip().title()
        sess["client_id"] = None
        next_step = "date"
        reply = f"'{sess['client_name']}'. " + _prompt("date", lang)

    # ── DATE ─────────────────────────────────────────────────────────────────
    elif step == "date":
        parsed = _parse_date(text)
        if not parsed:
            reply = (
                "Invalid date. Try 'today', 'yesterday' or 2026-05-28."
                if lang == "en" else
                "Neplatné datum. Zkus 'dnes', 'včera' nebo 28.05.2026."
                if lang == "cs" else
                "Nieprawidłowa data. Spróbuj 'dzisiaj', 'wczoraj' lub 28.05.2026."
            )
        else:
            sess["work_date"] = parsed
            next_step = "workers"
            reply = f"{parsed}. " + _prompt("workers", lang)

    # ── WORKERS ──────────────────────────────────────────────────────────────
    elif step == "workers":
        if low in ("done", "hotovo", "gotowe", "continue", "pokračuj", "dalej"):
            if sess["workers"]:
                next_step = "total_hours"
                reply = _prompt("total_hours", lang)
            else:
                reply = (
                    "No workers added yet."
                    if lang == "en" else
                    "Ještě žádní pracovníci."
                    if lang == "cs" else
                    "Nie dodano pracowników."
                )
        else:
            matched, not_found = _find_users(text, repository, user.company_id)
            # merge (avoid duplicates)
            existing_ids = {w["user_id"] for w in sess["workers"]}
            added = []
            for u in matched:
                if u.id not in existing_ids:
                    sess["workers"].append({
                        "user_id": u.id,
                        "display_name": u.display_name,
                        "hours": 0.0,
                        "hourly_rate": 0.0,
                    })
                    existing_ids.add(u.id)
                    added.append(u.display_name)
            if added and not not_found:
                names = ", ".join(added)
                reply = (
                    f"Added: {names}. Say more names or 'done'."
                    if lang == "en" else
                    f"Přidáni: {names}. Řekni další nebo 'hotovo'."
                    if lang == "cs" else
                    f"Dodano: {names}. Podaj kolejne lub powiedz 'gotowe'."
                )
            elif added and not_found:
                nf = ", ".join(not_found)
                names = ", ".join(added)
                reply = (
                    f"Added: {names}. Not found: {nf}. Say more or 'done'."
                    if lang == "en" else
                    f"Přidáni: {names}. Nenalezeni: {nf}. Řekni další nebo 'hotovo'."
                    if lang == "cs" else
                    f"Dodano: {names}. Nie znaleziono: {nf}. Podaj kolejne lub 'gotowe'."
                )
            else:
                reply = (
                    "No workers found. Try first names or 'done' to continue."
                    if lang == "en" else
                    "Nenalezeni. Zkus křestní jména nebo 'hotovo'."
                    if lang == "cs" else
                    "Nie znaleziono. Spróbuj imiona lub 'gotowe'."
                )

    # ── TOTAL HOURS ──────────────────────────────────────────────────────────
    elif step == "total_hours":
        hrs = _parse_hours(text)
        if hrs is None or hrs <= 0:
            reply = (
                "Invalid number. How many total hours?"
                if lang == "en" else
                "Neplatné číslo. Kolik hodin celkem?"
                if lang == "cs" else
                "Nieprawidłowa liczba. Ile godzin łącznie?"
            )
        else:
            sess["total_hours"] = hrs
            # distribute hours equally among workers
            wc = len(sess["workers"])
            if wc > 0:
                per = round(hrs / wc, 2)
                for w in sess["workers"]:
                    w["hours"] = per
            next_step = "notes"
            reply = f"{hrs}h. " + _prompt("notes", lang)

    # ── NOTES ────────────────────────────────────────────────────────────────
    elif step == "notes":
        if not _is_negative(text) and text:
            sess["notes"] = text
        next_step = "summary"
        summary = _build_summary(sess)
        reply = summary + "\n\n" + _prompt("summary", lang)

    # ── SUMMARY (cancel is handled globally above) ───────────────────────────
    elif step == "summary":
        if any(x in low for x in _CONFIRM_WORDS):
            next_step = "confirm"

        elif "client" in low or "klient" in low:
            next_step = "client"
            reply = _prompt("client", lang)
        elif "date" in low or "datum" in low or "data" in low:
            next_step = "date"
            reply = _prompt("date", lang)
        elif "worker" in low or "pracovn" in low or "pracown" in low:
            next_step = "workers"
            reply = _prompt("workers", lang)
        elif "hour" in low or "hodin" in low or "godzin" in low:
            next_step = "total_hours"
            reply = _prompt("total_hours", lang)
        elif "note" in low or "pozn" in low or "uwag" in low:
            next_step = "notes"
            reply = _prompt("notes", lang)
        else:
            reply = _prompt("summary", lang)

    # ── CONFIRM → save work report ────────────────────────────────────────────
    if next_step == "confirm" and step != "confirm":
        if not sess["client_name"]:
            next_step = "client"
            reply = "Client required. " + _prompt("client", lang)
        elif sess["total_hours"] <= 0:
            next_step = "total_hours"
            reply = "Hours required. " + _prompt("total_hours", lang)
        else:
            # build WorkReportCreate payload
            workers_payload = [
                WorkReportWorker(
                    worker_name=w["display_name"],
                    hours=w["hours"],
                    hourly_rate=w["hourly_rate"],
                )
                for w in sess["workers"]
            ]
            wr_payload = WorkReportCreate(
                job_id=None,
                client_id=sess["client_id"],
                work_date=sess["work_date"],
                total_hours=sess["total_hours"],
                total_price=0.0,
                currency="GBP",
                notes=f"[voice] client: {sess['client_name']}"
                      + (f" | {sess['notes']}" if sess["notes"] else ""),
                input_type="voice",
                workers=workers_payload,
            )
            record = repository.create_work_report(user.company_id, wr_payload)
            sess["saved_work_report_id"] = record.id
            sess["state"] = "completed"
            sess["step"] = "done"
            _touch(sess)
            repository.save_voice_session(sess)
            save_reply = (
                f"Saved. Work report id: {record.id}"
                if lang == "en" else
                f"Uloženo. Work report id: {record.id}"
                if lang == "cs" else
                f"Zapisano. Work report id: {record.id}"
            )
            return SessionResponse(
                session_id=sess["id"],
                step="done",
                prompt=save_reply,
                work_report_id=record.id,
                summary=_build_summary(sess),
            )

    sess["step"] = next_step
    _touch(sess)
    repository.save_voice_session(sess)
    return SessionResponse(
        session_id=sess["id"],
        step=next_step,
        prompt=reply or _prompt(next_step, lang),
    )


@router.post("/resume", response_model=SessionResponse)
def voice_session_resume(
    payload: SessionResumeRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Resume a paused session — returns current step and prompt."""
    sess = _get_session(repository, payload.session_id, user.company_id)
    if sess["state"] == "completed":
        return SessionResponse(
            session_id=sess["id"],
            step="done",
            prompt="Session already completed.",
            work_report_id=sess.get("saved_work_report_id"),
        )
    if sess["state"] == "cancelled":
        return SessionResponse(
            session_id=sess["id"],
            step="done",
            prompt="Session was cancelled.",
        )
    _touch(sess)
    repository.save_voice_session(sess)
    lang = sess["language"]
    step = sess["step"]
    return SessionResponse(
        session_id=sess["id"],
        step=step,
        prompt=_prompt(step, lang),
    )
