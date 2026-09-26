import hashlib
import hmac
import re
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime

from email_validator import EmailNotValidError, validate_email

CSRF_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_secret(size: int = 32) -> str:
    return urlsafe_b64encode(secrets.token_bytes(size)).rstrip(b"=").decode("ascii")


def digest_secret(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def digest_bytes(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def encode_secret(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_secret(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def csrf_token_matches(expected: bytes, supplied: str) -> bool:
    if not CSRF_TOKEN_PATTERN.fullmatch(supplied):
        return False
    try:
        decoded = decode_secret(supplied)
    except (ValueError, TypeError):
        return False
    return len(decoded) == 32 and hmac.compare_digest(expected, decoded)


def normalize_email(value: str) -> tuple[str, str]:
    try:
        validated = validate_email(value, check_deliverability=False, allow_smtputf8=False)
    except EmailNotValidError as exc:
        raise ValueError("email is invalid") from exc
    return validated.normalized, validated.normalized


def utc_now() -> datetime:
    return datetime.now(UTC)
