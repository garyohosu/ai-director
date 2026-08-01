from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

if __package__:
    from .ids import InvocationIdError, resolve_invocation_id, resolve_invocation_metadata
else:
    from ids import InvocationIdError, resolve_invocation_id, resolve_invocation_metadata

_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "api-key",
    "token",
    "password",
    "secret",
    "cookie",
    "authorization",
)
_QANDA_ID_RE = re.compile(r"^Q[0-9]{3,}$")
# Correlation and structural fields are the authoritative record body contract
# (real04_mitigation_design.md 「本文メタデータが正本」). They are never
# redacted: masking them would break invocation correlation and make a correct
# reply look like NO_REPLY.
_PROTECTED_KEYS = frozenset(
    {
        "message_type",
        "task_eligible",
        "status",
        "job_id",
        "decision_id",
        "invocation_id",
        "parent_invocation_id",
        "root_invocation_id",
        "trigger_mail_uid",
        "invocation_result",
        "agent_uid",
        "qanda_ids",
        "path",
        "sha256",
    }
)


def _load_mail_module(project_root: Path):
    if "mail" in sys.modules and hasattr(sys.modules["mail"], "send_mail"):
        return sys.modules["mail"]
    mail_dir = project_root / "mail"
    if not mail_dir.is_dir():
        raise RuntimeError(f"mail directory not found at {mail_dir}")
    init_path = mail_dir / "__init__.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mail", init_path, submodule_search_locations=[str(mail_dir)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to build spec for mail module")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mail"] = mod
    spec.loader.exec_module(mod)
    return mod


def mask_secrets(data: str) -> str:
    lines_out = []
    for line in data.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in _SECRET_MARKERS) and ("=" in line or ":" in line):
            lines_out.append("[REDACTED_SECRET_LINE]")
        else:
            lines_out.append(line)
    return "\n".join(lines_out)


def _mask_value(value):
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if isinstance(value, dict):
        return mask_structured_payload(value)
    return value


def mask_structured_payload(payload: dict) -> dict:
    """Redact secrets inside a result body without breaking its JSON shape.

    Masking the serialized text would replace whole ``"key": "value"`` lines
    with a bare marker, leaving a body that no longer parses. Orchestrator
    treats an unparsable body as "no structured payload", so a correct reply
    would be scored as NO_REPLY -- the exact REAL04 failure this design
    exists to prevent. Redaction therefore happens on values, before dumping.
    """

    masked: dict = {}
    for key, value in payload.items():
        if isinstance(key, str) and key in _PROTECTED_KEYS:
            masked[key] = value
            continue
        if isinstance(key, str) and any(
            marker in key.lower() for marker in _SECRET_MARKERS
        ):
            masked[key] = "[REDACTED_SECRET_VALUE]"
            continue
        masked[key] = _mask_value(value)
    return masked


def dump_masked_body(payload: dict) -> str:
    """Serialize a result body with secrets redacted and JSON kept valid."""

    return json.dumps(mask_structured_payload(payload), ensure_ascii=False, indent=2)


def validate_and_read_result_file(project_root: Path, file_path_str: str) -> dict:
    raw_path = Path(file_path_str)
    if raw_path.is_absolute():
        raise ValueError(f"Absolute path not allowed: {file_path_str}")

    resolved = (project_root / raw_path).resolve()
    root_resolved = project_root.resolve()

    try:
        resolved.relative_to(root_resolved)
    except ValueError as err:
        raise ValueError(
            f"Path outside project_path rejected: {file_path_str}"
        ) from err

    if not resolved.is_file():
        raise ValueError(
            f"File does not exist or is not a regular file: {file_path_str}"
        )

    if resolved.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("File size exceeds 10MB limit")

    try:
        content = resolved.read_bytes().decode("utf-8")
    except UnicodeDecodeError as err:
        raise ValueError(f"Result file is not valid UTF-8: {file_path_str}") from err
    try:
        data = json.loads(content)
    except json.JSONDecodeError as err:
        raise ValueError(f"Result file contains invalid JSON: {file_path_str}") from err
    if not isinstance(data, dict):
        raise ValueError("Result file JSON must be an object")

    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("Result file artifacts must be an array")
    for index, art in enumerate(artifacts):
        if not isinstance(art, dict):
            raise ValueError(f"Result file artifact at index {index} must be an object")
        if "path" not in art or not isinstance(art["path"], str) or not art["path"]:
            raise ValueError(
                f"Result file artifact at index {index} must contain a non-empty path"
            )
        art_path_str = art["path"]
        art_raw = Path(art_path_str)
        if art_raw.is_absolute():
            raise ValueError(f"Absolute artifact path not allowed: {art_path_str}")
        art_path = (project_root / art_raw).resolve()
        try:
            art_path.relative_to(root_resolved)
        except ValueError as err:
            raise ValueError(
                f"Artifact path outside project_path rejected: {art_path_str}"
            ) from err
        if not art_path.is_file():
            raise ValueError(
                f"Artifact file does not exist or is not regular: {art_path_str}"
            )
        actual_sha = hashlib.sha256(art_path.read_bytes()).hexdigest()
        if "sha256" in art and art["sha256"] != actual_sha:
            raise ValueError(
                f"SHA-256 mismatch for artifact {art['path']}: "
                f"expected {art['sha256']}, got {actual_sha}"
            )
        art["sha256"] = actual_sha
    return data


