# Parcel Tracking Assistant

A FastAPI app where you can look up a parcel, read its status and timeline, and
ask a chatbot about it in English, Hindi or Kannada. The chatbot answers only
from database records. Other apps can use the same features through an API key
protected REST API and signed webhooks.

**Live demo:** https://parcel-tracking-dusky.vercel.app (try tracking ID
`DEMO-PARCEL-001`) · [API guide](https://parcel-tracking-dusky.vercel.app/docs-guide)

Demo parcels are simulated. Real parcels can be added by forwarding an order
or shipping email; the app does not call courier APIs.

## Features

- **Chatbot first.** The home page is a multi-turn chat with the parcel's
  status, ETA and timeline beside it. Type a tracking ID inside any message.
- **Grounded and read-only.** Parcel facts come only from the database. The
  model is told to treat the question and history as untrusted data, to say "I
  don't know" when the records don't say, and to decline unrelated requests.
  It cannot change a parcel.
- **Three languages.** It replies in the language of the question: English,
  Hindi or Kannada.
- **REST API** under `/api/v1`: create a parcel, get tracking details, update
  status, ask the assistant. Keys have `read` or `write` scope, are stored only
  as SHA-256 hashes, and are rate limited per key and per client IP.
- **Webhooks.** Register an HTTPS endpoint and receive status updates signed
  with HMAC-SHA256, with up to three delivery attempts logged.
- **Parcels from forwarded emails.** Forward an Amazon, Flipkart or Blue Dart
  email to a CloudMailin address. Gemini extracts courier, order ID, AWB,
  items, status, ETA and city as JSON; Python keeps only values written in
  the email, then creates or updates the parcel and sends the webhook when the
  status changes. Search by tracking ID, AWB or order ID. See
  [docs/inbound-email.md](./docs/inbound-email.md).
- **Admin dashboard** for parcels, email imports (with a "Paste email text"
  box), API keys, webhooks and delivery logs, behind
  a signed session cookie and CSRF tokens.
- **Clean errors.** Gemini quota, overload and timeout problems return
  friendly 429, 503 and 504 responses without leaking provider details; a
  temporary overload is retried once.
- **Browser protections.** Content-Security-Policy, no framing, nosniff and
  referrer policy headers; admin sign-in is limited to 5 attempts a minute.

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python 3.14, FastAPI, Pydantic |
| Pages | Jinja2 templates, plain CSS, minimal vanilla JavaScript |
| Database | Supabase (PostgreSQL) with row-level security |
| Assistant | Google Gemini through the `google-genai` SDK |
| Webhooks | `httpx` |
| Tests | pytest with a fake Supabase client |
| Hosting | Vercel (free tier) |

## How it fits together

```
Browser ──► FastAPI (app.py) ──► Supabase  (parcels, events, keys, limits)
Other apps ─► /api/v1 + x-api-key   │
CloudMailin ─► /api/v1/inbound-email ├──► email_import.py ──► Gemini (JSON)
                                    ├──► Gemini  (answers from the records)
                                    └──► webhooks.py ──► your HTTPS endpoint
```

The chat page and `POST /api/v1/assistant` share one function, so the website
and other apps get the same answers.

## Quick start (Windows PowerShell)

You need Python 3.14, a Supabase project and a Gemini API key.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

If PowerShell blocks activation, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first.

1. Fill in `.env.local` (see the table below). It is ignored by Git.
2. Create the database by running the SQL files in [docs/database.md](./docs/database.md).
3. Start the app:

```powershell
python -m uvicorn app:app --reload
```

Open http://127.0.0.1:8000 for the app, `/health` for a health check and
`/docs` for the interactive API reference.

## Environment variables

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Your project URL, for example `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side key. Never expose it in browser code |
| `GEMINI_API_KEY` | Google AI Studio key |
| `GEMINI_MODEL` | Exact model ID. It must work with your key and accept system instructions |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Admin sign-in |
| `SESSION_SECRET` | Long random string that signs the admin cookie |
| `COOKIE_SECURE` | `true` on HTTPS hosting such as Vercel |
| `RATE_LIMIT_REQUESTS` | Requests per minute, 1 to 10,000. Default 60 |
| `INBOUND_EMAIL_SECRET` | At least 24 characters. CloudMailin's Authorization header is `Bearer <secret>` |
| `ALLOWED_EMAIL_SENDERS` | Comma-separated addresses allowed to forward parcel emails |
| `INBOUND_REQUIRE_SPF` | `true` (default) requires an SPF pass for forwarded emails |
| `INBOUND_EMAIL_RATE_LIMIT` | Email imports per minute, 1 to 1,000. Default 10 |

On Vercel, add the same variables in the project settings. Redeploy after you
change one, because new values only apply to new deployments.

## Run the tests

```powershell
python -m pytest -q
```

They need no credentials. See [docs/testing.md](./docs/testing.md) for the live
API and assistant checks. A GitHub Actions workflow (`.github/workflows/tests.yml`) runs the same tests on every push and pull request.

## Project structure

```
app.py          FastAPI app: pages, admin, /api/v1 routes, Gemini assistant
webhooks.py     Webhook URL checks, signed delivery, retries, logging
email_import.py Forwarded email -> Gemini extraction -> validated parcel updates
gemini_utils.py One retry when Gemini reports a temporary overload
templates/      Jinja pages
static/         CSS, small JavaScript, link preview image
supabase/       schema.sql, seed.sql and migrations to run in the SQL Editor
tests/          pytest suite and PowerShell integration scripts
scripts/        Helper scripts (test key creation, UI screenshots)
docs/           Database setup, API notes, testing, webhook walkthrough
```

## Documentation

- [Database setup](./docs/database.md)
- [API notes](./docs/api.md)
- [Parcels from forwarded emails](./docs/inbound-email.md)
- [Testing](./docs/testing.md)
- [Testing a webhook](./docs/webhook-testing.md)
- [Contributing](./CONTRIBUTING.md) · [Security policy](./SECURITY.md)

## Deployment

On Vercel, choose the FastAPI preset. `pyproject.toml` sets
`[tool.vercel] entrypoint = "app:app"` and lists the same pinned dependencies as
`requirements.txt`; a test keeps the two lists identical. `vercel.json` includes
`templates/` and `static/` in the function bundle.

## Known limitations

- Demo parcel data is simulated, with no courier API integration. Email
  import only knows what the forwarded emails say.
- Rate-limit rows are never deleted automatically, so that table keeps growing.
- Client IPs are stored as plain SHA-256 hashes, which are not fully anonymous.
- The chat limit is per minute only, with no daily cap. The free Gemini quota
  can run out, and the chat then shows a rate-limit message.
- Setup instructions cover Windows PowerShell only, and there is no Docker image.

## Credits

Built by Sharaj R Shetty as a university GenAI assignment. Uses FastAPI,
Supabase, Google Gemini and Vercel. Licensed under the [MIT License](./LICENSE).
