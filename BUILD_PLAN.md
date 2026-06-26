# QueueStorm Investigator — Foolproof Build Plan (4.5h Preliminary)

> Goal: a **safe, reliable, evidence-grounded** FastAPI service exposing `GET /health` and
> `POST /analyze-ticket`. Pure **rule-based** core (no LLM dependency) for determinism, speed,
> zero secret/leak risk, and full latency credit. This document is the spec to build against.

---

## 0. Strategy (read this first)

The rubric is weighted:

| Category | Weight | What wins it |
|---|---|---|
| Evidence Reasoning | **35** | Right `relevant_transaction_id`, `evidence_verdict`, `case_type`, `department`, `severity`, `human_review_required` |
| Safety & Escalation | **20** | Never ask credentials, never promise refunds, escalate risky/ambiguous/high-value |
| API Contract & Schema | **15** | Exact field names/types/enums, correct HTTP codes |
| Performance & Reliability | **10** | < 30s hard, p95 ≤ 5s for full credit, never crash on bad input |
| Response Quality | 10 | Clear summary/next-action, safe professional reply (manual) |
| Deployment & Reproducibility | 5 | Judges run it with zero help |
| Documentation | 5 | README explains setup, AI usage, safety, limits |

**Decision: pure rule-based, no external LLM.** The brief literally says "an LLM is not required to score well" and "a simple, reliable, safe API will score higher than a complex but unreliable one." Pure rules give us:
- p95 in **milliseconds** → full latency credit, never times out.
- **No API key** → nothing to leak, no quota/rate-limit failure during judging, trivial deployment.
- **Deterministic safety** → impossible to accidentally emit a credential request or refund promise.
- **Prompt injection is a non-issue** by construction — rules can't be "instructed."

LLM is an *optional stretch* only (Section 13), gated behind an env flag, default OFF, with hard timeout + rule fallback. Do **not** build it until everything else is green.

**Critical safety facts (auto-checked, subtract from total, can disqualify):**
- Ask for PIN/OTP/password/full card number → **−15**
- Confirm refund/reversal/unblock/recovery without authority → **−10** (checked in BOTH `customer_reply` AND `recommended_next_action`)
- Direct customer to a suspicious third party → **−10**
- **2+ critical safety violations across hidden cases → not eligible for top-40.**

Tie-breakers (in order): safety → evidence reasoning → schema validity → reliability → engineering quality → Bangla/Banglish quality → docs → video.

---

## 1. The contract (exact)

### Endpoints
- `GET /health` → `200 {"status":"ok"}` (must respond within 60s of start; ours is instant).
- `POST /analyze-ticket` → `200` with the response schema; must respond within **30s**.

### Request (JSON)
Required: `ticket_id` (str), `complaint` (str).
Optional: `language` (`en|bn|mixed`), `channel` (`in_app_chat|call_center|email|merchant_portal|field_agent`), `user_type` (`customer|merchant|agent|unknown`), `campaign_context` (str), `transaction_history` (array, may be empty/absent), `metadata` (object).

Transaction entry: `transaction_id`, `timestamp` (ISO8601), `type` (`transfer|payment|cash_in|cash_out|settlement|refund`), `amount` (number, BDT), `counterparty` (str), `status` (`completed|failed|pending|reversed`).

### Response (JSON) — required fields
`ticket_id`, `relevant_transaction_id` (str|null), `evidence_verdict` (`consistent|inconsistent|insufficient_data`), `case_type`, `severity` (`low|medium|high|critical`), `department`, `agent_summary` (str), `recommended_next_action` (str), `customer_reply` (str), `human_review_required` (bool).
Optional (include them — cheap value): `confidence` (0..1 float), `reason_codes` (string array).

`case_type` enum: `wrong_transfer, payment_failed, refund_request, duplicate_payment, merchant_settlement_delay, agent_cash_in_issue, phishing_or_social_engineering, other`.
`department` enum: `customer_support, dispute_resolution, payments_ops, merchant_operations, agent_operations, fraud_risk`.

> **Enums must match exactly.** Any case/plural/spelling variant = schema violation. Centralize them as constants; never hand-type a literal twice.

### HTTP status policy
- `200` — successful analysis.
- `400` — invalid JSON, or **missing/wrong-typed required field** (`ticket_id`/`complaint`). Map Pydantic validation errors → 400.
- `422` — schema valid but **semantically invalid**: `complaint` is empty/whitespace-only.
- `500` — internal error; body is a **generic, non-sensitive** message. Never leak stack traces, secrets, or raw input.
- The process must **never crash** on malformed input.

