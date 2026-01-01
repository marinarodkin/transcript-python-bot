from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from contextlib import suppress
from pathlib import Path

from dotenv import load_dotenv
from telegram import InputFile, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .checkLink import extract_video_id, is_valid_youtube_url, normalize_youtube_url
from .get_transcript import fetch_transcript
from .notion import send_markdown_to_notion
from .transcript_handler import handle_transcript
from .video_info import VideoInfo, fetch_video_info


logger = logging.getLogger(__name__)


def _parse_languages(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [lang.strip() for lang in value.split(",") if lang.strip()]


def _sanitize_filename(name: str, limit: int = 50) -> str:
    safe = re.sub(r"[^\w\s\-.,()]", "", name, flags=re.UNICODE).strip()
    safe = re.sub(r"\s+", " ", safe)
    safe = safe[:limit].strip() or "result"
    return safe


async def _send_text_file(app: Application, *, chat_id: int, filename: str, content: str) -> None:
    data = io.BytesIO(content.encode("utf-8"))
    data.name = filename
    await app.bot.send_document(chat_id=chat_id, document=InputFile(data, filename=filename))


def _build_openai_config() -> dict:
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in environment")
    return {
        "api_key": openai_api_key,
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        "prompt_path": Path(os.getenv("PROMPT_PATH", "prompts/transcript_prompts.yaml")),
        "chunk_size": int(os.getenv("CHUNK_SIZE", "30000")),
    }


def _build_notion_config() -> dict | None:
    notion_api_key = os.getenv("NOTION_API_KEY", "").strip()
    notion_database_id = os.getenv("NOTION_DATABASE_ID", "").strip()
    if not notion_api_key or not notion_database_id:
        return None
    return {
        "api_key": notion_api_key,
        "database_id": notion_database_id,
        "title_property": os.getenv("NOTION_TITLE_PROPERTY", "title").strip() or "title",
        "link_property": os.getenv("NOTION_LINK_PROPERTY", "link").strip() or "link",
    }


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Send me a YouTube link or a .txt file")


async def _schedule_self_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    chat_id = os.getenv("BOT_CHAT_ID", "").strip()
    if not chat_id:
        await update.message.reply_text("Missing BOT_CHAT_ID in .env")
        return

    interval_min = int(os.getenv("SELF_PING_INTERVAL_MIN", "25"))
    interval_sec = max(60, interval_min * 60)

    logger.info("self-ping scheduled chat_id=%s interval_min=%s", chat_id, interval_min)

    job = context.application.bot_data.get("self_ping_job")
    if job:
        job.schedule_removal()

    async def _ping_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await ctx.bot.send_message(chat_id=int(chat_id), text="self-ping")
            logger.info("self-ping sent chat_id=%s", chat_id)
        except Exception:
            logger.exception("self-ping failed chat_id=%s", chat_id)

    context.application.bot_data["self_ping_job"] = context.job_queue.run_repeating(
        _ping_job,
        interval=interval_sec,
        first=interval_sec,
    )

    await update.message.reply_text(f"received self-ping, I'll send ping message in {interval_min} minutes")


async def _enqueue_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    queue: asyncio.Queue = context.application.bot_data["queue"]
    position = queue.qsize()
    logger.info("enqueue youtube chat_id=%s position=%s url=%s", update.effective_chat.id, position, url)
    await queue.put({"type": "youtube", "chat_id": update.effective_chat.id, "url": url})
    _ensure_queue_worker(context.application)
    if update.message:
        if position == 0:
            await update.message.reply_text("link is in process")
        else:
            await update.message.reply_text(f"Please wait in queue, {position} more in line")


async def _enqueue_text(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, text: str) -> None:
    queue: asyncio.Queue = context.application.bot_data["queue"]
    position = queue.qsize()
    logger.info(
        "enqueue text chat_id=%s position=%s title=%s length=%s",
        update.effective_chat.id,
        position,
        title,
        len(text),
    )
    await queue.put({"type": "text", "chat_id": update.effective_chat.id, "title": title, "text": text})
    _ensure_queue_worker(context.application)
    if update.message:
        if position == 0:
            await update.message.reply_text("text is in process")
        else:
            await update.message.reply_text(f"Please wait in queue, {position} more in line")


async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    text = (update.message.text or "").strip()
    logger.info("telegram text received chat_id=%s length=%s", update.effective_chat.id, len(text))
    if not text:
        await update.message.reply_text("Send me a YouTube link or a .txt file")
        return

    if text == "self-ping":
        await _schedule_self_ping(update, context)
        return

    if not is_valid_youtube_url(text):
        await update.message.reply_text("link is not valid, send me a valid YouTube link")
        return

    await _enqueue_youtube(update, context, text)


async def _on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return

    doc = update.message.document
    logger.info(
        "telegram document received chat_id=%s name=%s mime=%s size=%s",
        update.effective_chat.id,
        doc.file_name,
        doc.mime_type,
        doc.file_size,
    )
    if doc.mime_type != "text/plain":
        await update.message.reply_text("Please send a valid text file (.txt)")
        return

    file = await doc.get_file()
    data = await file.download_as_bytearray()
    text = data.decode("utf-8", errors="replace")
    title = doc.file_name or "text from file"
    await update.message.reply_text("File received. Processing...")
    await _enqueue_text(update, context, title=title, text=text)


async def _process_youtube_item(app: Application, item: dict) -> None:
    chat_id = int(item["chat_id"])
    raw_url = str(item["url"])
    url = normalize_youtube_url(raw_url)
    logger.info("queue start youtube chat_id=%s url=%s raw_url=%s", chat_id, url, raw_url)
    languages = app.bot_data.get("TRANSCRIPT_LANGUAGES")
    openai_cfg = app.bot_data["OPENAI"]
    notion_cfg = app.bot_data.get("NOTION")

    try:
        video_info = await asyncio.to_thread(fetch_video_info, url, languages)
    except Exception:
        logger.exception("failed fetch_video_info chat_id=%s url=%s", chat_id, url)
        video_info = VideoInfo(video_id=extract_video_id(url), title="", author="", description="")

    title = video_info.title or f"YouTube {video_info.video_id}"
    author = video_info.author or "unknown"
    logger.info(
        "video info chat_id=%s video_id=%s title=%s author=%s description_len=%s",
        chat_id,
        video_info.video_id,
        title,
        author,
        len(video_info.description or ""),
    )
    await app.bot.send_message(chat_id=chat_id, text=f"video you provided '{title}' by '{author}' is now being processed")

    try:
        transcript_result = await asyncio.to_thread(fetch_transcript, url, languages)
    except Exception:
        logger.exception("failed fetch_transcript chat_id=%s url=%s", chat_id, url)
        await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")
        return

    if not transcript_result.transcript_text.strip():
        await app.bot.send_message(chat_id=chat_id, text="Sorry, I can't find subtitles for the video")
        return

    logger.info(
        "transcript fetched chat_id=%s video_id=%s length=%s",
        chat_id,
        transcript_result.video_id,
        len(transcript_result.transcript_text),
    )

    try:
        handled = await asyncio.to_thread(
            handle_transcript,
            transcript_result.transcript_text,
            api_key=openai_cfg["api_key"],
            model=openai_cfg["model"],
            prompt_path=openai_cfg["prompt_path"],
            chunk_size=openai_cfg["chunk_size"],
        )
    except Exception as exc:
        logger.exception("failed handle_transcript chat_id=%s url=%s", chat_id, url)
        await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")
        return

    logger.info(
        "transcript handled chat_id=%s detected_language=%s has_translation=%s",
        chat_id,
        handled.detected_language,
        bool(handled.translation_ru),
    )

    notion_links: dict[str, str] = {}
    if notion_cfg:
        try:
            variants: list[tuple[str, str]] = [
                ("original_transcript", handled.original_transcript),
                ("structured_markdown", handled.structured_markdown),
            ]
            if handled.translation_ru:
                variants.append(("translation_ru", handled.translation_ru))
            if handled.structured_translation_markdown:
                variants.append(("structured_translation_markdown", handled.structured_translation_markdown))

            for key, markdown in variants:
                logger.info("notion upload start chat_id=%s key=%s", chat_id, key)
                page = await asyncio.to_thread(
                    send_markdown_to_notion,
                    notion_api_key=notion_cfg["api_key"],
                    database_id=notion_cfg["database_id"],
                    title=f"{title} — {key}",
                    markdown=markdown,
                    link=url,
                    title_property=notion_cfg["title_property"],
                    link_property=notion_cfg["link_property"],
                )
                if page.get("url"):
                    notion_links[key] = page["url"]
                logger.info("notion upload done chat_id=%s key=%s url=%s", chat_id, key, page.get("url", ""))
        except Exception as exc:
            logger.exception("notion upload failed chat_id=%s", chat_id)
            await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")

    structure_link = notion_links.get("structured_markdown", "")
    original_link = notion_links.get("original_transcript", "")
    translation_link = notion_links.get("translation_ru", "")
    if translation_link:
        await app.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Here is notion link: {original_link}\n"
                f"Here is translation link: {translation_link}\n"
                f"Here is structure link: {structure_link}\n"
            ),
        )
    else:
        await app.bot.send_message(
            chat_id=chat_id,
            text=f"Here is a link to text with structure : {structure_link}\n\nHere is original transcript: {original_link}",
        )

    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    if channel_id and (original_link or structure_link):
        channel_message = f"ЗАГОЛОВОК: {title}\nАВТОР: {author}\nССЫЛКА: {url}\n\n{original_link}\n\nСТРУКТУРА: {structure_link}\n"
        if translation_link:
            channel_message = (
                f"ЗАГОЛОВОК: {title}\nавтор: {author}\nССЫЛКА: {url}\n\n{original_link}\n\nПЕРЕВОД: {translation_link}\n\nСТРУКТУРА: {structure_link}\n"
            )
        try:
            await app.bot.send_message(chat_id=channel_id, text=channel_message)
        except Exception:
            pass

    base_filename = _sanitize_filename(title)
    await _send_text_file(app, chat_id=chat_id, filename=f"{base_filename}.txt", content=handled.readable_transcript)
    await _send_text_file(
        app,
        chat_id=chat_id,
        filename=f"{base_filename}-structure.txt",
        content=handled.structured_markdown,
    )

    if handled.translation_ru:
        await _send_text_file(
            app,
            chat_id=chat_id,
            filename=f"trnsl-{base_filename}.txt",
            content=handled.translation_ru,
        )

    logger.info("queue done youtube chat_id=%s url=%s", chat_id, url)


