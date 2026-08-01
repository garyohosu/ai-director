"""Strict validation of commander decision JSON."""

from __future__ import annotations

import json
import math
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
    raw_confidence = payload["confidence"]
    if isinstance(raw_confidence, bool):
        raise DecisionError("invalid confidence")
    if isinstance(raw_confidence, (int, float)):
        try:
            confidence_value = float(raw_confidence)
        except OverflowError as err:
            raise DecisionError("invalid confidence") from err
        if not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0:
            raise DecisionError("invalid confidence")
        confidence = (
            "HIGH"
            if confidence_value >= 0.85
            else "MEDIUM"
            if confidence_value >= 0.7
            else "LOW"
        )
    elif isinstance(raw_confidence, str) and raw_confidence in ALLOWED_CONFIDENCE:
        confidence = raw_confidence
    else:
        raise DecisionError("invalid confidence")
    for key in ("reason", "answer", "target_agent"):
        if not isinstance(payload[key], str):
            raise DecisionError(f"{key} must be a string")
    if not isinstance(payload["requires_human"], bool):
        raise DecisionError("requires_human must be boolean")
    if confidence == "LOW" and not payload["requires_human"]:
        raise DecisionError("LOW confidence requires human review")
    if payload["action"] == "HUMAN_REQUIRED" and not payload["requires_human"]:
        raise DecisionError("HUMAN_REQUIRED must set requires_human")
    normalized = {
        key: payload[key]
        for key in (
            "action",
            "job_id",
            "decision_id",
            "reason",
            "answer",
            "target_agent",
            "requires_human",
        )
    }
    normalized["confidence"] = confidence
    return Decision(**normalized)
