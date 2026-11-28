"""Shared Gemini call: optional fallback model, otherwise one retry.

Google sometimes answers with 503 ("model is currently experiencing high
demand") or 504 (deadline exceeded), and one model can be overloaded while
another is fine. If GEMINI_FALLBACK_MODEL is set, a request that fails on the
main model with 500, 503, 504 or a client timeout is sent once to the
fallback model, immediately. Quota (429) and request errors (400, 404, ...)
are not retried: another model would not fix them.

If GEMINI_FALLBACK_MODEL is not set, the original behaviour applies: a 500 or
503 is repeated once on the same model after a short pause, and a 504 or
timeout is not repeated.

Time is bounded: every call has the client timeout (15 s) and there are at
most two calls, so a request waits at most 2 x 15 s = 30 s with a fallback,
or 15 s + 1.5 s + 15 s without one. vercel.json sets maxDuration to 60 s, and
a test keeps that above the worst case.
"""

import logging
import os
import time

import httpx2
from google import genai
from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)

# Statuses that mean "this model is busy or too slow right now".
BUSY_STATUS_CODES = {500, 503, 504}
# Without a fallback only 500 and 503 are repeated (a 504 already waited the
# full timeout, so repeating it would make the user wait twice as long).
RETRY_STATUS_CODES = {500, 503}
RETRY_DELAY_SECONDS = 1.5


def get_fallback_model(main_model: str) -> str | None:
    """The fallback model from GEMINI_FALLBACK_MODEL, if set and different."""
    fallback = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()
    return fallback if fallback and fallback != main_model else None


def _is_busy(exc: Exception) -> bool:
    if isinstance(exc, httpx2.TimeoutException):
        return True
    return isinstance(exc, genai_errors.APIError) and exc.code in BUSY_STATUS_CODES


def generate_with_fallback(gemini_client: genai.Client, *, model: str, **request):
    """Call generate_content on `model`, using the fallback model if needed.

    Raises the last error, so callers keep handling timeouts and APIError as
    before.
    """
    fallback = get_fallback_model(model)
    try:
        return gemini_client.models.generate_content(model=model, **request)
    except (genai_errors.APIError, httpx2.TimeoutException) as exc:
        if fallback and _is_busy(exc):
            logger.warning(
                "Gemini model %s failed (%s); trying fallback model %s.",
                model,
                getattr(exc, "code", "timeout"),
                fallback,
            )
            return gemini_client.models.generate_content(model=fallback, **request)
        if not fallback and getattr(exc, "code", None) in RETRY_STATUS_CODES:
            logger.warning("Gemini returned HTTP %s; retrying once.", exc.code)
            time.sleep(RETRY_DELAY_SECONDS)
            return gemini_client.models.generate_content(model=model, **request)
        raise
