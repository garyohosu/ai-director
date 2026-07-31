"""Generate and select the director's bounded Markdown knowledge index.

The index is advisory memory.  QandA.md and SPEC.md remain authoritative.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PAGES = ("architecture", "protocols", "operations", "decisions")
_ANSWERED = re.compile(r"^## (Q[0-9]{3,})$", re.MULTILINE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def _metadata(root: Path, generated_at: str, source_commit: str) -> str:
    sources = ["director/SPEC.md", "QandA.md"]
    lines = ["---", f'source_commit: "{source_commit}"', f'generated_at: "{generated_at}"', "source_files:"]
    lines.extend(f"  - {source}" for source in sources)
    lines.append("source_sha256:")
    lines.extend(f"  {source}: {_sha256(root / source)}" for source in sources)
    lines.append("---\n")
    return "\n".join(lines)


def _answered_index(root: Path) -> str:
    path = root / "QandA.md"
    if not path.is_file():
        return "- QandA.md がありません。"
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"(?m)^## ", text)
    rows: list[str] = []
    for block in blocks[1:]:
        number = block.splitlines()[0].strip()
        if "- Status: ANSWERED" in block:
            question = next((line[2:] for line in block.splitlines() if line.startswith("- Question: ")), "")
            rows.append(f"- [{number}](../../QandA.md#{number.lower()}): {question}")
    return "\n".join(rows) if rows else "- ANSWERED項目はありません。"


_CONTENTS = {
    "architecture": """# Architecture

mailは配送、orchestratorはCLI起動・監視、directorは判断の進行管理を担う。

基本遷移は `DISCOVERED → ACK_SENT → DELEGATION_PENDING → WORKER_RUNNING`。Blocking質問は `WORKER_WAITING_QUESTION → WAITING_FOR_DECISION → DECISION_PENDING`、回答後は `ANSWER_PENDING → WORKER_RESUMED → VERIFYING` と進む。成果物と終端通知を検証して `OUTBOX_PENDING → COMPLETED` とする。判断不能・タイムアウト・解析不能は `HUMAN_REQUIRED`。

Knowledge Indexは短い参照情報であり、正式仕様を変更しない。詳細は `director/SPEC.md` を参照する。
""",
    "protocols": """# Protocols

- ACKは受信確認であり、完了ではない。
- WAITING_FOR_DECISIONはCLI起動単位の正常終了で、Job全体は非終端。
- COMPLETEDは終端通知であり、成果物の相対パスとSHA-256を検証する。
- Job-IDとDecision-IDは件名・本文・状態JSONで一致させる。
- 質問後は同一CLIで回答を待たず、wait通知後に終了し、新規コンテキストで再開する。

正式な送信・検証規則は `director/SPEC.md` と `QandA.md` を優先する。
""",
    "operations": """# Operations

CLI内部タイムアウト < orchestrator監視期限 < テストハーネス外側タイムアウトとする。orchestratorはmailの公開 `find_mails()` だけでWAITING/終端通知を非破壊検索し、検知後に短い猶予を与える。自然終了しなければ既存の安全停止処理を使い、WAITING検知をTIMEOUTに分類しない。

通常のTIMEOUT、RATE_LIMITED、クラッシュ、通知失敗は構造化記録を残し、必要なら `HUMAN_REQUIRED` とする。directorはAI CLIを直接起動しない。
""",
    "decisions": """# Decisions

QandA.mdでANSWEREDになった正式判断の索引。内容の正本は必ずQandA.mdにある。

{answered}
""",
}


def generate_index(root: Path, *, generated_at: str | None = None, source_commit: str | None = None) -> list[Path]:
    root = Path(root).resolve()
    directory = root / "director" / "knowledge"
    directory.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or _utc_now()
    source_commit = source_commit or _commit(root)
    written: list[Path] = []
    for page in PAGES:
        content = _CONTENTS[page].format(answered=_answered_index(root))
        path = directory / f"{page}.md"
        path.write_text(_metadata(root, generated_at, source_commit) + content.strip() + "\n", encoding="utf-8")
        written.append(path)
    index = directory / "INDEX.md"
    index_body = """# Knowledge Index

正式情報の優先順位は、人間の最新指示、QandA.mdのANSWERED、SPEC.md、COMPONENTS.md、Job状態とcheckpoint、Knowledge Index、mail履歴、AIの提案の順とする。矛盾時はQandA.mdとSPEC.mdを優先する。

- [architecture.md](architecture.md): 責務と状態遷移
- [protocols.md](protocols.md): ACK、WAITING、COMPLETED、ID規則
- [operations.md](operations.md): 監視、タイムアウト、復旧、引継ぎ
- [decisions.md](decisions.md): ANSWERED Q&Aの索引

必要なページだけをContext Packetへ含め、全ページ・全SPEC・全Q&Aを毎回転載しない。
"""
    index.write_text(_metadata(root, generated_at, source_commit) + index_body.strip() + "\n", encoding="utf-8")
    written.insert(0, index)
    return written


class KnowledgeIndex:
    def __init__(self, root: Path, max_page_bytes: int = 12 * 1024) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / "director" / "knowledge"
        self.max_page_bytes = max_page_bytes

    def select(self, question: str) -> list[tuple[str, str, str]]:
        self.ensure_current()
        lowered = question.lower()
        names = ["protocols", "decisions"]
        if any(word in lowered for word in ("state", "状態", "責務", "architecture", "遷移")):
            names.append("architecture")
        if any(word in lowered for word in ("timeout", "タイムアウト", "waiting", "復旧", "引継ぎ")):
            names.append("operations")
        selected: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            path = self.directory / f"{name}.md"
            if not path.is_file():
                raise FileNotFoundError(path)
            raw = path.read_bytes()
            if len(raw) > self.max_page_bytes:
                raise ValueError(f"knowledge page exceeds limit: {name}")
            selected.append((name, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()))
        return selected

    def ensure_current(self) -> None:
        expected = {_source: _sha256(self.root / _source) for _source in ("director/SPEC.md", "QandA.md")}
        index = self.directory / "INDEX.md"
        if not index.is_file():
            generate_index(self.root)
            return
        text = index.read_text(encoding="utf-8")
        if any(f"  {_source}: {digest}" not in text for _source, digest in expected.items()):
            generate_index(self.root)
