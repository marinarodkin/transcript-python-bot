from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "youtube.com" in host:
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/shorts/")[-1].split("/")[0]
        if parsed.path == "/watch":
            params = parse_qs(parsed.query)
            video_ids = params.get("v")
            if video_ids:
                return video_ids[0]
        if parsed.path.startswith("/live/"):
            return parsed.path.split("/live/")[-1].split("/")[0]
    if "youtu.be" in host:
        return parsed.path.lstrip("/").split("/")[0]
    raise ValueError("Unsupported YouTube URL format")


def is_valid_youtube_url(url: str) -> bool:
    try:
        extract_video_id(url)
    except ValueError:
        return False
    return True


def normalize_youtube_url(url: str) -> str:
    video_id = extract_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}"
