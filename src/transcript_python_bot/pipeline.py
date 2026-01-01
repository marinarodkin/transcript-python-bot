from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .get_transcript import fetch_transcript
from .notion import send_markdown_to_notion
from .transcript_handler import HandlerResult, handle_transcript
from .video_info import VideoInfo, fetch_video_info


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    prompt_path: Path
    chunk_size: int


@dataclass(frozen=True)
class NotionConfig:
    api_key: str
    database_id: str
    title_property: str
    link_property: str


@dataclass(frozen=True)
class ProcessedYouTube:
    url: str
    video_info: VideoInfo
    transcript_text: str
    handled: HandlerResult
    notion_links: dict[str, str]


def _upload_variants_to_notion(
    *,
    notion: NotionConfig,
    url: str,
    base_title: str,
    handled: HandlerResult,
) -> dict[str, str]:
    variants: list[tuple[str, str]] = [
        ("original_transcript", handled.original_transcript),
        ("structured_markdown", handled.structured_markdown),
        ("readable_transcript", handled.readable_transcript),
    ]
    if handled.translation_ru:
        variants.append(("translation_ru", handled.translation_ru))
    if handled.structured_translation_markdown:
        variants.append(("structured_translation_markdown", handled.structured_translation_markdown))

    links: dict[str, str] = {}
    for key, text in variants:
        page = send_markdown_to_notion(
            notion_api_key=notion.api_key,
            database_id=notion.database_id,
            title=f"{base_title} — {key}",
            markdown=text,
            link=url,
            title_property=notion.title_property,
            link_property=notion.link_property,
        )
        if page.get("url"):
            links[key] = page["url"]
    return links


def process_youtube_url(
    *,
    url: str,
    languages: list[str] | None,
    openai: OpenAIConfig,
    notion: NotionConfig | None,
) -> ProcessedYouTube:
    video_info = fetch_video_info(url, languages=languages)
    transcript = fetch_transcript(url, languages=languages).transcript_text

    handled = handle_transcript(
        transcript,
        api_key=openai.api_key,
        model=openai.model,
        prompt_path=openai.prompt_path,
        chunk_size=openai.chunk_size,
    )

    notion_links: dict[str, str] = {}
    if notion:
        base_title = video_info.title or f"YouTube {video_info.video_id}"
        notion_links = _upload_variants_to_notion(
            notion=notion,
            url=url,
            base_title=base_title,
            handled=handled,
        )

    return ProcessedYouTube(
        url=url,
        video_info=video_info,
        transcript_text=transcript,
        handled=handled,
        notion_links=notion_links,
    )


@dataclass(frozen=True)
class ProcessedText:
    title: str
    text: str
    handled: HandlerResult
    notion_links: dict[str, str]


def process_plain_text(
    *,
    title: str,
    text: str,
    openai: OpenAIConfig,
    notion: NotionConfig | None,
) -> ProcessedText:
    handled = handle_transcript(
        text,
        api_key=openai.api_key,
        model=openai.model,
        prompt_path=openai.prompt_path,
        chunk_size=openai.chunk_size,
    )

    notion_links: dict[str, str] = {}
    if notion:
        notion_links = _upload_variants_to_notion(
            notion=notion,
            url="",
            base_title=title,
            handled=handled,
        )

    return ProcessedText(title=title, text=text, handled=handled, notion_links=notion_links)
