import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.core.config import Settings
from app.db.session import database_utc_now
from app.modules.identity.dependencies import (
    BrowserState,
    clear_session_cookies,
    normal_cookie_name,
    pending_cookie_name,
    set_session_cookie,
)
from app.modules.identity.models import (
    ApplicationSession,
    ExternalIdentity,
    Invitation,
    InvitationAcceptanceAttempt,
    OIDCTransaction,
    PendingIdentitySession,
    User,
)
from app.modules.identity.oidc import OIDCAdapter, OIDCAdapterError, OIDCIdentity
from app.modules.identity.security import (
    digest_secret,
    new_secret,
    normalize_email,
)

logger = logging.getLogger("saurorja.auth")


def transaction_cookie_name(request: Request) -> str:
    return (
        "saurorja_oidc_binding"
        if request.app.state.settings.app_env == "development"
        else "__Host-saurorja_oidc_binding"
    )


class OIDCFailure(Exception):
    def __init__(self, category: str = "authentication_failed") -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class LoginTransactionData:
    id: Any
    nonce_hash: bytes | None
    pkce_verifier: str | None
    expected_issuer: str | None
    return_to: str | None
    invitation_id: Any


def safe_return_path(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if value.startswith("/api/") or value.startswith("/health"):
        return "/"
    return value[:500]


def transaction_cookie_settings(request: Request) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": request.app.state.settings.app_env != "development",
        "samesite": "lax",
        "path": "/",
        "max_age": 600,
    }


def require_oidc(request: Request) -> tuple[Settings, OIDCAdapter]:
    settings: Settings = request.app.state.settings
    oidc: OIDCAdapter | None = request.app.state.oidc
    if oidc is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "authentication_unavailable",
                "message": "Authentication is not configured",
            },
        )
    return settings, oidc


