"""Regression: a SELECTED activity whose tenant row carries no explicit price
must still show/charge its system default rate — never 0.

This is the long-standing "most marked activities have no rate" bug: onboarding
seeded a tenant pricing row (is_active) with rate=NULL, and the read path turned
that into 0.0 instead of falling back to the default.
"""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import invoicing as inv


def _client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    co = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "Rate Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": co["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login",
                 json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}, co["id"]


def _pricing(c, h):
    return c.get("/api/v1/activities/tenant/0", headers=h).json()


def test_selected_activity_without_price_shows_default_not_zero(monkeypatch):
    c, h, _ = _client(monkeypatch)
    rows = _pricing(c, h)
    assert rows, "catalogue produced no activity rows"
    sample = rows[0]
    code = sample["activity_code"]
    method = sample["pricing_method"]

    # Select the activity (mark it active) but give it NO price — the exact
    # state onboarding used to seed.
    r = c.put(f"/api/v1/tenant-pricing/activities/{code}", headers=h,
              json={"selected_pricing_method_code": method, "rate": None})
    assert r.status_code == 200, r.text

    after = {row["activity_code"]: row for row in _pricing(c, h)}[code]
    assert after["is_active"] is True
    # The bug returned 0.0 here; the fix returns the system default.
    assert after["default_rate"] > 0
    assert after["rate"] == after["default_rate"], after
    assert after["rate"] != 0.0


def test_explicit_price_still_wins(monkeypatch):
    c, h, _ = _client(monkeypatch)
    rows = _pricing(c, h)
    code = rows[0]["activity_code"]
    method = rows[0]["pricing_method"]
    c.put(f"/api/v1/tenant-pricing/activities/{code}", headers=h,
          json={"selected_pricing_method_code": method, "rate": 123.0})
    after = {row["activity_code"]: row for row in _pricing(c, h)}[code]
    assert after["rate"] == 123.0


def test_invoicing_falls_back_to_catalogue_default(monkeypatch):
    # A work-report activity that arrived with no price bills at the catalogue
    # default, not 0 — no "Activity without a rate" warning.
    code = next(iter(inv._catalogue_index()))
    items, total, warnings = inv.activity_line_items(
        [{"activity_code": code, "name": "x", "quantity": 1}], {})
    assert items[0]["unit_price"] > 0
    assert total > 0
    assert not warnings
