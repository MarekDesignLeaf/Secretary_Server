from __future__ import annotations

from fastapi import APIRouter, Depends

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.language import resolve_language_context
from secretary_clean.core.models import Permission, UserAccount, VoiceExecuteRequest, VoiceExecuteResult, VoiceResolveRequest, VoiceResolveResult
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/voice", tags=["voice foundation"])

_INTENTS = {
    "create client": "crm.clients.create",
    "new client": "crm.clients.create",
    "create job": "crm.jobs.create",
    "new job": "crm.jobs.create",
    "create task": "crm.tasks.create",
    "new task": "crm.tasks.create",
    "create quote": "crm.quotes.create",
    "create invoice": "crm.invoices.create",
    "work report": "crm.work_reports.create",
}


def _resolve(utterance: str, *, user: UserAccount, repository: InMemorySecretaryRepository, client_id: str | None) -> VoiceResolveResult:
    normalized = " ".join(utterance.lower().split())
    profile = repository.get_tenant_operating_profile(user.company_id)
    client_language = repository.get_client_preferred_language_code(user.company_id, client_id)
    language_context = resolve_language_context(
        profile=profile,
        user=user,
        client_language_code=client_language,
    )
    for phrase, intent in _INTENTS.items():
        if phrase in normalized:
            return VoiceResolveResult(
                utterance=utterance,
                resolved_intent=intent,
                confidence=0.78,
                requires_confirmation=True,
                reason="Matched backend-owned clean intent phrase with tenant language context.",
                language_context=language_context,
            )
    return VoiceResolveResult(
        utterance=utterance,
        resolved_intent=None,
        confidence=0,
        requires_confirmation=True,
        reason="No executable backend intent matched; no fake action will be executed.",
        language_context=language_context,
    )


@router.post("/resolve", response_model=VoiceResolveResult)
def resolve_voice_command(
    payload: VoiceResolveRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return _resolve(payload.utterance, user=user, repository=repository, client_id=payload.client_id)


@router.post("/execute", response_model=VoiceExecuteResult)
def execute_voice_command(
    payload: VoiceExecuteRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    resolution = _resolve(payload.utterance, user=user, repository=repository, client_id=payload.client_id)
    if not resolution.resolved_intent:
        return VoiceExecuteResult(
            executed=False,
            resolved_intent=None,
            requires_confirmation=True,
            message="Command was not executed because no real backend intent matched.",
            language_context=resolution.language_context,
        )
    if not payload.confirmed:
        return VoiceExecuteResult(
            executed=False,
            resolved_intent=resolution.resolved_intent,
            requires_confirmation=True,
            message="Confirmation required before executing a mutating voice command.",
            language_context=resolution.language_context,
        )
    return VoiceExecuteResult(
        executed=True,
        resolved_intent=resolution.resolved_intent,
        requires_confirmation=False,
        message="Clean backend intent accepted for execution by the application service layer.",
        language_context=resolution.language_context,
    )
