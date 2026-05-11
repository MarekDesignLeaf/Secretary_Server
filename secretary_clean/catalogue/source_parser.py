"""Parser for secretary_work_types_tree_pricing_logic.txt.

The text file is the primary source of truth for industries, subtypes,
concrete activities, pricing methods and additional charges. This parser keeps
business catalogue ownership in the backend instead of the frontend.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .models import AdditionalCharge, CatalogueSnapshot, Industry, PricingMethod, WorkActivity, WorkSubtype

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_PATH = ROOT / "secretary_work_types_tree_pricing_logic.txt"
_PRICING_START = "DOSTUPNÉ DRUHY VÝPOČTU CENY PRO KAŽDOU ČINNOST"
_CHARGES_START = "DOPLŇKOVÉ POLOŽKY, KTERÉ MOHOU BÝT AKTIVNÍ SOUČASNĚ"
_TREE_START = "STROM ODVĚTVÍ, PODODVĚTVÍ A DRUHŮ PRACÍ"
_STOP_AFTER_TREE = "KONEČNÝ PRINCIP PRO UŽIVATELE"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_value.lower()).strip("_")
    return slug or "item"


def _line_range(lines: list[str], start_marker: str, end_marker: str) -> list[str]:
    start = next(i for i, line in enumerate(lines) if line.strip() == start_marker) + 1
    end = next(i for i, line in enumerate(lines[start:], start) if line.strip() == end_marker)
    return lines[start:end]


def _parse_numbered_items(lines: list[str], model: type[PricingMethod] | type[AdditionalCharge]):
    items = []
    current_number: int | None = None
    current_name: str | None = None
    current_description: list[str] = []
    pattern = re.compile(r"^(\d+)\.\s+(.+)$")

    def flush_current() -> None:
        if current_number is None or not current_name:
            return
        code = slugify(current_name)
        if model is PricingMethod:
            items.append(
                PricingMethod(
                    code=code,
                    name=current_name,
                    description=" ".join(current_description).strip(),
                    unit=_extract_unit(current_description),
                    display_order=current_number,
                )
            )
        else:
            items.append(
                AdditionalCharge(
                    code=code,
                    name=current_name,
                    display_order=current_number,
                )
            )

    for raw in lines:
        line = raw.strip()
        match = pattern.match(line)
        if match:
            flush_current()
            current_number = int(match.group(1))
            current_name = match.group(2).strip()
            current_description = []
        elif current_number is not None and line:
            current_description.append(line)
    flush_current()
    return items


def _extract_unit(description_lines: list[str]) -> str | None:
    for line in description_lines:
        if line.startswith("Jednotka:"):
            return line.split(":", 1)[1].strip().rstrip(".")
    return None


def _default_method_for_activity(activity_name: str, pricing_methods: list[PricingMethod]) -> str:
    """Choose exactly one system default while preserving every method as available.

    The file requires every activity to expose every pricing method and exactly
    one default. The source is a catalogue draft without per-activity defaults,
    so this function derives the backend-owned system recommendation from the
    concrete activity name using the pricing guidance in the same file.
    """
    name = activity_name.lower()
    by_code = {method.code: method.code for method in pricing_methods}
    rules = [
        (("contract", "subscription", "retainer"), "subscription_nebo_retainer"),
        (("stage", "milestone"), "milestone_nebo_stage_payment"),
        (("commission", "sale", "letting", "management fee"), "procento"),
        (("material", "supply", "topsoil", "compost", "gravel", "bark", "plant supply"), "materialova_polozka"),
        (("travel",), "cestovni_poplatek"),
        (("callout", "emergency"), "callout_fee"),
        (("package", "bundle"), "cena_za_balicek"),
        (("skip", "bulk bag", "container", "waste", "removal", "clearance"), "cena_za_bulk_bag_skip_nebo_container"),
        (("tonne", "weight"), "cena_za_vahu"),
        (("excavation", "concrete", "soil", "volume"), "cena_za_objem"),
        (("fencing", "edging", "hedge", "guttering", "pipe", "trench", "length"), "cena_za_delku"),
        (("painting", "flooring", "tiling", "turf", "washing", "area", "m2"), "cena_za_plochu"),
        (("installation", "repair", "replace", "fitting", "camera", "lock", "light", "tree", "post"), "cena_za_kus"),
        (("inspection", "visit", "consultation", "service", "maintenance"), "cena_za_navstevu"),
        (("day", "project management", "team"), "denni_sazba"),
    ]
    for words, code in rules:
        if any(word in name for word in words) and code in by_code:
            return code
    return "hodinova_sazba" if "hodinova_sazba" in by_code else pricing_methods[0].code


def load_catalogue(path: Path = DEFAULT_SOURCE_PATH) -> CatalogueSnapshot:
    lines = path.read_text(encoding="utf-8").splitlines()
    pricing_methods = _parse_numbered_items(
        _line_range(lines, _PRICING_START, _CHARGES_START), PricingMethod
    )
    additional_charges = _parse_numbered_items(
        _line_range(lines, _CHARGES_START, "DATOVÝ PRINCIP"), AdditionalCharge
    )
    industries: list[Industry] = []
    current_industry: Industry | None = None
    current_subtype: WorkSubtype | None = None
    tree_started = False
    industry_pattern = re.compile(r"^(\d+)\.\s+([A-Z][A-Z0-9 /&,-]+)$")
    subtype_pattern = re.compile(r"^(\d+)\.(\d+)\s+(.+)$")
    pricing_codes = [method.code for method in pricing_methods]

    for raw in lines:
        if raw.strip() == _TREE_START:
            tree_started = True
            continue
        if not tree_started:
            continue
        if raw.strip() == _STOP_AFTER_TREE:
            break
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        industry_match = industry_pattern.match(stripped)
        if industry_match and "." not in industry_match.group(1):
            current_industry = Industry(
                code=slugify(industry_match.group(2)),
                name=industry_match.group(2).title(),
                display_order=int(industry_match.group(1)),
            )
            industries.append(current_industry)
            current_subtype = None
            continue
        subtype_match = subtype_pattern.match(stripped)
        if subtype_match:
            if current_industry is None:
                raise ValueError(f"Subtype without industry: {stripped}")
            current_subtype = WorkSubtype(
                code=f"{current_industry.code}.{slugify(subtype_match.group(3))}",
                name=subtype_match.group(3).strip(),
                industry_code=current_industry.code,
                display_order=int(subtype_match.group(2)),
            )
            current_industry.subtypes.append(current_subtype)
            continue
        if line.startswith("    ") and current_industry:
            if current_subtype is None:
                current_subtype = WorkSubtype(
                    code=f"{current_industry.code}.{slugify(current_industry.name)}",
                    name=current_industry.name.title(),
                    industry_code=current_industry.code,
                    display_order=1,
                )
                current_industry.subtypes.append(current_subtype)
            activity_name = stripped
            activity = WorkActivity(
                code=f"{current_subtype.code}.{slugify(activity_name)}",
                name=activity_name,
                industry_code=current_industry.code,
                subtype_code=current_subtype.code,
                available_pricing_method_codes=pricing_codes.copy(),
                default_pricing_method_code=_default_method_for_activity(activity_name, pricing_methods),
            )
            current_subtype.activities.append(activity)

    snapshot = CatalogueSnapshot(
        industries=industries,
        pricing_methods=pricing_methods,
        additional_charges=additional_charges,
    )
    summary = snapshot.validation_summary()
    if any(not industry.subtypes for industry in snapshot.industries):
        raise ValueError("Catalogue source contains an industry without a subtype")
    if not summary["every_subtype_has_activities"]:
        raise ValueError("Catalogue source contains an empty subtype")
    if not summary["every_activity_has_all_pricing_methods"]:
        raise ValueError("Catalogue activity is missing available pricing methods")
    if not summary["every_activity_has_exactly_one_default_method"]:
        raise ValueError("Catalogue activity must have exactly one default pricing method")
    return snapshot
