"""Closed webmail request types. Ownership is always derived from the session."""

from typing import Literal

from pydantic import Field

from backend.requests import RequestModel


class MessageRef(RequestModel):
    folder: str = Field(min_length=1, max_length=256)
    uidvalidity: int = Field(gt=0, le=4294967295)
    uid: int = Field(gt=0, le=4294967295)


class Search(RequestModel):
    folder: str | None = Field(default=None, max_length=256)
    sender: str = Field(default="", max_length=256)
    recipient: str = Field(default="", max_length=256)
    subject: str = Field(default="", max_length=256)
    body: str = Field(default="", max_length=256)
    after: str = Field(default="", max_length=10)
    before: str = Field(default="", max_length=10)
    unread: bool = False
    label: str = Field(default="", max_length=32)
    include_trash: bool = False
    include_junk: bool = False
    page: int = Field(default=1, ge=1, le=100000)
    size: Literal[25, 50] = 25


class Bulk(RequestModel):
    messages: list[MessageRef] = Field(min_length=1, max_length=100)
    action: Literal["read", "unread", "move", "trash", "restore", "label", "unlabel", "delete"]
    destination: str = Field(default="", max_length=256)
    label: str = Field(default="", max_length=32)
    confirmation: str = Field(default="", max_length=32)


class FolderChange(RequestModel):
    name: str = Field(min_length=1, max_length=256)
    new_name: str = Field(default="", max_length=256)
    action: Literal["create", "rename", "delete"] = "create"


class LabelChange(RequestModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class ProfileChange(RequestModel):
    display_name: str = Field(max_length=256)
    reply_to: str = Field(max_length=256)
    signature: str = Field(max_length=8192)
    theme: Literal["light", "dark", "system"]


class SecurityProof(RequestModel):
    password: str = Field(max_length=1024)
    factor: Literal["totp", "recovery"] = "totp"
    code: str = Field(default="", max_length=128)


class PasswordChange(SecurityProof):
    new_password: str = Field(max_length=72)


class FactorChange(SecurityProof):
    action: Literal["begin", "confirm", "disable", "recovery"]
    new_code: str = Field(default="", max_length=6)


class Compose(RequestModel):
    to: str = Field(default="", max_length=4096)
    cc: str = Field(default="", max_length=4096)
    bcc: str = Field(default="", max_length=4096)
    subject: str = Field(default="", max_length=998)
    text: str = Field(default="", max_length=131072)
    uploads: list[str] = Field(default_factory=list, max_length=30)
    draft: MessageRef | None = None
    attachment_source: MessageRef | None = None
    attachments: list[str] = Field(default_factory=list, max_length=30)
    in_reply_to: str = Field(default="", max_length=998)
    references: str = Field(default="", max_length=4096)


class SendRequest(Compose):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
