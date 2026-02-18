from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from contextlib import suppress
from html import escape as html_escape
from typing import Any

from dotenv import load_dotenv
from telegram import InputFile, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import markdown as md

from .checkLink import is_valid_youtube_url, normalize_youtube_url
from .config import RuntimeLimits, load_notion_config, load_openai_config, load_runtime_limits
from .get_transcript import NoSupportedTranscriptFound
from .pipeline import process_plain_text, process_youtube_url
from .transcript_handler import OpenAIQuotaError, OpenAIRateLimitError


logger = logging.getLogger(__name__)


QUEUE_KEY = "queue"
QUEUE_WORKER_TASK_KEY = "queue_worker_task"
SELF_PING_JOB_KEY = "self_ping_job"
LIMITS_KEY = "LIMITS"
OPENAI_KEY = "OPENAI"
NOTION_KEY = "NOTION"
TRANSCRIPT_LANGUAGES_KEY = "TRANSCRIPT_LANGUAGES"


def _parse_languages(value: str | None) -> list[str] | None:
    if not value:
        return None
    langs = [lang.strip() for lang in value.split(",")]
    langs = [lang for lang in langs if lang]
    return langs or None


def _sanitize_filename(name: str, limit: int = 50) -> str:
    safe = re.sub(r"[^\w\s\-.,()]", "", name, flags=re.UNICODE).strip()
    safe = re.sub(r"\s+", " ", safe)
    safe = safe[:limit].strip() or "result"
    return safe


def _build_html_document(title: str, content: str, *, render_markdown: bool) -> str:
    safe_title = html_escape(title or "Transcript")
    if render_markdown:
        body_html = md.markdown(content or "", extensions=["extra", "sane_lists"])
    else:
        body_html = f"<pre>{html_escape(content or '')}</pre>"
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        f"  <title>{safe_title}</title>\n"
        "  <style>\n"
        "    :root { color-scheme: light; }\n"
        "    body { font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; margin: 24px; color: #111; }\n"
        "    h1 { font-size: 22px; margin: 0 0 16px; }\n"
        "    h2 { font-size: 18px; margin: 20px 0 10px; }\n"
        "    h3 { font-size: 16px; margin: 18px 0 8px; }\n"
        "    strong { font-weight: 700; }\n"
        "    em { font-style: italic; }\n"
        "    p { line-height: 1.55; margin: 10px 0; }\n"
        "    ul, ol { margin: 10px 0 10px 22px; }\n"
        "    li { margin: 6px 0; }\n"
        "    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f2f2f2; padding: 0 4px; border-radius: 4px; }\n"
        "    pre { white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f6f6f6; padding: 12px; border-radius: 6px; }\n"
        "    blockquote { border-left: 3px solid #ddd; padding-left: 12px; color: #444; margin: 12px 0; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"  <h1>{safe_title}</h1>\n"
        f"  <div class=\"content\">{body_html}</div>\n"
        "</body>\n"
        "</html>\n"
    )


async def _send_html_file(
    app: Application,
    *,
    chat_id: int,
    filename: str,
    content: str,
    title: str,
    render_markdown: bool,
) -> None:
    html_doc = _build_html_document(title, content, render_markdown=render_markdown)
    data = io.BytesIO(html_doc.encode("utf-8"))
    data.name = filename
    await app.bot.send_document(chat_id=chat_id, document=InputFile(data, filename=filename))


def _get_queue(app: Application) -> asyncio.Queue:
    queue = app.bot_data.get(QUEUE_KEY)
    if not isinstance(queue, asyncio.Queue):
        raise RuntimeError("Queue is not initialized in bot_data")
    return queue


def _get_limits(app: Application) -> RuntimeLimits:
    limits = app.bot_data.get(LIMITS_KEY)
    if not isinstance(limits, RuntimeLimits):
        raise RuntimeError("LIMITS is not initialized in bot_data")
    return limits


def _get_openai_cfg(app: Application) -> Any:
    cfg = app.bot_data.get(OPENAI_KEY)
    if cfg is None:
        raise RuntimeError("OPENAI config is not initialized in bot_data")
    return cfg


def _get_notion_cfg(app: Application) -> Any:
    return app.bot_data.get(NOTION_KEY)


