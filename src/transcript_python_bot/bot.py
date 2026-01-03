from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from contextlib import suppress

from dotenv import load_dotenv
from telegram import InputFile, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .checkLink import is_valid_youtube_url, normalize_youtube_url
from .config import RuntimeLimits, load_notion_config, load_openai_config, load_runtime_limits
from .pipeline import process_plain_text, process_youtube_url


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
    pending_chat_ids: set[int] = context.application.bot_data["pending_chat_ids"]
    if update.effective_chat.id in pending_chat_ids:
        if update.message:
            await update.message.reply_text("You already have a task in queue. Please wait.")
        return
    if queue.full():
        if update.message:
            await update.message.reply_text("Queue is full right now, please try again later.")
        return

    pending_chat_ids.add(update.effective_chat.id)
    position = queue.qsize()
    logger.info("enqueue youtube chat_id=%s position=%s url=%s", update.effective_chat.id, position, url)
    queue.put_nowait({"type": "youtube", "chat_id": update.effective_chat.id, "url": url})
    _ensure_queue_worker(context.application)
    if update.message:
        if position == 0:
            await update.message.reply_text("link is in process")
        else:
            await update.message.reply_text(f"Please wait in queue, {position} more in line")


async def _enqueue_text(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, text: str) -> None:
    queue: asyncio.Queue = context.application.bot_data["queue"]
    pending_chat_ids: set[int] = context.application.bot_data["pending_chat_ids"]
    if update.effective_chat.id in pending_chat_ids:
        if update.message:
            await update.message.reply_text("You already have a task in queue. Please wait.")
        return
    if queue.full():
        if update.message:
            await update.message.reply_text("Queue is full right now, please try again later.")
        return

    pending_chat_ids.add(update.effective_chat.id)
    position = queue.qsize()
    logger.info(
        "enqueue text chat_id=%s position=%s title=%s length=%s",
        update.effective_chat.id,
        position,
        title,
        len(text),
    )
    queue.put_nowait({"type": "text", "chat_id": update.effective_chat.id, "title": title, "text": text})
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
    limits: RuntimeLimits = context.application.bot_data["LIMITS"]
    if doc.file_size and doc.file_size > limits.max_text_file_bytes:
        await update.message.reply_text("File is too large. Please send a smaller .txt file.")
        return

    filename = (doc.file_name or "").strip()
    is_txt_by_name = filename.lower().endswith(".txt")
    is_txt_by_mime = (doc.mime_type or "").lower() in {"text/plain", "text/markdown"}
    if not (is_txt_by_name or is_txt_by_mime):
        await update.message.reply_text("Please send a .txt file (plain text).")
        return

    file = await doc.get_file()
    data = await file.download_as_bytearray()
    text = data.decode("utf-8", errors="replace")
    if len(text) > limits.max_text_chars:
        await update.message.reply_text("Text is too large. Please send a shorter file.")
        return

    title = filename or "text from file"
    await update.message.reply_text("File received. Processing...")
    await _enqueue_text(update, context, title=title, text=text)


