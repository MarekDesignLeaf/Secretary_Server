"""Translation helper — enforces the internal-language / customer-language rule.

Backend-only: Android asks the server to translate (POST /translate) or the
server translates transparently on outbound/inbound WhatsApp. Uses OpenAI;
without OPENAI_API_KEY everything degrades gracefully to the original text.
"""
from __future__ import annotations

import os


def is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _lang_root(code: str | None) -> str:
    return (code or "").split("-")[0].lower()


def same_language(a: str | None, b: str | None) -> bool:
    return _lang_root(a) == _lang_root(b) or not _lang_root(a) or not _lang_root(b)


def translate_text(
    text: str, target_language: str, source_language: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Returns (ok, translated_text, error). Never raises."""
    if not text.strip():
        return (True, text, None)
    if not is_configured():
        return (False, None, "Translation is not configured (OPENAI_API_KEY missing).")
    try:
        from openai import OpenAI
        client = OpenAI()
        src = f" from {source_language}" if source_language else ""
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini"),
            temperature=0,
            messages=[
                {"role": "system",
                 "content": ("You translate short business messages"
                             f"{src} into {target_language}. Reply with the "
                             "translation only — no quotes, no commentary. "
                             "If the text is already in the target language, "
                             "return it unchanged.")},
                {"role": "user", "content": text},
            ],
        )
        out = (resp.choices[0].message.content or "").strip()
        return (True, out or text, None)
    except Exception as e:  # noqa: BLE001 — provider errors must not break messaging
        return (False, None, str(e))
