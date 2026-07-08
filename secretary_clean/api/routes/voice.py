"""Voice routes — thin HTTP layer over the v2 engine (see VOICE_ENGINE_V2_DESIGN.md).

/voice/resolve  — classify an utterance into a backend intent (read-only preview)
/voice/execute  — the Voice Engine v2 pipeline: multi-command segmentation,
                  alias/parser/synonym/AI resolution, slot dialogs, enforced
                  confirmation of dangerous intents, execution through the
                  validated repository operations, read-back verification,
                  per-user durable learning.

All business logic lives in secretary_clean/voice2/. Android remains a thin
client: it sends text and echoes back pending_action_id.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core import help_content
from secretary_clean.core.language import resolve_language_context
from secretary_clean.core.models import (
    Permission,
    UserAccount,
    VoiceExecuteRequest,
    VoiceExecuteResult,
    VoiceResolveRequest,
    VoiceResolveResult,
)
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core import voice_intents as vi
from secretary_clean.voice2 import engine as v2

router = APIRouter(prefix="/voice", tags=["voice foundation"])


def _lang_ctx(repository, user: UserAccount, client_id: str | None):
    profile = repository.get_tenant_operating_profile(user.company_id)
    client_language = repository.get_client_preferred_language_code(user.company_id, client_id)
    return resolve_language_context(profile=profile, user=user,
                                    client_language_code=client_language)


@router.post("/resolve", response_model=VoiceResolveResult)
def resolve_voice_command(
    payload: VoiceResolveRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Classify an utterance (read-only). Does not perform any action."""
    parsed = vi.parse_intent(payload.utterance)
    return VoiceResolveResult(
        utterance=payload.utterance,
        resolved_intent=parsed.intent,
        confidence=parsed.confidence,
        requires_confirmation=parsed.requires_confirmation,
        reason=parsed.reason,
        language_context=_lang_ctx(repository, user, payload.client_id),
    )


@router.post("/execute", response_model=VoiceExecuteResult)
def execute_voice_command(
    payload: VoiceExecuteRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Voice Engine v2 entry point."""
    return v2.execute(payload, user, repository)


@router.get("/help")
def get_help(
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
):
    """Structured help filtered by the user's permissions, in their language."""
    return help_content.help_for_user(user)


@router.get("/command-tree")
def get_command_tree(
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
):
    """Hierarchical command catalogue (module -> branch -> command)."""
    from secretary_clean.core import command_tree
    return command_tree.tree_for_user(user)


class LearnAliasRequest(BaseModel):
    phrase: str
    answer: str


@router.post("/learn-alias")
def learn_alias(payload: LearnAliasRequest,
                user: UserAccount = Depends(require_permission(Permission.crm_manage)),
                repository: InMemorySecretaryRepository = Depends(get_repository)):
    """Adaptive alias learning (legacy endpoint kept for the Android client).

    Now actually PERSISTS the alias (previously it returned "saved" and stored
    nothing — the taught phrase was silently dropped). Uses the same learning
    service as POST /voice/aliases so the two paths stay consistent.
    """
    from secretary_clean.core import alias_learning as al
    from secretary_clean.core import voice_learning_service as vls
    from secretary_clean.core import voice_synonyms as syn
    from secretary_clean.core import command_tree

    if al.is_cancel(payload.answer):
        return {"status": "cancelled", "message": "Dobře, nic neukládám."}
    intent = vls.resolve_target_intent(payload.answer) or al.resolve_target_intent(payload.answer)
    if not intent or not al.is_known(intent):
        return {"status": "unknown_target",
                "message": "Tomu příkazu nerozumím. Zkus to říct jinak, nebo řekni omyl."}
    phrase = payload.phrase.strip()
    norm = syn.normalize(phrase)
    existing = vls.find_exact_alias(repository, user.company_id, user.id, norm)
    if existing is not None:
        existing.raw_phrase = phrase
        existing.target_intent = intent
        existing.status = vls.status_for(intent)
        repository.update_voice_alias(existing)
        alias = existing
    else:
        alias = vls.new_alias(user.company_id, user.id, phrase, intent, created_by=user.id)
        repository.create_voice_alias(alias)
    state = alias.status
    vls.record_event(repository, user.company_id, user.id, phrase,
                     "USER_ALIAS" if state == "ACTIVE" else "PENDING_ALIAS",
                     resolved_intent=intent, created_alias_id=alias.id)
    loc = command_tree.locate_intent(intent)
    loc_txt = f" ({loc['module_title']} > {loc['branch_title']})" if loc else ""
    if state == "ACTIVE":
        msg = f"Frázi „{phrase}“ jsem přiřadila k příkazu {intent}{loc_txt}. Můžeš ji hned použít."
    else:
        msg = (f"Frázi „{phrase}“ jsem přiřadila k příkazu {intent}{loc_txt}. "
               f"Jakmile bude tato funkce dostupná, příkaz začne fungovat.")
    return {"status": "saved", "alias_status": state, "target_intent": intent,
            "phrase": phrase, "location": loc, "alias_id": alias.id, "message": msg}
