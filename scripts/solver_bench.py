"""求解器调优实验台：同一模型、同一机器、同时限，比较参数档案的求解效率。

对应 references/求解器调优.md §5 的度量纪律：
  - 线程固定（默认 1）：并行会同时破坏"时间可比"与"结果可复现"；
  - 记录 目标值 / 下界 / gap / 节点数 / 时间，并逐条比对目标值——**加速不许改解**；
  - 多次重复取中位数，不报最好那一次。

用法：
  python solver_bench.py model.json                      # 跑全部档案
  python solver_bench.py model.json --profiles baseline,mipfocus_bound
  python solver_bench.py model.json --repeat 3 --time-limit 60 --out bench.csv
  python solver_bench.py model.json --list               # 只看档案清单

模型格式即 milp_lab.py 的 JSON 格式，可直接用 `milp_lab solve` 跑过的文件。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from milp_lab import load_model  # noqa: E402

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# --------------------------------------------------------------------------- 档案

# 每个档案 = 一组 Gurobi 参数 + 一句"什么时候该试它"。
# 参数名与取值见 Gurobi 参考手册；这里只放**有明确机理**的组合，不做穷举搜索。
PROFILES: dict[str, dict] = {
    "baseline": {
        "desc": "求解器默认，作为分母",
        "params": {},
    },
    "presolve": {
        "desc": "激进预处理：模型有大量冗余行/列、Big-M 松弛时先试",
        "params": {"Presolve": 2, "AggFill": 0, "PreDual": 0},
    },
    "cuts": {
        "desc": "加强割平面：LP 界弱、根节点 gap 大时收益最大",
        "params": {"Cuts": 3, "Presolve": 2, "GomoryPasses": 2},
    },
    "mipfocus_feas": {
        "desc": "优先找可行解：急需一个能用的方案、证明最优不急",
        "params": {"MIPFocus": 1, "Heuristics": 0.5, "Cuts": 2},
    },
    "mipfocus_bound": {
        "desc": "优先改界：解已够好但迟迟证不出最优",
        "params": {"MIPFocus": 3, "Cuts": 2, "Symmetry": 2},
    },
    "symmetry": {
        "desc": "对称性破除：等价解多、节点数爆炸（多机/多车同构）",
        "params": {"Symmetry": 2, "Presolve": 2},
    },
    "numeric": {
        "desc": "数值稳健：出现 'numerical trouble'、解校验违约时先试它",
        "params": {"NumericFocus": 3, "MarkowitzTol": 0.01},
    },
}


def _gurobi():
    try:
        import gurobipy as gp
    except Exception:
        raise SystemExit(
            "本脚本的档案依赖 Gurobi。HiGHS(scipy) 只暴露 presolve / mip_rel_gap，"
            "做不了档案对比；没有 Gurobi 时请用 `milp_lab solve` 单跑，"
            "并在文档里如实写「只测了预处理开关」。"
        )
    try:
        env = gp.Env(empty=True)
        env.setParam("OutputFlag", 0)
        env.start()
        env.dispose()
    except Exception as e:
        raise SystemExit(f"Gurobi 许可不可用：{type(e).__name__}: {e}")
    return gp


def run_once(m, gp, params: dict, time_limit: float, gap: float,
             threads: int, seed: int) -> dict:
    from gurobipy import GRB

    mdl = gp.Model(m.name)
    mdl.Params.OutputFlag = 0
    mdl.Params.TimeLimit = float(time_limit)
    mdl.Params.MIPGap = float(gap)
    mdl.Params.Threads = int(threads)
    mdl.Params.Seed = int(seed)
    for k, v in params.items():
        setattr(mdl.Params, k, v)

    vtype = {"bin": GRB.BINARY, "int": GRB.INTEGER, "cont": GRB.CONTINUOUS}
    xs = []
    for i, n in enumerate(m.names):
        lo = None if (m.lb[i] == float("-inf")) else m.lb[i]
        hi = None if (m.ub[i] == float("inf")) else m.ub[i]
        xs.append(mdl.addVar(lb=lo, ub=hi, obj=m.obj[i], vtype=vtype[m.types[i]], name=n))
    mdl.ModelSense = GRB.MAXIMIZE if m.sense == "max" else GRB.MINIMIZE

    for c in m.cons:
        expr = gp.LinExpr()
        for j, coef in c["idx"].items():
            expr.add(xs[j], coef)
        lo, hi = c["lb"], c["ub"]
        if lo == float("-inf") and hi == float("inf"):
            continue
        if lo == hi:
            mdl.addConstr(expr == lo, name=c["name"])
        else:
            if lo != float("-inf"):
                mdl.addConstr(expr >= lo, name=c["name"] + "_lo")
            if hi != float("inf"):
                mdl.addConstr(expr <= hi, name=c["name"] + "_up")
    mdl.update()

    t0 = time.perf_counter()
    mdl.optimize()
    dt = time.perf_counter() - t0
    st = int(mdl.Status)
    has = mdl.SolCount > 0
    return {
        "status_code": st,
        "status": {2: "OPTIMAL", 3: "INFEASIBLE", 4: "INF_OR_UNBD", 5: "UNBOUNDED",
                   9: "LIMIT", 11: "INTERRUPTED"}.get(st, f"OTHER({st})"),
        "obj": float(mdl.ObjVal) if has else None,
        "bound": float(mdl.ObjBound) if has else None,
        "gap": float(mdl.MIPGap) if has else None,
        "nodes": int(mdl.NodeCount),
        "time": dt,
        "n_sol": int(mdl.SolCount),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="求解器参数档案对比")
    ap.add_argument("spec", nargs="?", help="milp_lab 格式的模型 JSON")
    ap.add_argument("--profiles", default="all",
                    help="逗号分隔的档案名，或 all（默认）")
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--gap", type=float, default=0.0)
    ap.add_argument("--threads", type=int, default=1,
                    help="固定线程数，默认 1（并行会破坏时间可比与可复现）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--repeat", type=int, default=1, help="每个档案重复次数，取中位")
    ap.add_argument("--out", help="把结果写成 CSV")
    ap.add_argument("--list", action="store_true", help="只列出档案")
    args = ap.parse_args()

    if args.list:
        for k, v in PROFILES.items():
            print(f"{k:<16}{v['desc']}\n{'':<16}params={v['params']}")
        return 0

    names = list(PROFILES) if args.profiles == "all" else [x.strip() for x in args.profiles.split(",")]
    bad = [n for n in names if n not in PROFILES]
    if bad:
        raise SystemExit(f"未知档案：{bad}；可选 {list(PROFILES)}")

    if not args.spec:
        raise SystemExit("除 --list 外必须给模型 JSON 路径")

    m = load_model(args.spec)
    gp = _gurobi()
    print(f"模型 {m.name}：{m.nvars} 变量 / {len(m.cons)} 约束 / nnz={m.nnz} "
          f"/ 二元 {sum(1 for t in m.types if t == 'bin')}")
    print(f"口径：Threads={args.threads} Seed={args.seed} TimeLimit={args.time_limit:g}s "
          f"MIPGap={args.gap:g} 每档案 {args.repeat} 次取中位\n")

    rows: list[dict] = []
    for name in names:
        runs = [run_once(m, gp, PROFILES[name]["params"],
                         args.time_limit, args.gap, args.threads, args.seed)
                for _ in range(max(1, args.repeat))]
        ok = [r for r in runs if r["obj"] is not None]
        rep = {
            "profile": name,
            "params": json.dumps(PROFILES[name]["params"], ensure_ascii=False),
            "status": runs[0]["status"],
            "obj": statistics.median([r["obj"] for r in ok]) if ok else None,
            "bound": statistics.median([r["bound"] for r in ok]) if ok else None,
            "gap": statistics.median([r["gap"] for r in ok]) if ok else None,
            "nodes": statistics.median([r["nodes"] for r in runs]),
            "time": statistics.median([r["time"] for r in runs]),
            "obj_min": min([r["obj"] for r in ok]) if ok else None,
            "obj_max": max([r["obj"] for r in ok]) if ok else None,
            "runs": len(runs),
        }
        rows.append(rep)
        o = "—" if rep["obj"] is None else f"{rep['obj']:.6f}"
        g = "—" if rep["gap"] is None else f"{rep['gap']*100:.4f}%"
        print(f"{name:<16}{o:>16}  界={rep['bound'] if rep['bound'] is None else round(rep['bound'], 6)}"
              f"  gap={g:>9}  节点={int(rep['nodes']):>8}  {rep['time']:>7.2f}s  {rep['status']}")

    base = next((r for r in rows if r["profile"] == "baseline"), None)
    print("\n—— 相对 baseline ——")
    for r in rows:
        if base is None or r is base or r["time"] <= 0:
            continue
        sp = base["time"] / r["time"]
        flag = ""
        if base["obj"] is not None and r["obj"] is not None:
            rel = abs(r["obj"] - base["obj"]) / max(1e-9, abs(base["obj"]))
            if rel > 1e-6:
                if r["status"] != "OPTIMAL":
                    flag = f"  ⚠️ 未完成，incumbent 差 {rel*100:.3f}%（时间不可比）"
                else:
                    flag = f"  ❌ 解不一致 rel={rel:.2e}（该参数不可用于定稿）"
            elif base["status"] == "OPTIMAL" and r["status"] != "OPTIMAL":
                flag = "  ⚠️ 未证明最优"
        print(f"  {r['profile']:<16}{sp:>6.2f}× 时间比{'（更快）' if sp > 1.05 else ''}{flag}")

    # 一致性守卫分三种情形，混为一谈就会误报：
    #   (a) 两个及以上 OPTIMAL 档案目标值不同 → 真的换了答案，参数不可用；
    #   (b) 某档案 TIMEOUT 且 incumbent 与最优不同 → 只是没跑完，不能当"换了答案"，
    #       但它的时间只能读作"到此为止"，不可比；
    #   (c) 全部限时 → 只能比 incumbent 质量，不能比快慢。
    opt = [r for r in rows if r["status"] == "OPTIMAL" and r["obj"] is not None]
    objs = [r["obj"] for r in opt]
    all_limit = not opt
    if len(objs) >= 2 and (max(objs) - min(objs)) > 1e-6 * max(1.0, abs(objs[0])):
        print("\n❌ 多个已证最优的档案给出了不同目标值：这不是「加速」，是换了答案。"
              "先查数值容差与参数是否改变了可行域，确认前该参数不得用于定稿。")
    elif all_limit:
        inc = [r["obj"] for r in rows if r["obj"] is not None]
        if inc and (max(inc) - min(inc)) > 1e-6 * max(1.0, abs(inc[0])):
            print(f"\n⚠️  全部档案限时截断，incumbent 在 [{min(inc):.6f}, {max(inc):.6f}] 之间不同。")
            print("   这不是「换了答案」，但**不能比快慢**——只能说谁的 incumbent 更好。")
            print("   要得到可比时间，必须让所有档案都跑到 OPTIMAL 或同一 gap。")
    statuses = {r["status"] for r in rows}
    if "LIMIT" in statuses:
        print("⚠️  存在 TIMEOUT 档案：该行时间只能读作「到此为止」，不能与其他档案比快慢。")

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        cols = ["profile", "params", "status", "obj", "bound", "gap", "nodes", "time",
                "obj_min", "obj_max", "runs"]
        with p.open("w", encoding="utf-8", newline="") as f:
            import csv
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in cols})
        print(f"\n写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
