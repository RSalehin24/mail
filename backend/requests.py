"""Closed request schemas; domain validators independently enforce byte and host policies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LoginRequest(RequestModel):
    email: str = Field(max_length=256)
    password: str = Field(max_length=1024)


class FactorRequest(RequestModel):
    challenge: str = Field(max_length=128)
    factor: Literal["totp", "email", "recovery"]
    code: str = Field(max_length=128)


class OtpRequest(RequestModel):
    challenge: str = Field(max_length=128)
    purpose: Literal["login", "reauth"]


class SetupRequest(RequestModel):
    token: str = Field(max_length=128)
    password: str | None = Field(default=None, max_length=1024)
    code: str | None = Field(default=None, max_length=6)


class PasswordRequest(RequestModel):
    password: str = Field(max_length=1024)


class TotpRequest(RequestModel):
    code: str | None = Field(default=None, max_length=6)


class CreateMailbox(RequestModel):
    request_id: str = Field(max_length=36)
    email: str = Field(max_length=256)
    password: str = Field(max_length=72)
    display_name: str = Field(default="", max_length=256)
    notes: str = Field(default="", max_length=2048)


class ResetMailbox(RequestModel):
    request_id: str = Field(max_length=36)
    password: str = Field(max_length=72)


class MailboxMetadata(RequestModel):
    display_name: str = Field(max_length=256)
    notes: str = Field(max_length=2048)


class DeleteMailbox(RequestModel):
    request_id: str = Field(max_length=36)
    confirmation: str = Field(max_length=256)
    cutoff: str = Field(max_length=128)
    acknowledged: bool
