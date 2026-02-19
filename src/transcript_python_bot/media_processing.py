from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable


AUDIO_EXTENSIONS = {
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
}

VIDEO_EXTENSIONS = {
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
}


def guess_media_type(filename: str | None, mime_type: str | None) -> str | None:
    if mime_type:
        mime = mime_type.lower()
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("video/"):
            return "video"

    name = (filename or "").strip()
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
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


def probe_duration_seconds(path: Path) -> float | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nk=1:nw=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except Exception:
        return None
    value = (result.stdout or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _run_ffmpeg(args: Iterable[str]) -> None:
    result = subprocess.run(list(args), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed")


def convert_to_audio(
    input_path: Path,
    output_path: Path,
    *,
    speed: float = 2.0,
    bitrate_kbps: int = 64,
    sample_rate: int = 16000,
    channels: int = 1,
) -> None:
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-b:a",
        f"{bitrate_kbps}k",
        "-filter:a",
        f"atempo={speed}",
        str(output_path),
    ]
    _run_ffmpeg(args)


def convert_to_audio_with_size_limit(
    input_path: Path,
    output_path: Path,
    *,
    max_bytes: int,
    speed: float = 2.0,
    bitrate_steps: Iterable[int] = (64, 48, 32),
    sample_rate: int = 16000,
    channels: int = 1,
) -> int:
    last_error: Exception | None = None
    for bitrate in bitrate_steps:
        try:
            if output_path.exists():
                output_path.unlink()
            convert_to_audio(
                input_path,
                output_path,
                speed=speed,
                bitrate_kbps=bitrate,
                sample_rate=sample_rate,
                channels=channels,
            )
            if output_path.stat().st_size <= max_bytes:
                return bitrate
        except Exception as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error
    raise ValueError("Converted audio exceeds size limit")
