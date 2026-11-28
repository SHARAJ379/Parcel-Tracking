import pytest


@pytest.fixture(autouse=True)
def no_fallback_model_from_env_file(monkeypatch):
    """app.py loads .env.local when it is imported. If that file sets
    GEMINI_FALLBACK_MODEL, tests would silently change behaviour, so every
    test starts without it. Fallback tests set it themselves."""
    monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