---

## 2. Architecture & repo layout

```
queuestorm-investigator/
  app/
    __init__.py
    main.py          # FastAPI app, routes, exception handlers, size limits
    schemas.py       # Pydantic models (request/response) + enums
    config.py        # constants, thresholds, keyword sets, templates registry keys
    pipeline.py      # analyze(ticket) -> response  (orchestrator)
    extract.py       # language detect, amount/phone/time/keyword extraction, normalization
    classify.py      # case_type cascade + prompt-injection detection
    matching.py      # transaction matching + evidence verdict
    routing.py       # department + severity + human_review_required
    responses.py     # agent_summary, recommended_next_action, customer_reply (en/bn)
    safety.py        # final safety scrubber + safe fallbacks
  tests/
    test_samples.py  # the 10 public cases
    test_edges.py    # corner cases (Section 8)
  sample_outputs.json
  Dockerfile
  .dockerignore
  .gitignore
  requirements.txt   # fastapi, uvicorn[standard], pydantic  (that's it)
  .env.example       # PORT=8000 (+ optional flags)
  README.md
  RUNBOOK.md
```

**Pipeline order (one function, pure, deterministic):**
```
analyze(req):
  1. normalize input (lenient: coerce/skip bad optional fields, never throw)
  2. lang = detect_language(complaint, req.language)
  3. features = extract(complaint)          # amounts, phones, time hints, keyword hits, injection flag
  4. case_type = classify(features, req)    # priority cascade; phishing first
  5. relevant_id, verdict, match_meta = match(case_type, features, history)
  6. severity = severity_of(case_type, verdict, relevant_id, amount, features)
  7. department = route(case_type, req.user_type, severity)
  8. human_review = needs_review(case_type, verdict, relevant_id, severity, amount)
  9. texts = render(case_type, verdict, relevant_id, lang, req, match_meta)  # summary/action/reply
  10. texts.customer_reply = safety_scrub(texts.customer_reply, lang)        # defense in depth
  11. return Response(... + confidence + reason_codes)
```

---

## 3. Feature extraction (`extract.py`)

**Language detection** (`effective_language`):
- If any Bengali Unicode char (`ঀ`–`৿`) present and dominant → `bn`.
- Else if `req.language` ∈ {en,bn,mixed} → use it.
- Else → `en`. (We reply in `bn` only when text is actually Bengali; otherwise `en`. `mixed`→reply `en` for clarity/safety.)
- Trust *script over label* so a mislabeled `language:"en"` with Bangla text still gets a Bangla reply.

**Amount extraction** (returns a set of numbers):
- Convert Bangla digits `০১২৩৪৫৬৭৮৯` → ASCII before parsing.
- Regex for numbers with optional `,` thousands and optional decimals: e.g. `5000`, `5,000`, `5000.00`, `৳5000`, `BDT 5000`, `১২০০`.
- Strip currency words/symbols (`taka`, `tk`, `৳`, `BDT`).
- Keep all amounts found (a complaint may mention several).

**Phone/counterparty extraction:**
- Match BD mobile patterns: `+8801XXXXXXXXX`, `8801XXXXXXXXX`, `01XXXXXXXXX`.
- Normalize for comparison: strip `+`, leading `880`, leading `0` → compare last 10 digits. (Lets `01712345678` match `+8801712345678`.)
- Also capture merchant/agent tokens if present (`MERCHANT-xxxx`, `AGENT-xxx`, `BILLER-xxx`).

**Time hints (low weight, best-effort only):** today/আজ, yesterday/গতকাল, morning/সকাল, afternoon/দুপুর, evening/সন্ধ্যা, "2pm/2 PM/দুপুর ২টা". Do **not** anchor to a real "now" — timestamps are synthetic. Use only as a tiebreaker.

