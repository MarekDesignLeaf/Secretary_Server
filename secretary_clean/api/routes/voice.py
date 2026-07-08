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
                user: UserAccount = Depends(require_permission(Permission.crm_manage))):
    """Adaptive alias learning (legacy endpoint kept for the Android client)."""
    from secretary_clean.core import alias_learning as al
    if al.is_cancel(payload.answer):
        return {"status": "cancelled", "message": "Dobře, nic neukládám."}
    intent = al.resolve_target_intent(payload.answer)
    if not intent or not al.is_known(intent):
        return {"status": "unknown_target",
                "message": "Tomu příkazu nerozumím. Zkus to říct jinak, nebo řekni omyl."}
    state = al.status_for(intent)
    phrase = payload.phrase.strip()
    from secretary_clean.core import command_tree
    loc = command_tree.locate_intent(intent)
    loc_txt = f" ({loc['module_title']} > {loc['branch_title']})" if loc else ""
    if state == "ACTIVE":
        msg = f"Frázi „{phrase}“ jsem přiřadila k příkazu {intent}{loc_txt}. Můžeš ji hned použít."
    else:
        msg = (f"Frázi „{phrase}“ jsem přiřadila k příkazu {intent}{loc_txt}. "
               f"Jakmile bude tato funkce dostupná, příkaz začne fungovat.")
    return {"status": "saved", "alias_status": state, "target_intent": intent,
            "phrase": phrase, "location": loc, "message": msg}
