"""POST/GET /crm/jobs/{id}/audit (gap report §9, step 3)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Audit Test Ltd"},
    ).json()
    client.post(
        "/api/v1/bootstrap/first-admin",
        json={
            "company_id": company["id"],
            "email": "owner@example.com",
            "display_name": "Owner",
            "password": "very-secure-password",
        },
    )
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "very-secure-password"},
    ).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_job_audit_roundtrip(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    crm_client = client.post(
        "/api/v1/crm/clients", headers=headers, json={"name": "Audit Customer"},
    ).json()
    job = client.post(
        "/api/v1/crm/jobs", headers=headers,
        json={"title": "Fence repair", "client_id": crm_client["id"]},
    ).json()
    job_id = job["id"]

    assert client.get(f"/api/v1/crm/jobs/{job_id}/audit", headers=headers).json() == []

    res = client.post(
        f"/api/v1/crm/jobs/{job_id}/audit", headers=headers,
        json={"job_id": job_id, "action_type": "status_change",
              "description": "Status changed to in_progress"},
    )
    assert res.status_code == 201
    entry = res.json()
    assert entry["job_id"] == job_id
    assert entry["action_type"] == "status_change"
    assert entry["user_name"] == "Owner"
    assert entry["id"] and entry["created_at"]

    listed = client.get(f"/api/v1/crm/jobs/{job_id}/audit", headers=headers).json()
    assert [e["id"] for e in listed] == [entry["id"]]

    detail = client.get(f"/api/v1/crm/jobs/{job_id}", headers=headers).json()
    assert [e["id"] for e in detail["audit_log"]] == [entry["id"]]

    # The entry must also land in the company-wide admin activity log.
    activity = client.get("/api/v1/admin/activity-log", headers=headers).json()
    assert any(a["entity_type"] == "job" and a["entity_id"] == job_id
               and a["action"] == "status_change" for a in activity)


def test_job_audit_unknown_job_is_404(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    res = client.post(
        "/api/v1/crm/jobs/00000000-0000-0000-0000-000000000000/audit",
        headers=headers, json={"action_type": "x", "description": "y"},
    )
    assert res.status_code == 404
