# Parcel and Delivery Tracking Assistant

A FastAPI application for simulated parcel tracking, parcel-grounded assistant
answers, authenticated API endpoints, signed webhooks, and admin operations.
It does not connect to real courier services.

## Requirements

- Python 3.14 (pinned in `.python-version` and supported by Vercel)
- PowerShell on Windows
- A Supabase project and Gemini API key

## Windows PowerShell setup

Run these commands from the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

If PowerShell blocks activation, allow scripts for the current terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Fill in `.env.local` with the Supabase URL and service-role key. Keep real
credentials out of source control; `.env.local` is ignored by Git.

## Database files

The full schema is version-controlled in `supabase/`. To build a new, empty
Supabase project, run these in the SQL Editor in this order:

1. [`supabase/schema.sql`](./supabase/schema.sql): base tables, indexes,
   row-level security and service-role grants.
2. Every file in `supabase/migrations/`, oldest timestamp first.
3. [`supabase/seed.sql`](./supabase/seed.sql): the three `DEMO-PARCEL-*`
   sample parcels. It skips rows that already exist.

The existing project database already has all of this applied; do not re-run
it there.

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
- `POST /api/v1/assistant` (read): multi-turn chat about one owner's parcel and
  its timeline using Gemini.

The assistant request body is `{"question": "...", "tracking_id": "...",
"history": [{"role": "user" | "assistant", "text": "..."}]}`. Only `question`
is required. The parcel is chosen in this order: a tracking ID written in the
question, then `tracking_id`, then the latest ID the user wrote in `history`.
IDs are matched as typed or in uppercase. `history` holds at most 10 earlier
turns; each text is limited to 1,000 characters.

The model receives only that owner's parcel and timeline (times converted to
IST), the history and the question, all labelled as untrusted data. Its rules
say to use only those records, reply in English, Hindi or Kannada, decline
unrelated requests, and never act on instructions inside the data. With no
tracking ID it is sent no records and asks for one. An unknown tracking ID
returns 404 without calling Gemini. Gemini timeouts return 504, upstream quota
limits return 429, and other upstream or empty responses return 502 without
exposing provider details. The response data is `{"answer", "tracking_id",
"parcel", "timeline"}`.

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

## Phase 4: Webhooks

The webhook API uses write scope for registration/deletion and read scope for
listing:

- `POST /api/v1/webhooks` with `{"url":"https://..."}` registers an endpoint.
  The response includes a signing secret once.
- `GET /api/v1/webhooks` lists only the authenticated owner's endpoints and
  never returns their secrets.
- `DELETE /api/v1/webhooks/{id}` deletes only an endpoint owned by the
  authenticated API key's owner.

Registration accepts only HTTPS URLs that resolve exclusively to public IP
addresses; localhost, private/link-local/reserved addresses, credentials in
URLs, and URL fragments are rejected. Delivery rechecks the destination, uses
HTTPX with redirects disabled and a two-second timeout, and logs each attempt
(up to three). Status updates wait for delivery, but a delivery or logging
failure does not fail the parcel status response.

The registration response includes a randomly generated signing secret once.
Store it privately; list responses never include it. Requests use the exact
UTF-8 JSON body sent over the wire and the header
`X-Signature: sha256=<lowercase-hex-HMAC-SHA256>`.

### Test with webhook.site

