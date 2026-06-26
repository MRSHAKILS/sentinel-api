"""Central constants: enums, keyword lexicons, thresholds, and lookup tables.

Everything tunable lives here so the reasoning modules stay declarative.
Keep all enum string literals defined ONCE here and import them — never hand-type
an enum value elsewhere (prevents schema-violation typos).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Output enums (must match the problem statement EXACTLY).
# ---------------------------------------------------------------------------

# evidence_verdict
CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"
INSUFFICIENT = "insufficient_data"
EVIDENCE_VERDICTS = (CONSISTENT, INCONSISTENT, INSUFFICIENT)

# case_type
WRONG_TRANSFER = "wrong_transfer"
PAYMENT_FAILED = "payment_failed"
REFUND_REQUEST = "refund_request"
DUPLICATE_PAYMENT = "duplicate_payment"
MERCHANT_SETTLEMENT_DELAY = "merchant_settlement_delay"
AGENT_CASH_IN_ISSUE = "agent_cash_in_issue"
PHISHING = "phishing_or_social_engineering"
OTHER = "other"
CASE_TYPES = (
    WRONG_TRANSFER,
    PAYMENT_FAILED,
    REFUND_REQUEST,
    DUPLICATE_PAYMENT,
    MERCHANT_SETTLEMENT_DELAY,
    AGENT_CASH_IN_ISSUE,
    PHISHING,
    OTHER,
)

# severity
LOW = "low"
MEDIUM = "medium"
HIGH = "high"
CRITICAL = "critical"
SEVERITIES = (LOW, MEDIUM, HIGH, CRITICAL)
SEVERITY_RANK = {LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3}

# department
CUSTOMER_SUPPORT = "customer_support"
DISPUTE_RESOLUTION = "dispute_resolution"
PAYMENTS_OPS = "payments_ops"
MERCHANT_OPERATIONS = "merchant_operations"
AGENT_OPERATIONS = "agent_operations"
FRAUD_RISK = "fraud_risk"
DEPARTMENTS = (
    CUSTOMER_SUPPORT,
    DISPUTE_RESOLUTION,
    PAYMENTS_OPS,
    MERCHANT_OPERATIONS,
    AGENT_OPERATIONS,
    FRAUD_RISK,
)

# transaction enums (input side — used for matching, parsed leniently)
TXN_TRANSFER = "transfer"
TXN_PAYMENT = "payment"
TXN_CASH_IN = "cash_in"
TXN_CASH_OUT = "cash_out"
TXN_SETTLEMENT = "settlement"
TXN_REFUND = "refund"
TXN_TYPES = (
    TXN_TRANSFER,
    TXN_PAYMENT,
    TXN_CASH_IN,
    TXN_CASH_OUT,
    TXN_SETTLEMENT,
    TXN_REFUND,
)

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
STATUS_REVERSED = "reversed"
TXN_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING, STATUS_REVERSED)

# language / channel / user_type (optional inputs)
LANG_EN = "en"
LANG_BN = "bn"
LANG_MIXED = "mixed"
LANGUAGES = (LANG_EN, LANG_BN, LANG_MIXED)

USER_CUSTOMER = "customer"
USER_MERCHANT = "merchant"
USER_AGENT = "agent"
USER_UNKNOWN = "unknown"
USER_TYPES = (USER_CUSTOMER, USER_MERCHANT, USER_AGENT, USER_UNKNOWN)

CHANNELS = ("in_app_chat", "call_center", "email", "merchant_portal", "field_agent")

# ---------------------------------------------------------------------------
# Thresholds / tunables.
# ---------------------------------------------------------------------------

# Amount (matched) at/above this on a money-movement case forces high severity
# + human review. 15,000 stays medium per SAMPLE-09, so this sits above it.
HIGH_VALUE_BDT = 50_000.0

# Two same-amount + same-counterparty payments within this window => duplicate.
DUPLICATE_WINDOW_SECONDS = 600  # 10 minutes

# Float tolerance when comparing complaint amount to transaction amount.
AMOUNT_TOLERANCE = 0.01

# >= this many transfers in history to the matched counterparty contradicts a
# "wrong / unknown recipient" claim (established-recipient pattern).
ESTABLISHED_RECIPIENT_MIN = 2

# Defensive caps (DoS / resource guards).
MAX_COMPLAINT_SCAN_CHARS = 8_000   # only scan this much complaint text
MAX_HISTORY_ENTRIES = 200          # process at most this many transactions

# Money-movement case types (used for high-value escalation logic).
MONEY_MOVEMENT_CASES = frozenset(
    {
        WRONG_TRANSFER,
        PAYMENT_FAILED,
        DUPLICATE_PAYMENT,
        AGENT_CASH_IN_ISSUE,
        MERCHANT_SETTLEMENT_DELAY,
    }
)

# ---------------------------------------------------------------------------
# Lookup tables (declarative routing).
# ---------------------------------------------------------------------------

# Which transaction types are plausible candidates for each case_type.
# Empty tuple => no transaction can be "relevant" (e.g. phishing).
# None => all types are candidates (e.g. other).
TYPE_AFFINITY: dict[str, tuple[str, ...] | None] = {
    WRONG_TRANSFER: (TXN_TRANSFER,),
    PAYMENT_FAILED: (TXN_PAYMENT,),
    REFUND_REQUEST: (TXN_PAYMENT, TXN_REFUND),
    DUPLICATE_PAYMENT: (TXN_PAYMENT,),
    MERCHANT_SETTLEMENT_DELAY: (TXN_SETTLEMENT,),
    AGENT_CASH_IN_ISSUE: (TXN_CASH_IN,),
    PHISHING: (),
    OTHER: None,
}

# Expected transaction status per case_type (scores a small match bonus).
EXPECTED_STATUS: dict[str, str | None] = {
    PAYMENT_FAILED: STATUS_FAILED,
    MERCHANT_SETTLEMENT_DELAY: STATUS_PENDING,
    AGENT_CASH_IN_ISSUE: STATUS_PENDING,
}

# Base severity per case_type (before adjustments).
BASE_SEVERITY: dict[str, str] = {
    PHISHING: CRITICAL,
    WRONG_TRANSFER: HIGH,
    PAYMENT_FAILED: HIGH,
    DUPLICATE_PAYMENT: HIGH,
    AGENT_CASH_IN_ISSUE: HIGH,
    MERCHANT_SETTLEMENT_DELAY: MEDIUM,
    REFUND_REQUEST: LOW,
    OTHER: LOW,
}

# Base department per case_type (before refinements).
BASE_DEPARTMENT: dict[str, str] = {
    PHISHING: FRAUD_RISK,
    WRONG_TRANSFER: DISPUTE_RESOLUTION,
    PAYMENT_FAILED: PAYMENTS_OPS,
    DUPLICATE_PAYMENT: PAYMENTS_OPS,
    MERCHANT_SETTLEMENT_DELAY: MERCHANT_OPERATIONS,
    AGENT_CASH_IN_ISSUE: AGENT_OPERATIONS,
    REFUND_REQUEST: CUSTOMER_SUPPORT,
    OTHER: CUSTOMER_SUPPORT,
}

# ---------------------------------------------------------------------------
# Keyword lexicons (lowercased). English + Bangla + common Banglish.
# Bias the phishing set toward recall — it is the most safety-critical.
# ---------------------------------------------------------------------------

# Phishing detection is split to avoid false positives on legitimate complaints
# that merely mention a credential (e.g. "I entered my OTP but it failed").
# A ticket is phishing if it contains a STRONG scam phrase, OR a credential term
# appearing together with a solicitation/external-contact context.

PHISHING_STRONG = (
    "claiming to be", "claim to be", "pretending to be", "said they are from",
    "saying they are from", "they are from bkash", "customer care called",
    "suspicious call", "scam", "scammer", "fraud call", "phishing",
    "social engineering", "fake call", "fake sms", "fraudster",
    "account will be blocked", "account will be suspended", "will be blocked if",
    "blocked if i don't", "blocked if i do not", "verify your account",
    "confirm your account", "update your kyc", "click the link",
    "click this link", "click on the link", "won a prize", "you won a",
    "lottery", "reward money", "prize money", "winning amount",
    # Bangla
    "প্রতারণা", "প্রতারক", "ফাঁদ", "লটারি", "পুরস্কার", "একাউন্ট ব্লক",
    "অ্যাকাউন্ট ব্লক", "ব্লক হয়ে যাবে", "লিংকে ক্লিক", "লিঙ্কে ক্লিক",
    "বিকাশ থেকে কল", "নগদ থেকে কল", "ভুয়া কল", "প্রতারণার",
)

# Bangla credential terms (checked as substrings) + solicitation context.
CREDENTIAL_TERMS_BN = ("পিন", "ওটিপি", "পাসওয়ার্ড", "সিভিভি", "গোপন কোড")
SOLICIT_CONTEXT_BN = (
    "চাইছে", "চেয়েছে", "চাইল", "চায়", "শেয়ার", "দিতে বলেছে", "পাঠাতে বলেছে",
    "বলছে", "কল দিয়ে", "ফোন দিয়ে", "নাম্বার থেকে", "থেকে কল", "লিংক", "লিঙ্ক",
)

DUPLICATE_KEYWORDS = (
    "twice", "two times", "double", "doubly", "duplicate", "duplicated",
    "charged twice", "deducted twice", "debited twice", "paid twice",
    "two payments", "second time", "only paid once", "paid only once",
    "i paid once", "billed twice",
    "দুইবার", "দুবার", "দুই বার", "ডাবল", "দুটি লেনদেন", "একবারই",
    "একবার দিয়েছি",
)

PAYMENT_FAILED_KEYWORDS = (
    "failed", "fail", "declined", "unsuccessful", "not successful",
    "transaction failed", "payment failed", "recharge failed", "showed failed",
    "but deducted", "but my balance", "balance deducted", "balance was deducted",
    "money deducted", "amount deducted", "deducted but", "cut but",
    "ব্যর্থ", "ফেইল", "ফেল", "ব্যালেন্স কেটে", "টাকা কেটে", "কেটে নিয়েছে",
    "কেটে নিল", "কাটা হয়েছে", "ব্যালেন্স কমে",
)

AGENT_CASH_IN_KEYWORDS = (
    "cash in", "cash-in", "cashin", "cash in through agent", "agent",
    "deposited through", "deposit through agent", "gave the agent",
    "gave money to agent", "agent took", "agent said",
    "এজেন্ট", "ক্যাশ ইন", "ক্যাশইন", "ক্যাশ-ইন", "এজেন্টের কাছে",
    "জমা দিয়েছি", "টাকা জমা",
)

SETTLEMENT_KEYWORDS = (
    "settlement", "settle", "settled", "not settled", "unsettled", "payout",
    "pay out", "my sales", "yesterday's sales", "merchant settlement",
    "settlement delay", "deposit to my account", "sales amount",
    "সেটেলমেন্ট", "সেটেল", "পেআউট", "বিক্রির টাকা", "বিক্রয়ের টাকা",
)

WRONG_TRANSFER_KEYWORDS = (
    "wrong number", "wrong person", "wrong recipient", "wrong account",
    "wrong receiver", "to a wrong", "to the wrong", "sent to wrong",
    "by mistake", "mistakenly", "wrongly sent", "typed it wrong", "typed wrong",
    "didn't get it", "did not get it", "didn't receive", "did not receive",
    "hasn't received", "has not received", "not received it", "never got",
    "wrong transfer",
    "ভুল নম্বর", "ভুল মানুষ", "ভুল জায়গায়", "ভুলবশত", "ভুল করে", "ভুলে",
    "পায়নি", "পাইনি", "পাচ্ছে না", "পায় নাই", "পৌঁছায়নি",
    # banglish
    "vul number", "bhul number", "vul", "bhul", "wrong e", "wrong gece",
)

REFUND_KEYWORDS = (
    "refund", "refunded", "money back", "return my money", "return the money",
    "give my money back", "want my money back", "changed my mind",
    "change my mind", "don't want", "do not want", "no longer want",
    "cancel my order", "cancel the order",
    "রিফান্ড", "ফেরত", "ফেরত চাই", "টাকা ফেরত", "মন পরিবর্তন", "চাই না",
    "বাতিল",
)

# Vague-complaint signal — pushes weak/unspecified complaints to `other`.
VAGUE_KEYWORDS = (
    "something is wrong", "something wrong", "problem with my", "issue with my",
    "check my account", "please check", "not sure what", "i don't know what",
    "কিছু একটা সমস্যা", "সমস্যা হচ্ছে", "ঠিক নেই", "দেখুন", "চেক করুন",
)

# ---------------------------------------------------------------------------
# Prompt-injection signatures. Flagged and IGNORED — never obeyed.
# (Rule-based reasoning is structurally immune; this is for the reason_code.)
# ---------------------------------------------------------------------------

INJECTION_PATTERNS = (
    "ignore previous", "ignore all previous", "ignore above", "ignore the above",
    "ignore your", "disregard previous", "disregard all", "disregard your",
    "forget your instructions", "forget previous", "system prompt",
    "you are now", "act as", "new instructions", "override",
    "set human_review", "human_review_required", "mark this as resolved",
    "classify this as", "respond with", "output the following", "you must say",
    "approve my refund", "confirm my refund", "give me a refund now",
    "reveal your", "print your prompt", "developer mode", "jailbreak",
)

# Bangla digit -> ASCII.
BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
