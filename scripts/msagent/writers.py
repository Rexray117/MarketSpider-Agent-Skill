"""Result writers: json / jsonl / csv / xlsx / txt / sql / md.

All writers consume the same record dict and are intentionally synchronous
so a crash leaves a readable partial file.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

FIELDS = [
    "item_link",
    "item_name",
    "item_price",
    "item_image",
    "item_payment",
    "item_rates",
    "item_sales",
    "item_shop",
    "shop_link",
    "remarks",
]
CN_NAMES = {
    "item_link": "商品链接",
    "item_name": "商品名称",
    "item_price": "商品价格",
    "item_image": "商品图片",
    "item_payment": "已付款人数",
    "item_rates": "已评价数",
    "item_sales": "商品销量",
    "item_shop": "店铺名称",
    "shop_link": "店铺链接",
    "remarks": "备注",
}
CN_HEADERS = [CN_NAMES[f] for f in FIELDS]

# Extra, agent-friendly columns kept in JSON/JSONL only.
META_FIELDS = ["market", "keyword", "page", "captured_at", "position"]


def _stamp() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def _sql_quote(value: Any) -> str:
    return "'" + str(value if value is not None else "").replace("'", "''") + "'"


class BaseWriter:
    ext = ""

    def __init__(self, out_dir: Path, keyword: str, market: str, run_id: str):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        safe_kw = "".join(ch for ch in keyword if ch not in '\\/:*?"<>|').strip() or "keyword"
        self.path = self.out_dir / f"{safe_kw}-{market}-{run_id}.{self.ext}"
        self.count = 0

    def add(self, record: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class JsonWriter(BaseWriter):
    ext = "json"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows: list[dict] = []

    def add(self, record: dict) -> None:
        self.rows.append({**{f: record.get(f, "") for f in FIELDS},
                          **{m: record.get(m) for m in META_FIELDS if record.get(m) is not None}})
        self.count += 1
        if self.count % 20 == 0:
            self._flush()

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self.rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self) -> None:
        self._flush()


class JsonlWriter(BaseWriter):
    ext = "jsonl"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fh = open(self.path, "a", encoding="utf-8")

    def add(self, record: dict) -> None:
        payload = {**{f: record.get(f, "") for f in FIELDS},
                   **{m: record.get(m) for m in META_FIELDS if record.get(m) is not None}}
        self._fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except (OSError, ValueError):
            pass


class CsvWriter(BaseWriter):
    ext = "csv"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fh = open(self.path, "a", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=CN_HEADERS)
        self._writer.writeheader()
        self._fh.flush()

    def add(self, record: dict) -> None:
        self._writer.writerow({CN_NAMES[f]: record.get(f, "") for f in FIELDS})
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except (OSError, ValueError):
            pass


class TxtWriter(BaseWriter):
    ext = "txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fh = open(self.path, "a", encoding="utf-8")
        self._fh.write("\t".join(CN_HEADERS) + "\n")
        self._fh.flush()

    def add(self, record: dict) -> None:
        self._fh.write("\t".join(str(record.get(f, "")).replace("\t", " ") for f in FIELDS) + "\n")
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except (OSError, ValueError):
            pass


class SqlWriter(BaseWriter):
    ext = "sql"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fh = open(self.path, "a", encoding="utf-8")
        self.table = "market_spider_data"
        self._fh.write(
            f"""-- MarketSpider Agent export (MySQL / MariaDB)
CREATE TABLE IF NOT EXISTS {self.table} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    item_link TEXT,
    item_name TEXT,
    item_price VARCHAR(50),
    item_image TEXT,
    item_payment VARCHAR(50),
    item_rates VARCHAR(50),
    item_sales VARCHAR(50),
    item_shop VARCHAR(255),
    shop_link TEXT,
    remarks TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

"""
        )
        self._fh.flush()

    def add(self, record: dict) -> None:
        values = ", ".join(_sql_quote(record.get(f, "")) for f in FIELDS)
        self._fh.write(f"INSERT INTO {self.table} ({', '.join(FIELDS)}) VALUES ({values});\n")
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except (OSError, ValueError):
            pass


class SqliteWriter(BaseWriter):
    """Sidecar SQLite database - immediately queryable, unlike the .sql script."""

    ext = "sqlite"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_link TEXT, item_name TEXT, item_price TEXT, item_image TEXT,
                item_payment TEXT, item_rates TEXT, item_sales TEXT, item_shop TEXT,
                shop_link TEXT, remarks TEXT, market TEXT, keyword TEXT, page INTEGER,
                captured_at TEXT
            )"""
        )
        self.conn.commit()

    def add(self, record: dict) -> None:
        cols = FIELDS + ["market", "keyword", "page", "captured_at"]
        values = [str(record.get(c, "") if record.get(c) is not None else "") for c in cols]
        self.conn.execute(f"INSERT INTO items ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", values)
        self.conn.commit()
        self.count += 1

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except sqlite3.Error:
            pass


