"""Path resolution that never depends on the current working directory."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Root of the unpacked MarketSpider Agent Kit."""
    env = os.environ.get("MARKETSPIDER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    # <root>/skill/scripts/msagent/paths.py
    if len(here.parents) >= 4 and here.parents[2].name == "skill":
        return here.parents[3]
    return here.parents[2]


def skill_root() -> Path:
    return project_root() / "skill"


def default_data_dir() -> Path:
    env = os.environ.get("MARKETSPIDER_DATA")
    if env:
        return Path(env).expanduser().resolve()
    # Prefer the project data dir; fall back to the user profile when the
    # project directory is read-only (e.g. installed under Program Files).
    candidate = project_root() / "data"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return candidate
    except OSError:
        fallback = Path.home() / ".marketspider" / "data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


class Layout:
    """Directory layout for cookies, results, logs."""

    def __init__(self, data_dir: str | os.PathLike | None = None):
        self.root = Path(data_dir).expanduser().resolve() if data_dir else default_data_dir()
        self.cookie = self.root / "cookie"
        self.result = self.root / "result"
        self.logs = self.root / "logs"
        self.state = self.root / "state"
        self.config_file = self.root / "config.json"

    def ensure(self) -> "Layout":
        for d in (self.root, self.cookie, self.result, self.logs, self.state):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "cookie": str(self.cookie),
            "result": str(self.result),
            "logs": str(self.logs),
            "state": str(self.state),
            "config": str(self.config_file),
        }
