"""Severity, department, human-review, and confidence derivation.

These rules reproduce all ten public sample expectations and generalise via the
declarative tables in config.py.
"""

from __future__ import annotations

from typing import Optional

from . import config as C


def _is_money_movement(case_type: str) -> bool:
    return case_type in C.MONEY_MOVEMENT_CASES


def _is_high_value(case_type: str, amount: Optional[float]) -> bool:
    return (
        amount is not None
        and amount >= C.HIGH_VALUE_BDT
        and _is_money_movement(case_type)
    )


def severity_of(
    case_type: str, verdict: str, relevant_id: Optional[str], amount: Optional[float]
) -> str:
    if case_type == C.PHISHING:
        return C.CRITICAL

    sev = C.BASE_SEVERITY[case_type]

    if case_type == C.WRONG_TRANSFER:
        # A contradicted or unidentifiable wrong-transfer claim is less severe
        # than a clean, confirmed one.
        if verdict == C.INCONSISTENT or relevant_id is None:
            sev = C.MEDIUM

    if _is_high_value(case_type, amount) and C.SEVERITY_RANK[sev] < C.SEVERITY_RANK[C.HIGH]:
        sev = C.HIGH

    return sev


def department_of(
    case_type: str, user_type: Optional[str], severity: str, verdict: str
) -> str:
    dept = C.BASE_DEPARTMENT[case_type]

    if case_type == C.REFUND_REQUEST:
        # A contested or high-value refund becomes a dispute, not plain support.
        if verdict == C.INCONSISTENT or C.SEVERITY_RANK[severity] >= C.SEVERITY_RANK[C.HIGH]:
            dept = C.DISPUTE_RESOLUTION

    return dept


def needs_human_review(
    case_type: str,
    verdict: str,
    relevant_id: Optional[str],
    severity: str,
    amount: Optional[float],
) -> bool:
    if case_type == C.PHISHING:
        return True
    if (
        case_type in (C.WRONG_TRANSFER, C.DUPLICATE_PAYMENT, C.AGENT_CASH_IN_ISSUE)
        and relevant_id is not None
    ):
        return True
    if verdict == C.INCONSISTENT:
        return True
    if severity == C.CRITICAL:
        return True
    if _is_high_value(case_type, amount):
        return True
    return False


def confidence_of(
    case_type: str, verdict: str, meta: dict
) -> float:
    if case_type == C.PHISHING:
        return 0.95
    if verdict == C.CONSISTENT:
        if case_type == C.DUPLICATE_PAYMENT:
            return 0.92
        if meta.get("weak_match"):
            return 0.7
        return 0.9
    if verdict == C.INCONSISTENT:
        return 0.75
    if meta.get("ambiguous"):
        return 0.65
    return 0.6
