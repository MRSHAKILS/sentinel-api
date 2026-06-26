"""Orchestrator: turn a validated request into the response dict.

Pure and deterministic. Every enum value is sourced from config constants, and a
final clamp guarantees the output can never contain an out-of-spec enum (a bad
reasoning branch degrades gracefully instead of emitting an invalid schema).
"""

from __future__ import annotations

from typing import Optional

from . import config as C
from .classify import classify
from .extract import extract
from .matching import match
from .responses import render
from .routing import (
    confidence_of,
    department_of,
    needs_human_review,
    severity_of,
)
from .safety import scrub_customer_reply, scrub_recommended_action
from .schemas import AnalyzeRequest, parse_transactions


def _build_reason_codes(
    case_type: str,
    verdict: str,
    relevant_id: Optional[str],
    meta: dict,
    injection: bool,
    human_review: bool,
    high_value: bool,
) -> list[str]:
    codes: list[str] = [case_type]

    if case_type == C.PHISHING:
        codes += ["credential_protection", "critical_escalation"]

    if verdict == C.CONSISTENT and relevant_id is not None:
        codes.append("transaction_match")
    elif verdict == C.INCONSISTENT:
        codes.append("evidence_inconsistent")
        if meta.get("established_recipient"):
            codes.append("established_recipient_pattern")
        if meta.get("status_contradiction"):
            codes.append("status_contradiction")
        if meta.get("single_charge_only"):
            codes.append("single_charge_only")
    elif verdict == C.INSUFFICIENT:
        if meta.get("ambiguous"):
            codes += ["ambiguous_match", "needs_clarification"]
        elif case_type == C.OTHER:
            codes += ["vague_complaint", "needs_clarification"]
        elif case_type != C.PHISHING:
            codes += ["insufficient_evidence", "needs_clarification"]

    if meta.get("duplicate_of"):
        codes.append("duplicate_detected")
    if high_value:
        codes.append("high_value")
    if human_review:
        codes.append("human_review")
    if injection:
        codes.append("prompt_injection_ignored")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    return [c for c in codes if not (c in seen or seen.add(c))]


def _clamp(value: str, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def analyze(req: AnalyzeRequest) -> dict:
    history = parse_transactions(req.transaction_history)
    feats = extract(req.complaint, req.language)

    case_type = classify(feats, req.user_type, history)
    relevant_id, verdict, meta = match(case_type, feats, history)
    amount = meta.get("amount")

    severity = severity_of(case_type, verdict, relevant_id, amount)
    department = department_of(case_type, req.user_type, severity, verdict)
    human_review = needs_human_review(case_type, verdict, relevant_id, severity, amount)

    texts = render(case_type, verdict, relevant_id, meta, feats, req.user_type)
    customer_reply = scrub_customer_reply(texts["customer_reply"], feats.reply_lang)
    next_action = scrub_recommended_action(texts["recommended_next_action"])

    high_value = (
        amount is not None
        and amount >= C.HIGH_VALUE_BDT
        and case_type in C.MONEY_MOVEMENT_CASES
    )
    reason_codes = _build_reason_codes(
        case_type, verdict, relevant_id, meta, feats.injection, human_review, high_value
    )

    # Final enum clamp — guarantees a schema-valid response no matter what.
    return {
        "ticket_id": req.ticket_id,
        "relevant_transaction_id": relevant_id,
        "evidence_verdict": _clamp(verdict, C.EVIDENCE_VERDICTS, C.INSUFFICIENT),
        "case_type": _clamp(case_type, C.CASE_TYPES, C.OTHER),
        "severity": _clamp(severity, C.SEVERITIES, C.MEDIUM),
        "department": _clamp(department, C.DEPARTMENTS, C.CUSTOMER_SUPPORT),
        "agent_summary": texts["agent_summary"],
        "recommended_next_action": next_action,
        "customer_reply": customer_reply,
        "human_review_required": bool(human_review),
        "confidence": round(float(confidence_of(case_type, verdict, meta)), 2),
        "reason_codes": reason_codes,
    }
