# Parcel and Delivery Tracking Assistant

A Python/FastAPI university assignment project. The app will provide simulated
parcel tracking, a Gemini assistant grounded in parcel records, authenticated
API endpoints, signed webhooks, and a small admin interface. It does not connect
to real courier services.

Development is being completed in phases. Phase 1 added the project scaffold;
Phase 2 adds API-key-authenticated parcel endpoints and their Supabase
migration. Gemini, webhooks, public/admin pages, and deployment configuration
will be added in later phases.

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
scopes. Read and write requests are limited to 60 per API key per minute.

- `POST /api/v1/parcels` (write): create a parcel and its initial timeline event.
- `GET /api/v1/parcels/{tracking_id}` (read): get an owner-scoped parcel and
  chronological timeline.
- `PATCH /api/v1/parcels/{tracking_id}/status` (write): update status and add a
  timeline event in one database transaction.

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
local test keys as described above. Start the server with
`python -m uvicorn app:app --reload`, then run:

```powershell
.\tests\phase2-integration.ps1
```

The integration script checks success, 401, 403, 404, and 429 responses, plus
parcel creation and status updates. It creates a uniquely named test parcel.

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
