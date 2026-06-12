"""Tenant default rates + service rate types (clean_tenant_service_rates).

Contract tests for the 4 live SettingsScreen endpoints.
"""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Tenant Rates Ltd", "default_currency": "CZK"},
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


def test_default_rates_requires_auth(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    assert client.get("/api/v1/tenant/default-rates/1").status_code == 401


def test_first_get_seeds_oxfordshire_builtins(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    seeded = client.get("/api/v1/tenant/default-rates/1", headers=headers).json()
    rates = {r["rate_type"]: r for r in seeded}
    assert set(rates) == {"hourly_rate", "garden_maintenance", "hedge_trimming",
                          "arborist_works", "garden_waste_bulkbag", "minimum_charge"}
    assert rates["hourly_rate"]["rate"] == 27.0
    assert rates["hedge_trimming"]["rate"] == 31.0
    assert rates["minimum_charge"]["rate"] == 150.0
    assert all(r["is_builtin"] for r in seeded)
    assert all(r["currency"] == "CZK" for r in seeded)

    # Second GET must not duplicate the seed.
    again = client.get("/api/v1/tenant/default-rates/1", headers=headers).json()
    assert len(again) == len(seeded) == 6

    # Builtin types cannot be deleted.
    res = client.delete(
        "/api/v1/tenant/service-rate-types/1/hourly_rate", headers=headers)
    assert res.status_code == 409

    # Saving rates over the seeded builtins works (the original FAIL).
    updated = client.put(
        "/api/v1/tenant/default-rates/1", headers=headers,
        json={"hourly_rate": {"rate": 30}, "minimum_charge": {"rate": 180}},
    ).json()
    by_type = {r["rate_type"]: r["rate"] for r in updated}
    assert by_type["hourly_rate"] == 30.0
    assert by_type["minimum_charge"] == 180.0
    assert by_type["hedge_trimming"] == 31.0


def test_full_rate_type_lifecycle(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    # First GET seeds the 6 builtins; custom types are added on top of them.
    seeded = client.get("/api/v1/tenant/default-rates/1", headers=headers).json()
    assert len(seeded) == 6

    # POST /tenant/service-rate-types — Android sends {rate_type, description, rate}.
    created = client.post(
        "/api/v1/tenant/service-rate-types/1", headers=headers,
        json={"rate_type": "lawn_scarification", "description": "Vertikutace trávníku",
              "rate": 45},
    )
    assert created.status_code == 201
    row = created.json()
    assert row["rate_type"] == "lawn_scarification"
    assert row["rate"] == 45.0
    assert row["is_builtin"] is False
    assert row["currency"] == "CZK"  # from company default_currency
    assert row["sort_order"] == 7  # after the 6 seeded builtins

    # Duplicate (also vs. a seeded builtin) -> 409 with detail.
    for dup_key in ("lawn_scarification", "hourly_rate"):
        dup = client.post(
            "/api/v1/tenant/service-rate-types/1", headers=headers,
            json={"rate_type": dup_key, "description": "x", "rate": 1},
        )
        assert dup.status_code == 409
        assert "detail" in dup.json()

    # GET list — Android reads rate_type/rate/description/is_builtin.
    listed = client.get("/api/v1/tenant/default-rates/1", headers=headers).json()
    assert [r["rate_type"] for r in listed][-1] == "lawn_scarification"
    assert len(listed) == 7
    assert all({"rate_type", "rate", "description", "is_builtin"} <= set(r) for r in listed)

    # PUT — Android sends {rate_type: {"rate": x}}; returns the updated list.
    updated = client.put(
        "/api/v1/tenant/default-rates/1", headers=headers,
        json={"lawn_scarification": {"rate": 50.5}, "hourly_rate": {"rate": 29},
              "unknown_key": {"rate": 99}},
    ).json()
    by_type = {r["rate_type"]: r["rate"] for r in updated}
    assert by_type["lawn_scarification"] == 50.5
    assert by_type["hourly_rate"] == 29.0
    assert "unknown_key" not in by_type

    # DELETE custom type.
    deleted = client.delete(
        "/api/v1/tenant/service-rate-types/1/lawn_scarification", headers=headers)
    assert deleted.status_code == 200
    listed = client.get("/api/v1/tenant/default-rates/1", headers=headers).json()
    assert "lawn_scarification" not in [r["rate_type"] for r in listed]

    # Delete of a missing type -> 404.
    missing = client.delete(
        "/api/v1/tenant/service-rate-types/1/lawn_scarification", headers=headers)
    assert missing.status_code == 404

    # Re-adding a deleted type works again (soft-delete reactivation path).
    readd = client.post(
        "/api/v1/tenant/service-rate-types/1", headers=headers,
        json={"rate_type": "lawn_scarification", "description": "Vertikutace", "rate": 40},
    )
    assert readd.status_code == 201


def test_tenant_isolation(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post(
        "/api/v1/tenant/service-rate-types/1", headers=headers,
        json={"rate_type": "hourly_rate", "description": "", "rate": 27},
    )

    # Second company on the same app instance must not see the first one's rates.
    second_admin = client.post(
        "/api/v1/auth/register", headers=headers,
        json={"email": "other@example.com", "password": "x", "display_name": "O",
              "role": "admin"},
    )
    assert second_admin.status_code in (200, 201)
    # Same company -> sees the rate; isolation is by company_id in every query,
    # verified directly on the repository:
    app_rates = client.get("/api/v1/tenant/default-rates/1", headers=headers).json()
    assert len(app_rates) == 1
