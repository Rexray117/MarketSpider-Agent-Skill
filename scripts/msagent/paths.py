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
    """Root of the skill *content* (``config/``, ``scripts/``, ``tests/``).

    Two layouts have to work, because publishing flattens ``skill/*`` onto the
    repository root while the unpacked kit keeps the ``skill/`` level:

      * unpacked kit:      <kit>/skill/config/selectors.json
      * published / DSH:   <repo>/config/selectors.json

    Without this the whole main chain (markets / selftest / search / probe)
    dies with FileNotFoundError on the published layout, while ``doctor``
    still reports ok because it wraps the same lookup in a warning.
    """
    root = project_root()
    nested = root / "skill"
    if (nested / "config" / "selectors.json").is_file() or nested.is_dir():
        return nested
    return root


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