async def _process_youtube_item(app: Application, item: dict) -> None:
    chat_id = int(item["chat_id"])
    raw_url = str(item["url"])
    url = normalize_youtube_url(raw_url)
    logger.info("queue start youtube chat_id=%s url=%s raw_url=%s", chat_id, url, raw_url)
    try:
        languages = app.bot_data.get("TRANSCRIPT_LANGUAGES")
        openai_cfg = app.bot_data["OPENAI"]
        notion_cfg = app.bot_data.get("NOTION")
        processed = await asyncio.to_thread(
            process_youtube_url,
            url=url,
            languages=languages,
            openai=openai_cfg,
            notion=notion_cfg,
        )
    except Exception:
        logger.exception("failed process_youtube_url chat_id=%s url=%s", chat_id, url)
        await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")
        return

    title = processed.video_info.title or f"YouTube {processed.video_info.video_id}"
    author = processed.video_info.author or "unknown"
    logger.info(
        "processed youtube chat_id=%s video_id=%s title=%s author=%s transcript_len=%s detected_language=%s",
        chat_id,
        processed.video_info.video_id,
        title,
        author,
        len(processed.transcript_text),
        processed.handled.detected_language,
    )

    await app.bot.send_message(chat_id=chat_id, text=f"video you provided '{title}' by '{author}' is now being processed")

    structure_link = processed.notion_links.get("structured_markdown", "")
    original_link = processed.notion_links.get("original_transcript", "")
    translation_link = processed.notion_links.get("translation_ru", "")
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
    logger.info("send to channel_message", channel_id)
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
    await _send_text_file(
        app,
        chat_id=chat_id,
        filename=f"{base_filename}.txt",
        content=processed.handled.readable_transcript,
    )
    await _send_text_file(
        app,
        chat_id=chat_id,
        filename=f"{base_filename}-structure.txt",
        content=processed.handled.structured_markdown,
    )

    if processed.handled.translation_ru:
        await _send_text_file(
            app,
            chat_id=chat_id,
            filename=f"trnsl-{base_filename}.txt",
            content=processed.handled.translation_ru,
        )

    logger.info("queue done youtube chat_id=%s url=%s", chat_id, url)


async def _process_text_item(app: Application, item: dict) -> None:
    chat_id = int(item["chat_id"])
    title = str(item.get("title") or "text from file")
    text = str(item.get("text") or "")
    logger.info("queue start text chat_id=%s title=%s length=%s", chat_id, title, len(text))

    try:
        openai_cfg = app.bot_data["OPENAI"]
        notion_cfg = app.bot_data.get("NOTION")
        processed = await asyncio.to_thread(
            process_plain_text,
            title=title,
            text=text,
            openai=openai_cfg,
            notion=notion_cfg,
        )
    except Exception:
        logger.exception("failed process_plain_text chat_id=%s title=%s", chat_id, title)
        await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")
        return

    await app.bot.send_message(chat_id=chat_id, text=f"Notion links: {processed.notion_links}")
    base_filename = _sanitize_filename(title)
    await _send_text_file(
        app,
        chat_id=chat_id,
        filename=f"{base_filename}.txt",
        content=processed.handled.readable_transcript,
    )
    await _send_text_file(
        app,
        chat_id=chat_id,
        filename=f"{base_filename}-structure.txt",
        content=processed.handled.structured_markdown,
    )
    logger.info("queue done text chat_id=%s title=%s", chat_id, title)


async def _queue_worker(app: Application) -> None:
    queue: asyncio.Queue = app.bot_data["queue"]
    pending_chat_ids: set[int] = app.bot_data["pending_chat_ids"]
    while True:
        try:
            item = await queue.get()
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("queue worker cancelled")
            return
        chat_id = int(item.get("chat_id", 0) or 0)
        try:
            if item.get("type") == "youtube":
                await _process_youtube_item(app, item)
            elif item.get("type") == "text":
                await _process_text_item(app, item)
            else:
                if chat_id:
                    await app.bot.send_message(chat_id=chat_id, text="Unknown job type")
        finally:
            if chat_id:
                pending_chat_ids.discard(chat_id)
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

    limits = load_runtime_limits()
    application = Application.builder().token(token).post_shutdown(_post_shutdown).build()
    application.bot_data = {
        "OPENAI": load_openai_config(),
        "NOTION": load_notion_config(),
        "TRANSCRIPT_LANGUAGES": _parse_languages(os.getenv("TRANSCRIPT_LANGUAGES")),
        "LIMITS": limits,
        "queue": asyncio.Queue(maxsize=limits.queue_maxsize),
        "queue_worker_task": None,
        "self_ping_job": None,
        "pending_chat_ids": set(),
    }

    application.add_handler(CommandHandler("start", _start))
    application.add_handler(MessageHandler(filters.Document.ALL, _on_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))
    return application


def run_bot() -> None:
    application = build_application()
    application.run_polling()

if __name__ == "__main__":
    run_bot()