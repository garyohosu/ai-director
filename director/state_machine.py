"""Persistent director job state and safe state transitions."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

JOB_RE = re.compile(r"^JOB-[A-Za-z0-9._-]+$")
DECISION_RE = re.compile(r"^DEC-[A-Za-z0-9._-]+$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class StateError(ValueError):
    pass


class JobState:
    DISCOVERED = "DISCOVERED"
    ACK_SENT = "ACK_SENT"
    DELEGATION_PENDING = "DELEGATION_PENDING"
    WORKER_RUNNING = "WORKER_RUNNING"
    WORKER_WAITING_QUESTION = "WORKER_WAITING_QUESTION"
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


TERMINAL_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.HUMAN_REQUIRED, JobState.CANCELLED}
ALLOWED_TRANSITIONS = {
    JobState.DISCOVERED: {JobState.ACK_SENT, JobState.HUMAN_REQUIRED},
    JobState.ACK_SENT: {JobState.DELEGATION_PENDING, JobState.WORKER_RUNNING, JobState.HUMAN_REQUIRED},
    JobState.DELEGATION_PENDING: {JobState.WORKER_RUNNING, JobState.HUMAN_REQUIRED},
    JobState.WORKER_RUNNING: {JobState.WORKER_WAITING_QUESTION, JobState.VERIFYING, JobState.FAILED, JobState.HUMAN_REQUIRED},
    JobState.WORKER_WAITING_QUESTION: {JobState.WAITING_FOR_DECISION, JobState.DECISION_PENDING, JobState.HUMAN_REQUIRED},
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

    def __post_init__(self) -> None:
        validate_job_id(self.job_id)
        validate_decision_id(self.decision_id)
        if self.state not in {v for k, v in vars(JobState).items() if not k.startswith("_") and isinstance(v, str)}:
            raise StateError(f"invalid state: {self.state!r}")

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
        path = self.path_for(record.job_id)
        payload = json.dumps(asdict(record), ensure_ascii=False, indent=2) + "\n"
        path.write_text(payload, encoding="utf-8")

    def load(self, job_id: str) -> JobRecord | None:
        path = self.path_for(job_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return JobRecord(**data)

    def list_records(self) -> list[JobRecord]:
        records = []
        for path in sorted(self.root.glob("JOB-*.json")):
            records.append(JobRecord(**json.loads(path.read_text(encoding="utf-8"))))
        return records
