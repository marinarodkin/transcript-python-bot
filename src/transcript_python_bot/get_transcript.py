from __future__ import annotations

from dataclasses import dataclass

from langchain_community.document_loaders import YoutubeLoader

from .checkLink import extract_video_id, normalize_youtube_url


@dataclass(frozen=True)
class TranscriptResult:
    video_id: str
    transcript_text: str


def fetch_transcript(url: str, languages: list[str] | None = None) -> TranscriptResult:
    video_id = extract_video_id(url)
    canonical_url = normalize_youtube_url(url)
    loader = YoutubeLoader.from_youtube_url(
        canonical_url,
        add_video_info=False,
        language=languages,
    )
    docs = loader.load()
    transcript_text = "\n\n".join(doc.page_content for doc in docs).strip()
    if not transcript_text:
        raise ValueError("Transcript is empty")
    return TranscriptResult(video_id=video_id, transcript_text=transcript_text)
