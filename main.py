import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parent / "src"))

from transcript_python_bot.main import main  # noqa: E402


if __name__ == "__main__":
    main()
