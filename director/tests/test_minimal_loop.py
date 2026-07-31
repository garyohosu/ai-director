from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from context_packet import ContextPacketBuilder
from decision import DecisionError, parse_decision
from director.director import DirectorEngine
from outbox import Outbox, OutboxEntry, OutboxError
from qanda import QandaError, answer_question, find_answered_reuse, find_open_blocking, normalized_reuse_signature, parse_qanda
from state_machine import JobRecord, JobState, JobStore, StateError


class FakeMail:
    def __init__(self) -> None:
        self.users: dict[str, str] = {}
        self.messages: list[dict] = []
        self.next_uid = 1
        self.next_mail = 1

    def register_user(self, name: str) -> str:
        if name not in self.users:
            self.users[name] = f"UID{self.next_uid:06d}"
            self.next_uid += 1
        return self.users[name]

    def send_mail(self, sender_uid: str, recipient_uid: str, subject: str, body: str) -> int:
        mail_id = self.next_mail
        self.next_mail += 1
        self.messages.append({"mail_id": mail_id, "sender_uid": sender_uid, "recipient_uid": recipient_uid, "subject": subject, "body": body, "is_read": False})
        return mail_id

    def receive_mail(self, uid: str) -> list[dict]:
        result = [m for m in self.messages if m["recipient_uid"] == uid and not m["is_read"]]
        for message in result:
            message["is_read"] = True
        return result

    def find_mails(self, **kwargs):
        result = []
        for message in self.messages:
            if kwargs.get("sender_uid") and message["sender_uid"] != kwargs["sender_uid"]:
                continue
            if kwargs.get("recipient_uid") and message["recipient_uid"] != kwargs["recipient_uid"]:
                continue
            if kwargs.get("request_id") and f"[{kwargs['request_id']}]" not in message["subject"]:
                continue
            if kwargs.get("after_mail_id") is not None and message["mail_id"] <= kwargs["after_mail_id"]:
                continue
            result.append(message)
        return result[: kwargs["limit"]] if kwargs.get("limit") else result


