"""Persistent director job state and safe state transitions."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

JOB_RE = re.compile(r"^JOB-[A-Za-z0-9._-]+$")
DECISION_RE = re.compile(r"^DEC-[A-Za-z0-9._-]+$")
INVOCATION_RE = re.compile(r"^(?:INV|MANUAL)-[A-Za-z0-9._-]+$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class StateError(ValueError):
    pass


class DirectorState:
    """Long-lived workflow state for one Director job."""

    DISCOVERED = "DISCOVERED"
    ACK_SENT = "ACK_SENT"
    DELEGATION_PENDING = "DELEGATION_PENDING"
    WORKER_RUNNING = "WORKER_RUNNING"
    WORKER_WAITING_QUESTION = "WORKER_WAITING_QUESTION"
    WAITING_FOR_WORKER = "WAITING_FOR_WORKER"
    WAITING_FOR_DECISION = "WAITING_FOR_DECISION"
    DECISION_PENDING = "DECISION_PENDING"
    DECISION_RECEIVED = "DECISION_RECEIVED"
    ANSWER_PENDING = "ANSWER_PENDING"
    WORKER_RESUMED = "WORKER_RESUMED"
    VERIFYING = "VERIFYING"
    OUTBOX_PENDING = "OUTBOX_PENDING"
    SENT = "SENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    CANCELLED = "CANCELLED"


class InvocationResult:
    """Outcome of one CLI invocation, independent of DirectorState."""

    COMPLETED = "COMPLETED"
    DELEGATED = "DELEGATED"
    WAITING = "WAITING"
    FAILED = "FAILED"


# Compatibility name retained for existing callers and persisted records.
JobState = DirectorState


TERMINAL_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.HUMAN_REQUIRED, JobState.CANCELLED}
ALLOWED_TRANSITIONS = {
    JobState.DISCOVERED: {JobState.ACK_SENT, JobState.HUMAN_REQUIRED},
    JobState.ACK_SENT: {JobState.DELEGATION_PENDING, JobState.WORKER_RUNNING, JobState.HUMAN_REQUIRED},
    JobState.DELEGATION_PENDING: {JobState.WORKER_RUNNING, JobState.WAITING_FOR_WORKER, JobState.HUMAN_REQUIRED},
    JobState.WORKER_RUNNING: {JobState.WORKER_WAITING_QUESTION, JobState.WAITING_FOR_WORKER, JobState.VERIFYING, JobState.FAILED, JobState.HUMAN_REQUIRED},
    JobState.WORKER_WAITING_QUESTION: {JobState.WAITING_FOR_DECISION, JobState.DECISION_PENDING, JobState.ANSWER_PENDING, JobState.HUMAN_REQUIRED},
    JobState.WAITING_FOR_WORKER: {JobState.WORKER_RUNNING, JobState.WORKER_WAITING_QUESTION, JobState.WAITING_FOR_DECISION, JobState.VERIFYING, JobState.FAILED, JobState.HUMAN_REQUIRED},
    JobState.WAITING_FOR_DECISION: {JobState.DECISION_PENDING, JobState.HUMAN_REQUIRED},
    JobState.DECISION_PENDING: {JobState.DECISION_RECEIVED, JobState.HUMAN_REQUIRED},
    JobState.DECISION_RECEIVED: {JobState.ANSWER_PENDING, JobState.HUMAN_REQUIRED},
    JobState.ANSWER_PENDING: {JobState.WORKER_RESUMED, JobState.HUMAN_REQUIRED},
    JobState.WORKER_RESUMED: {JobState.VERIFYING, JobState.WORKER_WAITING_QUESTION, JobState.FAILED},
    JobState.VERIFYING: {JobState.COMPLETED, JobState.FAILED, JobState.HUMAN_REQUIRED},
    JobState.OUTBOX_PENDING: {JobState.SENT, JobState.HUMAN_REQUIRED},
    JobState.SENT: {JobState.ANSWER_PENDING, JobState.WORKER_RESUMED, JobState.COMPLETED, JobState.HUMAN_REQUIRED},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_job_id(value: str) -> str:
    if not isinstance(value, str) or not JOB_RE.fullmatch(value):
        raise StateError(f"invalid Job-ID: {value!r}")
    return value


def validate_decision_id(value: str) -> str:
    if not isinstance(value, str) or not DECISION_RE.fullmatch(value):
        raise StateError(f"invalid Decision-ID: {value!r}")
    return value


def validate_invocation_id(value: str) -> str:
    if value and not INVOCATION_RE.fullmatch(value):
        raise StateError(f"invalid Invocation-ID: {value!r}")
    return value


@dataclass
class JobRecord:
    job_id: str
    request_mail_id: int
    requester_uid: str
    current_agent_uid: str
    commander_uid: str
    decision_id: str
    state: str
    round_count: int = 0
    decision_count: int = 0
    handled_mail_ids: list[int] = field(default_factory=list)
    artifact_paths: list[str] = field(default_factory=list)
    latest_checkpoint: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    request_summary: str = ""
    latest_invocation_id: str = ""
    last_inbound_invocation_id: str = ""
    latest_invocation_result: str = ""
    parent_invocation_id: str | None = None
    root_invocation_id: str = ""
    trigger_mail_uid: int = 0
    result_mail_uid: int = 0
    inbound_result_mail_uids: dict[str, int] = field(default_factory=dict)
    rejected_mail_reasons: dict[str, str] = field(default_factory=dict)
    delegate_mail_id: int = 0
    expected_worker_parent_invocation_id: str = ""
    expected_worker_trigger_mail_uid: int = 0
    active_worker_invocation_id: str = ""
    expected_commander_parent_invocation_id: str = ""
    expected_commander_trigger_mail_uid: int = 0
    active_commander_invocation_id: str = ""

    def __post_init__(self) -> None:
        validate_job_id(self.job_id)
        validate_decision_id(self.decision_id)
        validate_invocation_id(self.latest_invocation_id)
        validate_invocation_id(self.last_inbound_invocation_id)
        validate_invocation_id(self.parent_invocation_id or "")
        validate_invocation_id(self.root_invocation_id)
        validate_invocation_id(self.expected_worker_parent_invocation_id)
        validate_invocation_id(self.active_worker_invocation_id)
        validate_invocation_id(self.expected_commander_parent_invocation_id)
        validate_invocation_id(self.active_commander_invocation_id)
        if self.latest_invocation_result and self.latest_invocation_result not in {
            InvocationResult.COMPLETED,
            InvocationResult.DELEGATED,
            InvocationResult.WAITING,
            InvocationResult.FAILED,
        }:
            raise StateError(
                f"invalid InvocationResult: {self.latest_invocation_result!r}"
            )
        if self.state not in {v for k, v in vars(JobState).items() if not k.startswith("_") and isinstance(v, str)}:
            raise StateError(f"invalid state: {self.state!r}")
        for name in (
            "request_mail_id", "round_count", "decision_count",
            "trigger_mail_uid", "result_mail_uid", "delegate_mail_id",
            "expected_worker_trigger_mail_uid",
            "expected_commander_trigger_mail_uid",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StateError(f"invalid non-negative integer {name}: {value!r}")
        if not isinstance(self.handled_mail_ids, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.handled_mail_ids
        ):
            raise StateError("handled_mail_ids must contain positive integers")
        if not isinstance(self.inbound_result_mail_uids, dict) or any(
            not isinstance(key, str)
            or not key
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for key, value in self.inbound_result_mail_uids.items()
        ):
            raise StateError(
                "inbound_result_mail_uids must map event keys to positive integers"
            )
        if not isinstance(self.rejected_mail_reasons, dict) or any(
            not isinstance(key, str)
            or not key.isdigit()
            or int(key) <= 0
            or not isinstance(reason, str)
            or not reason
            for key, reason in self.rejected_mail_reasons.items()
        ):
            raise StateError(
                "rejected_mail_reasons must map positive mail IDs to reasons"
            )

    def transition(self, target: str) -> "JobRecord":
        if target == self.state:
            return replace(self, updated_at=utc_now())
        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if target not in allowed:
            raise StateError(f"invalid transition {self.state} -> {target}")
        return replace(self, state=target, updated_at=utc_now())


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, job_id: str) -> Path:
        validate_job_id(job_id)
        name = job_id.replace("/", "_").replace("\\", "_")
        if not SAFE_NAME_RE.fullmatch(name):
            raise StateError("unsafe job filename")
        path = (self.root / f"{name}.json").resolve()
        path.relative_to(self.root)
        return path

    def save(self, record: JobRecord) -> None:
        record.__post_init__()
        path = self.path_for(record.job_id)
        payload = json.dumps(asdict(record), ensure_ascii=False, indent=2) + "\n"
        path.write_text(payload, encoding="utf-8")

    def load(self, job_id: str) -> JobRecord | None:
        path = self.path_for(job_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("request_summary", "")
        data.setdefault("latest_invocation_id", "")
        data.setdefault("last_inbound_invocation_id", "")
        data.setdefault("latest_invocation_result", "")
        data.setdefault("parent_invocation_id", None)
        data.setdefault("root_invocation_id", "")
        data.setdefault("trigger_mail_uid", 0)
        data.setdefault("result_mail_uid", 0)
        data.setdefault("inbound_result_mail_uids", {})
        data.setdefault("rejected_mail_reasons", {})
        data.setdefault("expected_worker_parent_invocation_id", "")
        data.setdefault("expected_worker_trigger_mail_uid", 0)
        data.setdefault("active_worker_invocation_id", "")
        data.setdefault("expected_commander_parent_invocation_id", "")
        data.setdefault("expected_commander_trigger_mail_uid", 0)
        data.setdefault("active_commander_invocation_id", "")
        return JobRecord(**data)

    def list_records(self) -> list[JobRecord]:
        records = []
        for path in sorted(self.root.glob("JOB-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("request_summary", "")
            data.setdefault("latest_invocation_id", "")
            data.setdefault("last_inbound_invocation_id", "")
            data.setdefault("latest_invocation_result", "")
            data.setdefault("parent_invocation_id", None)
            data.setdefault("root_invocation_id", "")
            data.setdefault("trigger_mail_uid", 0)
            data.setdefault("result_mail_uid", 0)
            data.setdefault("inbound_result_mail_uids", {})
            data.setdefault("rejected_mail_reasons", {})
            data.setdefault("expected_worker_parent_invocation_id", "")
            data.setdefault("expected_worker_trigger_mail_uid", 0)
            data.setdefault("active_worker_invocation_id", "")
            data.setdefault("expected_commander_parent_invocation_id", "")
            data.setdefault("expected_commander_trigger_mail_uid", 0)
            data.setdefault("active_commander_invocation_id", "")
            records.append(JobRecord(**data))
        return records
