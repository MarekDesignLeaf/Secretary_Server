from __future__ import annotations

from fastapi import APIRouter, Request

from secretary_clean.catalogue.models import AdditionalCharge, Industry, PricingMethod

router = APIRouter(prefix="/catalogue", tags=["catalogue"])


@router.get("/industries", response_model=list[Industry])
def industries(request: Request):
    return request.app.state.catalogue.industries


@router.get("/pricing-methods", response_model=list[PricingMethod])
def pricing_methods(request: Request):
    return request.app.state.catalogue.pricing_methods


@router.get("/additional-charges", response_model=list[AdditionalCharge])
def additional_charges(request: Request):
    return request.app.state.catalogue.additional_charges


@router.get("/validation-summary")
def validation_summary(request: Request):
    return request.app.state.catalogue.validation_summary()
