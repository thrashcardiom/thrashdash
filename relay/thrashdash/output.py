"""Somewhere for the program's messages to go when it has no console.

The Windows program people double-click is built without a console window, and such a program has no standard output at all
(sys.stdout is None): a print() would be lost and a failure would leave nothing to read. Its messages go to thrashdash.log
in the data folder instead (only the latest run is kept), the same file the Linux launcher writes.
"""
import os
import sys

from .paths import user_data_dir


def ensure_output(log_path=None):
    """Give sys.stdout and sys.stderr somewhere to write if they do not exist. Returns the log file's path, or None if
    nothing needed doing. Never raises: if even the log cannot be opened, messages are dropped rather than crash."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    path = log_path or (user_data_dir() / "thrashdash.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(path, "w", encoding="utf-8", buffering=1)  # line by line, so the log is useful while it runs
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
        path = None
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return path
