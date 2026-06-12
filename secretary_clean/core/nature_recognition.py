"""Plant / plant-disease / mushroom recognition from a photo via OpenAI vision.

Returns dicts shaped to the Android response models (PlantRecognitionResponse,
PlantDiseaseResponse, MushroomRecognitionResponse). Requires OPENAI_API_KEY;
without it every call returns a graceful "not configured" result. Never raises.

SAFETY: mushroom identification must never be used to decide edibility — the
spoken summary always carries a hard warning.
"""
from __future__ import annotations

import base64
import json
import os

_LANG_NAME = {"cs": "Czech", "en": "English", "pl": "Polish"}

MUSHROOM_SAFETY = {
    "cs": " VAROVÁNÍ: Nikdy nejez houbu určenou z fotky — určení může být chybné a záměna bývá smrtelná. Vždy nech ověřit odborníkem.",
    "en": " WARNING: Never eat a mushroom identified from a photo — identification can be wrong and mistakes can be fatal. Always have it checked by an expert.",
    "pl": " OSTRZEŻENIE: Nigdy nie jedz grzyba rozpoznanego ze zdjęcia — identyfikacja może być błędna i pomyłki bywają śmiertelne.",
}


def is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _lang(code: str | None) -> str:
    return _LANG_NAME.get((code or "").split("-")[0].lower(), "English")


def _vision(image_bytes: bytes, system: str, user: str) -> dict | None:
    """Call gpt-4o with one image, expect a JSON object back. None on failure."""
    if not is_configured():
        return None
    try:
        from openai import OpenAI
        client = OpenAI()
        b64 = base64.b64encode(image_bytes).decode()
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": user},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]},
            ],
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001
        return None


def identify_plant(image_bytes: bytes, language: str | None = None) -> dict:
    lang = _lang(language)
    raw = _vision(
        image_bytes,
        f"You are a botanist. Identify the plant in the photo. Reply as JSON.",
        f"Return JSON with keys: display_name (common name in {lang}), "
        f"scientific_name, family, genus, score (0..1 confidence), "
        f"guidance (1-2 sentences of care tips in {lang}), "
        f"spoken_summary (one natural {lang} sentence naming the plant and confidence). "
        f"If unsure, say so in spoken_summary.")
    if raw is None:
        return _unconfigured("plant", language)
    return {
        "database": "OpenAI Vision",
        "display_name": raw.get("display_name") or raw.get("scientific_name") or "",
        "scientific_name": raw.get("scientific_name") or "",
        "common_names": raw.get("common_names") or [],
        "family": raw.get("family"),
        "genus": raw.get("genus"),
        "score": float(raw.get("score") or 0.0),
        "organs": [],
        "guidance": raw.get("guidance"),
        "spoken_summary": raw.get("spoken_summary") or raw.get("display_name") or "",
        "suggestions": [],
    }


def assess_health(image_bytes: bytes, language: str | None = None) -> dict:
    lang = _lang(language)
    raw = _vision(
        image_bytes,
        "You are a plant pathologist. Assess the plant's health from the photo. Reply as JSON.",
        f"Return JSON with keys: is_healthy (bool), health_probability (0..1), "
        f"top_issue_name (disease/pest name in {lang} or null), top_issue_probability (0..1), "
        f"top_issue_description (in {lang}), guidance (treatment/prevention tips in {lang}), "
        f"spoken_summary (one natural {lang} sentence about health and the main issue).")
    if raw is None:
        return _unconfigured("health", language)
    return {
        "database": "OpenAI Vision",
        "is_healthy": bool(raw.get("is_healthy")),
        "health_probability": float(raw.get("health_probability") or 0.0),
        "top_issue_name": raw.get("top_issue_name"),
        "top_issue_common_names": raw.get("top_issue_common_names") or [],
        "top_issue_probability": float(raw.get("top_issue_probability") or 0.0),
        "top_issue_description": raw.get("top_issue_description"),
        "guidance": raw.get("guidance"),
        "spoken_summary": raw.get("spoken_summary") or "",
        "suggestions": [],
    }


def identify_mushroom(image_bytes: bytes, language: str | None = None) -> dict:
    lang = _lang(language)
    code = (language or "").split("-")[0].lower()
    raw = _vision(
        image_bytes,
        "You are a mycologist. Identify the mushroom from the photo. Reply as JSON. "
        "NEVER claim it is safe to eat.",
        f"Return JSON with keys: display_name (common name in {lang}), scientific_name, "
        f"family, genus, probability (0..1), description (in {lang}), "
        f"look_alikes (list of similar species), characteristics (list), "
        f"spoken_summary (one natural {lang} sentence naming the mushroom and confidence; "
        f"do NOT state edibility).")
    if raw is None:
        return _unconfigured("mushroom", language)
    spoken = (raw.get("spoken_summary") or raw.get("display_name") or "")
    spoken += MUSHROOM_SAFETY.get(code, MUSHROOM_SAFETY["en"])
    return {
        "database": "OpenAI Vision",
        "display_name": raw.get("display_name") or raw.get("scientific_name") or "",
        "scientific_name": raw.get("scientific_name") or "",
        "common_names": raw.get("common_names") or [],
        "probability": float(raw.get("probability") or 0.0),
        "description": raw.get("description"),
        "url": None,
        "edibility": "neuvedeno / not assessed",
        "psychoactive": None,
        "family": raw.get("family"),
        "genus": raw.get("genus"),
        "look_alikes": raw.get("look_alikes") or [],
        "characteristics": raw.get("characteristics") or [],
        "guidance": None,
        "spoken_summary": spoken,
        "suggestions": [],
    }


def _unconfigured(kind: str, language: str | None) -> dict:
    code = (language or "").split("-")[0].lower()
    msg = {
        "cs": "Rozpoznávání z fotek není na serveru nastavené (chybí OpenAI klíč).",
        "en": "Photo recognition is not configured on the server (missing OpenAI key).",
        "pl": "Rozpoznawanie ze zdjęć nie jest skonfigurowane (brak klucza OpenAI).",
    }.get(code, "Photo recognition is not configured on the server.")
    base = {"database": "unconfigured", "spoken_summary": msg, "suggestions": []}
    if kind == "health":
        base.update({"is_healthy": False, "health_probability": 0.0})
    else:
        base.update({"display_name": "", "scientific_name": ""})
    return base
