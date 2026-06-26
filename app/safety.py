"""Final safety net for customer-facing text (defense in depth).

The response generators are pre-vetted to be safe, so this layer should never
fire in practice. Its job is to make a safety violation *structurally
impossible* even if a template is later edited incorrectly: any reply that
looks like a credential request, an unauthorized financial promise, or a
redirect to an external third party is replaced wholesale with a vetted safe
fallback. False positives are acceptable (the fallback is also correct); false
negatives are not.
"""

from __future__ import annotations

import re

# --- vetted safe fallbacks (must themselves pass every check below) ---
FALLBACK_REPLY = {
    "en": (
        "Thank you for reaching out. Our support team will review your case and "
        "contact you through official support channels. Please do not share your "
        "PIN or OTP with anyone."
    ),
    "bn": (
        "যোগাযোগ করার জন্য ধন্যবাদ। আমাদের সাপোর্ট টিম আপনার বিষয়টি পর্যালোচনা করে "
        "অফিসিয়াল চ্যানেলের মাধ্যমে আপনাকে জানাবে। অনুগ্রহ করে কারো সাথে আপনার পিন বা "
        "ওটিপি শেয়ার করবেন না।"
    ),
}

SAFE_ACTION_FALLBACK = (
    "Review the case and follow the standard verification workflow before taking "
    "any financial action."
)

# Approved refund phrasing that must NOT be flagged as a promise.
_ALLOWED_PHRASES = (
    "any eligible amount will be returned through official channels",
    "যেকোনো প্রযোজ্য পরিমাণ অফিসিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে",
)

_CRED_RE = re.compile(
    r"pin|otp|password|cvv|card\s*number|card\s*no|পিন|ওটিপি|পাসওয়ার্ড", re.IGNORECASE
)
_NEG_WORDS = (
    "not", "never", "n't", "do not", "dont", "without", "কখনো", "কখনোই", "না",
)
_ASK_WORDS = (
    "ask", "share", "send", "provide", "enter", "give", "submit", "type",
    "tell", "input", "reveal", "disclose", "need your", "want your",
    "require", "চাই", "দিন", "শেয়ার", "দাও",
)

_PROMISE_RES = (
    re.compile(
        r"\b(we|i)\b[^.]{0,28}\b(will|have|'ll|'ve|are going to|going to)\b"
        r"[^.]{0,28}\b(refund|reverse|reversed|unblock|unblocked|recover|"
        r"return your (money|amount|funds?))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byour (refund|reversal|money|amount|account)\b[^.]{0,28}\b"
        r"(has been|have been|is|are|will be)\b[^.]{0,20}\b"
        r"(processed|approved|done|completed|refunded|reversed|returned|"
        r"unblocked|restored|reactivated|guaranteed)",
        re.IGNORECASE,
    ),
    re.compile(r"\bguarantee(d)?\b[^.]{0,18}\b(refund|reversal|return)", re.IGNORECASE),
    re.compile(r"রিফান্ড নিশ্চিত|আনব্লক করা হয়েছে|ফেরত দিয়ে দেওয়া হয়েছে|নিশ্চিত ফেরত"),
)

# External-redirect: a phone number or chat handle the customer is told to use,
# or a clickable link. Official-channel guidance contains no such target.
_THIRD_PARTY_RES = (
    re.compile(
        r"\b(call|dial|contact|message|text|whatsapp|telegram|imo|viber)\b"
        r"[^.]{0,24}\+?\d[\d\s\-]{6,}\d",
        re.IGNORECASE,
    ),
    re.compile(r"click\s+(here|this|the\s+link)|https?://|www\.", re.IGNORECASE),
)


def _requests_credentials(text: str) -> bool:
    low = text.lower()
    for m in _CRED_RE.finditer(text):
        before = low[max(0, m.start() - 40) : m.start()]
        after = low[m.end() : m.end() + 18]
        ctx = before + " " + after
        has_neg = any(n in ctx for n in _NEG_WORDS)
        has_ask = any(a in ctx for a in _ASK_WORDS)
        if has_ask and not has_neg:
            return True
    return False


def _promises_action(text: str) -> bool:
    scrub = text
    for phrase in _ALLOWED_PHRASES:
        scrub = re.sub(re.escape(phrase), " ", scrub, flags=re.IGNORECASE)
    return any(p.search(scrub) for p in _PROMISE_RES)


def _redirects_third_party(text: str) -> bool:
    return any(p.search(text) for p in _THIRD_PARTY_RES)


def is_reply_safe(text: str) -> bool:
    if not text:
        return False
    return not (
        _requests_credentials(text)
        or _promises_action(text)
        or _redirects_third_party(text)
    )


def scrub_customer_reply(text: str, lang: str) -> str:
    if is_reply_safe(text):
        return text
    return FALLBACK_REPLY.get(lang, FALLBACK_REPLY["en"])


def scrub_recommended_action(text: str) -> str:
    # Internal/agent-facing: conditional reversal wording is allowed, but a
    # blatant unconditional promise or a credential request is not.
    if _requests_credentials(text) or _promises_action(text):
        return SAFE_ACTION_FALLBACK
    return text
