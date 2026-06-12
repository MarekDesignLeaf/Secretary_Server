"""Photo recognition routes (plants / disease / mushrooms)."""
import io

from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import nature_recognition as nr


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company", json={"legal_name": "Nature Ltd"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def _img():
    return ("photo.jpg", io.BytesIO(b"\xff\xd8\xff\xe0fakejpeg"), "image/jpeg")


def test_plant_identify_unconfigured_is_graceful(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.setattr(nr, "is_configured", lambda: False)
    res = client.post("/api/v1/plants/identify", headers=headers,
                      files={"images": _img()}, data={"language": "cs"})
    assert res.status_code == 200
    body = res.json()
    assert "není" in body["spoken_summary"].lower() or "not configured" in body["spoken_summary"].lower()


def test_plant_identify_with_vision(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.setattr(nr, "is_configured", lambda: True)
    monkeypatch.setattr(nr, "_vision", lambda *a, **k: {
        "display_name": "Levandule", "scientific_name": "Lavandula angustifolia",
        "score": 0.93, "guidance": "Slunce a suchá půda.",
        "spoken_summary": "Na fotce je levandule, jistota 93 %."})
    res = client.post("/api/v1/plants/identify", headers=headers,
                      files={"images": _img()}, data={"language": "cs"}).json()
    assert res["display_name"] == "Levandule"
    assert res["score"] == 0.93
    assert "levandule" in res["spoken_summary"].lower()

    hist = client.get("/api/v1/nature/history", headers=headers).json()
    assert hist and hist[0]["display_name"] == "Levandule"


def test_mushroom_uses_kindwise_and_warns(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.setenv("MUSHROOM_ID_API_KEY", "test-key")
    monkeypatch.setattr(nr, "is_configured", lambda: False)  # no OpenAI -> guidance==spoken

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"result": {"classification": {"suggestions": [{
                "name": "Boletus edulis", "probability": 0.82,
                "details": {
                    "common_names": ["Hřib smrkový"],
                    "edibility": "edible", "psychoactive": True,
                    "look_alikes": ["Tylopilus felleus"],
                    "description": "Choice edible bolete.",
                    "taxonomy": {"family": "Boletaceae", "genus": "Boletus"},
                }}]}}}

    import secretary_clean.core.nature_recognition as mod
    captured = {}
    def _post(url, **kw):
        captured["url"] = url
        captured["api_key"] = kw["headers"]["Api-Key"]
        return _Resp()
    monkeypatch.setattr(mod.httpx if hasattr(mod, "httpx") else __import__("httpx"),
                        "post", _post, raising=False)
    import httpx
    monkeypatch.setattr(httpx, "post", _post)

    res = client.post("/api/v1/mushrooms/identify", headers=headers,
                      files={"images": _img()}, data={"language": "cs"}).json()
    assert "kindwise" in captured["url"]
    assert captured["api_key"] == "test-key"
    assert res["database"] == "mushroom.id"
    assert res["display_name"] == "Hřib smrkový"
    assert res["scientific_name"] == "Boletus edulis"
    assert res["edibility"] == "edible"          # real data from Kindwise
    assert res["psychoactive"] is True
    assert "Boletaceae" == res["family"]
    # Safety: spoken summary always warns, never confirms edibility.
    assert "nepotvrzuj" in res["spoken_summary"].lower()


def test_mushroom_unconfigured_is_graceful(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.delenv("MUSHROOM_ID_API_KEY", raising=False)
    monkeypatch.delenv("KINDWISE_MUSHROOM_API_KEY", raising=False)
    res = client.post("/api/v1/mushrooms/identify", headers=headers,
                      files={"images": _img()}, data={"language": "cs"})
    assert res.status_code == 200
    assert "MUSHROOM_ID_API_KEY" in res.json()["spoken_summary"]


def test_health_assessment(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.setattr(nr, "is_configured", lambda: True)
    monkeypatch.setattr(nr, "_vision", lambda *a, **k: {
        "is_healthy": False, "health_probability": 0.4,
        "top_issue_name": "Padlí", "top_issue_probability": 0.7,
        "spoken_summary": "Rostlina má padlí."})
    res = client.post("/api/v1/plants/health-assessment", headers=headers,
                      files={"images": _img()}, data={"language": "cs"}).json()
    assert res["is_healthy"] is False
    assert res["top_issue_name"] == "Padlí"


def test_recognition_requires_auth(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    assert client.post("/api/v1/plants/identify", files={"images": _img()}).status_code == 401
