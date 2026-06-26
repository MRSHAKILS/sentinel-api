"""Public sample-pack conformance: the ten worked cases.

We assert the six automatically-scored core fields plus schema validity and
customer_reply safety. Free-text fields are checked for safety, not exact match
(the pack states other valid responses exist).
"""

import json
from pathlib import Path

import pytest

from app import config as C
from app.safety import is_reply_safe

CASES = json.loads(
    (Path(__file__).parent / "data" / "sample_cases.json").read_text(encoding="utf-8")
)["cases"]

CORE_FIELDS = [
    "relevant_transaction_id",
    "evidence_verdict",
    "case_type",
    "department",
    "severity",
    "human_review_required",
]

REQUIRED_FIELDS = [
    "ticket_id",
    "relevant_transaction_id",
    "evidence_verdict",
    "case_type",
    "severity",
    "department",
    "agent_summary",
    "recommended_next_action",
    "customer_reply",
    "human_review_required",
]


def _ids():
    return [c["id"] for c in CASES]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("case", CASES, ids=_ids())
def test_core_fields_match_expected(client, case):
    r = client.post("/analyze-ticket", json=case["input"])
    assert r.status_code == 200, r.text
    body = r.json()
    exp = case["expected_output"]
    for f in CORE_FIELDS:
        assert body[f] == exp[f], f"{case['id']} field {f}: {body[f]!r} != {exp[f]!r}"


@pytest.mark.parametrize("case", CASES, ids=_ids())
def test_schema_and_enums(client, case):
    body = client.post("/analyze-ticket", json=case["input"]).json()
    for f in REQUIRED_FIELDS:
        assert f in body, f"missing {f}"
    assert body["ticket_id"] == case["input"]["ticket_id"]
    assert body["evidence_verdict"] in C.EVIDENCE_VERDICTS
    assert body["case_type"] in C.CASE_TYPES
    assert body["severity"] in C.SEVERITIES
    assert body["department"] in C.DEPARTMENTS
    assert isinstance(body["human_review_required"], bool)
    rid = body["relevant_transaction_id"]
    assert rid is None or isinstance(rid, str)


@pytest.mark.parametrize("case", CASES, ids=_ids())
def test_customer_reply_is_safe(client, case):
    body = client.post("/analyze-ticket", json=case["input"]).json()
    reply = body["customer_reply"]
    assert is_reply_safe(reply)
    low = reply.lower()
    # Never an imperative request for credentials.
    for bad in ("send your otp", "share your otp", "enter your pin", "provide your pin"):
        assert bad not in low
    # Never an unconditional refund promise.
    for bad in ("we will refund", "we have refunded", "refund has been processed"):
        assert bad not in low
