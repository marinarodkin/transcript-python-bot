from __future__ import annotations

import re

from notion_client import Client


def create_summary_page(
    notion_api_key: str,
    database_id: str,
    title: str,
    url: str,
    summary: str,
    title_property: str = "Name",
    url_property: str | None = None,
    summary_property: str | None = None,
) -> str:
    notion = Client(auth=notion_api_key)

    properties: dict = {
        title_property: {
            "title": [{"text": {"content": title}}],
        }
    }
    if url_property:
        properties[url_property] = {"url": url}
    if summary_property:
        properties[summary_property] = {"rich_text": [{"text": {"content": summary}}]}

    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": summary}}]
            },
        }
    ]

    page = notion.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=children,
    )
    return page["id"]


MAX_RICH_TEXT_CHARS = 2000
MAX_CHILDREN_BLOCKS = 99


def split_long_paragraphs(text: str, limit: int = MAX_RICH_TEXT_CHARS) -> list[str]:
    paragraphs = re.split(r"\n+", text)
    result: list[str] = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            result.append(paragraph)
            continue

        sentences = paragraph.split(".")
        new_paragraph = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            candidate = f"{new_paragraph}. {sentence}" if new_paragraph else sentence
            if len(candidate) <= limit:
                new_paragraph = candidate
                continue

            if new_paragraph:
                result.append(new_paragraph)
            new_paragraph = sentence

        if new_paragraph and len(new_paragraph) < int(limit * 0.95):
            result.append(new_paragraph)

    return result


def parse_inline(text: str) -> list[dict]:
    regex = re.compile(r"(\*\*([^*]+)\*\*|\*([^*]+)\*)")
    fragments: list[dict] = []
    last_index = 0

    for match in regex.finditer(text):
        if match.start() > last_index:
            fragments.append(
                {
                    "type": "text",
                    "text": {"content": text[last_index : match.start()]},
                }
            )

        if match.group(2):
            fragments.append(
                {
                    "type": "text",
                    "text": {"content": match.group(2)},
                    "annotations": {"bold": True},
                }
            )
        elif match.group(3):
            fragments.append(
                {
                    "type": "text",
                    "text": {"content": match.group(3)},
                    "annotations": {"italic": True},
                }
            )

        last_index = match.end()

    if last_index < len(text):
        fragments.append(
            {
                "type": "text",
                "text": {"content": text[last_index:]},
            }
        )

    return fragments


def convert_markdown_to_blocks(markdown: str) -> list[dict]:
    blocks: list[dict] = []
    lines = markdown.split("\n")

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("### "):
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": parse_inline(line[4:])},
                }
            )
        elif line.startswith("## "):
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": parse_inline(line[3:])},
                }
            )
        else:
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": parse_inline(line)},
                }
            )

    return blocks


def _markdown_to_children_blocks(markdown: str) -> list[dict]:
    parts = split_long_paragraphs(markdown)
    children_blocks: list[dict] = []

    for part in parts:
        for block in convert_markdown_to_blocks(part):
            if len(children_blocks) >= MAX_CHILDREN_BLOCKS:
                return children_blocks
            children_blocks.append(block)

    return children_blocks


def send_markdown_to_notion(
    *,
    notion_api_key: str,
    database_id: str,
    title: str,
    markdown: str,
    link: str,
    title_property: str = "title",
    link_property: str = "link",
) -> dict:
    notion = Client(auth=notion_api_key)
    children_blocks = _markdown_to_children_blocks(markdown)

    properties: dict = {
        title_property: {"title": [{"text": {"content": title}}]},
    }
    if link:
        properties[link_property] = {"url": link}

    page = notion.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=children_blocks,
    )
    return {"id": page["id"], "url": page.get("url", "")}
