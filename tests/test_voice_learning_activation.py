"""Phase 4 — pending-alias activation (startup pass + admin endpoint)."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.app import _activate_pending_aliases
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core import voice_learning_service as vls


def _pending_alias(repo, company_id, target_intent):
    """Force a PENDING alias regardless of whether the target is implemented."""
    a = vls.new_alias(company_id, "u1", "nauceny prikaz", target_intent, created_by="u1")
    a.status = "PENDING"
    repo.create_voice_alias(a)
    return a


def test_startup_pass_activates_pending_for_implemented_target():
    repo = InMemorySecretaryRepository()
    a_impl = _pending_alias(repo, "c1", "client.create")        # implemented
    a_plan = _pending_alias(repo, "c1", "invoice.send")  # not implemented

    _activate_pending_aliases(repo)

    assert repo.get_voice_alias(a_impl.id, "c1").status == "ACTIVE"
    assert repo.get_voice_alias(a_plan.id, "c1").status == "PENDING"
    # An activation learning event was written for the one that flipped.
    evs = repo.list_voice_learning_events("c1")
    assert any(e.created_alias_id == a_impl.id and e.metadata.get("activated_on_boot")
               for e in evs)


def test_startup_pass_is_idempotent():
    repo = InMemorySecretaryRepository()
    a = _pending_alias(repo, "c1", "task.create")
    _activate_pending_aliases(repo)
    _activate_pending_aliases(repo)  # second run must be a no-op
    assert repo.get_voice_alias(a.id, "c1").status == "ACTIVE"
    activations = [e for e in repo.list_voice_learning_events("c1")
                   if e.metadata.get("activated_on_boot")]
    assert len(activations) == 1


def _bootstrap(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "Act Ltd",
                                "default_internal_language_code": "cs-CZ"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}, company["id"]


def test_activate_pending_endpoint_is_tenant_scoped(monkeypatch):
    client, headers, company_id = _bootstrap(monkeypatch)
    repo = client.app.state.repository
    a = _pending_alias(repo, company_id, "client.create")

    out = client.post("/api/v1/voice/learning/activate-pending", headers=headers).json()
    assert out["count"] == 1
    assert repo.get_voice_alias(a.id, company_id).status == "ACTIVE"
