"""Agent-facing CLI. Every subcommand prints exactly one JSON envelope.

Exit codes are stable so an orchestrating agent can branch on them:
    0 ok | 2 usage | 3 environment | 4 driver | 5 auth | 6 blocked
    7 no data / parse | 8 io | 1 unexpected
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .browser import AuthRequired, BlockedBySite, BrowserSession, DriverUnavailable, SessionError
from .config import DEFAULTS, FORMATS, load_config, save_config, validate
from .logutil import RunLog
from .markets import MARKETS, get_market, normalize_price
from .paths import Layout, default_data_dir, project_root, skill_root
from .writers import WRITERS, WriterSet


class MsError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = "", exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.exit_code = exit_code


EXIT_FOR = {
    "usage": 2,
    "environment": 3,
    "driver": 4,
    "auth": 5,
    "blocked": 6,
    "no_data": 7,
    "io": 8,
}


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def envelope(command: str, data: dict, warnings: list[str] | None = None,
             artifacts: list[dict] | None = None, next_actions: list[str] | None = None,
             log_path: str | None = None) -> dict:
    return {
        "ok": True,
        "command": command,
        "version": __version__,
        "data": data,
        "warnings": warnings or [],
        "artifacts": artifacts or [],
        "next_actions": next_actions or [],
        "log": log_path,
        "duration_sec": None,
    }


def fail(command: str, code: str, message: str, hint: str = "", warnings: list[str] | None = None,
         log_path: str | None = None) -> dict:
    return {
        "ok": False,
        "command": command,
        "version": __version__,
        "error": {"code": code, "message": message, "hint": hint},
        "warnings": warnings or [],
        "artifacts": [],
        "next_actions": [hint] if hint else [],
        "log": log_path,
    }


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #

BROWSER_PATHS = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/microsoft-edge",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        "/Applications/Firefox.app/Contents/MacOS/firefox",
        "/usr/bin/firefox",
    ],
}


def find_browser(name: str) -> str | None:
    for path in BROWSER_PATHS.get(name, []):
        if Path(path).exists():
            return path
    exe = {"chrome": "chrome", "edge": "msedge", "firefox": "firefox"}[name]
    found = shutil.which(exe)
    return found


def package_versions() -> dict:
    versions = {}
    for module in ("selenium", "openpyxl", "requests"):
        try:
            mod = __import__(module)
            versions[module] = getattr(mod, "__version__", "unknown")
        except ImportError:
            versions[module] = None
    return versions


def cmd_doctor(args) -> dict:
    layout = Layout(args.data_dir).ensure()
    cfg = load_config(layout.config_file)
    versions = package_versions()
    browsers = {name: find_browser(name) for name in ("chrome", "edge", "firefox")}
    checks = []

    def check(name: str, ok: bool, detail: str, fatal: bool = True) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "fatal": fatal})

    check("python", sys.version_info >= (3, 9), f"{sys.version.split()[0]} ({sys.executable})")
    for pkg in ("selenium", "openpyxl", "requests"):
        check(f"pkg:{pkg}", bool(versions.get(pkg)), versions.get(pkg) or "未安装 -> ms env install")
    check("browser", any(browsers.values()),
          ", ".join(f"{k}={v}" for k, v in browsers.items() if v) or "未找到 Chrome/Edge/Firefox")
    try:
        probe = layout.root / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        check("data_dir", True, str(layout.root))
    except OSError as exc:
        check("data_dir", False, f"不可写: {exc}")
    selectors = skill_root() / "config" / "selectors.json"
    try:
        pack = json.loads(selectors.read_text(encoding="utf-8")) if selectors.exists() else {}
    except json.JSONDecodeError as exc:
        pack = {}
        check("selectors", False, f"JSON 解析失败: {exc}")
    else:
        check("selectors", all(m in pack for m in MARKETS), str(selectors))
    skill_installed = (Path.home() / ".dsh" / "skills" / "marketspider").exists()
    check("dsh_skill", skill_installed, "~/.dsh/skills/marketspider" if skill_installed else "未链接到 DSH 技能目录（可选）", fatal=False)
    from .browser import bundled_driver

    drivers = {name: bundled_driver(name) for name in ("chrome", "edge", "firefox")}
    check("driver", any(drivers.values()) or bool(cfg.get("driver_path")),
          ", ".join(f"{k}={v}" for k, v in drivers.items() if v) or "未捆绑 driver，将交给 Selenium Manager 自动下载（需要访问 storage.googleapis.com）",
          fatal=False)

    driver_ok = None
    if args.launch:
        try:
            session = BrowserSession.create(cfg, RunLog(layout.logs, "doctor"), layout.cookie,
                                            headless=True, browser=args.browser or cfg["browser"])
            session.goto("about:blank", wait=0)
            driver_ok = True
            session.quit()
        except DriverUnavailable as exc:
            driver_ok = False
            check("webdriver", False, f"{exc} | {exc.hint}")
        else:
            check("webdriver", True, "headless 启动成功")
    fatal_failed = [c for c in checks if not c["ok"] and c["fatal"]]
    data = {
        "checks": checks,
        "python": sys.executable,
        "packages": versions,
        "browsers": browsers,
        "drivers": drivers,
        "config": cfg,
        "paths": layout.as_dict(),
        "project_root": str(project_root()),
        "skill_root": str(skill_root()),
        "driver_tested": driver_ok,
        "ready": not fatal_failed,
    }
    warnings = [] if not args.launch else []
    return envelope("doctor", data, warnings=warnings,
                    next_actions=[] if not fatal_failed else [
                        "运行 ms env install 安装 selenium/openpyxl/requests",
                        "安装 Chrome 或 Edge 浏览器",
                    ])


def cmd_env(args) -> dict:
    layout = Layout(args.data_dir)
    versions = package_versions()
    if args.action == "check":
        missing = [k for k, v in versions.items() if not v]
        data = {"packages": versions, "missing": missing, "ready": not missing, "python": sys.executable}
        if missing:
            raise MsError("environment", f"缺少依赖: {', '.join(missing)}", "运行 ms env install", 3)
        return envelope("env check", data)

    if args.action == "install":
        req = skill_root() / "requirements.txt"
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
        if args.index_url:
            cmd += ["-i", args.index_url]
        cmd += ["-r", str(req)] if req.exists() else ["selenium", "openpyxl", "requests"]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        after = package_versions()
        data = {
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-2000:],
            "packages": after,
        }
        if proc.returncode != 0:
            raise MsError("environment", "pip 安装失败", f"检查网络或使用 -i 指定镜像。stderr: {proc.stderr[-400:]}", 3)
        return envelope("env install", data, next_actions=["运行 ms doctor --launch 验证浏览器与 driver"])

    raise MsError("usage", f"未知 env 动作: {args.action}", "可用: check | install", 2)


# --------------------------------------------------------------------------- #
# config / markets
# --------------------------------------------------------------------------- #

def cmd_config(args) -> dict:
    layout = Layout(args.data_dir).ensure()
    cfg = load_config(layout.config_file)
    if args.action == "show":
        return envelope("config show", {"config": cfg, "path": str(layout.config_file),
                                        "defaults": DEFAULTS})
    if args.action == "set":
        updates = {}
        for pair in args.pairs or []:
            if "=" not in pair:
                raise MsError("usage", f"格式应为 key=value: {pair}", "", 2)
            key, value = pair.split("=", 1)
            updates[key.strip()] = _coerce(value.strip())
        if not updates:
            raise MsError("usage", "至少提供一个 key=value", "", 2)
        cfg.update(updates)
        warnings = validate(cfg)
        path = save_config(layout.config_file, cfg)
        return envelope("config set", {"config": cfg, "path": str(path)}, warnings=warnings)
    raise MsError("usage", f"未知 config 动作: {args.action}", "可用: show | set", 2)


def _coerce(value: str):
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.lower() in ("null", "none"):
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def cmd_markets(args) -> dict:
    pack = json.loads((skill_root() / "config" / "selectors.json").read_text(encoding="utf-8"))
    data = []
    for slug in MARKETS:
        info = pack.get(slug, {})
        data.append({
            "slug": slug,
            "label": info.get("label", slug),
            "home": info.get("home"),
            "search_url_template": info.get("search_url"),
            "cards": len(info.get("cards", [])),
            "fields": sorted(info.get("fields", {}).keys()),
            "notes": info.get("notes", ""),
        })
    return envelope("markets", {"markets": data})


# --------------------------------------------------------------------------- #
# cookie
# --------------------------------------------------------------------------- #

def cmd_cookie(args) -> dict:
    layout = Layout(args.data_dir).ensure()
    cfg = load_config(layout.config_file)
    market = get_market(args.market)
    log = RunLog(layout.logs, f"cookie-{market.slug}")

    if args.import_file:
        src = Path(args.import_file).expanduser().resolve()
        if not src.exists():
            raise MsError("io", f"cookie 文件不存在: {src}", "", 8)
        data = json.loads(src.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("cookies", data.get("data", []))
        if not isinstance(data, list) or not data:
            raise MsError("usage", "cookie 文件应为非空 JSON 数组（浏览器导出的 cookies）", "", 2)
        dest = layout.cookie / f"{market.slug}.cookie"
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"imported {len(data)} cookies -> {dest}")
        log.close()
        return envelope("cookie import", {"market": market.slug, "path": str(dest), "cookies": len(data)},
                        next_actions=["ms cookie check --market " + market.slug,
                                      f"ms search --market {market.slug} --keyword 关键词"])

    if args.action == "check":
        session = BrowserSession.create(cfg, log, layout.cookie, headless=True, browser=args.browser)
        try:
            session.goto(market.home, wait=1.5)
            applied = session.load_cookie(market.slug) if session.cookie_exists(market.slug) else False
            session.goto(market.home, wait=1.5)
            data = {
                "market": market.slug,
                "cookie_file": session.cookie_exists(market.slug),
                "cookie_applied": applied,
                "login_wall": session.login_wall(),
                "blocked": session.blocked(),
                "url": session.url,
                "cookies": session.cookie_summary(),
            }
        finally:
            session.quit()
            log.close()
        ok = data["cookie_file"] and not data["login_wall"]
        return envelope("cookie check", data, warnings=[] if ok else ["未检测到有效登录态"],
                        next_actions=[] if ok else [f"ms cookie --market {market.slug} --wait 180，然后由人工在浏览器内登录"])

    # acquire
    session = BrowserSession.create(cfg, log, layout.cookie, headless=False, browser=args.browser)
    wait = max(30, int(args.wait))
    try:
        session.goto(market.login_home, wait=2)
        started = time.time()
        # Poll instead of blind sleeping: stop early once a login cookie shows up.
        login_markers = {"taobao": ("_tb_token_", "cookie2", "sgcookie"),
                         "jd": ("thor", "pin", "pt_key"),
                         "1688": ("__cn_logon__", "cookie2", "_tb_token_")}.get(market.slug, ())
        deadline = started + wait
        cookie_seen = False
        while time.time() < deadline:
            cookies = {c.get("name", "") for c in session.driver.get_cookies()}
            if login_markers and cookies.intersection(login_markers):
                cookie_seen = True
                break
            if session.login_wall():
                time.sleep(1)
                continue
            time.sleep(1)
        session.goto(market.login_home, wait=1)
        path = session.save_cookie(market.slug)
        data = {
            "market": market.slug,
            "path": str(path),
            "cookies": session.cookie_summary(),
            "login_marker_seen": cookie_seen,
            "waited_sec": round(time.time() - started, 1),
            "url": session.url,
        }
    finally:
        session.quit()
        log.close()
    warnings = [] if data["login_marker_seen"] else [
        "未检测到登录态 Cookie；请确认已在弹出的浏览器中完成登录，然后重试或直接运行 search（可能结果缺失）"]
    return envelope("cookie acquire", data, warnings=warnings,
                    next_actions=["ms cookie check --market " + market.slug,
                                  "ms search --market " + market.slug + " --keyword 关键词 --pages 1"])


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #

def _parse_page_range(args) -> tuple[int, int]:
    if args.pages:
        raw = str(args.pages)
        if "-" in raw:
            start, end = raw.split("-", 1)
            return max(1, int(start)), max(1, int(end))
        value = max(1, int(raw))
        return 1, value
    return max(1, args.start_page), max(1, args.end_page)


def cmd_search(args) -> dict:
    started = time.time()
    layout = Layout(args.data_dir).ensure()
    cfg = load_config(layout.config_file)
    if args.headless is not None:
        cfg["headless"] = args.headless
    if args.browser:
        cfg["browser"] = args.browser
    if args.max_items:
        cfg["max_items"] = args.max_items
    warnings = validate(cfg)
    formats = [f.strip() for f in (args.formats.split(",") if args.formats else cfg["output_format"]) if f.strip()]
    unknown_formats = [f for f in formats if f not in WRITERS]
    formats = [f for f in formats if f in WRITERS] or ["json"]
    warnings.extend([f"忽略未知输出格式: {f}" for f in unknown_formats])
    start_page, end_page = _parse_page_range(args)
    if end_page < start_page:
        raise MsError("usage", f"页范围非法: {start_page}-{end_page}", "", 2)

    market = get_market(args.market)
    log = RunLog(layout.logs, f"search-{market.slug}")
    result_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else layout.result
    writers = WriterSet(result_dir, args.keyword, market.slug, formats)
    warnings.extend(writers.errors)
    session = None
    records_written = 0
    pages_done = 0
    page_stats: list[dict] = []
    seen: set[str] = set()
    total_pages = None
    degraded = False
    dump_path = None

    try:
        session = BrowserSession.create(cfg, log, layout.cookie, headless=cfg["headless"],
                                        browser=cfg["browser"], driver_path=args.driver_path)
        fixture_url = None
        if args.fixture:
            fixture_path = Path(args.fixture).expanduser().resolve()
            if not fixture_path.exists():
                raise MsError("io", f"fixture 不存在: {fixture_path}", "", 8)
            fixture_url = fixture_path.as_uri()
            end_page = start_page
            session.goto(fixture_url, wait=1.0)
            log.info(f"fixture mode: {fixture_url}")
        else:
            session.goto(market.home, wait=1.5)
            if session.cookie_exists(market.slug):
                session.load_cookie(market.slug)
            else:
                warnings.append(f"未找到 {market.slug} 的 Cookie，匿名抓取可能结果缺失或被拦截")

            session.goto(market.search_url(args.keyword, start_page), wait=cfg["page_wait"])
            if session.login_wall():
                warnings.append("搜索页跳转到登录页：建议先执行 ms cookie 保存登录态")
                if args.require_login or not session.cookie_exists(market.slug):
                    raise AuthRequired(
                        f"{market.label}搜索页要求登录",
                        f"先运行 ms cookie --market {market.slug} --wait 180 完成登录，再重试搜索")
            if session.blocked():
                raise BlockedBySite("搜索页出现验证码/风控拦截", "用 --no-headless（默认）人工完成验证后重试")
            total_pages = market.total_pages(session)
            if total_pages:
                end_page = min(end_page, max(start_page, total_pages))
            log.info(f"range {start_page}-{end_page} total_pages={total_pages}")

        for page in range(start_page, end_page + 1):
            if page != start_page and fixture_url is None:
                session.goto(market.search_url(args.keyword, page), wait=cfg["page_wait"])
            session.scroll_to_end(rounds=int(cfg.get("scroll_rounds", 8)))
            if fixture_url is None and session.blocked():
                warnings.append(f"第 {page} 页触发验证码，停止翻页")
                break
            records, stats = market.collect(session, log, args.keyword, page)
            page_stats.append(stats)
            if stats.get("mode") == "link-scan":
                degraded = True
            if not records:
                warnings.append(f"第 {page} 页未解析到商品（选择器可能已失效，用 ms probe 检查）")
                if args.dump_html:
                    dump_path = str(session.save_artifact(
                        layout.state / f"{market.slug}-{int(time.time())}-p{page}.html"))
                if page == start_page:
                    break
                continue
            for record in records:
                if args.ignore_ads is False and "非商品链接" in (record.get("remarks") or ""):
                    pass
                key = record.get("item_link") or f"{record.get('item_name')}|{record.get('item_price')}"
                if key in seen:
                    continue
                seen.add(key)
                writers.add(record)
                records_written += 1
                if records_written >= int(cfg["max_items"]):
                    break
            pages_done += 1
            log.info(f"page {page}: {stats.get('parsed')} records, total={records_written}")
            if records_written >= int(cfg["max_items"]):
                warnings.append(f"已达到 max_items={cfg['max_items']}，提前结束")
                break
            if page < end_page:
                session.random_delay(*cfg["page_delay"])
    finally:
        artifacts = writers.close()
        if session is not None:
            session.quit()
        log.close()

    empty_run = records_written == 0
    if empty_run and not args.allow_empty:
        for art in artifacts:
            try:
                Path(art["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        artifacts = []

    duration = round(time.time() - started, 1)
    data = {
        "market": market.slug,
        "market_label": market.label,
        "keyword": args.keyword,
        "pages_requested": [start_page, end_page],
        "pages_completed": pages_done,
        "site_total_pages": total_pages,
        "records": records_written,
        "degraded_parse": degraded,
        "page_stats": page_stats,
        "headless": cfg["headless"],
        "browser": cfg["browser"],
        "result_dir": str(layout.result),
        "html_dump": dump_path,
    }
    if page_stats and all(s.get("cards") == 0 for s in page_stats):
        warnings.append("所有页面卡片选择器均未命中：站点 DOM 可能改版，请运行 probe 并更新 selectors.json")
    if empty_run and not args.allow_empty:
        hint = (f"检查登录态/验证码；运行 ms probe --market {market.slug} "
                f"--keyword {args.keyword} --dump-html 排查选择器。日志: {log.path}")
        raise MsError("no_data", "未抓到任何商品数据", hint, 7)
    env = envelope("search", data, warnings=warnings + log.warnings, artifacts=artifacts,
                   next_actions=[
                       "读取 artifacts 中的 JSON/CSV 供后续分析",
                       f"ms stats --input <json路径> 可得到价格与店铺统计",
                   ], log_path=str(log.path))
    env["duration_sec"] = duration
    return env


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #

def cmd_probe(args) -> dict:
    layout = Layout(args.data_dir).ensure()
    cfg = load_config(layout.config_file)
    if args.headless is not None:
        cfg["headless"] = args.headless
    market = get_market(args.market)
    log = RunLog(layout.logs, f"probe-{market.slug}")
    session = None
    try:
        session = BrowserSession.create(cfg, log, layout.cookie, headless=cfg["headless"],
                                        browser=args.browser)
        session.goto(market.home, wait=1.5)
        if session.cookie_exists(market.slug):
            session.load_cookie(market.slug)
        session.goto(market.search_url(args.keyword, args.page), wait=cfg["page_wait"])
        if args.scroll:
            session.scroll_to_end(rounds=int(cfg.get("scroll_rounds", 8)))
        report = market.probe(session)
        dump = None
        if args.dump_html:
            dump = str(session.save_artifact(
                layout.state / f"{market.slug}-probe-{int(time.time())}.html"))
            report["html_dump"] = dump
    finally:
        if session is not None:
            session.quit()
        log.close()
    hints = []
    if report["login_wall"]:
        hints.append(f"页面跳转到登录，先执行 ms cookie --market {market.slug} --wait 180")
    if report["blocked"]:
        hints.append("出现验证码/风控，用有头模式人工验证")
    if not any(report["cards"].values()):
        hints.append("所有卡片选择器命中 0，检查 html_dump 并更新 skill/config/selectors.json")
    return envelope("probe", report, warnings=log.warnings, next_actions=hints, log_path=str(log.path))


# --------------------------------------------------------------------------- #
# export / stats
# --------------------------------------------------------------------------- #

def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    if path.suffix.lower() == ".csv":
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        for key in ("data", "items", "records", "result"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return payload


def cmd_export(args) -> dict:
    src = Path(args.input).expanduser().resolve()
    if not src.exists():
        raise MsError("io", f"输入文件不存在: {src}", "", 8)
    rows = _read_rows(src)
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    writers = WriterSet(out_dir, stem, "export", formats)
    for row in rows:
        writers.add(row)
    artifacts = writers.close()
    return envelope("export", {"input": str(src), "rows_in": len(rows), "formats": formats},
                    artifacts=artifacts)


def _to_float(value) -> float | None:
    text = normalize_price(str(value or ""))
    try:
        return float(text) if text else None
    except ValueError:
        return None


def cmd_stats(args) -> dict:
    src = Path(args.input).expanduser().resolve()
    if not src.exists():
        raise MsError("io", f"输入文件不存在: {src}", "", 8)
    rows = _read_rows(src)
    prices = [p for p in (_to_float(r.get("item_price") or r.get("商品价格")) for r in rows) if p]
    prices.sort()
    shops: dict[str, int] = {}
    missing = {key: 0 for key in ("item_name", "item_price", "item_shop", "item_link", "item_image")}
    for row in rows:
        shop = row.get("item_shop") or row.get("店铺名称") or "未知"
        shops[shop] = shops.get(shop, 0) + 1
        for key in missing:
            value = row.get(key) or row.get({"item_name": "商品名称", "item_price": "商品价格",
                                             "item_shop": "店铺名称", "item_link": "商品链接",
                                             "item_image": "商品图片"}[key])
            if not value:
                missing[key] += 1
    total = len(rows) or 1
    data = {
        "input": str(src),
        "rows": len(rows),
        "price": {
            "count": len(prices),
            "min": prices[0] if prices else None,
            "max": prices[-1] if prices else None,
            "avg": round(sum(prices) / len(prices), 2) if prices else None,
            "median": prices[len(prices) // 2] if prices else None,
        },
        "top_shops": sorted(shops.items(), key=lambda kv: kv[1], reverse=True)[: args.top],
        "unique_shops": len(shops),
        "missing_rate": {key: round(count / total, 3) for key, count in missing.items()},
    }
    return envelope("stats", data)


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #

def cmd_selftest(args) -> dict:
    """Offline parse check against local fixtures with a real headless browser."""
    layout = Layout(args.data_dir).ensure()
    cfg = load_config(layout.config_file)
    cfg["headless"] = True
    fixtures = skill_root() / "tests" / "fixtures"
    cases = {
        "jd": ("jd_search.html", {"item_name": "小米", "item_price": "1999.00"}),
        "taobao": ("taobao_search.html", {"item_name": "无线", "item_price": "89.00"}),
        "1688": ("1688_search.html", {"item_name": "保温杯", "item_price": "12.50"}),
    }
    log = RunLog(layout.logs, "selftest")
    session = None
    results = []
    try:
        session = BrowserSession.create(cfg, log, layout.cookie, headless=True, browser=args.browser)
        for slug, (filename, expectations) in cases.items():
            path = fixtures / filename
            market = get_market(slug)
            case = {"market": slug, "fixture": str(path), "ok": False}
            if not path.exists():
                case["error"] = "fixture 缺失"
                results.append(case)
                continue
            session.goto(path.as_uri(), wait=0.5)
            records, stats = market.collect(session, log, "selftest", 1)
            case["stats"] = stats
            case["sample"] = records[0] if records else None
            checks = {
                "records>0": len(records) > 0,
                "name": bool(records and expectations["item_name"] in records[0].get("item_name", "")),
                "price": bool(records and normalize_price(records[0].get("item_price", "")) == expectations["item_price"]),
                "link": bool(records and records[0].get("item_link", "").startswith("http")),
            }
            case["checks"] = checks
            case["ok"] = all(checks.values())
            results.append(case)

        # --- writer round-trip: exercise every export format offline ---
        samples = [case["sample"] for case in results if case.get("sample")]
        writer_dir = layout.state / "selftest"
        writer_dir.mkdir(parents=True, exist_ok=True)
        all_formats = ["json", "jsonl", "csv", "xlsx", "txt", "sql", "sqlite", "md"]
        writer_set = WriterSet(writer_dir, "selftest", "mixed", all_formats,
                               run_id=f"roundtrip-{int(time.time())}")
        for record in samples:
            writer_set.add(record)
        artifacts = writer_set.close()
        roundtrip = {"formats": all_formats, "errors": writer_set.errors,
                     "artifacts": artifacts, "input_rows": len(samples)}
        json_art = next((a for a in artifacts if a["format"] == "json"), None)
        if json_art:
            roundtrip["json_rows"] = len(_read_rows(Path(json_art["path"])))
        sqlite_art = next((a for a in artifacts if a["format"] == "sqlite"), None)
        if sqlite_art:
            import sqlite3

            with sqlite3.connect(sqlite_art["path"]) as conn:
                roundtrip["sqlite_rows"] = conn.execute("select count(*) from items").fetchone()[0]
        wrote_all = len(artifacts) == len(all_formats) and not writer_set.errors
        rows_ok = roundtrip.get("json_rows") == len(samples) == roundtrip.get("sqlite_rows")
        roundtrip["ok"] = bool(wrote_all and rows_ok)
    finally:
        if session is not None:
            session.quit()
        log.close()
    ok = all(case["ok"] for case in results) and roundtrip.get("ok", False)
    return envelope("selftest", {"cases": results, "writers": roundtrip, "all_passed": ok},
                    warnings=[] if ok else ["自检失败：检查 fixtures / selectors.json / openpyxl 安装情况"],
                    artifacts=artifacts)


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #

class JsonArgumentParser(argparse.ArgumentParser):
    """Emit a JSON error envelope instead of plain argparse text."""

    def error(self, message: str) -> None:  # type: ignore[override]
        emit(fail("cli", "usage", message, self.format_usage().strip()))
        raise SystemExit(EXIT_FOR["usage"])


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="ms",
        description="MarketSpider Agent Kit - 淘宝/京东/1688 商品数据采集工具链（JSON 输出）",
    )
    parser.add_argument("--version", action="version", version=f"MarketSpider Agent Kit {__version__}")
    parser.add_argument("--data-dir", help="cookies/results/logs 根目录（默认 <project>/data）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="环境体检")
    p.add_argument("--launch", action="store_true", help="实际启动一次无头浏览器验证 driver")
    p.add_argument("--browser", choices=("chrome", "edge", "firefox"))
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("env", help="依赖检查/安装")
    p.add_argument("action", choices=("check", "install"))
    p.add_argument("--index-url", help="pip 镜像地址")
    p.set_defaults(func=cmd_env)

    p = sub.add_parser("config", help="查看/修改配置")
    p.add_argument("action", choices=("show", "set"))
    p.add_argument("pairs", nargs="*", help="key=value，例如 browser=edge output_format=json,csv")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("markets", help="列出支持的平台")
    p.set_defaults(func=cmd_markets)

    p = sub.add_parser("cookie", help="获取/导入/检查登录 Cookie")
    p.add_argument("--market", required=True, choices=tuple(MARKETS))
    p.add_argument("--wait", type=int, default=180, help="人工登录等待秒数（默认 180）")
    p.add_argument("--import-file", help="导入外部 cookie JSON 文件")
    p.add_argument("--action", choices=("check",), default=None)
    p.add_argument("--browser", choices=("chrome", "edge", "firefox"))
    p.set_defaults(func=cmd_cookie)

    p = sub.add_parser("search", help="按关键词搜索并导出商品数据")
    p.add_argument("--market", required=True, choices=tuple(MARKETS))
    p.add_argument("--keyword", required=True)
    p.add_argument("--pages", help="页范围，如 1 或 1-3")
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--end-page", type=int, default=1)
    p.add_argument("--formats", help="输出格式，逗号分隔: json,jsonl,csv,xlsx,txt,sql,sqlite,md")
    p.add_argument("--out-dir", help="结果目录（默认 data/result）")
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--headless", dest="headless", action="store_true", default=None)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--browser", choices=("chrome", "edge", "firefox"))
    p.add_argument("--driver-path")
    p.add_argument("--require-login", action="store_true", help="未登录直接失败")
    p.add_argument("--dump-html", action="store_true", help="解析为空时保存页面 HTML 供排查")
    p.add_argument("--allow-empty", action="store_true", help="允许 0 条结果并以成功返回")
    p.add_argument("--ignore-ads", action="store_true", default=True)
    p.add_argument("--fixture", help="离线自测：用本地 HTML 跑完整链路（不访问网络）")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("probe", help="诊断页面与选择器命中情况")
    p.add_argument("--market", required=True, choices=tuple(MARKETS))
    p.add_argument("--keyword", required=True)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--scroll", action="store_true")
    p.add_argument("--dump-html", action="store_true")
    p.add_argument("--headless", dest="headless", action="store_true", default=None)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--browser", choices=("chrome", "edge", "firefox"))
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("export", help="把 JSON/JSONL/CSV 结果重新导出为其他格式")
    p.add_argument("--input", required=True)
    p.add_argument("--formats", default="json,csv,xlsx")
    p.add_argument("--out-dir")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("stats", help="对结果做价格/店铺统计")
    p.add_argument("--input", required=True)
    p.add_argument("--top", type=int, default=10)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("selftest", help="用本地 fixture 自检解析链路（无需联网）")
    p.add_argument("--browser", choices=("chrome", "edge", "firefox"))
    p.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to GBK; JSON payloads contain Chinese.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.cmd
    try:
        payload = args.func(args)
        emit(payload)
        return 0
    except MsError as exc:
        emit(fail(command, exc.code, str(exc), exc.hint))
        return exc.exit_code
    except BlockedBySite as exc:
        emit(fail(command, "blocked", str(exc), exc.hint))
        return EXIT_FOR["blocked"]
    except AuthRequired as exc:
        emit(fail(command, "auth", str(exc), exc.hint))
        return EXIT_FOR["auth"]
    except DriverUnavailable as exc:
        emit(fail(command, "driver", str(exc), exc.hint))
        return EXIT_FOR["driver"]
    except SessionError as exc:
        emit(fail(command, exc.code, str(exc), exc.hint))
        return EXIT_FOR.get(exc.code, 1)
    except KeyboardInterrupt:
        emit(fail(command, "interrupted", "用户中断", ""))
        return 130
    except Exception as exc:  # pragma: no cover - safety net
        import traceback

        emit(fail(command, "unexpected", f"{type(exc).__name__}: {exc}",
                  traceback.format_exc().splitlines()[-1]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
