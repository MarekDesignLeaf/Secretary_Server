"""Voice weather intent (Open-Meteo, mocked)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import voice_intents as vi
from secretary_clean.core import weather as w


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company", json={"legal_name": "Weather Ltd"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def _mock_weather(monkeypatch, place_seen):
    def geocode(name):
        place_seen.append(name)
        return {"name": name.title(), "latitude": 50.0, "longitude": 14.0, "country": "CZ"}
    daily = [
        {"date": "2026-06-15", "tmin": 12, "tmax": 22, "code": 1, "precip_prob": 10, "wind": 14},
        {"date": "2026-06-16", "tmin": 13, "tmax": 24, "code": 61, "precip_prob": 60, "wind": 20},
    ] + [{"date": f"2026-06-{17+i}", "tmin": 11, "tmax": 20, "code": 3, "precip_prob": 30, "wind": 12}
         for i in range(5)]
    hourly = [{"time": f"2026-06-15T{h:02d}:00", "temp": 15 + h % 5, "code": 2,
               "precip_prob": h * 3} for h in range(12)]
    monkeypatch.setattr(w, "geocode_place", geocode)
    monkeypatch.setattr(w, "fetch_daily", lambda lat, lon, days=7: daily[:days])
    monkeypatch.setattr(w, "fetch_hourly", lambda lat, lon, hours=12: hourly[:hours])


def test_weather_intent_parses_variants():
    assert vi.parse_intent("jaké je počasí").intent == "weather.get"
    assert vi.parse_intent("počasí zítra").entities["date"] is not None
    assert vi.parse_intent("počasí na celý týden").entities["week"] is True
    assert vi.parse_intent("hodinová předpověď").entities["hourly"] is True
    assert vi.parse_intent("počasí v Praze").entities["place"] == "Praze"


def test_weather_today_default_location(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    seen = []
    _mock_weather(monkeypatch, seen)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "jaké bude počasí"}).json()
    assert out["executed"] is True
    assert out["resolved_intent"] == "weather.get"
    assert "°C" in out["message"]


def test_weather_week_and_hourly_and_place(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    seen = []
    _mock_weather(monkeypatch, seen)

    week = client.post("/api/v1/voice/execute", headers=headers,
                       json={"utterance": "počasí na celý týden"}).json()
    assert "Týdenní předpověď" in week["message"]

    hourly = client.post("/api/v1/voice/execute", headers=headers,
                         json={"utterance": "hodinová předpověď"}).json()
    assert "Hodinová předpověď" in hourly["message"]

    place = client.post("/api/v1/voice/execute", headers=headers,
                        json={"utterance": "počasí v Praze"}).json()
    assert "Praze" in place["message"]
    assert "praze" in [s.lower() for s in seen]


def test_weather_unknown_place(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.setattr(w, "geocode_place", lambda name: None)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "počasí v Xyzwville"}).json()
    assert out["status"] == "error"
    assert "Nenašla" in out["message"]
