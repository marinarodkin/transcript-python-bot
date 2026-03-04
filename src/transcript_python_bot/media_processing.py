from __future__ import annotations

from pathlib import Path


def guess_media_type(filename: str | None, mime_type: str | None) -> str | None:
    if mime_type:
        mime = mime_type.lower()
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"

    name = (filename or "").strip()
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in {
        "mp3",
        "wav",
        "m4a",
        "aac",
        "ogg",
        "oga",
        "opus",
        "flac",
        "webm",
        "mp4",
        "mpeg",
        "mpga",
        "mka",
        "caf",
        "aiff",
        "aif",
    }:
        return "audio"
    if ext in {
        "mp4",
        "mov",
        "mkv",
        "webm",
        "avi",
        "m4v",
        "mpeg",
        "mpg",
        "3gp",
        "3gpp",
        "3g2",
        "wmv",
    }:
        return "video"
    return None


def sanitize_media_filename(name: str | None, *, default: str = "media") -> str:
    raw = (name or "").strip()
    if not raw:
        return default
    # keep filename safe for filesystem; preserve extension
    base = Path(raw).stem
    ext = Path(raw).suffix
    safe_base = "".join(ch for ch in base if ch.isalnum() or ch in {"-", "_", ".", " "}).strip()
    safe_base = safe_base.replace(" ", "_") or default
    return f"{safe_base}{ext}" if ext else safe_base
