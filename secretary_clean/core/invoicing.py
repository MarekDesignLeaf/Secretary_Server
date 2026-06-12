"""Shared invoice line-item helpers — used by both repository backends so the
calculation stays identical."""
from __future__ import annotations


def activity_line_items(activities, rate_by_code: dict[str, float]):
    """Turn work-report activities into invoice line items.

    rate priority: the rate the app sent (already tenant-override or Oxfordshire
    default) → tenant pricing override for that code → 0 (+warning).
    Returns (items, total, warnings).
    """
    items, total, warnings = [], 0.0, []
    for a in activities or []:
        code = a.get("activity_code")
        qty = float(a.get("quantity") or 1)
        rate = float(a.get("rate") or 0)
        if rate <= 0 and code in rate_by_code:
            rate = rate_by_code[code]
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
