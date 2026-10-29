"""File logger that also keeps structured warnings for the JSON envelope."""

from __future__ import annotations

import time
from pathlib import Path


class RunLog:
    def __init__(self, log_dir: str | Path, component: str = "marketspider"):
        self.component = component
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{time.strftime('%Y_%m_%d')}.log"
        self.warnings: list[str] = []
        self._fh = open(self.path, "a", encoding="utf-8")
        self.info(f"run start pid={__import__('os').getpid()}")

    def _write(self, level: str, message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{level}] {stamp} [{self.component}] {message}"
        try:
            self._fh.write(line + "\n")
            self._fh.flush()
        except (OSError, ValueError):
            pass

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warn(self, message: str) -> None:
        self._write("WARN", message)
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)
        self.warnings.append(message)

    def close(self) -> None:
        try:
            self._fh.close()
        except (OSError, ValueError):
            pass
