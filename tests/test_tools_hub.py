"""Tools Hub tiles endpoint (Utilities screen)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _logged_in(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    company = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "Tools Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login", json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def test_hub_tiles_serves_nature_tools(monkeypatch):
    c, h = _logged_in(monkeypatch)
    res = c.get("/api/v1/tools/hub-tiles?tenant_id=1", headers=h)
    assert res.status_code == 200
    body = res.json()
    keys = [t["tile_key"] for t in body["tiles"]]
    assert keys == ["identify", "health", "mushroom"]
    assert body["count"] == 3
    first = body["tiles"][0]
    assert first["tile_title_cs"] == "Rozpoznání rostlin"
    assert first["icon"] == "Eco"


def test_hub_tiles_requires_auth(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    assert c.get("/api/v1/tools/hub-tiles").status_code == 401
