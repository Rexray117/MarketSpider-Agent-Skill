"""CLI integration tests: no network, no login required.

Run with:  .venv/Scripts/python.exe skill/tests/test_cli.py
Every case asserts the JSON envelope contract plus exit code.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # marketspider-agent/
SKILL = ROOT / "skill"
MS = SKILL / "scripts" / "ms.py"
PY = sys.executable
FIXTURES = SKILL / "tests" / "fixtures"
_TMP_BASE = ROOT / "data" / "state"
_TMP_BASE.mkdir(parents=True, exist_ok=True)
DATA = Path(tempfile.mkdtemp(prefix="test-tmp-", dir=_TMP_BASE))


def run(*args: str, expect: int = 0) -> dict:
    proc = subprocess.run(
        [PY, str(MS), "--data-dir", str(DATA), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if proc.returncode != expect:
        raise AssertionError(
            f"exit={proc.returncode} expected={expect}\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"
        )
    return json.loads(proc.stdout)


class ContractTests(unittest.TestCase):
    def test_doctor(self):
        payload = run("doctor")
        self.assertTrue(payload["ok"])
        names = {c["check"] for c in payload["data"]["checks"]}
        self.assertIn("python", names)
        self.assertIn("pkg:selenium", names)

    def test_markets(self):
        payload = run("markets")
        slugs = {m["slug"] for m in payload["data"]["markets"]}
        self.assertEqual(slugs, {"jd", "taobao", "1688"})

    def test_config_set_and_show(self):
        run("config", "set", "max_items=7", "output_format=json")
        payload = run("config", "show")
        self.assertEqual(payload["data"]["config"]["max_items"], 7)
        self.assertEqual(payload["data"]["config"]["output_format"], ["json"])
        run("config", "set", "max_items=200", "output_format=json,csv")

    def test_usage_error_is_json(self):
        payload = run("search", "--market", "pdd", "--keyword", "x", expect=2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "usage")

    def test_stats_missing_file(self):
        payload = run("stats", "--input", str(DATA / "nope.json"), expect=8)
        self.assertEqual(payload["error"]["code"], "io")


class OfflinePipelineTests(unittest.TestCase):
    def test_selftest_all_formats(self):
        payload = run("selftest")
        self.assertTrue(payload["data"]["all_passed"], payload["data"])
        for case in payload["data"]["cases"]:
            self.assertTrue(case["ok"], case)
        formats = {a["format"] for a in payload["data"]["writers"]["artifacts"]}
        self.assertGreaterEqual(len(formats), 8)

    def test_search_from_fixture_then_stats(self):
        out = DATA / "fixture-out"
        payload = run("search", "--market", "taobao", "--keyword", "离线测试",
                      "--fixture", str(FIXTURES / "taobao_search.html"),
                      "--formats", "json,csv,xlsx", "--headless", "--out-dir", str(out))
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["data"]["records"], 1)
        self.assertEqual(len(payload["artifacts"]), 3)
        json_art = next(a for a in payload["artifacts"] if a["format"] == "json")
        rows = json.loads(Path(json_art["path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(rows), payload["data"]["records"])
        self.assertTrue(rows[0]["item_link"].startswith("http"))

        stats = run("stats", "--input", json_art["path"])
        self.assertEqual(stats["data"]["rows"], len(rows))
        self.assertIsNotNone(stats["data"]["price"]["min"])

    def test_export_roundtrip(self):
        out = DATA / "export-out"
        run("search", "--market", "jd", "--keyword", "导出测试",
            "--fixture", str(FIXTURES / "jd_search.html"),
            "--formats", "json", "--headless", "--out-dir", str(out))
        source = next(out.glob("*.json"))
        payload = run("export", "--input", str(source), "--formats", "csv,md,xlsx",
                      "--out-dir", str(out / "converted"))
        self.assertTrue(payload["ok"])
        self.assertEqual({a["format"] for a in payload["artifacts"]}, {"csv", "md", "xlsx"})
        for art in payload["artifacts"]:
            self.assertGreater(art["bytes"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
