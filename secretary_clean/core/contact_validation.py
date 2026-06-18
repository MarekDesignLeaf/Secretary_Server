"""Contact field validation & normalization for CRM writes.

A client/contact must hold a *real* phone number — "1234" is not a valid phone
and must be rejected at every write path (manual create/update, bulk import, and
voice). Email, when present, must be well-formed. Pure functions, no DB / HTTP.

Phone rule: 9–15 digits (E.164 caps at 15; Czech numbers are 9). Formatting
characters (+ ( ) / - . space) are allowed and stripped; a leading "+" is kept.
"""
from __future__ import annotations

import re

PHONE_MIN_DIGITS = 9
PHONE_MAX_DIGITS = 15

# Keys across CRM payloads that carry a phone or an email.
PHONE_KEYS = ("phone", "phone_primary", "phone_secondary", "mobile",
              "contact_phone", "target_phone", "source_phone")
EMAIL_KEYS = ("email", "email_primary", "email_secondary", "contact_email")

_LABELS = {
    "phone": "Telefon", "phone_primary": "Telefon", "phone_secondary": "Druhý telefon",
    "mobile": "Mobil", "contact_phone": "Telefon", "target_phone": "Telefon",
    "source_phone": "Telefon",
    "email": "E-mail", "email_primary": "E-mail", "email_secondary": "Druhý e-mail",
    "contact_email": "E-mail",
}

_PHONE_ALLOWED = re.compile(r"^[+()/\-.\s\d]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(raw) -> tuple[str | None, str | None]:
    """Return (normalized, error). Empty input → (None, None): a phone is
    optional, but a *present* one must be valid. "1234" → (None, error)."""
    s = ("" if raw is None else str(raw)).strip()
    if not s:
        return None, None
    if not _PHONE_ALLOWED.match(s):
        return None, "obsahuje nepovolené znaky"
    digits = re.sub(r"\D", "", s)
    if len(digits) < PHONE_MIN_DIGITS:
        return None, f"musí mít aspoň {PHONE_MIN_DIGITS} číslic"
    if len(digits) > PHONE_MAX_DIGITS:
        return None, f"má příliš mnoho číslic (max {PHONE_MAX_DIGITS})"
    normalized = ("+" + digits) if s.startswith("+") else digits
    return normalized, None


def normalize_email(raw) -> tuple[str | None, str | None]:
    """Return (normalized_lowercased, error). Empty → (None, None)."""
    s = ("" if raw is None else str(raw)).strip()
    if not s:
        return None, None
    if not _EMAIL_RE.match(s):
        return None, "nemá platný formát"
    return s.lower(), None


def validate_and_normalize(data: dict) -> tuple[dict, list[str]]:
    """Normalize every known phone/email key in a CRM payload/data dict.
    Returns (new_dict, errors). Valid values are rewritten in canonical form;
    empty values are left untouched. Does not mutate the input."""
    out = dict(data or {})
    errors: list[str] = []
    for k in PHONE_KEYS:
        if out.get(k) not in (None, ""):
            norm, err = normalize_phone(out[k])
            if err:
                errors.append(f"{_LABELS.get(k, k)} „{out[k]}“ {err}.")
            else:
                out[k] = norm
    for k in EMAIL_KEYS:
        if out.get(k) not in (None, ""):
            norm, err = normalize_email(out[k])
            if err:
                errors.append(f"{_LABELS.get(k, k)} „{out[k]}“ {err}.")
            else:
                out[k] = norm
    return out, errors
