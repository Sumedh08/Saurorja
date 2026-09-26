import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.db.session import SessionFactory, database_utc_now
from app.modules.identity.models import (
    ApplicationSession,
    ExternalIdentity,
    Invitation,
    Membership,
    Organization,
    PendingIdentitySession,
    User,
)
from app.modules.identity.security import digest_secret, encode_secret, new_secret

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Module 2 API tests require PostgreSQL"
)

COOKIE = "__Host-saurorja_session"
ORIGIN = "https://testserver"


def seed_user(
    *,
    email: str | None,
    role: str | None = None,
    organization: Organization | None = None,
) -> tuple[User, ExternalIdentity, str, bytes]:
    secret = new_secret()
    csrf = os.urandom(32)
    with SessionFactory.begin() as db:
        user = User(
            status="ACTIVE",
            email=email,
            normalized_email=email,
            email_verified_at=datetime.now(UTC) if email else None,
            display_name="Test User",
        )
        db.add(user)
        db.flush()
        identity = ExternalIdentity(
            user_id=user.id,
            issuer="https://issuer.test",
            subject=str(uuid4()),
        )
        db.add(identity)
        db.flush()
        if organization is not None and role is not None:
            db.add(
                Membership(
                    organization_id=organization.id,
                    user_id=user.id,
                    role=role,
                    status="ACTIVE",
                    last_activated_at=datetime.now(UTC),
                )
            )
        db.add(
            ApplicationSession(
                session_token_hash=digest_secret(secret),
                csrf_secret=csrf,
                user_id=user.id,
                external_identity_id=identity.id,
                auth_email=email,
                auth_email_verified=email is not None,
                absolute_expires_at=datetime.now(UTC) + timedelta(hours=12),
            )
        )
        db.flush()
        db.expunge(user)
        db.expunge(identity)
        return user, identity, secret, csrf


def seed_organization(name: str = "North") -> Organization:
    with SessionFactory.begin() as db:
        organization = Organization(name=name)
        db.add(organization)
        db.flush()
        db.expunge(organization)
        return organization


def seed_pending_identity(
    *, email: str | None, verified: bool = False, expires_at: datetime | None = None
) -> tuple[str, bytes]:
    secret = new_secret()
    csrf = os.urandom(32)
    with SessionFactory.begin() as db:
        db.add(
            PendingIdentitySession(
                token_hash=digest_secret(secret),
                csrf_secret=csrf,
                issuer="https://issuer.test",
                subject=str(uuid4()),
                normalized_email=email,
                email_verified=verified,
                absolute_expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=10),
            )
        )
    return secret, csrf


def auth_headers(secret: str, *, csrf: bytes | None = None) -> dict[str, str]:
    headers = {"Cookie": f"{COOKIE}={secret}"}
    if csrf is not None:
        headers["X-CSRF-Token"] = encode_secret(csrf)
    return headers


def origin_headers(secret: str, csrf: bytes | None = None) -> dict[str, str]:
    headers = auth_headers(secret, csrf=csrf)
    headers["Origin"] = ORIGIN
    return headers


def pending_headers(secret: str, csrf: bytes | None = None) -> dict[str, str]:
    headers = {"Cookie": f"__Host-saurorja_pending={secret}"}
    if csrf is not None:
        headers["X-CSRF-Token"] = encode_secret(csrf)
    return headers


