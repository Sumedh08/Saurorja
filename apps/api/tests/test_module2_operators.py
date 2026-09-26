import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.cli import _confirm, parser
from app.core.config import Settings
from app.db.session import SessionFactory
from app.modules.identity.models import (
    ApplicationSession,
    ExternalIdentity,
    InstallationState,
    Membership,
    OperatorAction,
    Organization,
    User,
)
from app.modules.identity.operators import (
    OperatorConflict,
    bootstrap_admin,
    disable_user,
    enable_user,
    link_identity,
    provision_organization,
    recover_owner,
)
from app.modules.identity.organizations import change_membership_role
from app.modules.identity.security import digest_secret, new_secret

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Operator concurrency tests require PostgreSQL"
)

ISSUER = "https://issuer.test"


def operator_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        public_app_origin="https://testserver",
        oidc_issuer_url=ISSUER,
        oidc_client_id="test-client",
        oidc_client_secret="test-secret-only-for-tests",
        oidc_redirect_uri="https://testserver/api/v1/auth/callback",
        oidc_post_logout_redirect_uri="https://testserver/api/v1/auth/logout/callback",
    )


def seed_organization_with_users(
    owner_count: int,
    *,
    extra_users: int = 0,
) -> tuple[UUID, list[UUID], list[UUID]]:
    with SessionFactory.begin() as db:
        organization = Organization(name=f"Operators {uuid4()}")
        db.add(organization)
        db.flush()
        users: list[User] = []
        identities: list[ExternalIdentity] = []
        for index in range(owner_count + extra_users):
            user = User(status="ACTIVE", display_name=f"Member {index}")
            db.add(user)
            db.flush()
            identity = ExternalIdentity(user_id=user.id, issuer=ISSUER, subject=f"sub-{uuid4()}")
            db.add(identity)
            users.append(user)
            identities.append(identity)
            if index < owner_count:
                db.add(
                    Membership(
                        organization_id=organization.id,
                        user_id=user.id,
                        role="OWNER",
                        status="ACTIVE",
                        last_activated_at=datetime.now(UTC),
                    )
                )
            else:
                db.add(
                    Membership(
                        organization_id=organization.id,
                        user_id=user.id,
                        role="VIEWER",
                        status="ACTIVE",
                        last_activated_at=datetime.now(UTC),
                    )
                )
        return (
            organization.id,
            [user.id for user in users],
            [identity.id for identity in identities],
        )


def test_concurrent_owner_demotions_leave_one_active_owner() -> None:
    organization_id, user_ids, _ = seed_organization_with_users(2)
    with SessionFactory() as db:
        memberships_by_user = dict(
            db.execute(
                select(Membership.user_id, Membership.id).where(
                    Membership.organization_id == organization_id
                )
            ).all()
        )
    gate = Barrier(2)

    def demote(user_id: UUID, membership_id: UUID) -> str:
        gate.wait(timeout=10)
        with SessionFactory() as db:
            try:
                change_membership_role(db, user_id, organization_id, membership_id, "MANAGER")
                return "changed"
            except HTTPException as exc:
                db.rollback()
                if isinstance(exc.detail, dict):
                    return str(exc.detail.get("code"))
                return "request_error"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(demote, user_ids, [memberships_by_user[user_id] for user_id in user_ids])
        )
    assert results.count("changed") == 1
    assert results.count("forbidden") + results.count("last_owner_required") == 1
    with SessionFactory() as db:
        count = db.scalar(
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
    assert count == 1


def test_disable_requires_all_sole_owner_successors_and_is_atomic() -> None:
    with SessionFactory.begin() as db:
        organizations = [
            Organization(name=f"Affected {uuid4()}"),
            Organization(name=f"Affected {uuid4()}"),
        ]
        db.add_all(organizations)
        db.flush()
        target = User(status="ACTIVE")
        successors = [User(status="ACTIVE"), User(status="ACTIVE")]
        db.add_all([target, *successors])
        db.flush()
        identity = ExternalIdentity(user_id=target.id, issuer=ISSUER, subject=f"target-{uuid4()}")
        db.add(identity)
        db.flush()
        for index, organization in enumerate(organizations):
            db.add_all(
                [
                    Membership(
                        organization_id=organization.id,
                        user_id=target.id,
                        role="OWNER",
                        status="ACTIVE",
                    ),
                    Membership(
                        organization_id=organization.id,
                        user_id=successors[index].id,
                        role="VIEWER",
                        status="ACTIVE",
                    ),
                ]
            )
        target_id = target.id
        organization_ids = [organization.id for organization in organizations]
        successor_ids = [user.id for user in successors]
        session_secret = new_secret()
        db.add(
            ApplicationSession(
                session_token_hash=digest_secret(session_secret),
                csrf_secret=os.urandom(32),
                user_id=target.id,
                external_identity_id=identity.id,
                absolute_expires_at=datetime.now(UTC) + timedelta(hours=12),
            )
        )

    with SessionFactory() as db:
        with pytest.raises(OperatorConflict):
            with db.begin():
                disable_user(
                    db,
                    user_id=target_id,
                    successors={organization_ids[0]: successor_ids[0]},
                    operator_label="test operator",
                    reason="test rollback for omitted successor",
                    operation_id=uuid4(),
                )
    with SessionFactory() as db:
        loaded_target = db.get(User, target_id)
        session = db.scalar(
            select(ApplicationSession).where(
                ApplicationSession.session_token_hash == digest_secret(session_secret)
            )
        )
        assert loaded_target is not None and loaded_target.status == "ACTIVE"
        assert session is not None and session.revoked_at is None
        assert db.scalar(select(func.count()).select_from(OperatorAction)) == 0

    operation_id = uuid4()
    with SessionFactory() as db, db.begin():
        disable_user(
            db,
            user_id=target_id,
            successors=dict(zip(organization_ids, successor_ids, strict=True)),
            operator_label="test operator",
            reason="planned operator recovery test",
            operation_id=operation_id,
        )
    with SessionFactory() as db:
        disabled_user = db.get(User, target_id)
        assert disabled_user is not None and disabled_user.status == "DISABLED"
        session = db.scalar(
            select(ApplicationSession).where(
                ApplicationSession.session_token_hash == digest_secret(session_secret)
            )
        )
        assert session is not None and session.revocation_reason == "user_disabled"
        owner_count = db.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id.in_(organization_ids),
                Membership.role == "OWNER",
                Membership.status == "ACTIVE",
                User.status == "ACTIVE",
            )
        )
        assert owner_count == 2


