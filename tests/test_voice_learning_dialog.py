"""Phase 3 — the 'teach me this command' dialog and pending-learning state machine."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "Dialog Ltd",
                                "default_internal_language_code": "cs-CZ"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def _say(client, headers, utterance, pending_id=None):
    body = {"utterance": utterance}
    if pending_id:
        body["pending_action_id"] = pending_id
    return client.post("/api/v1/voice/execute", headers=headers, json=body).json()


def test_full_learn_loop_creates_active_alias(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    # Unknown command opens the dialog.
    out = _say(client, headers, "ňiňiňi blé")
    assert out["status"] == "needs_more_info"
    pid = out["pending_action_id"]
    assert "nerozum" in out["message"].lower()

    # Answer with a known command → alias becomes ACTIVE.
    done = _say(client, headers, "vytvoř klienta", pid)
    assert done["executed"] is True
    assert done["action"] == "alias.create"

    # The phrase now resolves directly.
    res = client.post("/api/v1/voice/learning/resolve", headers=headers,
                      json={"utterance": "ňiňiňi blé"}).json()
    assert res["intent"] == "client.create"
    assert res["source"] == "USER_ALIAS"


def test_learn_dialog_cancelled_by_omyl(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = _say(client, headers, "abrakadabra zorp")
    pid = out["pending_action_id"]
    cancelled = _say(client, headers, "omyl", pid)
    assert cancelled["status"] == "cancelled"
    # Nothing was saved.
    assert client.get("/api/v1/voice/aliases", headers=headers).json()["aliases"] == []


def test_learn_dialog_cancelled_by_neplatny_prikaz(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = _say(client, headers, "kchm kchm flux")
    pid = out["pending_action_id"]
    cancelled = _say(client, headers, "neplatný příkaz", pid)
    assert cancelled["status"] == "cancelled"
    assert client.get("/api/v1/voice/aliases", headers=headers).json()["aliases"] == []


def test_learn_dialog_gives_up_after_two_unclear_answers(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = _say(client, headers, "blurp glorp")
    pid = out["pending_action_id"]
    # First unclear answer → re-ask.
    retry = _say(client, headers, "úúplně nesmyslná odpověď xyz", pid)
    assert retry["status"] == "needs_more_info"
    # Second unclear answer → give up.
    giveup = _say(client, headers, "zase nic smysluplného qwe", pid)
    assert giveup["status"] == "cancelled"
    assert client.get("/api/v1/voice/aliases", headers=headers).json()["aliases"] == []


def test_learn_dialog_pending_when_target_not_implemented(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = _say(client, headers, "kobliha expres blafuj")
    pid = out["pending_action_id"]
    done = _say(client, headers, "pošli fakturu", pid)
    assert done["executed"] is True
    aliases = client.get("/api/v1/voice/aliases", headers=headers).json()["aliases"]
    learned = [a for a in aliases if a["target_intent"] == "invoice.send"]
    assert learned and learned[0]["status"] == "PENDING"
