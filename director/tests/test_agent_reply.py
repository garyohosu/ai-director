from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
import sys

from director.tests import helper

from director.agent_reply import validate_and_read_result_file, validate_wait_payload, mask_secrets
from director.ids import InvocationIdError, new_manual_invocation_id, resolve_invocation_id


class TestAgentReply(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_mask_secrets(self):
        sample = "api_key=12345\nnormal_line=hello\nsecret: supersecret"
        masked = mask_secrets(sample)
        self.assertIn("[REDACTED_SECRET_LINE]", masked)
        self.assertIn("normal_line=hello", masked)

    def test_validate_result_file_valid(self):
        art_path = self.root / "artifact.txt"
        art_path.write_text("test content", encoding="utf-8")
        art_sha = hashlib.sha256(b"test content").hexdigest()

        res_file = self.root / "result.json"
        res_data = {
            "status": "COMPLETED",
            "summary": "Done",
            "artifacts": [{"path": "artifact.txt", "sha256": art_sha}],
        }
        res_file.write_text(json.dumps(res_data), encoding="utf-8")

        parsed = validate_and_read_result_file(self.root, "result.json")
        self.assertEqual(parsed["summary"], "Done")

    def test_wait_payload_requires_matching_ids_and_project_relative_files(self):
        checkpoint = self.root / "director" / "checkpoints" / "worker-checkpoint.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text("{}", encoding="utf-8")
        (self.root / "QandA.md").write_text("# QandA.md\n", encoding="utf-8")
        payload = {
            "status": "WAITING_FOR_DECISION",
            "job_id": "JOB-WAIT-001",
            "decision_id": "DEC-WAIT-001",
            "qanda_ids": ["Q006"],
            "summary": "Blocking質問への回答待ち",
            "checkpoint": "director/checkpoints/worker-checkpoint.json",
        }
        self.assertEqual(validate_wait_payload(self.root, payload, "JOB-WAIT-001", "DEC-WAIT-001")["qanda_ids"], ["Q006"])
        payload["checkpoint"] = "../outside.json"
        with self.assertRaises(ValueError):
            validate_wait_payload(self.root, payload, "JOB-WAIT-001", "DEC-WAIT-001")

    def test_validate_result_file_absolute_path_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_and_read_result_file(self.root, str(self.root / "result.json"))
        self.assertIn("Absolute path not allowed", str(ctx.exception))

    def test_validate_result_file_traversal_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_and_read_result_file(self.root, "../outside.json")
        self.assertIn("Path outside project_path rejected", str(ctx.exception))

    def test_validate_result_file_sha_mismatch(self):
        art_path = self.root / "artifact.txt"
        art_path.write_text("test content", encoding="utf-8")

        res_file = self.root / "result.json"
        res_data = {
            "status": "COMPLETED",
            "summary": "Done",
            "artifacts": [{"path": "artifact.txt", "sha256": "wrongsha"}],
        }
        res_file.write_text(json.dumps(res_data), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            validate_and_read_result_file(self.root, "result.json")
        self.assertIn("SHA-256 mismatch", str(ctx.exception))


    def test_validate_result_file_size_limit_exceeded(self):
        res_file = self.root / "large_result.json"
        # Write file larger than 10MB logically using seek
        with res_file.open("wb") as fh:
            fh.seek(10 * 1024 * 1024 + 1)
            fh.write(b"0")
        with self.assertRaises(ValueError) as ctx:
            validate_and_read_result_file(self.root, "large_result.json")
        self.assertIn("File size exceeds 10MB limit", str(ctx.exception))

    def test_validate_result_file_invalid_utf8(self):
        res_file = self.root / "invalid_utf8.json"
        res_file.write_bytes(b"\x80\x81\x82")
        with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
            validate_and_read_result_file(self.root, "invalid_utf8.json")

    def test_validate_result_file_missing_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "File does not exist"):
            validate_and_read_result_file(self.root, "missing.json")

    def test_validate_result_file_broken_json_is_explicit_error(self):
        (self.root / "broken.json").write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            validate_and_read_result_file(self.root, "broken.json")

    def test_validate_result_file_rejects_invalid_artifacts_shape(self):
        (self.root / "invalid.json").write_text(
            json.dumps({"status": "COMPLETED", "artifacts": "artifact.txt"}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "artifacts must be an array"):
            validate_and_read_result_file(self.root, "invalid.json")

    def test_resolve_invocation_id_product_function(self):
        self.assertEqual(
            resolve_invocation_id({"AI_INVOCATION_ID": "INV-CANONICAL"}),
            "INV-CANONICAL",
        )
        self.assertEqual(
            resolve_invocation_id({"INVOCATION_ID": "INV-LEGACY"}),
            "INV-LEGACY",
        )
        self.assertEqual(
            resolve_invocation_id(
                {
                    "AI_INVOCATION_ID": "INV-SAME",
                    "INVOCATION_ID": "INV-SAME",
                }
            ),
            "INV-SAME",
        )
        with self.assertRaises(InvocationIdError):
            resolve_invocation_id(
                {"AI_INVOCATION_ID": "INV-A", "INVOCATION_ID": "INV-B"}
            )
        with self.assertRaises(InvocationIdError):
            resolve_invocation_id({"AI_INVOCATION_ID": ""})
        with self.assertRaises(InvocationIdError):
            resolve_invocation_id({})

    def test_manual_invocation_id_product_function_uses_complete_uuid4(self):
        generated_ids = {new_manual_invocation_id() for _ in range(1000)}
        self.assertEqual(len(generated_ids), 1000)
        for generated_id in generated_ids:
            self.assertTrue(generated_id.startswith("MANUAL-"))
            parsed = uuid.UUID(generated_id.removeprefix("MANUAL-"))
            self.assertEqual(parsed.version, 4)
            self.assertEqual(str(parsed).upper(), generated_id.removeprefix("MANUAL-"))

        resolved = resolve_invocation_id(
            {"AI_ALLOW_MISSING_INVOCATION_ID": "1"}
        )
        self.assertTrue(resolved.startswith("MANUAL-"))
        self.assertEqual(uuid.UUID(resolved.removeprefix("MANUAL-")).version, 4)

    def test_deduplication_and_state_transitions(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, send_mail, find_mails, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = os.environ.copy()
        env.update({
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-DEDUP-001",
            "DECISION_ID": "DEC-DEDUP-001",
            "AI_INVOCATION_ID": "INV-DEDUP-001",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        })

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        # 1. First ACK -> Mail sent
        subprocess.check_call([sys.executable, str(agent_reply_py), "ack"], env=env)
        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-DEDUP-001", db_path=db_path)
        self.assertEqual(len(mails), 1)

        checkpoint = self.root / "director" / "checkpoints" / "worker-checkpoint.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text("{}", encoding="utf-8")
        (self.root / "QandA.md").write_text("# QandA.md\n", encoding="utf-8")
        wait_file = self.root / "wait.json"
        wait_file.write_text(json.dumps({
            "status": "WAITING_FOR_DECISION", "job_id": "JOB-DEDUP-001", "decision_id": "DEC-DEDUP-001",
            "qanda_ids": ["Q006"], "summary": "Blocking question", "checkpoint": "director/checkpoints/worker-checkpoint.json",
        }), encoding="utf-8")
        subprocess.check_call([sys.executable, str(agent_reply_py), "wait", "--result-file", "wait.json"], env=env)
        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-DEDUP-001", db_path=db_path)
        self.assertEqual(len(mails), 2)

        # 2. ACK from a newly resumed CLI is allowed after WAITING (total 3 mails)
        subprocess.check_call([sys.executable, str(agent_reply_py), "ack"], env=env)
        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-DEDUP-001", db_path=db_path)
        self.assertEqual(len(mails), 3)

        # 3. First COMPLETED -> Mail sent (total 4 mails)
        res_file = self.root / "res.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "done"}), encoding="utf-8")
        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res.json"], env=env)
        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-DEDUP-001", db_path=db_path)
        self.assertEqual(len(mails), 4)

        # 4. Second COMPLETED (Duplicate) -> Mail skipped (still 4 mails)
        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res.json"], env=env)
        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-DEDUP-001", db_path=db_path)
        self.assertEqual(len(mails), 4)

        # 5. ACK after COMPLETED -> Rejected with exit code 1
        res_code = subprocess.call([sys.executable, str(agent_reply_py), "ack"], env=env)
        self.assertEqual(res_code, 1)

        # 6. Separate Decision-ID -> Processed independently as new decision
        env_dec2 = env.copy()
        env_dec2["DECISION_ID"] = "DEC-DEDUP-002"
        env_dec2["AI_INVOCATION_ID"] = "INV-DEDUP-002"
        subprocess.check_call([sys.executable, str(agent_reply_py), "ack"], env=env_dec2)
        mails_dec2 = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-DEDUP-001", db_path=db_path)
        self.assertEqual(len(mails_dec2), 5)

    def test_auto_propagate_invocation_id(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, find_mails, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-AUTO-001",
            "DECISION_ID": "DEC-AUTO-001",
            "AI_INVOCATION_ID": "INV-AUTO-001",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file = self.root / "res_auto.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "auto check"}), encoding="utf-8")

        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_auto.json"], env=env)

        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-AUTO-001", db_path=db_path)
        self.assertEqual(len(mails), 1)
        self.assertIn("[INV-AUTO-001]", mails[0]["subject"])

        body_data = json.loads(mails[0]["body"])
        self.assertEqual(body_data["invocation_id"], "INV-AUTO-001")

    def test_reject_mismatched_invocation_id(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-MISMATCH-001",
            "DECISION_ID": "DEC-MISMATCH-001",
            "AI_INVOCATION_ID": "INV-REAL-001",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file = self.root / "res_mismatch.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "mismatch check", "invocation_id": "INV-FAKE-001"}), encoding="utf-8")

        res = subprocess.call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_mismatch.json"], env=env)
        self.assertEqual(res, 1)

    def test_fail_on_missing_env_invocation_id(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-MISS-001",
            "DECISION_ID": "DEC-MISS-001",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file = self.root / "res_miss.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "missing env check", "invocation_id": "INV-REQUIRED-001"}), encoding="utf-8")

        res = subprocess.call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_miss.json"], env=env)
        self.assertEqual(res, 1)

    def test_fail_on_empty_env_invocation_id(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-EMPTY-001",
            "DECISION_ID": "DEC-EMPTY-001",
            "AI_INVOCATION_ID": "",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file = self.root / "res_empty.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "empty env check"}), encoding="utf-8")

        res = subprocess.call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_empty.json"], env=env)
        self.assertEqual(res, 1)

    def test_backwards_compatibility_no_invocation_id(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, find_mails, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        # 1. Normal mode without variables -> MUST FAIL
        env_fail = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-LEGACY-FAIL",
            "DECISION_ID": "DEC-LEGACY-FAIL",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file1 = self.root / "res_legacy_fail.json"
        res_file1.write_text(json.dumps({"status": "COMPLETED", "summary": "fail check"}), encoding="utf-8")

        res = subprocess.call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_legacy_fail.json"], env=env_fail)
        self.assertEqual(res, 1)

        # 2. Compatibility mode enabled -> MUST SUCCEED with MANUAL-<UUID>
        env_ok = env_fail.copy()
        env_ok["JOB_ID"] = "JOB-LEGACY-OK"
        env_ok["DECISION_ID"] = "DEC-LEGACY-OK"
        env_ok["AI_ALLOW_MISSING_INVOCATION_ID"] = "1"

        res_file2 = self.root / "res_legacy_ok.json"
        res_file2.write_text(json.dumps({"status": "COMPLETED", "summary": "ok check"}), encoding="utf-8")

        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_legacy_ok.json"], env=env_ok)

        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-LEGACY-OK", db_path=db_path)
        self.assertEqual(len(mails), 1)

        subject = mails[0]["subject"]
        body_data = json.loads(mails[0]["body"])

        # Must not be "INV-NOT-SET"
        self.assertNotIn("INV-NOT-SET", subject)

        # Must be MANUAL-<UUID> format
        self.assertTrue(body_data["invocation_id"].startswith("MANUAL-"))
        self.assertIn(body_data["invocation_id"], subject)

    def test_differing_invocation_ids_raises(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-DIFF-001",
            "DECISION_ID": "DEC-DIFF-001",
            "AI_INVOCATION_ID": "INV-A",
            "INVOCATION_ID": "INV-B",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file = self.root / "res_diff.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "differing check"}), encoding="utf-8")

        res = subprocess.call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_diff.json"], env=env)
        self.assertEqual(res, 1)

    def test_identical_invocation_ids_succeeds(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, find_mails, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-IDEN-001",
            "DECISION_ID": "DEC-IDEN-001",
            "AI_INVOCATION_ID": "INV-SAME",
            "INVOCATION_ID": "INV-SAME",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file = self.root / "res_iden.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "identical check"}), encoding="utf-8")

        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_iden.json"], env=env)

        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-IDEN-001", db_path=db_path)
        self.assertEqual(len(mails), 1)
        self.assertIn("[INV-SAME]", mails[0]["subject"])

    def test_consecutive_runs_no_interference(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, find_mails, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        # First execution
        env1 = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-CONSEC-1",
            "DECISION_ID": "DEC-CONSEC-1",
            "AI_INVOCATION_ID": "INV-CONSEC-1",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }
        res_file1 = self.root / "res_consec1.json"
        res_file1.write_text(json.dumps({"status": "COMPLETED", "summary": "consec 1"}), encoding="utf-8")
        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_consec1.json"], env=env1)

        # Second execution with different ID
        env2 = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-CONSEC-2",
            "DECISION_ID": "DEC-CONSEC-2",
            "AI_INVOCATION_ID": "INV-CONSEC-2",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }
        res_file2 = self.root / "res_consec2.json"
        res_file2.write_text(json.dumps({"status": "COMPLETED", "summary": "consec 2"}), encoding="utf-8")
        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_consec2.json"], env=env2)

        # Verify no interference (first is still 1, second is 2)
        mails1 = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-CONSEC-1", db_path=db_path)
        self.assertIn("[INV-CONSEC-1]", mails1[0]["subject"])

        mails2 = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-CONSEC-2", db_path=db_path)
        self.assertIn("[INV-CONSEC-2]", mails2[0]["subject"])

    def test_manual_uuid4_generation_rules(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        from mail import initialize, find_mails, register_user
        initialize(db_path=db_path)
        u1 = register_user("agent1", db_path=db_path)
        u2 = register_user("agent2", db_path=db_path)

        env = {
            "AGENT_UID": u1,
            "REPLY_TO_UID": u2,
            "JOB_ID": "JOB-MANUAL-TEST",
            "DECISION_ID": "DEC-MANUAL-TEST",
            "AI_ALLOW_MISSING_INVOCATION_ID": "1",
            "PROJECT_PATH": str(self.root),
            "AGENT_MAIL_DB_PATH": str(db_path),
        }

        import subprocess
        agent_reply_py = Path(__file__).parents[2] / "director" / "agent_reply.py"

        res_file = self.root / "res_manual.json"
        res_file.write_text(json.dumps({"status": "COMPLETED", "summary": "manual check"}), encoding="utf-8")

        subprocess.check_call([sys.executable, str(agent_reply_py), "complete", "--result-file", "res_manual.json"], env=env)
        mails = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-MANUAL-TEST", db_path=db_path)
        self.assertEqual(len(mails), 1)

        body_data = json.loads(mails[0]["body"])
        generated_id = body_data["invocation_id"]

        self.assertTrue(generated_id.startswith("MANUAL-"))
        self.assertNotIn("INV-NOT-SET", generated_id)

        uuid_part = generated_id.split("-", 1)[1]
        self.assertEqual(len(uuid_part), 36)
        import re
        self.assertTrue(re.match(r"^[A-F0-9]{8}-[A-F0-9]{4}-4[A-F0-9]{3}-[89AB][A-F0-9]{3}-[A-F0-9]{12}$", uuid_part, re.IGNORECASE))

        # 2. Verify 1000 generations are distinct and have no duplicates
        generated_ids = set()
        for i in range(1000):
            single_id = new_manual_invocation_id()
            self.assertTrue(single_id.startswith("MANUAL-"))
            self.assertNotIn("INV-NOT-SET", single_id)

            uuid_p = single_id.split("-", 1)[1]
            self.assertEqual(len(uuid_p), 36)
            self.assertTrue(re.match(r"^[A-F0-9]{8}-[A-F0-9]{4}-4[A-F0-9]{3}-[89AB][A-F0-9]{3}-[A-F0-9]{12}$", uuid_p, re.IGNORECASE))

            generated_ids.add(single_id)

        self.assertEqual(len(generated_ids), 1000)


if __name__ == "__main__":
    unittest.main()
