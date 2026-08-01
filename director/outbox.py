"""Idempotent mail outbox using only the mail public API."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from .state_machine import JobState, utc_now, validate_decision_id, validate_job_id
except ImportError:
    from state_machine import JobState, utc_now, validate_decision_id, validate_job_id


class OutboxError(RuntimeError):
    pass


@dataclass
class OutboxEntry:
    job_id: str
    decision_id: str
    sender_uid: str
    recipient_uid: str
    subject: str
    body: str
    state: str = JobState.OUTBOX_PENDING
    mail_id: int | None = None
    updated_at: str = ""


class Outbox:
    def __init__(self, root: Path, mail_port: object) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.mail = mail_port

    def _path(self, job_id: str, decision_id: str) -> Path:
        validate_job_id(job_id)
        validate_decision_id(decision_id)
        path = (self.root / f"{job_id}_{decision_id}.json").resolve()
        path.relative_to(self.root)
        return path

    def prepare(self, entry: OutboxEntry) -> None:
        entry.updated_at = utc_now()
        self._path(entry.job_id, entry.decision_id).write_text(json.dumps(asdict(entry), ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, job_id: str, decision_id: str) -> OutboxEntry | None:
        path = self._path(job_id, decision_id)
        if not path.is_file():
            return None
        return OutboxEntry(**json.loads(path.read_text(encoding="utf-8")))

    def pending(self) -> list[OutboxEntry]:
        entries: list[OutboxEntry] = []
        for path in sorted(self.root.glob("JOB-*_DEC-*.json")):
            entry = OutboxEntry(**json.loads(path.read_text(encoding="utf-8")))
            if entry.state == JobState.OUTBOX_PENDING:
                entries.append(entry)
        return entries

    def recover(self, entry: OutboxEntry) -> OutboxEntry:
        matches = self.mail.find_mails(
            sender_uid=entry.sender_uid,
            recipient_uid=entry.recipient_uid,
            request_id=entry.decision_id,
        )
        if len(matches) > 1:
            entry.state = JobState.HUMAN_REQUIRED
            self.prepare(entry)
            raise OutboxError(f"multiple mails for Decision-ID {entry.decision_id}")
        if len(matches) == 1:
            entry.mail_id = matches[0]["mail_id"]
            entry.state = JobState.SENT
            self.prepare(entry)
            return entry
        mail_id = self.mail.send_mail(entry.sender_uid, entry.recipient_uid, entry.subject, entry.body)
        entry.mail_id = mail_id
        entry.state = JobState.SENT
        self.prepare(entry)
        return entry
