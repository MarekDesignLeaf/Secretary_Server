"""Work report activities -> invoice line items at their set prices."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company", json={"legal_name": "WR Act Ltd"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_invoice_reflects_activities_and_their_prices(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    crm = client.post("/api/v1/crm/clients", headers=headers,
                      json={"name": "Garden Customer"}).json()

    wr = client.post("/api/v1/work-reports", headers=headers, json={
        "client_id": crm["id"], "work_date": "2026-06-15",
        "activities": [
            # rate sent by the app (tenant override / Oxfordshire default)
            {"activity_code": "garden.hedge", "name": "Hedge trimming",
             "quantity": 4, "rate": 31, "pricing_method": "hodinova_sazba"},
            {"activity_code": "garden.waste", "name": "Green waste removal",
             "quantity": 2, "rate": 55, "pricing_method": "cena_za_bulk_bag"},
        ],
        "workers": [{"worker_name": "Marek", "hours": 0, "hourly_rate": 0}],
    })
    assert wr.status_code == 201
    wr_id = wr.json()["id"]

    inv = client.post("/api/v1/crm/invoices/from-work-report", headers=headers,
                      json={"work_report_id": wr_id})
    assert inv.status_code == 201, inv.text
    body = inv.json()

    # 4*31 + 2*55 = 124 + 110 = 234
    assert body["grand_total"] == 234.0
    descs = {li["description"]: li for li in body["line_items"]}
    assert descs["Hedge trimming"]["subtotal"] == 124.0
    assert descs["Hedge trimming"]["activity_code"] == "garden.hedge"
    assert descs["Green waste removal"]["subtotal"] == 110.0
    assert body["profit"] == 234.0  # no worker cost -> profit == revenue
    assert body["profit_margin"] == 100.0


def test_activity_without_rate_warns(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    crm = client.post("/api/v1/crm/clients", headers=headers,
                      json={"name": "X"}).json()
    wr = client.post("/api/v1/work-reports", headers=headers, json={
        "client_id": crm["id"], "work_date": "2026-06-15",
        "activities": [{"activity_code": "garden.unknown", "name": "Mystery job",
                        "quantity": 1, "rate": 0}],
    }).json()
    inv = client.post("/api/v1/crm/invoices/from-work-report", headers=headers,
                      json={"work_report_id": wr["id"]}).json()
    assert inv["grand_total"] == 0.0
    assert any("Mystery job" in w for w in inv["pricing_warnings"])
