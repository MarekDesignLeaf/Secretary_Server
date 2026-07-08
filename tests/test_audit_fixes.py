"""Regression tests for the audit fixes:
  1. voice calendar.sync delegates to run_reconcile (no broken _push_* call)
  2. POST /voice/learn-alias actually persists the alias
"""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.api.routes import google_calendar as gc
from secretary_clean.voice2 import handlers
from secretary_clean.voice2.handlers import Ctx


def _client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    co = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "Audit Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": co["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login",
                 json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def test_learn_alias_persists(monkeypatch):
    c, h = _client(monkeypatch)
    out = c.post("/api/v1/voice/learn-alias", headers=h,
                 json={"phrase": "novej zákazník bob", "answer": "vytvoř klienta"}).json()
    assert out["status"] == "saved"
    assert out["target_intent"] == "client.create"
    assert out.get("alias_id")
    # The alias must now be listable and drive resolution.
    aliases = c.get("/api/v1/voice/aliases", headers=h).json()["aliases"]
    assert any(a["normalized_phrase"] == "novej zakaznik bob"
               and a["target_intent"] == "client.create" for a in aliases)


def test_calendar_sync_handler_uses_run_reconcile(monkeypatch):
    # calendar_sync must call run_reconcile (the one real path), never a
    # removed helper. Stub token + reconcile so no real Google call happens.
    from secretary_clean.core.repository import InMemorySecretaryRepository
    from secretary_clean.core.models import GoogleCalendarAccount
    from datetime import datetime, timezone

    repo = InMemorySecretaryRepository()
    now = datetime.now(timezone.utc)
    repo.upsert_google_account(GoogleCalendarAccount(
        id="g1", company_id="c1", status="connected",
        google_calendar_id="primary", created_at=now, updated_at=now))

    monkeypatch.setattr(gc, "_valid_access_token", lambda repository, acc: "tok")
    called = {}
    monkeypatch.setattr(gc, "run_reconcile", lambda repository, acc, token: called.setdefault(
        "stats", {"pushed": 2, "updated": 0, "pushed_deleted": 0, "pulled": 5,
                  "pulled_deleted": 0, "skipped": 0, "failed": 0}) or called["stats"])

    class U:
        company_id = "c1"; id = "u1"
    res = handlers.calendar_sync(Ctx(user=U(), repository=repo, utterance="synchronizuj kalendář"),
                                 {})
    assert res.executed is True
    assert "stats" in called                       # run_reconcile was invoked
    assert res.data["pulled"] == 5 and res.data["pushed"] == 2


def test_no_push_event_to_google_symbol():
    # The removed helper must not be referenced anywhere in voice2.
    assert not hasattr(gc, "_push_event_to_google")
