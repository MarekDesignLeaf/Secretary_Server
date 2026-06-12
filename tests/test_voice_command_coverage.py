"""Audit: every intent the parser emits has a clean execution branch."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from secretary_clean import create_app

_VOICE_PY = Path(__file__).resolve().parents[1] / "secretary_clean" / "api" / "routes" / "voice.py"
_INTENTS_PY = Path(__file__).resolve().parents[1] / "secretary_clean" / "core" / "voice_intents.py"


def test_every_parser_intent_is_executed():
    emitted = set(re.findall(r'intent="([a-z_.]+)"', _INTENTS_PY.read_text(encoding="utf-8")))
    voice_src = _VOICE_PY.read_text(encoding="utf-8")
    executed = set(re.findall(r'intent == "([a-z_.]+)"', voice_src))
    # tuple form: if intent in ("a", "b")
    for grp in re.findall(r'intent in \(([^)]+)\)', voice_src):
        executed |= set(re.findall(r'"([a-z_.]+)"', grp))
    missing = emitted - executed
    assert not missing, f"Parser emits intents with no execution branch: {missing}"


def _logged_in(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    company = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "Cov Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login", json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def test_work_report_start_hands_off_cleanly(monkeypatch):
    c, h = _logged_in(monkeypatch)
    out = c.post("/api/v1/voice/execute", headers=h,
                 json={"utterance": "vytvoř pracovní výkaz"}).json()
    assert out["resolved_intent"] == "work_report.start"
    assert out["executed"] is True
    assert out["status"] == "client_action"
    assert out["data"]["client_action"] == "start_work_report"
    assert "zatim neumim" not in out["message"].lower()
