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


# ── Mushrooms: faithful port of the original (commit 440aa04). Uses the
#    Kindwise mushroom.id API for the actual identification + edibility /
#    psychoactive / look-alike data; OpenAI only writes the human guidance. ──

def _mushroom_api_key() -> str:
    for k in ("MUSHROOM_ID_API_KEY", "MUSHROOM_API_KEY", "MUSHROOMID_API_KEY",
              "MUSHROOM_RECOGNITION_API_KEY", "KINDWISE_MUSHROOM_API_KEY"):
        v = os.environ.get(k)
        if v:
            return v
    return ""


def _mushroom_api_url() -> str:
    for k in ("MUSHROOM_ID_API_URL", "MUSHROOM_API_URL", "KINDWISE_MUSHROOM_API_URL"):
        v = os.environ.get(k)
        if v:
            return v
    return "https://mushroom.kindwise.com/api/v1/identification"


def mushroom_is_configured() -> bool:
    return bool(_mushroom_api_key())


def _tr(code: str, en: str, cs: str, pl: str) -> str:
    return {"cs": cs, "pl": pl}.get(code, en)


def _flatten_list(value) -> list[str]:
    out: list[str] = []

    def app(c):
        if c is None:
            return
        if isinstance(c, str):
            t = c.strip()
            if t:
                out.append(t)
        elif isinstance(c, (int, float)):
            out.append(str(c))
        elif isinstance(c, dict):
            primary = (c.get("scientific_name") or c.get("scientificName")
                       or c.get("common_name") or c.get("name") or c.get("label")
                       or c.get("title") or c.get("value") or c.get("text"))
            if isinstance(primary, (str, int, float)):
                app(primary)
            else:
                for n in c.values():
                    app(n)
        elif isinstance(c, list):
            for n in c:
                app(n)

    app(value)
    return list(dict.fromkeys(out))


def _flatten_text(value) -> str:
    items = _flatten_list(value)
    return items[0] if items else ""


def _flatten_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "ano", "tak", "1"):
            return True
        if low in ("false", "no", "ne", "nie", "0"):
            return False
    return None


def _score(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _extract_suggestions(raw) -> list[dict]:
    cands: list[dict] = []
    if isinstance(raw, dict):
        result = raw.get("result")
        if isinstance(result, dict):
            cl = result.get("classification")
            if isinstance(cl, dict) and isinstance(cl.get("suggestions"), list):
                cands += [i for i in cl["suggestions"] if isinstance(i, dict)]
            if isinstance(result.get("suggestions"), list):
                cands += [i for i in result["suggestions"] if isinstance(i, dict)]
        cl = raw.get("classification")
        if isinstance(cl, dict) and isinstance(cl.get("suggestions"), list):
            cands += [i for i in cl["suggestions"] if isinstance(i, dict)]
        if isinstance(raw.get("suggestions"), list):
            cands += [i for i in raw["suggestions"] if isinstance(i, dict)]
    if isinstance(raw, list):
        cands += [i for i in raw if isinstance(i, dict)]
    return cands


def _mushroom_guidance(code: str, display_name: str, scientific_name: str,
                       description: str, edibility: str, look_alikes: list[str]) -> tuple[str, str]:
    unknown = _tr(code, "unknown", "neuvedeno", "nie podano")
    common = display_name or scientific_name or _tr(code, "the mushroom", "houba", "grzyb")
    spoken = _tr(
        code,
        f"It is most likely {common}. Never confirm edibility from a photo alone.",
        f"Nejspíš je to {common}. Jedlost nikdy nepotvrzuj jen podle fotografie.",
        f"Najprawdopodobniej to {common}. Nigdy nie potwierdzaj jadalności wyłącznie na podstawie zdjęcia.")
    if not is_configured():  # no OpenAI -> guidance == spoken (original fallback)
        return spoken, spoken
    lookalikes = ", ".join(look_alikes[:3]) if look_alikes else unknown
    instruction = _tr(
        code,
        "Write in English. Keep it concise and practical. Always stress that edibility must not be confirmed from a photo alone.",
        "Piš česky. Buď stručný a praktický. Vždy zdůrazni, že jedlost se nesmí potvrzovat jen podle fotografie.",
        "Pisz po polsku. Bądź zwięzły i praktyczny. Zawsze podkreślaj, że nie wolno potwierdzać jadalności wyłącznie na podstawie zdjęcia.")
    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = (f"Mushroom identified as {common} ({scientific_name}).\n"
                  f"Description: {description or unknown}\nEdibility: {edibility or unknown}\n"
                  f"Look-alikes: {lookalikes}\n{instruction}\n"
                  "Return 4 short lines: description, edibility, likely habitat, and a "
                  "warning that photo recognition is not enough to confirm edibility.")
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini"),
            max_tokens=260,
            messages=[
                {"role": "system",
                 "content": "You write short practical mushroom identification summaries with safety warnings."},
                {"role": "user", "content": prompt},
            ])
        guidance = (resp.choices[0].message.content or "").strip()
        return guidance or common, spoken
    except Exception:  # noqa: BLE001
        return spoken, spoken


