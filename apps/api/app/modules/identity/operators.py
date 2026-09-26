from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text, tuple_, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.identity.models import (
    ApplicationSession,
    ExternalIdentity,
    InstallationState,
    Membership,
    OperatorAction,
    Organization,
    PendingIdentitySession,
    User,
)

BOOTSTRAP_LOCK_ID = 7_132_026


class OperatorConflict(Exception):
    """A safe, expected operator workflow conflict."""


def validate_operator(label: str, reason: str) -> tuple[str, str]:
    label = label.strip()
    reason = reason.strip()
    if not label or len(label) > 120 or "@" in label:
        raise ValueError("operator label must be a non-email label of at most 120 characters")
    if not reason or len(reason) > 1000:
        raise ValueError("a reason of at most 1000 characters is required")
    return label, reason


def _existing_action(db: Session, operation_id: UUID) -> OperatorAction | None:
    return db.scalar(select(OperatorAction).where(OperatorAction.operation_id == operation_id))


def _action(
    db: Session,
    operation_id: UUID,
    action_type: str,
    label: str,
    reason: str,
    *,
    user_id: UUID | None = None,
    organization_id: UUID | None = None,
    details: Mapping[str, object],
) -> OperatorAction:
    action = OperatorAction(
        operation_id=operation_id,
        action_type=action_type,
        operator_label=label,
        reason=reason,
        target_user_id=user_id,
        target_organization_id=organization_id,
        details=dict(details),
    )
    db.add(action)
    return action


def _assert_replay(
    action: OperatorAction | None,
    *,
    action_type: str,
    label: str,
    reason: str,
    expected: Mapping[str, object],
) -> bool:
    if action is None:
        return False
    if (
        action.action_type == action_type
        and action.operator_label == label
        and action.reason == reason
        and all(action.details.get(key) == value for key, value in expected.items())
    ):
        return True
    raise OperatorConflict("operation UUID was already used for different inputs")


def bootstrap_admin(
    db: Session,
    settings: Settings,
    *,
    organization_name: str,
    issuer: str,
    subject: str,
    operator_label: str,
    reason: str,
    operation_id: UUID,
) -> dict[str, UUID | str]:
    label, reason = validate_operator(operator_label, reason)
    name = organization_name.strip()
    if not name or len(name) > 200:
        raise ValueError("Organization name must be between 1 and 200 characters")
    if not settings.oidc_issuer_url or issuer != settings.oidc_issuer_url or not subject:
        raise OperatorConflict(
            "issuer must exactly match the configured OIDC issuer and subject is required"
        )
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": BOOTSTRAP_LOCK_ID})
    state = db.get(InstallationState, 1)
    if state is not None:
        organization = db.get(Organization, state.initial_organization_id)
        identity = db.get(ExternalIdentity, state.initial_external_identity_id)
        user = db.get(User, state.initial_user_id)
        initial_owner = db.scalar(
            select(Membership.id).where(
                Membership.organization_id == state.initial_organization_id,
                Membership.user_id == state.initial_user_id,
                Membership.role == "OWNER",
                Membership.status == "ACTIVE",
            )
        )
        expected = (
            organization is not None
            and identity is not None
            and user is not None
            and user.status == "ACTIVE"
            and identity.user_id == state.initial_user_id
            and initial_owner is not None
            and state.organization_name == name
            and identity.issuer == issuer
            and identity.subject == subject
        )
        if expected:
            return {
                "organization_id": state.initial_organization_id,
                "user_id": state.initial_user_id,
                "external_identity_id": state.initial_external_identity_id,
                "result": "already_initialized",
            }
        raise OperatorConflict("installation is already initialized with different inputs")
    if any(
        db.scalar(select(func.count()).select_from(model))
        for model in (User, ExternalIdentity, Organization, Membership)
    ):
        raise OperatorConflict("bootstrap requires an empty identity and Organization database")
    existing = _existing_action(db, operation_id)
    if existing is not None:
        raise OperatorConflict("bootstrap action exists without a consistent installation state")
    now = datetime.now(UTC)
    organization = Organization(name=name)
    user = User(status="ACTIVE")
    db.add_all([organization, user])
    db.flush()
    identity = ExternalIdentity(user_id=user.id, issuer=issuer, subject=subject)
    membership = Membership(
        organization_id=organization.id,
        user_id=user.id,
        role="OWNER",
        status="ACTIVE",
        last_activated_at=now,
    )
    db.add_all([identity, membership])
    db.flush()
    db.add(
        InstallationState(
            singleton_id=1,
            initial_organization_id=organization.id,
            initial_user_id=user.id,
            initial_external_identity_id=identity.id,
            organization_name=name,
        )
    )
    _action(
        db,
        operation_id,
        "BOOTSTRAPPED",
        label,
        reason,
        user_id=user.id,
        organization_id=organization.id,
        details={
            "organization_name": name,
            "organization_id": str(organization.id),
            "user_id": str(user.id),
            "external_identity_id": str(identity.id),
            "issuer_identity": True,
        },
    )
    return {
        "organization_id": organization.id,
        "user_id": user.id,
        "external_identity_id": identity.id,
        "result": "created",
    }


