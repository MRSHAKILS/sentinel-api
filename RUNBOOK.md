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

Pure rules (no key needed) — runs immediately, safe defaults:
```bash
docker run -d --name queuestorm -p 8000:8000 queuestorm
```

Hybrid (Gemini assist) — pass the key as env vars. Inside the container there is
**no `.env` file** (it is intentionally excluded from the image), so config must
come from `-e` flags or `--env-file`:
```bash
docker run -d --name queuestorm -p 8000:8000 \
  -e USE_LLM=true \
  -e OPENROUTER_API_KEY=sk-or-... \
  -e LLM_MODEL=google/gemini-2.5-flash \
  -e LLM_TIMEOUT_SECONDS=4.5 \
  queuestorm
```

Or with an env file you create on the VM (do NOT commit it):
```bash
# judging.env  -> USE_LLM=true / OPENROUTER_API_KEY=... / LLM_MODEL=google/gemini-2.5-flash
docker run -d --name queuestorm -p 8000:8000 --env-file judging.env queuestorm
```

Custom port: add `-e PORT=9000` and map `-p 9000:9000`.
The container binds `0.0.0.0`, runs as a non-root user, and has a built-in
HEALTHCHECK (`docker ps` shows `healthy` once `/health` responds).

---

## Deploying on a VM (EC2 / Poridhi VM / any Linux host)

```bash
# 1. Install Docker (Ubuntu)
curl -fsSL https://get.docker.com | sudo sh

# 2. Get the code and build
git clone <your-repo-url> queuestorm && cd queuestorm
sudo docker build -t queuestorm .

# 3. Create the env file with your key (never commit this)
cat > judging.env <<'EOF'
USE_LLM=true
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=google/gemini-2.5-flash
LLM_TIMEOUT_SECONDS=4.5
EOF

# 4. Run (auto-restart on reboot/crash)
sudo docker run -d --name queuestorm --restart unless-stopped \
  -p 80:8000 --env-file judging.env queuestorm

# 5. Verify locally on the VM
curl http://localhost:80/health
```

**Open the port to the internet** — this is the most common reason the judge
can't reach you:
- AWS EC2 / Poridhi: add an inbound Security Group rule for the port (80/8000)
  from `0.0.0.0/0`.
- Plain Linux firewall: `sudo ufw allow 80/tcp`.

Then confirm from **outside** the VM:
```bash
curl http://<VM_PUBLIC_IP>/health      # -> {"status":"ok"}
```

> If the LLM key is missing or wrong, the service still answers correctly using
> the deterministic rules (you'll see `llm_fallback_rules` in `reason_codes`),
> so a bad key never takes the service down.

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