class XlsxWriter(BaseWriter):
    ext = "xlsx"
    _EXTRA_HEADERS = ["平台", "关键词", "页码"]
    _EXTRA_KEYS = ["market", "keyword", "page"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            import openpyxl
            from openpyxl.utils import get_column_letter
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 openpyxl，请先运行 ms env install") from exc
        self._openpyxl = openpyxl
        self._get_col = get_column_letter
        self.wb = openpyxl.Workbook()
        self.ws = self.wb.active
        self.ws.title = "MarketSpider Data"
        headers = CN_HEADERS + self._EXTRA_HEADERS
        for idx, header in enumerate(headers, 1):
            self.ws[f"{get_column_letter(idx)}1"] = header
        self.row = 2

    def add(self, record: dict) -> None:
        for col, key in enumerate(FIELDS, 1):
            self.ws[f"{self._get_col(col)}{self.row}"] = record.get(key, "")
        base = len(FIELDS)
        for offset, key in enumerate(self._EXTRA_KEYS, 1):
            self.ws[f"{self._get_col(base + offset)}{self.row}"] = record.get(key, "")
        self.row += 1
        self.count += 1
        if self.count % 20 == 0:
            self._save()

    def _save(self) -> None:
        try:
            self.wb.save(self.path)
        except OSError:
            pass

    def close(self) -> None:
        self._save()


class MdWriter(BaseWriter):
    ext = "md"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows: list[dict] = []

    def add(self, record: dict) -> None:
        self.rows.append(record)
        self.count += 1

    def close(self) -> None:
        lines = [
            f"# {self.path.stem}",
            "",
            f"- 平台: {self.rows[0].get('market') if self.rows else ''}",
            f"- 记录数: {len(self.rows)}",
            "",
            "| # | 商品名称 | 价格 | 店铺 | 链接 |",
            "|---|---|---|---|---|",
        ]
        for i, row in enumerate(self.rows, 1):
            name = str(row.get("item_name", "")).replace("|", "\\|")[:60]
            price = str(row.get("item_price", "")).replace("|", "\\|")[:20]
            shop = str(row.get("item_shop", "")).replace("|", "\\|")[:30]
            link = str(row.get("item_link", ""))
            lines.append(f"| {i} | {name} | {price} | {shop} | {link} |")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


WRITERS = {
    "json": JsonWriter,
    "jsonl": JsonlWriter,
    "csv": CsvWriter,
    "xlsx": XlsxWriter,
    "txt": TxtWriter,
    "sql": SqlWriter,
    "sqlite": SqliteWriter,
    "md": MdWriter,
}


class WriterSet:
    def __init__(self, out_dir: str | Path, keyword: str, market: str, formats: Iterable[str],
                 run_id: str | None = None):
        self.run_id = run_id or _stamp()
        self.writers: dict[str, BaseWriter] = {}
        self.errors: list[str] = []
        for fmt in formats:
            cls = WRITERS.get(fmt)
            if cls is None:
                self.errors.append(f"未知输出格式: {fmt}")
                continue
            try:
                self.writers[fmt] = cls(Path(out_dir), keyword, market, self.run_id)
            except Exception as exc:  # e.g. openpyxl missing
                self.errors.append(f"{fmt} 输出初始化失败: {exc}")

    def add(self, record: dict) -> int:
        added = 0
        for fmt, writer in list(self.writers.items()):
            try:
                writer.add(record)
                added += 1
            except Exception as exc:
                self.errors.append(f"{fmt} 写入失败: {exc}")
                self.writers.pop(fmt, None)
        return added

    def close(self) -> list[dict]:
        artifacts = []
        for fmt, writer in self.writers.items():
            try:
                writer.close()
            except Exception as exc:
                self.errors.append(f"{fmt} 关闭失败: {exc}")
            try:
                artifacts.append({
                    "format": fmt,
                    "path": str(writer.path),
                    "rows": writer.count,
                    "bytes": writer.path.stat().st_size if writer.path.exists() else 0,
                })
            except OSError:
                pass
        self.writers.clear()
        return artifacts
