from __future__ import annotations

import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable
from urllib.parse import quote

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound

import requests
import urllib3

from .checkLink import extract_video_id


print("[get_transcript] module imported")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class NoSupportedTranscriptFound(RuntimeError):
    def __init__(self, *, video_id: str, tried_languages: list[str]):
        super().__init__(
            f"No transcript found for video_id={video_id} in languages={tried_languages}"
        )
        self.video_id = video_id
        self.tried_languages = tried_languages


@dataclass(frozen=True)
class TranscriptResult:
    video_id: str
    transcript_text: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"[get_transcript] Missing required env variable: {name}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"[get_transcript] Invalid boolean env {name}={raw!r}")


def _webshare_proxy_url() -> str:
    username = _require_env("WEBSHARE_PROXY_USERNAME")
    password = _require_env("WEBSHARE_PROXY_PASSWORD")
    safe_username = quote(username, safe="")
    safe_password = quote(password, safe="")
    return f"http://{safe_username}:{safe_password}@p.webshare.io:80"


def _join_snippets(snippets: Iterable) -> str:
    parts: list[str] = []
    for item in snippets:
        text = getattr(item, "text", "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# YouTube API (lazy init)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _ytt() -> YouTubeTranscriptApi:
    proxy_url = _webshare_proxy_url()
    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url
    print("[get_transcript] creating YouTubeTranscriptApi proxy_mode=env_http_https")
    return YouTubeTranscriptApi()


def _find_transcript(transcript_list, language_codes: list[str]):
    for language_code in language_codes:
        try:
            transcript = transcript_list.find_transcript([language_code])
            print("[get_transcript] transcript selected:", transcript.language_code)
            return transcript
        except NoTranscriptFound:
            continue
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_transcript(
    url: str,
    languages: list[str] | None = None,
    *,
    max_attempts: int = 3,
    base_delay_sec: int = 5,
) -> TranscriptResult:
    print("[get_transcript] fetch_transcript called")
    print("[get_transcript] raw url:", url)

    video_id = extract_video_id(url)
    preferred_languages = languages or ["ru"]
    supported_fallback_languages = list(
        dict.fromkeys([*preferred_languages, "ru", "en", "de"])
    )

    print("[get_transcript] video_id:", video_id)
    print("[get_transcript] preferred languages:", preferred_languages)

    use_webshare_proxy = _env_bool("USE_WEBSHARE_PROXY", True)
    fallback_to_direct = _env_bool("FALLBACK_TO_DIRECT_ON_PROXY_ERROR", False)
    if not use_webshare_proxy:
        raise RuntimeError(
            "[get_transcript] USE_WEBSHARE_PROXY=false is not supported in this deployment"
        )
    if fallback_to_direct:
        raise RuntimeError(
            "[get_transcript] FALLBACK_TO_DIRECT_ON_PROXY_ERROR=true is disabled in proxy-only mode"
        )
    print(
        "[get_transcript] mode:",
        "proxy_mode=env_http_https",
        "fallback_to_direct=false",
    )

    transcript_list = _ytt().list(video_id)
    print("[get_transcript] transcript_list received")

    print("[get_transcript] start find_transcript")
    transcript = _find_transcript(transcript_list, supported_fallback_languages)
    if transcript is None:
        raise NoSupportedTranscriptFound(video_id=video_id, tried_languages=supported_fallback_languages)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[get_transcript] fetch attempt {attempt}/{max_attempts}")
            items = transcript.fetch()
            print(f"[get_transcript] fetched {len(items)} snippets")

            transcript_text = _join_snippets(items)

            if not transcript_text:
                raise ValueError("Transcript is empty")

            print(
                "[get_transcript] transcript success, chars:",
                len(transcript_text),
            )

            return TranscriptResult(
                video_id=video_id,
                transcript_text=transcript_text,
            )

        except (
            requests.exceptions.RetryError,
            urllib3.exceptions.MaxRetryError,
        ) as exc:
            last_error = exc
            print(
                f"[get_transcript] rate limited (429-like) on attempt {attempt}"
            )

            if attempt >= max_attempts:
                break

            delay = base_delay_sec * attempt
            print(f"[get_transcript] sleeping {delay}s before retry")
            time.sleep(delay)

        except Exception as exc:
            print(
                "[get_transcript] unrecoverable error:",
                type(exc).__name__,
                exc,
            )
            raise

    print("[get_transcript] FAILED after retries")
    raise RuntimeError(
        f"YouTube rate limited after {max_attempts} attempts"
    ) from last_error
