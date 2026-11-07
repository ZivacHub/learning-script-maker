"""Runtime settings. Everything local: binds 127.0.0.1, data under `data/` (gitignored)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Hostnames a browser may legitimately use to reach this local app. Anything else in the
# Host header (e.g. an attacker's DNS name rebound to 127.0.0.1) is rejected.
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = field(default_factory=lambda: _env_path("SM_DATA_DIR", PROJECT_ROOT / "data"))
    allowed_hostnames: frozenset[str] = LOCAL_HOSTNAMES
    dev: bool = field(default_factory=lambda: os.environ.get("SM_DEV") == "1")  # developer-only pages
    # the Gemini key lives here (D50)
    env_file: Path = field(default_factory=lambda: _env_path("SM_ENV_FILE", PROJECT_ROOT / ".env"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "scriptmaker.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def pages_dir(self) -> Path:
        return self.data_dir / "pages"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def tessdata_dir(self) -> Path:
        return self.data_dir / "tessdata"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.pages_dir, self.jobs_dir, self.tessdata_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    port = int(os.environ.get("SM_PORT", "8765"))
    return Settings(port=port)
