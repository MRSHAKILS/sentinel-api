"""Transaction matching + evidence verdict — the evidence-reasoning core.

Returns (relevant_transaction_id, evidence_verdict, meta) where `meta` carries
the chosen Txn and explanation flags used to render text and reason codes.
"""

from __future__ import annotations

import re
from typing import Optional

from . import config as C
from .extract import Features
from .schemas import Txn

MatchResult = tuple[Optional[str], str, dict]


def _amount_matches(txn: Txn, feats: Features) -> bool:
    if txn.amount is None or not feats.amounts:
        return False
    return any(abs(txn.amount - a) <= C.AMOUNT_TOLERANCE for a in feats.amounts)


def _norm_counterparty(cp: Optional[str]) -> Optional[str]:
    if not cp:
        return None
    digits = re.sub(r"\D", "", cp)
    if len(digits) >= 10:
        return digits[-10:]
    return cp.upper()


def _counterparty_matches(txn: Txn, feats: Features) -> bool:
    norm = _norm_counterparty(txn.counterparty)
    if not norm:
        return False
    if norm in feats.phones:
        return True
    return norm in feats.tokens


def _sort_key(txn: Txn, index: int) -> tuple[float, int]:
    # Order by time when known, else by position in the supplied history.
    return (txn.epoch if txn.epoch is not None else float(index), index)


def _established_recipient(txn: Txn, history: list[Txn]) -> bool:
    if not txn.counterparty:
        return False
    count = sum(
        1
        for t in history
        if t.type == C.TXN_TRANSFER and t.counterparty == txn.counterparty
    )
    return count >= C.ESTABLISHED_RECIPIENT_MIN


def _verdict_for(
    case_type: str, txn: Txn, history: list[Txn], feats: Features
) -> tuple[str, dict]:
    meta: dict = {
        "txn": txn,
        "amount": txn.amount,
        "counterparty": txn.counterparty,
    }

    if case_type == C.WRONG_TRANSFER and _established_recipient(txn, history):
        meta["established_recipient"] = True
        return C.INCONSISTENT, meta

    if case_type == C.PAYMENT_FAILED and txn.status == C.STATUS_COMPLETED:
        meta["status_contradiction"] = True
        return C.INCONSISTENT, meta

    return C.CONSISTENT, meta


def _match_duplicate(feats: Features, history: list[Txn]) -> MatchResult:
    payments = [t for t in history if t.type == C.TXN_PAYMENT and t.amount is not None]
    indexed = {id(t): i for i, t in enumerate(history)}

    groups: dict[tuple, list[Txn]] = {}
    for t in payments:
        groups.setdefault((round(t.amount, 2), t.counterparty), []).append(t)
    pairs = [g for g in groups.values() if len(g) >= 2]

    # Prefer the duplicate group whose amount matches the complaint.
    chosen = None
    for g in pairs:
        if feats.amounts and any(
            abs(g[0].amount - a) <= C.AMOUNT_TOLERANCE for a in feats.amounts
        ):
            chosen = g
            break
    if chosen is None and pairs:
        chosen = max(pairs, key=len)

    if chosen:
        ordered = sorted(chosen, key=lambda t: _sort_key(t, indexed[id(t)]))
        suspected = ordered[-1]  # the later charge is the suspected duplicate
        original = ordered[0]
        return (
            suspected.transaction_id,
            C.CONSISTENT,
            {
                "txn": suspected,
                "amount": suspected.amount,
                "counterparty": suspected.counterparty,
                "duplicate_of": original.transaction_id,
            },
        )

    # Customer claims a duplicate but only a single matching charge exists ->
    # the data does not support the duplication claim.
    matching = [t for t in payments if _amount_matches(t, feats)]
    if len(matching) == 1:
        t = matching[0]
        return (
            t.transaction_id,
            C.INCONSISTENT,
            {"txn": t, "amount": t.amount, "counterparty": t.counterparty,
             "single_charge_only": True},
        )
    return None, C.INSUFFICIENT, {"no_duplicate_found": True}


def match(case_type: str, feats: Features, history: list[Txn]) -> MatchResult:
    affinity = C.TYPE_AFFINITY[case_type]

    # Phishing (and any empty-affinity case) never points at a transaction.
    if affinity == ():
        return None, C.INSUFFICIENT, {"no_relevant_txn": True}
    if not history:
        return None, C.INSUFFICIENT, {"empty_history": True}

    if case_type == C.DUPLICATE_PAYMENT:
        return _match_duplicate(feats, history)

    candidates = (
        list(history) if affinity is None else [t for t in history if t.type in affinity]
    )
    if not candidates:
        return None, C.INSUFFICIENT, {"no_candidate_type": True}

    expected_status = C.EXPECTED_STATUS.get(case_type)
    scored = []
    for i, t in enumerate(candidates):
        amt = _amount_matches(t, feats)
        cp = _counterparty_matches(t, feats)
        score = 0.0
        if amt:
            score += 3.0
        if cp:
            score += 2.0
        if expected_status and t.status == expected_status:
            score += 1.0
        scored.append({"score": score, "amt": amt, "cp": cp, "txn": t, "i": i})

    top = max(s["score"] for s in scored)
    # A "strong" match needs an amount or counterparty signal (not just a status
    # bonus) and must tie for the top score.
    strong = [s for s in scored if (s["amt"] or s["cp"]) and s["score"] == top]

    if len(strong) == 1:
        t = strong[0]["txn"]
        verdict, meta = _verdict_for(case_type, t, history, feats)
        return t.transaction_id, verdict, meta

    if len(strong) > 1:
        # Disambiguate by an explicit counterparty match if exactly one has it.
        cp_only = [s for s in strong if s["cp"]]
        if len(cp_only) == 1:
            t = cp_only[0]["txn"]
            verdict, meta = _verdict_for(case_type, t, history, feats)
            return t.transaction_id, verdict, meta
        return None, C.INSUFFICIENT, {"ambiguous": True, "candidates": len(strong)}

    # No amount/counterparty match. Weak fallback: a specific-affinity case with
    # exactly one candidate of the expected type (amount may be non-numeric).
    if affinity is not None and len(candidates) == 1:
        t = candidates[0]
        verdict, meta = _verdict_for(case_type, t, history, feats)
        meta["weak_match"] = True
        return t.transaction_id, verdict, meta

    return None, C.INSUFFICIENT, {"no_match": True}
