# RUNBOOK — QueueStorm Investigator

A stranger should be able to bring this service up by copy-pasting from here.
No secrets are required.

---

## Option A — Local Python (fastest)

Requires Python 3.10+ (developed on 3.12).

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check (new terminal):
```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## Option B — Docker

```bash
docker build -t queuestorm .
docker run -p 8000:8000 queuestorm
```

With a custom port:
```bash
docker run -p 9000:9000 -e PORT=9000 queuestorm
```

The container binds `0.0.0.0`, runs as a non-root user, and needs no env file.

---

## Smoke test the main endpoint

```bash
curl -X POST http://localhost:8000/analyze-ticket \
  -H "content-type: application/json" \
  -d '{
    "ticket_id":"TKT-001",
    "complaint":"I sent 5000 taka to a wrong number around 2pm today.",
    "language":"en",
    "transaction_history":[
      {"transaction_id":"TXN-9101","timestamp":"2026-04-14T14:08:22Z","type":"transfer","amount":5000,"counterparty":"+8801719876543","status":"completed"}
    ]
  }'
```

Expect a `200` JSON body with `relevant_transaction_id: "TXN-9101"`,
`evidence_verdict: "consistent"`, `case_type: "wrong_transfer"`,
`department: "dispute_resolution"`, `human_review_required: true`.

---

## Run the test suite

```bash
pip install -r requirements-dev.txt
pytest -q
```

All tests should pass (10 public sample cases + edge/robustness/safety).

---

## Deploying to a public host (e.g. Render / Railway / Fly / EC2 / Poridhi VM)

1. Push this repository to the host or point the host at it.
2. **Build command:** `pip install -r requirements.txt`
3. **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   (most PaaS providers inject `$PORT`; the app reads it and binds `0.0.0.0`).
4. No environment variables are required.
5. After deploy, confirm from **outside** the network:
   ```bash
   curl https://YOUR-PUBLIC-URL/health
   ```
6. Ensure no login / private network is required to reach `/health` and
   `/analyze-ticket`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| 404 on endpoints | Use exact paths `/health` and `/analyze-ticket`. |
| Not reachable externally | Bind `0.0.0.0` (already default) and expose the port. |
| Port already in use | Change `PORT` (`-e PORT=9000` for Docker) and republish. |
| 413 on large request | Body exceeds 256 KB; raise `MAX_BODY_BYTES` if genuinely needed. |
| Bangla shows as `\uXXXX` | That is valid JSON unicode escaping; clients decode it to Bangla. |

No API keys, model downloads, or GPU are needed at any point.
