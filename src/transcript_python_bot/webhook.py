from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, request
from telegram import Update

from .bot import build_application


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookConfig:
    path: str
    secret_token: str | None


class _BotRuntime:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="telegram-bot-loop", daemon=True)
        self._ready = threading.Event()
        self._application = None

    def start(self) -> None:
        if self._thread.is_alive():
            return
        self._thread.start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError("Telegram application failed to start within 30s")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._startup())
        self._loop.run_forever()

    async def _startup(self) -> None:
        try:
            application = build_application()
            await application.initialize()
            await application.start()
            self._application = application
            logger.info("telegram application started")
            self._ready.set()
        except Exception:
            logger.exception("failed to start telegram application")
            self._ready.set()
            raise

    def submit_update(self, update_payload: dict[str, Any]) -> None:
        self.start()
        if not self._application:
            raise RuntimeError("Telegram application is not available")
        update = Update.de_json(update_payload, self._application.bot)
        fut = asyncio.run_coroutine_threadsafe(self._application.update_queue.put(update), self._loop)
        fut.result(timeout=5)


_RUNTIME = _BotRuntime()


def _load_webhook_config() -> WebhookConfig:
    raw_path = (os.getenv("TELEGRAM_WEBHOOK_PATH") or "").strip()
    path = raw_path.lstrip("/")
    if not path:
        raise RuntimeError("Missing TELEGRAM_WEBHOOK_PATH in environment (example: telegram/<random-secret>)")
    secret_token = (os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or "").strip() or None
    return WebhookConfig(path=path, secret_token=secret_token)


def create_wsgi_application() -> Flask:
    load_dotenv()
    cfg = _load_webhook_config()
    app = Flask(__name__)

    @app.get("/")
    def health() -> tuple[str, int]:
        return "ok", 200

    @app.post(f"/{cfg.path}")
    def telegram_webhook() -> tuple[str, int]:
        if cfg.secret_token:
            provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if provided != cfg.secret_token:
                abort(403)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)

        try:
            _RUNTIME.submit_update(payload)
        except Exception:
            logger.exception("failed to submit telegram update")
            abort(500)

        return "ok", 200

    return app
