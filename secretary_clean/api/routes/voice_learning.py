"""Voice Command Learning endpoints (Phase 2).

  GET    /voice/intents            — export the intent registry (audit)
  GET    /voice/aliases            — list tenant aliases (active/pending/disabled)
  POST   /voice/aliases            — teach an alias (phrase → known intent)
  PUT    /voice/aliases/{id}       — remap / re-enable an alias
  DELETE /voice/aliases/{id}       — SOFT disable (status → DISABLED)
  POST   /voice/learning/resolve   — read-only pipeline preview
  GET    /voice/learning/events    — learning-event audit log

SECURITY: an alias is a translation, never a grant. Teaching/editing needs
crm_manage; previewing needs voice_execute. The target intent's own permission
is enforced only at execution (/voice/execute). Everything is tenant-scoped.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.models import Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core import voice_intent_registry as reg
from secretary_clean.core import voice_resolver as vr
from secretary_clean.core import voice_synonyms as syn
from secretary_clean.core import voice_learning_service as vls

router = APIRouter(prefix="/voice", tags=["voice learning"])


class AliasCreateRequest(BaseModel):
    phrase: str
    target_intent: str | None = None     # explicit intent code …
    answer: str | None = None            # … or a natural-language command
    language_code: str | None = None
    company_wide: bool = False           # True → company-wide (user_id = None)


class AliasUpdateRequest(BaseModel):
    target_intent: str | None = None
    status: str | None = None            # ACTIVE / DISABLED
    language_code: str | None = None


class ResolvePreviewRequest(BaseModel):
    utterance: str


# ── Intent registry (audit export) ────────────────────────────────────────────
@router.get("/intents")
def list_intents(user: UserAccount = Depends(require_permission(Permission.voice_execute))):
    return {"count": len(reg.REGISTRY), "intents": reg.export()}


# ── Aliases ───────────────────────────────────────────────────────────────────
@router.get("/aliases")
def list_aliases(
    status: str | None = Query(None),
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    items = repository.list_voice_aliases(user.company_id, status=status)
    return {"aliases": [a.model_dump(mode="json") for a in items]}


@router.post("/aliases")
def create_alias(
    payload: AliasCreateRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    target = vls.resolve_target_intent(payload.target_intent or payload.answer)
    if not target:
        # Unknown target: the explicit teach endpoint refuses; the learning
        # DIALOG (Phase 3) is what routes unknown targets to the admin queue.
        raise HTTPException(status_code=400,
                            detail="Neznámý cílový příkaz. Zkus to říct jinak.")
    user_id = None if payload.company_wide else user.id
    norm = syn.normalize(payload.phrase)
    existing = vls.find_exact_alias(repository, user.company_id, user_id, norm)
    if existing is not None:
        existing.raw_phrase = payload.phrase.strip()
        existing.target_intent = target
        existing.status = vls.status_for(target)
        if payload.language_code is not None:
            existing.language_code = payload.language_code
        repository.update_voice_alias(existing)
        alias = existing
    else:
        alias = vls.new_alias(user.company_id, user_id, payload.phrase.strip(), target,
                              language_code=payload.language_code, created_by=user.id)
        repository.create_voice_alias(alias)
    vls.record_event(
        repository, user.company_id, user.id, payload.phrase,
        "USER_ALIAS" if alias.status == "ACTIVE" else "PENDING_ALIAS",
        resolved_intent=target, created_alias_id=alias.id,
        metadata={"taught": True, "company_wide": payload.company_wide})
    return {"alias": alias.model_dump(mode="json"), "status": alias.status,
            "target_intent": target}


@router.put("/aliases/{alias_id}")
def update_alias(
    alias_id: str,
    payload: AliasUpdateRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    a = repository.get_voice_alias(alias_id, user.company_id)
    if not a or a.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Alias nenalezen.")
    if payload.target_intent:
        target = vls.resolve_target_intent(payload.target_intent)
        if not target:
            raise HTTPException(status_code=400, detail="Neznámý cílový příkaz.")
        a.target_intent = target
        a.status = vls.status_for(target)
    if payload.status in ("ACTIVE", "DISABLED"):
        a.status = payload.status
    if payload.language_code is not None:
        a.language_code = payload.language_code
    repository.update_voice_alias(a)
    return {"alias": a.model_dump(mode="json")}


@router.delete("/aliases/{alias_id}")
def disable_alias(
    alias_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    a = repository.get_voice_alias(alias_id, user.company_id)
    if not a or a.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Alias nenalezen.")
    a.status = "DISABLED"   # SOFT delete — never hard-delete (audit + remap)
    repository.update_voice_alias(a)
    return {"status": "disabled", "alias_id": alias_id}


# ── Read-only resolve preview ─────────────────────────────────────────────────
@router.post("/learning/resolve")
def resolve_preview(
    payload: ResolvePreviewRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    lookup = vls.build_alias_lookup(repository, user.company_id, user.id)
    res = vr.resolve(payload.utterance, alias_lookup=lookup)
    return {
        "intent": res.intent, "confidence": res.confidence, "source": res.source,
        "band": res.band, "requires_confirmation": res.requires_confirmation,
        "is_implemented": res.is_implemented, "is_ambiguous": res.is_ambiguous,
        "candidates": res.candidates, "normalized": res.normalized,
    }


# ── Learning-event audit ──────────────────────────────────────────────────────
@router.get("/learning/events")
def list_events(
    limit: int = Query(100, ge=1, le=1000),
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    evs = repository.list_voice_learning_events(user.company_id, limit=limit)
    return {"events": [e.model_dump(mode="json") for e in evs]}
