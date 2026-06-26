"""Feature extraction from the free-text complaint.

Produces a `Features` bundle the downstream rule modules consume. All regexes
are simple/linear (no nested quantifiers) to avoid ReDoS. Bangla digits are
normalised; phone numbers, identifiers, and time tokens are stripped before
amount detection so we don't mistake an ID or a clock time for a money amount.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config as C

# Bengali block U+0980–U+09FF.
_BANGLA_RE = re.compile(r"[ঀ-৿]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Phone / long-number runs (>= ~10 digits). Allows +, spaces, hyphens between.
_PHONE_RE = re.compile(r"\+?\d[\d\s\-]{7,}\d")

# Alphanumeric identifiers like TXN-9101, AGENT-512, MERCHANT-7821, TKT-001.
_IDENTIFIER_RE = re.compile(r"[A-Za-z]{2,}[-_]?\d{2,}")

# Clock times: "2pm", "2 pm", "11am", and Bangla "২টা" (o'clock). The negative
# lookahead stops the Bangla "টা" from matching inside "টাকা" (the word for money)
# and eating a digit of the amount.
_TIME_RE = re.compile(r"\d{1,2}\s?(?:am|pm)\b|\d{1,2}\s?টা(?![ঀ-৿])")

# Money amounts: comma-grouped (5,000) or plain (5000 / 5000.50).
_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

# Merchant/agent/biller tokens a complaint might cite.
_TOKEN_RE = re.compile(r"(?:merchant|agent|biller)[-\s]?[A-Za-z0-9]+", re.IGNORECASE)


@dataclass
class Features:
    raw: str
    lower: str               # lowercased original (keeps Bangla) — keyword scans
    reply_lang: str          # "bn" or "en" — language to answer the customer in
    has_bangla: bool
    amounts: set[float] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)   # normalised last-10 digits
    tokens: set[str] = field(default_factory=set)   # upper-cased id cores
    injection: bool = False

    def has(self, keywords) -> bool:
        return any(k in self.lower for k in keywords)


def _detect_reply_lang(raw: str, provided: str | None) -> tuple[str, bool]:
    bn = len(_BANGLA_RE.findall(raw))
    latin = len(_LATIN_RE.findall(raw))
    has_bangla = bn > 0
    if bn > 0 and bn >= latin:
        return "bn", has_bangla
    if (provided or "").lower() == C.LANG_BN:
        return "bn", has_bangla
    return "en", has_bangla


def _normalise_phone(run: str) -> str | None:
    digits = re.sub(r"\D", "", run)
    if len(digits) >= 10:
        return digits[-10:]
    return None


def extract(complaint: str, provided_language: str | None = None) -> Features:
    raw = (complaint or "")[: C.MAX_COMPLAINT_SCAN_CHARS]
    lower = raw.lower()
    reply_lang, has_bangla = _detect_reply_lang(raw, provided_language)

    feats = Features(
        raw=raw,
        lower=lower,
        reply_lang=reply_lang,
        has_bangla=has_bangla,
        injection=any(p in lower for p in C.INJECTION_PATTERNS),
    )

    # --- phones (also collected so we can strip them before amount scan) ---
    phone_spans: list[str] = []
    for m in _PHONE_RE.finditer(raw):
        run = m.group(0)
        norm = _normalise_phone(run)
        if norm:
            feats.phones.add(norm)
            phone_spans.append(run)

    # --- merchant/agent/biller tokens ---
    for m in _TOKEN_RE.finditer(raw):
        feats.tokens.add(re.sub(r"\s+", "-", m.group(0).upper()))

    # --- amounts: work on a cleaned copy ---
    amount_text = raw.translate(C.BANGLA_DIGITS)
    for run in phone_spans:
        amount_text = amount_text.replace(run, " ")
    amount_text = _IDENTIFIER_RE.sub(" ", amount_text)
    amount_text = _TIME_RE.sub(" ", amount_text)

    for m in _AMOUNT_RE.finditer(amount_text):
        token = m.group(0).replace(",", "")
        # Skip overly long digit strings (account/ID-like, not money).
        if len(token.split(".")[0]) >= 10:
            continue
        try:
            val = float(token)
        except ValueError:
            continue
        if val > 0:
            feats.amounts.add(val)

    return feats