def provision_organization(
    db: Session,
    settings: Settings,
    *,
    name: str,
    user_id: UUID,
    operator_label: str,
    reason: str,
    operation_id: UUID,
) -> UUID:
    label, reason = validate_operator(operator_label, reason)
    name = name.strip()
    if not name or len(name) > 200:
        raise ValueError("Organization name must be between 1 and 200 characters")
    expected = {"organization_name": name, "owner_user_id": str(user_id)}
    if _assert_replay(
        _existing_action(db, operation_id),
        action_type="ORGANIZATION_PROVISIONED",
        label=label,
        reason=reason,
        expected=expected,
    ):
        return UUID(str(_existing_action(db, operation_id).details["organization_id"]))  # type: ignore[union-attr]
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or user.status != "ACTIVE":
        raise OperatorConflict("owner User must exist and be ACTIVE")
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.user_id == user.id,
            ExternalIdentity.issuer == settings.oidc_issuer_url,
        )
    )
    if identity is None:
        raise OperatorConflict("owner must have an identity linked to the configured issuer")
    organization = Organization(name=name)
    db.add(organization)
    db.flush()
    membership = Membership(
        organization_id=organization.id,
        user_id=user.id,
        role="OWNER",
        status="ACTIVE",
        last_activated_at=datetime.now(UTC),
    )
    db.add(membership)
    _action(
        db,
        operation_id,
        "ORGANIZATION_PROVISIONED",
        label,
        reason,
        user_id=user.id,
        organization_id=organization.id,
        details={**expected, "organization_id": str(organization.id)},
    )
    return organization.id


def _lock_users(db: Session, ids: set[UUID]) -> dict[UUID, User]:
    if not ids:
        return {}
    users = list(
        db.scalars(select(User).where(User.id.in_(ids)).order_by(User.id).with_for_update()).all()
    )
    return {user.id: user for user in users}


def _user_is_active(db: Session, users: dict[UUID, User], user_id: UUID) -> bool:
    user = users.get(user_id)
    if user is None:
        user = db.get(User, user_id)
    return user is not None and user.status == "ACTIVE"


