from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_users_status"),
        CheckConstraint(
            "(email_verified_at IS NULL AND normalized_email IS NULL) OR "
            "(email_verified_at IS NOT NULL AND normalized_email IS NOT NULL)",
            name="ck_users_verified_email_pair",
        ),
        Index(
            "uq_users_normalized_verified_email",
            "normalized_email",
            unique=True,
            postgresql_where=text("email_verified_at IS NOT NULL AND normalized_email IS NOT NULL"),
        ),
        Index("ix_users_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    email: Mapped[str | None] = mapped_column(String(320))
    normalized_email: Mapped[str | None] = mapped_column(String(320))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_external_identity_issuer_subject"),
        CheckConstraint(
            "length(issuer) > 0 AND length(subject) > 0", name="ck_external_identity_nonempty"
        ),
        Index("ix_external_identities_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_organizations_name_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('OWNER','ADMIN','MANAGER','TECHNICIAN','VIEWER')", name="ck_memberships_role"
        ),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','REMOVED')", name="ck_memberships_status"),
        Index(
            "uq_memberships_user_organization_not_removed",
            "user_id",
            "organization_id",
            unique=True,
            postgresql_where=text("status <> 'REMOVED'"),
        ),
        Index("ix_memberships_organization_status_role", "organization_id", "status", "role"),
        Index("ix_memberships_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN','MANAGER','TECHNICIAN','VIEWER')", name="ck_invitations_role"
        ),
        CheckConstraint(
            "status IN ('PENDING','ACCEPTED','REVOKED','EXPIRED')", name="ck_invitations_status"
        ),
        UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        Index(
            "uq_invitations_pending_org_email",
            "organization_id",
            "normalized_email",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_invitations_organization_status_created", "organization_id", "status", "created_at"
        ),
        Index("ix_invitations_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    creator_membership_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="RESTRICT"), nullable=False
    )
    accepted_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationSession(Base):
    __tablename__ = "application_sessions"
    __table_args__ = (
        UniqueConstraint("session_token_hash", name="uq_application_sessions_token_hash"),
        CheckConstraint(
            "octet_length(session_token_hash) = 32", name="ck_application_sessions_hash_length"
        ),
        CheckConstraint(
            "octet_length(csrf_secret) = 32", name="ck_application_sessions_csrf_length"
        ),
        Index("ix_application_sessions_user_revoked", "user_id", "revoked_at"),
        Index("ix_application_sessions_absolute_expires", "absolute_expires_at"),
        Index("ix_application_sessions_last_seen", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    csrf_secret: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    external_identity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("external_identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    auth_email: Mapped[str | None] = mapped_column(String(320))
    auth_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(32))


class PendingIdentitySession(Base):
    __tablename__ = "pending_identity_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_pending_identity_sessions_token_hash"),
        CheckConstraint(
            "octet_length(token_hash) = 32", name="ck_pending_identity_sessions_hash_length"
        ),
        CheckConstraint(
            "octet_length(csrf_secret) = 32", name="ck_pending_identity_sessions_csrf_length"
        ),
        Index(
            "ix_pending_identity_sessions_identity_expiry",
            "issuer",
            "subject",
            "absolute_expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    csrf_secret: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    normalized_email: Mapped[str | None] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OIDCTransaction(Base):
    __tablename__ = "oidc_transactions"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_oidc_transactions_state_hash"),
        CheckConstraint("kind IN ('LOGIN','LOGOUT')", name="ck_oidc_transactions_kind"),
        CheckConstraint(
            "(kind = 'LOGIN' AND ((consumed_at IS NULL AND nonce_hash IS NOT NULL "
            "AND pkce_verifier IS NOT NULL AND browser_binding_hash IS NOT NULL "
            "AND expected_issuer IS NOT NULL) OR (consumed_at IS NOT NULL "
            "AND nonce_hash IS NULL AND pkce_verifier IS NULL "
            "AND browser_binding_hash IS NULL))) OR "
            "(kind = 'LOGOUT' AND nonce_hash IS NULL AND pkce_verifier IS NULL "
            "AND browser_binding_hash IS NULL)",
            name="ck_oidc_transactions_kind_fields",
        ),
        Index("ix_oidc_transactions_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    state_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    nonce_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    pkce_verifier: Mapped[str | None] = mapped_column(Text)
    browser_binding_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    expected_issuer: Mapped[str | None] = mapped_column(Text)
    return_to: Mapped[str | None] = mapped_column(String(500))
    invitation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invitations.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvitationAcceptanceAttempt(Base):
    __tablename__ = "invitation_acceptance_attempts"
    __table_args__ = (
        CheckConstraint(
            "(application_session_id IS NULL) <> (pending_identity_session_id IS NULL)",
            name="ck_invitation_attempt_exact_session",
        ),
        Index("ix_invitation_attempt_invitation_consumed", "invitation_id", "consumed_at"),
        Index("ix_invitation_attempt_application_session", "application_session_id"),
        Index("ix_invitation_attempt_pending_session", "pending_identity_session_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    invitation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invitations.id", ondelete="RESTRICT"), nullable=False
    )
    application_session_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("application_sessions.id", ondelete="CASCADE")
    )
    pending_identity_session_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pending_identity_sessions.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InstallationState(Base):
    __tablename__ = "installation_state"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="ck_installation_state_singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    initial_organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    initial_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    initial_external_identity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("external_identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_name: Mapped[str] = mapped_column(String(200), nullable=False)
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OperatorAction(Base):
    __tablename__ = "operator_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('BOOTSTRAPPED','ORGANIZATION_PROVISIONED','USER_DISABLED',"
            "'USER_ENABLED','IDENTITY_LINKED','OWNER_SUCCESSION','OWNER_RECOVERED')",
            name="ck_operator_actions_type",
        ),
        UniqueConstraint("operation_id", name="uq_operator_actions_operation_id"),
        CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_operator_actions_details_object"
        ),
        Index("ix_operator_actions_type_created", "action_type", "created_at"),
        Index("ix_operator_actions_target_user", "target_user_id"),
        Index("ix_operator_actions_target_organization", "target_organization_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_label: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    target_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    target_organization_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
