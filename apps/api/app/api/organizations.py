from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.modules.identity.dependencies import require_user
from app.modules.identity.models import Membership, Organization, User
from app.modules.identity.organizations import (
    change_membership_role,
    create_invitation,
    list_invitations,
    list_members,
    list_organizations,
    own_membership,
    read_organization,
    revoke_invitation,
    transition_membership,
    update_organization,
)
from app.modules.identity.schemas import (
    ChangeRoleRequest,
    InvitationAdminItem,
    InvitationCreateRequest,
    InvitationCreateResponse,
    InvitationList,
    InvitationRevokeResponse,
    MemberDirectory,
    MemberDirectoryItem,
    MembershipView,
    OrganizationList,
    OrganizationSummary,
    OrganizationUpdate,
    OrganizationView,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _membership_view(membership: Membership, user: User, *, include_email: bool) -> MembershipView:
    return MembershipView(
        membership_id=membership.id,
        display_name=user.display_name,
        email=user.email if include_email and user.email_verified_at is not None else None,
        role=membership.role,
        status=membership.status,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
        last_activated_at=membership.last_activated_at,
        last_suspended_at=membership.last_suspended_at,
        removed_at=membership.removed_at,
    )


def _organization_view(organization: Organization) -> OrganizationView:
    return OrganizationView(
        id=organization.id,
        name=organization.name,
        created_at=organization.created_at,
        updated_at=organization.updated_at,
    )


@router.get("", response_model=OrganizationList)
def organizations(
    user: User = Depends(require_user), db: Session = Depends(get_db_session)
) -> OrganizationList:
    rows = list_organizations(db, user.id)
    return OrganizationList(
        items=[OrganizationSummary(id=org.id, name=org.name, role=role) for org, role in rows]
    )


@router.get("/{organization_id}", response_model=OrganizationView)
def organization(
    organization_id: UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> OrganizationView:
    return _organization_view(read_organization(db, user.id, organization_id))


@router.patch("/{organization_id}", response_model=OrganizationView)
def patch_organization(
    organization_id: UUID,
    payload: OrganizationUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> OrganizationView:
    return _organization_view(update_organization(db, user.id, organization_id, payload.name))


@router.get("/{organization_id}/memberships/me", response_model=MembershipView)
def my_membership(
    organization_id: UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> MembershipView:
    membership, member = own_membership(db, user.id, organization_id)
    return _membership_view(membership, member, include_email=True)


@router.get("/{organization_id}/memberships", response_model=MemberDirectory)
def memberships(
    organization_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> MemberDirectory:
    actor_role, rows = list_members(db, user.id, organization_id, limit)
    include_email = actor_role in {"OWNER", "ADMIN"}
    return MemberDirectory(
        items=[
            MemberDirectoryItem(
                membership_id=membership.id,
                display_name=member.display_name,
                email=member.email
                if include_email and member.email_verified_at is not None
                else None,
                role=membership.role,
                status=membership.status,
            )
            for membership, member in rows
        ]
    )


@router.patch("/{organization_id}/memberships/{membership_id}/role", response_model=MembershipView)
def change_role(
    organization_id: UUID,
    membership_id: UUID,
    payload: ChangeRoleRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> MembershipView:
    membership = change_membership_role(db, user.id, organization_id, membership_id, payload.role)
    member = db.get(User, membership.user_id)
    assert member is not None
    return _membership_view(membership, member, include_email=True)


@router.post(
    "/{organization_id}/memberships/{membership_id}/suspend", response_model=MembershipView
)
def suspend_member(
    organization_id: UUID,
    membership_id: UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> MembershipView:
    membership = transition_membership(db, user.id, organization_id, membership_id, "suspend")
    member = db.get(User, membership.user_id)
    assert member is not None
    return _membership_view(membership, member, include_email=True)


@router.post(
    "/{organization_id}/memberships/{membership_id}/reactivate", response_model=MembershipView
)
def reactivate_member(
    organization_id: UUID,
    membership_id: UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> MembershipView:
    membership = transition_membership(db, user.id, organization_id, membership_id, "reactivate")
    member = db.get(User, membership.user_id)
    assert member is not None
    return _membership_view(membership, member, include_email=True)


@router.delete("/{organization_id}/memberships/{membership_id}", status_code=204)
def remove_member(
    organization_id: UUID,
    membership_id: UUID,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> Response:
    transition_membership(db, user.id, organization_id, membership_id, "remove")
    response.status_code = 204
    return response


@router.get("/{organization_id}/invitations", response_model=InvitationList)
def invitations(
    organization_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> InvitationList:
    rows = list_invitations(db, user.id, organization_id, limit)
    return InvitationList(
        items=[
            InvitationAdminItem(
                id=row.id,
                email=row.email,
                role=row.role,
                status=row.status,
                expires_at=row.expires_at,
            )
            for row in rows
        ]
    )


@router.post(
    "/{organization_id}/invitations", response_model=InvitationCreateResponse, status_code=201
)
def invite(
    organization_id: UUID,
    payload: InvitationCreateRequest,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> Response:
    invitation, raw_token = create_invitation(
        db, user.id, organization_id, payload.email, payload.role
    )
    origin = request.app.state.settings.public_app_origin.rstrip("/")
    response = InvitationCreateResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invitation_url=f"{origin}/invitations/accept#t={raw_token}",
    )
    from fastapi.responses import JSONResponse

    result = JSONResponse(status_code=201, content=response.model_dump(mode="json"))
    result.headers["Cache-Control"] = "no-store"
    result.headers["Pragma"] = "no-cache"
    result.headers["Referrer-Policy"] = "no-referrer"
    return result


@router.post(
    "/{organization_id}/invitations/{invitation_id}/revoke", response_model=InvitationRevokeResponse
)
def revoke(
    organization_id: UUID,
    invitation_id: UUID,
    user: User = Depends(require_user),
    db: Session = Depends(get_db_session),
) -> InvitationRevokeResponse:
    invitation = revoke_invitation(db, user.id, organization_id, invitation_id)
    return InvitationRevokeResponse(id=invitation.id, status="REVOKED")
