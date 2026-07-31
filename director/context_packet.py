"""Bounded external-memory packet construction for commander mail."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PacketResult:
    path: Path
    byte_size: int
    estimated_tokens: int
    truncated: bool
    source_sha256: str | None


class ContextPacketBuilder:
    def __init__(self, root: Path, max_bytes: int = 32 * 1024) -> None:
        if max_bytes < 96:
            raise ValueError("max_bytes is too small to preserve truncation metadata")
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes

    def build(self, job_id: str, decision_id: str, *, role: str, task: str, state: str,
              completed: list[str], unresolved: list[str], qanda: list[str], spec_sections: list[str],
              target_files: list[str], git_summary: str, test_summary: str, prohibitions: list[str],
              reply_commands: list[str], completion: str, path: Path) -> PacketResult:
        sections = [
            f"# Context Packet\n\n- Job-ID: {job_id}\n- Decision-ID: {decision_id}\n- Role: {role}\n- State: {state}\n",
            "## This task\n" + task + "\n",
            "## Completed\n" + self._bullets(completed),
            "## Unresolved\n" + self._bullets(unresolved),
            "## Related Q&A\n" + self._bullets(qanda),
            "## SPEC sections\n" + self._bullets(spec_sections),
            "## Target files\n" + self._bullets(target_files),
            "## Git diff summary\n" + git_summary + "\n",
            "## Latest tests\n" + test_summary + "\n",
            "## Prohibitions\n" + self._bullets(prohibitions),
            "## Required reply commands\n" + self._bullets(reply_commands),
            "## Completion condition\n" + completion + "\n",
        ]
        text = "\n".join(sections)
        raw = text.encode("utf-8")
        truncated = len(raw) > self.max_bytes
        source_sha = hashlib.sha256(raw).hexdigest() if truncated else None
        if truncated:
            footer = f"\n\n[TRUNCATED] source_sha256={source_sha}\n".encode("utf-8")
            keep = max(0, self.max_bytes - len(footer))
            text = raw[:keep].decode("utf-8", errors="ignore") + footer.decode("utf-8")
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        size = path.stat().st_size
        return PacketResult(path, size, max(1, len(text) // 4), truncated, source_sha)

    @staticmethod
    def _bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) + "\n" if items else "- なし\n"