def test_bootstrap_race_and_exact_repeat_are_safe() -> None:
    settings = operator_settings()
    operation_id = uuid4()
    gate = Barrier(2)

    def bootstrap() -> dict[str, UUID | str]:
        gate.wait(timeout=10)
        with SessionFactory() as db, db.begin():
            return bootstrap_admin(
                db,
                settings,
                organization_name="First Organization",
                issuer=ISSUER,
                subject="initial-subject",
                operator_label="initial operator",
                reason="initial installation bootstrap",
                operation_id=operation_id,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: bootstrap(), range(2)))
    assert {result["result"] for result in results} == {"created", "already_initialized"}
    with SessionFactory() as db:
        assert db.scalar(select(func.count()).select_from(InstallationState)) == 1
        assert db.scalar(select(func.count()).select_from(Organization)) == 1
        assert (
            db.scalar(
                select(func.count()).select_from(Membership).where(Membership.role == "OWNER")
            )
            == 1
        )
    with SessionFactory() as db, db.begin():
        repeat = bootstrap_admin(
            db,
            settings,
            organization_name="First Organization",
            issuer=ISSUER,
            subject="initial-subject",
            operator_label="initial operator",
            reason="initial installation bootstrap",
            operation_id=uuid4(),
        )
    assert repeat["result"] == "already_initialized"
    with SessionFactory() as db:
        with pytest.raises(OperatorConflict):
            with db.begin():
                bootstrap_admin(
                    db,
                    settings,
                    organization_name="Different Organization",
                    issuer=ISSUER,
                    subject="initial-subject",
                    operator_label="initial operator",
                    reason="conflicting repeat",
                    operation_id=uuid4(),
                )


def test_recover_owner_is_narrow_and_idempotent() -> None:
    settings = operator_settings()
    organization_id = uuid4()
    with SessionFactory.begin() as db:
        organization = Organization(id=organization_id, name="Recovery Target")
        db.add(organization)
    operation_id = uuid4()
    with SessionFactory() as db, db.begin():
        first = recover_owner(
            db,
            settings=settings,
            organization_id=organization_id,
            issuer=ISSUER,
            subject="emergency-subject",
            operator_label="server operator",
            reason="Restore owner access after the only identity was lost",
            operation_id=operation_id,
        )
    with SessionFactory() as db, db.begin():
        retry = recover_owner(
            db,
            settings=settings,
            organization_id=organization_id,
            issuer=ISSUER,
            subject="emergency-subject",
            operator_label="server operator",
            reason="Restore owner access after the only identity was lost",
            operation_id=operation_id,
        )
    assert first["result"] == "recovered"
    assert retry["result"] == "already_recovered"
    with SessionFactory() as db:
        user = db.get(User, UUID(str(first["user_id"])))
        membership = db.get(Membership, UUID(str(first["membership_id"])))
        action = db.scalar(
            select(OperatorAction).where(OperatorAction.operation_id == operation_id)
        )
        assert action is not None
        identity = db.get(ExternalIdentity, UUID(str(action.details["external_identity_id"])))
        assert user is not None and user.status == "ACTIVE" and user.email is None
        assert (
            membership is not None and membership.role == "OWNER" and membership.status == "ACTIVE"
        )
        assert (
            identity is not None
            and identity.issuer == ISSUER
            and identity.subject == "emergency-subject"
        )
        assert "subject" not in action.details and "email" not in action.details