class DirectorUnitTests(unittest.TestCase):
    def test_director_delegate_waits_with_invocation_bound_terminal_mail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            previous = os.environ.get("INVOCATION_ID")
            os.environ["INVOCATION_ID"] = "INV-DIRECTOR-001"
            try:
                engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
                human = engine.uids["human"]
                mail.send_mail(human, engine.uid, "[JOB-WAIT-001] [DEC-WAIT-001] request", "safe task")
                engine.process_once()
            finally:
                if previous is None:
                    os.environ.pop("INVOCATION_ID", None)
                else:
                    os.environ["INVOCATION_ID"] = previous
            record = engine.jobs.load("JOB-WAIT-001")
            self.assertEqual(record.state, JobState.WAITING_FOR_WORKER)
            self.assertGreater(record.delegate_mail_id, 0)
            self.assertTrue(record.latest_checkpoint.endswith("-worker-wait.json"))
            outbound = [m for m in mail.messages if m["sender_uid"] == engine.uid]
            self.assertEqual(len(outbound), 3)
            self.assertIn("STATUS: ACK", outbound[0]["subject"])
            self.assertIn("DELEGATE", outbound[1]["subject"])
            self.assertIn("STATUS: WAITING_FOR_WORKER", outbound[2]["subject"])
            for message in outbound:
                self.assertIn("INV-DIRECTOR-001", message["subject"] + message["body"])

    def test_job_store_and_safe_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            record = JobRecord("JOB-TEST-001", 1, "UID000001", "UID000002", "UID000003", "DEC-TEST-001", JobState.DISCOVERED)
            store.save(record)
            self.assertEqual(store.load(record.job_id).state, JobState.DISCOVERED)
            with self.assertRaises(StateError):
                store.path_for("JOB-../escape")
            self.assertEqual(record.transition(JobState.ACK_SENT).state, JobState.ACK_SENT)
            resumed = record.transition(JobState.ACK_SENT).transition(JobState.DELEGATION_PENDING).transition(JobState.WORKER_RUNNING).transition(JobState.WORKER_WAITING_QUESTION).transition(JobState.ANSWER_PENDING)
            self.assertEqual(resumed.state, JobState.ANSWER_PENDING)
            self.assertEqual(record.transition(JobState.ACK_SENT).transition(JobState.DELEGATION_PENDING).transition(JobState.WAITING_FOR_WORKER).state, JobState.WAITING_FOR_WORKER)

    def test_invocation_id_is_persisted_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp))
            record = JobRecord("JOB-INV-001", 1, "UID000001", "UID000002", "UID000003", "DEC-INV-001", JobState.WORKER_RUNNING, latest_invocation_id="INV-INV-001")
            store.save(record)
            restored = store.load(record.job_id)
            self.assertEqual(restored.latest_invocation_id, "INV-INV-001")
            with self.assertRaises(StateError):
                JobRecord("JOB-INV-002", 1, "UID000001", "UID000002", "UID000003", "DEC-INV-002", JobState.WORKER_RUNNING, latest_invocation_id="DEC-NOT-INV")

    def test_qanda_strict_parse_and_answer_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "QandA.md"
            path.write_text("""# QandA.md\n\n## Q006\n- Status: OPEN\n- Request-ID: JOB-TEST-001\n- From: claude_worker\n- To: director\n- Severity: HIGH\n- Blocking: YES\n- Category: SPEC\n- Question: Should the answer be exact?\n- Proposed-Answer: yes\n- Evidence: test\n""", encoding="utf-8")
            questions = parse_qanda(path)
            self.assertEqual(find_open_blocking(path, "JOB-TEST-001")[0].number, "Q006")
            answer_question(path, "Q006", decision="yes", reason="unique specification")
            self.assertEqual(parse_qanda(path)[0].status, "ANSWERED")
            answer_question(path, "Q006", decision="different", reason="ignored")
            self.assertIn("Decision: yes", path.read_text(encoding="utf-8"))
            bad = Path(tmp) / "bad.md"
            bad.write_text("## Q007\n- Status: OPEN\n- Broken line\n", encoding="utf-8")
            with self.assertRaises(QandaError):
                parse_qanda(bad)

    def test_context_packet_is_bounded_and_records_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = ContextPacketBuilder(Path(tmp), max_bytes=120).build(
                "JOB-TEST-001", "DEC-TEST-001", role="codex", task="x" * 500, state="DECISION_PENDING",
                completed=[], unresolved=[], qanda=[], spec_sections=[], target_files=[], git_summary="", test_summary="",
                prohibitions=[], reply_commands=[], completion="done", path=Path(tmp) / "packet.md",
            )
            self.assertTrue(result.truncated)
            self.assertLessEqual(result.byte_size, 200)
            self.assertIn("source_sha256=", result.path.read_text(encoding="utf-8"))

    def test_qanda_reuse_normalizes_only_fixed_artifact_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "QandA.md"
            path.write_text("""# QandA.md

## Q006
- Status: ANSWERED
- Request-ID: JOB-OLD
- From: claude_worker
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 成果物ファイルは末尾改行なしの一行で作成してよいですか？
- Proposed-Answer: はい。指定文字列を末尾改行なしで作成する。
- Evidence: test
- Answered-By: human_controller
- Decision: 成果物ファイルは、指定された文字列の一行を末尾改行なしで作成する。
- Reason: confirmed

## Q007
- Status: OPEN
- Request-ID: JOB-NEW
- From: claude_worker
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: 成果物は指定文字列の一行を末尾改行なしで作成してよいですか？
- Proposed-Answer: はい。指定文字列を末尾改行なしで作成する。
- Evidence: test
""", encoding="utf-8")
            questions = parse_qanda(path)
            target = next(q for q in questions if q.number == "Q007")
            source = find_answered_reuse(questions, target)
            self.assertEqual(source.number, "Q006")
            self.assertEqual(normalized_reuse_signature(source), normalized_reuse_signature(target))

    def test_decision_schema_rejects_mismatch_and_bad_action(self) -> None:
        valid = {"action": "ANSWER", "job_id": "JOB-TEST-001", "decision_id": "DEC-TEST-001", "confidence": "HIGH", "reason": "r", "answer": "a", "target_agent": "claude_worker", "requires_human": False}
        self.assertEqual(parse_decision(json.dumps(valid), expected_job_id=valid["job_id"], expected_decision_id=valid["decision_id"]).action, "ANSWER")
        valid["action"] = "NOPE"
        with self.assertRaises(DecisionError):
            parse_decision(json.dumps(valid), expected_job_id="JOB-TEST-001", expected_decision_id="DEC-TEST-001")
        valid["action"] = "ANSWER"
        with self.assertRaises(DecisionError):
            parse_decision(json.dumps(valid), expected_job_id="JOB-OTHER", expected_decision_id="DEC-TEST-001")

    def test_outbox_recovery_uses_find_mails_and_rejects_duplicates(self) -> None:
        mail = FakeMail()
        sender = mail.register_user("director")
        recipient = mail.register_user("worker")
        with tempfile.TemporaryDirectory() as tmp:
            outbox = Outbox(Path(tmp), mail)
            entry = OutboxEntry("JOB-TEST-001", "DEC-TEST-001", sender, recipient, "[JOB-TEST-001] [DEC-TEST-001] x", "body")
            outbox.prepare(entry)
            sent = outbox.recover(entry)
            self.assertEqual(sent.state, JobState.SENT)
            recovered = outbox.recover(entry)
            self.assertEqual(recovered.mail_id, sent.mail_id)
            mail.send_mail(sender, recipient, entry.subject, entry.body)
            with self.assertRaises(OutboxError):
                outbox.recover(entry)

    def test_mock_qanda_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            (root / "QandA.md").write_text("""# QandA.md\n\n## Q006\n- Status: OPEN\n- Request-ID: JOB-TEST-001\n- From: claude_worker\n- To: director\n- Severity: HIGH\n- Blocking: YES\n- Category: SPEC\n- Question: Is one line required?\n- Proposed-Answer: yes\n- Evidence: test\n""", encoding="utf-8")
            mail = FakeMail()
            engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
            human = engine.uids["human"]
            mail.send_mail(human, engine.uid, "[JOB-TEST-001] [DEC-TEST-001] request", "Do the safe task")
            self.assertEqual(engine.process_once(), 1)
            worker = engine.uids["worker"]
            mail.send_mail(worker, engine.uid, "[JOB-TEST-001] [DEC-TEST-001] QUESTION", "QANDA Q006")
            engine.process_once()
            commander = engine.uids["commander"]
            decision = {"action": "ANSWER", "job_id": "JOB-TEST-001", "decision_id": "DEC-TEST-001", "confidence": "HIGH", "reason": "spec is exact", "answer": "yes", "target_agent": "claude_worker", "requires_human": False}
            mail.send_mail(commander, engine.uid, "[JOB-TEST-001] [DEC-TEST-001] decision", json.dumps(decision))
            engine.process_once()
            mail.send_mail(worker, engine.uid, "[JOB-TEST-001] [DEC-TEST-001] STATUS: COMPLETE", json.dumps({"status": "COMPLETED", "artifacts": []}))
            engine.process_once()
            record = engine.jobs.load("JOB-TEST-001")
            self.assertEqual(record.state, JobState.COMPLETED)
            self.assertTrue(any(m["recipient_uid"] == human and "STATUS: COMPLETED" in m["subject"] for m in mail.messages))
            self.assertEqual(parse_qanda(root / "QandA.md")[0].status, "ANSWERED")

    def test_waiting_then_new_worker_context_and_timeout_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            (root / "QandA.md").write_text("""# QandA.md\n\n## Q006\n- Status: OPEN\n- Request-ID: JOB-WAIT-001\n- From: claude_designer\n- To: director\n- Severity: HIGH\n- Blocking: YES\n- Category: SPEC\n- Question: Exact one line?\n- Proposed-Answer: yes\n- Evidence: test\n""", encoding="utf-8")
            mail = FakeMail()
            engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
            human, worker, commander = engine.uids["human"], engine.uids["worker"], engine.uids["commander"]
            mail.send_mail(human, engine.uid, "[JOB-WAIT-001] [DEC-WAIT-001] request", "safe task")
            engine.process_once()
            mail.send_mail(worker, engine.uid, "[JOB-WAIT-001] [DEC-WAIT-001] STATUS: ACK", "ack")
            mail.send_mail(worker, engine.uid, "[JOB-WAIT-001] [DEC-WAIT-001] STATUS: QUESTION Q006", "QANDA Q006")
            checkpoint = root / "director" / "worker-checkpoint.json"
            checkpoint.write_text("{}", encoding="utf-8")
            wait_body = {"status": "WAITING_FOR_DECISION", "job_id": "JOB-WAIT-001", "decision_id": "DEC-WAIT-001", "qanda_ids": ["Q006"], "summary": "wait", "checkpoint": "director/worker-checkpoint.json"}
            mail.send_mail(worker, engine.uid, "[JOB-WAIT-001] [DEC-WAIT-001] STATUS: WAITING_FOR_DECISION", json.dumps(wait_body))
            engine.process_once()
            self.assertEqual(engine.jobs.load("JOB-WAIT-001").state, JobState.DECISION_PENDING)
            decision = {"action": "ANSWER", "job_id": "JOB-WAIT-001", "decision_id": "DEC-WAIT-001", "confidence": "HIGH", "reason": "exact requirement", "answer": "yes", "target_agent": "claude_designer", "requires_human": False}
            mail.send_mail(commander, engine.uid, "[JOB-WAIT-001] [DEC-WAIT-001] STATUS: ACK", json.dumps({"status": "ACK_RECEIVED"}))
            mail.send_mail(commander, engine.uid, "[JOB-WAIT-001] [DEC-WAIT-001] ANSWER", json.dumps(decision))
            engine.process_once()
            resumed = engine.jobs.load("JOB-WAIT-001")
            self.assertEqual(resumed.state, JobState.WORKER_RESUMED)
            self.assertNotEqual(resumed.decision_id, "DEC-WAIT-001")
            artifact = root / "artifact.txt"
            artifact.write_text("done", encoding="utf-8")
            import hashlib
            complete = {"status": "COMPLETED", "artifacts": [{"path": "artifact.txt", "sha256": hashlib.sha256(b"done").hexdigest()}]}
            mail.send_mail(worker, engine.uid, f"[JOB-WAIT-001] [{resumed.decision_id}] STATUS: ACK", json.dumps({"status": "ACK_RECEIVED"}))
            mail.send_mail(worker, engine.uid, f"[JOB-WAIT-001] [{resumed.decision_id}] STATUS: COMPLETE", json.dumps(complete))
            engine.process_once()
            self.assertEqual(engine.jobs.load("JOB-WAIT-001").state, JobState.COMPLETED)
            self.assertEqual(parse_qanda(root / "QandA.md")[0].status, "ANSWERED")

            timeout_root = Path(tmp) / "timeout"
            timeout_root.mkdir()
            (timeout_root / "director").mkdir()
            timeout_engine = DirectorEngine(timeout_root, mail=mail, config_path=timeout_root / "missing.json")
            timeout_human = timeout_engine.uids["human"]
            mail.send_mail(timeout_human, timeout_engine.uid, "[JOB-TIMEOUT-001] [DEC-TIMEOUT-001] request", "safe task")
            timeout_engine.process_once()
            orchestrator = mail.register_user("orchestrator")
            mail.send_mail(orchestrator, timeout_engine.uid, "[JOB-TIMEOUT-001][TIMEOUT] claude_designer", "status: TIMED_OUT\njob_id: JOB-TIMEOUT-001")
            timeout_engine.process_once()
            self.assertEqual(timeout_engine.jobs.load("JOB-TIMEOUT-001").state, JobState.HUMAN_REQUIRED)


if __name__ == "__main__":
    unittest.main()
