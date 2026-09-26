import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.session import SessionFactory
from app.modules.identity.dependencies import BrowserState
from app.modules.identity.invitations import accept_attempt
from app.modules.identity.models import (
    ApplicationSession,
    Invitation,
    InvitationAcceptanceAttempt,
    Membership,
    OperatorAction,
    Organization,
    PendingIdentitySession,
    User,
)
from app.modules.identity.operators import OperatorConflict, disable_user
from app.modules.identity.organizations import change_membership_role, revoke_invitation
from app.modules.identity.security import digest_secret, new_secret

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Concurrency tests require PostgreSQL"
)


def test_concurrent_invitation_acceptance_is_single_use() -> None:
    pending_secret = new_secret()
    invitation_secret = new_secret()
    with SessionFactory.begin() as db:
        organization = Organization(name=f"Invitation race {uuid4()}")
        owner = User(status="ACTIVE")
        db.add_all([organization, owner])
        db.flush()
        owner_membership = Membership(
            organization_id=organization.id,
            user_id=owner.id,
            role="OWNER",
            status="ACTIVE",
        )
        pending = PendingIdentitySession(
            token_hash=digest_secret(pending_secret),
            csrf_secret=os.urandom(32),
            issuer="https://issuer.test",
            subject=f"invitee-{uuid4()}",
            normalized_email="race@example.com",
            email_verified=True,
            absolute_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add_all([owner_membership, pending])
        db.flush()
        invitation = Invitation(
            organization_id=organization.id,
            creator_membership_id=owner_membership.id,
            email="race@example.com",
            normalized_email="race@example.com",
            role="VIEWER",
            token_hash=digest_secret(invitation_secret),
            status="PENDING",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db.add(invitation)
        db.flush()
        attempt = InvitationAcceptanceAttempt(
            invitation_id=invitation.id,
            pending_identity_session_id=pending.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add(attempt)
        db.flush()
        organization_id = organization.id
        pending_id = pending.id
        attempt_id = attempt.id

    gate = Barrier(2)

    def accept() -> str:
        gate.wait(timeout=10)
        with SessionFactory() as db:
            pending_session = db.get(PendingIdentitySession, pending_id)
            assert pending_session is not None
            state = BrowserState("pending_identity", pending_session)
            try:
                accept_attempt(db, state, attempt_id)
                return "accepted"
            except HTTPException as exc:
                db.rollback()
                if isinstance(exc.detail, dict):
                    return str(exc.detail.get("code", "rejected"))
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))

    assert results.count("accepted") == 1
    assert len(results) == 2 and results[0] != results[1]
    with SessionFactory() as db:
        accepted_user = db.scalar(select(User).where(User.normalized_email == "race@example.com"))
        assert accepted_user is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.organization_id == organization_id,
                    Membership.user_id == accepted_user.id,
                    Membership.role == "VIEWER",
                    Membership.status == "ACTIVE",
                )
            )
            == 1
        )
        stored_invitation = db.scalar(
            select(Invitation).where(Invitation.token_hash == digest_secret(invitation_secret))
        )
        stored_attempt = db.get(InvitationAcceptanceAttempt, attempt_id)
        stored_pending = db.get(PendingIdentitySession, pending_id)
        assert stored_invitation is not None and stored_invitation.status == "ACCEPTED"
        assert stored_attempt is not None and stored_attempt.consumed_at is not None
        assert stored_pending is not None and stored_pending.consumed_at is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(ApplicationSession)
                .where(
                    ApplicationSession.user_id == accepted_user.id,
                    ApplicationSession.revoked_at.is_(None),
                )
            )
            == 1
        )


