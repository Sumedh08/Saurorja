from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import database_utc_now
from app.modules.identity.models import (
    Invitation,
    Membership,
    Organization,
    User,
)
from app.modules.identity.security import digest_secret, new_secret, normalize_email


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class OrganizationContext(NamedTuple):
    actor: User
    organization: Organization
    membership: Membership

    @property
    def role(self) -> str:
        return self.membership.role


def _actor_and_org(
    db: Session,
    user_id: UUID,
    organization_id: UUID,
    *,
    lock: bool,
    target_membership_id: UUID | None = None,
) -> OrganizationContext:
    target_user_id: UUID | None = None
    if target_membership_id is not None:
        target_user_id = db.scalar(
            select(Membership.user_id).where(
                Membership.id == target_membership_id,
                Membership.organization_id == organization_id,
            )
        )
        if target_user_id is None:
            raise _error(404, "membership_not_found", "The Membership was not found")
    user_ids = {user_id}
    if target_user_id is not None:
        user_ids.add(target_user_id)
    locked_users: dict[UUID, User] = {}
    if lock:
        locked_users = {
            user.id: user
            for user in db.scalars(
                select(User)
                .where(User.id.in_(sorted(user_ids)))
                .order_by(User.id)
                .with_for_update(read=True)
            ).all()
        }
    user = locked_users.get(user_id) if lock else db.get(User, user_id)
    if user is None or user.status != "ACTIVE":
        raise _error(401, "unauthenticated", "Authentication is required")
    organization_query = select(Organization).where(Organization.id == organization_id)
    if lock:
        organization_query = organization_query.with_for_update()
    organization = db.scalar(organization_query)
    if organization is None:
        raise _error(404, "organization_not_found", "The Organization was not found")
    membership_query = select(Membership).where(
        Membership.user_id == user.id,
        Membership.organization_id == organization.id,
        Membership.status == "ACTIVE",
    )
    if lock:
        membership_query = membership_query.with_for_update()
    membership = db.scalar(membership_query)
    if membership is None:
        raise _error(404, "organization_not_found", "The Organization was not found")
    return OrganizationContext(user, organization, membership)


def _require_role(membership: Membership, roles: set[str]) -> None:
    if membership.role not in roles:
        raise _error(403, "forbidden", "This operation is not permitted")


def _owner_count(db: Session, organization_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id == organization_id,
                Membership.role == "OWNER",
                Membership.status == "ACTIVE",
                User.status == "ACTIVE",
            )
        )
        or 0
    )


def list_organizations(db: Session, user_id: UUID) -> list[tuple[Organization, str]]:
    return list(
        db.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.organization_id == Organization.id)
            .where(Membership.user_id == user_id, Membership.status == "ACTIVE")
            .order_by(Organization.name, Organization.id)
        ).all()
    )


def read_organization(db: Session, user_id: UUID, organization_id: UUID) -> Organization:
    _, organization, _ = _actor_and_org(db, user_id, organization_id, lock=False)
    return organization


def update_organization(
    db: Session, user_id: UUID, organization_id: UUID, name: str
) -> Organization:
    try:
        _, organization, actor = _actor_and_org(db, user_id, organization_id, lock=True)
        _require_role(actor, {"OWNER", "ADMIN"})
        organization.name = name
        db.commit()
        return organization
    except Exception:
        db.rollback()
        raise


def own_membership(db: Session, user_id: UUID, organization_id: UUID) -> tuple[Membership, User]:
    user, _, membership = _actor_and_org(db, user_id, organization_id, lock=False)
    return membership, user


def list_members(
    db: Session, user_id: UUID, organization_id: UUID, limit: int
) -> tuple[str, list[tuple[Membership, User]]]:
    _, organization, actor = _actor_and_org(db, user_id, organization_id, lock=False)
    del organization
    _require_role(actor, {"OWNER", "ADMIN", "MANAGER"})
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == organization_id, Membership.status != "REMOVED")
        .order_by(Membership.created_at, Membership.id)
        .limit(min(max(limit, 1), 100))
    ).all()
    return actor.role, list(rows)


def change_membership_role(
    db: Session, actor_user_id: UUID, organization_id: UUID, target_id: UUID, new_role: str
) -> Membership:
    if new_role not in {"OWNER", "ADMIN", "MANAGER", "TECHNICIAN", "VIEWER"}:
        raise _error(422, "validation_error", "The role is invalid")
    try:
        _, organization, actor = _actor_and_org(
            db,
            actor_user_id,
            organization_id,
            lock=True,
            target_membership_id=target_id,
        )
        _require_role(actor, {"OWNER", "ADMIN"})
        target = db.scalar(
            select(Membership)
            .where(Membership.id == target_id, Membership.organization_id == organization.id)
            .with_for_update()
        )
        if target is None or target.status == "REMOVED":
            raise _error(404, "membership_not_found", "The Membership was not found")
        if actor.role == "ADMIN" and (target.role == "OWNER" or new_role == "OWNER"):
            raise _error(403, "forbidden", "This operation is not permitted")
        if new_role == "OWNER":
            target_user = db.get(User, target.user_id)
            if target_user is None or target_user.status != "ACTIVE":
                raise _error(409, "membership_conflict", "The Membership cannot be promoted")
        if target.role == "OWNER" and new_role != "OWNER" and target.status == "ACTIVE":
            if _owner_count(db, organization_id) <= 1:
                raise _error(
                    409, "last_owner_required", "The Organization must retain an active Owner"
                )
        target.role = new_role
        target.updated_at = datetime.now(UTC)
        db.commit()
        return target
    except Exception:
        db.rollback()
        raise


