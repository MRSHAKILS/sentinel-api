"""Pydantic models + a lenient transaction parser.

Design choices:
- Required fields (`ticket_id`, `complaint`) are coerced from scalars but a
  missing/null value raises -> mapped to HTTP 400 by main.py.
- Optional scalar fields never raise: bad values are coerced or dropped to None.
- `transaction_history` / `metadata` accept arbitrary JSON; we normalise the
  history ourselves so one malformed row can never 400 the whole request.
- The OUTPUT model uses Literal enums so the service cannot emit an
  out-of-spec value (a test asserts these match config exactly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from . import config as C

# ---------------------------------------------------------------------------
# Request model (lenient).
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    complaint: str
    language: Optional[str] = None
    channel: Optional[str] = None
    user_type: Optional[str] = None
    campaign_context: Optional[str] = None
    transaction_history: Optional[Any] = None
    metadata: Optional[Any] = None

    @field_validator("ticket_id", "complaint", mode="before")
    @classmethod
    def _coerce_required_str(cls, v: Any) -> str:
        # None / missing -> validation error -> HTTP 400.
        if v is None:
            raise ValueError("field required")
        if isinstance(v, str):
            return v
        if isinstance(v, bool):
            # Avoid "True"/"False" sneaking in as a complaint.
            raise ValueError("must be a string")
        if isinstance(v, (int, float)):
            return str(v)
        raise ValueError("must be a string")

    @field_validator(
        "language", "channel", "user_type", "campaign_context", mode="before"
    )
    @classmethod
    def _coerce_optional_str(cls, v: Any) -> Optional[str]:
        # Optional scalars must never break the request: coerce or drop.
        if v is None:
            return None
        if isinstance(v, str):
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
        return None


# ---------------------------------------------------------------------------
# Normalised internal transaction record.
# ---------------------------------------------------------------------------


@dataclass
class Txn:
    transaction_id: Optional[str]
    timestamp: Optional[str]
    type: Optional[str]
    amount: Optional[float]
    counterparty: Optional[str]
    status: Optional[str]
    epoch: Optional[float] = None  # parsed UNIX seconds, or None if unparseable
    raw: dict = field(default_factory=dict)


def _coerce_amount(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except (ValueError, OverflowError):
            return None
    if isinstance(v, str):
        s = v.strip().translate(C.BANGLA_DIGITS)
        # keep digits, dot, minus; drop currency words/commas/symbols
        cleaned = []
        for ch in s:
            if ch.isdigit() or ch in ".-":
                cleaned.append(ch)
        token = "".join(cleaned)
        if token in ("", "-", ".", "-.", ".-"):
            return None
        try:
            return float(token)
        except ValueError:
            return None
    return None


def _coerce_str(v: Any) -> Optional[str]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    if isinstance(v, (int, float)):
        return str(v)
    return None


def _parse_epoch(ts: Optional[str]) -> Optional[float]:
    if not ts or not isinstance(ts, str):
        return None
    raw = ts.strip()
    if not raw:
        return None
    candidate = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        # last resort: date only
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def parse_transactions(raw: Any) -> list[Txn]:
    """Normalise arbitrary history JSON into Txn records, skipping junk rows."""
    if not isinstance(raw, list):
        return []
    out: list[Txn] = []
    for item in raw[: C.MAX_HISTORY_ENTRIES]:
        if not isinstance(item, dict):
            continue  # skip non-object rows rather than failing
        ttype = _coerce_str(item.get("type"))
        status = _coerce_str(item.get("status"))
        ts = _coerce_str(item.get("timestamp"))
        txn = Txn(
            transaction_id=_coerce_str(item.get("transaction_id")),
            timestamp=ts,
            type=ttype.lower() if ttype else None,
            amount=_coerce_amount(item.get("amount")),
            counterparty=_coerce_str(item.get("counterparty")),
            status=status.lower() if status else None,
            epoch=_parse_epoch(ts),
            raw=item,
        )
        out.append(txn)
    return out


# ---------------------------------------------------------------------------
# Response model (strict — cannot emit out-of-spec enums).
# ---------------------------------------------------------------------------


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    relevant_transaction_id: Optional[str]
    evidence_verdict: Literal["consistent", "inconsistent", "insufficient_data"]
    case_type: Literal[
        "wrong_transfer",
        "payment_failed",
        "refund_request",
        "duplicate_payment",
        "merchant_settlement_delay",
        "agent_cash_in_issue",
        "phishing_or_social_engineering",
        "other",
    ]
    severity: Literal["low", "medium", "high", "critical"]
    department: Literal[
        "customer_support",
        "dispute_resolution",
        "payments_ops",
        "merchant_operations",
        "agent_operations",
        "fraud_risk",
    ]
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: Optional[float] = None
    reason_codes: Optional[list[str]] = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
