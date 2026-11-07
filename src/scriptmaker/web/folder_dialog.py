"""The normal Windows "choose a folder" window, opened on this PC for the import wizard.

It runs in a separate Python process: a window needs its own main thread, and the web server must not freeze if the
window is left open. The answer is a folder path or None (window closed without choosing). Nothing is sent anywhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 600

_SCRIPT = """
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
chosen = filedialog.askdirectory(title="Odaberi mapu s gradivom", mustexist=True)
root.destroy()
sys.stdout.buffer.write(chosen.encode("utf-8"))
"""


def choose_folder() -> Path | None:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        done = subprocess.run(  # noqa: S603 - fixed script, our own interpreter
            [sys.executable, "-c", _SCRIPT],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = done.stdout.decode("utf-8", errors="replace").strip()
    return Path(text) if text and done.returncode == 0 else None
