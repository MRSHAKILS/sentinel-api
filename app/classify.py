"""Case-type classification via an explicit priority cascade.

Order matters: complaints overlap (a "failed + deducted + please refund" ticket
contains refund wording but is really a payment_failed case), so the most
specific / most safety-critical categories are tested first.
"""

from __future__ import annotations

import re

from . import config as C
from .extract import Features
from .schemas import Txn

# Credential terms / solicitation context (latin, word-bounded so "pin" does not
# match inside "shopping" and "ask" not inside "task").
_CRED_RE = re.compile(
    r"\b(otp|one[-\s]?time\s?password|verification\s?code|security\s?code|pin|"
    r"password|pass\s?word|cvv|card\s?(?:number|no))\b",
    re.IGNORECASE,
)
_SOLICIT_RE = re.compile(
    r"\b(ask(?:ed|ing|s)?|share|shared|sharing|send|sending|give|giving|"
    r"want(?:s|ed)?|provide|tell|telling|call(?:ed|ing)?|sms|messag\w*|"
    r"text(?:ed)?|link|claim(?:s|ed|ing)?|pretend\w*|disclose|reveal)\b",
    re.IGNORECASE,
)


def _is_phishing(feats: Features) -> bool:
    if feats.has(C.PHISHING_STRONG):
        return True
    low = feats.lower
    has_cred = bool(_CRED_RE.search(low)) or any(
        t in low for t in C.CREDENTIAL_TERMS_BN
    )
    has_solicit = bool(_SOLICIT_RE.search(low)) or any(
        t in low for t in C.SOLICIT_CONTEXT_BN
    )
    return has_cred and has_solicit

# Light payment-context gate so a coincidental duplicate pair in history doesn't
# hijack a non-payment complaint.
_PAYMENT_CONTEXT = (
    "paid", "pay", "payment", "bill", "charge", "charged", "deduct", "debited",
    "recharge", "পেমেন্ট", "বিল", "কেটে", "পরিশোধ",
)


def _has_duplicate_pair(history: list[Txn]) -> bool:
    """True if two payments share amount + counterparty within the window."""
    payments = [t for t in history if t.type == C.TXN_PAYMENT and t.amount is not None]
    groups: dict[tuple, list[Txn]] = {}
    for t in payments:
        groups.setdefault((round(t.amount, 2), t.counterparty), []).append(t)
    for group in groups.values():
        if len(group) < 2:
            continue
        epochs = sorted(t.epoch for t in group if t.epoch is not None)
        if len(epochs) < 2:
            return True  # same amount+counterparty, timestamps unknown -> treat as dup
        for i in range(len(epochs) - 1):
            if epochs[i + 1] - epochs[i] <= C.DUPLICATE_WINDOW_SECONDS:
                return True
    return False


def classify(feats: Features, user_type: str | None, history: list[Txn]) -> str:
    has = feats.has

    # 1. Phishing / social engineering — safety-first, always wins.
    if _is_phishing(feats):
        return C.PHISHING

    payment_failed_kw = has(C.PAYMENT_FAILED_KEYWORDS)
    payment_context = any(k in feats.lower for k in _PAYMENT_CONTEXT)

    # 2. Duplicate payment — explicit wording, or a real duplicate pair in a
    #    payment-context complaint that isn't already a clear failure report.
    if has(C.DUPLICATE_KEYWORDS) or (
        _has_duplicate_pair(history) and payment_context and not payment_failed_kw
    ):
        return C.DUPLICATE_PAYMENT

    # 3. Payment failed (with possible balance deduction).
    if payment_failed_kw:
        return C.PAYMENT_FAILED

    # 4. Agent cash-in not reflected.
    if has(C.AGENT_CASH_IN_KEYWORDS):
        return C.AGENT_CASH_IN_ISSUE

    # 5. Merchant settlement delay.
    if has(C.SETTLEMENT_KEYWORDS) or (
        (user_type or "").lower() == C.USER_MERCHANT
        and any(t.type == C.TXN_SETTLEMENT for t in history)
    ):
        return C.MERCHANT_SETTLEMENT_DELAY

    # 6. Wrong transfer / money not received by intended recipient.
    if has(C.WRONG_TRANSFER_KEYWORDS):
        return C.WRONG_TRANSFER

    # 7. Refund request (change of mind, no service failure).
    if has(C.REFUND_KEYWORDS):
        return C.REFUND_REQUEST

    # 8. Fallback.
    return C.OTHER