1. Open [webhook.site](https://webhook.site/) and copy the unique HTTPS URL it
   gives you.
2. With the app running locally and Phase 2 test keys provisioned, run these
   commands in PowerShell. Replace the webhook URL placeholder with your own
   webhook.site URL. Key values and the one-time webhook secret are kept in
   variables and are not printed.

```powershell
$readKey = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_READ_API_KEY', ''))"
$writeKey = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_WRITE_API_KEY', ''))"
$webhookSiteUrl = 'https://webhook.site/REPLACE-WITH-YOUR-UNIQUE-ID'
$baseUrl = 'http://127.0.0.1:8000'

$registration = Invoke-RestMethod -Uri "$baseUrl/api/v1/webhooks" -Method POST -Headers @{ 'x-api-key' = $writeKey } -ContentType 'application/json' -Body (@{ url = $webhookSiteUrl } | ConvertTo-Json -Compress)
$webhookId = $registration.data.id
$webhookSecret = $registration.data.secret

$trackingId = 'WEBHOOK-TEST-' + [guid]::NewGuid().ToString('N')
$null = Invoke-RestMethod -Uri "$baseUrl/api/v1/parcels" -Method POST -Headers @{ 'x-api-key' = $writeKey } -ContentType 'application/json' -Body (@{ tracking_id = $trackingId; destination = 'Webhook test' } | ConvertTo-Json -Compress)
$null = Invoke-RestMethod -Uri "$baseUrl/api/v1/parcels/$trackingId/status" -Method PATCH -Headers @{ 'x-api-key' = $writeKey } -ContentType 'application/json' -Body (@{ status = 'picked_up'; location = 'Test facility' } | ConvertTo-Json -Compress)
```

3. In webhook.site, open the received request and copy its **raw request body**
   and `X-Signature` header. Verify them in the same PowerShell window:

```powershell
$rawBody = Read-Host 'Paste the exact raw request body'
$receivedSignature = Read-Host 'Paste the X-Signature value'
$hmac = [System.Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($webhookSecret))
$digest = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($rawBody))
$expectedSignature = 'sha256=' + [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
$hmac.Dispose()
if ($expectedSignature -cne $receivedSignature) { throw 'Webhook signature did not match.' }
Write-Output 'Webhook signature verified.'

$null = Invoke-RestMethod -Uri "$baseUrl/api/v1/webhooks/$webhookId" -Method DELETE -Headers @{ 'x-api-key' = $writeKey }
Remove-Variable readKey, writeKey, webhookSecret
```

## Web interface and admin

Set `ADMIN_USERNAME`, `ADMIN_PASSWORD` and `SESSION_SECRET` in ignored
`.env.local`. The public
tracking page is at `/`, and the admin login is at `/admin/login`.

- `/` is built around the assistant chat, with the parcel's status, ETA and
  timeline beside it. No API key is needed. Users can type a tracking ID in a
  message or use the search box (`?tracking_id=` also works without
  JavaScript). The browser keeps the conversation and sends the last 10 turns
  to `POST /ui/assistant`, which uses the same logic as the API and returns a
  server-rendered parcel panel so the side panel follows the conversation.
  Requests are rate-limited by client IP.
- `/admin/login` signs in with `ADMIN_USERNAME` and `ADMIN_PASSWORD` (both
  compared in constant time, with one error message for either mistake) and
  uses a signed, HttpOnly, SameSite=Lax session cookie that expires after
  8 hours. Set `COOKIE_SECURE=true` on HTTPS hosting such as Vercel.
- The admin "Estimated arrival" field is entered in IST.
- The home page lists the last 5 parcels found under "Recent on this
  device". They are kept only in that browser's localStorage and are never
  sent to the server.
- `/admin` provides parcel creation/status updates, API-key creation/revocation,
  webhook endpoint status, and recent delivery logs.
- Admin POST forms require a hidden CSRF token tied to the signed session.
- `/docs-guide` provides a small project-specific API and webhook guide; FastAPI
  still provides interactive `/docs`.
- Jinja templates, plain CSS and minimal vanilla JavaScript are served from
  `templates/` and `static/`; no frontend build step is required.

Use a separate browser session or clear cookies before testing admin login.
API keys created in the admin page are displayed once and stored only by hash.

## Deployment

The application entrypoint is `app.py`. Configure the environment variables
listed in `.env.example` in the hosting provider's project settings. Keep the
`templates/` and `static/` directories alongside the application entrypoint.
[`vercel.json`](./vercel.json) explicitly includes both directories in the
Python function bundle because the template and static asset paths are resolved
at runtime.

## Official setup references

- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Set Python version on Vercel](https://vercel.com/docs/functions/runtimes/python/python-version)
- [Gemini API Python quickstart](https://ai.google.dev/gemini-api/docs/quickstart?lang=python)
- [Supabase Python reference](https://supabase.com/docs/reference/python/introduction)
