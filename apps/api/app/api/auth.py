from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.db.session import get_db_session
from app.modules.identity.auth_service import (
    begin_logout,
    complete_login,
    consume_logout_transaction,
    start_login,
)
from app.modules.identity.dependencies import BrowserState, get_browser_state, require_session_state
from app.modules.identity.invitations import (
    accept_attempt,
    create_attempt,
    inspect_attempt_preview,
    pending_invitation_preview,
)
from app.modules.identity.schemas import (
    AcceptInvitationResponse,
    AttemptCreated,
    AuthSessionResponse,
    CsrfResponse,
    InvitationAttemptView,
    InvitationTokenRequest,
    PendingView,
)
from app.modules.identity.security import encode_secret

router = APIRouter(prefix="/auth", tags=["auth"])
admission_router = APIRouter(tags=["invitation acceptance"])


def _login_token(body: bytes, content_type: str) -> str | None:
    if "application/json" in content_type:
        import json

        try:
            data: Any = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            return None
        return (
            data.get("token")
            if isinstance(data, dict) and isinstance(data.get("token"), str)
            else None
        )
    try:
        data = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return None
    values = data.get("token")
    return values[0] if values else None


@router.get("/login", include_in_schema=False)
async def login(
    request: Request,
    return_to: str | None = None,
    db: Session = Depends(get_db_session),
) -> Response:
    return await start_login(request, db, return_to=return_to)


@router.post("/login", include_in_schema=False)
async def invite_login(request: Request, db: Session = Depends(get_db_session)) -> Response:
    token = _login_token(await request.body(), request.headers.get("content-type", ""))
    if not token:
        raise HTTPException(
            status_code=404,
            detail={"code": "invitation_unavailable", "message": "This invitation is unavailable"},
        )
    return await start_login(request, db, invitation_token=token, return_to="/invitations/accept")


@router.get("/callback", include_in_schema=False)
async def callback(request: Request, db: Session = Depends(get_db_session)) -> Response:
    return await complete_login(request, db)


@router.get("/session", response_model=AuthSessionResponse)
def session_state(state: BrowserState = Depends(get_browser_state)) -> AuthSessionResponse:
    if state.kind == "pending_identity":
        return AuthSessionResponse(state="pending_identity", invitation_available=False)
    return AuthSessionResponse(state=state.kind)


@router.get("/csrf", response_model=CsrfResponse)
def csrf(state: BrowserState = Depends(require_session_state)) -> Response:
    assert state.session is not None
    token = encode_secret(state.session.csrf_secret)
    response = Response(
        content=CsrfResponse(csrf_token=token).model_dump_json(),
        media_type="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.post("/logout", include_in_schema=False)
async def logout(
    request: Request,
    state: BrowserState = Depends(get_browser_state),
    db: Session = Depends(get_db_session),
) -> Response:
    return await begin_logout(request, db, state)


@router.get("/logout/callback", include_in_schema=False)
def logout_callback(request: Request, db: Session = Depends(get_db_session)) -> Response:
    consume_logout_transaction(db, request.query_params.get("state"))
    response = RedirectResponse(url="/signed-out", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/pending", response_model=PendingView)
def pending(
    state: BrowserState = Depends(require_session_state),
    db: Session = Depends(get_db_session),
) -> PendingView:
    preview = pending_invitation_preview(db, state)
    if preview is None:
        return PendingView(invitation=None)
    return PendingView(
        invitation={
            "attempt_id": preview.attempt_id,
            "organization_name": preview.organization_name,
            "email": preview.email,
            "role": preview.role,
            "expires_at": preview.expires_at.isoformat(),
        }
    )


@admission_router.post(
    "/invitation-acceptance-attempts", response_model=AttemptCreated, status_code=201
)
def make_acceptance_attempt(
    payload: InvitationTokenRequest,
    state: BrowserState = Depends(require_session_state),
    db: Session = Depends(get_db_session),
) -> AttemptCreated:
    attempt = create_attempt(db, state, payload.token)
    return AttemptCreated(id=attempt.id, expires_at=attempt.expires_at)


@admission_router.get(
    "/invitation-acceptance-attempts/{attempt_id}", response_model=InvitationAttemptView
)
def get_acceptance_attempt(
    attempt_id: UUID,
    state: BrowserState = Depends(require_session_state),
    db: Session = Depends(get_db_session),
) -> InvitationAttemptView:
    preview = inspect_attempt_preview(db, state, attempt_id)
    return InvitationAttemptView(
        id=preview.attempt.id,
        organization={"id": preview.organization.id, "name": preview.organization.name},
        email=preview.invitation.email,
        role=preview.invitation.role,
        expires_at=preview.attempt.expires_at,
    )


@admission_router.post(
    "/invitation-acceptance-attempts/{attempt_id}/accept",
    response_model=AcceptInvitationResponse,
    status_code=201,
)
def accept_invitation(
    attempt_id: UUID,
    request: Request,
    response: Response,
    state: BrowserState = Depends(require_session_state),
    db: Session = Depends(get_db_session),
) -> AcceptInvitationResponse:
    organization_id, membership_id, role, new_cookie, expires_at = accept_attempt(
        db, state, attempt_id
    )
    from app.modules.identity.dependencies import clear_session_cookies, set_session_cookie

    if new_cookie:
        clear_session_cookies(response, request)
        set_session_cookie(response, request, new_cookie, expires_at=expires_at)
    return AcceptInvitationResponse(
        organization_id=organization_id, membership_id=membership_id, role=role
    )