async def start_login(
    request: Request,
    db: Session,
    *,
    return_to: str | None = None,
    invitation_token: str | None = None,
) -> RedirectResponse:
    settings, oidc = require_oidc(request)
    invitation: Invitation | None = None
    if invitation_token is not None:
        candidate = db.scalar(
            select(Invitation).where(Invitation.token_hash == digest_secret(invitation_token))
        )
        if candidate is None or candidate.status != "PENDING":
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "invitation_unavailable",
                    "message": "This invitation is unavailable",
                },
            )
        if candidate.expires_at <= database_utc_now(db):
            candidate.status = "EXPIRED"
            db.commit()
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "invitation_unavailable",
                    "message": "This invitation is unavailable",
                },
            )
        invitation = candidate

    state = new_secret()
    nonce = new_secret()
    browser_binding = new_secret()
    verifier = new_secret(48)
    now = database_utc_now(db)
    tx = OIDCTransaction(
        kind="LOGIN",
        state_hash=digest_secret(state),
        nonce_hash=digest_secret(nonce),
        pkce_verifier=verifier,
        browser_binding_hash=digest_secret(browser_binding),
        expected_issuer=settings.oidc_issuer_url,
        return_to=safe_return_path(return_to or ("/invitations/accept" if invitation else "/")),
        invitation_id=invitation.id if invitation else None,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(tx)
    db.commit()

    try:
        authorization_url = await oidc.authorization_url(
            state=state,
            nonce=nonce,
            code_verifier=verifier,
        )
    except OIDCAdapterError:
        tx.consumed_at = now
        tx.nonce_hash = None
        tx.pkce_verifier = None
        tx.browser_binding_hash = None
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "authentication_unavailable",
                "message": "The identity provider is unavailable",
            },
        ) from None
    except Exception:
        tx.consumed_at = now
        tx.nonce_hash = None
        tx.pkce_verifier = None
        tx.browser_binding_hash = None
        db.commit()
        logger.warning("OIDC login initiation failed", extra={"reason": "provider_unavailable"})
        raise HTTPException(
            status_code=503,
            detail={
                "code": "authentication_unavailable",
                "message": "The identity provider is unavailable",
            },
        ) from None

    response = RedirectResponse(authorization_url, status_code=303)
    response.set_cookie(
        transaction_cookie_name(request), browser_binding, **transaction_cookie_settings(request)
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _consume_login_transaction(
    db: Session, request: Request, state: str | None
) -> LoginTransactionData:
    if not state:
        raise OIDCFailure()
    now = database_utc_now(db)
    tx = db.scalar(
        select(OIDCTransaction)
        .where(OIDCTransaction.kind == "LOGIN", OIDCTransaction.state_hash == digest_secret(state))
        .with_for_update()
    )
    binding = request.cookies.get(transaction_cookie_name(request), "")
    if (
        tx is None
        or tx.consumed_at is not None
        or tx.expires_at <= now
        or tx.expected_issuer != request.app.state.settings.oidc_issuer_url
        or not binding
        or not hmac.compare_digest(tx.browser_binding_hash or b"", digest_secret(binding))
    ):
        raise OIDCFailure()
    snapshot = LoginTransactionData(
        tx.id, tx.nonce_hash, tx.pkce_verifier, tx.expected_issuer, tx.return_to, tx.invitation_id
    )
    tx.consumed_at = now
    tx.nonce_hash = None
    tx.pkce_verifier = None
    tx.browser_binding_hash = None
    db.flush()
    db.commit()
    return snapshot


def consume_logout_transaction(db: Session, state: str | None) -> None:
    if not state:
        return
    transaction = db.scalar(
        select(OIDCTransaction)
        .where(
            OIDCTransaction.kind == "LOGOUT",
            OIDCTransaction.state_hash == digest_secret(state),
        )
        .with_for_update()
    )
    now = database_utc_now(db)
    if transaction is not None and transaction.consumed_at is None and transaction.expires_at > now:
        transaction.consumed_at = now
        db.commit()


def _validated_profile(
    identity: OIDCIdentity,
) -> tuple[str, str | None, str | None, bool, str | None]:
    verified = identity.email_verified
    normalized_email: str | None = None
    email: str | None = None
    if verified and identity.email:
        try:
            email, normalized_email = normalize_email(identity.email)
        except ValueError:
            verified = False
    display_name = identity.display_name
    if display_name:
        display_name = display_name.strip()[:200] or None
    return identity.subject, email, normalized_email, verified, display_name


def _revoke_browser_sessions(db: Session, request: Request, now: datetime) -> None:
    normal_value = request.cookies.get(normal_cookie_name(request))
    pending_value = request.cookies.get(pending_cookie_name(request))
    if normal_value:
        db.execute(
            update(ApplicationSession)
            .where(
                ApplicationSession.session_token_hash == digest_secret(normal_value),
                ApplicationSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revocation_reason="rotated")
        )
    if pending_value:
        db.execute(
            update(PendingIdentitySession)
            .where(
                PendingIdentitySession.token_hash == digest_secret(pending_value),
                PendingIdentitySession.consumed_at.is_(None),
                PendingIdentitySession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )


async def complete_login(request: Request, db: Session) -> Response:
    settings, oidc = require_oidc(request)
    query = request.query_params
    try:
        snapshot = _consume_login_transaction(db, request, query.get("state"))
        if query.get("error") or not query.get("code"):
            raise OIDCFailure()
        if snapshot.nonce_hash is None or snapshot.pkce_verifier is None:
            raise OIDCFailure()
        identity_claims = await oidc.authenticate_callback(
            code=query["code"],
            code_verifier=snapshot.pkce_verifier,
            expected_nonce_hash=snapshot.nonce_hash,
        )
        subject, email, normalized_email, email_verified, display_name = _validated_profile(
            identity_claims
        )
        return_to = safe_return_path(snapshot.return_to)
        acceptance_attempt_id = None
        with db.begin():
            now = database_utc_now(db)
            identity = db.scalar(
                select(ExternalIdentity)
                .where(
                    ExternalIdentity.issuer == settings.oidc_issuer_url,
                    ExternalIdentity.subject == subject,
                )
                .with_for_update()
            )
            _revoke_browser_sessions(db, request, now)
            if identity is None:
                session_secret = new_secret()
                pending = PendingIdentitySession(
                    token_hash=digest_secret(session_secret),
                    csrf_secret=secrets.token_bytes(32),
                    issuer=settings.oidc_issuer_url,
                    subject=subject,
                    display_name=display_name,
                    normalized_email=normalized_email if email_verified else None,
                    email_verified=email_verified,
                    absolute_expires_at=now + timedelta(minutes=10),
                )
                db.add(pending)
                db.flush()
                state_kind = "pending"
                user = None
                expires_at = pending.absolute_expires_at
                session_id = pending.id
            else:
                user = db.scalar(select(User).where(User.id == identity.user_id).with_for_update())
                if user is None or user.status != "ACTIVE":
                    raise OIDCFailure()
                user.display_name = display_name or user.display_name
                if email_verified and email and normalized_email:
                    collision = db.scalar(
                        select(User.id).where(
                            User.normalized_email == normalized_email, User.id != user.id
                        )
                    )
                    if collision is None:
                        user.email = email
                        user.normalized_email = normalized_email
                        user.email_verified_at = now
                identity.last_authenticated_at = now
                session_secret = new_secret()
                application_session = ApplicationSession(
                    session_token_hash=digest_secret(session_secret),
                    csrf_secret=secrets.token_bytes(32),
                    user_id=user.id,
                    external_identity_id=identity.id,
                    auth_email=email if email_verified else None,
                    auth_email_verified=email_verified,
                    absolute_expires_at=now + timedelta(hours=12),
                )
                db.add(application_session)
                db.flush()
                state_kind = "normal"
                expires_at = application_session.absolute_expires_at
                session_id = application_session.id
            if snapshot.invitation_id:
                invite = db.scalar(
                    select(Invitation)
                    .where(Invitation.id == snapshot.invitation_id)
                    .with_for_update()
                )
                if invite is not None and invite.status == "PENDING" and invite.expires_at > now:
                    attempt = InvitationAcceptanceAttempt(
                        invitation_id=invite.id,
                        application_session_id=session_id if state_kind == "normal" else None,
                        pending_identity_session_id=session_id if state_kind == "pending" else None,
                        expires_at=min(invite.expires_at, now + timedelta(minutes=10)),
                    )
                    db.add(attempt)
                    db.flush()
                    acceptance_attempt_id = attempt.id
        if acceptance_attempt_id is not None:
            return_to = f"/invitations/accept?attempt={acceptance_attempt_id}"
        response = RedirectResponse(url=return_to, status_code=303)
        clear_session_cookies(response, request)
        set_session_cookie(
            response,
            request,
            session_secret,
            pending=state_kind == "pending",
            expires_at=expires_at,
        )
        response.delete_cookie(
            transaction_cookie_name(request),
            path="/",
            secure=request.app.state.settings.app_env != "development",
            httponly=True,
            samesite="lax",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except OIDCFailure:
        db.rollback()
        logger.warning("OIDC callback rejected", extra={"reason": "validation_failed"})
    except Exception:
        db.rollback()
        logger.warning("OIDC callback rejected", extra={"reason": "provider_or_storage_failure"})
    response = RedirectResponse(url="/?auth=failed", status_code=303)
    response.delete_cookie(
        transaction_cookie_name(request),
        path="/",
        secure=request.app.state.settings.app_env != "development",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def begin_logout(request: Request, db: Session, state: BrowserState) -> Response:
    now = database_utc_now(db)
    if state.kind == "authenticated" and isinstance(state.session, ApplicationSession):
        state.session.revoked_at = now
        state.session.revocation_reason = "logout"
    elif state.kind == "pending_identity" and isinstance(state.session, PendingIdentitySession):
        state.session.revoked_at = now
    db.commit()
    response: RedirectResponse
    try:
        settings, oidc = require_oidc(request)
        state_value = new_secret()
        logout_url = await oidc.logout_url(state=state_value)
        if logout_url is not None:
            db.add(
                OIDCTransaction(
                    kind="LOGOUT",
                    state_hash=digest_secret(state_value),
                    return_to="/signed-out",
                    expected_issuer=settings.oidc_issuer_url,
                    expires_at=now + timedelta(minutes=10),
                )
            )
            db.commit()
            response = RedirectResponse(logout_url, status_code=303)
        else:
            response = RedirectResponse(url="/signed-out", status_code=303)
    except Exception:
        db.rollback()
        logger.info(
            "Upstream logout unavailable after local logout", extra={"outcome": "local_only"}
        )
        response = RedirectResponse(url="/signed-out", status_code=303)
    clear_session_cookies(response, request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
