from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

from director.tests import helper

from mail import initialize, send_mail, find_mails
from config import AgentDefinition
from dispatch import DispatchCycle, MailWatcher, OutcomeClassifier, RetryPolicy, ErrorNotifier, RoundTripCounter, CheckpointStore
from launcher import CliLauncher, CliPathResolver
from logging_utils import JobLogger
from mail_adapter import MailModuleAdapter, MailReplyQuery
from runtime import RuntimeStateStore, TerminalStateStore


class TestOneRoundTripProtocol(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        
        self.db_path = self.root / "mail.db"
        initialize(db_path=self.db_path)

        self.human_uid = "UID000004"
        self.claude_uid = "UID000005"
        self.system_uid = "UID000007"
        
        # Build mock agent command that calls agent_reply.py
        # Environment variables AGENT_UID, REPLY_TO_UID, JOB_ID, DECISION_ID, PROJECT_PATH will be injected by launcher
        mock_script = self.root / "mock_claude.py"
        mock_script.write_text("""from __future__ import annotations
import json
import os
import sys
import subprocess
from pathlib import Path

# Create required artifact file
project_path = Path(os.environ["PROJECT_PATH"]).resolve()
art_dir = project_path / "director" / "tests" / "artifacts"
art_dir.mkdir(parents=True, exist_ok=True)
art_file = art_dir / "protocol_test_result.txt"
art_file.write_text("Protocol Test Success Content", encoding="utf-8")

import hashlib
actual_sha = hashlib.sha256(b"Protocol Test Success Content").hexdigest()

res_data = {
    "status": "COMPLETED",
    "summary": "Protocol test executed cleanly",
    "artifacts": [
        {
            "path": "director/tests/artifacts/protocol_test_result.txt",
            "sha256": actual_sha
        }
    ],
    "tests": [],
    "qanda": []
}

res_path = project_path / "protocol_result.json"
res_path.write_text(json.dumps(res_data, ensure_ascii=False, indent=2), encoding="utf-8")

# Call agent_reply.py ack first
sub_env = os.environ.copy()
sub_env["AGENT_MAIL_DB_PATH"] = str(project_path / "mail.db")
subprocess.check_call([sys.executable, str(project_path / "director" / "agent_reply.py"), "ack"], env=sub_env)

# Call agent_reply.py complete second
subprocess.check_call([sys.executable, str(project_path / "director" / "agent_reply.py"), "complete", "--result-file", "protocol_result.json"], env=sub_env)
""", encoding="utf-8")

        # Copy director directory structure to root for test
        (self.root / "director").mkdir(parents=True, exist_ok=True)
        orig_agent_reply = Path(__file__).parents[2] / "director" / "agent_reply.py"
        (self.root / "director" / "agent_reply.py").write_text(orig_agent_reply.read_text(encoding="utf-8"), encoding="utf-8")
        orig_ids = Path(__file__).parents[2] / "director" / "ids.py"
        (self.root / "director" / "ids.py").write_text(
            orig_ids.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.root / "mail").mkdir(parents=True, exist_ok=True)
        for name in ["__init__.py", "agent_mail.py"]:
            (self.root / "mail" / name).write_bytes((helper.MAIL_PATH / "mail" / name).read_bytes())

        self.mail_adapter = MailModuleAdapter(self.root / "mail", db_path=self.db_path)
        self.claude_uid = self.mail_adapter.register_user("claude_designer")
        self.human_uid = self.mail_adapter.register_user("human_controller")
        self.system_uid = self.mail_adapter.register_user("orchestrator")

        self.agents = [
            AgentDefinition(name="claude_designer", uid=self.claude_uid, cli_type="claude_code", command=[sys.executable, str(mock_script)])
        ]

        from adapters.claude_code import ClaudeCodeCliAdapter
        adapters = {"claude_code": ClaudeCodeCliAdapter()}
        cli_resolver = CliPathResolver(adapters)
        launcher = CliLauncher(cli_resolver, adapters, logs_dir=self.root / "logs")
        watcher = MailWatcher(self.mail_adapter)
        logger = JobLogger(self.root / "logs")
        checkpoint_store = CheckpointStore(self.root / "checkpoints")
        runtime_store = RuntimeStateStore(self.root / "runtime")
        terminal_store = TerminalStateStore(self.root / "runtime")
        reply_query = MailReplyQuery(self.mail_adapter)
        notifier = ErrorNotifier(self.mail_adapter, self.system_uid)
        round_trips = RoundTripCounter(10, runtime_dir=self.root / "runtime")

        self.cycle = DispatchCycle(
            watcher=watcher,
            launcher=launcher,
            mail_adapter=self.mail_adapter,
            reply_query=reply_query,
            classifier=OutcomeClassifier(),
            retry_policy=RetryPolicy(1),
            notifier=notifier,
            logger=logger,
            checkpoint_store=checkpoint_store,
            round_trips=round_trips,
            runtime_store=runtime_store,
            terminal_store=terminal_store,
            project_path=self.root,
            cli_timeout_sec=30,
            reply_check_timeout_sec=30,
            agents=self.agents,
            system_sender_uid=self.system_uid,
            max_handoffs=3,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_one_round_trip_success(self):
        job_id = "JOB-PROTOCOL-TEST-001"
        decision_id = "DEC-PROT-001"

        # 1. Human sends request mail
        self.mail_adapter.send_mail(
            sender_uid=self.human_uid,
            recipient_uid=self.claude_uid,
            subject=f"[{job_id}] [{decision_id}] One roundtrip test request",
            body="Please perform protocol verification test.",
        )

        # 2. Run orchestrator single pass
        outcomes = self.cycle.run_one_pass(self.agents)
        self.assertTrue(len(outcomes) > 0)

        # 3. Verify ACK mail received
        ack_mails = self.mail_adapter.find_mails(
            sender_uid=self.claude_uid,
            recipient_uid=self.human_uid,
            request_id=job_id,
        )
        self.assertGreaterEqual(len(ack_mails), 2)  # ACK + COMPLETED

        ack_mail = next(m for m in ack_mails if "STATUS: ACK" in m["subject"])
        self.assertIn("ACK_RECEIVED", ack_mail["body"])

        # 4. Verify COMPLETED mail received
        complete_mail = next(m for m in ack_mails if "STATUS: COMPLETE" in m["subject"])
        comp_payload = json.loads(complete_mail["body"])
        self.assertEqual(comp_payload["status"], "COMPLETED")
        self.assertEqual(comp_payload["job_id"], job_id)
        self.assertEqual(comp_payload["decision_id"], decision_id)
        self.assertEqual(comp_payload["agent_uid"], self.claude_uid)

        # 5. Verify Artifact existence, content, and SHA-256
        art_path = self.root / "director" / "tests" / "artifacts" / "protocol_test_result.txt"
        self.assertTrue(art_path.is_file())
        art_content = art_path.read_text(encoding="utf-8")
        self.assertEqual(art_content, "Protocol Test Success Content")
        actual_sha = hashlib.sha256(b"Protocol Test Success Content").hexdigest()
        self.assertEqual(comp_payload["artifacts"][0]["sha256"], actual_sha)


if __name__ == "__main__":
    unittest.main()
