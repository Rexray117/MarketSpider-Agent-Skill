"""Runtime configuration with safe defaults and on-disk persistence."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILENAME = "config.json"

DEFAULTS: dict = {
    "browser": "chrome",          # chrome | edge | firefox
    "headless": False,            # visible browser by default: helps with captcha
    "output_format": ["json", "csv"],
    "driver_path": None,
    "user_agent": None,
    "proxy": None,
    "implicit_wait": 8,
    "page_load_timeout": 45,
    "page_wait": 6,
    "page_delay": [2, 6],        # random seconds between pages (anti-bot)
    "max_pages": 3,
    "max_items": 200,
    "scroll_rounds": 8,
    "profile_dir": None,
}

BROWSERS = ("chrome", "edge", "firefox")
FORMATS = ("json", "jsonl", "csv", "xlsx", "txt", "sql", "sqlite", "md")


def load_config(path: str | Path) -> dict:
    """Load config, merging user values over defaults; never raise on bad file."""
    cfg = dict(DEFAULTS)
    p = Path(path)
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if key in DEFAULTS:
                        cfg[key] = value
                    else:
                        cfg[key] = value  # keep unknown keys for forward compat
        except (json.JSONDecodeError, OSError):
            pass
    fmt = cfg.get("output_format")
    if isinstance(fmt, str):
        cfg["output_format"] = [item.strip() for item in fmt.split(",") if item.strip()]
    delay = cfg.get("page_delay")
    if isinstance(delay, str) and "," in delay:
        try:
            cfg["page_delay"] = [int(x) for x in delay.split(",", 1)]
        except ValueError:
            pass
    return cfg


def save_config(path: str | Path, cfg: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if v is not None})
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def validate(cfg: dict) -> list[str]:
    warnings: list[str] = []
    fmt = cfg.get("output_format")
    if isinstance(fmt, str):
        fmt = [item.strip() for item in fmt.split(",") if item.strip()]
        cfg["output_format"] = fmt
    if cfg.get("browser") not in BROWSERS:
        warnings.append(f"browser={cfg.get('browser')!r} 不受支持，将回退到 chrome")
    formats = cfg.get("output_format") or []
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        warnings.append(f"output_format 含未知格式 {unknown}，将被忽略")
    if not [f for f in formats if f in FORMATS]:
        warnings.append("output_format 为空，已回退到 ['json','csv']")
        cfg["output_format"] = ["json", "csv"]
    delay = cfg.get("page_delay")
    if not (isinstance(delay, (list, tuple)) and len(delay) == 2 and delay[0] <= delay[1]):
        warnings.append("page_delay 非法，已回退到 [2, 6]")
        cfg["page_delay"] = [2, 6]
    for key in ("max_pages", "max_items"):
        try:
            if int(cfg.get(key)) < 1:
                raise ValueError
        except (TypeError, ValueError):
            cfg[key] = DEFAULTS[key]
            warnings.append(f"{key} 非法，已回退到 {DEFAULTS[key]}")
    return warnings
