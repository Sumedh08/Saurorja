from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, Request, Response
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.modules.identity.models import (
    ApplicationSession,
    ExternalIdentity,
    PendingIdentitySession,
    User,
)
from app.modules.identity.security import digest_secret


@dataclass(frozen=True)
class BrowserState:
    kind: Literal["anonymous", "authenticated", "pending_identity"]
    session: ApplicationSession | PendingIdentitySession | None = None
    user: User | None = None


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user: User
    session: ApplicationSession
    external_identity: ExternalIdentity


def normal_cookie_name(request: Request) -> str:
    return (
        "saurorja_session"
        if request.app.state.settings.app_env == "development"
        else "__Host-saurorja_session"
    )


def pending_cookie_name(request: Request) -> str:
    return (
        "saurorja_pending"
        if request.app.state.settings.app_env == "development"
        else "__Host-saurorja_pending"
    )


def cookie_secure(request: Request) -> bool:
    return bool(request.app.state.settings.app_env != "development")


def set_session_cookie(
    response: Response,
    request: Request,
    secret: str,
    *,
    pending: bool = False,
    expires_at: datetime | None = None,
) -> None:
    name = pending_cookie_name(request) if pending else normal_cookie_name(request)
    max_age = None
    if expires_at is not None:
        max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        name,
        secret,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        path="/",
        max_age=max_age,
    )


def clear_session_cookies(response: Response, request: Request) -> None:
    response.delete_cookie(
        normal_cookie_name(request),
        path="/",
        secure=cookie_secure(request),
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        pending_cookie_name(request),
        path="/",
        secure=cookie_secure(request),
        httponly=True,
        samesite="lax",
    )


def get_browser_state(
    request: Request,
    response: Response,
    db: Session = Depends(get_db_session),
) -> BrowserState:
    normal_value = request.cookies.get(normal_cookie_name(request))
    pending_value = request.cookies.get(pending_cookie_name(request))
    if normal_value and pending_value:
        clear_session_cookies(response, request)
        return BrowserState("anonymous")
    if normal_value:
        update_result = db.execute(
            update(ApplicationSession)
            .where(
                ApplicationSession.session_token_hash == digest_secret(normal_value),
                ApplicationSession.revoked_at.is_(None),
                ApplicationSession.absolute_expires_at > func.clock_timestamp(),
                ApplicationSession.last_seen_at
                > func.clock_timestamp() - text("INTERVAL '2 hours'"),
                select(User.id)
                .where(
                    User.id == ApplicationSession.user_id,
                    User.status == "ACTIVE",
                )
                .exists(),
            )
            .values(last_seen_at=func.clock_timestamp())
            .returning(ApplicationSession.id)
        )
        normal_session_id = update_result.scalar_one_or_none()
        if normal_session_id is None:
            disabled_session_id = db.scalar(
                select(ApplicationSession.id)
                .join(User, User.id == ApplicationSession.user_id)
                .where(
                    ApplicationSession.session_token_hash == digest_secret(normal_value),
                    ApplicationSession.revoked_at.is_(None),
                    User.status == "DISABLED",
                )
            )
            if disabled_session_id is not None:
                db.execute(
                    update(ApplicationSession)
                    .where(
                        ApplicationSession.id == disabled_session_id,
                        ApplicationSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=func.clock_timestamp(), revocation_reason="user_disabled")
                )
            db.commit()
            clear_session_cookies(response, request)
            return BrowserState("anonymous")
        normal_session = db.get(ApplicationSession, normal_session_id)
        assert normal_session is not None
        user = db.get(User, normal_session.user_id)
        if user is None or user.status != "ACTIVE":
            db.rollback()
            clear_session_cookies(response, request)
            return BrowserState("anonymous")
        db.commit()
        return BrowserState("authenticated", normal_session, user)
    if pending_value:
        pending_session = db.scalar(
            select(PendingIdentitySession).where(
                PendingIdentitySession.token_hash == digest_secret(pending_value),
                PendingIdentitySession.consumed_at.is_(None),
                PendingIdentitySession.revoked_at.is_(None),
                PendingIdentitySession.absolute_expires_at > func.clock_timestamp(),
            )
        )
        if pending_session is None:
            clear_session_cookies(response, request)
            return BrowserState("anonymous")
        assert pending_session is not None
        linked_user_id = db.scalar(
            select(ExternalIdentity.user_id).where(
                ExternalIdentity.issuer == pending_session.issuer,
                ExternalIdentity.subject == pending_session.subject,
            )
        )
        linked_user = db.get(User, linked_user_id) if linked_user_id is not None else None
        if linked_user is not None and linked_user.status == "DISABLED":
            db.execute(
                update(PendingIdentitySession)
                .where(PendingIdentitySession.id == pending_session.id)
                .values(revoked_at=func.clock_timestamp())
            )
            db.commit()
            clear_session_cookies(response, request)
            return BrowserState("anonymous")
        return BrowserState("pending_identity", pending_session, linked_user)
    return BrowserState("anonymous")


def require_principal(
    state: BrowserState = Depends(get_browser_state),
    db: Session = Depends(get_db_session),
) -> AuthenticatedPrincipal:
    from fastapi import HTTPException

    if (
        state.kind != "authenticated"
        or state.user is None
        or not isinstance(state.session, ApplicationSession)
    ):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Authentication is required"},
        )
    identity = db.get(ExternalIdentity, state.session.external_identity_id)
    if identity is None or identity.user_id != state.user.id:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_session", "message": "Authentication is required"},
        )
    return AuthenticatedPrincipal(state.user, state.session, identity)


def require_user(principal: AuthenticatedPrincipal = Depends(require_principal)) -> User:
    return principal.user


def require_session_state(state: BrowserState = Depends(get_browser_state)) -> BrowserState:
    from fastapi import HTTPException

    if state.kind == "anonymous" or state.session is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthenticated", "message": "Authentication is required"},
        )
    return state
