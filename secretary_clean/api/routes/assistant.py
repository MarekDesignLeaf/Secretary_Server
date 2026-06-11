"""Assistant memory routes — permanent "zapamatuj si" entries per company.

GET    /api/v1/assistant/memory          -> list entries (newest first)
POST   /api/v1/assistant/memory          -> create entry {content, memory_type?}
DELETE /api/v1/assistant/memory/{id}     -> delete entry

Backed by clean_assistant_memory. Voice tools remember/forget/recall (queued)
will reuse the same repository methods so UI and voice stay consistent.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import (
    AssistantMemoryCreate,
    AssistantMemoryItem,
    UserAccount,
)
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.get("/memory", response_model=list[AssistantMemoryItem])
def list_memory(
    limit: int = 100,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    rows = repository.list_assistant_memory(user.company_id, limit=max(1, min(limit, 500)))
    return [AssistantMemoryItem(**{k: r[k] for k in ("id", "memory_type", "content", "updated_at")}) for r in rows]


@router.post("/memory", response_model=AssistantMemoryItem)
def remember(
    payload: AssistantMemoryCreate,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content must not be empty")
    item = repository.add_assistant_memory(
        user.company_id, user.id, content, payload.memory_type or "long"
    )
    repository.log_activity(
        user.company_id, user.id, "assistant_memory", item["id"],
        "create", f"Assistant memory saved: {content[:80]}",
    )
    return AssistantMemoryItem(**{k: item[k] for k in ("id", "memory_type", "content", "updated_at")})


@router.delete("/memory/{memory_id}")
def forget(
    memory_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    deleted = repository.delete_assistant_memory(memory_id, user.company_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    repository.log_activity(
        user.company_id, user.id, "assistant_memory", memory_id,
        "delete", "Assistant memory deleted",
    )
    return {"ok": True}