def _get_transcript_languages(app: Application) -> list[str] | None:
    langs = app.bot_data.get(TRANSCRIPT_LANGUAGES_KEY)
    if langs is None:
        return None
    if not isinstance(langs, list):
        raise RuntimeError("TRANSCRIPT_LANGUAGES must be a list[str] or None")
    return langs


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Send me a YouTube link or a .txt file")


async def _schedule_self_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    chat_id_raw = os.getenv("BOT_CHAT_ID", "").strip()
    if not chat_id_raw:
        await update.message.reply_text("Missing BOT_CHAT_ID in .env")
        return

    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        await update.message.reply_text("BOT_CHAT_ID must be a number")
        return

    interval_min_raw = os.getenv("SELF_PING_INTERVAL_MIN", "25").strip()
    try:
        interval_min = int(interval_min_raw)
    except ValueError:
        interval_min = 25

    interval_sec = max(60, interval_min * 60)
    logger.info("self-ping scheduled chat_id=%s interval_min=%s", chat_id, interval_min)

    existing_job = context.application.bot_data.get(SELF_PING_JOB_KEY)
    if existing_job:
        existing_job.schedule_removal()

    async def _ping_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await ctx.bot.send_message(chat_id=chat_id, text="self-ping")
            logger.info("self-ping sent chat_id=%s", chat_id)
        except Exception:
            logger.exception("self-ping failed chat_id=%s", chat_id)

    context.application.bot_data[SELF_PING_JOB_KEY] = context.job_queue.run_repeating(
        _ping_job,
        interval=interval_sec,
        first=interval_sec,
    )

    await update.message.reply_text(f"received self-ping, I'll send ping message in {interval_min} minutes")


async def _enqueue_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    job: dict[str, Any],
    accepted_message: str,
) -> None:
    app = context.application
    queue = _get_queue(app)

    chat_id = update.effective_chat.id
    if queue.full():
        if update.message:
            await update.message.reply_text("Queue is full right now, please try again later.")
        return

    position = queue.qsize()
    job["chat_id"] = chat_id

    logger.info("enqueue job chat_id=%s position=%s type=%s", chat_id, position, job.get("type"))
    queue.put_nowait(job)

    _ensure_queue_worker(app)

    if update.message:
        if position == 0:
            await update.message.reply_text(accepted_message)
        else:
            await update.message.reply_text(f"Please wait in queue, {position} more in line")


async def _enqueue_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    logger.info("enqueue youtube chat_id=%s url=%s", update.effective_chat.id, url)
    await _enqueue_job(
        update,
        context,
        job={"type": "youtube", "url": url},
        accepted_message="link is in process",
    )


async def _enqueue_text(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, text: str) -> None:
    logger.info("enqueue text chat_id=%s title=%s length=%s", update.effective_chat.id, title, len(text))
    await _enqueue_job(
        update,
        context,
        job={"type": "text", "title": title, "text": text},
        accepted_message="File received. Processing...",
    )


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

    limits = _get_limits(context.application)

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
    await _enqueue_text(update, context, title=title, text=text)


