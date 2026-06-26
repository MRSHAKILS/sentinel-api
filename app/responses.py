"""Deterministic text generation: agent_summary, recommended_next_action,
customer_reply.

- agent_summary / recommended_next_action are agent-facing -> always English
  (matches the sample pack, where even the Bangla case has an English summary).
- customer_reply is localised to the customer's language (bn or en).
- Templates inject only structured transaction data (id/amount/counterparty),
  never raw complaint text, so embedded prompt-injection can't reach the output.
- Every customer_reply is built from vetted safe phrases and additionally passes
  through safety.scrub_customer_reply in the pipeline.
"""

from __future__ import annotations

from typing import Optional

from . import config as C
from .extract import Features

# Exact safe phrases (kept identical to safety.py allow-list where relevant).
PIN_LINE = {
    "en": "Please do not share your PIN or OTP with anyone.",
    "bn": "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।",
}
ELIGIBLE = {
    "en": "any eligible amount will be returned through official channels",
    "bn": "যেকোনো প্রযোজ্য পরিমাণ অফিসিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে",
}


def fmt_amount(a: Optional[float]) -> Optional[str]:
    if a is None:
        return None
    try:
        f = float(a)
    except (TypeError, ValueError):
        return None
    return str(int(f)) if f.is_integer() else f"{f:.2f}"


def _amt_phrase(amt: Optional[str]) -> str:
    return f"{amt} BDT" if amt else "the reported amount"


def _txn_ref(txn: Optional[str]) -> str:
    return f"transaction {txn}" if txn else "the transaction"


