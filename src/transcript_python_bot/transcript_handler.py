from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from langdetect import detect
from openai import OpenAI
from openai import APIError, RateLimitError


logger = logging.getLogger(__name__)


class OpenAIRateLimitError(RuntimeError):
    pass


class OpenAIQuotaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Prompts:
    readability_system: str
    readability_user: str
    translation_system: str
    translation_user: str
    structure_system: str
    structure_user: str


def load_prompts(prompt_path: Path) -> Prompts:
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Prompt file must be a YAML mapping")

    prompts_root = data.get("prompts")
    if not isinstance(prompts_root, dict):
        raise ValueError("Prompt file missing top-level 'prompts' mapping")

    def _get(section: str, key: str) -> str:
        section_obj = prompts_root.get(section)
        if not isinstance(section_obj, dict):
            raise ValueError(f"Prompt file missing prompts.{section}")

        current = section_obj.get("current", "v1")
        versions = section_obj.get("versions")
        if not isinstance(versions, dict):
            raise ValueError(f"Prompt file missing prompts.{section}.versions")
        if current not in versions:
            raise ValueError(f"Prompt file missing prompts.{section}.versions.{current}")

        prompt_obj = versions.get(current)
        if not isinstance(prompt_obj, dict):
            raise ValueError(f"Prompt file invalid prompts.{section}.versions.{current}")

        value = prompt_obj.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Prompt file missing prompts.{section}.versions.{current}.{key}")
        return value

    return Prompts(
        readability_system=_get("readability", "system"),
        readability_user=_get("readability", "user"),
        translation_system=_get("translation_to_ru", "system"),
        translation_user=_get("translation_to_ru", "user"),
        structure_system=_get("structure_markdown", "system"),
        structure_user=_get("structure_markdown", "user"),
    )


def remove_timecodes(text: str) -> str:
    without_timecodes = re.sub(r"\b\d{1,2}:\d{2}\b", "", text)
    without_linebreaks = re.sub(r"\r?\n|\r", " ", without_timecodes)
    return re.sub(r"\s+", " ", without_linebreaks).strip()


def split_into_chunks(text: str, chunk_size: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


@dataclass(frozen=True)
class TailResult:
    complete_text: str
    tail: str


def find_tail(text: str) -> TailResult:
    if not text:
        return TailResult(complete_text="", tail="")

    end_index = len(text) - 1
    sentence_break = max(text.rfind(". ", 0, end_index), text.rfind("? ", 0, end_index), text.rfind("! ", 0, end_index))
    if sentence_break == -1:
        return TailResult(complete_text=text.strip(), tail="")

    complete_text = text[: sentence_break + 1].strip()
    tail = text[sentence_break + 2 :].strip()
    return TailResult(complete_text=complete_text, tail=tail)


def detect_language_name(text: str) -> str:
    sample = text.strip()[:300]
    if not sample:
        return "English"
    mapping = {"en": "English", "de": "German", "ru": "Russian"}
    try:
        code = detect(sample)
    except Exception:
        return "English"
    return mapping.get(code, "English")


def render_template(template: str, **values: str) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _chat(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    temperature: float,
) -> str:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
    except RateLimitError as exc:
        body = getattr(exc, "body", None)
        error_code = getattr(exc, "code", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                error_code = error.get("code") or error_code
        if error_code == "insufficient_quota":
            raise OpenAIQuotaError("OpenAI quota exceeded") from exc
        raise OpenAIRateLimitError("OpenAI rate limit exceeded") from exc
    except APIError as exc:
        logger.exception("OpenAI API error status=%s", getattr(exc, "status_code", None))
        raise

    return (response.choices[0].message.content or "").strip()


def improve_readability(text: str, *, client: OpenAI, model: str, prompts: Prompts) -> TailResult:
    language = detect_language_name(text)
    user = render_template(prompts.readability_user, language=language, text=text)
    result = _chat(client, model, prompts.readability_system, user, temperature=0.1)
    if not result:
        raise ValueError("OpenAI returned empty readability result")
    return find_tail(result)


def translate_to_russian(text: str, *, client: OpenAI, model: str, prompts: Prompts) -> str:
    language = detect_language_name(text)
    user = render_template(prompts.translation_user, language=language, text=text)
    result = _chat(client, model, prompts.translation_system, user, temperature=0.1)
    if not result:
        raise ValueError("OpenAI returned empty translation result")
    return result


def structure_markdown(text: str, *, client: OpenAI, model: str, prompts: Prompts) -> str:
    user = render_template(prompts.structure_user, text=text)
    result = _chat(client, model, prompts.structure_system, user, temperature=0.1)
    if not result:
        raise ValueError("OpenAI returned empty structure result")
    return result


@dataclass(frozen=True)
class HandlerResult:
    original_transcript: str
    readable_transcript: str
    translation_ru: str | None
    structured_markdown: str
    structured_translation_markdown: str | None
    detected_language: str


def handle_transcript(
    transcript: str,
    *,
    api_key: str,
    model: str,
    prompt_path: Path,
    chunk_size: int = 30_000,
) -> HandlerResult:
    logger.info("starting transcript processing chars=%s chunk_size=%s", len(transcript or ""), chunk_size)
    prompts = load_prompts(prompt_path)
    client = OpenAI(api_key=api_key)

    original_transcript = transcript
    clear_text = remove_timecodes(original_transcript)
    chunks = split_into_chunks(clear_text, chunk_size=chunk_size)
    if not chunks:
        raise ValueError("Transcript is empty")

    logger.info("transcript split into chunks count=%s", len(chunks))

    readable_parts: list[str] = []
    current_tail = ""
    for idx, chunk in enumerate(chunks, start=1):
        logger.info("processing chunk %s of %s chars=%s", idx, len(chunks), len(chunk))
        tail_result = improve_readability(current_tail + chunk, client=client, model=model, prompts=prompts)
        readable_parts.append(tail_result.complete_text)
        current_tail = tail_result.tail
    if current_tail:
        readable_parts.append(current_tail)
    readable_transcript = "\n\n".join(part for part in readable_parts if part.strip()).strip()
    if not readable_transcript:
        raise ValueError("Readable transcript is empty")

    detected_language = detect_language_name(readable_transcript)
    logger.info("readability done detected_language=%s", detected_language)
    translation_ru: str | None = None
    if detected_language != "Russian":
        logger.info("starting ru translation")
        translation_ru = translate_to_russian(readable_transcript, client=client, model=model, prompts=prompts)

    text_for_structuring = translation_ru if translation_ru else readable_transcript
    logger.info("starting markdown structuring for language=%s", "Russian")
    structured_markdown = structure_markdown(text_for_structuring, client=client, model=model, prompts=prompts)
    structured_translation_markdown: str | None = None

    return HandlerResult(
        original_transcript=original_transcript,
        readable_transcript=readable_transcript,
        translation_ru=translation_ru,
        structured_markdown=structured_markdown,
        structured_translation_markdown=structured_translation_markdown,
        detected_language=detected_language,
    )
