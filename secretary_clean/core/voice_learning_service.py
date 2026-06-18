"""Glue between the pure resolver and the repository (Voice Command Learning).

Builds the alias-lookup hook the resolver consumes, resolves a teach target to a
known intent, derives alias status (ACTIVE vs PENDING), constructs alias /
learning-event records, and persists learning events.

SECURITY: nothing here grants any permission. An alias is only a phrase→intent
translation; the target intent's own permission is always checked by the caller
at execution time. Recording a learning event must never break command flow.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from secretary_clean.core import voice_intent_registry as reg
from secretary_clean.core import voice_synonyms as syn
from secretary_clean.core import voice_resolver as vr
from secretary_clean.core.models import VoiceCommandAlias, VoiceLearningEvent


def build_alias_lookup(repository, company_id: str, user_id: str | None):
    """Return a `lookup(normalized)->dict|None` hook for voice_resolver.resolve."""
    def lookup(normalized: str):
        a = repository.find_voice_alias(company_id, normalized, user_id)
        if not a:
            return None
        return {"intent": a.target_intent, "confidence": a.confidence,
                "status": a.status, "alias_id": a.id}
    return lookup


def resolve_target_intent(text_or_code: str | None) -> str | None:
    """Map an explicit intent code OR a natural-language answer ('vytvoř fakturu')
    to a KNOWN intent code. Returns None if it can't be mapped to a real intent."""
    if not text_or_code:
        return None
    s = text_or_code.strip()
    if reg.is_known(s):
        return s
    res = vr.resolve(s)
    if res.intent and reg.is_known(res.intent):
        return res.intent
    # Planned modules the resolver doesn't execute yet (keyword map).
    try:
        from secretary_clean.core import alias_learning as al
        intent = al.resolve_target_intent(s)
        if intent and reg.is_known(intent):
            return intent
    except Exception:
        pass
    return None


def status_for(intent_code: str) -> str:
    """ACTIVE if the target is executable today, else PENDING (auto-activates
    when the module ships — design §10)."""
    return "ACTIVE" if reg.is_implemented(intent_code) else "PENDING"


def find_exact_alias(repository, company_id: str, user_id: str | None, normalized: str):
    """Locate an existing alias with the SAME (company, scope, phrase) regardless
    of status — so re-teaching a disabled phrase reactivates it instead of
    violating the unique constraint."""
    for a in repository.list_voice_aliases(company_id, status=None, include_global=False):
        if a.normalized_phrase == normalized and a.user_id == user_id:
            return a
    return None


def new_alias(company_id: str, user_id: str | None, raw_phrase: str, target_intent: str,
              *, language_code: str | None = None, source: str = "user_learning",
              created_by: str | None = None, is_global: bool = False,
              confidence: float = 1.0) -> VoiceCommandAlias:
    now = datetime.now(timezone.utc)
    return VoiceCommandAlias(
        id=str(uuid.uuid4()), company_id=company_id, user_id=user_id,
        raw_phrase=raw_phrase, normalized_phrase=syn.normalize(raw_phrase),
        target_intent=target_intent, language_code=language_code,
        status=status_for(target_intent), confidence=confidence, source=source,
        created_by=created_by, created_at=now, updated_at=now, is_global=is_global)


def record_event(repository, company_id: str, user_id: str | None, raw_input: str,
                 resolution_type: str, *, resolved_intent: str | None = None,
                 confidence: float | None = None, was_executed: bool = False,
                 was_confirmed: bool = False, created_alias_id: str | None = None,
                 metadata: dict | None = None) -> VoiceLearningEvent:
    now = datetime.now(timezone.utc)
    ev = VoiceLearningEvent(
        id=str(uuid.uuid4()), company_id=company_id, user_id=user_id,
        raw_input=raw_input, normalized_input=syn.normalize(raw_input),
        resolved_intent=resolved_intent, resolution_type=resolution_type,
        confidence=confidence, was_executed=was_executed, was_confirmed=was_confirmed,
        created_alias_id=created_alias_id, created_at=now, metadata=metadata or {})
    try:
        repository.record_voice_learning_event(ev)
    except Exception:
        pass  # audit logging must never break command execution
    return ev
