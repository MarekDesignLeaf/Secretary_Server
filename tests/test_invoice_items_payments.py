"""Invoice items + payments endpoints (gap report §9, step 4)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Items Test Ltd"},
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


def _make_invoice(client, headers) -> str:
    crm_client = client.post(
        "/api/v1/crm/clients", headers=headers, json={"name": "Items Customer"},
    ).json()
    inv = client.post(
        "/api/v1/crm/invoices", headers=headers,
        json={"invoice_number": "INV-001", "client_id": crm_client["id"]},
    ).json()
    return inv["id"]


def test_invoice_items_crud_recomputes_grand_total(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    inv_id = _make_invoice(client, headers)

    assert client.get(f"/api/v1/crm/invoices/{inv_id}/items", headers=headers).json() == []

    a = client.post(
        f"/api/v1/crm/invoices/{inv_id}/items", headers=headers,
        json={"description": "Labour", "quantity": 10, "unit_price": 40},
    ).json()
    assert a["subtotal"] == 400.0
    assert a["invoice_grand_total"] == 400.0

    b = client.post(
        f"/api/v1/crm/invoices/{inv_id}/items", headers=headers,
        json={"description": "Materials", "quantity": 2, "unit_price": 50},
    ).json()
    assert b["invoice_grand_total"] == 500.0

    items = client.get(f"/api/v1/crm/invoices/{inv_id}/items", headers=headers).json()
    assert len(items) == 2 and all(i["id"] for i in items)

    listed = client.get("/api/v1/crm/invoices", headers=headers).json()
    assert [i["grand_total"] for i in listed if i["id"] == inv_id] == [500.0]

    deleted = client.delete(
        f"/api/v1/crm/invoices/{inv_id}/items/{a['id']}", headers=headers).json()
    assert deleted == {"status": "deleted", "invoice_grand_total": 100.0}

    missing = client.delete(
        f"/api/v1/crm/invoices/{inv_id}/items/{a['id']}", headers=headers)
    assert missing.status_code == 404


def test_invoice_payments_drive_status(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    inv_id = _make_invoice(client, headers)
    client.post(
        f"/api/v1/crm/invoices/{inv_id}/items", headers=headers,
        json={"description": "Labour", "quantity": 10, "unit_price": 40},
    )

    bad = client.post(
        f"/api/v1/crm/invoices/{inv_id}/payments", headers=headers, json={"amount": 0})
    assert bad.status_code == 400

    p1 = client.post(
        f"/api/v1/crm/invoices/{inv_id}/payments", headers=headers,
        json={"amount": 150, "payment_method": "cash"},
    ).json()
    assert p1["total_paid"] == 150.0
    listed = client.get("/api/v1/crm/invoices", headers=headers).json()
    assert [i["status"] for i in listed if i["id"] == inv_id] == ["castecne_uhrazena"]

    p2 = client.post(
        f"/api/v1/crm/invoices/{inv_id}/payments", headers=headers,
        json={"amount": 250},
    ).json()
    assert p2["total_paid"] == 400.0
    listed = client.get("/api/v1/crm/invoices", headers=headers).json()
    assert [i["status"] for i in listed if i["id"] == inv_id] == ["uhrazena"]

    payments = client.get(
        f"/api/v1/crm/invoices/{inv_id}/payments", headers=headers).json()
    assert [p["amount"] for p in payments] == [150.0, 250.0]
    assert payments[0]["payment_method"] == "cash"
