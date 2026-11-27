# Testing

## Unit tests

They use a fake Supabase client and mocked Gemini calls, so they need no
credentials and no running server:

```powershell
python -m pytest -q
```

## API integration checks

These need a running server, the migration applied and test keys created (see
[database.md](./database.md)). Use the same low limit in both PowerShell
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

The script checks success, 401, 403, 404 and 429 responses, plus parcel
creation and status updates. It shows each rate-limit probe and creates a
uniquely named test parcel. The low limit affects only the test server; do not
lower the production default to make this test pass.

## Live assistant check

Start the app (`python -m uvicorn app:app --reload`), then in another window:

```powershell
.\tests\phase3-assistant.ps1
```

The script checks missing-key, unknown-parcel and question-length errors, then
asks Gemini about the seeded `DEMO-PARCEL-001` parcel. It reads the test key
from `.env.local` without printing it.

To test webhooks end to end, see [webhook-testing.md](./webhook-testing.md).
