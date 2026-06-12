"""Smoke test FAIL fixes: client matching, 'nový klient', cancel everywhere."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Voice Session Ltd"},
    ).json()
    client.post(
        "/api/v1/bootstrap/first-admin",
        json={
            "company_id": company["id"],
            "email": "owner@example.com",
            "display_name": "Marek Novák",
            "password": "very-secure-password",
        },
    )
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "very-secure-password"},
    ).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def _start_session(client, headers, language="cs"):
    res = client.post("/api/v1/voice/session/start", headers=headers,
                      json={"language": language})
    assert res.status_code == 200
    return res.json()["session_id"]


def _say(client, headers, sid, text):
    res = client.post("/api/v1/voice/session/input", headers=headers,
                      json={"session_id": sid, "text": text})
    assert res.status_code == 200
    return res.json()


def test_client_found_despite_diacritics_and_hyphen(monkeypatch):
    """FAIL #1: STT says 'smoke novak', stored client is 'SMOKE-Novák'."""
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers, json={"name": "SMOKE-Novák"})

    sid = _start_session(client, headers)
    out = _say(client, headers, sid, "smoke novak")
    assert out["step"] == "date"
    assert "SMOKE-Novák" in out["prompt"]


def test_new_client_without_diacritics(monkeypatch):
    """FAIL #2: 'novy klient' (no diacritics) must start the new-client branch."""
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    sid = _start_session(client, headers)
    out = _say(client, headers, sid, "novy klient")
    assert out["step"] == "client_name"

    out = _say(client, headers, sid, "Pan Dvořák")
    assert out["step"] == "date"
    assert "Pan Dvořák" in out["prompt"]


def test_new_client_with_inline_name_keeps_diacritics(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    sid = _start_session(client, headers)
    out = _say(client, headers, sid, "nový klient Růžička")
    assert out["step"] == "date"
    assert "Růžička" in out["prompt"]


def test_cancel_works_at_every_step(monkeypatch):
    """FAIL #3: 'zrušit' must abort the dialog at any step, nothing saved."""
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers, json={"name": "SMOKE-Novák"})

    # Cancel at the very first (client) step.
    sid = _start_session(client, headers)
    out = _say(client, headers, sid, "zrušit")
    assert out["step"] == "done"
    assert "neulož" in out["prompt"].lower() or "zrušeno" in out["prompt"].lower()

    # Cancel mid-dialog (date step), with diacritics-free variant + filler words.
    sid = _start_session(client, headers)
    assert _say(client, headers, sid, "smoke novak")["step"] == "date"
    out = _say(client, headers, sid, "tak to zrus")
    assert out["step"] == "done"

    # Cancelled session refuses further input.
    res = client.post("/api/v1/voice/session/input", headers=headers,
                      json={"session_id": sid, "text": "dnes"})
    assert res.status_code == 409

    # Nothing was saved.
    assert client.get("/api/v1/work-reports", headers=headers).json() == []


def test_dictated_note_mentioning_stop_is_not_cancelled(monkeypatch):
    """Long dictated text containing a cancel word must NOT abort the dialog."""
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers, json={"name": "SMOKE-Novák"})
    sid = _start_session(client, headers)
    _say(client, headers, sid, "smoke novak")
    _say(client, headers, sid, "dnes")
    _say(client, headers, sid, "Marek")
    _say(client, headers, sid, "hotovo")
    _say(client, headers, sid, "8")
    out = _say(client, headers, sid,
               "klient chtel jeste zrusit stary zahon a vysadit novy travnik u plotu")
    assert out["step"] == "summary"


def test_pending_voice_action_cancelled_by_omyl(monkeypatch):
    """'omyl' must cancel a multi-turn /voice/execute pending action."""
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    first = client.post("/api/v1/voice/execute", headers=headers,
                        json={"utterance": "vytvoř schůzku"}).json()
    assert first["status"] == "needs_more_info"
    pid = first["pending_action_id"]
    assert pid

    cancelled = client.post("/api/v1/voice/execute", headers=headers,
                            json={"utterance": "omyl", "pending_action_id": pid}).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["executed"] is False
