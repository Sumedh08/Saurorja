import secrets
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import database_utc_now
from app.modules.identity.dependencies import BrowserState
from app.modules.identity.models import (
    ApplicationSession,
    ExternalIdentity,
    Invitation,
    InvitationAcceptanceAttempt,
    Membership,
    PendingIdentitySession,
    User,
)
from app.modules.identity.security import digest_secret, new_secret


def _conflict(code: str = "invitation_unavailable", status: int = 409) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": "The invitation could not be accepted"}
    )


def create_attempt(db: Session, state: BrowserState, token: str) -> InvitationAcceptanceAttempt:
    invitation = db.scalar(
        select(Invitation).where(Invitation.token_hash == digest_secret(token)).with_for_update()
    )
    if invitation is None:
        raise _conflict("invitation_unavailable", 404)
    now = database_utc_now(db)
    if invitation.status == "PENDING" and invitation.expires_at <= now:
        invitation.status = "EXPIRED"
        db.commit()
        raise _conflict("invitation_unavailable", 404)
    if invitation.status != "PENDING":
        raise _conflict("invitation_unavailable", 404)
    if state.kind == "authenticated" and isinstance(state.session, ApplicationSession):
        attempt = InvitationAcceptanceAttempt(
            invitation_id=invitation.id,
            application_session_id=state.session.id,
            expires_at=min(invitation.expires_at, now + timedelta(minutes=10)),
        )
    elif state.kind == "pending_identity" and isinstance(state.session, PendingIdentitySession):
        attempt = InvitationAcceptanceAttempt(
            invitation_id=invitation.id,
            pending_identity_session_id=state.session.id,
            expires_at=min(invitation.expires_at, now + timedelta(minutes=10)),
        )
    else:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Authentication is required"},
        )
    db.add(attempt)
    db.commit()
    return attempt


def inspect_attempt(
    db: Session, state: BrowserState, attempt_id: UUID
) -> tuple[InvitationAcceptanceAttempt, Invitation]:
    attempt = db.scalar(
        select(InvitationAcceptanceAttempt).where(InvitationAcceptanceAttempt.id == attempt_id)
    )
    if attempt is None:
        raise _conflict("invitation_unavailable", 404)
    bound = (
        state.kind == "authenticated"
        and isinstance(state.session, ApplicationSession)
        and attempt.application_session_id == state.session.id
    ) or (
        state.kind == "pending_identity"
        and isinstance(state.session, PendingIdentitySession)
        and attempt.pending_identity_session_id == state.session.id
    )
    now = database_utc_now(db)
    if not bound or attempt.consumed_at is not None or attempt.expires_at <= now:
        raise _conflict("invitation_unavailable", 404)
    invitation = db.get(Invitation, attempt.invitation_id)
    if invitation is None or invitation.status != "PENDING":
        raise _conflict("invitation_unavailable", 404)
    if invitation.expires_at <= now:
        invitation.status = "EXPIRED"
        db.commit()
        raise _conflict()
    return attempt, invitation


