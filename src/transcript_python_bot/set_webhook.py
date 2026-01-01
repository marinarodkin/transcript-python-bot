from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from telegram import Bot


def _build_webhook_url() -> str:
    url = (os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip()
    if url:
        return url

    base = (os.getenv("TELEGRAM_WEBHOOK_BASE_URL") or "").strip().rstrip("/")
    path = (os.getenv("TELEGRAM_WEBHOOK_PATH") or "").strip().lstrip("/")
    if not base or not path:
        raise RuntimeError(
            "Set TELEGRAM_WEBHOOK_URL (full url) or both TELEGRAM_WEBHOOK_BASE_URL and TELEGRAM_WEBHOOK_PATH",
        )
    return f"{base}/{path}"


async def _set_webhook() -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment")

    url = _build_webhook_url()
    secret_token = (os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or "").strip() or None

    bot = Bot(token=token)
    await bot.set_webhook(
        url=url,
        secret_token=secret_token,
        drop_pending_updates=True,
    )


def main() -> None:
    load_dotenv()
    asyncio.run(_set_webhook())


if __name__ == "__main__":
    main()

