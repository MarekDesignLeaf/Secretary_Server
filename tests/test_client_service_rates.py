"""Client service-rates endpoints (gap report #29-31)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Rates Test Ltd"},
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


def test_service_rates_roundtrip(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    cid = client.post(
        "/api/v1/crm/clients", headers=headers, json={"name": "Rates Customer"},
    ).json()["id"]

    empty = client.get(f"/api/v1/crm/clients/{cid}/service-rates", headers=headers).json()
    assert empty["has_individual_service_rates"] is False
    assert empty["service_rate_overrides"] == {}

    put = client.put(
        f"/api/v1/crm/clients/{cid}/service-rates", headers=headers,
        json={"hourly_rate": 35, "minimum_charge": 120,
              "nonsense": "abc", "zero": 0, "negative": -5},
    ).json()
    # Only numeric values > 0 survive (440aa04 semantics).
    assert put["service_rate_overrides"] == {"hourly_rate": 35.0, "minimum_charge": 120.0}
    assert put["has_individual_service_rates"] is True
    assert put["service_rates"]["hourly_rate"] == 35.0

    # PUT replaces the whole map.
    replaced = client.put(
        f"/api/v1/crm/clients/{cid}/service-rates", headers=headers,
        json={"hourly_rate": 40},
    ).json()
    assert replaced["service_rate_overrides"] == {"hourly_rate": 40.0}

    detail = client.get(f"/api/v1/crm/clients/{cid}", headers=headers).json()
    assert detail["service_rate_overrides"] == {"hourly_rate": 40.0}
    assert detail["has_individual_service_rates"] is True


def test_single_rate_endpoint(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    cid = client.post(
        "/api/v1/crm/clients", headers=headers, json={"name": "Rate Customer"},
    ).json()["id"]

    res = client.put(
        f"/api/v1/crm/clients/{cid}/rate", headers=headers,
        json={"default_hourly_rate": 27.5},
    ).json()
    assert res["default_hourly_rate"] == 27.5
    assert res["display_name"] == "Rate Customer"

    rates = client.get(f"/api/v1/crm/clients/{cid}/service-rates", headers=headers).json()
    assert rates["service_rate_overrides"]["hourly_rate"] == 27.5

    cleared = client.put(
        f"/api/v1/crm/clients/{cid}/rate", headers=headers,
        json={"default_hourly_rate": 0},
    ).json()
    assert cleared["default_hourly_rate"] is None
    rates = client.get(f"/api/v1/crm/clients/{cid}/service-rates", headers=headers).json()
    assert "hourly_rate" not in rates["service_rate_overrides"]


def test_service_rates_unknown_client_404(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    res = client.get(
        "/api/v1/crm/clients/00000000-0000-0000-0000-000000000000/service-rates",
        headers=headers)
    assert res.status_code == 404
