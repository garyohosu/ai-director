"""Mail-driven director process; it never launches an AI CLI."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import PurePosixPath
from pathlib import Path

try:
    from .context_packet import ContextPacketBuilder
    from .knowledge import KnowledgeIndex
    from .decision import DecisionError, parse_decision
    from .ids import InvocationIdError, resolve_invocation_id, resolve_invocation_metadata
    from .outbox import Outbox, OutboxEntry, OutboxError
    from .qanda import QandaError, answer_question, find_answered_reuse, find_open_blocking, normalized_reuse_signature, parse_qanda
    from .state_machine import InvocationResult, JobRecord, JobState, JobStore, StateError, TERMINAL_STATES, utc_now, validate_job_id
except ImportError:  # direct ``py director/director.py`` compatibility
    from context_packet import ContextPacketBuilder
    from knowledge import KnowledgeIndex
    from decision import DecisionError, parse_decision
    from ids import InvocationIdError, resolve_invocation_id, resolve_invocation_metadata
    from outbox import Outbox, OutboxEntry, OutboxError
    from qanda import QandaError, answer_question, find_answered_reuse, find_open_blocking, normalized_reuse_signature, parse_qanda
    from state_machine import InvocationResult, JobRecord, JobState, JobStore, StateError, TERMINAL_STATES, utc_now, validate_job_id

JOB_RE = re.compile(r"\[(JOB-[A-Za-z0-9._-]+)\]")
DEC_RE = re.compile(r"\[(DEC-[A-Za-z0-9._-]+)\]")


class DirectorError(RuntimeError):
    pass


def _load_mail(project_root: Path):
    mail_dir = project_root / "mail"
    init_path = mail_dir / "__init__.py"
    if not init_path.is_file():
        raise DirectorError("mail package not found")
    spec = importlib.util.spec_from_file_location("mail", init_path, submodule_search_locations=[str(mail_dir)])
    if spec is None or spec.loader is None:
        raise DirectorError("mail module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules["mail"] = module
    spec.loader.exec_module(module)
    return module


def _id(pattern: re.Pattern[str], subject: str, label: str) -> str:
    match = pattern.search(subject)
    if not match:
        raise DirectorError(f"{label} missing from subject")
    return match.group(1)


def _new_decision_id(job_id: str, ordinal: int) -> str:
    digest = hashlib.sha256(f"{job_id}:{ordinal}".encode()).hexdigest()[:4].upper()
    return f"DEC-{utc_now().replace('-', '').replace(':', '').replace('T', 'T').replace('Z', 'Z')}-{ordinal:02d}-{digest}"


class DirectorEngine:
    def __init__(self, project_root: Path, *, mail=None, config_path: Path | None = None) -> None:
        self.root = Path(project_root).resolve()
        self.config = self._read_config(config_path or (self.root / "director" / "config.json"))
        self.mail = mail or _load_mail(self.root)
        self.runtime = self.root / "director" / "runtime"
        self.jobs = JobStore(self.runtime / "jobs")
        self.outbox = Outbox(self.runtime / "outbox", self.mail)
        self.packet_builder = ContextPacketBuilder(self.root, int(self.config.get("context_max_bytes", 32 * 1024)))
        self.knowledge = KnowledgeIndex(self.root, int(self.config.get("knowledge_page_max_bytes", 12 * 1024)))
        self.qanda_path = self.root / "QandA.md"
        self.pending_path = self.runtime / "pending.json"
        self.uid = self._register_and_persist()
        try:
            self.invocation_id = resolve_invocation_id(os.environ)
            invocation_metadata = resolve_invocation_metadata(
                os.environ, self.invocation_id
            )
        except InvocationIdError as err:
            raise DirectorError(str(err)) from err
        self.parent_invocation_id = invocation_metadata.parent_invocation_id
        self.root_invocation_id = invocation_metadata.root_invocation_id
        self.trigger_mail_uid = invocation_metadata.trigger_mail_uid
        self.uids = self._register_names()
        self.log_path = self.root / "director" / "logs" / "director.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_config(path: Path) -> dict:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise DirectorError("director config must be an object")
        return data

    def _register_and_persist(self) -> str:
        name = str(self.config.get("director_name", "director"))
        uid = self.mail.register_user(name)
        path = self.runtime / "self.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        env_uid = os.environ.get("AGENT_UID")
        if env_uid and env_uid != uid:
            raise DirectorError("orchestrator AGENT_UID does not match registered director UID")
        path.write_text(json.dumps({"name": name, "uid": uid, "updated_at": utc_now()}, indent=2), encoding="utf-8")
        return uid

    def _register_names(self) -> dict[str, str]:
        names = {
            "worker": self.config.get("worker_name", "claude_worker"),
            "commander": self.config.get("commander_name", "codex_commander"),
            "human": self.config.get("human_name", "human_controller"),
        }
        return {key: self.mail.register_user(str(name)) for key, name in names.items()}

    def _log(self, event: str, **data: object) -> None:
        row = {"timestamp": utc_now(), "event": event, **data}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _send(self, record: JobRecord, recipient: str, decision_id: str, subject: str, body: str) -> int:
        entry = OutboxEntry(record.job_id, decision_id, self.uid, recipient, subject, body)
        self.outbox.prepare(entry)
        sent = self.outbox.recover(entry)
        self._log("mail_sent", job_id=record.job_id, decision_id=decision_id, mail_id=sent.mail_id, recipient_uid=recipient)
        return int(sent.mail_id or 0)

    def _invocation_metadata(self, record: JobRecord) -> dict[str, object]:
        return {
            "invocation_id": self.invocation_id,
            "parent_invocation_id": self.parent_invocation_id,
            "root_invocation_id": self.root_invocation_id,
            "trigger_mail_uid": self.trigger_mail_uid or record.trigger_mail_uid,
        }

    def _complete_noop(
        self, record: JobRecord, message: dict, reason: str
    ) -> JobRecord:
        """Emit a terminal result for a trigger needing no state transition."""

        payload = {
            "message_type": "INVOCATION_RESULT",
            "task_eligible": False,
            "status": "COMPLETED",
            "director_state": record.state,
            "invocation_result": InvocationResult.COMPLETED,
            "job_id": record.job_id,
            "decision_id": record.decision_id,
            **self._invocation_metadata(record),
            "reason": reason,
        }
        result_decision_id = (
            f"{record.decision_id}-RESULT-{int(message['mail_id'])}-{self.invocation_id}"
        )
        record.result_mail_uid = self._send(
            record,
            message["sender_uid"],
            result_decision_id,
            f"[{record.job_id}] [{record.decision_id}] [{result_decision_id}] [{self.invocation_id}] STATUS: COMPLETED",
            json.dumps(payload, ensure_ascii=False),
        )
        record.latest_invocation_id = self.invocation_id
        record.latest_invocation_result = InvocationResult.COMPLETED
        self.jobs.save(record)
        return record

    def _replay_invocation_result(
        self, record: JobRecord, message: dict, replay: dict[str, object]
    ) -> JobRecord:
        """Re-emit the original semantic result with this launch's lineage."""

        decision_id = str(replay["decision_id"])
        invocation_result = str(replay["invocation_result"])
        payload = {
            "message_type": "INVOCATION_RESULT",
            "task_eligible": False,
            "status": invocation_result,
            "director_state": replay["director_state"],
            "invocation_result": invocation_result,
            "job_id": record.job_id,
            "decision_id": decision_id,
            **self._invocation_metadata(record),
            "delegated_mail_uid": replay["delegated_mail_uid"],
            "next_decision_id": replay["next_decision_id"],
            "reason": "replayed persisted invocation result",
        }
        result_decision_id = (
            f"{decision_id}-REPLAY-{int(message['mail_id'])}-{self.invocation_id}"
        )
        result_mail_uid = self._send(
            record,
            message["sender_uid"],
            result_decision_id,
            f"[{record.job_id}] [{decision_id}] [{result_decision_id}] [{self.invocation_id}] STATUS: {invocation_result}",
            json.dumps(payload, ensure_ascii=False),
        )
        record.result_mail_uid = result_mail_uid
        record.latest_invocation_id = self.invocation_id
        record.latest_invocation_result = invocation_result
        self.jobs.save(record)
        self._log(
            "invocation_result_replayed",
            job_id=record.job_id,
            source_mail_uid=message["mail_id"],
            result_mail_uid=result_mail_uid,
            invocation_result=invocation_result,
            decision_id=decision_id,
        )
        return record

    @staticmethod
    def _resume_replay_lineage(message: dict) -> dict[str, object]:
        """Capture the immutable inbound lineage used to authenticate replays."""

        try:
            payload = json.loads(message.get("body", ""))
        except (TypeError, json.JSONDecodeError):
            payload = None
        lineage: dict[str, object] = {
            "source_sender_uid": message["sender_uid"],
        }
        if isinstance(payload, dict) and isinstance(payload.get("invocation_id"), str):
            lineage.update(
                {
                    "source_invocation_id": payload.get("invocation_id"),
                    "source_parent_invocation_id": payload.get(
                        "parent_invocation_id"
                    ),
                    "source_root_invocation_id": payload.get("root_invocation_id"),
                    "source_trigger_mail_uid": payload.get("trigger_mail_uid"),
                }
            )
        return lineage

    def _continue_resume_delegation(
        self, record: JobRecord, replay: dict[str, object]
    ) -> JobRecord:
        """Idempotently finish the child TASK portion of a resume transaction."""

        delegated_mail_uid = int(replay["delegated_mail_uid"])
        if delegated_mail_uid > 0:
            return record
        next_decision_id = str(replay["next_decision_id"])
        if record.decision_id not in {str(replay["decision_id"]), next_decision_id}:
            raise DirectorError("resume replay no longer matches the Director decision")
        record.decision_id = next_decision_id
        if record.state != JobState.ANSWER_PENDING:
            record = record.transition(JobState.ANSWER_PENDING)
        self.jobs.save(record)
        packet, size, tokens = self._build_packet(
            record,
            f"Resume from checkpoint {record.latest_checkpoint}; use the formal Q&A answer: {replay['answer']}",
        )
        resumed_record = record.transition(JobState.WORKER_RESUMED)
        body = json.dumps(
            {
                "message_type": "TASK",
                "task_eligible": True,
                "status": "DELEGATED",
                "director_state": resumed_record.state,
                "invocation_result": InvocationResult.DELEGATED,
                "job_id": record.job_id,
                "decision_id": record.decision_id,
                "invocation_id": replay["child_invocation_id"],
                "parent_invocation_id": replay["child_parent_invocation_id"],
                "root_invocation_id": replay["child_root_invocation_id"],
                "trigger_mail_uid": replay["child_trigger_mail_uid"],
                "answer": replay["answer"],
                "action": "ANSWER",
                "reason": replay["reason"],
                "context_packet": packet,
                "instruction": "Start a new CLI context. Do not repeat the answered question. Use the mandatory reply_protocol commands for ACK and terminal COMPLETED/FAILED; never handcraft terminal mail. Send WAITING_FOR_DECISION only if strictly necessary.",
                "reply_protocol": self._worker_reply_protocol(record),
            },
            ensure_ascii=False,
        )
        delegated_mail_uid = self._send(
            record,
            record.current_agent_uid,
            record.decision_id,
            f"[{record.job_id}] [{record.decision_id}] [{replay['child_invocation_id']}] ANSWER RESUME",
            body,
        )
        replay["delegated_mail_uid"] = delegated_mail_uid
        record.latest_invocation_id = self.invocation_id
        record.latest_invocation_result = InvocationResult.DELEGATED
        record.expected_worker_parent_invocation_id = str(
            replay["child_invocation_id"]
        )
        record.expected_worker_trigger_mail_uid = delegated_mail_uid
        record.active_worker_invocation_id = ""
        resumed_record.latest_invocation_id = record.latest_invocation_id
        resumed_record.latest_invocation_result = record.latest_invocation_result
        resumed_record.expected_worker_parent_invocation_id = (
            record.expected_worker_parent_invocation_id
        )
        resumed_record.expected_worker_trigger_mail_uid = delegated_mail_uid
        resumed_record.active_worker_invocation_id = ""
        resumed_record.invocation_replay_results = dict(
            record.invocation_replay_results
        )
        # The child can reply immediately. Persist its new correlation before
        # attempting the separate terminal result for this Director launch.
        self.jobs.save(resumed_record)
        self._log(
            "resume_context_packet",
            job_id=record.job_id,
            decision_id=record.decision_id,
            path=packet,
            bytes=size,
            estimated_tokens=tokens,
        )
        return resumed_record

    def _fail_invocation(
        self, record: JobRecord, message: dict, reason: str
    ) -> JobRecord:
        """Fail only the current Director invocation and preserve workflow state."""

        mail_key = str(int(message["mail_id"]))
        reason = record.rejected_mail_reasons.setdefault(mail_key, reason)
        correlation_decision_id = self._failure_correlation_decision(
            record, message
        )
        payload = {
            "message_type": "INVOCATION_RESULT",
            "task_eligible": False,
            "status": "FAILED",
            "director_state": record.state,
            "invocation_result": InvocationResult.FAILED,
            "job_id": record.job_id,
            "decision_id": correlation_decision_id,
            **self._invocation_metadata(record),
            "reason": reason,
        }
        result_decision_id = (
            f"{correlation_decision_id}-FAILED-{int(message['mail_id'])}-{self.invocation_id}"
        )
        record.result_mail_uid = self._send(
            record,
            message["sender_uid"],
            result_decision_id,
            f"[{record.job_id}] [{correlation_decision_id}] [{result_decision_id}] [{self.invocation_id}] STATUS: FAILED",
            json.dumps(payload, ensure_ascii=False),
        )
        record.latest_invocation_id = self.invocation_id
        record.latest_invocation_result = InvocationResult.FAILED
        if message["mail_id"] not in record.handled_mail_ids:
            record.handled_mail_ids.append(message["mail_id"])
        self.jobs.save(record)
        self._log(
            "invocation_rejected",
            job_id=record.job_id,
            mail_id=message.get("mail_id"),
            error=reason,
        )
        return record

    @staticmethod
    def _failure_correlation_decision(
        record: JobRecord, message: dict
    ) -> str:
        """Recover an old Decision only from a complete persisted identity.

        A contradictory delayed result must be rejected, not replayed. Its
        FAILED terminal still has to correlate with the invocation that sent
        it. An exact persisted event supplies that identity even when its
        sender is what failed validation. For a semantically contradictory
        event, status/message type are excluded while the sender and complete
        immutable Invocation lineage remain mandatory.
        """

        exact_replay = record.invocation_replay_results.get(
            DirectorEngine._replay_key_for_message(message)
        )
        if exact_replay is not None:
            return str(exact_replay["decision_id"])
        try:
            payload = json.loads(message.get("body", ""))
        except (TypeError, json.JSONDecodeError):
            return record.decision_id
        if not isinstance(payload, dict) or payload.get("job_id") != record.job_id:
            return record.decision_id
        inbound_identity = {
            "source_sender_uid": message.get("sender_uid"),
            "decision_id": payload.get("decision_id"),
            "source_invocation_id": payload.get("invocation_id"),
            "source_parent_invocation_id": payload.get("parent_invocation_id"),
            "source_root_invocation_id": payload.get("root_invocation_id"),
            "source_trigger_mail_uid": payload.get("trigger_mail_uid"),
        }
        for replay in record.invocation_replay_results.values():
            persisted_identity = {
                name: replay.get(name) for name in inbound_identity
            }
            if inbound_identity == persisted_identity:
                return str(replay["decision_id"])
        return record.decision_id

    def _validate_inbound_metadata(
        self, record: JobRecord, message: dict
    ) -> dict[str, object] | None:
        """Validate structured lineage before mutating persistent state.

        The first human request may remain plain text for compatibility. Once a
        job exists, worker and commander messages are machine-to-machine
        results, so their JSON metadata is the canonical routing source.
        """

        internal_sender = message.get("sender_uid") in {
            record.current_agent_uid,
            record.commander_uid,
        }
        strict_internal = internal_sender and self.trigger_mail_uid is not None
        try:
            payload = json.loads(message.get("body", ""))
        except (TypeError, json.JSONDecodeError) as err:
            if strict_internal:
                raise DirectorError(
                    "internal agent result body must be a JSON object"
                ) from err
            return None
        if not isinstance(payload, dict):
            if strict_internal:
                raise DirectorError(
                    "internal agent result body must be a JSON object"
                )
            return None
        has_lineage = any(
            key in payload
            for key in (
                "invocation_id",
                "parent_invocation_id",
                "root_invocation_id",
                "trigger_mail_uid",
            )
        )
        if not has_lineage:
            if strict_internal:
                raise DirectorError(
                    "internal agent result is missing Invocation metadata"
                )
            return payload
        try:
            from .state_machine import validate_invocation_id
        except ImportError:
            from state_machine import validate_invocation_id
        inbound_invocation = payload.get("invocation_id")
        validate_invocation_id(inbound_invocation)
        if inbound_invocation != self.parent_invocation_id:
            raise DirectorError("inbound invocation_id does not match launch parent")
        inbound_root = payload.get("root_invocation_id")
        validate_invocation_id(inbound_root)
        if inbound_root != self.root_invocation_id:
            raise DirectorError("inbound root_invocation_id does not match launch root")
        inbound_parent = payload.get("parent_invocation_id")
        if inbound_parent is not None:
            validate_invocation_id(inbound_parent)
        inbound_trigger = payload.get("trigger_mail_uid")
        if (
            isinstance(inbound_trigger, bool)
            or not isinstance(inbound_trigger, int)
            or inbound_trigger <= 0
        ):
            raise DirectorError("inbound trigger_mail_uid must be positive")
        if payload.get("job_id") != record.job_id:
            raise DirectorError("inbound job_id does not match Director job")
        replay = self._replay_for_payload(record, payload)
        replay_decision_id = replay.get("decision_id") if replay else None
        inbound_decision_id = payload.get("decision_id")
        if (
            inbound_decision_id != record.decision_id
            and inbound_decision_id != replay_decision_id
        ):
            raise DirectorError("inbound decision_id does not match Director decision")
        if replay is not None:
            if message.get("sender_uid") != replay.get("source_sender_uid"):
                raise DirectorError(
                    "replayed result sender does not match persisted lineage"
                )
            persisted_lineage = {
                "invocation_id": replay.get("source_invocation_id"),
                "parent_invocation_id": replay.get(
                    "source_parent_invocation_id"
                ),
                "root_invocation_id": replay.get("source_root_invocation_id"),
                "trigger_mail_uid": replay.get("source_trigger_mail_uid"),
            }
            inbound_lineage = {
                "invocation_id": inbound_invocation,
                "parent_invocation_id": inbound_parent,
                "root_invocation_id": inbound_root,
                "trigger_mail_uid": inbound_trigger,
            }
            if inbound_lineage != persisted_lineage:
                raise DirectorError(
                    "replayed result does not match persisted Invocation lineage"
                )
        if strict_internal:
            message_type = payload.get("message_type")
            if not isinstance(message_type, str) or not message_type:
                raise DirectorError("internal agent result is missing message_type")
            if payload.get("task_eligible") is not True:
                raise DirectorError("internal agent result is not task eligible")
            invocation_result = payload.get("invocation_result")
            if invocation_result is not None and invocation_result not in {
                InvocationResult.COMPLETED,
                InvocationResult.DELEGATED,
                InvocationResult.WAITING,
                InvocationResult.FAILED,
            }:
                raise DirectorError("internal agent result has invalid InvocationResult")
            if replay is not None:
                expected_parent = replay.get("source_parent_invocation_id")
                expected_trigger = replay.get("source_trigger_mail_uid")
                active_invocation = replay.get("source_invocation_id")
            elif message.get("sender_uid") == record.current_agent_uid:
                expected_parent = record.expected_worker_parent_invocation_id
                expected_trigger = record.expected_worker_trigger_mail_uid
                active_invocation = record.active_worker_invocation_id
            else:
                expected_parent = record.expected_commander_parent_invocation_id
                expected_trigger = record.expected_commander_trigger_mail_uid
                active_invocation = record.active_commander_invocation_id
            if not expected_parent or expected_trigger <= 0:
                raise DirectorError(
                    "no issued child task matches the internal agent result"
                )
            if inbound_parent != expected_parent:
                raise DirectorError(
                    "inbound parent_invocation_id does not match issued child task"
                )
            if inbound_trigger != expected_trigger:
                raise DirectorError(
                    "inbound trigger_mail_uid does not match issued child task"
                )
            if active_invocation and inbound_invocation != active_invocation:
                raise DirectorError(
                    "inbound invocation_id does not match active child Invocation"
                )
        return payload

    @staticmethod
    def _inbound_event_key(payload: dict[str, object]) -> str:
        """Return a stable key used to suppress duplicate result mails."""

        fields = {
            "job_id": payload.get("job_id"),
            "decision_id": payload.get("decision_id"),
            "invocation_id": payload.get("invocation_id"),
            "parent_invocation_id": payload.get("parent_invocation_id"),
            "root_invocation_id": payload.get("root_invocation_id"),
            "trigger_mail_uid": payload.get("trigger_mail_uid"),
            "message_type": payload.get("message_type"),
            "task_eligible": payload.get("task_eligible"),
            "invocation_result": payload.get("invocation_result"),
            "status": payload.get("status"),
        }
        return json.dumps(fields, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _replay_key_for_message(cls, message: dict) -> str:
        try:
            payload = json.loads(message.get("body", ""))
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("invocation_id"), str):
            return f"event:{cls._inbound_event_key(payload)}"
        return f"mail:{int(message['mail_id'])}"

    @classmethod
    def _replay_for_payload(
        cls, record: JobRecord, payload: dict[str, object]
    ) -> dict[str, object] | None:
        return record.invocation_replay_results.get(
            f"event:{cls._inbound_event_key(payload)}"
        )

    @staticmethod
    def _control_payload(message: dict) -> dict[str, object] | None:
        try:
            payload = json.loads(message.get("body", ""))
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("message_type") == "SYSTEM_ALERT":
            return payload
        if payload.get("task_eligible") is False:
            return payload
        return None

    def _ignore_control_notification(
        self, message: dict, payload: dict[str, object]
    ) -> JobRecord | None:
        """Record a control mail without creating or transitioning a Job."""

        record = None
        job_id = payload.get("job_id")
        if isinstance(job_id, str):
            try:
                validate_job_id(job_id)
            except StateError:
                job_id = None
            else:
                record = self.jobs.load(job_id)
        mail_id = int(message["mail_id"])
        duplicate = bool(record and mail_id in record.handled_mail_ids)
        if record is not None and not duplicate:
            record.handled_mail_ids.append(mail_id)
            self.jobs.save(record)
        self._log(
            "control_notification_ignored",
            job_id=job_id,
            mail_id=mail_id,
            message_type=payload.get("message_type"),
            known_job=record is not None,
            duplicate=duplicate,
        )
        return record

    def _ack(self, record: JobRecord, recipient: str) -> None:
        ack_decision = f"{record.decision_id}-ACK"
        subject = f"[{record.job_id}] [{ack_decision}] [{self.invocation_id}] STATUS: ACK"
        payload = {
            "message_type": "INVOCATION_ACK",
            "task_eligible": False,
            "status": "ACK_RECEIVED",
            "job_id": record.job_id,
            "decision_id": record.decision_id,
            **self._invocation_metadata(record),
        }
        self._send(
            record,
            recipient,
            ack_decision,
            subject,
            json.dumps(payload, ensure_ascii=False),
        )

    def _save_worker_wait_checkpoint(self, record: JobRecord) -> str:
        path = self.root / "director" / "checkpoints" / record.job_id / f"{record.decision_id}-worker-wait.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "job_id": record.job_id,
            "decision_id": record.decision_id,
            "invocation_id": self.invocation_id,
            "state": JobState.WAITING_FOR_WORKER,
            "delegate_mail_id": record.delegate_mail_id,
            "next_step": "workerからのInvocationを待つ",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(path.relative_to(self.root))

    def _waiting_for_worker(self, record: JobRecord) -> JobRecord:
        record = record.transition(JobState.WAITING_FOR_WORKER)
        record.latest_invocation_id = self.invocation_id
        record.latest_checkpoint = self._save_worker_wait_checkpoint(record)
        self.jobs.save(record)
        body = json.dumps({
            "message_type": "INVOCATION_RESULT",
            "task_eligible": False,
            "status": "WAITING_FOR_WORKER",
            "director_state": JobState.WAITING_FOR_WORKER,
            "invocation_result": InvocationResult.WAITING,
            "job_id": record.job_id,
            "decision_id": record.decision_id,
            **self._invocation_metadata(record),
            "delegate_mail_id": record.delegate_mail_id,
            "checkpoint": record.latest_checkpoint,
            "summary": "workerへ委任済み。Jobは非終端でworkerの応答を待つ。",
        }, ensure_ascii=False)
        result_mail_uid = self._send(record, record.requester_uid, f"{record.decision_id}-WAITING-FOR-WORKER", f"[{record.job_id}] [{record.decision_id}] [{self.invocation_id}] STATUS: WAITING_FOR_WORKER", body)
        record.latest_invocation_result = InvocationResult.WAITING
        record.result_mail_uid = result_mail_uid
        self.jobs.save(record)
        return record

    def _create_record(self, message: dict) -> JobRecord:
        job_id = _id(JOB_RE, message["subject"], "Job-ID")
        validate_job_id(job_id)
        existing = self.jobs.load(job_id)
        if existing:
            return existing
        decision_id = _id(DEC_RE, message["subject"], "Decision-ID")
        record = JobRecord(
            job_id,
            message["mail_id"],
            message["sender_uid"],
            self.uids["worker"],
            self.uids["commander"],
            decision_id,
            JobState.DISCOVERED,
            request_summary=message.get("body", "")[:4000],
            latest_invocation_id=self.invocation_id,
            parent_invocation_id=self.parent_invocation_id,
            root_invocation_id=self.root_invocation_id,
            trigger_mail_uid=self.trigger_mail_uid or 0,
        )
        self.jobs.save(record)
        return record

    @staticmethod
    def _worker_reply_protocol(record: JobRecord) -> dict[str, object]:
        """Return the mandatory product reporter contract for worker replies."""

        result_prefix = (
            f"director/runtime/agent-results/{record.job_id}/{record.decision_id}"
        )
        complete_path = f"{result_prefix}-complete.json"
        wait_path = f"{result_prefix}-wait.json"
        fail_path = f"{result_prefix}-fail.json"
        return {
            "mandatory": True,
            "prohibition": (
                "Do not call mail.send_mail or construct ACK/WAITING/COMPLETED/FAILED "
                "mail JSON manually. Use director/agent_reply.py for every status reply."
            ),
            "ack_command": "py -3 director/agent_reply.py ack",
            "result_directory": f"director/runtime/agent-results/{record.job_id}",
            "complete_result_file": complete_path,
            "wait_result_file": wait_path,
            "fail_result_file": fail_path,
            "wait_command": f"py -3 director/agent_reply.py wait --result-file {wait_path}",
            "complete_command": f"py -3 director/agent_reply.py complete --result-file {complete_path}",
            "fail_command": f"py -3 director/agent_reply.py fail --result-file {fail_path}",
            "complete_result_example": {
                "status": "COMPLETED",
                "job_id": record.job_id,
                "decision_id": record.decision_id,
                "artifacts": [
                    {
                        "path": "replace-with-actual-project-relative-artifact-path",
                        "sha256": "replace-with-actual-lowercase-sha256",
                    }
                ],
            },
            "wait_result_example": {
                "status": "WAITING_FOR_DECISION",
                "job_id": record.job_id,
                "decision_id": record.decision_id,
                "qanda_ids": ["replace-with-Q-number"],
                "summary": "replace-with-blocking-question-summary",
                "checkpoint": "replace-with-existing-project-relative-checkpoint",
                "qanda_path": "QandA.md",
            },
            "fail_result_example": {
                "status": "FAILED",
                "job_id": record.job_id,
                "decision_id": record.decision_id,
                "reason": "replace-with-failure-reason",
                "artifacts": [],
            },
            "completion_condition": (
                "The chosen agent_reply.py command must finish with exit code 0; "
                "otherwise the task is not reported complete."
            ),
            "terminal_contract": (
                "agent_reply.py emits message_type=INVOCATION_RESULT and "
                "task_eligible=true so Director is launched. A COMPLETED result file "
                "must contain artifacts as a list of {path, sha256} objects."
            ),
        }

    def _delegate(self, record: JobRecord, message: dict) -> JobRecord:
        self._ack(record, record.requester_uid)
        record = record.transition(JobState.ACK_SENT)
        self.jobs.save(record)
        record = record.transition(JobState.DELEGATION_PENDING)
        body = json.dumps(
            {
                "message_type": "TASK",
                "task_eligible": True,
                "job_id": record.job_id,
                "decision_id": record.decision_id,
                **self._invocation_metadata(record),
                "role": "claude_worker",
                "task": "execute the requested work and report questions or completion",
                "required": "send ACK and a terminal COMPLETED/FAILED mail through the mandatory product reporter",
                "reply_protocol": self._worker_reply_protocol(record),
                "prohibitions": "do not modify director code, config, or SPEC; do not commit or push",
                "blocking_question": "send JSON with message_type=QUESTION, task_eligible=true, Invocation lineage, and the Q-number",
                "original_request_summary": message.get("body", "")[:4000],
            },
            ensure_ascii=False,
        )
        record.delegate_mail_id = self._send(record, record.current_agent_uid, record.decision_id, f"[{record.job_id}] [{record.decision_id}] [{self.invocation_id}] DELEGATE", body)
        record.expected_worker_parent_invocation_id = self.invocation_id
        record.expected_worker_trigger_mail_uid = record.delegate_mail_id
        record.active_worker_invocation_id = ""
        record = self._waiting_for_worker(record)
        record.round_count += 1
        self.jobs.save(record)
        return record

    def _build_packet(self, record: JobRecord, question: str) -> tuple[str, int, int]:
        path = self.root / "director" / "checkpoints" / record.job_id / f"{record.decision_id}-context.md"
        knowledge_pages = self.knowledge.select(question)
        result = self.packet_builder.build(
            record.job_id, record.decision_id, role="codex_commander", task=question, state=record.state,
            completed=[f"request mail {record.request_mail_id} received", "worker delegation sent", f"Original request: {record.request_summary[:2000]}"],
            unresolved=[question], qanda=self._packet_qanda(question), spec_sections=["director/SPEC.md §10", "director/SPEC.md §11"],
            target_files=record.artifact_paths, git_summary="git diff summary is intentionally omitted unless supplied by the worker",
            test_summary="latest worker test result is pending", prohibitions=["no director self-change", "no commit or push"],
            reply_commands=["send ACK", "send one valid decision JSON and terminal status"], completion="Return a valid decision JSON with matching Job-ID and Decision-ID.", path=path,
            knowledge_pages=knowledge_pages,
        )
        record.latest_checkpoint = str(path.relative_to(self.root))
        self.jobs.save(record)
        return record.latest_checkpoint, result.byte_size, result.estimated_tokens

    def _packet_qanda(self, current_question: str) -> list[str]:
        items = [current_question]
        try:
            for question in parse_qanda(self.qanda_path):
                if question.number == "Q006" and question.status == "ANSWERED":
                    items.append(f"Q006 Question: {question.fields.get('Question', '')} / Decision: {question.fields.get('Decision', '')}")
        except QandaError:
            pass
        return items

    def _request_decision(self, record: JobRecord, question: str) -> JobRecord:
        packet, size, tokens = self._build_packet(record, question)
        record = record.transition(JobState.DECISION_PENDING)
        record.decision_count += 1
        record.latest_invocation_id = self.invocation_id
        record.parent_invocation_id = self.parent_invocation_id
        record.root_invocation_id = self.root_invocation_id
        record.trigger_mail_uid = self.trigger_mail_uid or record.trigger_mail_uid
        self.jobs.save(record)
        body = json.dumps({
            "message_type": "DECISION_REQUEST",
            "task_eligible": True,
            "status": "DELEGATED",
            "director_state": JobState.DECISION_PENDING,
            "invocation_result": InvocationResult.DELEGATED,
            "job_id": record.job_id,
            "decision_id": record.decision_id,
            **self._invocation_metadata(record),
            "context_packet": packet, "question": question,
            "allowed_actions": ["ANSWER", "CONTINUE", "REVISE", "DELEGATE", "COMPLETE", "HUMAN_REQUIRED", "REJECT"],
            "answer_json": "{action, job_id, decision_id, confidence, reason, answer, target_agent, requires_human}",
            "required": "ACK and terminal notification are mandatory.",
        }, ensure_ascii=False)
        result_mail_uid = self._send(
            record,
            record.commander_uid,
            record.decision_id,
            f"[{record.job_id}] [{record.decision_id}] [{self.invocation_id}] DECISION_REQUEST",
            body,
        )
        record.latest_invocation_result = InvocationResult.DELEGATED
        record.result_mail_uid = result_mail_uid
        record.expected_commander_parent_invocation_id = self.invocation_id
        record.expected_commander_trigger_mail_uid = result_mail_uid
        record.active_commander_invocation_id = ""
        self.jobs.save(record)
        self._log("context_packet", job_id=record.job_id, decision_id=record.decision_id, path=packet, bytes=size, estimated_tokens=tokens)
        return record

    def _handle_waiting(self, record: JobRecord, message: dict) -> JobRecord:
        if record.state == JobState.WORKER_RUNNING:
            record = record.transition(JobState.WORKER_WAITING_QUESTION)
        if record.state == JobState.WORKER_WAITING_QUESTION:
            record = record.transition(JobState.WAITING_FOR_DECISION)
        try:
            payload = json.loads(message.get("body", ""))
            checkpoint = payload.get("checkpoint")
            if isinstance(checkpoint, str) and not Path(checkpoint).is_absolute() and (self.root / checkpoint).resolve().is_file():
                (self.root / checkpoint).resolve().relative_to(self.root)
                record.latest_checkpoint = checkpoint
        except (json.JSONDecodeError, ValueError, TypeError) as err:
            self._log(
                "worker_checkpoint_ignored",
                job_id=record.job_id,
                mail_id=message.get("mail_id"),
                error=type(err).__name__,
            )
        self.jobs.save(record)
        return record

    def _handle_timeout(self, record: JobRecord, message: dict) -> JobRecord:
        self._log("worker_timeout", job_id=record.job_id, mail_id=message.get("mail_id"), status="TIMED_OUT")
        return self._human_required(record, "作業AIのCLIタイムアウト通知を受信しました。質問メールが併存していても自動再開しません。")

    def _human_required(self, record: JobRecord, reason: str) -> JobRecord:
        if record.state not in TERMINAL_STATES:
            record = record.transition(JobState.HUMAN_REQUIRED)
        record.latest_invocation_id = self.invocation_id
        self.jobs.save(record)
        body = json.dumps(
            {
                "message_type": "HUMAN_REQUIRED",
                "task_eligible": True,
                "status": "HUMAN_REQUIRED",
                "director_state": record.state,
                "invocation_result": InvocationResult.FAILED,
                "job_id": record.job_id,
                "decision_id": record.decision_id,
                **self._invocation_metadata(record),
                "reason": reason,
            },
            ensure_ascii=False,
        )
        try:
            record.result_mail_uid = self._send(record, self.uids["human"], f"{record.decision_id}-HUMAN", f"[{record.job_id}] [{record.decision_id}] [{self.invocation_id}] HUMAN_REQUIRED", body)
        except Exception as err:
            self._log("human_notify_failed", job_id=record.job_id, error=type(err).__name__)
            raise
        record.latest_invocation_result = InvocationResult.FAILED
        self.jobs.save(record)
        return record

    def _handle_question(self, record: JobRecord, message: dict) -> JobRecord:
        record = record.transition(JobState.WORKER_WAITING_QUESTION)
        self.jobs.save(record)
        try:
            all_questions = parse_qanda(self.qanda_path)
            questions = [q for q in all_questions if q.status == "OPEN" and q.blocking and q.fields["Request-ID"] == record.job_id]
        except QandaError as err:
            return self._human_required(record, f"QandA解析不能: {err}")
        if not questions:
            question_id = re.search(r"\b(Q[0-9]{3,})\b", message.get("subject", "") + " " + message.get("body", ""))
            answered = next((q for q in all_questions if question_id and q.number == question_id.group(1) and q.status == "ANSWERED"), None)
            if answered and answered.fields.get("Decision"):
                return self._resume_with_answer(
                    record,
                    answered.fields["Decision"],
                    answered.fields.get("Reason", "既存のANSWERED回答を再利用"),
                    source_message=message,
                )
            return self._human_required(record, "質問に対応するOPEN Blocking Q&Aがありません")
        question_record = questions[0]
        reused = find_answered_reuse(all_questions, question_record)
        if reused and reused.fields.get("Decision"):
            decision = "成果物は、指定された文字列の一行を末尾改行なしで作成する。"
            reason = "同一内容のQ006に対する確定済み回答を再利用した。"
            answer_question(self.qanda_path, question_record.number, decision=decision, reason=reason, answered_by="director", reused_from=reused.number)
            self._log(
                "qanda_reused",
                job_id=record.job_id,
                decision_id=record.decision_id,
                question_id=question_record.number,
                reused_from=reused.number,
                normalized_signature=normalized_reuse_signature(question_record),
                reason="Category, target terms, proposed answer, and confirmed answer matched the fixed artifact protocol",
            )
            return self._resume_with_answer(
                record, decision, reason, source_message=message
            )
        question = question_record.fields["Question"] if questions else message.get("body", "")[:2000]
        return self._request_decision(record, question)

    def _resume_with_answer(
        self,
        record: JobRecord,
        answer: str,
        reason: str,
        *,
        source_message: dict,
    ) -> JobRecord:
        origin_decision_id = record.decision_id
        new_decision_id = _new_decision_id(record.job_id, record.decision_count + 1)
        replay_key = self._replay_key_for_message(source_message)
        replay = {
            "invocation_result": InvocationResult.DELEGATED,
            "director_state": JobState.WORKER_RESUMED,
            "decision_id": origin_decision_id,
            "delegated_mail_uid": 0,
            "next_decision_id": new_decision_id,
            "answer": answer,
            "reason": reason,
            "child_invocation_id": self.invocation_id,
            "child_parent_invocation_id": self.parent_invocation_id,
            "child_root_invocation_id": self.root_invocation_id,
            "child_trigger_mail_uid": (
                self.trigger_mail_uid or record.trigger_mail_uid
            ),
            **self._resume_replay_lineage(source_message),
        }
        record.invocation_replay_results[replay_key] = replay
        # Persist the operation intent before sending the child. If the process
        # stops after SMTP/DB commit but before the state save, the child outbox
        # and this intent make the resume transaction recoverable.
        self.jobs.save(record)
        resumed_record = self._continue_resume_delegation(record, replay)
        delegated_mail_uid = int(replay["delegated_mail_uid"])
        result_payload = json.dumps(
            {
                "message_type": "INVOCATION_RESULT",
                "task_eligible": False,
                "status": "DELEGATED",
                "director_state": resumed_record.state,
                "invocation_result": InvocationResult.DELEGATED,
                "job_id": record.job_id,
                "decision_id": origin_decision_id,
                **self._invocation_metadata(record),
                "delegated_mail_uid": delegated_mail_uid,
                "next_decision_id": new_decision_id,
                "reason": "answer recorded and worker resume task issued",
            },
            ensure_ascii=False,
        )
        result_decision_id = (
            f"{origin_decision_id}-RESULT-{int(source_message['mail_id'])}-{self.invocation_id}"
        )
        record.result_mail_uid = self._send(
            record,
            source_message["sender_uid"],
            result_decision_id,
            f"[{record.job_id}] [{origin_decision_id}] [{result_decision_id}] [{self.invocation_id}] STATUS: DELEGATED",
            result_payload,
        )
        resumed_record.result_mail_uid = record.result_mail_uid
        resumed_record.latest_invocation_id = self.invocation_id
        resumed_record.latest_invocation_result = InvocationResult.DELEGATED
        self.jobs.save(resumed_record)
        return resumed_record

    def _handle_decision(self, record: JobRecord, message: dict) -> JobRecord:
        try:
            decision = parse_decision(message.get("body", ""), expected_job_id=record.job_id, expected_decision_id=record.decision_id)
        except DecisionError as err:
            return self._human_required(record, f"判断JSON検証失敗: {err}")
        record = record.transition(JobState.DECISION_RECEIVED)
        self.jobs.save(record)
        if decision.requires_human or decision.action in {"HUMAN_REQUIRED", "REJECT"}:
            return self._human_required(record, decision.reason)
        if decision.action == "COMPLETE":
            return self._human_required(record, "指揮AIのCOMPLETEは成果物と終端通知の検証前のため、人間確認が必要です")
        if decision.action == "HUMAN_REQUIRED":
            return self._human_required(record, decision.reason)
        if decision.action == "ANSWER":
            try:
                open_questions = find_open_blocking(self.qanda_path, record.job_id)
                if open_questions:
                    answer_question(self.qanda_path, open_questions[0].number, decision=decision.answer, reason=decision.reason)
            except QandaError as err:
                return self._human_required(record, f"QandA更新失敗: {err}")
        return self._resume_with_answer(
            record,
            decision.answer,
            decision.reason,
            source_message=message,
        )

    def _handle_completion(self, record: JobRecord, message: dict) -> JobRecord:
        body = message.get("body", "")
        if "COMPLETED" not in message.get("subject", "") and '"status": "COMPLETED"' not in body:
            return self._human_required(record, "ファイル変更または非終端通知のみで、COMPLETEDが確認できません")
        try:
            payload = json.loads(body)
            if payload.get("status") != "COMPLETED":
                raise ValueError("completion status is not COMPLETED")
            artifacts = payload.get("artifacts", [])
            if not isinstance(artifacts, list):
                raise ValueError("artifacts must be a list")
            checked: list[str] = []
            for artifact in artifacts:
                if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str) or not isinstance(artifact.get("sha256"), str):
                    raise ValueError("artifact entry is invalid")
                relative = PurePosixPath(artifact["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("artifact path traversal rejected")
                path = (self.root / Path(*relative.parts)).resolve()
                path.relative_to(self.root)
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise ValueError("artifact missing or SHA-256 mismatch")
                checked.append(artifact["path"])
            record.artifact_paths = checked
        except (ValueError, json.JSONDecodeError) as err:
            return self._human_required(record, f"成果物検証失敗: {err}")
        record = record.transition(JobState.VERIFYING)
        self.jobs.save(record)
        result_payload = dict(payload)
        result_payload.update(
            {
                "status": "COMPLETED",
                "message_type": "INVOCATION_RESULT",
                "task_eligible": False,
                "director_state": JobState.COMPLETED,
                "invocation_result": InvocationResult.COMPLETED,
                "job_id": record.job_id,
                "decision_id": record.decision_id,
                **self._invocation_metadata(record),
            }
        )
        record.result_mail_uid = self._send(
            record,
            record.requester_uid,
            f"{record.decision_id}-FINAL",
            f"[{record.job_id}] [{record.decision_id}] [{self.invocation_id}] STATUS: COMPLETED",
            json.dumps(result_payload, ensure_ascii=False),
        )
        record.latest_invocation_id = self.invocation_id
        record.latest_invocation_result = InvocationResult.COMPLETED
        record = record.transition(JobState.COMPLETED)
        self.jobs.save(record)
        return record

    def _process(self, message: dict) -> JobRecord | None:
        control_payload = self._control_payload(message)
        if control_payload is not None:
            return self._ignore_control_notification(message, control_payload)

        record = self._create_record(message)
        if message["mail_id"] in record.handled_mail_ids:
            rejected_reason = record.rejected_mail_reasons.get(
                str(int(message["mail_id"]))
            )
            if rejected_reason:
                return self._fail_invocation(record, message, rejected_reason)
            replay = record.invocation_replay_results.get(
                self._replay_key_for_message(message)
            )
            if replay is not None:
                return self._replay_invocation_result(record, message, replay)
            return self._complete_noop(record, message, "duplicate trigger already handled")

        # Validate correlation before changing any persistent lineage fields.
        payload = self._validate_inbound_metadata(record, message)
        sender = message["sender_uid"]
        internal_sender = sender in {record.current_agent_uid, record.commander_uid}
        event_key = ""
        if internal_sender and payload is not None and payload.get("invocation_id"):
            event_key = self._inbound_event_key(payload)
        replay = record.invocation_replay_results.get(
            self._replay_key_for_message(message)
        )
        if replay is not None:
            record = self._continue_resume_delegation(record, replay)
            result = self._replay_invocation_result(record, message, replay)
            if event_key:
                current_mail_uid = int(message["mail_id"])
                prior_mail_uid = result.inbound_result_mail_uids.get(event_key)
                result.inbound_result_mail_uids[event_key] = (
                    current_mail_uid
                    if prior_mail_uid is None
                    else min(prior_mail_uid, current_mail_uid)
                )
            if message["mail_id"] not in result.handled_mail_ids:
                result.handled_mail_ids.append(message["mail_id"])
            self.jobs.save(result)
            return result
        if internal_sender and payload is not None and payload.get("invocation_id"):
            canonical_mail_uid = record.inbound_result_mail_uids.get(event_key)
            if canonical_mail_uid is not None:
                canonical_mail_uid = min(canonical_mail_uid, int(message["mail_id"]))
                record.inbound_result_mail_uids[event_key] = canonical_mail_uid
                self._log(
                    "duplicate_invocation_result",
                    job_id=record.job_id,
                    mail_id=message["mail_id"],
                    canonical_mail_uid=canonical_mail_uid,
                    inbound_invocation_id=payload.get("invocation_id"),
                )
                replay = self._replay_for_payload(record, payload)
                result = (
                    self._replay_invocation_result(record, message, replay)
                    if replay is not None
                    else self._complete_noop(
                        record,
                        message,
                        "duplicate Invocation result ignored; canonical result retained",
                    )
                )
                if message["mail_id"] not in result.handled_mail_ids:
                    result.handled_mail_ids.append(message["mail_id"])
                    self.jobs.save(result)
                return result

        record.latest_invocation_id = self.invocation_id
        record.parent_invocation_id = self.parent_invocation_id
        record.root_invocation_id = self.root_invocation_id
        record.trigger_mail_uid = self.trigger_mail_uid or int(message["mail_id"])
        if internal_sender and payload is not None and payload.get("invocation_id"):
            record.last_inbound_invocation_id = str(payload["invocation_id"])
            if sender == record.current_agent_uid and not record.active_worker_invocation_id:
                record.active_worker_invocation_id = str(payload["invocation_id"])
            if sender == record.commander_uid and not record.active_commander_invocation_id:
                record.active_commander_invocation_id = str(payload["invocation_id"])
        self.jobs.save(record)

        if record.state in TERMINAL_STATES:
            result = self._complete_noop(
                record, message, "late result ignored; Director state is terminal"
            )
        elif record.state == JobState.DISCOVERED:
            result = self._delegate(record, message)
        elif internal_sender and (
            payload is None or not isinstance(payload.get("message_type"), str)
        ):
            # Explicit manual/compatibility mode only. Orchestrated invocations
            # always carry AI_TRIGGER_MAIL_UID and are rejected above unless
            # their structured routing metadata is complete.
            subject = message.get("subject", "")
            body = message.get("body", "")
            if "[TIMEOUT]" in subject or "status: TIMED_OUT" in body:
                result = self._handle_timeout(record, message)
            elif sender == record.current_agent_uid and (
                "QUESTION" in subject or "QANDA" in body
            ):
                result = self._handle_question(record, message)
            elif sender == record.current_agent_uid and (
                "WAITING_FOR_DECISION" in subject
                or '"status": "WAITING_FOR_DECISION"' in body
            ):
                result = self._handle_waiting(record, message)
                result = self._complete_noop(
                    result, message, "worker waiting state already recorded"
                )
            elif sender == record.commander_uid:
                if "STATUS: ACK" in subject or '"status": "ACK_RECEIVED"' in body:
                    result = self._complete_noop(record, message, "commander ACK recorded")
                else:
                    result = self._handle_decision(record, message)
            elif sender == record.current_agent_uid and "COMPLETE" in subject:
                result = self._handle_completion(record, message)
            else:
                result = self._complete_noop(
                    record, message, "no additional Director action required"
                )
        elif sender == record.current_agent_uid:
            assert payload is not None
            message_type = payload.get("message_type")
            invocation_result = payload.get("invocation_result")
            status = payload.get("status")
            if message_type == "QUESTION":
                if record.state in {
                    JobState.WAITING_FOR_WORKER,
                    JobState.WORKER_RUNNING,
                    JobState.WORKER_RESUMED,
                }:
                    result = self._handle_question(record, message)
                else:
                    result = self._complete_noop(
                        record, message, "late worker question ignored for current state"
                    )
            elif invocation_result == InvocationResult.WAITING or status in {
                "WAITING",
                "WAITING_FOR_DECISION",
                "WAITING_FOR_WORKER",
            }:
                result = self._handle_waiting(record, message)
                result = self._complete_noop(
                    result, message, "worker waiting state already recorded"
                )
            elif invocation_result == InvocationResult.COMPLETED or status == "COMPLETED":
                result = self._handle_completion(record, message)
            elif invocation_result == InvocationResult.FAILED or status in {
                "FAILED",
                "HUMAN_REQUIRED",
                "REJECTED",
                "CANCELLED",
            }:
                result = self._human_required(
                    record, "作業AIが構造化された失敗結果を返しました"
                )
            else:
                result = self._complete_noop(
                    record, message, "unsupported worker result ignored"
                )
        elif sender == record.commander_uid:
            assert payload is not None
            if payload.get("message_type") == "INVOCATION_ACK":
                result = self._complete_noop(record, message, "commander ACK recorded")
            else:
                result = self._handle_decision(record, message)
        else:
            result = self._complete_noop(
                record, message, "no additional Director action required"
            )
        if internal_sender and result.state == JobState.HUMAN_REQUIRED:
            # Human visibility and invocation termination are separate duties.
            # Keep the human escalation above, then explicitly terminate the
            # Director launch back to the internal trigger sender.
            result = self._fail_invocation(
                result,
                message,
                "Director workflow requires human intervention",
            )
        if event_key:
            current_mail_uid = int(message["mail_id"])
            prior_mail_uid = result.inbound_result_mail_uids.get(event_key)
            result.inbound_result_mail_uids[event_key] = (
                current_mail_uid
                if prior_mail_uid is None
                else min(prior_mail_uid, current_mail_uid)
            )
        if message["mail_id"] not in result.handled_mail_ids:
            result.handled_mail_ids.append(message["mail_id"])
            self.jobs.save(result)
        return result

    def process_once(self) -> int:
        records = {record.job_id: record for record in self.jobs.list_records()}
        for entry in self.outbox.pending():
            try:
                self.outbox.recover(entry)
            except OutboxError:
                record = records.get(entry.job_id)
                if record:
                    self._human_required(record, "Outboxに同一Decision-IDの複数メールが存在します")
        if self.trigger_mail_uid is not None:
            messages = self.mail.receive_mail(
                self.uid, mail_id=self.trigger_mail_uid
            )
            if not messages:
                recovered = self.mail.find_mails(
                    recipient_uid=self.uid,
                    after_mail_id=max(self.trigger_mail_uid - 1, 0),
                    limit=1,
                )
                messages = [
                    message
                    for message in recovered
                    if int(message.get("mail_id", 0)) == self.trigger_mail_uid
                ]
            if not messages:
                raise DirectorError(
                    "AI_TRIGGER_MAIL_UID did not identify a Director mail"
                )
        else:
            messages = self._load_pending()
            messages.extend(self.mail.receive_mail(self.uid))
        unique: dict[int, dict] = {int(message["mail_id"]): message for message in messages}
        messages = [unique[key] for key in sorted(unique)]
        limit = (
            1
            if self.trigger_mail_uid is not None
            else int(self.config.get("max_mails_per_once", 5))
        )
        if limit <= 0:
            raise DirectorError("max_mails_per_once must be positive")
        if self.trigger_mail_uid is None:
            self._save_pending(messages[limit:])
        for message in messages[:limit]:
            try:
                self._process(message)
            except DirectorError as err:
                record = self._create_record(message)
                self._fail_invocation(record, message, f"相関検証失敗: {err}")
            except (StateError, ValueError) as err:
                record = self._create_record(message)
                self._human_required(record, f"安全停止: {err}")
        return min(len(messages), limit)

    def _load_pending(self) -> list[dict]:
        if not self.pending_path.is_file():
            return []
        data = json.loads(self.pending_path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise DirectorError("pending mail queue is invalid")
        return data

    def _save_pending(self, messages: list[dict]) -> None:
        if messages:
            self.pending_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        elif self.pending_path.exists():
            self.pending_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.once:
        parser.error("director supports --once only")
    root = Path(os.environ.get("PROJECT_PATH", Path(__file__).resolve().parents[1])).resolve()
    engine = DirectorEngine(root)
    count = engine.process_once()
    print(f"director: processed={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
