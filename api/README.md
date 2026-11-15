# nostos API: website assistant (Grok Bot)

A visitor asks a question. FastAPI stores it as a `pending` row and POSTs it to the Grok Bot routine's webhook. The routine answers on its own time and POSTs the answer back to `/api/ask/answer`. That saves it to the row and pushes it to the visitor's WebSocket, if one is open.

```
Browser ─POST /api/ask─▶ FastAPI ─INSERT pending─▶ Tiger (assistant_requests)
                            └─POST webhook {request_id, question}─▶ Grok Bot routine
Grok Bot ─POST /api/ask/answer {request_id, answer}─▶ FastAPI ─UPDATE → done─▶ Tiger
                                                         └─push─▶ Browser WS /ws/ask/{id}
Browser (no WS, or reconnecting) ─GET /api/ask/{id} every 2–3 s─▶ FastAPI
```

## Run
```
pip install -r requirements.txt
python db/run_sql.py db/07_assistant.sql              # once; already applied on Tiger
uvicorn api.main:app --proxy-headers                  # ONE worker; http://127.0.0.1:8000/docs
cloudflared tunnel --url http://localhost:8000        # second terminal: public https URL for the routine
python -m pytest tests -q                             # offline tests, no DB or webhook needed
```
- `.env` (gitignored) holds `GROK_BOT_WEBHOOK_URL`, `GROK_BOT_WEBHOOK_KEY`, `GROK_BOT_AUTH` and `NOSTOS_ANSWER_SECRET`. See `.env.example`.
- `TIMESCALE_SERVICE_URL` is found the same way as in `db/run_sql.py`: the environment, then the Tiger credentials file, then the repo `.env`.
- Keep **one** uvicorn worker. Open sockets live in that process's memory. To scale out, switch to Postgres `LISTEN/NOTIFY`.
- A cloudflared quick tunnel gets a **new URL every time it starts**. Give the routine the new one each time.

**End-to-end test:** with the API and the tunnel running, run `python -m api.grok_bot "What is tomorrow's ERCOT peak?"`. It inserts a pending row, POSTs the webhook, and waits up to 5 minutes for the routine's answer to come back.

## Endpoints
| Call | Returns |
|---|---|
| `POST /api/ask` `{"question": "..."}` (1–1000 chars) | `202 {"request_id", "status": "pending"}` · `422` bad question · `429` more than 5 per minute per IP · `502 {"request_id", "status": "failed", "error"}` webhook down |
| `GET /api/ask/{request_id}` | `{"request_id", "status", "answer", "error", "created_at", "answered_at"}` · `404` unknown. `status` is `pending`, `done` or `failed` (a row still pending after 5 min shows as `failed` / `"timed out"`). |
| `WS /ws/ask/{request_id}` | Sends **one** message with the same JSON as GET once the row is `done` or `failed` (right away if it already is), then closes. Closes after 5 min with `"timed out"`. Unknown id: close code `4404`. |
| `POST /api/ask/answer` | **For the Grok Bot routine.** Body `{"request_id", "answer"}`, or `{"request_id", "error"}` when it can't answer. Header `Authorization: Bearer <NOSTOS_ANSWER_SECRET>`. `200` saved · `401` bad token · `409` unknown or already answered · `422` bad body (an answer is limited to 8000 chars) · `404` if no secret is configured. |

Frontend: after `POST /api/ask`, open `/ws/ask/{id}` and show "Grok Bot is working…". When the message arrives, show the answer if `done`, or a retry button if `failed`. If the socket drops before that message, poll `GET /api/ask/{id}`. The webhook URL, key and answer secret stay on the server and never go in frontend code.

## Files
| File | What it does |
|---|---|
| `grok_bot.py` | Webhook client: POSTs `{request_id, question}` with `bearer` or `signed` (Standard Webhooks HMAC) auth. Running it on its own does the end-to-end test. |
| `ask.py` | All four endpoints, the WebSocket registry and the in-memory rate limiter. Use `app.include_router(ask.router)` in any app. |
| `assistant_store.py` | DB access: `create_request`, `mark_failed`, `get_request`, `save_answer` |
| `main.py` | Minimal app that includes the router |
