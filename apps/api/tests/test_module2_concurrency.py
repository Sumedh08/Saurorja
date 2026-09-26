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
    Organization,
    PendingIdentitySession,
    User,
)
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
        stored_attempt = db.scalar(
            select(InvitationAcceptanceAttempt).where(InvitationAcceptanceAttempt.id == attempt_id)
        )
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
