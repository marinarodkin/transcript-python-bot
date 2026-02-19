from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .pipeline import NotionConfig, OpenAIConfig


@dataclass(frozen=True)
class RuntimeLimits:
    queue_maxsize: int
    max_text_file_bytes: int
    max_text_chars: int
    max_media_file_bytes: int
    max_media_duration_sec: int
    max_audio_bytes: int


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


def load_openai_config() -> OpenAIConfig:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in environment")
    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    audio_model = (os.getenv("OPENAI_AUDIO_MODEL") or "whisper-1").strip() or "whisper-1"
    prompt_path = Path((os.getenv("PROMPT_PATH") or "prompts/transcript_prompts.yaml").strip())
    chunk_size = _env_int("CHUNK_SIZE", 30_000)
    return OpenAIConfig(
        api_key=api_key,
        model=model,
        audio_model=audio_model,
        prompt_path=prompt_path,
        chunk_size=chunk_size,
    )


def load_notion_config() -> NotionConfig | None:
    api_key = (os.getenv("NOTION_API_KEY") or "").strip()
    database_id = (os.getenv("NOTION_DATABASE_ID") or "").strip()
    if not api_key or not database_id:
        return None
    title_property = (os.getenv("NOTION_TITLE_PROPERTY") or "title").strip() or "title"
    link_property = (os.getenv("NOTION_LINK_PROPERTY") or "link").strip() or "link"
    return NotionConfig(
        api_key=api_key,
        database_id=database_id,
        title_property=title_property,
        link_property=link_property,
    )


def load_runtime_limits() -> RuntimeLimits:
    return RuntimeLimits(
        queue_maxsize=_env_int("QUEUE_MAXSIZE", 20),
        max_text_file_bytes=_env_int("MAX_TEXT_FILE_BYTES", 1_000_000),
        max_text_chars=_env_int("MAX_TEXT_CHARS", 1_000_000),
        max_media_file_bytes=_env_int("MAX_MEDIA_FILE_BYTES", 50_000_000),
        max_media_duration_sec=_env_int("MAX_MEDIA_DURATION_SEC", 3600),
        max_audio_bytes=_env_int("MAX_AUDIO_BYTES", 24_000_000),
    )
