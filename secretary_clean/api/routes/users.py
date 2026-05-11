from __future__ import annotations

from fastapi import APIRouter, Depends

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserAccount])
def list_users(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    return repository.list_users(user.company_id)


@router.get("/roles")
def list_roles(repository: InMemorySecretaryRepository = Depends(get_repository)):
    return repository.list_roles()
