#!/usr/bin/env python3
"""通用配对统计：任意 CSV、任意配对键、任意指标列。

为什么单独做这个脚本：pandas.merge 会**静默丢掉缺失配对**，而运筹实验要求
「同 (算例, 种子) 配对」且不同样本口径绝不可混用。本脚本强制报告
A 独有 / B 独有 / 重复键，并可用 --expect-n 核对预期配对数；发现异常以非零码退出。

零第三方依赖（只用标准库），任意 Python 3.10+ 可运行：

    python paired_stats.py A.csv B.csv \
        --keys instance,seed --value objective --where iters=2000 \
        --expect-n 160 --csv pairs.csv

Δ = (A - B) / B * 100（--mode pct，默认），负 = A 更好（默认越小越好）。
--mode abs 时为 A - B；--higher-is-better 翻转好坏方向。
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def natural(s: str):
    """让 'seed2' 排在 'seed10' 前面。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def parse_where(items: list[str]) -> list[tuple[str, str]]:
    out = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--where 需要 col=value 形式，收到：{item}")
        col, val = item.split("=", 1)
        out.append((col.strip(), val.strip()))
    return out


def row_matches(r: dict, col: str, want: str) -> bool:
    got = (r.get(col) or "").strip()
    try:
        return abs(float(got) - float(want)) < 1e-9
    except ValueError:
        return got == want


def load(path: Path, key_cols: list[str], value_col: str,
         wheres: list[tuple[str, str]]) -> tuple[dict[tuple[str, ...], float], int, int]:
    """返回 {配对键: 指标值}、重复键数、总行数。"""
    rows: dict[tuple[str, ...], float] = {}
    dup = 0
    total = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = set(reader.fieldnames or [])
        need = set(key_cols) | {value_col} | {c for c, _ in wheres}
        missing = need - fields
        if missing:
            raise SystemExit(f"{path}: 缺少列 {', '.join(sorted(missing))}；实际列：{', '.join(sorted(fields))}")
        for r in reader:
            total += 1
            if not all(row_matches(r, c, v) for c, v in wheres):
                continue
            key = tuple((r[k] or "").strip() for k in key_cols)
            if key in rows:
                dup += 1
            try:
                rows[key] = float(r[value_col])
            except (TypeError, ValueError):
                raise SystemExit(f"{path}: 第 {total} 行的 {value_col} 不是数值：{r[value_col]!r}")
    if not rows:
        raise SystemExit(f"{path}: 没有可用行（共 {total} 行，检查 --where 是否筛掉了全部）")
    return rows, dup, total


