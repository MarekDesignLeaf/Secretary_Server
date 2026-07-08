"""Shared invoice line-item helpers — used by both repository backends so the
calculation stays identical."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _catalogue_index() -> dict[str, tuple[str, str, str]]:
    """Map activity_code -> (industry_code, activity_name, default_pricing_method).

    Lets invoicing compute the SAME system-default rate the settings screen
    shows, so a work-report activity that arrived without a price still bills at
    its default instead of 0. Cached — the catalogue is static per deploy.
    """
    try:
        from secretary_clean.catalogue.source_parser import load_catalogue
        snap = load_catalogue()
    except Exception:  # noqa: BLE001 — never let pricing setup break invoicing
        return {}
    idx: dict[str, tuple[str, str, str]] = {}
    for ind in snap.industries:
        for sub in ind.subtypes:
            for act in sub.activities:
                idx[act.code] = (ind.code, act.name, act.default_pricing_method_code)
    return idx


def catalogue_default_rate(activity_code: str, pricing_method: str | None = None) -> float:
    """System default rate for an activity code (0.0 if unknown)."""
    info = _catalogue_index().get(activity_code or "")
    if not info:
        return 0.0
    industry_code, name, default_method = info
    from secretary_clean.catalogue import default_rates
    return default_rates.default_rate(industry_code, name, pricing_method or default_method)


def activity_line_items(activities, rate_by_code: dict[str, float]):
    """Turn work-report activities into invoice line items.

    Rate priority (first positive wins):
      1) the rate the app sent on the line (already tenant-override or default)
      2) tenant pricing override for that activity code
      3) the catalogue SYSTEM DEFAULT for that code  ← added: stops selected
         activities billing at 0 when no explicit price was captured
      4) 0 (+warning)
    Returns (items, total, warnings).
    """
    items, total, warnings = [], 0.0, []
    for a in activities or []:
        code = a.get("activity_code")
        qty = float(a.get("quantity") or 1)
        rate = float(a.get("rate") or 0)
        if rate <= 0 and code in rate_by_code and rate_by_code[code] > 0:
            rate = rate_by_code[code]
        if rate <= 0:
            rate = catalogue_default_rate(code, a.get("pricing_method"))
        if rate <= 0:
            warnings.append(f"Activity without a rate: {a.get('name') or code}")
        subtotal = round(qty * rate, 2)
        total += subtotal
        items.append({
            "description": a.get("name") or code,
            "quantity": qty,
            "unit_price": rate,
            "subtotal": subtotal,
            "activity_code": code,
            "pricing_method": a.get("pricing_method"),
            "unit": a.get("unit"),
        })
    return items, round(total, 2), warnings
