"""Voice responses are localized to the team language (cs native, en/pl translated)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import translation as tr


def _client_with_internal_lang(monkeypatch, internal_lang):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company", json={
        "legal_name": "Lang Ltd",
        "default_internal_language_code": internal_lang,
        "default_customer_language_code": "en-GB"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password",
        "preferred_language_code": internal_lang})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_czech_response_is_native(monkeypatch):
    client, headers = _client_with_internal_lang(monkeypatch, "cs-CZ")
    # Translation must NOT be called for Czech.
    monkeypatch.setattr(tr, "translate_text",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("translated cs")))
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř úkol zavolat dodavateli"}).json()
    assert out["executed"] is True
    assert "úkol" in out["message"].lower()


def test_english_response_is_translated(monkeypatch):
    client, headers = _client_with_internal_lang(monkeypatch, "en-GB")
    monkeypatch.setattr(tr, "is_configured", lambda: True)
    monkeypatch.setattr(tr, "translate_text",
                        lambda text, target, source=None: (True, f"[{target}] {text}", None))
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř úkol zavolat dodavateli"}).json()
    assert out["executed"] is True
    assert out["message"].startswith("[English] ")


def test_english_falls_back_to_czech_without_translation(monkeypatch):
    client, headers = _client_with_internal_lang(monkeypatch, "en-GB")
    monkeypatch.setattr(tr, "is_configured", lambda: False)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř úkol zavolat dodavateli"}).json()
    # No OpenAI -> graceful: original Czech, never an error.
    assert out["executed"] is True
    assert "úkol" in out["message"].lower()


def test_polish_followup_question_is_translated(monkeypatch):
    client, headers = _client_with_internal_lang(monkeypatch, "pl-PL")
    monkeypatch.setattr(tr, "is_configured", lambda: True)
    monkeypatch.setattr(tr, "translate_text",
                        lambda text, target, source=None: (True, f"[{target}] {text}", None))
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř schůzku"}).json()
    assert out["status"] == "needs_more_info"
    assert out["question"].startswith("[Polish] ")
    assert out["message"].startswith("[Polish] ")


def test_czech_company_with_english_user_pref_stays_czech(monkeypatch):
    """Regression: company internal language is Czech, but the owner's personal
    preferred_language_code is en-GB. The assistant must answer in Czech (the
    internal language) and never translate — talking to internal staff with no
    client in context uses the internal language, not the user's stray pref."""
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company", json={
        "legal_name": "Czech Co",
        "default_internal_language_code": "cs-CZ",
        "default_customer_language_code": "en-GB"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "marek@example.com",
        "display_name": "Marek", "password": "very-secure-password",
        "preferred_language_code": "en-GB"})  # personal pref differs from internal
    tokens = client.post("/api/v1/auth/login", json={
        "email": "marek@example.com", "password": "very-secure-password"}).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    monkeypatch.setattr(tr, "translate_text",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not translate")))
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř úkol zavolat dodavateli"}).json()
    assert out["executed"] is True
    assert "úkol" in out["message"].lower()