def sign_test_p(better: int, worse: int) -> float:
    """双侧精确二项检验（p=0.5），去并列。"""
    n = better + worse
    if n == 0:
        return float("nan")
    k = min(better, worse)
    return min(1.0, 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path, help="实验臂 A 的 CSV")
    ap.add_argument("b", type=Path, help="对照臂 B（通常 = 基线）的 CSV")
    ap.add_argument("--keys", default="instance,seed", help="配对键列，逗号分隔（默认 instance,seed）")
    ap.add_argument("--value", default="objective", help="指标列（默认 objective）")
    ap.add_argument("--where", action="append", default=[], metavar="col=value",
                    help="行筛选，可重复；数值按浮点比较，其余按字符串")
    ap.add_argument("--mode", choices=("pct", "abs"), default="pct", help="相对百分比或绝对差")
    ap.add_argument("--higher-is-better", action="store_true", help="指标越大越好（默认越小越好）")
    ap.add_argument("--expect-n", type=int, default=0, help="预期配对数，0 = 不检查")
    ap.add_argument("--allow-unpaired", action="store_true", help="允许 A/B 键集合不一致（默认拦截）")
    ap.add_argument("--csv", type=Path, default=None, help="可选：导出逐配对明细")
    args = ap.parse_args()

    for p in (args.a, args.b):
        if not p.exists():
            raise SystemExit(f"找不到文件：{p}")

    key_cols = [k.strip() for k in args.keys.split(",") if k.strip()]
    if not key_cols:
        raise SystemExit("--keys 不能为空")
    wheres = parse_where(args.where)

    a, dup_a, total_a = load(args.a, key_cols, args.value, wheres)
    b, dup_b, total_b = load(args.b, key_cols, args.value, wheres)

    only_a, only_b = set(a) - set(b), set(b) - set(a)
    keys = sorted(set(a) & set(b), key=lambda k: tuple(natural(x) for x in k))
    if not keys:
        raise SystemExit("A 与 B 没有共同的配对键，无法做配对比较")

    diffs: list[float] = []
    for k in keys:
        va, vb = a[k], b[k]
        if args.mode == "pct":
            if abs(vb) < 1e-12:
                raise SystemExit(f"配对 {k} 的 B 值为 0，相对百分比无定义；请改用 --mode abs")
            d = (va - vb) / vb * 100.0
            tol = 1e-9
        else:
            d = va - vb
            tol = 1e-9 * max(1.0, abs(vb))
        diffs.append(d)

    better = sum((d > tol if args.higher_is_better else d < -tol) for d in diffs)
    worse = sum((d < -tol if args.higher_is_better else d > tol) for d in diffs)
    tied = len(diffs) - better - worse
    srt = sorted(diffs)
    mid = len(srt) // 2
    median = srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2
    mean = sum(diffs) / len(diffs)
    p = sign_test_p(better, worse)

    print(f"A = {args.a}    （{total_a} 行 → 筛后 {len(a)} 个键）")
    print(f"B = {args.b}    （{total_b} 行 → 筛后 {len(b)} 个键）")
    print(f"配对键 = {','.join(key_cols)}   指标 = {args.value}   "
          f"模式 = {args.mode}   方向 = {'越大越好' if args.higher_is_better else '越小越好'}")
    if wheres:
        print("筛选 = " + ", ".join(f"{c}={v}" for c, v in wheres))
    expr = "(A − B) / B × 100" if args.mode == "pct" else "A − B"
    print(f"Δ = {expr}，" + ("正 = A 更好" if args.higher_is_better else "负 = A 更好"))
    cardinality = ", ".join(f"{c}={len({k[i] for k in keys})}" for i, c in enumerate(key_cols))
    print(f"配对 n = {len(diffs)}   键基数：{cardinality}")
    print(f"A 独有 {len(only_a)}   B 独有 {len(only_b)}   "
          f"A 重复键 {dup_a}   B 重复键 {dup_b}")
    unit = "%" if args.mode == "pct" else ""
    print(f"均值 {mean:+.4f}{unit}   中位 {median:+.4f}{unit}")
    print(f"更好 / 更差 / 并列 = {better} / {worse} / {tied}")
    print(f"符号检验 p = " + ("n/a" if math.isnan(p) else f"{p:.2g}")
          + f"（去并列 n = {better + worse}，双侧精确二项）")
    print("写结论时请自行标注算例数 × 种子数与样本口径；不同口径不可混入同一张表。")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(list(key_cols) + ["value_a", "value_b", "delta"])
            for k, d in zip(keys, diffs):
                w.writerow(list(k) + [a[k], b[k], round(d, 8)])
        print(f"逐配对明细已写出 {args.csv}")

    code = 0
    if only_a or only_b:
        print("\n[警告] A/B 的配对键集合不一致 —— 很可能是把不同样本口径混用了。")
        if only_a:
            print("  A 独有示例：" + ", ".join("/".join(k) for k in sorted(only_a)[:5]))
        if only_b:
            print("  B 独有示例：" + ", ".join("/".join(k) for k in sorted(only_b)[:5]))
        print("  请先用 --where 或统一的算例集对齐口径，再重算。")
        if not args.allow_unpaired:
            code = 2
    if args.expect_n and len(diffs) != args.expect_n:
        print(f"\n[警告] 配对数 n={len(diffs)} 与 --expect-n {args.expect_n} 不符；"
              f"可能有配对被静默丢弃，或样本口径不对。")
        if code == 0:
            code = 3
    return code


if __name__ == "__main__":
    raise SystemExit(main())
