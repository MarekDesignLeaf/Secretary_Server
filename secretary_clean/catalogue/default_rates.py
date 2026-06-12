"""System default rates for catalogue activities — Oxfordshire-area averages.

Owner-approved editable presets (2026, GBP): base hourly rate per industry,
derived per pricing method, keyword multipliers for notably cheaper/pricier
work. Used as the default rate wherever a tenant has no pricing override;
saving in the app creates an override, reset returns to these values.
"""
from __future__ import annotations

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

RATE_UNITS = {
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

# Flat fees and pass-through items are not scaled by activity keywords.
_UNSCALED_METHODS = ("materialova_polozka", "procento", "cestovni_poplatek")

# (keywords, multiplier) — multipliers stack across groups.
_KEYWORD_FACTORS = [
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


def _method_rate(method: str, hourly: float) -> float:
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


def _keyword_factor(name: str) -> float:
    low = name.lower()
    factor = 1.0
    for words, mult in _KEYWORD_FACTORS:
        if any(w in low for w in words):
            factor *= mult
    return factor


def _round_rate(value: float, method: str) -> float:
    if method == "procento":
        return float(round(value))
    if value <= 0:
        return 0.0
    if value < 1:
        return round(value, 2)
    if value < 20:
        return round(value * 2) / 2
    if value < 100:
        return float(round(value))
    return float(round(value / 5) * 5)


def default_rate(industry_code: str, activity_name: str, pricing_method: str) -> float:
    """Default rate for one activity, in the unit of its pricing method."""
    hourly = INDUSTRY_HOURLY.get(industry_code, 35.0)
    value = _method_rate(pricing_method, hourly)
    if pricing_method not in _UNSCALED_METHODS:
        value *= _keyword_factor(activity_name)
    return _round_rate(value, pricing_method)