def test_health_is_liveness_and_status_requires_application_session(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/me").status_code == 401


def test_session_profile_and_status_use_active_user(client: TestClient) -> None:
    user, _, secret, _ = seed_user(email="operator@example.com")
    headers = auth_headers(secret)
    assert client.get("/api/v1/auth/session", headers=headers).json() == {
        "state": "authenticated",
        "invitation_available": None,
    }
    profile = client.get("/api/v1/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json() == {
        "id": str(user.id),
        "display_name": "Test User",
        "email": "operator@example.com",
        "email_verified": True,
    }
    status = client.get("/api/v1/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["database"] == {"status": "connected"}
    assert status.json()["object_storage"] == {"status": "connected"}

    with SessionFactory.begin() as db:
        saved_user = db.get(User, user.id)
        assert saved_user is not None
        saved_user.status = "DISABLED"
    denied = client.get("/api/v1/me", headers=headers)
    assert denied.status_code == 401
    with SessionFactory() as db:
        session = db.scalar(
            select(ApplicationSession).where(
                ApplicationSession.session_token_hash == digest_secret(secret)
            )
        )
        assert session is not None and session.revoked_at is not None


def test_pending_identity_is_limited_to_admission_workflow_and_expires(
    client: TestClient,
) -> None:
    pending_secret, csrf = seed_pending_identity(email="not-yet-linked@example.com", verified=True)
    headers = pending_headers(pending_secret)
    assert client.get("/api/v1/auth/session", headers=headers).json() == {
        "state": "pending_identity",
        "invitation_available": False,
    }
    assert client.get("/api/v1/auth/pending", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/csrf", headers=headers).json() == {
        "csrf_token": encode_secret(csrf)
    }
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    assert client.get("/api/v1/organizations", headers=headers).status_code == 401

    expired_secret, _ = seed_pending_identity(
        email=None, expires_at=datetime.now(UTC) - timedelta(days=1)
    )
    expired = client.get("/api/v1/auth/session", headers=pending_headers(expired_secret))
    assert expired.json()["state"] == "anonymous"
    assert "__Host-saurorja_pending" in expired.headers.get("set-cookie", "")


def test_normal_session_idle_and_absolute_expiry_are_rejected(client: TestClient) -> None:
    _, _, idle_secret, _ = seed_user(email="idle@example.com")
    _, _, absolute_secret, _ = seed_user(email="absolute@example.com")
    with SessionFactory.begin() as db:
        idle = db.scalar(
            select(ApplicationSession).where(
                ApplicationSession.session_token_hash == digest_secret(idle_secret)
            )
        )
        absolute = db.scalar(
            select(ApplicationSession).where(
                ApplicationSession.session_token_hash == digest_secret(absolute_secret)
            )
        )
        assert idle is not None and absolute is not None
        database_now = database_utc_now(db)
        idle.last_seen_at = database_now - timedelta(hours=2, seconds=10)
        absolute.absolute_expires_at = database_now - timedelta(minutes=1)
    assert client.get("/api/v1/me", headers=auth_headers(idle_secret)).status_code == 401
    assert client.get("/api/v1/me", headers=auth_headers(absolute_secret)).status_code == 401


def test_local_logout_revokes_session_before_redirect(client: TestClient) -> None:
    _, _, secret, csrf = seed_user(email="logout@example.com")
    response = client.post(
        "/api/v1/auth/logout",
        data={"csrf_token": encode_secret(csrf)},
        headers={"Origin": ORIGIN, **auth_headers(secret)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/signed-out"
    with SessionFactory() as db:
        session = db.scalar(
            select(ApplicationSession).where(
                ApplicationSession.session_token_hash == digest_secret(secret)
            )
        )
        assert session is not None and session.revocation_reason == "logout"
    assert "__Host-saurorja_session" in response.headers.get("set-cookie", "")


def test_csrf_requires_session_secret_and_exact_origin(client: TestClient) -> None:
    owner = seed_organization()
    user, _, secret, csrf = seed_user(email="owner@example.com", role="OWNER", organization=owner)
    path = f"/api/v1/organizations/{owner.id}"
    assert client.patch(path, json={"name": "New"}, headers=auth_headers(secret)).status_code == 403
    bad_origin = origin_headers(secret, csrf)
    bad_origin["Origin"] = "https://attacker.test"
    assert client.patch(path, json={"name": "New"}, headers=bad_origin).status_code == 403
    bad_token = origin_headers(secret, os.urandom(32))
    assert client.patch(path, json={"name": "New"}, headers=bad_token).status_code == 403
    assert (
        client.patch(path, json={"name": "New"}, headers=origin_headers(secret, csrf)).status_code
        == 200
    )
    token_response = client.get("/api/v1/auth/csrf", headers=auth_headers(secret))
    assert token_response.headers["cache-control"] == "no-store"
    assert token_response.json()["csrf_token"] == encode_secret(csrf)
    assert (
        client.patch(
            path,
            json={"name": "No cookie"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": encode_secret(csrf)},
        ).status_code
        == 401
    )
    assert user.status == "ACTIVE"


def test_cross_organization_resources_are_not_visible(client: TestClient) -> None:
    org_a = seed_organization("A")
    org_b = seed_organization("B")
    user, _, owner_secret, owner_csrf = seed_user(
        email="owner@example.com", role="OWNER", organization=org_a
    )
    other, _, _, _ = seed_user(email="other@example.com", role="OWNER", organization=org_b)
    other_membership_id = None
    with SessionFactory() as db:
        other_membership_id = db.scalar(select(Membership.id).where(Membership.user_id == other.id))
    headers = auth_headers(owner_secret)
    assert client.get(f"/api/v1/organizations/{org_b.id}", headers=headers).status_code == 404
    response = client.patch(
        f"/api/v1/organizations/{org_a.id}/memberships/{other_membership_id}/role",
        json={"role": "MANAGER"},
        headers=origin_headers(owner_secret, owner_csrf),
    )
    assert response.status_code == 404
    assert user.status == "ACTIVE"


def test_owner_admin_api_and_verified_email_invitation_acceptance(client: TestClient) -> None:
    organization = seed_organization()
    owner, _, owner_secret, owner_csrf = seed_user(
        email="owner@example.com", role="OWNER", organization=organization
    )
    invitation_response = client.post(
        f"/api/v1/organizations/{organization.id}/invitations",
        json={"email": "invitee@example.com", "role": "VIEWER"},
        headers=origin_headers(owner_secret, owner_csrf),
    )
    assert invitation_response.status_code == 201, invitation_response.text
    invite_data = invitation_response.json()
    assert "#t=" in invite_data["invitation_url"]
    raw_token = urlsplit(invite_data["invitation_url"]).fragment.removeprefix("t=")
    with SessionFactory() as db:
        invitation = db.get(Invitation, invite_data["id"])
        assert invitation is not None
        assert invitation.token_hash == digest_secret(raw_token)
        assert raw_token.encode() not in invitation.token_hash

    invitee, _, invitee_secret, invitee_csrf = seed_user(email="invitee@example.com")
    attempt = client.post(
        "/api/v1/invitation-acceptance-attempts",
        json={"token": raw_token},
        headers=origin_headers(invitee_secret, invitee_csrf),
    )
    assert attempt.status_code == 201
    attempt_id = attempt.json()["id"]
    details = client.get(
        f"/api/v1/invitation-acceptance-attempts/{attempt_id}",
        headers=auth_headers(invitee_secret),
    )
    assert details.status_code == 200
    assert details.json()["organization"]["id"] == str(organization.id)
    accepted = client.post(
        f"/api/v1/invitation-acceptance-attempts/{attempt_id}/accept",
        json={"confirm": True},
        headers=origin_headers(invitee_secret, invitee_csrf),
    )
    assert accepted.status_code == 201
    assert accepted.json()["role"] == "VIEWER"
    replay = client.post(
        f"/api/v1/invitation-acceptance-attempts/{attempt_id}/accept",
        json={"confirm": True},
        headers=origin_headers(invitee_secret, invitee_csrf),
    )
    assert replay.status_code in {404, 409}
    with SessionFactory() as db:
        invitee_membership = db.scalar(
            select(Membership).where(
                Membership.user_id == invitee.id,
                Membership.organization_id == organization.id,
            )
        )
        saved_invite = db.get(Invitation, invite_data["id"])
        assert invitee_membership is not None and invitee_membership.status == "ACTIVE"
        assert saved_invite is not None and saved_invite.status == "ACCEPTED"


def test_invitation_revocation_reports_only_real_revocation_and_uses_database_time(
    client: TestClient,
) -> None:
    organization = seed_organization()
    _, _, owner_secret, owner_csrf = seed_user(
        email="revoke-owner@example.com", role="OWNER", organization=organization
    )
    headers = origin_headers(owner_secret, owner_csrf)

    expired_response = client.post(
        f"/api/v1/organizations/{organization.id}/invitations",
        json={"email": "expired@example.com", "role": "VIEWER"},
        headers=headers,
    )
    assert expired_response.status_code == 201
    expired_id = expired_response.json()["id"]
    with SessionFactory.begin() as db:
        expired = db.get(Invitation, expired_id)
        assert expired is not None
        assert abs((expired.expires_at - expired.created_at).total_seconds() - 7 * 86400) < 2
        expired.expires_at = func.clock_timestamp() - text("INTERVAL '1 second'")
    expired_revoke = client.post(
        f"/api/v1/organizations/{organization.id}/invitations/{expired_id}/revoke",
        headers=headers,
    )
    assert expired_revoke.status_code == 409
    assert expired_revoke.json()["error"]["code"] == "invitation_unavailable"
    with SessionFactory() as db:
        expired = db.get(Invitation, expired_id)
        assert expired is not None and expired.status == "EXPIRED"

    accepted_response = client.post(
        f"/api/v1/organizations/{organization.id}/invitations",
        json={"email": "accepted@example.com", "role": "VIEWER"},
        headers=headers,
    )
    assert accepted_response.status_code == 201
    accepted_id = accepted_response.json()["id"]
    with SessionFactory.begin() as db:
        accepted = db.get(Invitation, accepted_id)
        assert accepted is not None
        accepted.status = "ACCEPTED"
    accepted_revoke = client.post(
        f"/api/v1/organizations/{organization.id}/invitations/{accepted_id}/revoke",
        headers=headers,
    )
    assert accepted_revoke.status_code == 409
    assert accepted_revoke.json()["error"]["code"] == "invitation_unavailable"

    pending_response = client.post(
        f"/api/v1/organizations/{organization.id}/invitations",
        json={"email": "pending@example.com", "role": "VIEWER"},
        headers=headers,
    )
    assert pending_response.status_code == 201
    pending_id = pending_response.json()["id"]
    first_revoke = client.post(
        f"/api/v1/organizations/{organization.id}/invitations/{pending_id}/revoke",
        headers=headers,
    )
    repeated_revoke = client.post(
        f"/api/v1/organizations/{organization.id}/invitations/{pending_id}/revoke",
        headers=headers,
    )
    assert first_revoke.status_code == repeated_revoke.status_code == 200
    assert first_revoke.json()["status"] == repeated_revoke.json()["status"] == "REVOKED"


def test_admin_cannot_grant_owner_and_last_owner_is_protected(client: TestClient) -> None:
    organization = seed_organization()
    owner, _, owner_secret, owner_csrf = seed_user(
        email="owner@example.com", role="OWNER", organization=organization
    )
    admin, _, admin_secret, admin_csrf = seed_user(
        email="admin@example.com", role="ADMIN", organization=organization
    )
    viewer, _, _, _ = seed_user(
        email="viewer@example.com", role="VIEWER", organization=organization
    )
    viewer_membership_id = None
    admin_membership_id = None
    owner_membership_id = None
    with SessionFactory() as db:
        admin_membership_id = db.scalar(select(Membership.id).where(Membership.user_id == admin.id))
        viewer_membership_id = db.scalar(
            select(Membership.id).where(Membership.user_id == viewer.id)
        )
        owner_membership_id = db.scalar(select(Membership.id).where(Membership.user_id == owner.id))
    denied = client.patch(
        f"/api/v1/organizations/{organization.id}/memberships/{viewer_membership_id}/role",
        json={"role": "OWNER"},
        headers=origin_headers(admin_secret, admin_csrf),
    )
    assert denied.status_code == 403
    self_promotion = client.patch(
        f"/api/v1/organizations/{organization.id}/memberships/{admin_membership_id}/role",
        json={"role": "OWNER"},
        headers=origin_headers(admin_secret, admin_csrf),
    )
    assert self_promotion.status_code == 403
    denied_owner = client.post(
        f"/api/v1/organizations/{organization.id}/memberships/{owner_membership_id}/suspend",
        headers=origin_headers(admin_secret, admin_csrf),
    )
    assert denied_owner.status_code == 403
    last_owner = client.patch(
        f"/api/v1/organizations/{organization.id}/memberships/{owner_membership_id}/role",
        json={"role": "ADMIN"},
        headers=origin_headers(owner_secret, owner_csrf),
    )
    assert last_owner.status_code == 409


@pytest.mark.parametrize(
    ("role", "can_view_directory", "can_view_invitations", "can_manage"),
    [
        ("OWNER", True, True, True),
        ("ADMIN", True, True, True),
        ("MANAGER", True, False, False),
        ("TECHNICIAN", False, False, False),
        ("VIEWER", False, False, False),
    ],
)
def test_organization_role_matrix(
    client: TestClient,
    role: str,
    can_view_directory: bool,
    can_view_invitations: bool,
    can_manage: bool,
) -> None:
    organization = seed_organization(f"Role matrix {role}")
    if role == "OWNER":
        actor, _, actor_secret, actor_csrf = seed_user(
            email="role-owner@example.com", role=role, organization=organization
        )
    else:
        seed_user(
            email=f"owner-for-{role.lower()}@example.com", role="OWNER", organization=organization
        )
        actor, _, actor_secret, actor_csrf = seed_user(
            email=f"actor-{role.lower()}@example.com", role=role, organization=organization
        )
    target, _, _, _ = seed_user(
        email=f"target-{role.lower()}@example.com", role="VIEWER", organization=organization
    )
    with SessionFactory() as db:
        target_membership_id = db.scalar(
            select(Membership.id).where(Membership.user_id == target.id)
        )
    headers = auth_headers(actor_secret)
    assert (
        client.get(f"/api/v1/organizations/{organization.id}", headers=headers).status_code == 200
    )
    own = client.get(f"/api/v1/organizations/{organization.id}/memberships/me", headers=headers)
    assert own.status_code == 200 and own.json()["role"] == role

    directory = client.get(f"/api/v1/organizations/{organization.id}/memberships", headers=headers)
    assert directory.status_code == (200 if can_view_directory else 403)
    if role == "MANAGER":
        assert all(member["email"] is None for member in directory.json()["items"])
    invitations = client.get(
        f"/api/v1/organizations/{organization.id}/invitations", headers=headers
    )
    assert invitations.status_code == (200 if can_view_invitations else 403)

    rename = client.patch(
        f"/api/v1/organizations/{organization.id}",
        json={"name": f"Renamed by {role}"},
        headers=origin_headers(actor_secret, actor_csrf),
    )
    assert rename.status_code == (200 if can_manage else 403)
    role_change = client.patch(
        f"/api/v1/organizations/{organization.id}/memberships/{target_membership_id}/role",
        json={"role": "MANAGER"},
        headers=origin_headers(actor_secret, actor_csrf),
    )
    assert role_change.status_code == (200 if can_manage else 403)
