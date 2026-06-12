"""POST /crm/invoices/from-work-report — enriched response (gap report §9, step 2)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Invoice Test Ltd"},
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


def test_invoice_from_work_report_returns_android_fields(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    crm_client = client.post(
        "/api/v1/crm/clients", headers=headers, json={"name": "Profit Customer"},
    ).json()

    wr = client.post(
        "/api/v1/work-reports",
        headers=headers,
        json={
            "client_id": crm_client["id"],
            "work_date": "2026-06-12",
            "workers": [
                {"worker_name": "Marek", "hours": 10, "hourly_rate": 40, "hourly_cost": 15},
                {"worker_name": "Pomocnik", "hours": 5, "hourly_rate": 20},
            ],
        },
    )
    assert wr.status_code == 201
    wr_id = wr.json()["id"]

    res = client.post(
        "/api/v1/crm/invoices/from-work-report",
        headers=headers,
        json={"work_report_id": wr_id},
    )
    assert res.status_code == 201
    inv = res.json()

    # 10h x 40 + 5h x 20 = 500 revenue; cost 10h x 15 = 150 (missing cost = 0)
    assert inv["grand_total"] == 500.0
    assert inv["total_cost"] == 150.0
    assert inv["profit"] == 350.0
    assert inv["profit_margin"] == 70.0
    assert inv["invoice_number"]
    assert inv["client_id"] == crm_client["id"]
    assert inv["currency"] == "GBP"
    assert inv["status"] == "draft"
    assert len(inv["line_items"]) == 2
    assert inv["work_report_id"] == wr_id

    # Invoice list must show the same grand_total (previously 0.0 — shapes fallback).
    listed = client.get("/api/v1/crm/invoices", headers=headers).json()
    assert [i["grand_total"] for i in listed if i["id"] == inv["id"]] == [500.0]

    # Second invoicing of the same work report must be refused.
    again = client.post(
        "/api/v1/crm/invoices/from-work-report",
        headers=headers,
        json={"work_report_id": wr_id},
    )
    assert again.status_code == 409


def test_invoice_from_missing_work_report_is_404(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    res = client.post(
        "/api/v1/crm/invoices/from-work-report",
        headers=headers,
        json={"work_report_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert res.status_code == 404
