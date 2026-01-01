from __future__ import annotations

import sys
import os
from pathlib import Path

from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parent / "src"))

from transcript_python_bot.checkLink import is_valid_youtube_url  # noqa: E402
from transcript_python_bot.get_transcript import fetch_transcript  # noqa: E402
from transcript_python_bot.notion import send_markdown_to_notion  # noqa: E402
from transcript_python_bot.transcript_handler import handle_transcript  # noqa: E402
from transcript_python_bot.video_info import fetch_video_info  # noqa: E402


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

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    prompt_path = Path(os.getenv("PROMPT_PATH", "prompts/transcript_prompts.yaml"))
    chunk_size = int(os.getenv("CHUNK_SIZE", "30000"))
    if not openai_api_key:
        print("Missing OPENAI_API_KEY in .env")
        sys.exit(1)

    try:
        languages = _parse_languages(os.getenv("TRANSCRIPT_LANGUAGES"))
        video_info = fetch_video_info(url, languages=languages)
        result = fetch_transcript(url, languages=languages)
    except Exception as exc:  # pragma: no cover - surfaced to user
        print(f"Error: {exc}")
        sys.exit(1)

    print("\n=== VIDEO INFO ===\n")
    print(f"video_id: {video_info.video_id}")
    print(f"title: {_preview(video_info.title)}")
    print(f"author: {_preview(video_info.author)}")
    print(f"description: {_preview(video_info.description)}")

    try:
        handled = handle_transcript(
            result.transcript_text,
            api_key=openai_api_key,
            model=openai_model,
            prompt_path=prompt_path,
            chunk_size=chunk_size,
        )
    except Exception as exc:  # pragma: no cover - surfaced to user
        print(f"Error: {exc}")
        sys.exit(1)

    print("\n=== ORIGINAL TRANSCRIPT ===\n")
    print(_preview(handled.original_transcript))
    print("\n=== READABLE TRANSCRIPT ===\n")
    print(_preview(handled.readable_transcript))
    if handled.translation_ru:
        print("\n=== TRANSLATION (RU) ===\n")
        print(_preview(handled.translation_ru))
    print("\n=== STRUCTURED (MARKDOWN) ===\n")
    print(_preview(handled.structured_markdown))
    if handled.structured_translation_markdown:
        print("\n=== STRUCTURED TRANSLATION (MARKDOWN) ===\n")
        print(_preview(handled.structured_translation_markdown))

    notion_api_key = os.getenv("NOTION_API_KEY", "").strip()
    notion_database_id = os.getenv("NOTION_DATABASE_ID", "").strip()
    notion_title_property = os.getenv("NOTION_TITLE_PROPERTY", "title").strip() or "title"
    notion_link_property = os.getenv("NOTION_LINK_PROPERTY", "link").strip() or "link"

    notion_links: dict[str, str] = {}
    if notion_api_key and notion_database_id:
        base_title = video_info.title or f"YouTube {result.video_id}"
        variants: list[tuple[str, str]] = [
            ("original_transcript", handled.original_transcript),
            ("readable_transcript", handled.readable_transcript),
            ("structured_markdown", handled.structured_markdown),
        ]
        if handled.translation_ru:
            variants.append(("translation_ru", handled.translation_ru))
        if handled.structured_translation_markdown:
            variants.append(("structured_translation_markdown", handled.structured_translation_markdown))

        for key, text in variants:
            page = send_markdown_to_notion(
                notion_api_key=notion_api_key,
                database_id=notion_database_id,
                title=f"{base_title} — {key}",
                markdown=text,
                link=url,
                title_property=notion_title_property,
                link_property=notion_link_property,
            )
            if page.get("url"):
                notion_links[key] = page["url"]
    else:
        print("\nNotion is disabled (missing NOTION_API_KEY or NOTION_DATABASE_ID).")

    print("\n=== NOTION LINKS ===\n")
    print(notion_links)


if __name__ == "__main__":
    main()
