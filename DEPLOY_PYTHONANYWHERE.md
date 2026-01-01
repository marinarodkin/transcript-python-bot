## PythonAnywhere deploy (webhook)

This project uses `python-telegram-bot` and should be deployed as a **PythonAnywhere Web app** (WSGI) with a Telegram webhook.

### 1) Add env vars on PythonAnywhere

Minimum:
- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `TELEGRAM_WEBHOOK_PATH` (example: `telegram/<random-secret>`)

Recommended:
- `TELEGRAM_WEBHOOK_SECRET_TOKEN` (Telegram header verification)
- Notion vars if you use Notion integration

### 2) Install dependencies

In a PythonAnywhere console (virtualenv activated):
- `pip install -r requirements.txt`

### 3) Configure the Web app WSGI entry

In the PythonAnywhere Web tab, edit the WSGI file and point it at the Flask app exported as `application`:

```python
import sys
from pathlib import Path

project_root = Path("/home/<your-user>/<your-project-folder>")
sys.path.insert(0, str(project_root / "src"))

from transcript_python_bot.wsgi import application  # noqa
```

### 4) Set the Telegram webhook

Set either:
- `TELEGRAM_WEBHOOK_URL="https://<your-user>.pythonanywhere.com/<TELEGRAM_WEBHOOK_PATH>"`

Then run:
- `python3 -m transcript_python_bot.set_webhook`

### Switch back to polling (local dev)

If you previously set a webhook, delete it before running polling locally:
- `python3 -m transcript_python_bot.delete_webhook`

### 5) Remove polling usage

Do not run `python main.py` on PythonAnywhere (that starts polling). The bot will work via webhook through the web app.