async def _process_youtube_item(app: Application, item: dict[str, Any]) -> None:
    chat_id = int(item["chat_id"])
    raw_url = str(item["url"])
    url = normalize_youtube_url(raw_url)

    logger.info("queue start youtube chat_id=%s url=%s raw_url=%s", chat_id, url, raw_url)

    try:
        languages = _get_transcript_languages(app)
        openai_cfg = _get_openai_cfg(app)
        notion_cfg = _get_notion_cfg(app)

        processed = await asyncio.to_thread(
            process_youtube_url,
            url=url,
            languages=languages,
            openai=openai_cfg,
            notion=notion_cfg,
        )
    except NoSupportedTranscriptFound:
        logger.info("no supported transcript found chat_id=%s url=%s", chat_id, url)
        await app.bot.send_message(chat_id=chat_id, text="нет субтитров для данного языка")
        return
    except OpenAIQuotaError:
        logger.exception("openai quota exceeded chat_id=%s url=%s", chat_id, url)
        await app.bot.send_message(
            chat_id=chat_id,
            text="OpenAI quota exceeded. Please check billing and try again later.",
        )
        return
    except OpenAIRateLimitError:
        logger.exception("openai rate limited chat_id=%s url=%s", chat_id, url)
        await app.bot.send_message(
            chat_id=chat_id,
            text="OpenAI rate limit reached. Please try again in a few minutes.",
        )
        return
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

    try:
        await app.bot.send_message(
            chat_id=chat_id,
            text=f"video you provided '{title}' by '{author}' is now being processed",
        )
    except Exception:
        logger.exception("failed to send processing message chat_id=%s", chat_id)

    structure_link = processed.notion_links.get("structured_markdown", "")
    original_link = processed.notion_links.get("original_transcript", "")
    translation_link = processed.notion_links.get("translation_ru", "")

    try:
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
                text=(
                    f"Here is a link to text with structure : {structure_link}\n\n"
                    f"Here is original transcript: {original_link}"
                ),
            )
    except Exception:
        logger.exception("failed to send notion links message chat_id=%s", chat_id)

    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    if channel_id and (original_link or structure_link):
        channel_message = (
            f"ЗАГОЛОВОК: {title}\n"
            f"АВТОР: {author}\n"
            f"ССЫЛКА: {url}\n\n"
            f"{original_link}\n\n"
            f"СТРУКТУРА: {structure_link}\n"
        )

        if translation_link:
            channel_message = (
                f"ЗАГОЛОВОК: {title}\n"
                f"автор: {author}\n"
                f"ССЫЛКА: {url}\n\n"
                f"{original_link}\n\n"
                f"ПЕРЕВОД: {translation_link}\n\n"
                f"СТРУКТУРА: {structure_link}\n"
            )

        logger.info("send to channel_message channel_id=%s", channel_id)

        try:
            await app.bot.send_message(chat_id=channel_id, text=channel_message)
        except Exception:
            logger.exception("failed to send message to channel_id=%s", channel_id)

    base_filename = _sanitize_filename(title)

    try:
        await _send_html_file(
            app,
            chat_id=chat_id,
            filename=f"{base_filename}.html",
            content=processed.handled.readable_transcript,
            title=title,
            render_markdown=False,
        )
    except Exception:
        logger.exception("failed to send readable transcript file chat_id=%s", chat_id)

    try:
        await _send_html_file(
            app,
            chat_id=chat_id,
            filename=f"{base_filename}-structure.html",
            content=processed.handled.structured_markdown,
            title=f"{title} (Structured)",
            render_markdown=True,
        )
    except Exception:
        logger.exception("failed to send structured markdown file chat_id=%s", chat_id)

    if processed.handled.translation_ru:
        try:
            await _send_html_file(
                app,
                chat_id=chat_id,
                filename=f"trnsl-{base_filename}.html",
                content=processed.handled.translation_ru,
                title=f"{title} (Translation)",
                render_markdown=True,
            )
        except Exception:
            logger.exception("failed to send translation file chat_id=%s", chat_id)

    logger.info("queue done youtube chat_id=%s url=%s", chat_id, url)


async def _process_text_item(app: Application, item: dict[str, Any]) -> None:
    chat_id = int(item["chat_id"])
    title = str(item.get("title") or "text from file")
    text = str(item.get("text") or "")

    logger.info("queue start text chat_id=%s title=%s length=%s", chat_id, title, len(text))

    try:
        openai_cfg = _get_openai_cfg(app)
        notion_cfg = _get_notion_cfg(app)

        processed = await asyncio.to_thread(
            process_plain_text,
            title=title,
            text=text,
            openai=openai_cfg,
            notion=notion_cfg,
        )
    except OpenAIQuotaError:
        logger.exception("openai quota exceeded chat_id=%s title=%s", chat_id, title)
        await app.bot.send_message(
            chat_id=chat_id,
            text="OpenAI quota exceeded. Please check billing and try again later.",
        )
        return
    except OpenAIRateLimitError:
        logger.exception("openai rate limited chat_id=%s title=%s", chat_id, title)
        await app.bot.send_message(
            chat_id=chat_id,
            text="OpenAI rate limit reached. Please try again in a few minutes.",
        )
        return
    except Exception:
        logger.exception("failed process_plain_text chat_id=%s title=%s", chat_id, title)
        await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")
        return

    try:
        await app.bot.send_message(chat_id=chat_id, text=f"Notion links: {processed.notion_links}")
    except Exception:
        logger.exception("failed to send notion links message chat_id=%s", chat_id)

    base_filename = _sanitize_filename(title)

    try:
        await _send_html_file(
            app,
            chat_id=chat_id,
            filename=f"{base_filename}.html",
            content=processed.handled.readable_transcript,
            title=title,
            render_markdown=False,
        )
    except Exception:
        logger.exception("failed to send readable transcript file chat_id=%s", chat_id)

    try:
        await _send_html_file(
            app,
            chat_id=chat_id,
            filename=f"{base_filename}-structure.html",
            content=processed.handled.structured_markdown,
            title=f"{title} (Structured)",
            render_markdown=True,
        )
    except Exception:
        logger.exception("failed to send structured markdown file chat_id=%s", chat_id)

    logger.info("queue done text chat_id=%s title=%s", chat_id, title)


