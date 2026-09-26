from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Role = Literal["OWNER", "ADMIN", "MANAGER", "TECHNICIAN", "VIEWER"]
NonOwnerRole = Literal["ADMIN", "MANAGER", "TECHNICIAN", "VIEWER"]


class AuthSessionResponse(BaseModel):
    state: Literal["anonymous", "authenticated", "pending_identity"]
    invitation_available: bool | None = None


class CsrfResponse(BaseModel):
    csrf_token: str


class MeResponse(BaseModel):
    id: UUID
    display_name: str | None
    email: str | None
    email_verified: bool


class UserProfileResponse(MeResponse):
    pass


class InvitationTokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class AttemptCreated(BaseModel):
    id: UUID
    expires_at: datetime


class InvitationAttemptView(BaseModel):
    id: UUID
    organization: dict[str, str | UUID]
    email: str
    role: NonOwnerRole
    expires_at: datetime


class AcceptInvitationResponse(BaseModel):
    organization_id: UUID
    membership_id: UUID
    role: NonOwnerRole


class OrganizationSummary(BaseModel):
    id: UUID
    name: str
    role: Role


class OrganizationList(BaseModel):
    items: list[OrganizationSummary]
    next_cursor: None = None


class OrganizationView(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class OrganizationUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be empty")
        return value


class MembershipView(BaseModel):
    membership_id: UUID
    display_name: str | None
    email: str | None = None
    role: Role
    status: Literal["ACTIVE", "SUSPENDED", "REMOVED"]
    created_at: datetime
    updated_at: datetime
    last_activated_at: datetime | None
    last_suspended_at: datetime | None
    removed_at: datetime | None


class MemberDirectoryItem(BaseModel):
    membership_id: UUID
    display_name: str | None
    email: str | None = None
    role: Role
    status: Literal["ACTIVE", "SUSPENDED", "REMOVED"]


class MemberDirectory(BaseModel):
    items: list[MemberDirectoryItem]
    next_cursor: None = None


class ChangeRoleRequest(BaseModel):
    role: Role


class InvitationCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: NonOwnerRole


class InvitationCreateResponse(BaseModel):
    id: UUID
    email: str
    role: NonOwnerRole
    expires_at: datetime
    invitation_url: str


class InvitationAdminItem(BaseModel):
    id: UUID
    email: str
    role: NonOwnerRole
    status: Literal["PENDING", "ACCEPTED", "REVOKED", "EXPIRED"]
    expires_at: datetime


class InvitationList(BaseModel):
    items: list[InvitationAdminItem]
    next_cursor: None = None


class InvitationRevokeResponse(BaseModel):
    id: UUID
    status: Literal["REVOKED"]


class PendingView(BaseModel):
    invitation: dict[str, str | UUID] | None