def disable_user(
    db: Session,
    *,
    user_id: UUID,
    successors: dict[UUID, UUID],
    operator_label: str,
    reason: str,
    operation_id: UUID,
) -> list[UUID]:
    label, reason = validate_operator(operator_label, reason)
    expected = {
        "target_user_id": str(user_id),
        "successors": {str(org): str(successor) for org, successor in sorted(successors.items())},
    }
    existing = _existing_action(db, operation_id)
    if _assert_replay(
        existing,
        action_type="USER_DISABLED",
        label=label,
        reason=reason,
        expected=expected,
    ):
        return [UUID(value) for value in existing.details.get("affected_organization_ids", [])]  # type: ignore[union-attr]

    all_user_ids = {user_id, *successors.values()}
    users = _lock_users(db, all_user_ids)
    target = users.get(user_id)
    if target is None or target.status != "ACTIVE":
        raise OperatorConflict("target User must exist and be ACTIVE")
    owned_rows = list(
        db.execute(
            select(Membership.organization_id, Membership.id)
            .where(
                Membership.user_id == user_id,
                Membership.role == "OWNER",
                Membership.status == "ACTIVE",
            )
            .order_by(Membership.organization_id)
        ).all()
    )
    affected_ids = sorted({row.organization_id for row in owned_rows})
    organizations = (
        list(
            db.scalars(
                select(Organization)
                .where(Organization.id.in_(affected_ids))
                .order_by(Organization.id)
                .with_for_update()
            ).all()
        )
        if affected_ids
        else []
    )
    if len(organizations) != len(affected_ids):
        raise OperatorConflict("an affected Organization is unavailable")
    memberships = (
        list(
            db.scalars(
                select(Membership)
                .where(Membership.organization_id.in_(affected_ids))
                .order_by(Membership.id)
                .with_for_update()
            ).all()
        )
        if affected_ids
        else []
    )
    by_org: dict[UUID, list[Membership]] = {}
    for membership in memberships:
        by_org.setdefault(membership.organization_id, []).append(membership)
    sole_owner_orgs: list[UUID] = []
    for org_id in affected_ids:
        active_owner_ids = {
            membership.user_id
            for membership in by_org.get(org_id, [])
            if membership.role == "OWNER"
            and membership.status == "ACTIVE"
            and _user_is_active(db, users, membership.user_id)
        }
        if len(active_owner_ids) <= 1:
            sole_owner_orgs.append(org_id)
    if set(successors) != set(sole_owner_orgs):
        raise OperatorConflict(
            "an explicit successor is required for each and only each sole-Owner Organization"
        )
    for org_id, successor_id in successors.items():
        if successor_id == user_id:
            raise OperatorConflict("the disabled User cannot be their own successor")
        successor = users.get(successor_id)
        if successor is None or successor.status != "ACTIVE":
            raise OperatorConflict("every successor must be an existing ACTIVE User")
        successor_membership = next(
            (
                item
                for item in by_org.get(org_id, [])
                if item.user_id == successor_id and item.status == "ACTIVE"
            ),
            None,
        )
        if successor_membership is None:
            raise OperatorConflict(
                "every successor must have an ACTIVE Membership in the Organization"
            )
        successor_membership.role = "OWNER"
        successor_membership.updated_at = datetime.now(UTC)
    if any(
        not any(
            member.role == "OWNER"
            and member.status == "ACTIVE"
            and _user_is_active(db, users, member.user_id)
            for member in by_org.get(org_id, [])
        )
        for org_id in affected_ids
    ):
        raise OperatorConflict("every affected Organization must retain an active Owner")
    target.status = "DISABLED"
    target.status_changed_at = datetime.now(UTC)
    db.execute(
        update(ApplicationSession)
        .where(ApplicationSession.user_id == target.id, ApplicationSession.revoked_at.is_(None))
        .values(revoked_at=target.status_changed_at, revocation_reason="user_disabled")
    )
    identity_pairs = select(ExternalIdentity.issuer, ExternalIdentity.subject).where(
        ExternalIdentity.user_id == target.id
    )
    db.execute(
        update(PendingIdentitySession)
        .where(
            tuple_(PendingIdentitySession.issuer, PendingIdentitySession.subject).in_(
                identity_pairs
            ),
            PendingIdentitySession.revoked_at.is_(None),
            PendingIdentitySession.consumed_at.is_(None),
        )
        .values(revoked_at=target.status_changed_at)
    )
    _action(
        db,
        operation_id,
        "USER_DISABLED",
        label,
        reason,
        user_id=target.id,
        details={**expected, "affected_organization_ids": [str(value) for value in affected_ids]},
    )
    return affected_ids


