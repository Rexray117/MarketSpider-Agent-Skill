"""Selenium session wrapper: headless-capable, cookie-aware, failure-typed.

Upstream ``Core.BrowserControl`` was tied to Tk dialogs, a fixed Chrome
profile and blocking countdowns. This module keeps the same capabilities
without any GUI dependency so an agent can drive it non-interactively.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any, Iterable

from .logutil import RunLog

# selenium is imported on demand. A module-level `from selenium import webdriver`
# made `ms --help`, `ms doctor` and `ms env install` die with ImportError when
# selenium was missing - i.e. the documented recovery command ("run ms env
# install") was blocked by the very package it is supposed to install.
SELENIUM_HINT = "安装 selenium：运行 ms env install，或 pip install selenium"

try:  # tiny module, needed by the except-clauses below; no driver required
    from selenium.common.exceptions import WebDriverException
except ImportError:  # pragma: no cover - environment dependent
    class WebDriverException(Exception):  # type: ignore[no-redef]
        """Stand-in so importing this module never fails without selenium.

        Nothing can raise it when selenium is absent (no browser can exist).
        """


def _selenium_webdriver():
    """Import the selenium webdriver API lazily, with an actionable error."""
    try:
        from selenium import webdriver
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SeleniumMissing("未安装 selenium，无法启动浏览器", SELENIUM_HINT) from exc
    return webdriver


class SessionError(RuntimeError):
    code = "browser_error"

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class SeleniumMissing(SessionError):
    """selenium itself is not installed -> environment exit code (3)."""

    code = "environment"


class DriverUnavailable(SessionError):
    code = "driver_unavailable"


class AuthRequired(SessionError):
    code = "auth_required"


class BlockedBySite(SessionError):
    code = "blocked"


BLOCK_SELECTORS = [
    "#nc_1_wrapper",
    ".nc-container",
    "#nocaptcha",
    "iframe[src*='captcha']",
    "iframe[src*='punish']",
    "#J_VerifyBox",
    "[class*='verify-box']",
    "[class*='risk-verify']",
]
LOGIN_URL_MARKERS = ("login.taobao.com", "login.tmall.com", "passport.jd.com", "login.1688.com")
LOGIN_SELECTORS = ["#login-box", "#J_LoginBox", "#login-form", ".login-box", "form[name='login']"]


def _options(browser: str, headless: bool, user_agent: str | None, proxy: str | None,
             profile_dir: str | None):
    webdriver = _selenium_webdriver()
    if browser == "chrome":
        opts = webdriver.ChromeOptions()
    elif browser == "edge":
        opts = webdriver.EdgeOptions()
    elif browser == "firefox":
        opts = webdriver.FirefoxOptions()
    else:
        raise SessionError(f"不支持的浏览器: {browser}", "使用 chrome / edge / firefox")
    # Reduce the most obvious automation fingerprint (same as upstream).
    try:
        opts.add_argument("--disable-blink-features=AutomationControlled")
    except Exception:
        pass
    if browser != "firefox":
        try:
            opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
            opts.add_experimental_option("useAutomationExtension", False)
        except Exception:
            pass
    if headless:
        if browser == "firefox":
            opts.add_argument("-headless")
        else:
            opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,1000")
    opts.add_argument("--lang=zh-CN,zh;q=0.9")
    opts.add_argument("--disable-gpu") if browser != "firefox" else None
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    if browser != "firefox":
        # Only applied to Chromium-family; fixes DevToolsActivePort crashes in
        # restricted / nested-session environments.
        for flag in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox",
                     "--remote-debugging-port=0"):
            opts.add_argument(flag)
    if user_agent:
        opts.add_argument(f"--user-agent={user_agent}")
    if proxy:
        opts.add_argument(f"--proxy-server={proxy}")
    if profile_dir:
        try:
            opts.add_argument(f"--user-data-dir={Path(profile_dir).expanduser()}")
        except Exception:
            pass
    return opts


def bundled_driver(browser: str) -> str | None:
    """Look for a driver shipped under <project>/drivers (offline-friendly)."""
    try:
        from .paths import project_root

        name = {"chrome": "chromedriver.exe", "edge": "msedgedriver.exe",
                "firefox": "geckodriver.exe"}[browser]
        for candidate in (project_root() / "drivers" / name,
                          project_root() / "drivers" / name.replace(".exe", "")):
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass
    return None


def build_driver(browser: str = "chrome", headless: bool = False, driver_path: str | None = None,
                 user_agent: str | None = None, proxy: str | None = None,
                 profile_dir: str | None = None, page_load_timeout: int = 45):
    webdriver = _selenium_webdriver()
    opts = _options(browser, headless, user_agent, proxy, profile_dir)
    if not driver_path:
        driver_path = bundled_driver(browser)
    service = None
    if driver_path:
        try:
            from selenium.webdriver.chrome.service import Service as ChromeService
            from selenium.webdriver.edge.service import Service as EdgeService
            from selenium.webdriver.firefox.service import Service as FirefoxService

            svc_cls = {"chrome": ChromeService, "edge": EdgeService, "firefox": FirefoxService}[browser]
            service = svc_cls(executable_path=str(Path(driver_path).expanduser()))
        except Exception as exc:  # pragma: no cover - config dependent
            raise DriverUnavailable(f"无法使用指定的 driver_path: {exc}") from exc
    try:
        if browser == "chrome":
            drv = webdriver.Chrome(service=service, options=opts) if service else webdriver.Chrome(options=opts)
        elif browser == "edge":
            drv = webdriver.Edge(service=service, options=opts) if service else webdriver.Edge(options=opts)
        else:
            drv = webdriver.Firefox(service=service, options=opts) if service else webdriver.Firefox(options=opts)
    except WebDriverException as exc:
        raise DriverUnavailable(
            f"启动浏览器失败: {exc.msg or exc}",
            "确认已安装 Chrome/Edge 且可联网下载 driver；离线时可用 --driver-path 指定 chromedriver.exe",
        ) from exc
    drv.set_page_load_timeout(page_load_timeout)
    drv.implicitly_wait(0.5)
    try:
        if browser in ("chrome", "edge"):
            # Hide the webdriver flag before any page script runs.
            drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            })
    except Exception:
        pass
    return drv


class BrowserSession:
    def __init__(self, driver, log: RunLog, cookie_dir: str | Path | None = None):
        self.driver = driver
        self.log = log
        self.cookie_dir = Path(cookie_dir) if cookie_dir else None

    # ---------------- lifecycle ----------------
    @classmethod
    def create(cls, cfg: dict, log: RunLog, cookie_dir: str | Path | None = None,
               headless: bool | None = None, browser: str | None = None,
               driver_path: str | None = None) -> "BrowserSession":
        browser = browser or cfg.get("browser", "chrome")
        profile_dir = cfg.get("profile_dir")
        cleanup_profile = False
        if not profile_dir and browser != "firefox":
            # A throwaway profile avoids "profile in use" locks on repeated runs.
            # It lives under <data>/state/profiles so it never leaks into cwd.
            # Only create <data>/state here. chromedriver creates the profile
            # tree itself; pre-creating its parent "profiles" directory made
            # session creation fail with "failed to write prefs file" when the
            # data dir lived on some volumes (seen with a %TEMP% data dir).
            base = Path(log.dir).parent / "state"
            try:
                base.mkdir(parents=True, exist_ok=True)
                profile_dir = str(base / "profiles" / f"p{os.getpid()}-{int(time.time() * 1000)}")
                cleanup_profile = True
            except OSError:
                profile_dir = None
        drv = build_driver(
            browser=browser,
            headless=cfg.get("headless", False) if headless is None else headless,
            driver_path=driver_path or cfg.get("driver_path"),
            user_agent=cfg.get("user_agent"),
            proxy=cfg.get("proxy"),
            profile_dir=profile_dir,
            page_load_timeout=int(cfg.get("page_load_timeout", 45)),
        )
        if cleanup_profile and profile_dir:
            try:
                drv._ms_profile_dir = profile_dir
            except Exception:
                pass
        return cls(drv, log, cookie_dir)

    def quit(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass
        profile = getattr(self.driver, "_ms_profile_dir", None)
        if profile:
            import shutil

            shutil.rmtree(profile, ignore_errors=True)

    # ---------------- navigation ----------------
    def goto(self, url: str, wait: float = 1.0) -> None:
        self.log.info(f"goto {url}")
        try:
            self.driver.get(url)
        except WebDriverException as exc:
            # Page-load timeouts are common on search pages; keep going.
            self.log.warn(f"页面加载告警: {str(exc)[:200]}")
        if wait:
            time.sleep(wait)
        self.wait_ready()

    def wait_ready(self, timeout: float = 15.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    return
            except WebDriverException:
                return
            time.sleep(0.4)

    def refresh(self) -> None:
        try:
            self.driver.refresh()
        except WebDriverException as exc:
            self.log.warn(f"刷新失败: {exc}")

    @property
    def url(self) -> str:
        try:
            return self.driver.current_url or ""
        except WebDriverException:
            return ""

    @property
    def title(self) -> str:
        try:
            return self.driver.title or ""
        except WebDriverException:
            return ""

    def html(self) -> str:
        try:
            return self.driver.page_source or ""
        except WebDriverException:
            return ""

    def save_artifact(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.html(), encoding="utf-8", errors="replace")
        return p

    # ---------------- elements ----------------
    def find(self, selector: str, parent: Any = None):
        from selenium.webdriver.common.by import By

        try:
            root = parent if parent is not None else self.driver
            found = root.find_elements(By.CSS_SELECTOR, selector)
            return found[0] if found else None
        except WebDriverException:
            return None

    def find_all(self, selector: str, parent: Any = None) -> list:
        from selenium.webdriver.common.by import By

        try:
            root = parent if parent is not None else self.driver
            return root.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            return []

    def find_any(self, selectors: Iterable[str], parent: Any = None):
        for sel in selectors:
            el = self.find(sel, parent)
            if el is not None:
                return el, sel
        return None, None

    def find_all_any(self, selectors: Iterable[str]) -> list:
        """Union of all selectors, de-duplicated by element id."""
        seen: set[str] = set()
        out: list = []
        for sel in selectors:
            for el in self.find_all(sel):
                key = getattr(el, "id", None) or id(el)
                if key in seen:
                    continue
                seen.add(key)
                out.append(el)
        return out

    def count(self, selector: str) -> int:
        return len(self.find_all(selector))

    @staticmethod
    def text(el: Any) -> str:
        if el is None:
            return ""
        try:
            value = el.text
        except Exception:
            value = ""
        if not value:
            try:
                value = el.get_attribute("textContent") or ""
            except Exception:
                value = ""
        return " ".join(str(value).split())

    @staticmethod
    def attr(el: Any, name: str) -> str:
        if el is None:
            return ""
        try:
            return el.get_attribute(name) or ""
        except Exception:
            return ""

    def js(self, script: str, *args):
        try:
            return self.driver.execute_script(script, *args)
        except WebDriverException as exc:
            self.log.warn(f"JS 执行失败: {str(exc)[:160]}")
            return None

    def present(self, selectors: Iterable[str]) -> str | None:
        for sel in selectors:
            if self.find(sel) is not None:
                return sel
        return None

    # ---------------- behaviour ----------------
    def scroll_to_end(self, rounds: int = 8, step: int = 600, pause: float = 1.0) -> None:
        """Progressive scroll; lazy-loaded cards need several passes."""
        last_height = -1
        for _ in range(max(1, rounds)):
            self.js(f"window.scrollBy(0, {step})")
            time.sleep(pause)
            try:
                height = self.driver.execute_script("return document.body.scrollHeight") or 0
            except WebDriverException:
                height = 0
            if height == last_height:
                # one extra nudge, then stop
                self.js("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(pause)
                break
            last_height = height
        self.js("window.scrollTo(0, Math.min(document.body.scrollHeight, 1200))")

    def random_delay(self, low: float, high: float) -> None:
        delay = random.uniform(max(0.0, low), max(low, high))
        if delay > 0:
            time.sleep(delay)

    # ---------------- state detection ----------------
    def login_wall(self) -> bool:
        url = self.url.lower()
        if any(m in url for m in LOGIN_URL_MARKERS):
            return True
        return self.present(LOGIN_SELECTORS) is not None

    def blocked(self) -> bool:
        url = self.url.lower()
        if any(token in url for token in ("verify", "punish", "captcha", "risk")):
            return True
        if self.present(BLOCK_SELECTORS) is not None:
            return True
        title = self.title
        if "验证" in title or "captcha" in title.lower():
            return True
        return False

    # ---------------- cookies ----------------
    def _cookie_path(self, market: str) -> Path:
        if self.cookie_dir is None:
            raise SessionError("未配置 cookie 目录")
        return Path(self.cookie_dir) / f"{market}.cookie"

    def cookie_exists(self, market: str) -> bool:
        try:
            return self._cookie_path(market).exists()
        except SessionError:
            return False

    def load_cookie(self, market: str) -> bool:
        """Inject saved cookies + web storage. Returns True when applied."""
        path = self._cookie_path(market)
        if not path.exists():
            self.log.info(f"未找到 {market} 的 cookie 文件，将以匿名状态继续")
            return False
        ok = True
        try:
            cookies = json.loads(path.read_text(encoding="utf-8"))
            self.driver.delete_all_cookies()
            for cookie in cookies:
                clean = {k: v for k, v in cookie.items() if k in
                         ("name", "value", "domain", "path", "expiry", "secure", "httpOnly", "sameSite")}
                try:
                    self.driver.add_cookie(clean)
                except WebDriverException as exc:
                    self.log.warn(f"cookie 注入跳过 {cookie.get('name')}: {str(exc)[:120]}")
        except (OSError, json.JSONDecodeError) as exc:
            self.log.warn(f"读取 cookie 失败: {exc}")
            return False
        storage = path.with_suffix(".storage")
        if storage.exists():
            try:
                data = json.loads(storage.read_text(encoding="utf-8"))
                for key in ("localStorage", "sessionStorage"):
                    try:
                        items = json.loads(data.get(key, "{}"))
                    except (TypeError, json.JSONDecodeError):
                        items = {}
                    for k, v in items.items():
                        try:
                            self.js(f"{key}.setItem(arguments[0], arguments[1])", k, v)
                        except Exception:
                            pass
            except (OSError, json.JSONDecodeError) as exc:
                self.log.warn(f"读取 storage 失败: {exc}")
        self.refresh()
        return ok

    def save_cookie(self, market: str) -> Path:
        path = self._cookie_path(market)
        path.parent.mkdir(parents=True, exist_ok=True)
        cookies = self.driver.get_cookies()
        path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        storage = {
            "localStorage": json.dumps(self.js("return window.localStorage") or {}, ensure_ascii=False),
            "sessionStorage": json.dumps(self.js("return window.sessionStorage") or {}, ensure_ascii=False),
        }
        path.with_suffix(".storage").write_text(json.dumps(storage, ensure_ascii=False), encoding="utf-8")
        return path

    def import_cookie(self, market: str, cookie_file: str | Path) -> Path:
        """Import a cookie list from a user-supplied JSON file."""
        src = Path(cookie_file).expanduser()
        data = json.loads(src.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("cookies", data.get("data", []))
        if not isinstance(data, list):
            raise SessionError("cookie 文件格式不正确，应为 JSON 数组")
        dest = self._cookie_path(market)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return dest

    def cookie_summary(self) -> dict:
        cookies = self.driver.get_cookies()
        names = sorted({c.get("name", "") for c in cookies if c.get("name")})
        return {"count": len(cookies), "names": names[:60]}