async def _process_text_item(app: Application, item: dict) -> None:
    chat_id = int(item["chat_id"])
    title = str(item.get("title") or "text from file")
    text = str(item.get("text") or "")
    logger.info("queue start text chat_id=%s title=%s length=%s", chat_id, title, len(text))
    openai_cfg = app.bot_data["OPENAI"]
    notion_cfg = app.bot_data.get("NOTION")

    try:
        handled = await asyncio.to_thread(
            handle_transcript,
            text,
            api_key=openai_cfg["api_key"],
            model=openai_cfg["model"],
            prompt_path=openai_cfg["prompt_path"],
            chunk_size=openai_cfg["chunk_size"],
        )
    except Exception as exc:
        logger.exception("failed handle_transcript for text chat_id=%s title=%s", chat_id, title)
        await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")
        return

    notion_links: dict[str, str] = {}
    if notion_cfg:
        try:
            variants: list[tuple[str, str]] = [
                ("structured_markdown", handled.structured_markdown),
                ("original_transcript", handled.original_transcript),
            ]
            if handled.translation_ru:
                variants.append(("translation_ru", handled.translation_ru))
            if handled.structured_translation_markdown:
                variants.append(("structured_translation_markdown", handled.structured_translation_markdown))

            for key, markdown in variants:
                logger.info("notion upload start chat_id=%s key=%s", chat_id, key)
                page = await asyncio.to_thread(
                    send_markdown_to_notion,
                    notion_api_key=notion_cfg["api_key"],
                    database_id=notion_cfg["database_id"],
                    title=f"{title} — {key}",
                    markdown=markdown,
                    link="",
                    title_property=notion_cfg["title_property"],
                    link_property=notion_cfg["link_property"],
                )
                if page.get("url"):
                    notion_links[key] = page["url"]
                logger.info("notion upload done chat_id=%s key=%s url=%s", chat_id, key, page.get("url", ""))
        except Exception as exc:
            logger.exception("notion upload failed chat_id=%s", chat_id)
            await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")

    await app.bot.send_message(chat_id=chat_id, text=f"Notion links: {notion_links}")
    base_filename = _sanitize_filename(title)
    await _send_text_file(app, chat_id=chat_id, filename=f"{base_filename}.txt", content=handled.readable_transcript)
    await _send_text_file(
        app,
        chat_id=chat_id,
        filename=f"{base_filename}-structure.txt",
        content=handled.structured_markdown,
    )
    logger.info("queue done text chat_id=%s title=%s", chat_id, title)


