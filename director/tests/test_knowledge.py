import hashlib
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from knowledge import KnowledgeIndex, generate_index
from context_packet import ContextPacketBuilder


class KnowledgeIndexTests(unittest.TestCase):
    def test_generates_markdown_pages_and_answered_index(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "director").mkdir()
        (root / "director" / "SPEC.md").write_text("spec", encoding="utf-8")
        (root / "QandA.md").write_text("## Q006\n\n- Status: ANSWERED\n- Question: one\n", encoding="utf-8")
        paths = generate_index(root, generated_at="2026-01-01T00:00:00Z", source_commit="abc")
        self.assertEqual(len(paths), 5)
        self.assertIn("Q006", (root / "director/knowledge/decisions.md").read_text(encoding="utf-8"))
        self.assertIn('source_commit: "abc"', (root / "director/knowledge/INDEX.md").read_text(encoding="utf-8"))

    def test_selects_only_relevant_pages_and_records_hash(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "director").mkdir()
        (root / "director" / "SPEC.md").write_text("spec", encoding="utf-8")
        (root / "QandA.md").write_text("## Q006\n\n- Status: ANSWERED\n", encoding="utf-8")
        generate_index(root)
        selected = KnowledgeIndex(root).select("タイムアウトとWAITINGの判断")
        names = [name for name, _text, _digest in selected]
        self.assertEqual(names, ["protocols", "decisions", "operations"])
        for name, text, digest in selected:
            self.assertEqual(hashlib.sha256((root / "director/knowledge" / f"{name}.md").read_bytes()).hexdigest(), digest)
            self.assertIn("source_sha256", text)

    def test_context_packet_contains_selected_knowledge_once_and_is_bounded(self) -> None:
        root = Path(tempfile.mkdtemp())
        packet = ContextPacketBuilder(root, max_bytes=4096).build(
            "JOB-1", "DEC-1", role="commander", task="question", state="DECISION_PENDING",
            completed=[], unresolved=["question"], qanda=[], spec_sections=[], target_files=[],
            git_summary="", test_summary="", prohibitions=[], reply_commands=[], completion="done",
            path=root / "packet.md", knowledge_pages=[("protocols", "protocol body", "a" * 64)],
        )
        text = packet.path.read_text(encoding="utf-8")
        self.assertLessEqual(packet.byte_size, 4096)
        self.assertEqual(text.count("protocols.md"), 1)
        self.assertEqual(packet.estimated_tokens, max(1, len(text) // 4))


if __name__ == "__main__":
    unittest.main()
