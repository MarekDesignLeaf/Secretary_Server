from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import CRMRecord, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

CRM_MODULES = ("clients", "jobs", "tasks", "quotes", "invoices", "communications", "work_reports")


class CRMCreateRequest(BaseModel):
    name: str
    data: dict = Field(default_factory=dict)


router = APIRouter(prefix="/crm", tags=["crm core"])


for module_name in CRM_MODULES:
    def make_list(module: str):
        def list_records(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
            try:
                return repository.list_crm_records(module, user.company_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Unknown CRM module") from exc
        return list_records

    def make_create(module: str):
        def create_record(payload: CRMCreateRequest, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
            try:
                return repository.create_crm_record(module, user.company_id, payload.name, payload.data)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Unknown CRM module") from exc
        return create_record

    router.add_api_route(f"/{module_name}", make_list(module_name), methods=["GET"], response_model=list[CRMRecord], name=f"list_{module_name}")
    router.add_api_route(f"/{module_name}", make_create(module_name), methods=["POST"], response_model=CRMRecord, name=f"create_{module_name}")
