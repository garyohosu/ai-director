"""Strict, line-oriented QandA.md parser and updater."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class QandaError(ValueError):
    pass


REQUIRED = ("Status", "Request-ID", "From", "To", "Severity", "Blocking", "Category", "Question", "Proposed-Answer", "Evidence")
HEADER_RE = re.compile(r"^## (Q[0-9]{3,})\s*$")
FIELD_RE = re.compile(r"^- ([A-Za-z][A-Za-z0-9-]*):(?: (.*))?$")


@dataclass(frozen=True)
class Question:
    number: str
    fields: dict[str, str]
    raw: str

    @property
    def status(self) -> str:
        return self.fields["Status"]

    @property
    def blocking(self) -> bool:
        return self.fields.get("Blocking", "").upper() == "YES"


def _parse_block(number: str, lines: list[str]) -> Question:
    fields: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        match = FIELD_RE.fullmatch(line)
        if not match:
            raise QandaError(f"unparseable Q&A line in {number}: {line!r}")
        key, value = match.group(1), match.group(2) or ""
        if key in fields:
            raise QandaError(f"duplicate Q&A field {key!r} in {number}")
        fields[key] = value
    missing = [key for key in REQUIRED if key not in fields]
    if missing:
        raise QandaError(f"missing Q&A fields in {number}: {', '.join(missing)}")
    if fields["Status"] not in {"OPEN", "ANSWERED"}:
        raise QandaError(f"unsupported Q&A status in {number}: {fields['Status']!r}")
    if fields["Blocking"] not in {"YES", "NO"}:
        raise QandaError(f"invalid Blocking value in {number}")
    return Question(number, fields, "\n".join([f"## {number}", *lines]))


def parse_qanda(path: Path) -> list[Question]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("\ufeff"):
        lines[0] = lines[0].lstrip("\ufeff")
    blocks: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in lines:
        header = HEADER_RE.fullmatch(line)
        if header:
            if current:
                blocks.append(current)
            current = (header.group(1), [])
        elif current is not None:
            current[1].append(line)
        elif line.strip() and not line.startswith("# QandA"):
            raise QandaError(f"content outside Q&A block: {line!r}")
    if current:
        blocks.append(current)
    return [_parse_block(number, body) for number, body in blocks]


def find_open_blocking(path: Path, request_id: str | None = None) -> list[Question]:
    questions = parse_qanda(path)
    return [q for q in questions if q.status == "OPEN" and q.blocking and (request_id is None or q.fields["Request-ID"] == request_id)]


def find_answered(question_list: list[Question], question: Question) -> Question | None:
    for candidate in question_list:
        if candidate.status == "ANSWERED" and candidate.fields.get("Question") == question.fields.get("Question"):
            return candidate
    return None


def answer_question(path: Path, number: str, *, decision: str, reason: str, answered_by: str = "director") -> None:
    questions = parse_qanda(path)
    target = next((q for q in questions if q.number == number), None)
    if target is None:
        raise QandaError(f"unknown Q&A number: {number}")
    if target.status == "ANSWERED":
        return
    fields = dict(target.fields)
    fields["Status"] = "ANSWERED"
    fields["Answered-By"] = answered_by
    fields["Decision"] = decision
    fields["Reason"] = reason
    fields["Answered-At"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rendered: list[str] = ["# QandA.md", ""]
    for question in questions:
        rendered.append(f"## {question.number}")
        source = fields if question.number == number else question.fields
        for key, value in source.items():
            rendered.append(f"- {key}: {value}")
        rendered.append("")
    path.write_text("\n".join(rendered), encoding="utf-8")
