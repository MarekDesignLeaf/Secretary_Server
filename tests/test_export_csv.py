"""GET /crm/export/csv (gap report #37)."""
import csv
import io

from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "CSV Test Ltd"},
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


def test_export_csv_contains_clients(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers,
                json={"name": "Zeta Ltd", "phone": "+420777000111",
                      "email_primary": "zeta@example.com"})
    client.post("/api/v1/crm/clients", headers=headers, json={"name": "Alfa Ltd"})

    res = client.get("/api/v1/crm/export/csv", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=export_" in res.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert [r["display_name"] for r in rows] == ["Alfa Ltd", "Zeta Ltd"]
    zeta = rows[1]
    assert zeta["phone_primary"] == "+420777000111"
    assert zeta["email_primary"] == "zeta@example.com"
    assert zeta["id"]


def test_export_csv_requires_auth(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    assert client.get("/api/v1/crm/export/csv").status_code == 401
