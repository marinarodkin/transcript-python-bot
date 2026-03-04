from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot


_ENV_ASSIGN_RE = re.compile(
    r"""os\.environ\[\s*["'](?P<key>[A-Z0-9_]+)["']\s*\]\s*=\s*["'](?P<value>.*?)["']""",
)
_NEEDED_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_WEBHOOK_BASE_URL",
    "TELEGRAM_WEBHOOK_PATH",
    "TELEGRAM_WEBHOOK_SECRET_TOKEN",
)


def _find_pythonanywhere_wsgi_files() -> list[Path]:
    explicit = (os.getenv("PYTHONANYWHERE_WSGI_FILE") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.exists():
            return [p]

    user = (os.getenv("USER") or "").strip()
    if not user:
        return []

    var_www = Path("/var/www")
    if not var_www.exists():
        return []

    return sorted(var_www.glob(f"{user}_*wsgi.py"))


def _load_env_from_pythonanywhere_wsgi() -> None:
    # PythonAnywhere users often keep secrets in WSGI only.
    if all((os.getenv(key) or "").strip() for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_PATH")):
        return

    for wsgi_path in _find_pythonanywhere_wsgi_files():
        try:
            text = wsgi_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        loaded_any = False
        for match in _ENV_ASSIGN_RE.finditer(text):
            key = match.group("key")
            if key not in _NEEDED_KEYS:
                continue
            if (os.getenv(key) or "").strip():
                continue
            os.environ[key] = match.group("value")
            loaded_any = True

        if loaded_any and all((os.getenv(key) or "").strip() for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_PATH")):
            return


def _infer_pythonanywhere_base_url() -> str | None:
    if (os.getenv("TELEGRAM_WEBHOOK_BASE_URL") or "").strip():
        return None
    if (os.getenv("TELEGRAM_WEBHOOK_URL") or "").strip():
        return None

    candidates = _find_pythonanywhere_wsgi_files()
    if not candidates:
        return None

    best: Path | None = None
    for path in candidates:
        name = path.name
        if "pythonanywhere" in name:
            best = path
            break
    if best is None:
        best = candidates[0]

    name = best.name
    suffix = "_wsgi.py"
    if not name.endswith(suffix):
        return None

    host_part = name[: -len(suffix)]
    host = host_part.replace("_", ".")
    if not host:
        return None
    return f"https://{host}"


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
    _load_env_from_pythonanywhere_wsgi()
    inferred_base = _infer_pythonanywhere_base_url()
    if inferred_base:
        os.environ["TELEGRAM_WEBHOOK_BASE_URL"] = inferred_base
    asyncio.run(_set_webhook())


if __name__ == "__main__":
    main()
