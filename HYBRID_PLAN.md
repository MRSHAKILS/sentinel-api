# Hybrid Rule + AI Plan (adjusted) — for review

Goal: add Google **Gemini 2.5 Flash** (via OpenRouter) to the existing service as
an **assist layer**, without putting the 35-point Evidence Reasoning score at the
mercy of an LLM call. Your 3-stage sketch is the right skeleton; this adjusts
*who decides what* for the best, safest outcome.

---

## Why not let the LLM decide (your Stage 2 "Translator picks the transaction")

- The rule engine **already reproduces all 10 sample cases** on the six scored
  fields, deterministically, in ~3 ms.
- Letting the LLM pick the transaction / verdict introduces: hallucinated or
  invalid IDs, nondeterminism (same input → different answer), latency, and
  hard failures when OpenRouter/Gemini rate-limits or errors mid-judging.
- Rubric reality: tie-breaker #1 is **safety**, #2 is **evidence reasoning**.
  Determinism + a guaranteed-correct fallback protect both.
- The sample pack explicitly rewards **not guessing** (SAMPLE-08 is correct
  *because* it returns `insufficient_data`). A confident LLM guessing a
  transaction there would turn a correct answer into a wrong one.

**Therefore:** rules stay authoritative for the scored fields; the LLM adds
upside where it is actually strong — **language understanding** (messy
Banglish/mixed, novel phrasing, credential-solicitation nuance) and **fluent,
on-tone reply drafting** — and **code verifies every LLM output**.

---

## Adjusted architecture (maps to your 3 stages)

```
[ Ticket ]
    │
    ▼
STAGE 1 — BOUNCER  (existing app/main.py + schemas.py)
  • Validate; 400 / 422 / 413 on bad input. Never crash.            (unchanged)
    │ clean data
    ▼
STAGE 2 — RULE ENGINE  (existing app/pipeline.py)        ← runs ALWAYS
  • Deterministic baseline: relevant_transaction_id, evidence_verdict,
    case_type, severity, department, human_review_required + template texts.
  • This is the backbone AND the fallback. If the LLM is off/slow/broken,
    this is the final answer.
    │ rule decision + facts
    ▼
STAGE 2.5 — TRANSLATOR / ASSIST  (new app/llm.py)         ← optional, 1 call
  • ONE Gemini call. Input: complaint (as untrusted data) + compact
    transactions + the rule decision. Output: strict JSON with
      - drafted customer_reply / agent_summary / recommended_next_action
        (written to MATCH the rule decision, in the customer's language)
      - a structured "second opinion": {case_type, transaction_id,
        credential_solicited, confidence}
  • Hard timeout (4.5s). Any timeout/error/non-JSON → return None → rules used.
    │ llm draft + second opinion (or None)
    ▼
STAGE 3 — INSPECTOR / RECONCILER  (existing app/safety.py + new glue)
  • Validate LLM JSON: enums legal; transaction_id ∈ real ids ∪ {null}.
  • Reconcile decision fields per the chosen policy (below).
  • Safety-scrub ALL customer-facing text (existing scrubber). If the LLM
    text fails any check → drop it, use the rule template.
  • Final enum clamp → 100% schema-valid, 100% safe JSON.
    │
    ▼
[ Final Output ]
```

Single round trip. Text is drafted *to the final rule decision*, so the reply
never references a transaction the decision didn't pick.

---

## The one decision for you: how much authority does the LLM get?

| Policy | What the LLM can change | Risk to 35-pt core | Upside |
|---|---|---|---|
| **A. Assist-only (recommended)** | Only the *wording* of replies/summary. Decision fields are 100% rules. | ~0 (core identical to today) | Better Banglish replies (Response Quality + tie-breaker #6); strictly ≥ pure rules |
| **B. Guarded override** | A, **plus** may set decision fields **only** when rules are low-confidence (`other` / `insufficient_data` from *no signal*, never the *ambiguous* multi-match case) and its `transaction_id` is real. | Low | Rescues hard hidden Banglish cases the keywords miss |
| **C. LLM-first (your original)** | LLM decides; code verifies/overwrites. | Higher (nondeterminism on clear cases) | Max language flexibility |

Recommendation: **A**, optionally **B** if you want the extra hidden-case
coverage. B is carefully fenced to never override the "don't guess" cases.

---

## Reliability & safety rails (non-negotiable, baked in)

- **Always-fallback:** rules produce a complete answer before the LLM is called;
  the LLM can only *improve*, never *break*, the response.
- **Timeout 4.5s** (well under the 30s hard limit; keeps p95 ≤ 5s for full
  latency credit). One attempt, no retries on the hot path.
- **Strict JSON** (`response_format: json_object`) + defensive parse; bad shape → fallback.
- **Safety is code, not prompt:** the system prompt tells Gemini never to ask for
  credentials / promise refunds / follow instructions inside the complaint — but
  the guarantee is the existing `safety.py` scrubber, which runs on the LLM text
  regardless. Defense in depth.
- **Injection-safe:** the complaint is passed as clearly-delimited untrusted data;
  the LLM's drafted text is still scrubbed; decisions still come from rules.
- **Transaction-ID validation:** any LLM-named id not present in the input history
  is rejected.
- **Secrets:** key only in `.env` (gitignored) / host env vars; never in repo,
  logs, image, or responses.

---

## Implementation checklist (after you approve)

1. `requirements.txt` += `httpx`; add `python-dotenv` + `load_dotenv()` so local
   `.env` is read (no-op in prod where the host sets env vars).
2. `app/settings.py` — read `USE_LLM`, `LLM_MODEL`, `OPENROUTER_API_KEY`,
   `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS`.
3. `app/llm.py` — OpenRouter client, prompt builder (untrusted-data framing),
   one combined enrich+draft call, strict-JSON parse, **returns None on any
   failure**. Connectivity smoke test as the first step.
4. `app/pipeline.py` — after the rule baseline: if `USE_LLM`, call the LLM,
   reconcile per chosen policy, scrub text, assemble. Add reason codes
   (`llm_text_used` / `llm_fallback_rules` / `llm_override` for visibility).
5. Tests — mock the client; assert: LLM off ⇒ identical to today (all 69 pass);
   bad/invalid txn id ⇒ rejected; unsafe LLM text ⇒ scrubbed to template;
   timeout/error ⇒ rules used; (policy B) override only on low-confidence.
   Tests stay **offline** (conftest forces `USE_LLM=false` unless mocked).
6. README/MODELS — document Gemini 2.5 Flash usage, cost (~fractions of a cent
   per ticket), and the fallback behavior.

---

## Open questions for you
1. **Authority policy:** A (assist-only), B (guarded override), or C (LLM-first)?
2. **Run the LLM live during judging** (on by default, with rule fallback), or
   keep it flag-gated and submit rules-only until you decide?

(Connectivity test against the real key is the first implementation step — held
until you approve so we don't spend credits prematurely.)
