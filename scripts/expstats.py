#!/usr/bin/env python3
"""OR 计算实验统计：gap 表、性能剖面、结果透视。

面向运筹优化论文/技术报告的常用口径，零第三方依赖，任意 Python 3.10+ 可跑。

CSV 约定（列名可用参数改）：
    instance, solver, obj, time, status[, seed]
    status: ok / timeout / infeasible / error（缺省按 ok）
参考值两种给法：
    1) --ref-col ref            同一行里带参考值
    2) --ref-csv refs.csv       refs.csv 两列：instance, ref（BKS 或 LB 都行，类型自己心里要清楚）

用法：
    python expstats.py gap  res.csv --ref-csv refs.csv
    python expstats.py gap  res.csv --ref-csv refs.csv --group solver
    python expstats.py table res.csv --ref-csv refs.csv
    python expstats.py profile res.csv --metric time --out profile.csv
    python expstats.py profile res.csv --metric gap --ref-csv refs.csv

口径说明（写进报告时必须交代）：
  * gap% = (obj - ref) / |ref| × 100，最小化问题；--maximize 会翻转符号。
  * 超时行默认按「未完成」处理：gap 记为缺失，性能剖面里记为 +inf。
  * 绝对值不可跨文献比；gap 才是可比量。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DONE = {"", "ok", "optimal", "feasible", "success", "done"}


def read_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"找不到 {p}")
    with p.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_refs(args, rows: list[dict]) -> dict[str, float]:
    refs: dict[str, float] = {}
    if args.ref_csv:
        for r in read_csv(args.ref_csv):
            inst = r.get(args.instance) or r.get("instance")
            if inst is None:
                raise SystemExit(f"{args.ref_csv} 缺少 instance 列")
            key = "ref" if "ref" in r else list(r)[1]
            try:
                refs[str(inst)] = float(r[key])
            except (TypeError, ValueError):
                raise SystemExit(f"参考值不是数字：{inst} → {r[key]!r}")
    elif args.ref_col:
        for r in rows:
            try:
                refs[str(r[args.instance])] = float(r[args.ref_col])
            except (KeyError, TypeError, ValueError):
                raise SystemExit(f"列 {args.ref_col} 不是数字或不存在")
    else:
        raise SystemExit("需要 --ref-csv 或 --ref-col 给出参考值（BKS / LB）")
    return refs


def is_done(status: str) -> bool:
    return (status or "").strip().lower() in DONE


def gap_pct(obj: float, ref: float, maximize: bool = False) -> float:
    if ref == 0:
        return float("nan")
    g = (obj - ref) / abs(ref) * 100.0
    return -g if maximize else g


def collect(rows: list[dict], args) -> list[dict]:
    refs = load_refs(args, rows)
    out = []
    for r in rows:
        inst = str(r[args.instance])
        if inst not in refs:
            print(f"⚠️  算例 {inst} 没有参考值，跳过")
            continue
        status = r.get(args.status, "ok") if args.status else "ok"
        rec = {
            "instance": inst,
            "solver": r.get(args.solver, "solver") if args.solver else "solver",
            "status": status,
            "obj": None,
            "time": None,
            "gap": None,
        }
        try:
            rec["obj"] = float(r[args.obj])
        except (KeyError, TypeError, ValueError):
            pass
        try:
            rec["time"] = float(r[args.time])
        except (KeyError, TypeError, ValueError):
            pass
        if rec["obj"] is not None and is_done(status):
            g = gap_pct(rec["obj"], refs[inst], args.maximize)
            rec["gap"] = None if math.isnan(g) else g
        out.append(rec)
    return out


def fmt(v, nd=3, pct=False) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞"
    s = f"{v:.{nd}f}"
    return s + ("%" if pct else "")


def cmd_gap(args) -> int:
    recs = collect(read_csv(args.csv), args)
    if not recs:
        raise SystemExit("没有可用记录（检查列名与参考值）")
    groups: dict[str, list[dict]] = {}
    for r in recs:
        key = r[args.group] if args.group in r else "all"
        groups.setdefault(str(key), []).append(r)

    print(f"\n口径：gap% = (obj − ref) / |ref| × 100；参考值来自 "
          f"{args.ref_csv or args.ref_col}；超时/失败不计入 gap，单独计数。")
    print(f"\n{'组合':<20}{'n':>5}{'完成':>6}{'最优':>6}{'均值%':>10}{'中位%':>10}{'最大%':>10}{'≤1%':>6}")
    print("-" * 82)
    for name, rs in sorted(groups.items()):
        ok = [r for r in rs if r["gap"] is not None]
        gs = [r["gap"] for r in ok]
        n_opt = sum(1 for g in gs if abs(g) <= 1e-6)
        n_1 = sum(1 for g in gs if g <= 1.0 + 1e-9)
        print(f"{name:<20}{len(rs):>5}{len(ok):>6}{n_opt:>6}"
              f"{fmt(statistics.fmean(gs) if gs else None, 3, True):>10}"
              f"{fmt(statistics.median(gs) if gs else None, 3, True):>10}"
              f"{fmt(max(gs) if gs else None, 3, True):>10}{n_1:>6}")
    failed = [r for r in recs if r["gap"] is None]
    if failed:
        print(f"\n未完成 / 无目标值 {len(failed)} 条（必须单列，不许混进均值）：")
        for r in failed[:12]:
            print(f"  - {r['instance']} / {r['solver']}：status={r['status']}")
        if len(failed) > 12:
            print(f"  ... 另有 {len(failed) - 12} 条")
    # 比参考值还好，通常意味着参考值搞错了、模型漏了约束，或「解」根本不可行。
    better = [r for r in recs if r["gap"] is not None and r["gap"] < -1e-6]
    if better:
        print(f"\n⚠️  {len(better)} 条结果优于参考值（gap < 0）：")
        for r in better[:8]:
            print(f"  - {r['instance']} / {r['solver']}：gap {fmt(r['gap'], 3, True)}")
        print("   若参考值是 OPT / BKS，先查三件事：① 该解是否真的可行（用独立校验器）"
              "② 模型是否漏了约束 ③ 参考值口径是否不同（加权 / 不同距离度量）。")
    if args.out:
        with Path(args.out).open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["instance", "solver", "status", "obj", "time", "gap"])
            w.writeheader()
            w.writerows(recs)
        print(f"\n逐条 gap 已写入 {args.out}")
    return 0


def cmd_table(args) -> int:
    recs = collect(read_csv(args.csv), args)
    solvers = sorted({r["solver"] for r in recs})
    insts = sorted({r["instance"] for r in recs})
    cell: dict[tuple[str, str], dict] = {(r["instance"], r["solver"]): r for r in recs}

    print(f"\n{'算例':<16}" + "".join(f"{s[:14]:>16}" for s in solvers))
    print("-" * (16 + 16 * len(solvers)))
    for i in insts:
        line = f"{i:<16}"
        for s in solvers:
            r = cell.get((i, s))
            if not r or r["obj"] is None:
                line += f"{'—':>16}"
            elif args.show_time and r["time"] is not None:
                line += f"{fmt(r['obj'], 2) + ' / ' + fmt(r['time'], 1) + 's':>16}"
            elif r["gap"] is not None:
                line += f"{fmt(r['obj'], 2) + ' (' + fmt(r['gap'], 2) + '%)':>16}"
            else:
                line += f"{fmt(r['obj'], 2):>16}"
        print(line)

    print("\n汇总（gap%）：")
    for s in solvers:
        gs = [r["gap"] for r in recs if r["solver"] == s and r["gap"] is not None]
        n_all = sum(1 for r in recs if r["solver"] == s)
        n_ok = len(gs)
        if gs:
            print(f"  {s:<18} 完成 {n_ok}/{n_all}　均值 {fmt(statistics.fmean(gs), 3, True)}"
                  f"　中位 {fmt(statistics.median(gs), 3, True)}　最优 {sum(1 for g in gs if abs(g) <= 1e-6)}")
        else:
            print(f"  {s:<18} 完成 0/{n_all}")
    return 0


def cmd_profile(args) -> int:
    recs = collect(read_csv(args.csv), args)
    metric = args.metric
    solvers = sorted({r["solver"] for r in recs})
    insts = sorted({r["instance"] for r in recs})
    cell: dict[tuple[str, str], float] = {}
    for r in recs:
        if r["gap"] is None:
            cell[(r["instance"], r["solver"])] = math.inf
            continue
        v = r["gap"] if metric == "gap" else r["time"]
        if v is None:
            v = math.inf
        if metric == "gap":
            v = max(v, 0.0)
        cell[(r["instance"], r["solver"])] = float(v)

    taus = [1.0, 1.05, 1.1, 1.2, 1.5, 2.0, 4.0, 8.0, math.inf]
    print(f"\n性能剖面（Dolan–Moré），指标 = {metric}；比值 = 本方 / 该算例上最好的求解器。")
    print(f"读法：τ=1 列是该求解器「取得最好」的算例比例；τ 越大越靠右说明只是慢/差得有限。")
    if metric == "gap":
        zeros = sum(1 for i in insts if min(cell.get((i, s), math.inf) for s in solvers) <= 1e-12)
        if zeros:
            print(f"ℹ️  {zeros}/{len(insts)} 个算例存在 gap=0 的求解器；比值口径下这些算例只有「同样为 0」才算命中，"
                  "因此 gap 剖面偏二元。要看「差多少」请用 --metric time 或直接看 gap 表。")
    print(f"\n{'τ':<8}" + "".join(f"{s[:14]:>16}" for s in solvers))
    print("-" * (8 + 16 * len(solvers)))
    table = []
    for tau in taus:
        line = f"{'∞' if math.isinf(tau) else f'{tau:g}':<8}"
        row = {"tau": "inf" if math.isinf(tau) else tau}
        for s in solvers:
            hit = 0
            total = 0
            for i in insts:
                v = cell.get((i, s), math.inf)
                best = min(cell.get((i, s2), math.inf) for s2 in solvers)
                total += 1
                if math.isinf(v):
                    achieved = math.isinf(best)          # 双方都没完成，算打平
                elif math.isinf(best):
                    achieved = False                     # 只有自己完成
                elif math.isinf(tau):
                    achieved = True                      # τ=∞：只要完成就算
                else:
                    achieved = v <= tau * best + 1e-12
                if achieved:
                    hit += 1
            frac = hit / total if total else 0.0
            row[s] = frac
            line += f"{frac:>16.3f}"
        print(line)
        table.append(row)
    if args.out:
        with Path(args.out).open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["tau"] + solvers)
            w.writeheader()
            w.writerows(table)
        print(f"\n剖面数据已写入 {args.out}（可直接画阶梯图）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="OR 计算实验统计：gap 表 / 性能剖面 / 透视表")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("csv")
        p.add_argument("--instance", default="instance")
        p.add_argument("--solver", default="solver")
        p.add_argument("--obj", default="obj")
        p.add_argument("--time", default="time")
        p.add_argument("--status", default="status")
        p.add_argument("--ref-csv", help="参考值 CSV（instance, ref）")
        p.add_argument("--ref-col", help="或：结果 CSV 里的参考值列名")
        p.add_argument("--maximize", action="store_true", help="最大化问题（gap 符号翻转）")
        p.add_argument("--out")

    p = sub.add_parser("gap", help="逐条 gap 与分组汇总")
    common(p)
    p.add_argument("--group", default="solver", choices=["solver", "instance", "status"])
    p.set_defaults(func=cmd_gap)

    p = sub.add_parser("table", help="算例 × 求解器 透视表")
    common(p)
    p.add_argument("--show-time", action="store_true", help="单元格显示 obj / time")
    p.set_defaults(func=cmd_table)

    p = sub.add_parser("profile", help="性能剖面（Dolan–Moré）")
    common(p)
    p.add_argument("--metric", default="time", choices=["time", "gap"])
    p.set_defaults(func=cmd_profile)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
