"""Shared Gemini call with one retry for temporary overloads.

During the live review Google answered some requests with 503 ("model is
currently experiencing high demand"). Those usually pass a moment later, so
the call is repeated once. A 504 (deadline exceeded) is not repeated: it has
already used the full timeout, and a second wait would make the user wait
twice as long.
"""

import logging
import time

from google import genai
from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)

RETRY_STATUS_CODES = {500, 503}
RETRY_DELAY_SECONDS = 1.5


def generate_with_retry(gemini_client: genai.Client, **request):
    try:
        return gemini_client.models.generate_content(**request)
    except genai_errors.APIError as exc:
        if exc.code not in RETRY_STATUS_CODES:
            raise
        logger.warning("Gemini returned HTTP %s; retrying once.", exc.code)
        time.sleep(RETRY_DELAY_SECONDS)
        return gemini_client.models.generate_content(**request)
