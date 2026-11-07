"""Starts the real app on the made-up sample folder, with a fresh throw-away database (for the showcase screenshots).

Run: uv run python docs/showcase/showcase_demo.py [port]
Everything it writes goes to data/showcase/ (gitignored). The Windows folder window is replaced by a fixed answer so
the import wizard can be clicked through without it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import uvicorn

from scriptmaker.app import create_app
from scriptmaker.config import PROJECT_ROOT, Settings

sys.path.insert(0, str(Path(__file__).parent))
from make_sample_material import build  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8780
root = PROJECT_ROOT / "data" / "showcase"
shutil.rmtree(root, ignore_errors=True)
sample = root / "Racunalne_mreze_usmeni"
build(sample)

# env_file points at an empty file so the demo never reads the real .env (API keys)
app = create_app(Settings(data_dir=root / "app-data", env_file=root / "demo.env"))
app.state.choose_folder = lambda: sample
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
