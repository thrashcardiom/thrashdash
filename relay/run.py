"""Convenience launcher: `python relay/run.py [options]` works from any directory without installing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from thrashdash.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
