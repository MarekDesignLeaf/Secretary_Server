"""Regression tests for the voice-control audit fixes (P0/P1/P2)."""
from secretary_clean.voice2 import nlu
from fastapi.testclient import TestClient
from secretary_clean import create_app


def _c(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    co = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "V Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": co["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login",
                 json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def _exec(c, h, u, pid=None):
    b = {"utterance": u}
    if pid:
        b["pending_action_id"] = pid
    return c.post("/api/v1/voice/execute", headers=h, json=b).json()


# ── nlu unit-level ────────────────────────────────────────────────────────────
def test_jeste_does_not_oversplit():
    # bare "ještě" must NOT split a single command (P1-4)
    segs = nlu.segment("napiš úkol koupit ještě cement")
    assert len(segs) == 1


def test_value_list_not_split():
    segs = nlu.segment("zapiš materiál cement a písek")
    assert len(segs) == 1


def test_conjunction_with_verb_splits():
    segs = nlu.segment("vytvoř úkol koupit materiál a vytvoř zakázku plot")
    assert len(segs) == 2


def test_anaphora_to_substring_does_not_leak(monkeypatch):
    # "beton" contains "to" — must NOT inherit person/entity (P0-1)
    ctx = nlu.SegmentContext()
    ctx.person = "Novák"
    ctx.client = "Novák"
    d = ctx.enrich("vystav fakturu za beton", {"raw": "vystav fakturu za beton"})
    assert "person" not in d
    assert "client" not in d


def test_person_not_leaked_without_anaphora():
    # P0-2: person must not carry into a command that named nobody
    ctx = nlu.SegmentContext()
    ctx.person = "Petr"
    d = ctx.enrich("vytvoř úkol koupit materiál", {"raw": "x"})
    assert "person" not in d


def test_anaphora_mu_does_inherit():
    ctx = nlu.SegmentContext()
    ctx.person = "Petr"
    d = ctx.enrich("přidej mu úkol zavolat", {"raw": "x"})
    assert d.get("person") == "Petr"


def test_lead_name_extracted_from_phrase():
    d = nlu.entities_from_text("lead.create", "nová poptávka Karel Dvořák")
    assert d.get("name") == "Karel Dvořák"


# ── engine-level ──────────────────────────────────────────────────────────────
def test_dictated_note_with_cancel_word_is_not_cancelled(monkeypatch):
    # P0-3: a note body containing "nechci" must be captured, not abort
    c, h = _c(monkeypatch)
    c.post("/api/v1/crm/clients", headers=h, json={"name": "Karel Veselý"})
    out = _exec(c, h, "přidej poznámku ke klientovi Veselý")
    if out["status"] == "needs_more_info":
        out = _exec(c, h, "řekni ať to nechci zdržovat", out["pending_action_id"])
    assert out["status"] != "cancelled"


def test_job_status_continuation_is_mapped(monkeypatch):
    # P1-5: answering "hotovo" to the status question stores canonical status
    c, h = _c(monkeypatch)
    c.post("/api/v1/crm/jobs", headers=h, json={"job_title": "Plot Novák"})
    out = _exec(c, h, "změň stav zakázky Plot")
    assert out["status"] == "needs_more_info"
    out = _exec(c, h, "hotovo", out["pending_action_id"])
    assert out["executed"] is True
    jobs = c.get("/api/v1/crm/jobs", headers=h).json()
    assert any((j.get("job_status") or j.get("status")) == "dokončeno" for j in jobs)


def test_ambiguity_is_answerable(monkeypatch):
    # P1-7: an ambiguous utterance parks a pending the user can answer
    c, h = _c(monkeypatch)
    out = _exec(c, h, "nová poptávka")   # lead.create needs a name → asks
    # (not asserting ambiguity specifically; just that no dead-end 'error')
    assert out["status"] in ("needs_more_info", "executed")
