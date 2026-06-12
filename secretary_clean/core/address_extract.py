"""Best-effort address extraction from free-text messages (WhatsApp / SMS).

Tuned for UK addresses (the company operates in Oxfordshire) but also catches
"<house number> <street>" patterns without a postcode. Returns a cleaned
single-line address or None. Never raises.
"""
from __future__ import annotations

import re

# UK postcode, e.g. "OX5 1AB", "SW1A1AA", "M1 1AE".
_UK_POSTCODE = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.IGNORECASE)

# "14 Oxford Road", "221b Baker Street", "5 High St"
_HOUSE_STREET = re.compile(
    r"\b(\d+[A-Za-z]?)\s+([A-ZÁ-Ž][\wá-ž]+(?:\s+[A-ZÁ-Ž][\wá-ž]+){0,3}"
    r"\s+(?:Road|Rd|Street|St|Lane|Ln|Avenue|Ave|Close|Cl|Drive|Dr|Way|Court|Ct|"
    r"Place|Pl|Crescent|Gardens|Grove|Terrace|Hill|Park|Square|ulice|ulici|náměstí))\b",
    re.IGNORECASE)

# Greetings / lead-ins to strip from the front of a captured fragment.
_LEAD_NOISE = re.compile(
    r"^(hi|hello|hey|ahoj|dobr[ýy]\s+den|zdrav[ií]m|moje\s+adresa\s+je|"
    r"adresa\s+je|adresa|it'?s|address\s+is|address|my\s+address\s+is|"
    r"jsem\s+na|najdete\s+m[ěe]\s+na|p[rř]ij[eď]te\s+na)\s*[:,-]?\s*",
    re.IGNORECASE)


def _clean(fragment: str) -> str:
    s = " ".join(fragment.replace("\n", ", ").split())
    s = _LEAD_NOISE.sub("", s).strip(" ,.-")
    # Collapse repeated separators left by stripping.
    s = re.sub(r"\s*,\s*,+", ", ", s)
    return s.strip(" ,.-")


def extract_address(text: str) -> str | None:
    if not text or not text.strip():
        return None

    pc = _UK_POSTCODE.search(text)
    if pc:
        postcode = f"{pc.group(1).upper()} {pc.group(2).upper()}"
        # Take up to ~90 chars before the postcode as the street/town part,
        # starting at the most recent line/sentence break.
        head = text[:pc.start()]
        break_at = max(head.rfind("\n"), head.rfind(". "), head.rfind("! "),
                       head.rfind("? "))
        prefix = head[break_at + 1:] if break_at >= 0 else head
        prefix = prefix[-90:]
        candidate = _clean(f"{prefix} {postcode}")
        # Guard against a lone postcode with stray words: keep it anyway, the
        # postcode alone navigates fine.
        return candidate or postcode

    hs = _HOUSE_STREET.search(text)
    if hs:
        # Extend to the end of the line for town/postcode-less addresses.
        line_end = text.find("\n", hs.start())
        end = line_end if line_end >= 0 else min(len(text), hs.end() + 40)
        return _clean(text[hs.start():end]) or None

    return None