def validate_wait_payload(project_root: Path, payload: dict, job_id: str, decision_id: str | None, invocation_id: str | None = None) -> dict:
    if payload.get("status") != "WAITING_FOR_DECISION":
        raise ValueError("wait result status must be WAITING_FOR_DECISION")
    if payload.get("job_id") != job_id:
        raise ValueError("wait result Job-ID does not match JOB_ID")
    if decision_id and payload.get("decision_id") != decision_id:
        raise ValueError("wait result Decision-ID does not match DECISION_ID")
    if invocation_id and payload.get("invocation_id") not in (None, invocation_id):
        raise ValueError("wait result Invocation-ID does not match INVOCATION_ID")
    qanda_ids = payload.get("qanda_ids")
    if not isinstance(qanda_ids, list) or not qanda_ids or not all(isinstance(item, str) and _QANDA_ID_RE.fullmatch(item) for item in qanda_ids):
        raise ValueError("qanda_ids must contain one or more valid Q&A IDs")
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        raise ValueError("summary is required")
    checkpoint = payload.get("checkpoint")
    if not isinstance(checkpoint, str) or not checkpoint:
        raise ValueError("checkpoint is required")
    _validate_project_relative_file(project_root, checkpoint, must_exist=True)
    qanda_path = payload.get("qanda_path", "QandA.md")
    _validate_project_relative_file(project_root, qanda_path, must_exist=True)
    return payload