def accept_attempt(
    db: Session, state: BrowserState, attempt_id: UUID
) -> tuple[UUID, UUID, str, str | None, datetime | None]:
    """Atomically consumes an invitation and, for pending identities, creates a normal session."""
    now = database_utc_now(db)
    try:
        attempt_preview = db.get(InvitationAcceptanceAttempt, attempt_id)
        if attempt_preview is None:
            raise _conflict("invitation_unavailable", 404)
        bound_normal = (
            state.kind == "authenticated"
            and isinstance(state.session, ApplicationSession)
            and attempt_preview.application_session_id == state.session.id
        )
        bound_pending = (
            state.kind == "pending_identity"
            and isinstance(state.session, PendingIdentitySession)
            and attempt_preview.pending_identity_session_id == state.session.id
        )
        if (
            not (bound_normal or bound_pending)
            or attempt_preview.consumed_at is not None
            or attempt_preview.expires_at <= now
        ):
            raise _conflict("invitation_unavailable", 404)

        invitation = db.get(Invitation, attempt_preview.invitation_id)
        if invitation is None or invitation.status != "PENDING":
            raise _conflict()
        if invitation.expires_at <= now:
            invitation.status = "EXPIRED"
            db.commit()
            raise _conflict()
        organization_id = invitation.organization_id

        if bound_normal:
            assert isinstance(state.session, ApplicationSession) and state.user is not None
            identity = db.scalar(
                select(ExternalIdentity).where(
                    ExternalIdentity.id == state.session.external_identity_id
                )
            )
            user = db.scalar(select(User).where(User.id == state.user.id).with_for_update())
            email = state.session.auth_email
            verified = state.session.auth_email_verified
            if identity is None or user is None or user.status != "ACTIVE":
                raise _conflict("unauthenticated", 401)
            pending = None
        else:
            assert isinstance(state.session, PendingIdentitySession)
            pending = db.get(PendingIdentitySession, state.session.id)
            if (
                pending is None
                or pending.consumed_at is not None
                or pending.revoked_at is not None
                or pending.absolute_expires_at <= now
            ):
                raise _conflict("unauthenticated", 401)
            identity = db.scalar(
                select(ExternalIdentity).where(
                    ExternalIdentity.issuer == pending.issuer,
                    ExternalIdentity.subject == pending.subject,
                )
            )
            user = None
            if identity is not None:
                user = db.scalar(select(User).where(User.id == identity.user_id).with_for_update())
                if user is None or user.status != "ACTIVE":
                    raise _conflict("unauthenticated", 401)
            email = None
            verified = pending.email_verified
            if verified and pending.normalized_email:
                email = pending.normalized_email

        if not verified or not email:
            raise _conflict("identity_conflict")
        normalized = email
        if normalized != invitation.normalized_email:
            raise _conflict("identity_conflict")

        collision = db.scalar(select(User).where(User.normalized_email == normalized))
        if collision is not None and (user is None or collision.id != user.id):
            raise _conflict("identity_conflict")

        # Organization is the serialization lock for all membership/Owner writers.
        from app.modules.identity.models import Organization

        organization = db.scalar(
            select(Organization).where(Organization.id == organization_id).with_for_update()
        )
        if organization is None:
            raise _conflict()
        invitation = db.scalar(
            select(Invitation)
            .where(Invitation.id == attempt_preview.invitation_id)
            .with_for_update()
        )
        if invitation is None or invitation.status != "PENDING":
            raise _conflict()
        attempt = db.scalar(
            select(InvitationAcceptanceAttempt)
            .where(InvitationAcceptanceAttempt.id == attempt_id)
            .with_for_update()
        )
        if bound_pending:
            assert isinstance(state.session, PendingIdentitySession)
            pending = db.scalar(
                select(PendingIdentitySession)
                .where(PendingIdentitySession.id == state.session.id)
                .with_for_update()
            )
        now = database_utc_now(db)
        if invitation.expires_at <= now:
            invitation.status = "EXPIRED"
            db.commit()
            raise _conflict()
        if attempt is None or attempt.consumed_at is not None or attempt.expires_at <= now:
            raise _conflict("invitation_unavailable", 404)
        if bound_pending:
            assert isinstance(pending, PendingIdentitySession)
            if (
                pending.consumed_at is not None
                or pending.revoked_at is not None
                or pending.absolute_expires_at <= now
            ):
                raise _conflict("unauthenticated", 401)
            current_identity = db.scalar(
                select(ExternalIdentity).where(
                    ExternalIdentity.issuer == pending.issuer,
                    ExternalIdentity.subject == pending.subject,
                )
            )
            if (identity is None) != (current_identity is None) or (
                identity is not None
                and current_identity is not None
                and identity.id != current_identity.id
            ):
                # A trusted identity link appeared after the User lock set was chosen. Retry safely.
                raise _conflict("identity_conflict")

        if bound_pending and user is None:
            assert pending is not None
            user = User(
                status="ACTIVE",
                email=pending.normalized_email if verified else None,
                normalized_email=pending.normalized_email if verified else None,
                email_verified_at=now if verified else None,
                display_name=pending.display_name,
            )
            db.add(user)
            db.flush()
            identity = ExternalIdentity(
                user_id=user.id,
                issuer=pending.issuer,
                subject=pending.subject,
            )
            db.add(identity)
            db.flush()
        assert user is not None and identity is not None
        existing_membership = db.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == organization_id,
                Membership.status != "REMOVED",
            )
        )
        if existing_membership is not None:
            raise _conflict("membership_conflict")

        membership = Membership(
            organization_id=organization_id,
            user_id=user.id,
            role=invitation.role,
            status="ACTIVE",
            last_activated_at=now,
        )
        db.add(membership)
        invitation.status = "ACCEPTED"
        invitation.accepted_at = now
        invitation.accepted_user_id = user.id
        attempt.consumed_at = now
        new_cookie: str | None = None
        expires_at: datetime | None = None
        if bound_pending:
            assert pending is not None
            pending.consumed_at = now
            new_cookie = new_secret()
            expires_at = now + timedelta(hours=12)
            db.add(
                ApplicationSession(
                    session_token_hash=digest_secret(new_cookie),
                    csrf_secret=secrets.token_bytes(32),
                    user_id=user.id,
                    external_identity_id=identity.id,
                    auth_email=email,
                    auth_email_verified=True,
                    absolute_expires_at=expires_at,
                )
            )
        db.commit()
        return organization_id, membership.id, invitation.role, new_cookie, expires_at
    except IntegrityError as exc:
        db.rollback()
        # A unique identity/email/membership collision is deliberately non-enumerating.
        raise _conflict("identity_conflict") from exc
    except Exception:
        db.rollback()
        raise