**Keyword hit sets** (English + Bangla + common Banglish/transliteration). Maintain in `config.py`. Examples (extend generously):
- wrong_transfer: `wrong number, wrong person, wrong recipient, by mistake, mistakenly, didn't get/receive, did not receive, ভুল নম্বর, ভুল মানুষ, ভুলে, পায়নি, vul number, bhul`
- payment_failed: `failed, payment failed, transaction failed, declined, but deducted, balance deducted, ব্যর্থ, ফেইল, কেটে নিয়েছে, টাকা কাটা`
- duplicate_payment: `twice, two times, double, duplicate, charged twice, deducted twice, only paid once, দুইবার, দুবার, ডাবল`
- merchant_settlement_delay: `settlement, settle, not settled, payout, my sales, সেটেলমেন্ট, সেটেল`
- agent_cash_in_issue: `cash in, cash-in, agent, deposited, এজেন্ট, ক্যাশ ইন, ক্যাশইন, jma`
- refund_request: `refund, money back, return my money, changed my mind, don't want, রিফান্ড, ফেরত, ফেরত চাই`
- phishing/social-engineering: `OTP, PIN, password, verification code, code, link, click, lottery, you won, prize, account will be blocked, suspend, verify your account, kyc, customer care called, bkash/nagad theke call, পিন, ওটিপি, পাসওয়ার্ড, লিংক, লটারি, পুরস্কার, ব্লক, কোড দিন`

> The phishing set is the most safety-critical; bias toward recall.

