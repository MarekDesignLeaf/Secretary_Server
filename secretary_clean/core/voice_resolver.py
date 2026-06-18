"""Deterministic voice resolution pipeline (Voice Command Learning — Phase 1, §2).

resolve() runs an utterance through the ordered pipeline and returns a structured
`Resolution`. It is pure and testable — NO HTTP, NO AI, NO DB. The HTTP layer
(voice.py) supplies the optional `alias_lookup` hook (Phase 2) and runs entity
extraction + execution after resolution.

Pipeline order (each stage can short-circuit):
  A. normalize
  B. pure-cancel detection            → unknown.cancel
  C. builtin synonym / phrase match   → intent (EXPLICIT 0.95 / COMPOSED 0.9)
  D. tenant/user alias lookup (hook)  → intent (ACTIVE / PENDING)
  E. deterministic parser fallback    → intent (parser confidence)
  F. ambiguity check across same-tier candidates → AMBIGUOUS (ask, never guess)

The AI fallback (D' in the design) intentionally stays in voice.py; the resolver
is deterministic so it can be unit-tested without network calls. When the
resolver returns UNKNOWN, voice.py may still try the AI classifier before
dropping into the learning dialog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from secretary_clean.core import voice_intent_registry as reg
from secretary_clean.core import voice_synonyms as syn
from secretary_clean.core import voice_intents as vi

# Pure-cancel words (normalized, diacritics-free). Used ONLY when the whole
# utterance is essentially a cancel — a long dictated note that merely contains
# "stop", or a real command like "zruš schůzku" (where "zruš" = delete verb),
# must NOT be treated as a cancel here.
_CANCEL_PHRASES = (
    "omyl", "zrus", "zrusit", "zrus to", "neplatny prikaz", "neplatny",
    "to nic", "nic", "zapomen", "zapomen na to", "nech to byt", "cancel",
    "stop", "konec", "nechci", "anuluj",
)
# Words allowed alongside a cancel token in a "pure cancel" utterance. If any
# word is neither a cancel token nor a filler, it is a real command, not a cancel
# (e.g. "zruš SCHŮZKU" → the object word blocks cancel classification).
_CANCEL_FILLERS = frozenset({
    "to", "na", "byt", "uz", "tak", "ne", "mi", "si", "ten", "ta", "tu",
    "prosim", "uz", "this", "that", "it", "please", "just", "no", "nevadi",
})
_CANCEL_TOKENS = frozenset(w for p in _CANCEL_PHRASES for w in p.split())
_MAX_CANCEL_WORDS = 5

# Match-type tiers, best first. A longer/explicit registry phrase always beats a
# bare action+object composition, which prevents spurious ambiguity (e.g.
# "vytvoř úkol pro klienta Nováka" resolves to task.create, not an X-or-Y ask).
_TIER_RANK = {"EXPLICIT": 2, "COMPOSED": 1}


@dataclass
class Resolution:
    intent: Optional[str]
    confidence: float
    source: str        # CANCEL/BUILTIN_SYNONYM/USER_ALIAS/PENDING_ALIAS/PARSER/UNKNOWN/AMBIGUOUS
    band: str          # HIGH / MEDIUM / LOW
    requires_confirmation: bool = False
    is_implemented: bool = True
    normalized: str = ""
    candidates: list[tuple[str, float]] = field(default_factory=list)
    reason: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return self.source == "AMBIGUOUS"


@dataclass
class _Cand:
    intent: str
    confidence: float
    tier: str
    phrase_len: int = 0

    @property
    def sort_key(self):
        return (_TIER_RANK.get(self.tier, 0), self.confidence, self.phrase_len)


def is_pure_cancel(text: str) -> bool:
    """A cancel only when the WHOLE short utterance is cancel words + fillers.
    "zruš schůzku" is a delete command (object word present), not a cancel."""
    norm = syn.normalize(text)
    if not norm:
        return False
    words = norm.split()
    if len(words) > _MAX_CANCEL_WORDS:
        return False
    if not any(w in _CANCEL_TOKENS for w in words):
        return False
    return all(w in _CANCEL_TOKENS or w in _CANCEL_FILLERS for w in words)


def _phrase_in(norm: str, phrase: str) -> bool:
    """True if `phrase` (already normalized) appears as a consecutive run of
    whole words inside `norm`."""
    if not phrase:
        return False
    nt, pt = norm.split(), phrase.split()
    if not pt or len(pt) > len(nt):
        return norm == phrase
    for i in range(len(nt) - len(pt) + 1):
        if nt[i:i + len(pt)] == pt:
            return True
    return False


def _builtin_candidates(norm: str) -> list[_Cand]:
    out: list[_Cand] = []
    # Explicit whole-phrase matches against the registry.
    for code, intent in reg.REGISTRY.items():
        best_len = 0
        for phrase in intent.all_phrases:
            np = syn.normalize(phrase)
            if not np:
                continue
            if norm == np:
                best_len = max(best_len, len(np.split()) + 1)  # full match slightly favoured
            elif _phrase_in(norm, np):
                best_len = max(best_len, len(np.split()))
        if best_len:
            out.append(_Cand(code, reg.PHRASE_MATCH_CONFIDENCE, "EXPLICIT", best_len))
    # Composed action+object candidates.
    for intent_code, conf in syn.compose_intent(norm):
        out.append(_Cand(intent_code, conf, "COMPOSED", 1))
    return out


def _dedupe_best(cands: list[_Cand]) -> dict[str, _Cand]:
    best: dict[str, _Cand] = {}
    for c in cands:
        cur = best.get(c.intent)
        if cur is None or c.sort_key > cur.sort_key:
            best[c.intent] = c
    return best


def _finish(intent_code: str, confidence: float, source: str, *,
            norm: str, candidates=None, reason="") -> Resolution:
    return Resolution(
        intent=intent_code,
        confidence=confidence,
        source=source,
        band=reg.band(confidence),
        requires_confirmation=reg.requires_confirmation(intent_code),
        is_implemented=reg.is_implemented(intent_code),
        normalized=norm,
        candidates=candidates or [(intent_code, confidence)],
        reason=reason,
    )


def resolve(
    utterance: str,
    *,
    alias_lookup: Optional[Callable[[str], Optional[dict]]] = None,
    parser: Callable[..., "vi.ParsedIntent"] = vi.parse_intent,
) -> Resolution:
    """Resolve an utterance into a `Resolution`. `alias_lookup(normalized)` may
    return {intent, confidence, status, alias_id, scope} or None (Phase 2)."""
    norm = syn.normalize(utterance)

    # ── B. pure cancel ────────────────────────────────────────────────────
    if is_pure_cancel(utterance):
        return Resolution(intent="unknown.cancel", confidence=1.0, source="CANCEL",
                          band="HIGH", normalized=norm, candidates=[("unknown.cancel", 1.0)],
                          reason="Pure cancel phrase.")

    # ── C. builtin synonym / phrase match ─────────────────────────────────
    builtin = _dedupe_best(_builtin_candidates(norm))
    if builtin:
        ranked = sorted(builtin.values(), key=lambda c: c.sort_key, reverse=True)
        best = ranked[0]
        # F. ambiguity — only between candidates at the SAME (top) tier.
        same_tier = [c for c in ranked if c.tier == best.tier and c.intent != best.intent]
        if same_tier:
            second = same_tier[0]
            close = (best.confidence - second.confidence) < reg.AMBIGUITY_MARGIN
            if close and second.confidence >= reg.MEDIUM_THRESHOLD:
                cands = [(best.intent, best.confidence), (second.intent, second.confidence)]
                return Resolution(intent=None, confidence=best.confidence, source="AMBIGUOUS",
                                  band=reg.band(best.confidence), normalized=norm,
                                  candidates=cands, reason="Two intents tie within margin.")
        return _finish(best.intent, best.confidence, "BUILTIN_SYNONYM",
                       norm=norm, candidates=[(c.intent, c.confidence) for c in ranked[:3]],
                       reason="Matched pre-built synonym dictionary.")

    # ── D. tenant/user alias lookup (hook) ────────────────────────────────
    if alias_lookup is not None:
        hit = alias_lookup(norm)
        if hit and hit.get("intent"):
            status = (hit.get("status") or "ACTIVE").upper()
            conf = float(hit.get("confidence") or 1.0)
            if status == "ACTIVE":
                return _finish(hit["intent"], conf, "USER_ALIAS", norm=norm,
                               reason="Matched an active tenant alias.")
            if status == "PENDING":
                r = _finish(hit["intent"], conf, "PENDING_ALIAS", norm=norm,
                            reason="Matched a pending alias (target not implemented yet).")
                return r

    # ── E. deterministic parser fallback ──────────────────────────────────
    parsed = parser(utterance)
    if parsed and parsed.intent:
        conf = parsed.confidence
        return Resolution(
            intent=parsed.intent, confidence=conf, source="PARSER", band=reg.band(conf),
            requires_confirmation=reg.requires_confirmation(parsed.intent) or parsed.requires_confirmation,
            is_implemented=reg.is_implemented(parsed.intent),
            normalized=norm, candidates=[(parsed.intent, conf)],
            reason=parsed.reason or "Deterministic parser match.",
        )

    # ── Unknown — voice.py may still try AI, then the learning dialog ──────
    return Resolution(intent=None, confidence=0.0, source="UNKNOWN", band="LOW",
                      normalized=norm, candidates=[], reason="No intent matched.")
