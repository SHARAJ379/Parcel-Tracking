# Parcel and Delivery Tracking Assistant

A Python/FastAPI university assignment project. The app will provide simulated
parcel tracking, a Gemini assistant grounded in parcel records, authenticated
API endpoints, signed webhooks, and a small admin interface. It does not connect
to real courier services.

Development is being completed in phases. This initial commit is Phase 1: the
project scaffold and local setup. Features, Supabase integration, deployment
configuration, and their tests will be added in later phases.

## Requirements

- Python 3.12 or newer
- PowerShell on Windows
- A Supabase project and Gemini API key for the later integration phases

## Windows PowerShell setup

Run these commands from the project folder. Use Python 3.12 or newer (the
current local environment uses Python 3.14):

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

Fill in `.env.local` when the service integrations are implemented. Keep real
credentials out of source control; `.env.local` is ignored by Git.

## Run and test

```powershell
python -m uvicorn app:app --reload
```

Open `http://127.0.0.1:8000/health` to see the scaffold health response and
`http://127.0.0.1:8000/docs` for FastAPI's generated API documentation.

In another activated PowerShell terminal:

```powershell
python -m pytest
```

## Deployment

The application entrypoint is `app.py`, as recognized by Vercel's Python
runtime. Deployment instructions and environment variable configuration will
be added after the application features are implemented.

## Official setup references

- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Gemini API Python quickstart](https://ai.google.dev/gemini-api/docs/quickstart?lang=python)
- [Supabase Python reference](https://supabase.com/docs/reference/python/introduction)
