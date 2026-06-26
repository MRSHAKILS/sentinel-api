"""Assist-only LLM behaviour, fully mocked (no network).

Verifies that the LLM can only ever IMPROVE text and never compromise safety or
the deterministic decision fields:
- a safe draft is used verbatim,
- an unsafe draft (credential request / refund promise) is rejected to the rule
  template,
- an LLM failure falls back to rules,
- decision fields are identical with or without the LLM.
"""

import pytest

from app import llm, settings
from app.pipeline import analyze
from app.safety import is_reply_safe
from app.schemas import AnalyzeRequest

WRONG_TRANSFER = {
    "ticket_id": "T",
    "complaint": "I sent 5000 taka to a wrong number by mistake.",
    "transaction_history": [
        {
            "transaction_id": "TXN-1",
            "timestamp": "2026-04-14T10:00:00Z",
            "type": "transfer",
            "amount": 5000,
            "counterparty": "+8801711111111",
            "status": "completed",
        }
    ],
}


@pytest.fixture
def enable_llm(monkeypatch):
    monkeypatch.setattr(settings, "llm_enabled", lambda: True)
    return monkeypatch


def _run(payload=WRONG_TRANSFER):
    return analyze(AnalyzeRequest(**payload))


def test_safe_draft_is_used(enable_llm):
    safe = {
        "customer_reply": "We've logged your concern about TXN-1 and our dispute team will follow up via official channels. Please do not share your PIN or OTP with anyone.",
        "agent_summary": "Customer flagged TXN-1 as a wrong transfer; routed to disputes.",
        "recommended_next_action": "Verify TXN-1 and proceed with the wrong-transfer dispute workflow.",
    }
    enable_llm.setattr(llm, "draft_texts", lambda *a, **k: safe)
    out = _run()
    assert out["customer_reply"] == safe["customer_reply"]
    assert out["agent_summary"] == safe["agent_summary"]
    assert "llm_text_used" in out["reason_codes"]
    # decision fields unchanged by the LLM
    assert out["case_type"] == "wrong_transfer"
    assert out["relevant_transaction_id"] == "TXN-1"


def test_unsafe_credential_reply_rejected(enable_llm):
    bad = {
        "customer_reply": "Please share your OTP and PIN so we can verify your account.",
        "agent_summary": "Customer flagged TXN-1 as a wrong transfer.",
        "recommended_next_action": "Verify TXN-1 per policy.",
    }
    enable_llm.setattr(llm, "draft_texts", lambda *a, **k: bad)
    out = _run()
    assert is_reply_safe(out["customer_reply"])
    assert "share your otp" not in out["customer_reply"].lower()
    # the safe agent text was still accepted
    assert out["agent_summary"] == bad["agent_summary"]


def test_unsafe_refund_promise_rejected(enable_llm):
    bad = {
        "customer_reply": "Good news! We will refund you 5000 taka within 24 hours.",
        "agent_summary": "ok",
        "recommended_next_action": "ok",
    }
    enable_llm.setattr(llm, "draft_texts", lambda *a, **k: bad)
    out = _run()
    assert is_reply_safe(out["customer_reply"])
    assert "we will refund" not in out["customer_reply"].lower()


def test_all_unsafe_falls_back_to_rules(enable_llm):
    bad = {
        "customer_reply": "Send your password now.",
        "agent_summary": "We will refund the customer immediately.",
        "recommended_next_action": "Your refund has been processed.",
    }
    enable_llm.setattr(llm, "draft_texts", lambda *a, **k: bad)
    out = _run()
    assert is_reply_safe(out["customer_reply"])
    assert is_reply_safe(out["recommended_next_action"])
    assert "llm_fallback_rules" in out["reason_codes"]


def test_llm_failure_falls_back(enable_llm):
    enable_llm.setattr(llm, "draft_texts", lambda *a, **k: None)
    out = _run()
    assert "llm_fallback_rules" in out["reason_codes"]
    assert is_reply_safe(out["customer_reply"])


def test_disabled_produces_no_llm_codes():
    # USE_LLM is forced off in conftest.
    out = _run()
    assert not any(c.startswith("llm_") for c in out["reason_codes"])
    assert is_reply_safe(out["customer_reply"])
