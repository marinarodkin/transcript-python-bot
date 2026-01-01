from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from .checkLink import is_valid_youtube_url
from .config import load_notion_config, load_openai_config
from .pipeline import process_youtube_url


def _parse_languages(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [lang.strip() for lang in value.split(",") if lang.strip()]


def _preview(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def main() -> None:
    load_dotenv()
    url = input("Enter YouTube URL: ").strip()

    if not url:
        url = "https://www.youtube.com/watch?v=HWesCaLC9HE"
        print(f"Using test URL: {url}")

    if not is_valid_youtube_url(url):
        print("Invalid YouTube URL.")
        sys.exit(1)

    try:
        languages = _parse_languages(os.getenv("TRANSCRIPT_LANGUAGES"))
        openai = load_openai_config()
        notion = load_notion_config()
        processed = process_youtube_url(url=url, languages=languages, openai=openai, notion=notion)
    except Exception as exc:  # pragma: no cover - surfaced to user
        print(f"Error: {exc}")
        sys.exit(1)

    print("\n=== VIDEO INFO ===\n")
    print(f"video_id: {processed.video_info.video_id}")
    print(f"title: {_preview(processed.video_info.title)}")
    print(f"author: {_preview(processed.video_info.author)}")
    print(f"description: {_preview(processed.video_info.description)}")

    print("\n=== ORIGINAL TRANSCRIPT ===\n")
    print(_preview(processed.handled.original_transcript))
    print("\n=== READABLE TRANSCRIPT ===\n")
    print(_preview(processed.handled.readable_transcript))
    if processed.handled.translation_ru:
        print("\n=== TRANSLATION (RU) ===\n")
        print(_preview(processed.handled.translation_ru))
    print("\n=== STRUCTURED (MARKDOWN) ===\n")
    print(_preview(processed.handled.structured_markdown))
    if processed.handled.structured_translation_markdown:
        print("\n=== STRUCTURED TRANSLATION (MARKDOWN) ===\n")
        print(_preview(processed.handled.structured_translation_markdown))

    print("\n=== NOTION LINKS ===\n")
    if processed.notion_links:
        print(processed.notion_links)
    else:
        print("Notion is disabled or upload failed.")


if __name__ == "__main__":
    main()

