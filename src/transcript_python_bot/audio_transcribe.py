from __future__ import annotations

import logging
from pathlib import Path

from openai import APIError, OpenAI, RateLimitError

from .transcript_handler import OpenAIQuotaError, OpenAIRateLimitError


logger = logging.getLogger(__name__)


def transcribe_audio_file(
    path: Path,
    *,
    api_key: str,
    model: str,
    response_format: str = "text",
) -> str:
    client = OpenAI(api_key=api_key)
    with path.open("rb") as audio_file:
        try:
            result = client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format=response_format,
            )
        except RateLimitError as exc:
            body = getattr(exc, "body", None)
            error_code = getattr(exc, "code", None)
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    error_code = error.get("code") or error_code
            if error_code == "insufficient_quota":
                raise OpenAIQuotaError("OpenAI quota exceeded") from exc
            raise OpenAIRateLimitError("OpenAI rate limit exceeded") from exc
        except APIError as exc:
            logger.exception("OpenAI API error status=%s", getattr(exc, "status_code", None))
            raise

    if isinstance(result, str):
        text = result
    else:
        text = getattr(result, "text", "") or ""
    return text.strip()
