"""Pydantic schemas for /businesses and /registrations (task 0.6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BusinessCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=255)
    pan: str = Field(min_length=10, max_length=10)
    trade_name: str | None = Field(default=None, max_length=255)


class BusinessPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = Field(default=None, min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)


class RegistrationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gstin: str = Field(min_length=15, max_length=15)
    registered_address: str | None = Field(default=None, max_length=1000)
    aato_minor: int | None = Field(default=None, ge=0, description="AATO in paise")
    filing_scheme: str | None = Field(default=None, pattern="^(REGULAR_MONTHLY|QRMP|COMPOSITION)$")


class RegistrationPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registered_address: str | None = Field(default=None, max_length=1000)
    aato_minor: int | None = Field(default=None, ge=0, description="AATO in paise")
    filing_scheme: str | None = Field(default=None, pattern="^(REGULAR_MONTHLY|QRMP|COMPOSITION)$")


class BusinessOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    legal_name: str
    trade_name: str | None
    pan: str
    created_at: str | None


class RegistrationOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    business_id: str
    gstin: str
    state_code: str
    filing_scheme: str
    irn_applicable: bool
    aato_latest_minor: int
    registered_address: str | None
    created_at: str | None


class BusinessDetailOut(BusinessOut):
    registrations: list[RegistrationOut] = []
