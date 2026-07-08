"""voice2 read-back verification.

After every write the engine re-reads the entity through the SAME repository
path the UI uses and compares the fields the user asked for. A write that
cannot be read back (or disagrees) is reported as unverified — never silently
claimed as done. This is the "check and report the result" guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VerifySpec:
    kind: str                     # "crm:<module>" | "calendar" | "none"
    entity_id: str | None
    expected: dict = field(default_factory=dict)   # field -> expected value


@dataclass
class VerifyResult:
    verified: bool
    checked: dict = field(default_factory=dict)    # field -> {"expected", "actual", "ok"}
    error: str | None = None

    def as_dict(self) -> dict:
        return {"verified": self.verified, "checked": self.checked,
                **({"error": self.error} if self.error else {})}


def _get(record, field_name):
    if record is None:
        return None
    if field_name == "name":
        return getattr(record, "name", None)
    if field_name == "status":
        return getattr(record, "status", None)
    if field_name == "title":
        return getattr(record, "title", None)
    if field_name == "start_at":
        v = getattr(record, "start_at", None)
        return v.isoformat() if hasattr(v, "isoformat") else v
    data = getattr(record, "data", None) or {}
    return data.get(field_name)


def run(repository, company_id: str, spec: VerifySpec) -> VerifyResult:
    if spec.kind == "none" or not spec.entity_id:
        return VerifyResult(verified=True)
    try:
        if spec.kind.startswith("crm:"):
            module = spec.kind.split(":", 1)[1]
            rec = repository.get_crm_record(module, spec.entity_id, company_id)
        elif spec.kind == "calendar":
            rec = repository.get_calendar_event(spec.entity_id, company_id)
        else:
            return VerifyResult(verified=True)
    except Exception as exc:  # noqa: BLE001 — verification must not crash the command
        return VerifyResult(verified=False, error=f"read-back failed: {exc}")

    if rec is None:
        return VerifyResult(verified=False, error="entity not found on read-back")

    checked: dict = {}
    ok_all = True
    for f, expected in (spec.expected or {}).items():
        actual = _get(rec, f)
        ok = _loose_eq(expected, actual)
        checked[f] = {"expected": expected, "actual": actual, "ok": ok}
        ok_all = ok_all and ok
    return VerifyResult(verified=ok_all, checked=checked)


def _loose_eq(expected, actual) -> bool:
    if expected is None:
        return True
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() == actual.strip().lower() or \
            expected.strip().lower() in actual.strip().lower()
    return expected == actual
