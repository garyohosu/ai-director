from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

# Add project root and mail to path for imports
import sys
sys.path.extend([str(Path(__file__).parents[2]), str(Path(__file__).parents[2] / "mail")])

from director.agent_reply import validate_and_read_result_file, validate_wait_payload, mask_secrets


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
        with self.assertRaises(UnicodeDecodeError):
            validate_and_read_result_file(self.root, "invalid_utf8.json")

    def test_deduplication_and_state_transitions(self):
        db_path = self.root / "mail.db"
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((Path(__file__).parents[2] / "mail" / name).read_bytes())

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
        subprocess.check_call([sys.executable, str(agent_reply_py), "ack"], env=env_dec2)
        mails_dec2 = find_mails(sender_uid=u1, recipient_uid=u2, request_id="JOB-DEDUP-001", db_path=db_path)
        self.assertEqual(len(mails_dec2), 5)


if __name__ == "__main__":
    unittest.main()