def test_recover_owner_fails_while_normal_owner_administration_is_available() -> None:
    settings = operator_settings()
    organization_id, user_ids, _ = seed_organization_with_users(1)
    with SessionFactory() as db:
        with pytest.raises(OperatorConflict, match="active Owner"):
            with db.begin():
                recover_owner(
                    db,
                    settings=settings,
                    organization_id=organization_id,
                    issuer=ISSUER,
                    subject="unlinked-recovery-subject",
                    operator_label="server operator",
                    reason="Attempt must fail while an Owner exists",
                    operation_id=uuid4(),
                )
    with SessionFactory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        current_user = db.get(User, user_ids[0])
        assert current_user is not None and current_user.status == "ACTIVE"


def test_operator_user_status_identity_link_and_organization_provisioning() -> None:
    settings = operator_settings()
    with SessionFactory.begin() as db:
        target = User(status="DISABLED", display_name=None)
        db.add(target)
        db.flush()
        target_id = target.id
    with SessionFactory() as db, db.begin():
        enable_user(
            db,
            user_id=target_id,
            operator_label="server operator",
            reason="Restore access for verified identity",
            operation_id=uuid4(),
        )
    with SessionFactory() as db, db.begin():
        linked_identity_id = link_identity(
            db,
            settings=settings,
            user_id=target_id,
            issuer=ISSUER,
            subject="explicit-link-subject",
            operator_label="server operator",
            reason="Link identity after provider migration",
            operation_id=uuid4(),
        )
    with SessionFactory() as db, db.begin():
        organization_id = provision_organization(
            db,
            settings,
            name="Operator provisioned",
            user_id=target_id,
            operator_label="server operator",
            reason="Create customer Organization after approval",
            operation_id=uuid4(),
        )
    with SessionFactory() as db:
        membership = db.scalar(
            select(Membership).where(
                Membership.organization_id == organization_id,
                Membership.user_id == target_id,
            )
        )
        current_user = db.get(User, target_id)
        linked_identity = db.get(ExternalIdentity, linked_identity_id)
        assert current_user is not None and current_user.status == "ACTIVE"
        assert linked_identity is not None and linked_identity.subject == "explicit-link-subject"
        assert membership is not None and membership.role == "OWNER"


def test_concurrent_exact_recover_owner_calls_are_idempotent() -> None:
    organization_id, _, _ = seed_organization_with_users(0)
    operation_id = uuid4()
    subject = f"recovery-{uuid4()}"
    gate = Barrier(2)

    def recover() -> dict[str, UUID | int | str]:
        gate.wait(timeout=10)
        with SessionFactory.begin() as db:
            return recover_owner(
                db,
                settings=operator_settings(),
                organization_id=organization_id,
                issuer=ISSUER,
                subject=subject,
                operator_label="concurrency-test",
                reason="Verify exact concurrent recovery retry",
                operation_id=operation_id,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recover(), range(2)))

    assert {result["result"] for result in results} == {"recovered", "already_recovered"}
    assert len({result["user_id"] for result in results}) == 1
    with SessionFactory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ExternalIdentity)
                .where(ExternalIdentity.issuer == ISSUER, ExternalIdentity.subject == subject)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(OperatorAction)
                .where(OperatorAction.operation_id == operation_id)
            )
            == 1
        )


def test_operator_cli_has_only_purpose_specific_role_recovery() -> None:
    args = parser().parse_args(
        [
            "auth",
            "recover-owner",
            "--organization-id",
            str(uuid4()),
            "--issuer",
            ISSUER,
            "--operator",
            "operator",
            "--reason",
            "recovery test",
            "--operation-id",
            str(uuid4()),
            "--confirm-organization",
            str(uuid4()),
        ]
    )
    assert args.command == "recover-owner"
    assert not hasattr(parser().parse_args(["auth", "prune"]), "role")


def test_noninteractive_cli_uuid_confirmation_accepts_exact_target() -> None:
    target = uuid4()
    _confirm(str(target), target, "Confirm test operation.")