def enable_user(
    db: Session,
    *,
    user_id: UUID,
    operator_label: str,
    reason: str,
    operation_id: UUID,
) -> None:
    label, reason = validate_operator(operator_label, reason)
    expected = {"target_user_id": str(user_id)}
    if _assert_replay(
        _existing_action(db, operation_id),
        action_type="USER_ENABLED",
        label=label,
        reason=reason,
        expected=expected,
    ):
        return
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or user.status != "DISABLED":
        raise OperatorConflict("target User must exist and be DISABLED")
    user.status = "ACTIVE"
    user.status_changed_at = datetime.now(UTC)
    _action(
        db,
        operation_id,
        "USER_ENABLED",
        label,
        reason,
        user_id=user.id,
        details=expected,
    )


def link_identity(
    db: Session,
    *,
    settings: Settings,
    user_id: UUID,
    issuer: str,
    subject: str,
    operator_label: str,
    reason: str,
    operation_id: UUID,
) -> UUID:
    label, reason = validate_operator(operator_label, reason)
    if issuer != settings.oidc_issuer_url or not subject:
        raise OperatorConflict("issuer must exactly match configured OIDC issuer")
    existing_identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject
        )
    )
    expected = {
        "target_user_id": str(user_id),
        "external_identity_id": str(existing_identity.id) if existing_identity else "new",
    }
    if _assert_replay(
        _existing_action(db, operation_id),
        action_type="IDENTITY_LINKED",
        label=label,
        reason=reason,
        expected=expected,
    ):
        return UUID(str(_existing_action(db, operation_id).details["external_identity_id"]))  # type: ignore[union-attr]
    users = _lock_users(db, {user_id})
    user = users.get(user_id)
    if user is None:
        raise OperatorConflict("target User does not exist")
    if existing_identity is not None:
        if existing_identity.user_id != user_id:
            raise OperatorConflict("identity is already linked to another User")
        identity = existing_identity
    else:
        identity = ExternalIdentity(user_id=user_id, issuer=issuer, subject=subject)
        db.add(identity)
        db.flush()
    _action(
        db,
        operation_id,
        "IDENTITY_LINKED",
        label,
        reason,
        user_id=user_id,
        details={"target_user_id": str(user_id), "external_identity_id": str(identity.id)},
    )
    return identity.id


