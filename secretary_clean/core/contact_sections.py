"""Pre-set contact directory sections (groups) seeded per company.

The Android Contacts Directory groups contacts by `section_code`. Without
sections the import dialog has nothing to assign and the import is impossible —
so every company starts with these defaults. Codes match the app's canonical
voice-sorting / display map (MainActivity SECTION_VOICE_MAP/SECTION_DISPLAY)
where one exists, so voice sorting and manual import agree.

Covers the requested groups: zákazníci (client), rodina (family),
přátelé (friends), dodavatelé (material_supplier), subdodavatelé (subcontractor),
plus employees, rentals, private and other.
"""
from __future__ import annotations

import re
import unicodedata

# (section_code, default Czech display name, sort_order)
DEFAULT_CONTACT_SECTIONS: list[tuple[str, str, int]] = [
    ("client", "Zákazníci", 10),
    ("private", "Soukromé", 20),
    ("family", "Rodina", 30),
    ("friends", "Přátelé", 40),
    ("subcontractor", "Subdodavatelé", 50),
    ("employee", "Zaměstnanci", 60),
    ("material_supplier", "Dodavatelé", 70),
    ("equipment_vehicle_rental", "Půjčovny", 80),
    ("other", "Ostatní", 90),
]


def slugify_section(name: str) -> str:
    """Make a stable section_code from a display name ('VIP klienti' -> 'vip_klienti')."""
    s = "".join(c for c in unicodedata.normalize("NFKD", name or "")
                if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "section"
