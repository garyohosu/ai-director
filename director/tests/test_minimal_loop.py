from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from context_packet import ContextPacketBuilder
from decision import DecisionError, parse_decision
from director.director import DirectorEngine, DirectorError
from outbox import Outbox, OutboxEntry, OutboxError
from qanda import QandaError, answer_question, find_answered_reuse, find_open_blocking, normalized_reuse_signature, parse_qanda
from state_machine import (
    DirectorState,
    InvocationResult,
    JobRecord,
    JobState,
    JobStore,
    StateError,
)


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

    def receive_mail(self, uid: str, *, mail_id: int | None = None) -> list[dict]:
        result = [
            m
            for m in self.messages
            if m["recipient_uid"] == uid
            and not m["is_read"]
            and (mail_id is None or m["mail_id"] == mail_id)
        ]
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
    _INVOCATION_ENV_KEYS = (
        "AI_INVOCATION_ID",
        "INVOCATION_ID",
        "AI_PARENT_INVOCATION_ID",
        "AI_ROOT_INVOCATION_ID",
        "AI_TRIGGER_MAIL_UID",
        "AI_ALLOW_MISSING_INVOCATION_ID",
    )

    def setUp(self) -> None:
        self._saved_invocation_env = {
            key: os.environ[key]
            for key in self._INVOCATION_ENV_KEYS
            if key in os.environ
        }
        for key in self._INVOCATION_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["AI_ALLOW_MISSING_INVOCATION_ID"] = "1"

    def tearDown(self) -> None:
        for key in self._INVOCATION_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self._saved_invocation_env)

    def test_director_state_and_invocation_result_are_separate(self) -> None:
        self.assertIsNot(DirectorState, InvocationResult)
        self.assertEqual(DirectorState.DECISION_PENDING, "DECISION_PENDING")
        self.assertEqual(InvocationResult.DELEGATED, "DELEGATED")

    def test_unknown_control_notifications_do_not_create_jobs_or_replies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
            orchestrator = mail.register_user("orchestrator")
            first_id = mail.send_mail(
                orchestrator,
                engine.uid,
                "[JOB-UNKNOWN-ALERT][NO_REPLY] display only",
                json.dumps(
                    {
                        "message_type": "SYSTEM_ALERT",
                        "task_eligible": False,
                        "job_id": "JOB-UNKNOWN-ALERT",
                    }
                ),
            )
            second_id = mail.send_mail(
                orchestrator,
                engine.uid,
                "ordinary display",
                json.dumps(
                    {
                        "message_type": "INVOCATION_ACK",
                        "task_eligible": False,
                        "job_id": "JOB-UNKNOWN-ACK",
                    }
                ),
            )

            self.assertEqual(engine.process_once(), 2)
            self.assertEqual(engine.jobs.list_records(), [])
            self.assertEqual([m["mail_id"] for m in mail.messages], [first_id, second_id])
            self.assertTrue(all(m["is_read"] for m in mail.messages))

    def test_control_notification_is_idempotent_for_existing_and_terminal_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
            human = engine.uids["human"]
            mail.send_mail(
                human,
                engine.uid,
                "[JOB-CONTROL-001] [DEC-CONTROL-001] task",
                "normal task",
            )
            engine.process_once()
            record = engine.jobs.load("JOB-CONTROL-001")
            original_state = record.state
            original_mail_count = len(mail.messages)
            orchestrator = mail.register_user("orchestrator")
            alert_id = mail.send_mail(
                orchestrator,
                engine.uid,
                "[JOB-CONTROL-001][TIMEOUT] display only",
                json.dumps(
                    {
                        "message_type": "SYSTEM_ALERT",
                        "task_eligible": False,
                        "job_id": "JOB-CONTROL-001",
                    }
                ),
            )
            alert = mail.messages[-1]

            engine.process_once()
            engine._process(alert)
            record = engine.jobs.load("JOB-CONTROL-001")
            self.assertEqual(record.state, original_state)
            self.assertEqual(record.handled_mail_ids.count(alert_id), 1)
            self.assertEqual(len(mail.messages), original_mail_count + 1)

            record.state = JobState.COMPLETED
            engine.jobs.save(record)
            late_id = mail.send_mail(
                orchestrator,
                engine.uid,
                "[JOB-CONTROL-001][NO_REPLY] late display",
                json.dumps(
                    {
                        "message_type": "SYSTEM_ALERT",
                        "task_eligible": False,
                        "job_id": "JOB-CONTROL-001",
                    }
                ),
            )
            engine.process_once()
            terminal = engine.jobs.load("JOB-CONTROL-001")
            self.assertEqual(terminal.state, JobState.COMPLETED)
            self.assertIn(late_id, terminal.handled_mail_ids)

    def test_no_reply_subject_and_broken_json_do_not_hide_normal_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
            human = engine.uids["human"]
            mail.send_mail(
                human,
                engine.uid,
                "[JOB-NORMAL-ALERT-TEXT] [DEC-NORMAL-ALERT-TEXT] [NO_REPLY] real task",
                "{broken-json",
            )

            self.assertEqual(engine.process_once(), 1)
            self.assertEqual(
                engine.jobs.load("JOB-NORMAL-ALERT-TEXT").state,
                JobState.WAITING_FOR_WORKER,
            )
            self.assertEqual(len(mail.messages), 4)

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

    def test_decision_request_finishes_invocation_as_delegated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            director_uid = mail.register_user("director")
            worker_uid = mail.register_user("claude_worker")
            commander_uid = mail.register_user("codex_commander")
            human_uid = mail.register_user("human_controller")
            (root / "QandA.md").write_text(
                """# QandA.md

## Q010
- Status: OPEN
- Request-ID: JOB-DELEGATE-001
- From: claude_worker
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: Which encoding?
- Proposed-Answer: UTF-8
- Evidence: test
""",
                encoding="utf-8",
            )
            question_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-DELEGATE-001] [DEC-DELEGATE-001] QUESTION Q010",
                json.dumps(
                    {
                        "message_type": "QUESTION",
                        "task_eligible": True,
                        "job_id": "JOB-DELEGATE-001",
                        "decision_id": "DEC-DELEGATE-001",
                        "invocation_id": "INV-WORKER-001",
                        "parent_invocation_id": "INV-ROOT-001",
                        "root_invocation_id": "INV-ROOT-001",
                        "trigger_mail_uid": 1,
                    }
                ),
            )
            environment = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-002",
                "INVOCATION_ID": "INV-DIRECTOR-002",
                "AI_PARENT_INVOCATION_ID": "INV-WORKER-001",
                "AI_ROOT_INVOCATION_ID": "INV-ROOT-001",
                "AI_TRIGGER_MAIL_UID": str(question_id),
            }
            with patch.dict(os.environ, environment, clear=False):
                engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
                engine.jobs.save(
                    JobRecord(
                        "JOB-DELEGATE-001",
                        1,
                        human_uid,
                        worker_uid,
                        commander_uid,
                        "DEC-DELEGATE-001",
                        JobState.WAITING_FOR_WORKER,
                        expected_worker_parent_invocation_id="INV-ROOT-001",
                        expected_worker_trigger_mail_uid=1,
                    )
                )
                self.assertEqual(engine.process_once(), 1)

            record = engine.jobs.load("JOB-DELEGATE-001")
            self.assertEqual(record.state, DirectorState.DECISION_PENDING)
            self.assertEqual(
                record.latest_invocation_result, InvocationResult.DELEGATED
            )
            delegated = next(
                message
                for message in mail.messages
                if message["mail_id"] == record.result_mail_uid
            )
            self.assertEqual(delegated["recipient_uid"], commander_uid)
            payload = json.loads(delegated["body"])
            self.assertEqual(payload["invocation_result"], "DELEGATED")
            self.assertEqual(payload["director_state"], "DECISION_PENDING")
            self.assertEqual(payload["parent_invocation_id"], "INV-WORKER-001")
            self.assertEqual(payload["root_invocation_id"], "INV-ROOT-001")
            self.assertEqual(payload["trigger_mail_uid"], question_id)
            self.assertEqual(record.result_mail_uid, delegated["mail_id"])

    def test_trigger_mail_uid_consumes_only_the_selected_mail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            director_uid = mail.register_user("director")
            human_uid = mail.register_user("human_controller")
            first_id = mail.send_mail(
                human_uid,
                director_uid,
                "[JOB-TRIGGER-001] [DEC-TRIGGER-001] request",
                "first task",
            )
            second_id = mail.send_mail(
                human_uid,
                director_uid,
                "[JOB-TRIGGER-002] [DEC-TRIGGER-002] request",
                "second task",
            )
            environment = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-TRIGGER",
                "INVOCATION_ID": "INV-DIRECTOR-TRIGGER",
                "AI_ROOT_INVOCATION_ID": "INV-DIRECTOR-TRIGGER",
                "AI_TRIGGER_MAIL_UID": str(first_id),
            }
            with patch.dict(os.environ, environment, clear=False):
                engine = DirectorEngine(
                    root, mail=mail, config_path=root / "missing.json"
                )
                self.assertEqual(engine.process_once(), 1)

            first = next(m for m in mail.messages if m["mail_id"] == first_id)
            second = next(m for m in mail.messages if m["mail_id"] == second_id)
            self.assertTrue(first["is_read"])
            self.assertFalse(second["is_read"])
            self.assertIsNotNone(engine.jobs.load("JOB-TRIGGER-001"))
            self.assertIsNone(engine.jobs.load("JOB-TRIGGER-002"))

    def test_question_then_separate_waiting_trigger_returns_noop_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            (root / "QandA.md").write_text(
                """# QandA.md

## Q010
- Status: OPEN
- Request-ID: JOB-SEPARATE-001
- From: claude_worker
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: Which encoding?
- Proposed-Answer: UTF-8
- Evidence: test
""",
                encoding="utf-8",
            )
            mail = FakeMail()
            director_uid = mail.register_user("director")
            worker_uid = mail.register_user("claude_worker")
            commander_uid = mail.register_user("codex_commander")
            human_uid = mail.register_user("human_controller")
            worker_payload = {
                "message_type": "INVOCATION_RESULT",
                "task_eligible": True,
                "job_id": "JOB-SEPARATE-001",
                "decision_id": "DEC-SEPARATE-001",
                "invocation_id": "INV-WORKER-SEPARATE",
                "parent_invocation_id": "INV-ROOT-SEPARATE",
                "root_invocation_id": "INV-ROOT-SEPARATE",
                "trigger_mail_uid": 1,
            }
            question_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-SEPARATE-001] [DEC-SEPARATE-001] QUESTION Q010",
                json.dumps(
                    {
                        **worker_payload,
                        "message_type": "QUESTION",
                        "invocation_result": "WAITING",
                        "status": "WAITING_FOR_DECISION",
                    }
                ),
            )
            initial = JobRecord(
                "JOB-SEPARATE-001",
                1,
                human_uid,
                worker_uid,
                commander_uid,
                "DEC-SEPARATE-001",
                JobState.WAITING_FOR_WORKER,
                expected_worker_parent_invocation_id="INV-ROOT-SEPARATE",
                expected_worker_trigger_mail_uid=1,
            )
            first_env = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-QUESTION",
                "INVOCATION_ID": "INV-DIRECTOR-QUESTION",
                "AI_PARENT_INVOCATION_ID": "INV-WORKER-SEPARATE",
                "AI_ROOT_INVOCATION_ID": "INV-ROOT-SEPARATE",
                "AI_TRIGGER_MAIL_UID": str(question_id),
            }
            with patch.dict(os.environ, first_env, clear=False):
                engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
                engine.jobs.save(initial)
                self.assertEqual(engine.process_once(), 1)
            self.assertEqual(
                engine.jobs.load("JOB-SEPARATE-001").latest_invocation_result,
                InvocationResult.DELEGATED,
            )

            waiting_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-SEPARATE-001] [DEC-SEPARATE-001] STATUS: WAITING_FOR_DECISION",
                json.dumps(
                    {
                        **worker_payload,
                        "invocation_result": "WAITING",
                        "status": "WAITING_FOR_DECISION",
                    }
                ),
            )
            second_env = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-WAITING",
                "INVOCATION_ID": "INV-DIRECTOR-WAITING",
                "AI_PARENT_INVOCATION_ID": "INV-WORKER-SEPARATE",
                "AI_ROOT_INVOCATION_ID": "INV-ROOT-SEPARATE",
                "AI_TRIGGER_MAIL_UID": str(waiting_id),
            }
            with patch.dict(os.environ, second_env, clear=False):
                waiting_engine = DirectorEngine(
                    root, mail=mail, config_path=root / "missing.json"
                )
                self.assertEqual(waiting_engine.process_once(), 1)

            record = waiting_engine.jobs.load("JOB-SEPARATE-001")
            self.assertEqual(record.state, JobState.DECISION_PENDING)
            noop = next(m for m in mail.messages if m["mail_id"] == record.result_mail_uid)
            noop_payload = json.loads(noop["body"])
            self.assertEqual(noop_payload["invocation_id"], "INV-DIRECTOR-WAITING")
            self.assertEqual(noop_payload["invocation_result"], "COMPLETED")
            self.assertFalse(noop_payload["task_eligible"])

    def test_wrong_inbound_invocation_fails_without_corrupting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            director_uid = mail.register_user("director")
            worker_uid = mail.register_user("claude_worker")
            commander_uid = mail.register_user("codex_commander")
            human_uid = mail.register_user("human_controller")
            bad_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-WRONG-001] [DEC-WRONG-001] QUESTION",
                json.dumps(
                    {
                        "message_type": "QUESTION",
                        "task_eligible": True,
                        "status": "WAITING_FOR_DECISION",
                        "invocation_result": "WAITING",
                        "job_id": "JOB-WRONG-001",
                        "decision_id": "DEC-WRONG-001",
                        "invocation_id": "INV-WRONG",
                        "parent_invocation_id": "INV-ROOT",
                        "root_invocation_id": "INV-ROOT",
                        "trigger_mail_uid": 1,
                    }
                ),
            )
            environment = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-WRONG",
                "INVOCATION_ID": "INV-DIRECTOR-WRONG",
                "AI_PARENT_INVOCATION_ID": "INV-EXPECTED",
                "AI_ROOT_INVOCATION_ID": "INV-ROOT",
                "AI_TRIGGER_MAIL_UID": str(bad_id),
            }
            with patch.dict(os.environ, environment, clear=False):
                engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
                engine.jobs.save(
                    JobRecord(
                        "JOB-WRONG-001", 1, human_uid, worker_uid,
                        commander_uid, "DEC-WRONG-001", JobState.WAITING_FOR_WORKER,
                        parent_invocation_id="INV-ORIGINAL-PARENT",
                        root_invocation_id="INV-ROOT",
                        trigger_mail_uid=1,
                        expected_worker_parent_invocation_id="INV-ROOT",
                        expected_worker_trigger_mail_uid=1,
                        active_worker_invocation_id="INV-EXPECTED",
                    )
                )
                self.assertEqual(engine.process_once(), 1)
                retry_engine = DirectorEngine(
                    root, mail=mail, config_path=root / "missing.json"
                )
                self.assertEqual(retry_engine.process_once(), 1)
                retried_record = retry_engine.jobs.load("JOB-WRONG-001")
                retried_payload = json.loads(
                    next(
                        message["body"]
                        for message in mail.messages
                        if message["mail_id"] == retried_record.result_mail_uid
                    )
                )
                self.assertEqual(retried_payload["invocation_result"], "FAILED")
                self.assertEqual(retried_record.state, JobState.WAITING_FOR_WORKER)
            forged_parent_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-WRONG-001] [DEC-WRONG-001] QUESTION forged parent",
                json.dumps(
                    {
                        "message_type": "QUESTION",
                        "task_eligible": True,
                        "status": "WAITING_FOR_DECISION",
                        "invocation_result": "WAITING",
                        "job_id": "JOB-WRONG-001",
                        "decision_id": "DEC-WRONG-001",
                        "invocation_id": "INV-EXPECTED",
                        "parent_invocation_id": "INV-FOREIGN-PARENT",
                        "root_invocation_id": "INV-ROOT",
                        "trigger_mail_uid": 1,
                    }
                ),
            )
            forged_environment = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-FORGED",
                "INVOCATION_ID": "INV-DIRECTOR-FORGED",
                "AI_PARENT_INVOCATION_ID": "INV-EXPECTED",
                "AI_ROOT_INVOCATION_ID": "INV-ROOT",
                "AI_TRIGGER_MAIL_UID": str(forged_parent_id),
            }
            with patch.dict(os.environ, forged_environment, clear=False):
                forged_engine = DirectorEngine(
                    root, mail=mail, config_path=root / "missing.json"
                )
                self.assertEqual(forged_engine.process_once(), 1)
            record = forged_engine.jobs.load("JOB-WRONG-001")
            self.assertEqual(record.state, JobState.WAITING_FOR_WORKER)
            self.assertEqual(record.last_inbound_invocation_id, "")
            self.assertEqual(record.latest_invocation_result, InvocationResult.FAILED)
            self.assertEqual(record.parent_invocation_id, "INV-ORIGINAL-PARENT")
            self.assertEqual(record.root_invocation_id, "INV-ROOT")
            self.assertEqual(record.trigger_mail_uid, 1)

    def test_duplicate_question_uses_lowest_mail_uid_without_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            (root / "QandA.md").write_text(
                """# QandA.md

## Q010
- Status: OPEN
- Request-ID: JOB-DUPLICATE-001
- From: claude_worker
- To: director
- Severity: HIGH
- Blocking: YES
- Category: SPEC
- Question: Which encoding?
- Proposed-Answer: UTF-8
- Evidence: test
""",
                encoding="utf-8",
            )
            mail = FakeMail()
            director_uid = mail.register_user("director")
            worker_uid = mail.register_user("claude_worker")
            commander_uid = mail.register_user("codex_commander")
            human_uid = mail.register_user("human_controller")
            payload = {
                "message_type": "QUESTION",
                "task_eligible": True,
                "status": "WAITING_FOR_DECISION",
                "invocation_result": "WAITING",
                "job_id": "JOB-DUPLICATE-001",
                "decision_id": "DEC-DUPLICATE-001",
                "invocation_id": "INV-WORKER-DUPLICATE",
                "parent_invocation_id": "INV-DIRECTOR-ROOT",
                "root_invocation_id": "INV-DIRECTOR-ROOT",
                "trigger_mail_uid": 1,
            }
            first_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-DUPLICATE-001] [DEC-DUPLICATE-001] QUESTION Q010",
                json.dumps(payload),
            )
            initial = JobRecord(
                "JOB-DUPLICATE-001",
                1,
                human_uid,
                worker_uid,
                commander_uid,
                "DEC-DUPLICATE-001",
                JobState.WAITING_FOR_WORKER,
                expected_worker_parent_invocation_id="INV-DIRECTOR-ROOT",
                expected_worker_trigger_mail_uid=1,
            )
            first_env = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-FIRST",
                "INVOCATION_ID": "INV-DIRECTOR-FIRST",
                "AI_PARENT_INVOCATION_ID": "INV-WORKER-DUPLICATE",
                "AI_ROOT_INVOCATION_ID": "INV-DIRECTOR-ROOT",
                "AI_TRIGGER_MAIL_UID": str(first_id),
            }
            with patch.dict(os.environ, first_env, clear=False):
                engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
                engine.jobs.save(initial)
                self.assertEqual(engine.process_once(), 1)

            second_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-DUPLICATE-001] [DEC-DUPLICATE-001] QUESTION Q010 duplicate",
                json.dumps(payload),
            )
            second_env = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-SECOND",
                "INVOCATION_ID": "INV-DIRECTOR-SECOND",
                "AI_PARENT_INVOCATION_ID": "INV-WORKER-DUPLICATE",
                "AI_ROOT_INVOCATION_ID": "INV-DIRECTOR-ROOT",
                "AI_TRIGGER_MAIL_UID": str(second_id),
            }
            with patch.dict(os.environ, second_env, clear=False):
                duplicate_engine = DirectorEngine(
                    root, mail=mail, config_path=root / "missing.json"
                )
                self.assertEqual(duplicate_engine.process_once(), 1)

            record = duplicate_engine.jobs.load("JOB-DUPLICATE-001")
            self.assertEqual(record.state, JobState.DECISION_PENDING)
            self.assertIn(first_id, record.inbound_result_mail_uids.values())
            decision_requests = [
                json.loads(message["body"])
                for message in mail.messages
                if message["recipient_uid"] == commander_uid
                and message["body"].lstrip().startswith("{")
                and json.loads(message["body"]).get("message_type")
                == "DECISION_REQUEST"
            ]
            self.assertEqual(len(decision_requests), 1)

    def test_late_completion_does_not_reopen_terminal_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            director_uid = mail.register_user("director")
            worker_uid = mail.register_user("claude_worker")
            commander_uid = mail.register_user("codex_commander")
            human_uid = mail.register_user("human_controller")
            late_id = mail.send_mail(
                worker_uid,
                director_uid,
                "[JOB-LATE-001] [DEC-LATE-001] STATUS: COMPLETED",
                json.dumps(
                    {
                        "message_type": "INVOCATION_RESULT",
                        "task_eligible": True,
                        "status": "COMPLETED",
                        "invocation_result": "COMPLETED",
                        "job_id": "JOB-LATE-001",
                        "decision_id": "DEC-LATE-001",
                        "invocation_id": "INV-WORKER-LATE",
                        "parent_invocation_id": "INV-DIRECTOR-ROOT",
                        "root_invocation_id": "INV-DIRECTOR-ROOT",
                        "trigger_mail_uid": 1,
                        "artifacts": [],
                    }
                ),
            )
            environment = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-LATE",
                "INVOCATION_ID": "INV-DIRECTOR-LATE",
                "AI_PARENT_INVOCATION_ID": "INV-WORKER-LATE",
                "AI_ROOT_INVOCATION_ID": "INV-DIRECTOR-ROOT",
                "AI_TRIGGER_MAIL_UID": str(late_id),
            }
            with patch.dict(os.environ, environment, clear=False):
                engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
                engine.jobs.save(
                    JobRecord(
                        "JOB-LATE-001",
                        1,
                        human_uid,
                        worker_uid,
                        commander_uid,
                        "DEC-LATE-001",
                        JobState.COMPLETED,
                        expected_worker_parent_invocation_id="INV-DIRECTOR-ROOT",
                        expected_worker_trigger_mail_uid=1,
                        active_worker_invocation_id="INV-WORKER-LATE",
                    )
                )
                self.assertEqual(engine.process_once(), 1)
            record = engine.jobs.load("JOB-LATE-001")
            self.assertEqual(record.state, JobState.COMPLETED)
            self.assertEqual(record.latest_invocation_result, InvocationResult.COMPLETED)

    def test_exact_trigger_retry_recovers_already_read_mail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            mail = FakeMail()
            director_uid = mail.register_user("director")
            human_uid = mail.register_user("human_controller")
            trigger_id = mail.send_mail(
                human_uid,
                director_uid,
                "[JOB-RETRY-001] [DEC-RETRY-001] request",
                "safe task",
            )
            environment = {
                "AI_INVOCATION_ID": "INV-DIRECTOR-RETRY",
                "INVOCATION_ID": "INV-DIRECTOR-RETRY",
                "AI_ROOT_INVOCATION_ID": "INV-DIRECTOR-RETRY",
                "AI_TRIGGER_MAIL_UID": str(trigger_id),
            }
            with patch.dict(os.environ, environment, clear=False):
                engine = DirectorEngine(root, mail=mail, config_path=root / "missing.json")
                self.assertEqual(engine.process_once(), 1)
                retry_engine = DirectorEngine(
                    root, mail=mail, config_path=root / "missing.json"
                )
                self.assertEqual(retry_engine.process_once(), 1)
            record = retry_engine.jobs.load("JOB-RETRY-001")
            self.assertEqual(record.state, JobState.WAITING_FOR_WORKER)

    def test_director_rejects_conflicting_invocation_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            with patch.dict(
                os.environ,
                {"AI_INVOCATION_ID": "INV-A", "INVOCATION_ID": "INV-B"},
                clear=False,
            ):
                with self.assertRaises(DirectorError):
                    DirectorEngine(root, mail=FakeMail(), config_path=root / "missing.json")

    def test_missing_exact_trigger_is_an_explicit_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "director").mkdir()
            environment = {
                "AI_INVOCATION_ID": "INV-MISSING-TRIGGER",
                "INVOCATION_ID": "INV-MISSING-TRIGGER",
                "AI_ROOT_INVOCATION_ID": "INV-MISSING-TRIGGER",
                "AI_TRIGGER_MAIL_UID": "999",
            }
            with patch.dict(os.environ, environment, clear=False):
                engine = DirectorEngine(
                    root, mail=FakeMail(), config_path=root / "missing.json"
                )
                with self.assertRaises(DirectorError):
                    engine.process_once()

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
            mail.send_mail(
                orchestrator,
                timeout_engine.uid,
                "[JOB-TIMEOUT-001][TIMEOUT] claude_designer",
                json.dumps(
                    {
                        "message_type": "SYSTEM_ALERT",
                        "task_eligible": False,
                        "status": "TIMED_OUT",
                        "job_id": "JOB-TIMEOUT-001",
                    }
                ),
            )
            timeout_engine.process_once()
            self.assertEqual(
                timeout_engine.jobs.load("JOB-TIMEOUT-001").state,
                JobState.WAITING_FOR_WORKER,
            )


if __name__ == "__main__":
    unittest.main()
