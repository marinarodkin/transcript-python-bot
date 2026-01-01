from __future__ import annotations

import json
import logging
import re
from html import unescape
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from langchain_community.document_loaders import YoutubeLoader

from .checkLink import extract_video_id, normalize_youtube_url


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    author: str
    description: str


def _fetch_oembed_title_author(canonical_url: str) -> tuple[str, str]:
    query = urlencode({"url": canonical_url, "format": "json"})
    oembed_url = f"https://www.youtube.com/oembed?{query}"
    req = Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=15) as resp:  # nosec - user-provided URL is normalized
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    title = str(data.get("title") or "").strip()
    author = str(data.get("author_name") or "").strip()
    return title, author


def _fetch_description_from_html(canonical_url: str) -> str:
    req = Request(canonical_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=15) as resp:  # nosec - user-provided URL is normalized
        html = resp.read().decode("utf-8", errors="replace")

    match = re.search(r'property="og:description"\s+content="([^"]*)"', html)
    if match:
        return unescape(match.group(1)).strip()

    match = re.search(r'"shortDescription":"(.*?)"', html)
    if match:
        raw = match.group(1)
        raw = raw.encode("utf-8", "backslashreplace").decode("unicode_escape", errors="ignore")
        return unescape(raw).replace("\\n", "\n").strip()

    return ""


def fetch_video_info(url: str, languages: list[str] | None = None) -> VideoInfo:
    video_id = extract_video_id(url)
    canonical_url = normalize_youtube_url(url)
    title = author = description = ""
    loader_error: Exception | None = None
    oembed_error: Exception | None = None
    html_error: Exception | None = None

    try:
        loader = YoutubeLoader.from_youtube_url(
            canonical_url,
            add_video_info=True,
            language=languages,
        )
        docs = loader.load()
        metadata = docs[0].metadata if docs else {}
        if not isinstance(metadata, dict):
            metadata = {}

        title = str(metadata.get("title") or metadata.get("video_title") or "").strip()
        author = str(metadata.get("author") or metadata.get("channel") or metadata.get("creator") or "").strip()
        description = str(metadata.get("description") or metadata.get("video_description") or "").strip()
    except Exception as exc:
        loader_error = exc
        logger.debug("YoutubeLoader video info failed url=%s", canonical_url, exc_info=True)

    if not title or not author:
        try:
            oembed_title, oembed_author = _fetch_oembed_title_author(canonical_url)
            title = title or oembed_title
            author = author or oembed_author
        except Exception as exc:
            oembed_error = exc
            logger.debug("oEmbed video info failed url=%s", canonical_url, exc_info=True)

    if not description:
        try:
            description = _fetch_description_from_html(canonical_url)
        except Exception as exc:
            html_error = exc
            logger.debug("HTML description fetch failed url=%s", canonical_url, exc_info=True)

    if not title and not author and not description:
        logger.warning(
            "video info empty url=%s errors=%s",
            canonical_url,
            {
                "youtube_loader": type(loader_error).__name__ if loader_error else None,
                "oembed": type(oembed_error).__name__ if oembed_error else None,
                "html": type(html_error).__name__ if html_error else None,
            },
        )

    return VideoInfo(
        video_id=video_id,
        title=title,
        author=author,
        description=description,
    )
