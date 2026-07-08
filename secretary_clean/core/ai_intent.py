"""AI intent detection — fallback when the deterministic parser can't classify.

Maps a free-form utterance to one of the known intents + entities using an LLM,
so any natural phrasing works ("intuitive variations"). Requires OPENAI_API_KEY;
without it returns None (caller keeps the deterministic-only behaviour).

The execute layer caches successful classifications per tenant (learning), so a
phrasing the AI resolved once is recognized instantly next time.
"""
from __future__ import annotations

import json
import os

# Intent catalogue shown to the model — DERIVED from the single source of
# truth (voice_intent_registry) so it can never drift from what the executor
# actually supports (v2 rewrite: replaces a hand-maintained duplicate list).
def _build_catalogue() -> dict[str, tuple[str, list[str]]]:
    from secretary_clean.core import voice_intent_registry as _reg
    out: dict[str, tuple[str, list[str]]] = {}
    for code, spec in _reg.REGISTRY.items():
        if not spec.is_implemented or not spec.is_active:
            continue
        out[code] = (spec.description,
                     list(spec.required_entities) + list(spec.optional_entities))
    return out


INTENT_CATALOGUE: dict[str, tuple[str, list[str]]] = _build_catalogue()


def is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def supported() -> set[str]:
    return set(INTENT_CATALOGUE)


def classify(utterance: str, language: str | None = None) -> dict | None:
    """Return {"intent": str, "entities": dict, "confidence": float} or None."""
    if not utterance.strip() or not is_configured():
        return None
    catalogue = "\n".join(
        f"- {name}: {desc} (entities: {', '.join(keys) or 'none'})"
        for name, (desc, keys) in INTENT_CATALOGUE.items())
    system = (
        "You are the intent router for a voice-controlled business assistant. "
        "Map the user's utterance to exactly ONE intent from the list, or null if "
        "none fits. Extract any entities you can. Dates as YYYY-MM-DD, times as "
        "HH:MM (24h) when present; otherwise omit. Reply as JSON: "
        '{"intent": "<name|null>", "entities": {...}, "confidence": 0..1}.')
    user = f"Intents:\n{catalogue}\n\nUtterance: {utterance}"
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_INTENT_MODEL", "gpt-4o-mini"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001 — AI is best-effort
        return None

    intent = data.get("intent")
    if intent not in INTENT_CATALOGUE:
        return None
    if float(data.get("confidence") or 0) < 0.55:
        return None
    entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
    # Normalise date+time into start_at the way the deterministic parser does.
    if entities.get("date"):
        t = entities.get("time")
        entities["start_at"] = (f"{entities['date']}T{t}:00Z" if t
                                else f"{entities['date']}T00:00:00Z")
    return {"intent": intent, "entities": entities,
            "confidence": float(data.get("confidence") or 0)}
