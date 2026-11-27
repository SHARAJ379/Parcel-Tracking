# Contributing

Thanks for taking a look. This is a small FastAPI project; changes should stay
simple and easy to explain.

## Set up

Follow "Quick start" in the [README](./README.md), then run the tests:

```
python -m pytest -q
```

The tests use a fake Supabase client and mocked Gemini calls, so they need no
real credentials.

## Making a change

1. Open an issue first for anything larger than a small fix.
2. Keep the stack as it is: Python with FastAPI on the backend, and plain
   HTML, CSS and minimal JavaScript on the front.
3. Add or update a test in `tests/test_app.py` for every behaviour change.
4. Never commit `.env.local` or any key. Database changes go in a new file
   under `supabase/migrations/`; do not edit applied migrations.
5. Make sure `python -m pytest -q` passes before opening a pull request.