def identify_mushroom(images: list[bytes], language: str | None = None) -> dict:
    """Original Kindwise mushroom.id flow. `images` is a list of raw photo bytes."""
    code = (language or "").split("-")[0].lower()
    if not mushroom_is_configured():
        return _unconfigured("mushroom", language)
    import httpx
    encoded = [base64.b64encode(b).decode("ascii") for b in images if b]
    if not encoded:
        return _unconfigured("mushroom", language)
    try:
        resp = httpx.post(
            _mushroom_api_url(),
            params={"details": "common_names,url,description,edibility,psychoactive,"
                               "look_alikes,taxonomy,characteristics",
                    "language": code or "en"},
            headers={"Api-Key": _mushroom_api_key()},
            json={"images": encoded, "similar_images": True},
            timeout=45.0,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:  # noqa: BLE001
        msg = _tr(code, f"Mushroom recognition failed: {e}",
                  f"Rozpoznání houby selhalo: {e}",
                  f"Rozpoznanie grzyba nie powiodło się: {e}")
        return {"database": "mushroom.id", "spoken_summary": msg, "suggestions": [],
                "display_name": "", "scientific_name": ""}

    suggestions_raw = _extract_suggestions(raw)
    if not suggestions_raw:
        msg = _tr(code,
                  "No matching mushroom was found. Try clearer photos of the whole mushroom, underside, and stem or base.",
                  "Nebyla nalezena shoda. Zkus jasnější fotky celé houby, spodní strany a třeně nebo báze.",
                  "Nie znaleziono dopasowania. Spróbuj wyraźniejszych zdjęć całego grzyba, spodu oraz trzonu lub podstawy.")
        return {"database": "mushroom.id", "spoken_summary": msg, "suggestions": [],
                "display_name": "", "scientific_name": ""}

    def shape(item: dict) -> dict:
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        taxonomy = details.get("taxonomy") if isinstance(details.get("taxonomy"), dict) else {}
        common_names = _flatten_list(details.get("common_names"))
        name = (item.get("name") or _flatten_text(details.get("scientific_name"))
                or _flatten_text(details.get("name")) or "")
        return {
            "name": name,
            "display_name": common_names[0] if common_names else name,
            "common_names": common_names,
            "probability": _score(item.get("probability") or item.get("score")),
            "description": _flatten_text(details.get("description")),
            "url": _flatten_text(details.get("url")),
            "edibility": _flatten_text(details.get("edibility") or details.get("edible")),
            "psychoactive": _flatten_bool(details.get("psychoactive") or details.get("is_psychoactive")),
            "family": _flatten_text(taxonomy.get("family")),
            "genus": _flatten_text(taxonomy.get("genus")),
            "look_alikes": _flatten_list(details.get("look_alikes")),
            "characteristics": _flatten_list(details.get("characteristics")),
        }

    shaped = [shape(i) for i in suggestions_raw[:5] if isinstance(i, dict)]
    shaped = [s for s in shaped if s.get("name") or s.get("display_name")]
    if not shaped:
        return _unconfigured("mushroom", language)
    top = shaped[0]
    guidance, spoken = _mushroom_guidance(
        code, top["display_name"], top["name"], top.get("description") or "",
        top.get("edibility") or "", top.get("look_alikes") or [])
    return {
        "database": "mushroom.id",
        "display_name": top["display_name"],
        "scientific_name": top["name"],
        "common_names": top["common_names"],
        "probability": top["probability"],
        "description": top["description"],
        "url": top["url"],
        "edibility": top["edibility"],
        "psychoactive": top["psychoactive"],
        "family": top["family"],
        "genus": top["genus"],
        "look_alikes": top["look_alikes"],
        "characteristics": top["characteristics"],
        "guidance": guidance,
        "spoken_summary": spoken,
        "suggestions": [
            {"name": s["name"], "common_names": s["common_names"],
             "probability": s["probability"], "description": s["description"],
             "url": s["url"], "edibility": s["edibility"],
             "psychoactive": s["psychoactive"], "family": s["family"],
             "genus": s["genus"]}
            for s in shaped
        ],
    }


def _unconfigured(kind: str, language: str | None) -> dict:
    code = (language or "").split("-")[0].lower()
    if kind == "mushroom":
        msg = {
            "cs": "Služba pro rozpoznávání hub není nastavená (chybí MUSHROOM_ID_API_KEY).",
            "en": "Mushroom recognition service is not configured (missing MUSHROOM_ID_API_KEY).",
            "pl": "Usługa rozpoznawania grzybów nie jest skonfigurowana (brak MUSHROOM_ID_API_KEY).",
        }.get(code, "Mushroom recognition service is not configured.")
    else:
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
