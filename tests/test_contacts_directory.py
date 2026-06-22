"""Shared Contacts Directory: pre-set groups + import (the flow that was 404ing)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "Dir Ltd",
                                "default_internal_language_code": "cs-CZ"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_default_sections_seeded():
    """The bug: no groups → import impossible. Now every company has defaults."""
    from secretary_clean.core.repository import InMemorySecretaryRepository
    codes = {s["section_code"] for s in InMemorySecretaryRepository().list_contact_sections("c")}
    # The user's requested groups are present.
    assert {"client", "family", "friends", "material_supplier", "subcontractor"} <= codes


def test_get_sections_endpoint_returns_defaults(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    secs = client.get("/api/v1/crm/contact-sections", headers=headers).json()
    names = {s["display_name"] for s in secs}
    assert {"Rodina", "Přátelé", "Zákazníci"} <= names
    assert all("section_code" in s and "display_name" in s for s in secs)


def test_create_custom_section(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = client.post("/api/v1/crm/contact-sections", headers=headers,
                      json={"display_name": "VIP klienti"}).json()
    assert out["section_code"] == "vip_klienti"
    assert out["display_name"] == "VIP klienti"
    codes = {s["section_code"] for s in client.get("/api/v1/crm/contact-sections", headers=headers).json()}
    assert "vip_klienti" in codes


def test_import_contacts_with_sections(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    res = client.post("/api/v1/crm/contacts/import", headers=headers, json={"contacts": [
        {"contact_key": "k1", "display_name": "Máma", "phone_primary": "+44 7911 111222",
         "section_code": "family", "selected": True},
        {"contact_key": "k2", "display_name": "Dodavatel s.r.o.", "phone_primary": "0161 496 0000",
         "section_code": "material_supplier", "selected": True},
        {"contact_key": "k3", "display_name": "Neoznačený", "selected": False},  # skipped
    ]}).json()
    assert res["imported"] == 2

    contacts = client.get("/api/v1/crm/contacts", headers=headers).json()
    by_name = {c["display_name"]: c for c in contacts}
    assert by_name["Máma"]["section_code"] == "family"
    assert by_name["Máma"]["section_name"] == "Rodina"
    assert by_name["Máma"]["phone_primary"] == "+447911111222"   # normalized
    assert "Neoznačený" not in by_name


def test_import_dedupes_by_phone(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    client.post("/api/v1/crm/contacts/import", headers=headers, json={"contacts": [
        {"display_name": "Petr", "phone_primary": "+44 7911 000111", "section_code": "client", "selected": True},
    ]})
    again = client.post("/api/v1/crm/contacts/import", headers=headers, json={"contacts": [
        {"display_name": "Petr Novák", "phone_primary": "07911 000111", "section_code": "client", "selected": True},
    ]}).json()
    assert again["merged"] == 1 and again["imported"] == 0
    assert len(client.get("/api/v1/crm/contacts", headers=headers).json()) == 1


def test_import_drops_bad_phone_but_keeps_contact(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    client.post("/api/v1/crm/contacts/import", headers=headers, json={"contacts": [
        {"display_name": "Bez čísla", "phone_primary": "1234", "section_code": "other", "selected": True},
    ]})
    c = client.get("/api/v1/crm/contacts", headers=headers).json()[0]
    assert c["display_name"] == "Bez čísla"
    assert c["phone_primary"] is None   # junk number not stored


def test_crud_and_validation(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    created = client.post("/api/v1/crm/contacts", headers=headers, json={
        "display_name": "Karel", "section_code": "friends", "phone_primary": "+44 7911 222333"}).json()
    cid = created["id"]
    assert created["section_name"] == "Přátelé"

    # Bad phone on edit → 422.
    bad = client.put(f"/api/v1/crm/contacts/{cid}", headers=headers,
                     json={"display_name": "Karel", "section_code": "friends", "phone_primary": "12"})
    assert bad.status_code == 422

    # Good edit.
    upd = client.put(f"/api/v1/crm/contacts/{cid}", headers=headers,
                     json={"display_name": "Karel Novák", "section_code": "client"}).json()
    assert upd["display_name"] == "Karel Novák" and upd["section_code"] == "client"

    # Delete.
    assert client.delete(f"/api/v1/crm/contacts/{cid}", headers=headers).json()["ok"] is True
    assert client.get("/api/v1/crm/contacts", headers=headers).json() == []


def test_assign_section_and_duplicates_and_merge(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    a = client.post("/api/v1/crm/contacts", headers=headers, json={
        "display_name": "Jan", "phone_primary": "+44 7911 333444"}).json()
    b = client.post("/api/v1/crm/contacts", headers=headers, json={
        "display_name": "Jan Dvořák", "phone_primary": "07911 333444"}).json()

    # assign-section by id
    client.post("/api/v1/crm/contacts/assign-section", headers=headers,
                json={"contact_id": a["id"], "section_code": "subcontractor"})
    refreshed = {c["id"]: c for c in client.get("/api/v1/crm/contacts", headers=headers).json()}
    assert refreshed[a["id"]]["section_code"] == "subcontractor"

    # duplicates (same phone)
    dups = client.get("/api/v1/crm/contacts/duplicates", headers=headers).json()["duplicates"]
    assert any(d["reason"] == "same_phone" for d in dups)

    # merge
    m = client.post("/api/v1/crm/contacts/merge", headers=headers,
                    json={"primary_id": a["id"], "secondary_id": b["id"]}).json()
    assert m["ok"] is True
    assert len(client.get("/api/v1/crm/contacts", headers=headers).json()) == 1


def test_sort_session_returns_unclassified(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    client.post("/api/v1/crm/contacts", headers=headers, json={"display_name": "Bez skupiny"})
    client.post("/api/v1/crm/contacts", headers=headers, json={"display_name": "Má skupinu", "section_code": "client"})
    out = client.get("/api/v1/crm/contacts/sort-session", headers=headers).json()
    names = {c["display_name"] for c in out["contacts"]}
    assert "Bez skupiny" in names and "Má skupinu" not in names
