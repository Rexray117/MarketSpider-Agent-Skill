"""Market adapters (JD / Taobao / 1688) with selector packs.

Upstream kept selectors inline in each spider script, hard-coded to one DOM
snapshot. Here selectors live in ``skill/config/selectors.json`` so a break
in the site can be repaired without touching code, and every adapter has a
degraded link-scan mode plus a probe report.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .browser import BrowserSession, SessionError
from .logutil import RunLog
from .paths import skill_root

PRICE_RE = re.compile(r"(\d+(?:\.\d{1,2})?)")
SPACE_RE = re.compile(r"\s+")


def _load_pack() -> dict:
    path = skill_root() / "config" / "selectors.json"
    if not path.exists():
        raise SessionError(f"缺少选择器配置: {path}", "重新解压 skill 包或检查 MARKETSPIDER_HOME")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def normalize_price(raw: str) -> str:
    raw = (raw or "").replace("¥", "").replace("￥", "").replace(",", "").strip()
    found = PRICE_RE.search(raw)
    return found.group(1) if found else ""


def clean_text(raw: str) -> str:
    return SPACE_RE.sub(" ", (raw or "").replace("\u3000", " ")).strip()


class Market:
    slug = ""

    def __init__(self, pack: dict | None = None):
        self.pack = pack or _load_pack()[self.slug]

    # ---- metadata ----
    @property
    def label(self) -> str:
        return self.pack.get("label", self.slug)

    @property
    def home(self) -> str:
        return self.pack["home"]

    @property
    def login_home(self) -> str:
        return self.pack.get("login_home", self.home)

    # ---- urls ----
    def search_url(self, keyword: str, page: int = 1) -> str:
        mode = self.pack.get("page_mode", "direct")
        kw = quote(keyword, safe="")
        if mode == "double":
            param = 2 * page - 1
        elif mode == "offset44":
            param = (page - 1) * 44
        elif mode == "offset60":
            param = (page - 1) * 60
        else:
            param = page
        return self.pack["search_url"].format(kw=kw, page=page, page_param=param)

    # ---- page helpers ----
    def total_pages(self, session: BrowserSession) -> int | None:
        for sel in self.pack.get("total_pages", []):
            el = session.find(sel)
            if el is None:
                continue
            raw = session.text(el)
            parts = [p for p in re.split(r"[/／总共页]", raw) if p.strip()]
            for part in reversed(parts):
                m = re.search(r"\d+", part)
                if m:
                    try:
                        value = int(m.group(0))
                        if value > 0:
                            return value
                    except ValueError:
                        continue
        return None

    def cards(self, session: BrowserSession) -> list:
        for sel in self.pack.get("cards", []):
            found = session.find_all(sel)
            if found:
                return found
        return []

    # ---- field extraction ----
    def _field(self, session: BrowserSession, card: Any, field: str) -> str:
        specs = self.pack.get("fields", {}).get(field, [])
        for spec in specs:
            el = session.find(spec["sel"], card)
            if el is None:
                continue
            if spec.get("attr"):
                raw = session.attr(el, spec["attr"])
            else:
                raw = session.text(el)
            raw = clean_text(raw)
            if raw:
                return raw
        return ""

    def parse_card(self, session: BrowserSession, card: Any) -> dict | None:
        record = {key: self._field(session, card, key) for key in
                  ("item_link", "item_name", "item_price", "item_image",
                   "item_payment", "item_rates", "item_sales", "item_shop", "shop_link")}
        record["item_link"] = normalize_url(record["item_link"])
        record["shop_link"] = normalize_url(record["shop_link"])
        record["item_price"] = normalize_price(record["item_price"]) or record["item_price"]

        # --- degraded heuristics: keep partial rows instead of dropping them ---
        if not record["item_name"]:
            img = session.find("img", card)
            record["item_name"] = clean_text(session.attr(img, "alt"))
        if not record["item_link"]:
            link, _ = session.find_any(self.pack.get("link_fallback", ["a[href]"]), card)
            record["item_link"] = normalize_url(session.attr(link, "href"))
        if not record["item_price"]:
            record["item_price"] = normalize_price(session.text(card)[:120])
        if not record["item_name"]:
            return None
        return record

    def collect(self, session: BrowserSession, log: RunLog, keyword: str,
                page: int) -> tuple[list[dict], dict]:
        cards = self.cards(session)
        stats = {"page": page, "cards": len(cards), "parsed": 0, "skipped": 0, "mode": "selector"}
        records: list[dict] = []
        if not cards:
            log.warn(f"第 {page} 页未匹配到商品卡片，尝试降级链接扫描")
            stats["mode"] = "link-scan"
            records = self.scan_links(session, keyword, page, log)
            stats["parsed"] = len(records)
            return records, stats

        for index, card in enumerate(cards):
            record = self.parse_card(session, card)
            if record is None:
                stats["skipped"] += 1
                continue
            pattern = self.pack.get("item_link_pattern")
            if pattern and record.get("item_link") and not re.search(pattern, record["item_link"]):
                # ads / recommendations usually point elsewhere; keep but mark
                record["remarks"] = (record.get("remarks") or "") + "非商品链接"
            record["market"] = self.slug
            record["keyword"] = keyword
            record["page"] = page
            record["position"] = index + 1
            record["captured_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            records.append(record)
            stats["parsed"] += 1
        return records, stats

    def scan_links(self, session: BrowserSession, keyword: str, page: int,
                   log: RunLog) -> list[dict]:
        """Last-resort extraction: any anchor whose href matches the item pattern."""
        pattern = self.pack.get("item_link_pattern", "")
        if not pattern:
            return []
        regex = re.compile(pattern)
        anchors = session.find_all("a[href]")
        records: list[dict] = []
        seen: set[str] = set()
        for anchor in anchors:
            href = normalize_url(session.attr(anchor, "href"))
            if not href or not regex.search(href) or href in seen:
                continue
            seen.add(href)
            name = clean_text(session.text(anchor))
            if not name:
                img = session.find("img", anchor)
                name = clean_text(session.attr(img, "alt"))
            container_text = ""
            try:
                container = anchor.find_element("xpath", "./ancestor::*[self::li or self::div][1]")
                container_text = clean_text(session.text(container))
                img = container.find_element("css selector", "img")
            except Exception:
                container = None
                img = None
            records.append({
                "item_link": href,
                "item_name": name or container_text[:60],
                "item_price": normalize_price(container_text[:160]),
                "item_image": normalize_url(session.attr(img, "src")) if img is not None else "",
                "item_payment": "",
                "item_rates": "",
                "item_sales": "",
                "item_shop": "",
                "shop_link": "",
                "remarks": "降级提取",
                "market": self.slug,
                "keyword": keyword,
                "page": page,
                "position": len(records) + 1,
                "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        log.info(f"降级扫描得到 {len(records)} 条链接")
        return records

    def probe(self, session: BrowserSession) -> dict:
        report = {"market": self.slug, "url": session.url, "title": session.title,
                  "cards": {}, "fields": {}, "blocked": session.blocked(),
                  "login_wall": session.login_wall()}
        for sel in self.pack.get("cards", []):
            report["cards"][sel] = session.count(sel)
        cards = self.cards(session)
        if cards:
            sample = cards[:5]
            for field in self.pack.get("fields", {}):
                hits = sum(1 for card in sample if self._field(session, card, field))
                report["fields"][field] = f"{hits}/{len(sample)}"
            report["sample"] = [self.parse_card(session, card) for card in sample[:2]]
        report["total_pages"] = self.total_pages(session)
        return report


class JD(Market):
    slug = "jd"


class Taobao(Market):
    slug = "taobao"


class Alibaba1688(Market):
    slug = "1688"


MARKETS: dict[str, type[Market]] = {
    "jd": JD,
    "taobao": Taobao,
    "1688": Alibaba1688,
}


def get_market(slug: str) -> Market:
    cls = MARKETS.get(slug)
    if cls is None:
        raise SessionError(f"未知平台: {slug}", f"可选: {', '.join(MARKETS)}")
    return cls()
