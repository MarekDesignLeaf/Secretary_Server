"""Generate the activity catalogue CSV with estimated Oxfordshire-area rates.

Model (approved by the owner as editable presets, not researched quotes):
  base hourly rate per industry  ->  derived per pricing method  ->  keyword
  multipliers for notably cheaper/pricier activities  ->  sane rounding.

Run from the server repo root:
  uv run --python cpython-3.12-windows-x86_64 --with-requirements requirements.txt \
      python tools/oxfordshire_rates.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from secretary_clean.catalogue.source_parser import load_catalogue  # noqa: E402

OUT_PATH = Path(r"C:\Users\hutra\AndroidStudioProjects\secretary\SECRETARY_ACTIVITIES_CATALOGUE.csv")

# Typical small-business hourly rates, Oxfordshire area, 2026 (GBP).
INDUSTRY_HOURLY = {
    "garden_landscaping_tree_and_outdoor_work": 30.0,
    "building_renovation_and_construction_trades": 45.0,
    "electrical_plumbing_heating_and_energy": 58.0,
    "property_facilities_and_real_estate": 35.0,
    "cleaning_waste_and_exterior_washing": 22.0,
    "vehicles_transport_logistics_and_storage": 40.0,
    "beauty_health_wellbeing_and_fitness": 45.0,
    "food_hospitality_and_events": 35.0,
    "education_training_childcare_and_coaching": 40.0,
    "digital_creative_it_and_media": 55.0,
    "retail_sales_wholesale_and_e_commerce": 30.0,
    "security_access_alarms_and_safety": 50.0,
    "agriculture_farming_animals_and_land_work": 35.0,
    "general_business_admin_and_support": 30.0,
}

UNITS = {
    "hodinova_sazba": "GBP/h",
    "denni_sazba": "GBP/day",
    "cena_za_navstevu": "GBP/visit",
    "cena_za_kus": "GBP/item",
    "cena_za_plochu": "GBP/m2",
    "cena_za_delku": "GBP/m",
    "cena_za_objem": "GBP/m3",
    "cena_za_vahu": "GBP/kg",
    "cena_za_bulk_bag_skip_nebo_container": "GBP/bag-or-skip",
    "fixni_cena": "GBP/job",
    "cena_za_balicek": "GBP/package",
    "callout_fee": "GBP/callout",
    "cestovni_poplatek": "GBP/trip",
    "materialova_polozka": "at cost",
    "procento": "%",
    "milestone_nebo_stage_payment": "GBP/milestone",
    "subscription_nebo_retainer": "GBP/month",
}


def method_rate(method: str, hourly: float) -> float:
    return {
        "hodinova_sazba": hourly,
        "denni_sazba": hourly * 7.5,
        "cena_za_navstevu": max(hourly * 2.0, 45.0),
        "cena_za_kus": hourly * 0.75,
        "cena_za_plochu": hourly / 6.0,
        "cena_za_delku": hourly / 3.0,
        "cena_za_objem": hourly * 1.5,
        "cena_za_vahu": 0.35,
        "cena_za_bulk_bag_skip_nebo_container": 70.0,
        "fixni_cena": hourly * 4.0,
        "cena_za_balicek": hourly * 5.0,
        "callout_fee": max(60.0, hourly * 1.4),
        "cestovni_poplatek": 30.0,
        "materialova_polozka": 0.0,
        "procento": 12.0,
        "milestone_nebo_stage_payment": hourly * 8.0,
        "subscription_nebo_retainer": hourly * 10.0,
    }.get(method, hourly)


# (keywords, multiplier) — first match group applies, multipliers stack across groups.
KEYWORD_FACTORS = [
    (("tree felling", "tree removal", "stump", "crane", "dismantl", "demolition"), 2.5),
    (("emergency", "urgent", "24/7", "24 hour", "out of hours"), 1.6),
    (("deep clean", "after builders", "end of tenancy"), 1.3),
    (("design", "architect", "structural", "survey"), 1.4),
    (("certificate", "certification", "inspection", "testing", "compliance"), 1.2),
    (("waste", "disposal", "clearance", "rubbish"), 1.2),
    (("installation", "install", "fitting"), 1.2),
    (("repair", "fix"), 1.1),
    (("weeding", "watering", "litter", "sweeping", "tidy"), 0.8),
]


def keyword_factor(name: str) -> float:
    low = name.lower()
    factor = 1.0
    for words, mult in KEYWORD_FACTORS:
        if any(w in low for w in words):
            factor *= mult
    return factor


def round_rate(value: float, method: str) -> float:
    if method == "procento":
        return round(value)
    if value <= 0:
        return 0.0
    if value < 1:
        return round(value, 2)
    if value < 20:
        return round(value * 2) / 2  # nearest 0.50
    if value < 100:
        return float(round(value))
    return float(round(value / 5) * 5)


def main() -> None:
    cat = load_catalogue()
    rows = 0
    with OUT_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["industry_code", "industry_name", "subtype_code", "subtype_name",
                    "activity_code", "activity_name", "default_pricing_method",
                    "rate_unit", "oxfordshire_rate_gbp", "available_pricing_methods"])
        for ind in cat.industries:
            hourly = INDUSTRY_HOURLY.get(ind.code, 35.0)
            for st in ind.subtypes:
                for a in st.activities:
                    method = a.default_pricing_method_code
                    base = method_rate(method, hourly)
                    # Keyword factors only scale work-value methods, not flat fees.
                    if method not in ("materialova_polozka", "procento", "cestovni_poplatek"):
                        base *= keyword_factor(a.name)
                    w.writerow([ind.code, ind.name, st.code, st.name, a.code, a.name,
                                method, UNITS.get(method, "GBP"),
                                round_rate(base, method),
                                "|".join(a.available_pricing_method_codes)])
                    rows += 1
    print(f"written: {OUT_PATH} rows={rows}")


if __name__ == "__main__":
    main()
