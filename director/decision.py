"""Strict validation of commander decision JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass

ALLOWED_ACTIONS = {"ANSWER", "CONTINUE", "REVISE", "DELEGATE", "COMPLETE", "HUMAN_REQUIRED", "REJECT"}
ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


class DecisionError(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    action: str
    job_id: str
    decision_id: str
    confidence: str
    reason: str
    answer: str
    target_agent: str
    requires_human: bool


def parse_decision(raw: str, *, expected_job_id: str, expected_decision_id: str) -> Decision:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        raise DecisionError(f"invalid decision JSON: {err.msg}") from err
    if not isinstance(payload, dict):
        raise DecisionError("decision must be a JSON object")
    required = {"action", "job_id", "decision_id", "confidence", "reason", "answer", "target_agent", "requires_human"}
    missing = sorted(required - payload.keys())
    if missing:
        raise DecisionError(f"missing decision fields: {', '.join(missing)}")
    if payload["action"] not in ALLOWED_ACTIONS:
        raise DecisionError("unsupported decision action")
    if payload["job_id"] != expected_job_id or payload["decision_id"] != expected_decision_id:
        raise DecisionError("Job-ID or Decision-ID mismatch")
    if payload["confidence"] not in ALLOWED_CONFIDENCE:
        raise DecisionError("invalid confidence")
    for key in ("reason", "answer", "target_agent"):
        if not isinstance(payload[key], str):
            raise DecisionError(f"{key} must be a string")
    if not isinstance(payload["requires_human"], bool):
        raise DecisionError("requires_human must be boolean")
    if payload["confidence"] == "LOW" and not payload["requires_human"]:
        raise DecisionError("LOW confidence requires human review")
    if payload["action"] == "HUMAN_REQUIRED" and not payload["requires_human"]:
        raise DecisionError("HUMAN_REQUIRED must set requires_human")
    return Decision(**{key: payload[key] for key in ("action", "job_id", "decision_id", "confidence", "reason", "answer", "target_agent", "requires_human")})
