"""Catalogue domain models for Secretary work activities and pricing."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PricingMethod(BaseModel):
    code: str
    name: str
    description: str = ""
    unit: str | None = None
    display_order: int


class AdditionalCharge(BaseModel):
    code: str
    name: str
    display_order: int


class WorkActivity(BaseModel):
    code: str
    name: str
    industry_code: str
    subtype_code: str
    available_pricing_method_codes: list[str] = Field(default_factory=list)
    default_pricing_method_code: str


class WorkSubtype(BaseModel):
    code: str
    name: str
    industry_code: str
    display_order: int
    activities: list[WorkActivity] = Field(default_factory=list)


class Industry(BaseModel):
    code: str
    name: str
    display_order: int
    subtypes: list[WorkSubtype] = Field(default_factory=list)


class CatalogueSnapshot(BaseModel):
    industries: list[Industry]
    pricing_methods: list[PricingMethod]
    additional_charges: list[AdditionalCharge]

    @property
    def subtype_count(self) -> int:
        return sum(len(industry.subtypes) for industry in self.industries)

    @property
    def activity_count(self) -> int:
        return sum(
            len(subtype.activities)
            for industry in self.industries
            for subtype in industry.subtypes
        )

    def validation_summary(self) -> dict[str, bool | int]:
        method_codes = {method.code for method in self.pricing_methods}
        activities = [
            activity
            for industry in self.industries
            for subtype in industry.subtypes
            for activity in subtype.activities
        ]
        return {
            "industry_count": len(self.industries),
            "subtype_count": self.subtype_count,
            "activity_count": self.activity_count,
            "every_subtype_has_activities": all(
                subtype.activities
                for industry in self.industries
                for subtype in industry.subtypes
            ),
            "every_activity_has_all_pricing_methods": all(
                set(activity.available_pricing_method_codes) == method_codes
                for activity in activities
            ),
            "every_activity_has_exactly_one_default_method": all(
                activity.default_pricing_method_code in method_codes
                and activity.available_pricing_method_codes.count(
                    activity.default_pricing_method_code
                )
                == 1
                for activity in activities
            ),
        }
