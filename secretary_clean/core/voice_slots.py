"""Slot-filling for pending voice actions (Phase A5.2).

Pure, testable logic. Given an intent + collected data, returns which required
fields are still missing and the next question to ask. NO HTTP, NO AI.
"""
from __future__ import annotations

# Required slots per intent and the question to ask when each is missing.
# Order matters: first missing slot is asked first.
_SLOTS = {
    "calendar.create": [
        ("start_at", "Na kdy mám schůzku vytvořit?"),
        ("title_or_person", "S kým nebo jaký název má schůzka mít?"),
    ],
    "client.create": [
        ("name", "Jak se klient jmenuje?"),
        ("phone", "Jaké je telefonní číslo klienta?"),
        ("address", "Jaká je adresa klienta?"),
    ],
    "whatsapp.send": [
        ("person", "Komu mám WhatsApp poslat?"),
        ("message", "Co mám napsat?"),
    ],
    "job.create": [
        ("title", "Jaká zakázka? Řekni název nebo popis."),
    ],
    "task.create": [
        ("title", "Jaký úkol mám vytvořit?"),
    ],
}


def missing_slots(intent: str, data: dict) -> list[str]:
    """Return required slot keys still missing for this intent."""
    missing = []
    for key, _q in _SLOTS.get(intent, []):
        if key == "start_at":
            if not data.get("start_at"):
                missing.append(key)
        elif key == "title_or_person":
            if not data.get("title") and not data.get("person"):
                missing.append(key)
        else:
            if not data.get(key):
                missing.append(key)
    return missing


def next_question(intent: str, missing: list[str]) -> str | None:
    """Question for the first missing slot."""
    if not missing:
        return None
    first = missing[0]
    for key, q in _SLOTS.get(intent, []):
        if key == first:
            return q
    return None


def supported_intents() -> list[str]:
    return list(_SLOTS.keys())
