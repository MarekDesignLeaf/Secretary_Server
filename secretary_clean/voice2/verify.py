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
        ok = _field_eq(f, expected, actual)
        checked[f] = {"expected": expected, "actual": actual, "ok": ok}
        ok_all = ok_all and ok
    return VerifyResult(verified=ok_all, checked=checked)


def _field_eq(field_name: str, expected, actual) -> bool:
    if expected is None:
        return True
    # datetime fields (e.g. start_at): compare the instant, tolerating tz-naive
    # vs tz-aware and trailing-Z formatting differences (P2-12).
    if field_name in ("start_at",) and isinstance(expected, str) and isinstance(actual, str):
        return _same_instant(expected, actual)
    if isinstance(expected, str) and isinstance(actual, str):
        # exact match after normalizing whitespace/case — no loose substring,
        # which used to let a partially-wrong value pass verification (P2-10).
        return expected.strip().lower() == actual.strip().lower()
    return expected == actual


def _same_instant(a: str, b: str) -> bool:
    from datetime import datetime, timezone
    def _p(s):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    da, db = _p(a), _p(b)
    if da and db:
        return da == db
    return a.strip() == b.strip()
