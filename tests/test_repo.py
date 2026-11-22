"""仓库级最小测试：不联网、不需要 Gurobi，也不需要高德 key。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    if spec is None or spec.loader is None:
        raise AssertionError(f"无法加载模块：{rel}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestStructure(unittest.TestCase):
    def test_参考文档齐全(self) -> None:
        files = sorted(p.name for p in (ROOT / "references").glob("*.md"))
        self.assertEqual(len(files), 20, files)

    def test_入口与模块文件存在(self) -> None:
        for rel in (
            "SKILL.md", "README.md", "LICENSE", ".gitignore",
            "amap/MODULE.md", "amap/assets/map_template.html",
            "amap/assets/.env.example", "amap/scripts/build_map.py",
        ):
            with self.subTest(rel=rel):
                self.assertTrue((ROOT / rel).is_file(), rel)


class TestExamples(unittest.TestCase):
    def setUp(self) -> None:
        self.solution = json.loads((ROOT / "examples/sample_solution.json").read_text(encoding="utf-8"))
        self.payload = json.loads((ROOT / "examples/sample_payload.json").read_text(encoding="utf-8"))

    def test_示例解能被适配器转换(self) -> None:
        adapt = _load("adapt_tddo_under_test", "amap/scripts/adapt_tddo.py")
        converted = adapt.convert_solution(self.solution)
        self.assertEqual([s["key"] for s in converted["scenarios"]], ["initial", "final"])
        final = converted["scenarios"][1]
        self.assertEqual(len(final["routes"]), 1)
        self.assertEqual(sum(len(r["sorties"]) for r in final["routes"]), 1)
        self.assertEqual(final["total_cost"], 400.0)

    def test_架次端点落在停靠点上(self) -> None:
        route = self.solution["final"]["routes"][0]
        sortie = route["sorties"][0]
        stops = [st["ll"] for st in route["stops"]]
        for tag, pt in (("起飞", sortie["path"][0]), ("归队", sortie["path"][-1])):
            near_km = min(adapt_km(pt, st) for st in stops)
            self.assertLess(near_km * 1000, 80, f"{tag}点飘移：{near_km*1000:.0f} m")

    def test_示例payload契约(self) -> None:
        self.assertEqual(set(self.payload), {"meta", "depot", "stores", "nfz", "scenarios"})
        self.assertEqual(len(self.payload["stores"]), 4)
        self.assertEqual(len(self.payload["scenarios"]), 2)
        for sc in self.payload["scenarios"]:
            self.assertTrue(sc["routes"])
            for r in sc["routes"]:
                self.assertGreaterEqual(len(r["path"]), 2)
                self.assertTrue(r["veh"])

    def test_出图视野能包住所有点(self) -> None:
        build_map = _load("build_map_under_test", "amap/scripts/build_map.py")
        pts = build_map.collect_points(self.payload)
        self.assertGreaterEqual(len(pts), 6)


class TestNoSecrets(unittest.TestCase):
    def test_源码与文档无32位十六进制串(self) -> None:
        suffixes = {".py", ".md", ".json", ".html", ".yml", ".example"}
        hits = []
        for p in ROOT.rglob("*"):
            if not p.is_file() or p.suffix not in suffixes:
                continue
            if ".git" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"\b[0-9a-fA-F]{32}\b", text):
                hits.append(str(p.relative_to(ROOT)))
        self.assertEqual(hits, [], hits)


class TestRunnable(unittest.TestCase):
    def test_脚本帮助可用(self) -> None:
        for rel in (
            "scripts/study.py", "scripts/data_profile.py", "scripts/expstats.py",
            "scripts/paired_stats.py", "scripts/milp_lab.py", "scripts/solver_bench.py",
            "amap/scripts/adapt_tddo.py", "amap/scripts/build_map.py",
        ):
            with self.subTest(rel=rel):
                r = subprocess.run([sys.executable, str(ROOT / rel), "--help"],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr[-500:])

    def test_算法引擎自测含负对照(self) -> None:
        r = subprocess.run([sys.executable, str(ROOT / "scripts/algotemplates/selftest.py")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("PASS", r.stdout + r.stderr)


def adapt_km(a: list[float], b: list[float]) -> float:
    """与 adapt_tddo.km_between 同式，避免测试依赖被测实现。"""
    lon1, lat1, lon2, lat2 = map(float, (a[0], a[1], b[0], b[1]))
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


if __name__ == "__main__":
    unittest.main(verbosity=2)
