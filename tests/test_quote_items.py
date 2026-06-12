"""Quote items endpoints (gap report §9, step 4b)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Quote Items Ltd"},
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


def test_quote_items_add_and_delete_recompute_total(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    crm_client = client.post(
        "/api/v1/crm/clients", headers=headers, json={"name": "Quote Customer"},
    ).json()
    quote = client.post(
        "/api/v1/crm/quotes", headers=headers,
        json={"quote_title": "Garden refresh", "client_id": crm_client["id"]},
    ).json()
    quote_id = quote["id"]
    assert quote["items"] == []

    after_a = client.post(
        f"/api/v1/crm/quotes/{quote_id}/items", headers=headers,
        json={"description": "Hedge trim", "quantity": 4, "unit_price": 35},
    ).json()
    assert after_a["grand_total"] == 140.0
    assert len(after_a["items"]) == 1

    after_b = client.post(
        f"/api/v1/crm/quotes/{quote_id}/items", headers=headers,
        json={"description": "Waste removal", "quantity": 1, "unit_price": 60},
    ).json()
    assert after_b["grand_total"] == 200.0

    item_a_id = after_a["items"][0]["id"]
    after_delete = client.delete(
        f"/api/v1/crm/quotes/{quote_id}/items/{item_a_id}", headers=headers,
    ).json()
    assert after_delete["grand_total"] == 60.0
    assert [i["description"] for i in after_delete["items"]] == ["Waste removal"]

    missing = client.delete(
        f"/api/v1/crm/quotes/{quote_id}/items/{item_a_id}", headers=headers)
    assert missing.status_code == 404