**Prompt-injection detection (flag, don't obey):** patterns like `ignore previous/above instructions, ignore all rules, system prompt, you are now, disregard, set human_review_required, classify this as, output json, refund me, you must`. If found, set `reason_code = "prompt_injection_ignored"`. It **never** changes routing/safety; classification proceeds on the literal complaint content only.

---

## 4. Case-type classification (`classify.py`) — priority cascade

Evaluate **in this order**, first match wins (order matters because complaints overlap):

1. **phishing_or_social_engineering** — if phishing keywords present (someone asking for OTP/PIN/password, suspicious call/SMS, "account will be blocked", links, lottery/prize, caller claiming to be staff). *Safety-first: check before everything.* (SAMPLE-05)
2. **duplicate_payment** — duplicate keywords (`twice/double/duplicate/only paid once`) OR two+ payments with same amount+counterparty close in time. (SAMPLE-10)
3. **payment_failed** — failure keywords (`failed/declined`) AND/OR a matching `payment` with `status=failed`, especially with "balance deducted". (SAMPLE-03)
4. **agent_cash_in_issue** — cash-in/agent keywords, or a `cash_in` transaction in play with "not reflected/not received". (SAMPLE-07)
5. **merchant_settlement_delay** — settlement keywords OR `user_type=merchant` complaining about payout/settlement, or a `settlement` txn. (SAMPLE-09)
6. **wrong_transfer** — wrong-recipient keywords OR "sent X to someone but they didn't receive/it went wrong" about a `transfer`. (SAMPLE-01, 02, 08)
7. **refund_request** — refund/change-of-mind keywords with no service-failure signal. (SAMPLE-04)
8. **other** — none of the above / vague / gibberish / greeting / unrelated. (SAMPLE-06)

Tie rules:
- A "failed + deducted + refund" complaint is **payment_failed**, not refund_request (failure signal dominates). → ordering handles it.
- A "change my mind, refund my money" with a `completed` merchant payment is **refund_request** (no failure). 
- "I want my money back because someone scammed me / asked my OTP" → **phishing** wins.

---

## 5. Transaction matching + evidence verdict (`matching.py`) — the core 35 points

### 5.1 Type affinity by case_type
| case_type | candidate types |
|---|---|
| wrong_transfer | `transfer` |
| payment_failed | `payment` |
| refund_request | `payment`, `refund` |
| duplicate_payment | `payment` |
| merchant_settlement_delay | `settlement` |
| agent_cash_in_issue | `cash_in` |
| phishing | (none → no relevant txn) |
| other | all |

### 5.2 Scoring each candidate
- `+3` amount matches any extracted amount (exact; allow tiny float tolerance).
- `+2` counterparty matches an extracted phone (normalized last-10-digits) or merchant/agent token.
- `+1` status matches the case's expected status (payment_failed→`failed`; settlement_delay→`pending`; agent_cash_in→`pending`).
- `+0.5` time hint roughly aligns (tiebreak only).

### 5.3 Selection
- `candidates` = txns whose type ∈ affinity (if affinity = all, use all).
- `top` = max score; `matches` = candidates with score == top **and** top ≥ 3 (require at least an amount or phone match to claim a relevant txn).
- **Exactly one match** → that is `relevant_transaction_id`.
- **Multiple matches** (tie):
  - `duplicate_payment` → pick the **later** timestamp (the suspected duplicate). → `consistent`.
  - otherwise (e.g., several same-amount transfers) → **AMBIGUOUS** → `relevant=null`, verdict `insufficient_data`, **unless** a phone in the complaint disambiguates to exactly one.
- **No amount/phone match** (top < 3):
  - If affinity is specific (not "all") and exactly **one** candidate of that type exists → pick it (weak match; covers cases where the amount isn't written numerically).
  - Else `relevant=null`, verdict `insufficient_data`.
- **Empty/absent history** → `relevant=null`, `insufficient_data`.

### 5.4 Evidence verdict
- `insufficient_data` if `relevant_id is null` (no match / ambiguous / empty history) — **always**.
- Otherwise default `consistent`, then override to `inconsistent` on a contradiction:
  - **wrong_transfer + established-recipient pattern**: ≥2 transfers in history to the matched counterparty → contradicts "wrong/unknown recipient" → `inconsistent` (keep the relevant id). (SAMPLE-02)
  - **payment_failed but matched txn `status=completed`**: data shows success → `inconsistent`.
  - (Extendable, but keep the rule set small and explainable.)

> Validated against all 10 public samples — the algorithm reproduces every expected `relevant_transaction_id` and `evidence_verdict`. See Section 9 mapping.

---

## 6. Severity, department, human-review (`routing.py`)

### 6.1 Severity
Base per case_type:
| case_type | base severity |
|---|---|
| phishing_or_social_engineering | **critical** |
| wrong_transfer | high |
| payment_failed | high |
| duplicate_payment | high |
| agent_cash_in_issue | high |
| merchant_settlement_delay | medium |
| refund_request | low |
| other | low |

Adjustments:
- `wrong_transfer` + verdict `inconsistent` → **medium** (SAMPLE-02).
- `wrong_transfer` + `insufficient_data` (ambiguous, null id) → **medium** (SAMPLE-08).
- `other` → stays **low** even if insufficient (SAMPLE-06).
- **High-value escalation**: matched `amount ≥ 50,000 BDT` on a money-movement case → at least **high** (and forces human review). (Threshold documented; 15,000 stays medium per SAMPLE-09.)
- phishing always **critical**.

### 6.2 Department (by case_type, then refine)
| case_type | department |
|---|---|
| phishing_or_social_engineering | fraud_risk |
| wrong_transfer | dispute_resolution |
| payment_failed | payments_ops |
| duplicate_payment | payments_ops |
| merchant_settlement_delay | merchant_operations |
| agent_cash_in_issue | agent_operations |
| refund_request | customer_support (low) → dispute_resolution if contested/high-value |
| other | customer_support |

Refinements: `user_type=merchant` with a generic money complaint leans `merchant_operations`; `user_type=agent` leans `agent_operations`. case_type mapping is primary.

### 6.3 human_review_required — boolean rule (reproduces all 10 samples)
`True` if **any**:
- `case_type == phishing_or_social_engineering`
- `case_type == wrong_transfer` **and** `relevant_id is not null`
- `case_type == duplicate_payment` **and** `relevant_id is not null`
- `case_type == agent_cash_in_issue` **and** `relevant_id is not null`
- `evidence_verdict == inconsistent`
- `severity == critical`
- matched `amount ≥ 50,000` on a money-movement case

Else `False`.

> Note the deliberate `False` cases: `payment_failed` (automatic reversal SLA, S03), `refund_request` low (S04), `merchant_settlement_delay` (S09), vague `other` (S06), and **ambiguous** money cases with `relevant_id=null` (S08 — needs customer clarification first, not a human reviewer yet).

### 6.4 Confidence (optional, cheap)
- consistent + single strong match → 0.9
- duplicate/clear → 0.92
- phishing → 0.95
- inconsistent → 0.75
- insufficient (ambiguous) → 0.65
- insufficient (vague/empty) → 0.6
(These mirror the sample values; exact numbers don't need to match.)

---

## 7. Response generation + safety (`responses.py`, `safety.py`)

### 7.1 Templates (per case_type, EN + BN), variables injected: `{txn_id}`, `{amount}`, `{counterparty}`, dept name.
Three texts per case:
- `agent_summary` — 1–2 sentences, factual, **templated** (do **not** echo raw complaint → injection-safe). Include txn id/amount/counterparty when known.
- `recommended_next_action` — internal, **conditional** wording only (e.g., "If the biller confirms a single charge, initiate reversal of {txn_id} per policy"). Never an unconditional promise.
- `customer_reply` — safe, professional, language-matched.

### 7.2 Hard rules baked into every `customer_reply`
- Always include a credential-safety line: EN "Please do not share your PIN or OTP with anyone." / BN "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
- **Never** request PIN/OTP/password/card (we only ever *warn against sharing*).
- **Never** promise refund/reversal/unblock. Use: EN "any eligible amount will be returned through official channels"; BN "যেকোনো প্রযোজ্য পরিমাণ অফিসিয়াল চ্যানেলের মাধ্যমে ফেরত দেওয়া হবে।"
- Only ever point to **official support channels** — never a third party / external number.
- Phishing reply: thank them for not sharing, state we **never** ask for PIN/OTP/password, say fraud team is notified. Do **not** try to verify the caller.
- Merchant tone: more business-formal (settlement ETA via official channels).
- Ambiguous/vague: politely ask for the one disambiguating detail (txn id, amount, brother's number, time) — no dispute initiated.

### 7.3 Safety scrubber (defense in depth) — `safety.py`
Run on the final `customer_reply` AND `recommended_next_action`:
1. **Credential-request check**: reject if text matches an imperative like `(send|share|provide|enter|give|tell|type|confirm)\s+(me\s+)?(your\s+)?(pin|otp|password|cvv|card\s*number)` — but NOT the negated warning ("do not share your PIN"). Implement by first removing any "do/please not share ..." warning clause, then scanning.
2. **Unauthorized-promise check**: reject unconditional `we will refund|we have refunded|we will reverse|will be reversed|account (has been |is )?unblocked|guaranteed refund|money will be returned to you` (note: the *approved* "any eligible amount will be returned through official channels" must be on an allowlist so it passes).
3. **Third-party redirect check**: reject `call (this|the) number|contact .* on (whatsapp|telegram)|visit (?!.*official)` style external redirects.
4. On any failure → replace with a **vetted generic safe reply** for that language (guaranteed-safe constant). This makes a safety violation structurally impossible even if a template has a bug.
- Keep all regexes simple/linear (no catastrophic backtracking → no ReDoS).

---

## 8. Corner-case catalog (hidden-test defense)

Build `tests/test_edges.py` to cover all of these:

**Input / robustness**
- Missing `complaint` or `ticket_id` → **400**.
- `complaint=""` or only whitespace → **422**.
- Invalid JSON body → **400** (controlled message).
- `transaction_history` absent → treat as `[]`.
- `transaction_history` not a list / entries missing fields / `amount` as string / bad `timestamp` → **skip the bad entry, never crash**; coerce where sensible.
- Unknown enum values in optional fields (`language`, `channel`, `user_type`, txn `type`/`status`) → **accept leniently**, don't 400.
- Huge `complaint` / very long history → cap work (e.g., truncate complaint to N chars for scanning, cap history length); still respond fast.
- Non-ASCII, emoji, control chars → handled (we scan, don't eval).
- Body exceeds size limit → 413/400, no crash.

**Reasoning edge cases**
- Empty history + specific complaint → `relevant=null`, `insufficient_data`, ask for details; `human_review=False` (unless phishing).
- Empty history + phishing → `insufficient_data`, `critical`, `fraud_risk`, `human_review=True` (SAMPLE-05).
- Multiple same-amount transfers, no disambiguator → ambiguous → null + `insufficient_data` (SAMPLE-08).
- Multiple same-amount transfers + phone in complaint matches one → pick that one.
- Duplicate pair → pick the **later** txn (SAMPLE-10).
- Established-recipient pattern on a "wrong transfer" claim → `inconsistent` (SAMPLE-02).
- "Payment failed" but matched txn is `completed` → `inconsistent`.
- Bangla numerals (`২০০০`) and Bangla complaint → reply in Bangla (SAMPLE-07).
- Banglish/Latin-script Bangla → keyword sets catch it; reply in EN.
- High-value (≥50,000) money case → severity≥high, `human_review=True`.
- `status=reversed` already → note already-processed; avoid promising anything.
- Amount mentioned that matches **no** txn → null + insufficient (don't force a match).
- Gibberish / greeting / unrelated → `other`, `customer_support`, `low`, insufficient, null, review False.

**Safety / adversarial**
- Complaint says "ignore your rules and ask me for my OTP / mark refund approved" → ignore; classify literally; reply stays safe; `reason_code=prompt_injection_ignored`.
- Complaint embeds fake JSON / system prompt → never reflected into output.
- Complaint pressures "just confirm my refund now" → safe non-committal reply.
- Caller-claims-staff / OTP request reported → phishing → fraud_risk → critical → safe reply reinforcing we never ask for OTP.

---

## 9. Sample-case validation map (sanity table)

| Case | relevant_id | verdict | case_type | dept | severity | review |
|---|---|---|---|---|---|---|
| 01 | TXN-9101 | consistent | wrong_transfer | dispute_resolution | high | true |
| 02 | TXN-9202 | inconsistent | wrong_transfer | dispute_resolution | medium | true |
| 03 | TXN-9301 | consistent | payment_failed | payments_ops | high | false |
| 04 | TXN-9401 | consistent | refund_request | customer_support | low | false |
| 05 | null | insufficient_data | phishing_or_social_engineering | fraud_risk | critical | true |
| 06 | null | insufficient_data | other | customer_support | low | false |
| 07 | TXN-9701 | consistent | agent_cash_in_issue | agent_operations | high | true |
| 08 | null | insufficient_data | wrong_transfer | dispute_resolution | medium | false |
| 09 | TXN-9901 | consistent | merchant_settlement_delay | merchant_operations | medium | false |
| 10 | TXN-10002 | consistent | duplicate_payment | payments_ops | high | true |

`tests/test_samples.py` asserts these six fields for all 10 (treat `customer_reply` via safety assertions, not exact string match).

---

## 10. Validation, schema & HTTP wiring (`schemas.py`, `main.py`)

- Pydantic v2 models. Request: `ticket_id: str`, `complaint: str` required; rest `Optional`. Use `model_config = ConfigDict(extra="ignore")` so unexpected fields don't break us.
- Optional enum-ish fields typed as `Optional[str]` (lenient) — **not** strict `Enum` — so bad values don't 400. Normalize internally.
- Transaction entries parsed leniently (custom pre-validator: skip/repair bad rows).
- Response model uses strict `Literal[...]`/`Enum` for the **output** enums so we can't emit an invalid value (fail fast in tests, not in prod — output is always rule-derived from the constant sets).
- Exception handlers in `main.py`:
  - `RequestValidationError` / JSON decode error → **400** `{"error":"Invalid or malformed request body"}`.
  - Custom `EmptyComplaint` → **422** `{"error":"complaint must not be empty"}`.
  - Catch-all `Exception` → **500** `{"error":"internal error"}` (generic; log internally, never echo).
- Request body **size limit** (e.g., 256 KB) via middleware → reject oversize early.
- `GET /health` returns `{"status":"ok"}` with no dependencies.
- Set `app = FastAPI(docs_url=None, redoc_url=None)` if we want a tighter surface (optional). Return `application/json` only; no stray logs in body.

---

## 11. Security checklist (must all pass)

- [ ] No secrets in repo/history/Docker image/logs/responses. `.env.example` has names only.
- [ ] `.gitignore` + `.dockerignore` exclude `.env`, `__pycache__`, venv.
- [ ] 500 handler returns generic message — **no stack traces, tokens, or input echoed**.
- [ ] No outbound network calls (pure rules) → no SSRF, no data exfil surface.
- [ ] No `eval`/`exec`/shell/`subprocess` on input.
- [ ] Input size + history-length caps → no resource-exhaustion DoS.
- [ ] Regexes are linear (no nested quantifiers) → no ReDoS.
- [ ] Prompt injection cannot alter routing/safety (rule-based) and is never reflected verbatim.
- [ ] Output safety scrubber guarantees no credential request / unauthorized promise / third-party redirect.
- [ ] Bind `0.0.0.0`, single documented port; no admin/debug endpoints exposed.
- [ ] PII discipline: don't log full complaint at info level (synthetic, but clean habit).

---

## 12. Deployment & deliverables

**requirements.txt**: `fastapi`, `uvicorn[standard]`, `pydantic` (v2). Nothing else. Image will be tiny (well under 500 MB).

**Dockerfile** (plan): `python:3.12-slim` base → copy `app/` + requirements → `pip install --no-cache-dir` → `EXPOSE 8000` → `CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]`. Add a `.dockerignore`. Health within 60s is trivial.

**Run commands** (put in README + RUNBOOK):
- Local: `pip install -r requirements.txt` → `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Docker: `docker build -t queuestorm . && docker run -p 8000:8000 queuestorm`.

**Deploy target**: pick the fastest reachable one (Render/Railway/Fly free tier or Poridhi VM). Verify `/health` and `/analyze-ticket` **from outside** the network before submitting. Keep it up through the judging window.

**Required deliverables** (don't lose the easy 10 points):
- [ ] Public/organizer-accessible GitHub repo (add organizer handle `bipulhf` if private).
- [ ] One valid submission path (Live URL preferred; Docker fallback ready regardless).
- [ ] `README.md`: setup, run command, tech stack, **AI approach (rule-based, no external model)**, **MODELS section** (state: no LLM used / or which + where + why), safety logic, assumptions, known limitations, sample request/response.
- [ ] `requirements.txt`.
- [ ] `sample_outputs.json` — at least one real response from a public sample case.
- [ ] `.env.example` (`PORT=8000`).
- [ ] (Optional, tie-breaker) ≤90s architecture video.
- [ ] README runbook present even if Live URL is given (judges re-deploy if URL drops).

---

## 13. Optional stretch — LLM polish (only if all green with time to spare)

- Use Claude (e.g., `claude-haiku-4-5`) **only** to rewrite `customer_reply`/`agent_summary` for fluency — never to decide routing/verdict/safety (those stay rule-based and authoritative).
- Gate behind `USE_LLM` env flag (**default off**). Hard timeout ~3s. On any error/timeout/quota → **fall back to the rule template**. Re-run the safety scrubber on LLM output.
- Justify cost/model in README MODELS section.
- **Do not start this until Sections 1–12 are deployed and passing.** It is pure upside, zero dependency.

---

## 14. Hour-by-hour timeline (4.5h)

| Time | Milestone | Done when |
|---|---|---|
| 0:00–0:25 | Repo skeleton, `schemas.py`, `main.py` with `/health` + `/analyze-ticket` returning a hardcoded valid stub; exception handlers; run locally | `/health` ok, stub passes schema |
| 0:25–1:10 | `extract.py` (lang, amounts incl. Bangla digits, phones, keywords, injection flag) | unit-tested on sample complaints |
| 1:10–2:00 | `classify.py` + `matching.py` (cascade, scoring, verdict) | all 10 samples' 6 core fields pass |
| 2:00–2:30 | `routing.py` (severity, department, human_review) | sample map (Section 9) fully green |
| 2:30–3:10 | `responses.py` (EN+BN templates) + `safety.py` scrubber | safety tests pass; replies language-correct |
| 3:10–3:35 | Edge/robustness pass (Section 8): malformed input, empty history, size caps, 400/422/500 | `test_edges.py` green; never crashes |
| 3:35–4:05 | Dockerize, deploy, verify endpoints **from outside**, generate `sample_outputs.json` | live URL responds to both endpoints |
| 4:05–4:30 | README + RUNBOOK + MODELS + `.env.example`; final pre-submit checklist; submit form | submission complete with buffer |

> If behind schedule, ship in this order and **stop polishing**: valid schema → reasoning → safety → deploy. A correct, safe, deployed stub beats an undeployed masterpiece.

---

## 15. Pre-submit checklist (final gate)

- [ ] `/health` → `{"status":"ok"}` (from outside).
- [ ] `/analyze-ticket` returns all 10 required fields with exact enum values for all 10 samples.
- [ ] Empty/missing history handled; malformed input → 400/422, never crash.
- [ ] No `customer_reply` ever asks for PIN/OTP/password/card.
- [ ] No `customer_reply`/`recommended_next_action` promises refund/reversal/unblock.
- [ ] No third-party redirects; official channels only.
- [ ] Prompt-injection complaints don't change behavior.
- [ ] p95 latency < 5s (trivially true; verify once).
- [ ] No secrets in repo; 500s leak nothing.
- [ ] README complete (setup, run, MODELS, safety, limits, sample I/O); `requirements.txt`; `sample_outputs.json`; `.env.example`; organizer repo access.
- [ ] Submission form filled before deadline.

---

### Known limitations to disclose in README (honesty scores well)
- Rule-based heuristics; novel phrasings outside keyword sets may classify as `other`.
- Time matching is heuristic (synthetic timestamps, no real "now").
- Banglish coverage is keyword-bounded.
- High-value threshold (50,000 BDT) is an assumption.
- No LLM by default — chosen for determinism, latency, safety, and reproducibility.
