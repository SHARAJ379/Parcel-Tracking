# Parcel and Delivery Tracking Assistant

A Python/FastAPI university assignment project. The app will provide simulated
parcel tracking, a Gemini assistant grounded in parcel records, authenticated
API endpoints, signed webhooks, and a small admin interface. It does not connect
to real courier services.

Development is being completed in phases. Phase 1 added the project scaffold;
Phase 2 adds API-key-authenticated parcel endpoints and their Supabase
migration. Phase 3 adds the Gemini assistant; webhooks, public/admin pages, and
deployment configuration will be added in later phases.

## Requirements

- Python 3.14 (pinned in `.python-version` and supported by Vercel)
- PowerShell on Windows
- A Supabase project and Gemini API key for the later integration phases

## Windows PowerShell setup

Run these commands from the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

If PowerShell blocks activation, allow scripts for the current terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Fill in `.env.local` with the Supabase URL and service-role key. Keep real
credentials out of source control; `.env.local` is ignored by Git.

## Phase 2: Supabase migration

Before testing the parcel API against Supabase, inspect the existing
`rate_limits` columns with this query in the Supabase SQL Editor:

```sql
SELECT
    ordinal_position,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'rate_limits'
ORDER BY ordinal_position;
```

Then run the complete migration at
[`supabase/migrations/20261126100000_rate_limits_and_parcel_operations.sql`](./supabase/migrations/20261126100000_rate_limits_and_parcel_operations.sql)
in the Supabase SQL Editor. It preserves existing API-key counters, adds
hashed-client-IP counters, and uses atomic PostgreSQL upserts. It also adds
transactional database functions used to create parcels with their initial
event and update statuses with their timeline events. It does not recreate or
seed any tables.

To create local test credentials for the seeded sample owner, run this once
after the migration:

```powershell
python scripts\provision_phase2_test_keys.py
```

This generates random read-only and read/write keys, stores only their hashes
in Supabase, and writes the local test keys to the ignored `.env.local` file
without displaying them. These keys are only for local testing; revoke them in
Supabase when finished.

## Phase 2 API

All `/api/v1` routes require the `x-api-key` header. Keys are SHA-256 hashed
before lookup, checked for revocation, and restricted to their stored owner and
scopes. Read and write requests default to 60 per API key per UTC minute. Set
`RATE_LIMIT_REQUESTS` to a positive integer from 1 through 10,000 to tune that
limit; the production default remains 60. Each request window is aligned to a
UTC minute. The Supabase client is cached for the process lifetime. API-key
validation and `last_used_at` update share one database call; the limiter and
parcel/timeline queries remain separate.

- `POST /api/v1/parcels` (write): create a parcel and its initial timeline event.
- `GET /api/v1/parcels/{tracking_id}` (read): get an owner-scoped parcel and
  chronological timeline.
- `PATCH /api/v1/parcels/{tracking_id}/status` (write): update status and add a
  timeline event in one database transaction.
- `POST /api/v1/assistant` (read): ask about one owner's parcel and its
  timeline using Gemini.

The assistant request body is `{"tracking_id": "DEMO-PARCEL-001",
"question": "What is the current status?"}`. Questions are limited to 1,000
characters. The model receives only that owner's parcel and its timeline;
question text and record values are explicitly treated as untrusted data.
Missing parcel records return 404 without calling Gemini. Gemini timeouts
return 504, upstream quota limits return 429, and other upstream or empty
responses return 502 without exposing provider details.

API responses use `{"success": true, "data": ...}` or
`{"success": false, "error": {"code": ..., "message": ...}}`. Invalid input is
returned as HTTP 400; missing/invalid keys as 401; missing scope as 403;
unknown or other-owner parcels as 404; duplicate tracking IDs as 409; and
rate-limited requests as 429.

## Run and test

```powershell
python -m uvicorn app:app --reload
```

Open `http://127.0.0.1:8000/health` to see the scaffold health response and
`http://127.0.0.1:8000/docs` for FastAPI's generated API documentation.

In another activated PowerShell terminal, run the focused unit tests:

```powershell
python -m pytest
```

For the API integration checks, first run the migration and provision the
local test keys as described above. Use the same low limit in both PowerShell
windows so the test can reach 429 well within one 60-second window:

```powershell
$env:RATE_LIMIT_REQUESTS = '2'
python -m uvicorn app:app --reload --port 8001
```

In the second window:

```powershell
$env:RATE_LIMIT_REQUESTS = '2'
$env:PARCEL_API_BASE_URL = 'http://127.0.0.1:8001'
.\tests\phase2-integration.ps1
```

The integration script checks success, 401, 403, 404, and 429 responses, plus
parcel creation and status updates. It shows each rate-limit probe and creates
a uniquely named test parcel. The configured test limit affects only the test
server; do not lower the production default to make this test pass.

## Phase 3: Gemini assistant

Set `GEMINI_API_KEY` and `GEMINI_MODEL` in ignored `.env.local`. The SDK client
uses the configured model and a 15-second request timeout. To run its mocked
tests:

```powershell
python -m pytest
```

For live endpoint checks, start the app and then run the test script in another
PowerShell window:

```powershell
python -m uvicorn app:app --reload
```

```powershell
.\tests\phase3-assistant.ps1
```

The live script checks missing-key, unknown-parcel and question-length errors,
then asks Gemini about the seeded `DEMO-PARCEL-001` parcel. It reads the
existing test key from `.env.local` without printing it.

## Deployment

The application entrypoint is `app.py`, as recognized by Vercel's Python
runtime. Deployment instructions and environment variable configuration will
be added after the application features are implemented.

## Official setup references

- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Set Python version on Vercel](https://vercel.com/docs/functions/runtimes/python/python-version)
- [Gemini API Python quickstart](https://ai.google.dev/gemini-api/docs/quickstart?lang=python)
- [Supabase Python reference](https://supabase.com/docs/reference/python/introduction)