def test_concurrent_invitation_revoke_and_accept_have_one_winner() -> None:
    invitation_secret = new_secret()
    with SessionFactory.begin() as db:
        organization = Organization(name=f"Revoke race {uuid4()}")
        owner = User(status="ACTIVE")
        db.add_all([organization, owner])
        db.flush()
        owner_membership = Membership(
            organization_id=organization.id,
            user_id=owner.id,
            role="OWNER",
            status="ACTIVE",
        )
        pending = PendingIdentitySession(
            token_hash=digest_secret(new_secret()),
            csrf_secret=os.urandom(32),
            issuer="https://issuer.test",
            subject=f"revoke-race-{uuid4()}",
            normalized_email="revoke-race@example.com",
            email_verified=True,
            absolute_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add_all([owner_membership, pending])
        db.flush()
        now = db.scalar(func.clock_timestamp())
        assert isinstance(now, datetime)
        invitation = Invitation(
            organization_id=organization.id,
            creator_membership_id=owner_membership.id,
            email="revoke-race@example.com",
            normalized_email="revoke-race@example.com",
            role="VIEWER",
            token_hash=digest_secret(invitation_secret),
            status="PENDING",
            expires_at=now + timedelta(days=7),
        )
        db.add(invitation)
        db.flush()
        attempt = InvitationAcceptanceAttempt(
            invitation_id=invitation.id,
            pending_identity_session_id=pending.id,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(attempt)
        db.flush()
        owner_id, organization_id = owner.id, organization.id
        invitation_id, attempt_id, pending_id = invitation.id, attempt.id, pending.id

    gate = Barrier(2)

    def accept() -> str:
        gate.wait(timeout=10)
        with SessionFactory() as db:
            pending_session = db.get(PendingIdentitySession, pending_id)
            assert pending_session is not None
            try:
                accept_attempt(db, BrowserState("pending_identity", pending_session), attempt_id)
                return "accepted"
            except HTTPException:
                db.rollback()
                return "accept_rejected"

    def revoke() -> str:
        gate.wait(timeout=10)
        with SessionFactory() as db:
            try:
                revoke_invitation(db, owner_id, organization_id, invitation_id)
                return "revoked"
            except HTTPException:
                db.rollback()
                return "revoke_rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        accept_result, revoke_result = list(pool.map(lambda fn: fn(), (accept, revoke)))

    assert {accept_result, revoke_result} in (
        {"accepted", "revoke_rejected"},
        {"accept_rejected", "revoked"},
    )
    with SessionFactory() as db:
        stored = db.get(Invitation, invitation_id)
        assert stored is not None
        assert stored.status in {"ACCEPTED", "REVOKED"}
        member_count = db.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id == organization_id,
                Membership.status == "ACTIVE",
                User.normalized_email == "revoke-race@example.com",
            )
        )
        assert member_count == (1 if stored.status == "ACCEPTED" else 0)


def test_user_disable_races_membership_role_change_without_losing_owner() -> None:
    operation_id = uuid4()
    with SessionFactory.begin() as db:
        organization = Organization(name=f"Disable race {uuid4()}")
        owner = User(status="ACTIVE")
        successor = User(status="ACTIVE")
        db.add_all([organization, owner, successor])
        db.flush()
        owner_membership = Membership(
            organization_id=organization.id,
            user_id=owner.id,
            role="OWNER",
            status="ACTIVE",
        )
        successor_membership = Membership(
            organization_id=organization.id,
            user_id=successor.id,
            role="MANAGER",
            status="ACTIVE",
        )
        db.add_all([owner_membership, successor_membership])
        db.flush()
        owner_id, successor_id = owner.id, successor.id
        organization_id, successor_membership_id = organization.id, successor_membership.id

    gate = Barrier(2)

    def disable() -> str:
        gate.wait(timeout=10)
        try:
            with SessionFactory.begin() as db:
                disable_user(
                    db,
                    user_id=owner_id,
                    successors={organization_id: successor_id},
                    operator_label="concurrency-test",
                    reason="exercise owner disable race",
                    operation_id=operation_id,
                )
            return "disabled"
        except OperatorConflict:
            return "disable_rejected"

    def change_role() -> str:
        gate.wait(timeout=10)
        with SessionFactory() as db:
            try:
                change_membership_role(
                    db, owner_id, organization_id, successor_membership_id, "VIEWER"
                )
                return "role_changed"
            except HTTPException as exc:
                db.rollback()
                if isinstance(exc.detail, dict) and exc.detail.get("code") == "unauthenticated":
                    return "actor_disabled"
                return "role_change_rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda fn: fn(), (disable, change_role)))

    assert "disabled" in results
    assert set(results).issubset({"disabled", "role_changed", "actor_disabled"})
    with SessionFactory() as db:
        disabled_owner = db.get(User, owner_id)
        promoted_successor = db.get(User, successor_id)
        membership = db.get(Membership, successor_membership_id)
        assert disabled_owner is not None and disabled_owner.status == "DISABLED"
        assert promoted_successor is not None and promoted_successor.status == "ACTIVE"
        assert membership is not None and membership.status == "ACTIVE"
        assert membership.role == "OWNER"
        active_owner_count = db.scalar(
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
        assert active_owner_count is not None and active_owner_count >= 1
        action = db.scalar(
            select(OperatorAction).where(OperatorAction.operation_id == operation_id)
        )
        assert action is not None and action.action_type == "USER_DISABLED"
