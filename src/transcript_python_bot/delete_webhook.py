from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from telegram import Bot


async def _delete_webhook() -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment")

    bot = Bot(token=token)
    await bot.delete_webhook(drop_pending_updates=True)


def main() -> None:
    load_dotenv()
    asyncio.run(_delete_webhook())


if __name__ == "__main__":
    main()

