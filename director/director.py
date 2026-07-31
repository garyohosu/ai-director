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
    from .outbox import Outbox, OutboxEntry, OutboxError
    from .qanda import QandaError, answer_question, find_answered_reuse, find_open_blocking, normalized_reuse_signature, parse_qanda
    from .state_machine import JobRecord, JobState, JobStore, StateError, utc_now, validate_job_id
except ImportError:  # direct ``py director/director.py`` compatibility
    from context_packet import ContextPacketBuilder
    from knowledge import KnowledgeIndex
    from decision import DecisionError, parse_decision
    from outbox import Outbox, OutboxEntry, OutboxError
    from qanda import QandaError, answer_question, find_answered_reuse, find_open_blocking, normalized_reuse_signature, parse_qanda
    from state_machine import JobRecord, JobState, JobStore, StateError, utc_now, validate_job_id

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
        self.invocation_id = os.environ.get("INVOCATION_ID", "")
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

    def _ack(self, record: JobRecord, recipient: str) -> None:
        ack_decision = f"{record.decision_id}-ACK"
        subject = f"[{record.job_id}] [{ack_decision}] STATUS: ACK"
        self._send(record, recipient, ack_decision, subject, json.dumps({"status": "ACK_RECEIVED", "job_id": record.job_id, "decision_id": record.decision_id, "invocation_id": self.invocation_id}, ensure_ascii=False))

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
            "status": "WAITING_FOR_WORKER",
            "job_id": record.job_id,
            "decision_id": record.decision_id,
            "invocation_id": self.invocation_id,
            "delegate_mail_id": record.delegate_mail_id,
            "checkpoint": record.latest_checkpoint,
            "summary": "workerへ委任済み。Jobは非終端でworkerの応答を待つ。",
        }, ensure_ascii=False)
        self._send(record, record.requester_uid, f"{record.decision_id}-WAITING-FOR-WORKER", f"[{record.job_id}] [{record.decision_id}] [{self.invocation_id}] STATUS: WAITING_FOR_WORKER", body)
        return record

    def _create_record(self, message: dict) -> JobRecord:
        job_id = _id(JOB_RE, message["subject"], "Job-ID")
        validate_job_id(job_id)
        existing = self.jobs.load(job_id)
        if existing:
            return existing
        decision_id = _id(DEC_RE, message["subject"], "Decision-ID")
        record = JobRecord(job_id, message["mail_id"], message["sender_uid"], self.uids["worker"], self.uids["commander"], decision_id, JobState.DISCOVERED, request_summary=message.get("body", "")[:4000], latest_invocation_id=self.invocation_id)
        self.jobs.save(record)
        return record

    def _delegate(self, record: JobRecord, message: dict) -> JobRecord:
        self._ack(record, record.requester_uid)
        record = record.transition(JobState.ACK_SENT)
        self.jobs.save(record)
        record = record.transition(JobState.DELEGATION_PENDING)
        body = "\n".join([
            f"Job-ID: {record.job_id}", f"Decision-ID: {record.decision_id}",
            "Role: claude_worker", "Task: execute the requested work and report questions or completion.",
            "Required: send ACK and a terminal COMPLETED/FAILED/HUMAN_REQUIRED mail.",
            "Prohibitions: do not modify director code, config, or SPEC; do not commit or push.",
            "For a blocking question, send a mail containing QANDA and the Q-number.",
            "Original request summary: " + message.get("body", "")[:4000],
        ])
        record.delegate_mail_id = self._send(record, record.current_agent_uid, record.decision_id, f"[{record.job_id}] [{record.decision_id}] [{self.invocation_id}] DELEGATE", body)
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
        body = json.dumps({
            "type": "DECISION_REQUEST", "job_id": record.job_id, "decision_id": record.decision_id,
            "context_packet": packet, "question": question,
            "allowed_actions": ["ANSWER", "CONTINUE", "REVISE", "DELEGATE", "COMPLETE", "HUMAN_REQUIRED", "REJECT"],
            "answer_json": "{action, job_id, decision_id, confidence, reason, answer, target_agent, requires_human}",
            "required": "ACK and terminal notification are mandatory.",
        }, ensure_ascii=False)
        self._send(record, record.commander_uid, record.decision_id, f"[{record.job_id}] [{record.decision_id}] DECISION_REQUEST", body)
        record = record.transition(JobState.DECISION_PENDING)
        record.decision_count += 1
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
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        self.jobs.save(record)
        return record

    def _handle_timeout(self, record: JobRecord, message: dict) -> JobRecord:
        self._log("worker_timeout", job_id=record.job_id, mail_id=message.get("mail_id"), status="TIMED_OUT")
        return self._human_required(record, "作業AIのCLIタイムアウト通知を受信しました。質問メールが併存していても自動再開しません。")

    def _human_required(self, record: JobRecord, reason: str) -> JobRecord:
        record = record.transition(JobState.HUMAN_REQUIRED)
        self.jobs.save(record)
        try:
            self._send(record, self.uids["human"], f"{record.decision_id}-HUMAN", f"[{record.job_id}] [{record.decision_id}] HUMAN_REQUIRED", reason)
        except Exception as err:
            self._log("human_notify_failed", job_id=record.job_id, error=type(err).__name__)
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
                return self._resume_with_answer(record, answered.fields["Decision"], answered.fields.get("Reason", "既存のANSWERED回答を再利用"))
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
            return self._resume_with_answer(record, decision, reason)
        question = question_record.fields["Question"] if questions else message.get("body", "")[:2000]
        return self._request_decision(record, question)

    def _resume_with_answer(self, record: JobRecord, answer: str, reason: str) -> JobRecord:
        new_decision_id = _new_decision_id(record.job_id, record.decision_count + 1)
        record.decision_id = new_decision_id
        record = record.transition(JobState.ANSWER_PENDING)
        self.jobs.save(record)
        packet, size, tokens = self._build_packet(record, f"Resume from checkpoint {record.latest_checkpoint}; use the formal Q&A answer: {answer}")
        body = json.dumps({"job_id": record.job_id, "decision_id": record.decision_id, "answer": answer, "action": "ANSWER", "reason": reason, "context_packet": packet, "instruction": "Start a new CLI context. Do not repeat the answered question. Send ACK, then COMPLETED or WAITING_FOR_DECISION only if strictly necessary."}, ensure_ascii=False)
        self._send(record, record.current_agent_uid, record.decision_id, f"[{record.job_id}] [{record.decision_id}] ANSWER RESUME", body)
        self._log("resume_context_packet", job_id=record.job_id, decision_id=record.decision_id, path=packet, bytes=size, estimated_tokens=tokens)
        record = record.transition(JobState.WORKER_RESUMED)
        self.jobs.save(record)
        return record

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
        return self._resume_with_answer(record, decision.answer, decision.reason)

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
        self._send(record, record.requester_uid, f"{record.decision_id}-FINAL", f"[{record.job_id}] [{record.decision_id}] STATUS: COMPLETED", body[:4000])
        record = record.transition(JobState.COMPLETED)
        self.jobs.save(record)
        return record

    def _process(self, message: dict) -> JobRecord:
        record = self._create_record(message)
        if message["mail_id"] in record.handled_mail_ids:
            return record
        record.handled_mail_ids.append(message["mail_id"])
        try:
            payload = json.loads(message.get("body", ""))
            if isinstance(payload, dict) and isinstance(payload.get("invocation_id"), str):
                record.latest_invocation_id = payload["invocation_id"]
        except (TypeError, json.JSONDecodeError):
            pass
        self.jobs.save(record)
        sender = message["sender_uid"]
        subject = message.get("subject", "")
        if "[TIMEOUT]" in subject or "status: TIMED_OUT" in message.get("body", ""):
            return self._handle_timeout(record, message)
        if record.state == JobState.DISCOVERED:
            return self._delegate(record, message)
        if sender == record.current_agent_uid and ("WAITING_FOR_DECISION" in subject or '"status": "WAITING_FOR_DECISION"' in message.get("body", "")):
            return self._handle_waiting(record, message)
        if sender == record.current_agent_uid and ("QUESTION" in subject or "QANDA" in message.get("body", "")):
            return self._handle_question(record, message)
        if sender == record.commander_uid:
            if "STATUS: ACK" in subject or '"status": "ACK_RECEIVED"' in message.get("body", ""):
                return record
            return self._handle_decision(record, message)
        if sender == record.current_agent_uid and "COMPLETE" in subject:
            return self._handle_completion(record, message)
        return record

    def process_once(self) -> int:
        records = {record.job_id: record for record in self.jobs.list_records()}
        for entry in self.outbox.pending():
            try:
                self.outbox.recover(entry)
            except OutboxError:
                record = records.get(entry.job_id)
                if record:
                    self._human_required(record, "Outboxに同一Decision-IDの複数メールが存在します")
        messages = self._load_pending()
        messages.extend(self.mail.receive_mail(self.uid))
        unique: dict[int, dict] = {int(message["mail_id"]): message for message in messages}
        messages = [unique[key] for key in sorted(unique)]
        limit = int(self.config.get("max_mails_per_once", 5))
        if limit <= 0:
            raise DirectorError("max_mails_per_once must be positive")
        self._save_pending(messages[limit:])
        for message in messages[:limit]:
            try:
                self._process(message)
            except (DirectorError, StateError, ValueError) as err:
                try:
                    record = self._create_record(message)
                    self._human_required(record, f"安全停止: {err}")
                except Exception:
                    self._log("processing_failed", mail_id=message.get("mail_id"), error=type(err).__name__)
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
