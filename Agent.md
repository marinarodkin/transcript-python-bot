Agent Notes
===========

Project
-------
This repository currently contains a Python package scaffold under `src/transcript_python_bot/`.

Setup
-----
- Python 3.10+ recommended.
- Create a virtualenv and install requirements:
  - `python -m venv .venv`
  - `source .venv/bin/activate`
  - `pip install -r requirements.txt`
  - Optional (recommended for clean imports): `pip install -e .`

Run
---
- Telegram bot entry point: `python3 -m transcript_python_bot` (polling)
- CLI entry point: `python3 -m transcript_python_bot.cli` (or `python3 cli-bot.py`)

Examples:
- `python3 cli-bot.py`
- `python3 -m transcript_python_bot`

Tests
-----
- No tests configured yet.

Notes
-----
- Fill in this file with concrete run/test commands once the project gains dependencies or a CLI.
