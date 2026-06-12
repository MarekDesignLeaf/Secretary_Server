"""First-install recovery from a half-installed DB (production 500 fix)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core.models import Role, ROLE_PERMISSIONS, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core.security import hash_password

INSTALL_PAYLOAD = {
    "company_name": "DesignLeaf",
    "first_admin_email": "owner@example.com",
    "first_admin_password": "very-secure-password",
    "first_admin_display_name": "Marek",
    "country": "GB", "currency": "GBP", "timezone": "Europe/London",
    "default_internal_language_code": "cs-CZ",
    "default_customer_language_code": "en-GB",
}


def _app_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    repo = InMemorySecretaryRepository()
    app = create_app(repository=repo)
    return TestClient(app, raise_server_exceptions=False), repo


def test_first_install_adopts_orphan_company(monkeypatch):
    client, _repo = _app_client(monkeypatch)
    client.post("/api/v1/bootstrap/first-company", json={"legal_name": "Half Ltd"})
    assert client.get("/api/v1/bootstrap/status").json() == {
        "needs_first_company": False, "needs_first_admin": True, "is_ready": False}

    res = client.post("/api/v1/bootstrap/first-install", json=INSTALL_PAYLOAD)
    assert res.status_code == 200, res.text
    assert res.json()["bootstrap_status"]["is_ready"] is True


def test_first_install_adopts_leftover_user_with_same_email(monkeypatch):
    client, repo = _app_client(monkeypatch)
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "Half Ltd"}).json()

    # Simulate the production leftover: a non-owner user row already holds the
    # e-mail (partial wipe / failed earlier install).
    leftover = UserAccount(
        id="leftover-user-id", company_id=company["id"],
        email="owner@example.com", display_name="Old Row", role=Role.staff,
        permissions=sorted(ROLE_PERMISSIONS[Role.staff]))
    repo.users[leftover.id] = leftover
    repo.password_hashes[leftover.id] = hash_password("old-password-123")

    res = client.post("/api/v1/bootstrap/first-install", json=INSTALL_PAYLOAD)
    assert res.status_code == 200, res.text
    admin = res.json()["admin"]
    # The leftover row was adopted: same id, promoted to owner, no duplicate.
    assert admin["id"] == "leftover-user-id"
    assert admin["role"] == "owner"
    assert sum(1 for u in repo.users.values()
               if u.email == "owner@example.com") == 1

    # New password works.
    login = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"})
    assert login.status_code == 200


def test_first_install_unexpected_error_returns_diagnostic_detail(monkeypatch):
    client, repo = _app_client(monkeypatch)

    def boom(payload, *, activity_defaults=None):
        raise RuntimeError("duplicate key value violates unique constraint")

    monkeypatch.setattr(repo, "create_first_install", boom)
    res = client.post("/api/v1/bootstrap/first-install", json=INSTALL_PAYLOAD)
    assert res.status_code == 500
    assert "RuntimeError" in res.json()["detail"]
