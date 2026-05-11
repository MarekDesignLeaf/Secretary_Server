import os

from fastapi.testclient import TestClient

from secretary_clean import create_app


def test_clean_bootstrap_auth_catalogue_and_tenant_override_flow(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())

    assert client.get("/api/v1/bootstrap/status").json() == {
        "needs_first_company": True,
        "needs_first_admin": True,
        "is_ready": False,
    }

    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={
            "legal_name": "Clean Secretary Ltd",
            "default_internal_language_code": "en-GB",
            "default_customer_language_code": "cs-CZ",
        },
    ).json()
    admin = client.post(
        "/api/v1/bootstrap/first-admin",
        json={
            "company_id": company["id"],
            "email": "owner@example.com",
            "display_name": "Owner",
            "password": "very-secure-password",
        },
    ).json()
    assert admin["role"] == "owner"

    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "very-secure-password"},
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    language_settings = client.get("/api/v1/language/settings", headers=headers).json()
    assert language_settings["default_internal_language_code"] == "en-GB"
    assert language_settings["default_customer_language_code"] == "cs-CZ"
    assert language_settings["auto_translate_customer_to_internal"] is True

    available_languages = client.get("/api/v1/language/available", headers=headers).json()
    assert {language["code"] for language in available_languages} >= {"en-GB", "cs-CZ", "pl-PL"}

    updated_settings = client.put(
        "/api/v1/language/settings",
        headers=headers,
        json={
            "internal_language_mode": "single",
            "customer_language_mode": "multilingual",
            "default_internal_language_code": "en-GB",
            "default_customer_language_code": "pl-PL",
            "voice_input_strategy": "client_preferred",
            "voice_output_strategy": "client_preferred",
            "auto_translate_customer_to_internal": True,
            "auto_translate_internal_to_customer": True,
        },
    ).json()
    assert updated_settings["default_customer_language_code"] == "pl-PL"

    tenant_languages = client.get("/api/v1/language/tenant", headers=headers).json()
    assert {language["language_scope"] for language in tenant_languages} >= {
        "internal",
        "customer",
        "voice_input",
        "voice_output",
    }

    client_record = client.post(
        "/api/v1/crm/clients",
        headers=headers,
        json={"name": "Customer One"},
    ).json()
    client_language = client.put(
        f"/api/v1/language/client/{client_record['id']}",
        headers=headers,
        json={"preferred_language_code": "cs"},
    ).json()
    assert client_language["preferred_language_code"] == "cs-CZ"

    context = client.get(
        f"/api/v1/language/context?client_id={client_record['id']}",
        headers=headers,
    ).json()
    assert context["internal_language_code"] == "en-GB"
    assert context["customer_language_code"] == "cs-CZ"
    assert context["translate_internal_to_customer"] is True

    summary = client.get("/api/v1/catalogue/validation-summary", headers=headers).json()
    assert summary["activity_count"] > 1000

    first_activity = client.get("/api/v1/catalogue/industries", headers=headers).json()[0]["subtypes"][0]["activities"][0]
    override = client.put(
        f"/api/v1/tenant-pricing/activities/{first_activity['code']}",
        headers=headers,
        json={"selected_pricing_method_code": first_activity["available_pricing_method_codes"][1], "rate": 125},
    ).json()
    assert override["selected_pricing_method_code"] != first_activity["default_pricing_method_code"]

    reset = client.delete(
        f"/api/v1/tenant-pricing/activities/{first_activity['code']}/override",
        headers=headers,
    ).json()
    assert reset == {"reset": True, "system_default_preserved": True}


def test_voice_resolver_does_not_execute_fake_action(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company", json={"legal_name": "Voice Ltd"}).json()
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
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    response = client.post(
        "/api/v1/voice/execute",
        headers=headers,
        json={"utterance": "do a random magic fake action", "confirmed": True},
    ).json()
    assert response["executed"] is False
    assert response["resolved_intent"] is None
    assert response["language_context"]["voice_input_language_code"] == "en-GB"


def test_clean_first_install_creates_ready_company_admin_languages_and_industry(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())

    first_install = client.post(
        "/api/v1/bootstrap/first-install",
        json={
            "company_name": "Installed Secretary Ltd",
            "company_legal_type": "limited_company",
            "country": "GB",
            "timezone": "Europe/London",
            "currency": "GBP",
            "internal_company_language": "en-GB",
            "default_customer_language": "pl-PL",
            "workspace_mode": "team",
            "industry_group": "trades_and_field_services",
            "industry_subtype": "trades_and_field_services.landscaping",
            "first_admin_display_name": "Install Owner",
            "first_admin_email": "install-owner@example.com",
            "first_admin_password": "very-secure-password",
            "first_admin_first_name": "Install",
            "first_admin_last_name": "Owner",
            "phone": "+441234567890",
            "website": "https://example.com",
        },
    )
    assert first_install.status_code == 200
    payload = first_install.json()
    assert payload["company"]["legal_name"] == "Installed Secretary Ltd"
    assert payload["company"]["legal_type"] == "limited_company"
    assert payload["company"]["industry_group"] == "trades_and_field_services"
    assert payload["company"]["industry_subtype"] == "trades_and_field_services.landscaping"
    assert payload["admin"]["email"] == "install-owner@example.com"
    assert payload["admin"]["first_name"] == "Install"
    assert payload["admin"]["last_name"] == "Owner"
    assert payload["bootstrap_status"] == {
        "needs_first_company": False,
        "needs_first_admin": False,
        "is_ready": True,
    }

    status = client.get("/api/v1/bootstrap/status").json()
    assert status == {
        "needs_first_company": False,
        "needs_first_admin": False,
        "is_ready": True,
    }

    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": "install-owner@example.com", "password": "very-secure-password"},
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    language_settings = client.get("/api/v1/language/settings", headers=headers).json()
    assert language_settings["default_internal_language_code"] == "en-GB"
    assert language_settings["default_customer_language_code"] == "pl-PL"
    assert language_settings["workspace_mode"] == "team"
    assert language_settings["industry_group"] == "trades_and_field_services"
    assert language_settings["industry_subtype"] == "trades_and_field_services.landscaping"
