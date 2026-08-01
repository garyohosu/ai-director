from __future__ import annotations

import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone


REQUEST_ID_RE = re.compile(r"\[(JOB-[A-Za-z0-9][A-Za-z0-9_.-]*)\]")
DECISION_ID_RE = re.compile(
    r"\[(DEC-[0-9]{8}T[0-9]{6}Z-[0-9]{2}-[A-F0-9]{4})\]"
)


class InvocationIdError(ValueError):
    """Raised when Invocation-ID environment metadata is unsafe or missing."""


INVOCATION_ID_RE = re.compile(r"^(?:INV|MANUAL)-[A-Za-z0-9._-]+$")


def validate_invocation_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not INVOCATION_ID_RE.fullmatch(value):
        raise InvocationIdError(f"{field_name} must be a valid Invocation-ID")
    return value


@dataclass(frozen=True)
class InvocationMetadata:
    parent_invocation_id: str | None
    root_invocation_id: str
    trigger_mail_uid: int | None


def resolve_invocation_metadata(
    environment: Mapping[str, str], invocation_id: str
) -> InvocationMetadata:
    parent = environment.get("AI_PARENT_INVOCATION_ID")
    if parent == "":
        raise InvocationIdError("AI_PARENT_INVOCATION_ID is defined but empty")
    if parent is not None:
        parent = validate_invocation_id(parent, "AI_PARENT_INVOCATION_ID")
    if "AI_ROOT_INVOCATION_ID" in environment:
        root = validate_invocation_id(
            environment.get("AI_ROOT_INVOCATION_ID"), "AI_ROOT_INVOCATION_ID"
        )
    else:
        root = invocation_id
    trigger_raw = environment.get("AI_TRIGGER_MAIL_UID")
    trigger = None
    if trigger_raw is not None:
        try:
            trigger = int(trigger_raw)
        except (TypeError, ValueError) as err:
            raise InvocationIdError("AI_TRIGGER_MAIL_UID must be an integer") from err
        if trigger <= 0:
            raise InvocationIdError("AI_TRIGGER_MAIL_UID must be positive")
    return InvocationMetadata(parent, root, trigger)


def new_manual_invocation_id() -> str:
    """Return a compatibility-mode ID containing one complete UUID4."""

    return f"MANUAL-{str(uuid.uuid4()).upper()}"


def resolve_invocation_id(environment: Mapping[str, str]) -> str:
    """Resolve canonical and legacy Invocation-ID environment variables."""

    ai_present = "AI_INVOCATION_ID" in environment
    legacy_present = "INVOCATION_ID" in environment
    ai_value = environment.get("AI_INVOCATION_ID")
    legacy_value = environment.get("INVOCATION_ID")

    if (ai_present and ai_value == "") or (legacy_present and legacy_value == ""):
        raise InvocationIdError(
            "Invocation-ID environment variable is defined but empty"
        )
    if ai_present and legacy_present and ai_value != legacy_value:
        raise InvocationIdError(
            "AI_INVOCATION_ID and INVOCATION_ID are both set but have different values"
        )
    if ai_present:
        return validate_invocation_id(ai_value, "AI_INVOCATION_ID")
    if legacy_present:
        return validate_invocation_id(legacy_value, "INVOCATION_ID")
    if environment.get("AI_ALLOW_MISSING_INVOCATION_ID") == "1":
        return new_manual_invocation_id()
    raise InvocationIdError(
        "Invocation-ID is not set in environment and compatibility mode is not enabled"
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    value = value or utc_now()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_request_id(subject: str, body: str = "") -> str | None:
    match = REQUEST_ID_RE.search(subject or "")
    if match:
        return match.group(1)
    match = re.search(r"(?:Request-ID|request_id)\s*:\s*(JOB-[A-Za-z0-9][A-Za-z0-9_.-]*)", body or "")
    return match.group(1) if match else None


def new_decision_id(sequence: int) -> str:
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"DEC-{stamp}-{sequence:02d}-{secrets.token_hex(2).upper()}"


def safe_request_filename(request_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", request_id)