async def _queue_worker(app: Application) -> None:
    queue = _get_queue(app)

    while True:
        item = None
        chat_id = 0
        
        try:
            item = await queue.get()
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("queue worker cancelled")
            return
        except Exception:
            logger.exception("unexpected error getting item from queue")
            # Continue to next iteration instead of stopping
            await asyncio.sleep(1)
            continue

        # Safely extract chat_id
        try:
            chat_id_raw = item.get("chat_id") if item else None
            if chat_id_raw:
                chat_id = int(chat_id_raw)
        except (ValueError, TypeError):
            logger.warning("invalid chat_id in queue item: %s", chat_id_raw)
            chat_id = 0

        try:
            job_type = item.get("type") if item else None
            if job_type == "youtube":
                await _process_youtube_item(app, item)
            elif job_type == "text":
                await _process_text_item(app, item)
            else:
                if chat_id:
                    try:
                        await app.bot.send_message(chat_id=chat_id, text="Unknown job type")
                    except Exception:
                        logger.exception("failed to send 'Unknown job type' message chat_id=%s", chat_id)
        except Exception:
            logger.exception("unexpected error processing queue item chat_id=%s type=%s", chat_id, item.get("type") if item else "unknown")
            if chat_id:
                try:
                    await app.bot.send_message(chat_id=chat_id, text="something went wrong, Marina needs to watch logs")
                except Exception:
                    logger.exception("failed to send error message chat_id=%s", chat_id)
        finally:
            # Always clean up and mark task as done
            try:
                queue.task_done()
            except Exception:
                logger.exception("failed to call queue.task_done()")


def _ensure_queue_worker(app: Application) -> None:
    task = app.bot_data.get(QUEUE_WORKER_TASK_KEY)
    if task and hasattr(task, "done") and not task.done():
        return

    logger.info("starting queue worker")
    app.bot_data[QUEUE_WORKER_TASK_KEY] = app.create_task(_queue_worker(app))


async def _post_shutdown(app: Application) -> None:
    task = app.bot_data.get(QUEUE_WORKER_TASK_KEY)
    if task and hasattr(task, "done") and not task.done():
        logger.info("stopping queue worker")
        task.cancel()
        with suppress(asyncio.CancelledError, GeneratorExit):
            await task


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # NOTE: This prevents "No error handlers are registered" and keeps one place for all unexpected errors
    logger.exception("Unhandled error", exc_info=context.error)


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
        OPENAI_KEY: load_openai_config(),
        NOTION_KEY: load_notion_config(),
        TRANSCRIPT_LANGUAGES_KEY: _parse_languages(os.getenv("TRANSCRIPT_LANGUAGES")),
        LIMITS_KEY: limits,
        QUEUE_KEY: asyncio.Queue(maxsize=limits.queue_maxsize),
        QUEUE_WORKER_TASK_KEY: None,
        SELF_PING_JOB_KEY: None,
    }

    application.add_handler(CommandHandler("start", _start))
    application.add_handler(MessageHandler(filters.Document.ALL, _on_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))

    application.add_error_handler(_on_error)

    return application


def run_bot() -> None:
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    run_bot()