def render(
    case_type: str,
    verdict: str,
    relevant_id: Optional[str],
    meta: dict,
    feats: Features,
    user_type: Optional[str],
) -> dict:
    lang = feats.reply_lang
    amt = fmt_amount(meta.get("amount"))
    cp = meta.get("counterparty")
    txn = relevant_id
    txn_obj = meta.get("txn")
    status = getattr(txn_obj, "status", None) or C.STATUS_PENDING
    pin = PIN_LINE[lang]
    elig = ELIGIBLE[lang]

    # ---------------------------------------------------------------- phishing
    if case_type == C.PHISHING:
        summary = (
            "Customer reports an unsolicited contact requesting credentials or "
            "claiming to be from the company. Likely social engineering; treat as "
            "a fraud risk."
        )
        action = (
            "Escalate to the fraud_risk team immediately. Reassure the customer "
            "that the company never asks for OTP or PIN, and log the reported "
            "contact for fraud-pattern analysis."
        )
        if lang == "bn":
            reply = (
                "তথ্য শেয়ার করার আগে আমাদের জানানোর জন্য ধন্যবাদ। আমরা কখনোই আপনার "
                "পিন, ওটিপি বা পাসওয়ার্ড চাই না। কেউ আমাদের নাম করলেও এগুলো কারো সাথে "
                "শেয়ার করবেন না। আমাদের ফ্রড টিমকে বিষয়টি জানানো হয়েছে।"
            )
        else:
            reply = (
                "Thank you for reaching out before sharing any information. We never "
                "ask for your PIN, OTP, or password under any circumstances. Please "
                "do not share these with anyone, even if they claim to be from us. "
                "Our fraud team has been notified of this incident."
            )
        return _bundle(summary, action, reply)

    # ----------------------------------------------------------- wrong_transfer
    if case_type == C.WRONG_TRANSFER:
        if relevant_id is None:  # ambiguous / insufficient
            summary = (
                f"Customer reports a {_amt_phrase(amt)} transfer was not received by "
                "the intended recipient, but more than one transaction matches and "
                "the correct one cannot be determined without further detail."
            )
            action = (
                "Ask the customer for the recipient's number to identify the correct "
                "transaction. Do not initiate a dispute until the transaction is "
                "confirmed."
            )
            if lang == "bn":
                reply = (
                    f"যোগাযোগ করার জন্য ধন্যবাদ। আমরা {_amt_bn(amt)} এর একাধিক লেনদেন "
                    "দেখতে পাচ্ছি। সঠিক লেনদেনটি শনাক্ত করতে অনুগ্রহ করে প্রাপকের নম্বরটি "
                    f"জানান। {pin}"
                )
            else:
                reply = (
                    f"Thank you for reaching out. We can see more than one transaction "
                    f"of {_amt_phrase(amt)} around that time. Could you share the "
                    f"recipient's number so we can identify the right one? {pin}"
                )
            return _bundle(summary, action, reply)

        if verdict == C.INCONSISTENT:
            summary = (
                f"Customer claims {_txn_ref(txn)} ({_amt_phrase(amt)} to {cp}) was a "
                "wrong transfer, but the history shows prior transfers to the same "
                "counterparty, suggesting an established recipient."
            )
            action = (
                "Flag for human review. Confirm with the customer whether this was "
                "genuinely a wrong transfer given the established pattern with this "
                "recipient before taking any action."
            )
        else:
            summary = (
                f"Customer reports sending {_amt_phrase(amt)} via {txn} to {cp}, which "
                "they now believe went to the wrong recipient."
            )
            action = (
                f"Verify {txn} details with the customer and initiate the "
                "wrong-transfer dispute workflow per policy."
            )
        reply = _review_reply(lang, txn, pin)
        return _bundle(summary, action, reply)

    # ------------------------------------------------------------ payment_failed
    if case_type == C.PAYMENT_FAILED:
        if relevant_id is None:
            return _insufficient(
                "a failed payment with a possible balance deduction", lang, pin
            )
        if verdict == C.INCONSISTENT:
            summary = (
                f"Customer reports a failed {_amt_phrase(amt)} payment ({txn}), but the "
                "transaction record shows it as completed. Evidence does not support "
                "the failure claim."
            )
            action = (
                f"Verify {txn} ledger status with the customer; the record currently "
                "indicates a completed payment."
            )
            reply = _review_reply(lang, txn, pin)
            return _bundle(summary, action, reply)
        summary = (
            f"Customer attempted a {_amt_phrase(amt)} payment ({txn}) which failed but "
            "reports the balance was deducted. Requires payments operations review."
        )
        action = (
            f"Investigate {txn} ledger status. If the balance was deducted on a failed "
            "payment, initiate the automatic reversal flow within standard SLA."
        )
        reply = _payments_reply(lang, txn, elig, pin, deduction=True)
        return _bundle(summary, action, reply)

    # ---------------------------------------------------------- duplicate_payment
    if case_type == C.DUPLICATE_PAYMENT:
        if relevant_id is None:
            return _insufficient("a possible duplicate charge", lang, pin)
        if verdict == C.INCONSISTENT:
            summary = (
                f"Customer reports a duplicate charge of {_amt_phrase(amt)} ({txn}), but "
                "only a single matching payment exists in the history."
            )
            action = (
                f"Confirm with the customer; the record currently shows a single charge "
                f"for {txn}."
            )
            reply = _review_reply(lang, txn, pin)
            return _bundle(summary, action, reply)
        dup_of = meta.get("duplicate_of")
        pair = f" ({dup_of} and {txn})" if dup_of else ""
        summary = (
            f"Customer reports a duplicate payment. Two {_amt_phrase(amt)} payments to "
            f"{cp} were completed close together{pair}; {txn} is the likely duplicate."
        )
        action = (
            f"Verify the duplicate with payments_ops. If the biller confirms only a "
            f"single charge was received, initiate reversal of {txn} per policy."
        )
        reply = _payments_reply(lang, txn, elig, pin, deduction=False)
        return _bundle(summary, action, reply)

    # -------------------------------------------------- merchant_settlement_delay
    if case_type == C.MERCHANT_SETTLEMENT_DELAY:
        if relevant_id is None:
            return _insufficient("a delayed settlement", lang, pin)
        summary = (
            f"Merchant reports a {_amt_phrase(amt)} settlement ({txn}) delayed beyond "
            f"the expected window. Settlement status is {status}."
        )
        action = (
            "Route to merchant_operations to verify the settlement batch status and "
            "communicate a revised ETA to the merchant if the batch is delayed."
        )
        # Business tone; no PIN line (matches the merchant sample).
        if lang == "bn":
            reply = (
                f"আপনার সেটেলমেন্ট {txn} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের মার্চেন্ট "
                "অপারেশন্স দল ব্যাচের অবস্থা যাচাই করে প্রত্যাশিত সেটেলমেন্টের সময় "
                "অফিসিয়াল চ্যানেলের মাধ্যমে আপনাকে জানাবে।"
            )
        else:
            reply = (
                f"We have noted your concern about settlement {txn}. Our merchant "
                "operations team will check the batch status and update you on the "
                "expected settlement time through official channels."
            )
        return _bundle(summary, action, reply)

    # ------------------------------------------------------- agent_cash_in_issue
    if case_type == C.AGENT_CASH_IN_ISSUE:
        if relevant_id is None:
            return _insufficient("an agent cash-in not reflected in the balance", lang, pin)
        summary = (
            f"Customer reports a {_amt_phrase(amt)} cash-in via {cp} ({txn}) not "
            f"reflected in their balance. Transaction status is {status}."
        )
        action = (
            f"Investigate the {status} status of {txn} with agent operations. Confirm "
            "the settlement state and resolve within the standard cash-in SLA."
        )
        if lang == "bn":
            reply = (
                f"আপনার লেনদেন {txn} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের এজেন্ট "
                "অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে "
                f"জানাবে। {pin}"
            )
        else:
            reply = (
                f"We have noted your concern about transaction {txn}. Our agent "
                "operations team will verify it promptly and update you through "
                f"official support channels. {pin}"
            )
        return _bundle(summary, action, reply)

    # ------------------------------------------------------------- refund_request
    if case_type == C.REFUND_REQUEST:
        if relevant_id is None:
            return _insufficient("a refund for an unspecified payment", lang, pin)
        summary = (
            f"Customer requests a refund of {_amt_phrase(amt)} for {txn} (merchant "
            "payment). No service failure is indicated."
        )
        action = (
            "Inform the customer that refund eligibility depends on the merchant's own "
            "policy and guide them to contact the merchant through official channels."
        )
        if lang == "bn":
            reply = (
                "যোগাযোগ করার জন্য ধন্যবাদ। সম্পন্ন হওয়া মার্চেন্ট পেমেন্টের রিফান্ড "
                "মার্চেন্টের নিজস্ব নীতির উপর নির্ভর করে। অনুগ্রহ করে অফিসিয়াল চ্যানেলের "
                f"মাধ্যমে মার্চেন্টের সাথে যোগাযোগ করুন। প্রয়োজনে আমরা সাহায্য করব। {pin}"
            )
        else:
            reply = (
                "Thank you for reaching out. Refunds for completed merchant payments "
                "depend on the merchant's own policy. We recommend contacting the "
                "merchant through their official channel. If you need help reaching "
                f"them, please reply and we will guide you. {pin}"
            )
        return _bundle(summary, action, reply)

    # --------------------------------------------------------------------- other
    summary = (
        "Customer reports a vague concern without specifying a transaction, amount, "
        "or issue. Insufficient detail to identify a relevant transaction."
    )
    action = (
        "Reply to the customer asking for specifics: which transaction, what amount, "
        "what went wrong, and the approximate time."
    )
    if lang == "bn":
        reply = (
            "যোগাযোগ করার জন্য ধন্যবাদ। দ্রুত সাহায্য করতে অনুগ্রহ করে লেনদেন আইডি, "
            f"পরিমাণ এবং সমস্যার সংক্ষিপ্ত বিবরণ জানান। {pin}"
        )
    else:
        reply = (
            "Thank you for reaching out. To help you faster, please share the "
            "transaction ID, the amount involved, and a short description of what went "
            f"wrong. {pin}"
        )
    return _bundle(summary, action, reply)


