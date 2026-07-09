"""Conversational assistant routes.

POST /process           — one chat turn (voice "chat mode" on the phone)
POST /session/summarize — summarize a finished chat session, store to memory

Uses OpenAI when OPENAI_API_KEY is set; otherwise degrades gracefully:
`/process` falls back to the deterministic voice engine so the assistant can
still perform commands, and `/session/summarize` reports it could not summarize
instead of erroring. Responses match the shapes the Android client expects.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.models import Permission, UserAccount, VoiceExecuteRequest
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(tags=["assistant chat"])


# ── request/response models (mirror the Android data classes) ─────────────────
class ChatMessage(BaseModel):
    role: str
    content: str


class MessageRequest(BaseModel):
    text: str
    history: list[ChatMessage] = Field(default_factory=list)
    context_entity_id: str | None = None
    context_type: str | None = None
    internal_language: str = "cs-CZ"
    external_language: str = "en-GB"
    calendar_context: str | None = None
    current_datetime: str | None = None


class AssistantResponse(BaseModel):
    reply_cs: str
    action_type: str | None = None
    action_data: dict | None = None
    needs_confirmation: bool = False
    is_question: bool = False


class SummarizeRequest(BaseModel):
    history: list[ChatMessage] = Field(default_factory=list)
    user_id: str | None = None
    tenant_id: int = 1
    internal_language: str = "cs"


class SummarizeResponse(BaseModel):
    stored: bool = False
    summary: str | None = None
    reason: str | None = None
    error: str | None = None


def _openai_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _chat_model() -> str:
    return os.environ.get("OPENAI_CHAT_MODEL", os.environ.get("OPENAI_INTENT_MODEL", "gpt-4o-mini"))


@router.post("/process", response_model=AssistantResponse)
def process_message(
    payload: MessageRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """One conversational turn. With OpenAI: a helpful CRM assistant reply in
    Czech. Without OpenAI: fall back to the voice command engine so the user can
    still get things done."""
    text = (payload.text or "").strip()
    if not text:
        return AssistantResponse(reply_cs="Neslyšela jsem nic. Zkus to prosím znovu.",
                                 is_question=True)

    if _openai_configured():
        try:
            return _openai_reply(payload, user, repository)
        except Exception:  # noqa: BLE001 — never 500 the chat; fall through
            pass

    # Fallback: route the utterance through the voice engine (real actions).
    try:
        from secretary_clean.voice2 import engine as v2
        result = v2.execute(
            VoiceExecuteRequest(utterance=text, client_id=payload.context_entity_id),
            user, repository)
        reply = result.message or "Dobře."
        return AssistantResponse(
            reply_cs=reply,
            action_type=result.action,
            needs_confirmation=bool(getattr(result, "requires_confirmation", False)),
            is_question=(result.status == "needs_more_info"))
    except Exception:  # noqa: BLE001
        return AssistantResponse(
            reply_cs="Teď ti bohužel neumím odpovědět. Zkus příkaz, třeba "
                     "„ukaž úkoly“ nebo „vytvoř klienta“.",
            is_question=False)


def _openai_reply(payload: MessageRequest, user, repository) -> AssistantResponse:
    from openai import OpenAI
    client = OpenAI()
    company = repository.get_company(user.company_id)
    company_name = getattr(company, "legal_name", None) or getattr(company, "name", "") or ""
    sys = (
        "Jsi Tajemník, hlasový asistent pro řemeslníky a malé firmy v CRM Secretary. "
        f"Firma: {company_name}. Odpovídej stručně, česky, prakticky. Pokud uživatel "
        "chce provést akci (vytvořit klienta/úkol/zakázku, zapsat výkaz, poslat zprávu), "
        "navrhni ji jasně a zeptej se na potvrzení. Vrať JSON: "
        '{"reply_cs": "...", "is_question": true|false}.'
    )
    msgs = [{"role": "system", "content": sys}]
    if payload.calendar_context:
        msgs.append({"role": "system", "content": f"Kalendář: {payload.calendar_context}"})
    for m in payload.history[-20:]:
        role = "assistant" if m.role in ("assistant", "ai") else "user"
        msgs.append({"role": role, "content": m.content})
    msgs.append({"role": "user", "content": payload.text})
    resp = client.chat.completions.create(
        model=_chat_model(), temperature=0.3,
        response_format={"type": "json_object"}, messages=msgs)
    data = json.loads(resp.choices[0].message.content or "{}")
    reply = (data.get("reply_cs") or data.get("reply") or "").strip() or "Dobře."
    return AssistantResponse(reply_cs=reply, is_question=bool(data.get("is_question")))


@router.post("/session/summarize", response_model=SummarizeResponse)
def summarize_session(
    payload: SummarizeRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Summarize a finished chat session and store it to assistant memory."""
    convo = [m for m in payload.history if (m.content or "").strip()]
    if len(convo) < 2:
        return SummarizeResponse(stored=False, reason="too_short")
    if not _openai_configured():
        return SummarizeResponse(stored=False, reason="ai_unavailable")
    try:
        from openai import OpenAI
        client = OpenAI()
        transcript = "\n".join(f"{m.role}: {m.content}" for m in convo[-40:])
        resp = client.chat.completions.create(
            model=_chat_model(), temperature=0.2,
            messages=[
                {"role": "system", "content": "Shrň tuto konverzaci do 1–3 vět česky. "
                 "Zachyť, co uživatel chtěl a co se dohodlo."},
                {"role": "user", "content": transcript}])
        summary = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        return SummarizeResponse(stored=False, error=str(exc))
    if not summary:
        return SummarizeResponse(stored=False, reason="empty_summary")
    try:
        item = repository.add_assistant_memory(user.company_id, user.id, summary, "session")
        repository.log_activity(user.company_id, user.id, "assistant_memory",
                                item["id"], "create", "Session summary stored")
    except Exception as exc:  # noqa: BLE001
        return SummarizeResponse(stored=False, summary=summary, error=str(exc))
    return SummarizeResponse(stored=True, summary=summary)