def recover_owner(
    db: Session,
    *,
    settings: Settings,
    organization_id: UUID,
    issuer: str,
    subject: str,
    operator_label: str,
    reason: str,
    operation_id: UUID,
) -> dict[str, UUID | int | str]:
    label, reason = validate_operator(operator_label, reason)
    if issuer != settings.oidc_issuer_url or not subject:
        raise OperatorConflict("issuer must exactly match configured OIDC issuer")
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject
        )
    )
    user = (
        _lock_users(db, {identity.user_id} if identity else set()).get(identity.user_id)
        if identity
        else None
    )
    existing = _existing_action(db, operation_id)
    if existing is not None:
        expected_identity_id = str(identity.id) if identity else "unlinked"
        if _assert_replay(
            existing,
            action_type="OWNER_RECOVERED",
            label=label,
            reason=reason,
            expected={
                "organization_id": str(organization_id),
                "external_identity_id": expected_identity_id,
            },
        ):
            return {
                "organization_id": organization_id,
                "user_id": UUID(str(existing.details["user_id"])),
                "membership_id": UUID(str(existing.details["membership_id"])),
                "active_owners_after": int(existing.details["active_owners_after"]),
                "result": "already_recovered",
            }
    if user is not None and user.status != "ACTIVE":
        raise OperatorConflict("a DISABLED identity cannot recover Organization ownership")
    organization = db.scalar(
        select(Organization).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise OperatorConflict("Organization does not exist")
    # Concurrent exact retries may have waited on this Organization lock. Recheck their
    # operation record after serialization so an unlinked identity recovery is idempotent too.
    existing = _existing_action(db, operation_id)
    if existing is not None:
        replay_identity = db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject
            )
        )
        if replay_identity is None or not _assert_replay(
            existing,
            action_type="OWNER_RECOVERED",
            label=label,
            reason=reason,
            expected={
                "organization_id": str(organization_id),
                "external_identity_id": str(replay_identity.id),
            },
        ):
            raise OperatorConflict("operation ID was already used for another recovery")
        return {
            "organization_id": organization_id,
            "user_id": UUID(str(existing.details["user_id"])),
            "membership_id": UUID(str(existing.details["membership_id"])),
            "active_owners_after": int(existing.details["active_owners_after"]),
            "result": "already_recovered",
        }
    if identity is not None:
        # Identity uniqueness and the user lock are checked again after Organization serialization.
        identity = db.scalar(
            select(ExternalIdentity)
            .where(ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject)
            .with_for_update()
        )
        if identity is None or user is None or identity.user_id != user.id:
            raise OperatorConflict("identity mapping changed; retry recovery")
    before = int(
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id == organization.id,
                Membership.role == "OWNER",
                Membership.status == "ACTIVE",
                User.status == "ACTIVE",
            )
        )
        or 0
    )
    if before > 0:
        raise OperatorConflict(
            "an active Owner can restore access through normal Organization administration"
        )
    membership = (
        db.scalar(
            select(Membership)
            .where(
                Membership.organization_id == organization.id,
                Membership.user_id == user.id,
            )
            .order_by(
                (Membership.status != "REMOVED").desc(),
                Membership.created_at.desc(),
            )
            .with_for_update()
        )
        if user
        else None
    )
    if membership and membership.role == "OWNER" and membership.status == "ACTIVE":
        raise OperatorConflict("identity is already an active Owner for this Organization")
    prior_membership_id = str(membership.id) if membership else None
    prior_role = membership.role if membership else None
    prior_status = membership.status if membership else None
    if user is None:
        user = User(status="ACTIVE")
        db.add(user)
        db.flush()
        identity = ExternalIdentity(user_id=user.id, issuer=issuer, subject=subject)
        db.add(identity)
        db.flush()
    assert identity is not None
    now = datetime.now(UTC)
    if membership is None or membership.status == "REMOVED":
        membership = Membership(
            organization_id=organization.id,
            user_id=user.id,
            role="OWNER",
            status="ACTIVE",
            last_activated_at=now,
        )
        db.add(membership)
        db.flush()
    else:
        membership.role = "OWNER"
        membership.status = "ACTIVE"
        membership.updated_at = now
        membership.last_activated_at = now
    after = int(
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id == organization.id,
                Membership.role == "OWNER",
                Membership.status == "ACTIVE",
                User.status == "ACTIVE",
            )
        )
        or 0
    )
    if after < 1:
        raise OperatorConflict("recovery failed to establish an active Owner")
    details = {
        "organization_id": str(organization.id),
        "user_id": str(user.id),
        "external_identity_id": str(identity.id),
        "membership_id": str(membership.id),
        "prior_membership_id": prior_membership_id,
        "prior_role": prior_role,
        "result_role": "OWNER",
        "prior_status": prior_status,
        "result_status": "ACTIVE",
        "active_owners_before": before,
        "active_owners_after": after,
    }
    _action(
        db,
        operation_id,
        "OWNER_RECOVERED",
        label,
        reason,
        user_id=user.id,
        organization_id=organization.id,
        details=details,
    )
    return {
        "organization_id": organization.id,
        "user_id": user.id,
        "membership_id": membership.id,
        "active_owners_after": after,
        "result": "recovered",
    }
