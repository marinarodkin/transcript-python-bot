from __future__ import annotations

import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig

import requests
import urllib3

from .checkLink import extract_video_id


print("[get_transcript] module imported")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

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


def _parse_locations(value: str | None) -> list[str]:
    if not value:
        return ["de", "nl", "pl"]
    return [x.strip().lower() for x in value.split(",") if x.strip()]


def _join_snippets(snippets: Iterable) -> str:
    parts: list[str] = []
    for item in snippets:
        text = getattr(item, "text", "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# YouTube API (always proxy, lazy init)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _ytt() -> YouTubeTranscriptApi:
    print("[get_transcript] creating YouTubeTranscriptApi with Webshare proxy")

    username = _require_env("WEBSHARE_PROXY_USERNAME")
    password = _require_env("WEBSHARE_PROXY_PASSWORD")
    locations = _parse_locations(os.getenv("WEBSHARE_PROXY_LOCATIONS"))

    print(
        "[get_transcript] proxy config:",
        f"username={'***' if username else 'NONE'}",
        f"locations={locations}",
    )

    api = YouTubeTranscriptApi(
        proxy_config=WebshareProxyConfig(
            proxy_username=username,
            proxy_password=password,
            filter_ip_locations=locations,
        )
    )

    print("[get_transcript] YouTubeTranscriptApi created")
    return api


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
    preferred_languages = languages or ["ru", "en"]

    print("[get_transcript] video_id:", video_id)
    print("[get_transcript] preferred languages:", preferred_languages)

    transcript_list = _ytt().list(video_id)
    print("[get_transcript] transcript_list received")

    print("[get_transcript] start find_transcript")
    transcript = transcript_list.find_transcript(preferred_languages)
    print("[get_transcript] transcript selected:", transcript.language_code)

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
