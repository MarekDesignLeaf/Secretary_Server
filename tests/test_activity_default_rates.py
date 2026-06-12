"""Activity pricing: Oxfordshire system defaults + multi-industry filter."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Pricing Ltd"},
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


def test_every_activity_has_a_preset_rate(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    # No industries set -> all industries, all activities, every one pre-priced.
    rows = client.get("/api/v1/activities/tenant/1", headers=headers).json()
    assert len(rows) > 1800
    priced = [r for r in rows if r["rate"] > 0]
    # Only pass-through methods (material at cost) may stay at 0.
    zero_methods = {r["pricing_method"] for r in rows if r["rate"] == 0}
    assert zero_methods <= {"materialova_polozka"}
    assert len(priced) > 1700
    sample = rows[0]
    assert sample["default_rate"] == sample["rate"]
    assert sample["rate_unit"]


def test_override_beats_default_and_reset_restores_it(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    rows = client.get("/api/v1/activities/tenant/1", headers=headers).json()
    target = next(r for r in rows if r["pricing_method"] == "hodinova_sazba")
    tid = target["template_id"]
    assert target["rate"] == target["default_rate"] > 0

    client.put(f"/api/v1/activities/tenant/1/{tid}", headers=headers,
               json={"pricing_method": "hodinova_sazba", "rate": 99.5})
    rows = client.get("/api/v1/activities/tenant/1", headers=headers).json()
    updated = next(r for r in rows if r["template_id"] == tid)
    assert updated["rate"] == 99.5
    assert updated["default_rate"] == target["default_rate"]  # default unchanged

    client.delete(f"/api/v1/activities/tenant/1/{tid}", headers=headers)
    rows = client.get("/api/v1/activities/tenant/1", headers=headers).json()
    restored = next(r for r in rows if r["template_id"] == tid)
    assert restored["rate"] == target["default_rate"]


def test_multi_industry_filter_shows_all_selected_industries(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    # Company picks TWO industries (can be any number, even all).
    res = client.put(
        "/api/v1/company/industries", headers=headers,
        json={"industries": [
            {"industry_code": "garden_landscaping_tree_and_outdoor_work", "is_primary": True},
            {"industry_code": "cleaning_waste_and_exterior_washing", "is_primary": False},
        ]},
    )
    assert res.status_code == 200

    rows = client.get("/api/v1/activities/tenant/1", headers=headers).json()
    industries = {r["industry_code"] for r in rows}
    assert industries == {"garden_landscaping_tree_and_outdoor_work",
                          "cleaning_waste_and_exterior_washing"}
    # 257 garden + 142 cleaning activities
    assert len(rows) == 399
