"""Pydantic response/request models for /auth (API_SPECIFICATION.md §1 shapes)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OtpRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=5, max_length=255)
    purpose: str = Field(pattern="^(LOGIN|REGISTER)$")  # noqa: S105


class OtpRequestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    otp_sent: bool
    dev_otp: str | None = None


class OtpVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=5, max_length=255)
    otp: str = Field(min_length=6, max_length=6)


class UserOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    mobile: str
    email: str | None
    full_name: str
    totp_enabled: bool


class TokenPairOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserOut | None = None
    access_token: str
    refresh_token: str


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = None


class RefreshOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str


class StepUpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    otp: str = Field(min_length=6, max_length=6)


class StepUpOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stepup_token: str


class TotpSetupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str
    qr_uri: str


class TotpVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=6)


class TotpEnabledOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class MeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserOut
    businesses: list[str] = []  # ids; resolved fully by task 0.5's guard
    firm: str | None = None  # firm id if the user is a firm member


class MeEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool = True
    data: MeOut


class FirmEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool = True
    data: FirmOut

class FirmCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm_name: str = Field(min_length=1, max_length=255)
    pan: str = Field(min_length=10, max_length=10, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class FirmOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    firm_name: str
    pan: str
    ca_code: str


class EnvelopeError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class EnvelopeErrorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = False
    error: EnvelopeError


class EnvelopeDataOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    data: dict[str, object] | list[object] | None = None
