"""Google Calendar integration routes (Phase G3).

Server-side OAuth 2.0 authorization code flow. The backend holds the Google
refresh token (clean_google_calendar_accounts) and is the source of truth.
Android only calls these endpoints; it never talks to Google directly.

Endpoints:
    GET  /calendar/google/status              connection status (no secrets)
    GET  /calendar/google/connect/start       returns Google consent URL
    GET  /calendar/google/callback            OAuth redirect target (exchanges code)
    POST /calendar/google/disconnect          clear stored tokens
    GET  /calendar/google/calendars           list the account's Google calendars
    PUT  /calendar/google/selected-calendar   choose which calendar to sync
    POST /calendar/google/sync                run reconciliation now (manual)
    GET  /calendar/google/sync-log            recent sync log entries

Security: tokens are NEVER logged or returned in responses. Access tokens are
refreshed automatically via the refresh token; if refresh fails the account
status becomes needs_reauth.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
import json as _json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel

from secretary_clean.api.deps import get_repository, current_user, require_permission
from secretary_clean.core.models import GoogleCalendarAccount, Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/calendar/google", tags=["google calendar"])

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar"


def _cfg():
    return (
        os.environ.get("GOOGLE_CLIENT_ID"),
        os.environ.get("GOOGLE_CLIENT_SECRET"),
        os.environ.get("GOOGLE_REDIRECT_URI",
                       "https://web-production-4b451.up.railway.app/api/v1/calendar/google/callback"),
    )


def _refresh_access_token(repository, account):
    """Refresh the access token via the stored refresh token. On failure set
    status=needs_reauth. Tokens are never logged."""
    client_id, client_secret, _redirect = _cfg()
    if not account.refresh_token or not client_id or not client_secret:
        account.status = "needs_reauth"
        repository.upsert_google_account(account)
        return None
    data = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": account.refresh_token, "grant_type": "refresh_token",
    }).encode()
    try:
        req = urllib.request.Request(GOOGLE_TOKEN_URI, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            tok = _json.loads(resp.read().decode())
        account.access_token = tok.get("access_token")
        account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=tok.get("expires_in", 3600))
        account.status = "connected"
        repository.upsert_google_account(account)
        return account.access_token
    except Exception:
        account.status = "needs_reauth"
        repository.upsert_google_account(account)
        return None

@router.get("/status")
def google_status(user: UserAccount = Depends(require_permission(Permission.crm_manage)),
                  repository: InMemorySecretaryRepository = Depends(get_repository)):
    acc = repository.get_google_account(user.company_id)
    if not acc:
        return {"status": "disconnected", "connected": False, "auto_sync_enabled": False,
                "google_account_email": None, "google_calendar_id": None, "last_sync_at": None}
    return {"status": acc.status, "connected": acc.status == "connected",
            "auto_sync_enabled": acc.auto_sync_enabled,
            "google_account_email": acc.google_account_email,
            "google_calendar_id": acc.google_calendar_id,
            "last_sync_at": acc.last_sync_at.isoformat() if acc.last_sync_at else None}


@router.get("/connect/start")
def google_connect_start(user: UserAccount = Depends(require_permission(Permission.crm_manage))):
    client_id, _secret, redirect = _cfg()
    if not client_id:
        raise HTTPException(status_code=503, detail="GOOGLE_CLIENT_ID not configured")
    params = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": GOOGLE_SCOPE, "access_type": "offline", "prompt": "consent",
        "state": user.company_id,
    })
    return {"authorization_url": f"{GOOGLE_AUTH_URI}?{params}"}

@router.get("/callback")
def google_callback(code: str = Query(None), state: str = Query(None),
                    error: str = Query(None),
                    repository: InMemorySecretaryRepository = Depends(get_repository)):
    if error or not code or not state:
        return HTMLResponse("<h3>Google propojeni se nezdarilo. Muzete zavrit okno.</h3>", status_code=400)
    client_id, client_secret, redirect = _cfg()
    data = urllib.parse.urlencode({
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    }).encode()
    try:
        req = urllib.request.Request(GOOGLE_TOKEN_URI, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            tok = _json.loads(resp.read().decode())
    except Exception:
        return HTMLResponse("<h3>Vymena tokenu selhala. Zkuste to znovu.</h3>", status_code=502)
    import uuid as _uuid
    try:
        existing = repository.get_google_account(state)
        now = datetime.now(timezone.utc)
        acc = existing or GoogleCalendarAccount(id=str(_uuid.uuid4()), company_id=state, created_at=now, updated_at=now)
        acc.access_token = tok.get("access_token")
        if tok.get("refresh_token"):
            acc.refresh_token = tok.get("refresh_token")
        acc.token_expires_at = now + timedelta(seconds=tok.get("expires_in", 3600))
        acc.scope = tok.get("scope", GOOGLE_SCOPE)
        acc.status = "connected"
        repository.upsert_google_account(acc)
    except Exception as exc:
        import traceback
        return HTMLResponse(f"<h3>Ulozeni tokenu selhalo: {exc}</h3><pre>{traceback.format_exc()}</pre>", status_code=500)
    return HTMLResponse("<h3>Google Calendar je propojen. Muzete zavrit okno a vratit se do aplikace.</h3>")

@router.post("/disconnect")
def google_disconnect(user: UserAccount = Depends(require_permission(Permission.crm_manage)),
                      repository: InMemorySecretaryRepository = Depends(get_repository)):
    acc = repository.get_google_account(user.company_id)
    if not acc:
        return {"status": "disconnected"}
    acc.access_token = None
    acc.refresh_token = None
    acc.token_expires_at = None
    acc.status = "disconnected"
    acc.auto_sync_enabled = False
    repository.upsert_google_account(acc)
    return {"status": "disconnected"}


def _valid_access_token(repository, acc):
    if not acc or acc.status != "connected":
        return None
    if acc.token_expires_at and acc.token_expires_at <= datetime.now(timezone.utc) + timedelta(seconds=60):
        return _refresh_access_token(repository, acc)
    return acc.access_token or _refresh_access_token(repository, acc)

@router.get("/calendars")
def google_calendars(user: UserAccount = Depends(require_permission(Permission.crm_manage)),
                     repository: InMemorySecretaryRepository = Depends(get_repository)):
    acc = repository.get_google_account(user.company_id)
    token = _valid_access_token(repository, acc)
    if not token:
        raise HTTPException(status_code=409, detail="Google not connected")
    try:
        req = urllib.request.Request("https://www.googleapis.com/calendar/v3/users/me/calendarList",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = _json.loads(resp.read().decode())
    except Exception:
        raise HTTPException(status_code=502, detail="Google calendarList failed")
    items = [{"id": it.get("id"), "summary": it.get("summary"), "primary": it.get("primary", False)}
             for it in payload.get("items", [])]
    return {"calendars": items}


class SelectedCalendarBody(BaseModel):
    google_calendar_id: str


@router.put("/selected-calendar")
def google_select_calendar(body: SelectedCalendarBody,
                           user: UserAccount = Depends(require_permission(Permission.crm_manage)),
                           repository: InMemorySecretaryRepository = Depends(get_repository)):
    acc = repository.get_google_account(user.company_id)
    if not acc:
        raise HTTPException(status_code=409, detail="Google not connected")
    acc.google_calendar_id = body.google_calendar_id
    repository.upsert_google_account(acc)
    return {"google_calendar_id": acc.google_calendar_id}

def _push_event_to_google(token: str, calendar_id: str, ev) -> str | None:
    """Create one event in Google Calendar. Returns the Google event id or None.
    One-way push (backend -> Google). Tokens/PII are never logged."""
    body = {
        "summary": ev.title or "(bez nazvu)",
        "description": ev.description or "",
        "location": ev.location or "",
    }
    if ev.all_day:
        body["start"] = {"date": ev.start_at.date().isoformat()}
        end_date = (ev.end_at or ev.start_at).date().isoformat()
        body["end"] = {"date": end_date}
    else:
        body["start"] = {"dateTime": ev.start_at.isoformat()}
        end_dt = ev.end_at or (ev.start_at + timedelta(hours=1))
        body["end"] = {"dateTime": end_dt.isoformat()}
    url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(calendar_id)}/events"
    data = _json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            created = _json.loads(resp.read().decode())
        return created.get("id")
    except Exception:
        return None


@router.post("/sync")
def google_sync(user: UserAccount = Depends(require_permission(Permission.crm_manage)),
                repository: InMemorySecretaryRepository = Depends(get_repository)):
    acc = repository.get_google_account(user.company_id)
    token = _valid_access_token(repository, acc)
    if not token:
        raise HTTPException(status_code=409, detail="Google not connected")
    if not acc.google_calendar_id:
        raise HTTPException(status_code=400, detail="No calendar selected")

    # G5: one-way push backend -> Google. Events already mapped are skipped
    # (dedup via clean_google_calendar_mappings), so re-running sync is safe.
    backend_events = repository.list_calendar_events(user.company_id)
    pushed = 0
    skipped = 0
    failed = 0
    for ev in backend_events:
        existing = repository.get_google_mapping(user.company_id, ev.id)
        if existing:
            skipped += 1
            continue
        gid = _push_event_to_google(token, acc.google_calendar_id, ev)
        if gid:
            repository.set_google_mapping(user.company_id, ev.id, gid)
            repository.add_google_sync_log(user.company_id, "push", "create", "ok",
                                           backend_event_id=ev.id, google_event_id=gid)
            pushed += 1
        else:
            repository.add_google_sync_log(user.company_id, "push", "create", "error",
                                           backend_event_id=ev.id)
            failed += 1

    acc.last_sync_at = datetime.now(timezone.utc)
    repository.upsert_google_account(acc)
    repository.add_google_sync_log(user.company_id, "manual", "sync_finished", "ok",
                                   detail=f"pushed={pushed} skipped={skipped} failed={failed}")
    return {"status": "ok", "backend_events": len(backend_events),
            "pushed": pushed, "skipped": skipped, "failed": failed,
            "calendar_id": acc.google_calendar_id, "synced_at": acc.last_sync_at.isoformat()}


@router.get("/sync-log")
def google_sync_log(user: UserAccount = Depends(require_permission(Permission.crm_manage)),
                    repository: InMemorySecretaryRepository = Depends(get_repository)):
    rows = repository.list_google_sync_log(user.company_id, limit=50)
    out = []
    for r in rows:
        ca = r.get("created_at")
        out.append({"direction": r.get("direction"), "action": r.get("action"),
                    "status": r.get("status"), "detail": r.get("detail"),
                    "created_at": ca.isoformat() if hasattr(ca, "isoformat") else ca})
    return {"entries": out}
