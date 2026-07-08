"""New endpoints added after the wiring audit: job photos, /crm/photos,
communications import, batch invoice-from-work-reports."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    co = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "New Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": co["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login",
                 json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def _make_job(c, h):
    return c.post("/api/v1/crm/jobs", headers=h, json={"job_title": "Plot Novák"}).json()


def test_job_photos_add_list(monkeypatch):
    c, h = _client(monkeypatch)
    job = _make_job(c, h)
    r = c.post(f"/api/v1/crm/jobs/{job['id']}/photos", headers=h,
               json={"url": "https://x/p1.jpg", "caption": "před"})
    assert r.status_code == 201
    assert r.json()["url"].endswith("p1.jpg")
    photos = c.get(f"/api/v1/crm/jobs/{job['id']}/photos", headers=h).json()
    assert len(photos) == 1 and photos[0]["caption"] == "před"


def test_crm_photos_aggregate_and_add(monkeypatch):
    c, h = _client(monkeypatch)
    job = _make_job(c, h)
    c.post("/api/v1/crm/photos", headers=h,
           json={"job_id": job["id"], "url": "https://x/a.jpg"})
    allp = c.get("/api/v1/crm/photos", headers=h).json()
    assert len(allp) == 1 and allp[0]["parent_id"] == job["id"]
    # missing target → 422
    assert c.post("/api/v1/crm/photos", headers=h, json={"url": "x"}).status_code == 422


def test_communications_import(monkeypatch):
    c, h = _client(monkeypatch)
    r = c.post("/api/v1/crm/communications/import", headers=h, json={"items": [
        {"message_summary": "hovor 1", "type": "call", "direction": "out"},
        {"message_summary": "hovor 2", "type": "call", "direction": "out"},
        {"nonsense": "no name"},  # skipped, not fatal
    ]})
    assert r.status_code == 201
    assert r.json()["imported_count"] == 2
    assert len(c.get("/api/v1/crm/communications", headers=h).json()) == 2


def test_batch_invoice_from_work_reports(monkeypatch):
    c, h = _client(monkeypatch)
    wr1 = c.post("/api/v1/work-reports", headers=h, json={
        "client_name": "Novák",
        "workers": [{"worker_name": "A", "hours": 3, "hourly_rate": 20}]}).json()
    wr2 = c.post("/api/v1/work-reports", headers=h, json={
        "client_name": "Svoboda",
        "workers": [{"worker_name": "B", "hours": 2, "hourly_rate": 25}]}).json()
    r = c.post("/api/v1/crm/invoices/batch-from-work-reports", headers=h,
               json={"work_report_ids": [wr1["id"], wr2["id"], "does-not-exist"]})
    assert r.status_code == 201
    body = r.json()
    assert body["created_count"] == 2
    assert body["failed_count"] == 1
    assert body["errors"][0]["error"] == "not_found"
