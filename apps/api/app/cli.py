import argparse
import getpass
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.modules.identity.models import (
    ApplicationSession,
    InvitationAcceptanceAttempt,
    OIDCTransaction,
    PendingIdentitySession,
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


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a UUID") from exc


def _subject(prompt: str = "Exact OIDC subject: ") -> str:
    if sys.stdin.isatty():
        return getpass.getpass(prompt).strip()
    return sys.stdin.readline().strip()


def _confirm(expected: str, provided: str | UUID | None, prompt: str) -> None:
    if sys.stdin.isatty():
        entered = input(f"{prompt} Type {expected} to confirm: ").strip()
    else:
        entered = str(provided) if provided is not None else ""
    if entered != expected:
        raise OperatorConflict("confirmation did not match the requested target")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="saurorja")
    commands = root.add_subparsers(dest="group", required=True)
    auth = commands.add_parser("auth", help="trusted operator identity and access workflows")
    auth_commands = auth.add_subparsers(dest="command", required=True)

    bootstrap = auth_commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--organization-name", required=True)
    bootstrap.add_argument("--issuer", required=True)
    bootstrap.add_argument("--operator", required=True)
    bootstrap.add_argument("--reason", required=True)
    bootstrap.add_argument("--operation-id", type=_uuid, required=True)

    provision = auth_commands.add_parser("provision-organization")
    provision.add_argument("--name", required=True)
    provision.add_argument("--user-id", type=_uuid, required=True)
    provision.add_argument("--operator", required=True)
    provision.add_argument("--reason", required=True)
    provision.add_argument("--operation-id", type=_uuid, required=True)

    disable = auth_commands.add_parser("disable-user")
    disable.add_argument("--user-id", type=_uuid, required=True)
    disable.add_argument("--successor", action="append", default=[], metavar="ORG_UUID=USER_UUID")
    disable.add_argument("--operator", required=True)
    disable.add_argument("--reason", required=True)
    disable.add_argument("--operation-id", type=_uuid, required=True)
    disable.add_argument("--confirm-user", type=_uuid)

    enable = auth_commands.add_parser("enable-user")
    enable.add_argument("--user-id", type=_uuid, required=True)
    enable.add_argument("--operator", required=True)
    enable.add_argument("--reason", required=True)
    enable.add_argument("--operation-id", type=_uuid, required=True)

    link = auth_commands.add_parser("link-identity")
    link.add_argument("--user-id", type=_uuid, required=True)
    link.add_argument("--issuer", required=True)
    link.add_argument("--operator", required=True)
    link.add_argument("--reason", required=True)
    link.add_argument("--operation-id", type=_uuid, required=True)
    link.add_argument("--confirm-user", type=_uuid)

    recover = auth_commands.add_parser("recover-owner")
    recover.add_argument("--organization-id", type=_uuid, required=True)
    recover.add_argument("--issuer", required=True)
    recover.add_argument("--operator", required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument("--operation-id", type=_uuid, required=True)
    recover.add_argument("--confirm-organization", type=_uuid)

    prune = auth_commands.add_parser("prune")
    prune.add_argument("--limit", type=int, default=1000, choices=range(1, 5001))
    return root


def _successors(raw_values: list[str]) -> dict[UUID, UUID]:
    result: dict[UUID, UUID] = {}
    for value in raw_values:
        try:
            org_text, user_text = value.split("=", 1)
            organization_id, user_id = UUID(org_text), UUID(user_text)
        except ValueError as exc:
            raise OperatorConflict("successors must use ORG_UUID=USER_UUID") from exc
        if organization_id in result:
            raise OperatorConflict("duplicate Organization successor mapping")
        result[organization_id] = user_id
    return result


def _prune(db: Session, limit: int) -> dict[str, int]:
    now = datetime.now(UTC)
    plan: list[tuple[Any, ColumnElement[bool]]] = [
        (
            ApplicationSession,
            (ApplicationSession.revoked_at < now - timedelta(days=30))
            | (ApplicationSession.absolute_expires_at < now - timedelta(days=30))
            | (ApplicationSession.last_seen_at < now - timedelta(days=30, hours=2)),
        ),
        (
            PendingIdentitySession,
            (PendingIdentitySession.consumed_at < now - timedelta(hours=24))
            | (PendingIdentitySession.revoked_at < now - timedelta(hours=24))
            | (PendingIdentitySession.absolute_expires_at < now - timedelta(hours=24)),
        ),
        (
            OIDCTransaction,
            (OIDCTransaction.consumed_at < now - timedelta(hours=24))
            | (OIDCTransaction.expires_at < now - timedelta(hours=24)),
        ),
        (
            InvitationAcceptanceAttempt,
            (InvitationAcceptanceAttempt.consumed_at < now - timedelta(hours=24))
            | (InvitationAcceptanceAttempt.expires_at < now - timedelta(hours=24)),
        ),
    ]
    counts: dict[str, int] = {}
    for model, predicate in plan:
        ids: list[UUID] = list(
            db.scalars(select(model.id).where(predicate).order_by(model.id).limit(limit)).all()
        )
        if ids:
            db.execute(delete(model).where(model.id.in_(ids)))
            counts[model.__tablename__] = len(ids)
        else:
            counts[model.__tablename__] = 0
    return counts


def run(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = get_settings()
    try:
        with SessionFactory() as db, db.begin():
            if args.command == "bootstrap-admin":
                subject = _subject()
                bootstrap_result = bootstrap_admin(
                    db,
                    settings,
                    organization_name=args.organization_name,
                    issuer=args.issuer,
                    subject=subject,
                    operator_label=args.operator,
                    reason=args.reason,
                    operation_id=args.operation_id,
                )
                print(
                    f"Bootstrap {bootstrap_result['result']}; Organization "
                    f"{bootstrap_result['organization_id']}; User {bootstrap_result['user_id']}"
                )
            elif args.command == "provision-organization":
                organization_id = provision_organization(
                    db,
                    settings,
                    name=args.name,
                    user_id=args.user_id,
                    operator_label=args.operator,
                    reason=args.reason,
                    operation_id=args.operation_id,
                )
                print(f"Organization provisioned: {organization_id}")
            elif args.command == "disable-user":
                _confirm(str(args.user_id), args.confirm_user, "Disable this Saurorja User.")
                affected = disable_user(
                    db,
                    user_id=args.user_id,
                    successors=_successors(args.successor),
                    operator_label=args.operator,
                    reason=args.reason,
                    operation_id=args.operation_id,
                )
                print(f"User disabled; affected Organizations: {len(affected)}")
            elif args.command == "enable-user":
                enable_user(
                    db,
                    user_id=args.user_id,
                    operator_label=args.operator,
                    reason=args.reason,
                    operation_id=args.operation_id,
                )
                print(f"User enabled: {args.user_id}")
            elif args.command == "link-identity":
                _confirm(
                    str(args.user_id), args.confirm_user, "Link an OIDC identity to this User."
                )
                identity_id = link_identity(
                    db,
                    settings=settings,
                    user_id=args.user_id,
                    issuer=args.issuer,
                    subject=_subject(),
                    operator_label=args.operator,
                    reason=args.reason,
                    operation_id=args.operation_id,
                )
                print(f"Identity linked: {identity_id}")
            elif args.command == "recover-owner":
                _confirm(
                    str(args.organization_id),
                    args.confirm_organization,
                    "Emergency Owner recovery.",
                )
                recovery_result = recover_owner(
                    db,
                    settings=settings,
                    organization_id=args.organization_id,
                    issuer=args.issuer,
                    subject=_subject(),
                    operator_label=args.operator,
                    reason=args.reason,
                    operation_id=args.operation_id,
                )
                print(
                    f"Owner recovery {recovery_result['result']}; Organization "
                    f"{recovery_result['organization_id']}; User {recovery_result['user_id']}"
                )
            elif args.command == "prune":
                prune_counts = _prune(db, args.limit)
                print(prune_counts)
        return 0
    except (OperatorConflict, ValueError, IntegrityError) as exc:
        print(f"Operator command failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
