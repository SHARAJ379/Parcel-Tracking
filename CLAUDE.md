# Parcel and Delivery Tracking Assistant

Individual university GenAI assignment. All work and reviews must be finished
before **4 December 2026**.

## Requirements

- Python backend and logic with FastAPI; HTML, CSS and minimal JavaScript only.
- Gemini API integrated directly, configured by `GEMINI_API_KEY` and `GEMINI_MODEL`.
- The chatbot is the main feature.
- Faculty also require: free deployment on Vercel, API endpoints for other apps,
  webhooks with proper JSON and status codes, API key authentication, API
  documentation, and secrets kept in env files.

## Chatbot goals

- Centrepiece of the home page, with parcel status and timeline beside it.
- Multi-turn with follow-ups; accepts a tracking ID inside a message.
- Answers parcel facts only from database records, never invents them.
- Replies in English, Hindi or Kannada; declines unrelated requests.
- Read-only, resistant to prompt injection, clean quota and timeout errors.
- Also usable by other apps through `POST /api/v1/assistant`.

## UI

Clean, professional logistics tool. Responsive. No gradients, emoji icons or
marketing filler.

## Rules

- Never print or commit values from `.env.local`, and do not open it unless the
  user agrees.
- Keep code simple and explain it; the user must defend it in a review.
- Do not reset or reseed the database. Give the user any SQL to run themselves.
- Do not push to any remote or deploy without asking.
- Do not create, prepare or push any pull request, and do not push to any
  remote, unless the user asks. The user will say when all 3 faculty reviews
  are done; this replaces the earlier plan to prepare a PR for
  kamathn-blr/GenAI-CIAP-2025Batch after faculty approval.
- Stop any server you start.

## Project layout

- `app.py` — FastAPI app: pages, admin, `/api/v1` routes, Gemini assistant.
- `webhooks.py` — webhook URL validation, signed delivery, retries, logging.
- `templates/`, `static/` — Jinja pages, CSS, small JS for chat and copy buttons.
- `supabase/migrations/` — SQL the user runs in the Supabase SQL Editor.
- `tests/test_app.py` — pytest suite with a fake Supabase client; run with
  `.venv\Scripts\python.exe -m pytest -q`.