def _validate_project_relative_file(project_root: Path, value: str, *, must_exist: bool) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        raise ValueError(f"Absolute path not allowed: {value}")
    resolved = (project_root / raw).resolve()
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as err:
        raise ValueError(f"Path outside project_path rejected: {value}") from err
    if must_exist and not resolved.is_file():
        raise ValueError(f"File does not exist: {value}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Secure status reporter for AI agents"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("ack", help="Report ACK_RECEIVED")

    complete_p = subparsers.add_parser("complete", help="Report COMPLETED status")
    complete_p.add_argument(
        "--result-file",
        required=True,
        help="Path to result JSON file relative to project root",
    )

    wait_p = subparsers.add_parser("wait", help="Report WAITING_FOR_DECISION status")
    wait_p.add_argument(
        "--result-file",
        required=True,
        help="Path to waiting result JSON file relative to project root",
    )

    fail_p = subparsers.add_parser("fail", help="Report FAILED status")
    fail_p.add_argument(
        "--result-file",
        required=True,
        help="Path to error result JSON file relative to project root",
    )

    args = parser.parse_args()

    mail_db_path = os.environ.get("AGENT_MAIL_DB_PATH")
    agent_uid = os.environ.get("AGENT_UID")
    reply_to_uid = os.environ.get("REPLY_TO_UID")
    job_id = os.environ.get("JOB_ID")
    decision_id = os.environ.get("DECISION_ID")
    project_path_str = os.environ.get("PROJECT_PATH", ".")

    if not all([agent_uid, reply_to_uid, job_id]):
        print(
            "ERROR: Missing required environment variables (AGENT_UID, REPLY_TO_UID, JOB_ID)",
            file=sys.stderr,
        )
        return 1

    project_root = Path(project_path_str).resolve()
    mail = _load_mail_module(project_root)

    kwargs = {}
    if mail_db_path:
        db_p = Path(mail_db_path)
        if not db_p.is_absolute():
            db_p = (project_root / db_p).resolve()
        kwargs["db_path"] = db_p

    try:
        final_inv_id = resolve_invocation_id(os.environ)
        invocation_metadata = resolve_invocation_metadata(os.environ, final_inv_id)
    except InvocationIdError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    payload = None
    if args.action != "ack":
        try:
            payload = validate_and_read_result_file(project_root, args.result_file)
        except (OSError, ValueError) as err:
            print(f"ERROR validating result file: {err}", file=sys.stderr)
            return 1

        payload_invocation_id = payload.get("invocation_id")
        explicit_invocation_id = (
            "AI_INVOCATION_ID" in os.environ or "INVOCATION_ID" in os.environ
        )
        if payload_invocation_id is not None:
            if not isinstance(payload_invocation_id, str) or not payload_invocation_id:
                print(
                    "ERROR: result Invocation-ID must be a non-empty string",
                    file=sys.stderr,
                )
                return 1
            if not explicit_invocation_id:
                print(
                    "ERROR: Invocation-ID is required by result file but not set in environment",
                    file=sys.stderr,
                )
                return 1
            if payload_invocation_id != final_inv_id:
                print(
                    "ERROR: Specified Invocation-ID "
                    f"'{payload_invocation_id}' does not match environment '{final_inv_id}'",
                    file=sys.stderr,
                )
                return 1
        expected_lineage = {
            "parent_invocation_id": invocation_metadata.parent_invocation_id,
            "root_invocation_id": invocation_metadata.root_invocation_id,
            "trigger_mail_uid": invocation_metadata.trigger_mail_uid,
        }
        for key, expected_value in expected_lineage.items():
            if key in payload and payload[key] != expected_value:
                print(
                    f"ERROR: result {key} does not match launch metadata",
                    file=sys.stderr,
                )
                return 1

    act_str = args.action.upper()
    dec_str = decision_id or "DEC-NONE"
    subject = f"[{job_id}] [{dec_str}] [{final_inv_id}] STATUS: {act_str}"

    # Persistent state tracking for deduplication & state transition rules
    state_dir = project_root / "director" / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{job_id}_{dec_str}_{final_inv_id}.json"

    current_state = None
    if state_file.is_file():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(state_data, dict):
                raise ValueError("state JSON must be an object")
            current_state = state_data.get("status")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as err:
            print(f"ERROR reading Invocation state file: {err}", file=sys.stderr)
            return 1

    target_status = {
        "ack": "ACK_RECEIVED",
        "wait": "WAITING_FOR_DECISION",
        "complete": "COMPLETED",
        "fail": "FAILED",
    }[args.action]

    # State Transition Rules:
    # 1. Same status twice -> Duplicate, skip sending mail.
    if current_state == target_status:
        print(f"INFO: Duplicate {target_status} requested for {job_id}/{dec_str}. Skipping mail delivery.", file=sys.stderr)
        return 0

    # 2. Transition from terminal state (COMPLETED or FAILED) to any state -> Rejected.
    if current_state in ("COMPLETED", "FAILED"):
        print(f"ERROR: Cannot transition from terminal state '{current_state}' to '{target_status}' for {job_id}/{dec_str}.", file=sys.stderr)
        return 1

    if args.action == "ack":
        body_dict = {
            "message_type": "INVOCATION_ACK",
            "task_eligible": False,
            "status": "ACK_RECEIVED",
            "job_id": job_id,
            "decision_id": decision_id,
            "invocation_id": final_inv_id,
            "parent_invocation_id": invocation_metadata.parent_invocation_id,
            "root_invocation_id": invocation_metadata.root_invocation_id,
            "trigger_mail_uid": invocation_metadata.trigger_mail_uid,
            "agent_uid": agent_uid,
            "message": "Task instruction accepted and processing started.",
        }
        body_text = dump_masked_body(body_dict)
        mail.send_mail(agent_uid, reply_to_uid, subject, body_text, **kwargs)
        state_file.write_text(json.dumps({"status": "ACK_RECEIVED", "updated_at": subject}), encoding="utf-8")
        print(f"ACK sent to {reply_to_uid}")
        return 0

    if args.action == "wait":
        try:
            assert payload is not None
            payload = validate_wait_payload(
                project_root, payload, job_id, decision_id, final_inv_id
            )
        except ValueError as err:
            print(f"ERROR validating wait result file: {err}", file=sys.stderr)
            return 1
        payload["agent_uid"] = agent_uid
        payload["message_type"] = "INVOCATION_RESULT"
        payload["task_eligible"] = True
        payload["decision_id"] = decision_id
        payload["invocation_id"] = final_inv_id
        payload["parent_invocation_id"] = invocation_metadata.parent_invocation_id
        payload["root_invocation_id"] = invocation_metadata.root_invocation_id
        payload["trigger_mail_uid"] = invocation_metadata.trigger_mail_uid
        payload["invocation_result"] = "WAITING"
        body_text = dump_masked_body(payload)
        mail.send_mail(agent_uid, reply_to_uid, subject, body_text, **kwargs)
        state_file.write_text(json.dumps({"status": "WAITING_FOR_DECISION", "updated_at": subject}), encoding="utf-8")
        print(f"Report WAITING_FOR_DECISION sent to {reply_to_uid}")
        return 0

    assert payload is not None

    status_code = "COMPLETED" if args.action == "complete" else "FAILED"
    payload["status"] = status_code
    payload["job_id"] = job_id
    payload["decision_id"] = decision_id
    payload["agent_uid"] = agent_uid
    payload["message_type"] = "INVOCATION_RESULT"
    payload["task_eligible"] = True
    payload["invocation_id"] = final_inv_id
    payload["parent_invocation_id"] = invocation_metadata.parent_invocation_id
    payload["root_invocation_id"] = invocation_metadata.root_invocation_id
    payload["trigger_mail_uid"] = invocation_metadata.trigger_mail_uid
    payload["invocation_result"] = (
        "COMPLETED" if args.action == "complete" else "FAILED"
    )

    body_text = dump_masked_body(payload)
    mail.send_mail(agent_uid, reply_to_uid, subject, body_text, **kwargs)
    state_file.write_text(json.dumps({"status": status_code, "updated_at": subject}), encoding="utf-8")
    print(f"Report {status_code} sent to {reply_to_uid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