# --------------------------------------------------------------------- helpers


def _bundle(summary: str, action: str, reply: str) -> dict:
    return {
        "agent_summary": summary,
        "recommended_next_action": action,
        "customer_reply": reply,
    }


def _review_reply(lang: str, txn: Optional[str], pin: str) -> str:
    ref = txn or "your case"
    if lang == "bn":
        return (
            f"আপনার লেনদেন {txn} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের ডিসপিউট দল বিষয়টি "
            f"মনোযোগ দিয়ে পর্যালোচনা করে অফিসিয়াল চ্যানেলের মাধ্যমে আপনাকে জানাবে। {pin}"
            if txn
            else f"আপনার বিষয়টি আমাদের ডিসপিউট দল পর্যালোচনা করবে। {pin}"
        )
    if txn:
        return (
            f"We have noted your concern about transaction {txn}. Our dispute team "
            "will review the case carefully and contact you through official support "
            f"channels. {pin}"
        )
    return (
        "We have noted your concern. Our dispute team will review the case carefully "
        f"and contact you through official support channels. {pin}"
    )


def _payments_reply(
    lang: str, txn: Optional[str], elig: str, pin: str, deduction: bool
) -> str:
    if lang == "bn":
        lead = (
            f"আমরা লক্ষ্য করেছি যে লেনদেন {txn} এর কারণে একটি অনাকাঙ্ক্ষিত ব্যালেন্স "
            "কর্তন হয়ে থাকতে পারে।"
            if deduction
            else f"লেনদেন {txn} এর সম্ভাব্য ডুপ্লিকেট পেমেন্টের বিষয়টি আমরা নোট করেছি।"
        )
        return (
            f"{lead} আমাদের পেমেন্টস দল বিষয়টি যাচাই করবে এবং {elig}। {pin}"
        )
    lead = (
        f"We have noted that transaction {txn} may have caused an unexpected balance "
        "deduction."
        if deduction
        else f"We have noted the possible duplicate payment for transaction {txn}."
    )
    return f"{lead} Our payments team will verify the case and {elig}. {pin}"


def _insufficient(topic: str, lang: str, pin: str) -> dict:
    summary = (
        f"Customer reports {topic}, but the provided history does not contain enough "
        "detail to identify a specific transaction."
    )
    action = (
        "Ask the customer for the transaction ID, the exact amount, and the "
        "approximate time so the relevant transaction can be identified."
    )
    if lang == "bn":
        reply = (
            "যোগাযোগ করার জন্য ধন্যবাদ। বিষয়টি শনাক্ত করতে অনুগ্রহ করে লেনদেন আইডি, "
            f"সঠিক পরিমাণ এবং আনুমানিক সময় জানান। {pin}"
        )
    else:
        reply = (
            "Thank you for reaching out. To identify the issue, please share the "
            f"transaction ID, the exact amount, and the approximate time. {pin}"
        )
    return _bundle(summary, action, reply)


def _amt_bn(amt: Optional[str]) -> str:
    return f"{amt} BDT" if amt else "এই পরিমাণের"