def transition_membership(
    db: Session,
    actor_user_id: UUID,
    organization_id: UUID,
    target_id: UUID,
    action: str,
) -> Membership:
    try:
        _, organization, actor = _actor_and_org(
            db,
            actor_user_id,
            organization_id,
            lock=True,
            target_membership_id=target_id,
        )
        _require_role(actor, {"OWNER", "ADMIN"})
        target = db.scalar(
            select(Membership)
            .where(Membership.id == target_id, Membership.organization_id == organization.id)
            .with_for_update()
        )
        if target is None:
            raise _error(404, "membership_not_found", "The Membership was not found")
        if target.status == "REMOVED":
            raise _error(409, "membership_conflict", "The Membership cannot be changed")
        if actor.role == "ADMIN" and target.role == "OWNER":
            raise _error(403, "forbidden", "This operation is not permitted")
        now = datetime.now(UTC)
        if action == "suspend":
            if target.status != "ACTIVE":
                raise _error(409, "membership_conflict", "The Membership cannot be changed")
            if target.role == "OWNER" and _owner_count(db, organization_id) <= 1:
                raise _error(
                    409, "last_owner_required", "The Organization must retain an active Owner"
                )
            target.status = "SUSPENDED"
            target.last_suspended_at = now
        elif action == "reactivate":
            if target.status != "SUSPENDED":
                raise _error(409, "membership_conflict", "The Membership cannot be changed")
            target_user = db.get(User, target.user_id)
            if target_user is None or target_user.status != "ACTIVE":
                raise _error(409, "membership_conflict", "The Membership cannot be changed")
            target.status = "ACTIVE"
            target.last_activated_at = now
        elif action == "remove":
            if (
                target.status == "ACTIVE"
                and target.role == "OWNER"
                and _owner_count(db, organization_id) <= 1
            ):
                raise _error(
                    409, "last_owner_required", "The Organization must retain an active Owner"
                )
            target.status = "REMOVED"
            target.removed_at = now
        target.updated_at = now
        db.commit()
        return target
    except Exception:
        db.rollback()
        raise


def create_invitation(
    db: Session,
    actor_user_id: UUID,
    organization_id: UUID,
    email: str,
    role: str,
) -> tuple[Invitation, str]:
    if role not in {"ADMIN", "MANAGER", "TECHNICIAN", "VIEWER"}:
        raise _error(422, "validation_error", "The invitation role is invalid")
    try:
        normalized_email, _ = normalize_email(email)
    except ValueError as exc:
        raise _error(422, "validation_error", "The email address is invalid") from exc
    try:
        _, organization, actor = _actor_and_org(db, actor_user_id, organization_id, lock=True)
        _require_role(actor, {"OWNER", "ADMIN"})
        now = database_utc_now(db)
        db.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == organization_id,
                Invitation.normalized_email == normalized_email,
                Invitation.status == "PENDING",
                Invitation.expires_at <= now,
            )
            .values(status="EXPIRED")
        )
        invitation_secret = new_secret()
        invitation = Invitation(
            organization_id=organization.id,
            creator_membership_id=actor.id,
            email=email,
            normalized_email=normalized_email,
            role=role,
            token_hash=digest_secret(invitation_secret),
            status="PENDING",
            expires_at=now + timedelta(days=7),
        )
        db.add(invitation)
        db.commit()
        return invitation, invitation_secret
    except IntegrityError as exc:
        db.rollback()
        raise _error(
            409, "invitation_conflict", "A pending invitation already exists for this address"
        ) from exc
    except Exception:
        db.rollback()
        raise


def list_invitations(
    db: Session, user_id: UUID, organization_id: UUID, limit: int
) -> list[Invitation]:
    _, _, actor = _actor_and_org(db, user_id, organization_id, lock=False)
    _require_role(actor, {"OWNER", "ADMIN"})
    db.execute(
        update(Invitation)
        .where(
            Invitation.organization_id == organization_id,
            Invitation.status == "PENDING",
            Invitation.expires_at <= func.clock_timestamp(),
        )
        .values(status="EXPIRED")
    )
    db.commit()
    return list(
        db.scalars(
            select(Invitation)
            .where(Invitation.organization_id == organization_id)
            .order_by(Invitation.created_at.desc(), Invitation.id)
            .limit(min(max(limit, 1), 100))
        ).all()
    )


def revoke_invitation(
    db: Session, user_id: UUID, organization_id: UUID, invitation_id: UUID
) -> Invitation:
    try:
        _, organization, actor = _actor_and_org(db, user_id, organization_id, lock=True)
        _require_role(actor, {"OWNER", "ADMIN"})
        invitation = db.scalar(
            select(Invitation)
            .where(Invitation.id == invitation_id, Invitation.organization_id == organization.id)
            .with_for_update()
        )
        if invitation is None:
            raise _error(404, "not_found", "The invitation was not found")
        if invitation.status == "PENDING":
            now = database_utc_now(db)
            if invitation.expires_at <= now:
                invitation.status = "EXPIRED"
                db.commit()
                raise _error(409, "invitation_unavailable", "The invitation is unavailable")
            invitation.status = "REVOKED"
            invitation.revoked_at = now
        elif invitation.status != "REVOKED":
            raise _error(409, "invitation_unavailable", "The invitation is unavailable")
        db.commit()
        return invitation
    except Exception:
        db.rollback()
        raise
