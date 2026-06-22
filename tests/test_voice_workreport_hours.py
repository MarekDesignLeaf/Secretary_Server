"""Work-report hours must accept spoken number words, not only digits.

Regression: saying "osm" (eight) or "osm a půl" (8.5) was rejected with
"Neplatné číslo" because the parser only matched digits."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.api.routes.voice_session import _parse_hours


# ── unit: _parse_hours ────────────────────────────────────────────────────────
def test_parse_hours_digits():
    assert _parse_hours("8") == 8.0
    assert _parse_hours("8.5") == 8.5
    assert _parse_hours("8,5") == 8.5
    assert _parse_hours("8 hodin") == 8.0


def test_parse_hours_czech_words():
    assert _parse_hours("osm") == 8.0
    assert _parse_hours("osm hodin") == 8.0
    assert _parse_hours("dvanáct") == 12.0
    assert _parse_hours("osm a půl") == 8.5
    assert _parse_hours("půl") == 0.5
    assert _parse_hours("čtvrt") == 0.25
    assert _parse_hours("tři čtvrtě") == 0.75
    assert _parse_hours("dvacet čtyři") == 24.0


def test_parse_hours_english_and_polish_words():
    assert _parse_hours("eight") == 8.0
    assert _parse_hours("eight and a half") == 8.5
    assert _parse_hours("ten hours") == 10.0
    assert _parse_hours("osiem") == 8.0       # pl
    assert _parse_hours("dziesiec") == 10.0   # pl


def test_parse_hours_rejects_non_numbers():
    assert _parse_hours("ahoj") is None
    assert _parse_hours("") is None


# ── integration: the work-report dialog accepts a spoken-word hour count ───────
def _bootstrap(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "WR Ltd",
                                "default_internal_language_code": "cs-CZ"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Marek Novák", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def _say(client, headers, sid, text):
    r = client.post("/api/v1/voice/session/input", headers=headers,
                    json={"session_id": sid, "text": text})
    assert r.status_code == 200
    return r.json()


def test_workreport_accepts_spoken_word_hours(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers, json={"name": "SMOKE-Novák"})

    sid = client.post("/api/v1/voice/session/start", headers=headers,
                      json={"language": "cs"}).json()["session_id"]
    assert _say(client, headers, sid, "smoke novak")["step"] == "date"
    assert _say(client, headers, sid, "dnes")["step"] == "workers"
    _say(client, headers, sid, "Marek")
    assert _say(client, headers, sid, "hotovo")["step"] == "total_hours"

    # The fix: "osm" (eight) is accepted instead of "Neplatné číslo".
    out = _say(client, headers, sid, "osm")
    assert out["step"] == "notes", out
    assert "neplatn" not in out["prompt"].lower()