async def _queue_worker(app: Application) -> None:
    queue: asyncio.Queue = app.bot_data["queue"]
    while True:
        try:
            item = await queue.get()
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("queue worker cancelled")
            return
        try:
            if item.get("type") == "youtube":
                await _process_youtube_item(app, item)
            elif item.get("type") == "text":
                await _process_text_item(app, item)
            else:
                await app.bot.send_message(chat_id=int(item.get("chat_id", 0)), text="Unknown job type")
        finally:
            queue.task_done()

def _ensure_queue_worker(app: Application) -> None:
    task = app.bot_data.get("queue_worker_task")
    if task and not task.done():
        return
    logger.info("starting queue worker")
    app.bot_data["queue_worker_task"] = app.create_task(_queue_worker(app))

async def _post_shutdown(app: Application) -> None:
    task = app.bot_data.get("queue_worker_task")
    if task and not task.done():
        logger.info("stopping queue worker")
        task.cancel()
        with suppress(asyncio.CancelledError, GeneratorExit):
            await task


def build_application() -> Application:
    load_dotenv()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(os.getenv("HTTPX_LOG_LEVEL", "WARNING").upper())
    logging.getLogger("telegram").setLevel(os.getenv("TELEGRAM_LOG_LEVEL", "WARNING").upper())
    logging.getLogger("telegram.ext").setLevel(os.getenv("TELEGRAM_LOG_LEVEL", "WARNING").upper())
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment")

    application = Application.builder().token(token).post_shutdown(_post_shutdown).build()
    application.bot_data = {
        "OPENAI": _build_openai_config(),
        "NOTION": _build_notion_config(),
        "TRANSCRIPT_LANGUAGES": _parse_languages(os.getenv("TRANSCRIPT_LANGUAGES")),
        "queue": asyncio.Queue(),
        "queue_worker_task": None,
        "self_ping_job": None,
    }

    application.add_handler(CommandHandler("start", _start))
    application.add_handler(MessageHandler(filters.Document.ALL, _on_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))
    return application


def run_bot() -> None:
    application = build_application()
    application.run_polling()
