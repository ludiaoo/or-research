#!/usr/bin/env python3
"""MILP 模型实验室：解、松弛、强弱 formulation 对比、解校验。

用途：把「模型卡」里的数学式子变成可计算的证据——LP 下界、整数最优值、
integrality gap、求解器诊断（对偶界 / gap / 节点数 / 时间），并对给定解做独立校验。

模型用 JSON 描述，与具体问题无关：

    {
      "name": "pmed_strong",
      "sense": "min",
      "vars": {
        "y0":  {"type": "bin",  "obj": 0.0},
        "x00": {"type": "cont", "obj": 12.5, "lb": 0, "ub": 1}
      },
      "cons": [
        {"name": "assign_0", "terms": {"x00": 1, "x10": 1}, "lb": 1, "ub": 1}
      ]
    }

type: bin | int | cont；vars 也可以是 [{"name": ..., ...}, ...] 保序写法。
cons 的 lb / ub 缺省为 -inf / +inf；terms 是 {变量名: 系数}。

用法：
    python milp_lab.py solve   spec.json [--solver auto|highs|gurobi] [--time-limit 60] [--gap 0]
    python milp_lab.py relax   spec.json [...]
    python milp_lab.py compare strong.json weak.json [...]   # 强弱 formulation 体检
    python milp_lab.py check   spec.json solution.json       # 独立校验一个解
    python milp_lab.py demo    --out ./lab                   # 生成 p-median 强弱两套样例

依赖：numpy + scipy（HiGHS）；装了 gurobipy 且许可证可用时自动优先 Gurobi。
退出码：0 正常；2 参数/模型错误；3 求解未达最优（有时间上限内可行解）；4 不可行/无界。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

INF = math.inf
TOL = 1e-6


# --------------------------------------------------------------------------- 模型


class Model:
    def __init__(self, spec: dict, path: Path | None = None):
        self.name = spec.get("name") or (path.stem if path else "model")
        self.sense = (spec.get("sense") or "min").lower()
        if self.sense not in ("min", "max"):
            raise SystemExit(f"sense 只能是 min / max，收到 {self.sense!r}")

        raw_vars = spec.get("vars")
        if not raw_vars:
            raise SystemExit("模型缺少 vars")
        items = raw_vars.items() if isinstance(raw_vars, dict) else [(v["name"], v) for v in raw_vars]
        self.names: list[str] = []
        self.types: list[str] = []
        self.obj: list[float] = []
        self.lb: list[float] = []
        self.ub: list[float] = []
        for name, v in items:
            t = (v.get("type") or "cont").lower()
            if t not in ("bin", "int", "cont"):
                raise SystemExit(f"变量 {name} 的 type 非法：{t}")
            self.names.append(name)
            self.types.append(t)
            self.obj.append(float(v.get("obj", 0.0)))
            lo = v.get("lb", 0.0 if t in ("bin", "int") else -INF)
            hi = v.get("ub", 1.0 if t == "bin" else INF)
            self.lb.append(float(lo))
            self.ub.append(float(hi))
        self.index = {n: i for i, n in enumerate(self.names)}
        if len(self.index) != len(self.names):
            raise SystemExit("变量名有重复")

        self.cons: list[dict] = []
        seen: dict[str, int] = {}
        for k, c in enumerate(spec.get("cons") or []):
            nm = str(c.get("name") or f"c{k}")
            seen[nm] = seen.get(nm, 0) + 1
            if seen[nm] > 1:
                nm = f"{nm}#{seen[nm]}"
            terms: dict[str, float] = c.get("terms") or {}
            idx: dict[int, float] = {}
            for vn, coef in terms.items():
                if vn not in self.index:
                    raise SystemExit(f"约束 {nm} 引用了不存在的变量 {vn}")
                if abs(float(coef)) > 0:
                    idx[self.index[vn]] = float(coef)
            self.cons.append({
                "name": nm,
                "idx": idx,
                "lb": float(c.get("lb", -INF)),
                "ub": float(c.get("ub", INF)),
            })

    # ---- 规模
    @property
    def nvars(self) -> int:
        return len(self.names)

    @property
    def nnz(self) -> int:
        return sum(len(c["idx"]) for c in self.cons)

    def integrality(self, relaxed: bool = False) -> list[int]:
        if relaxed:
            return [0] * self.nvars
        return [0 if t == "cont" else 1 for t in self.types]

    def c(self, negate: bool = False):
        s = -1.0 if negate else 1.0
        return [s * v for v in self.obj]

    # ---- 校验
    def evaluate(self, x: dict[str, float]) -> dict:
        missing = [n for n in self.names if n not in x]
        obj = sum(self.obj[i] * x[n] for i, n in enumerate(self.names) if n in x)
        bound_viol = 0.0
        int_viol = 0.0
        for i, n in enumerate(self.names):
            if n not in x:
                continue
            v = float(x[n])
            bound_viol = max(bound_viol, self.lb[i] - v, v - self.ub[i])
            if self.types[i] == "int":
                int_viol = max(int_viol, abs(v - round(v)))
            elif self.types[i] == "bin":
                int_viol = max(int_viol, min(abs(v), abs(v - 1.0)))
        cons_viol = 0.0
        worst = None
        for c in self.cons:
            val = sum(coef * float(x.get(self.names[j], 0.0)) for j, coef in c["idx"].items())
            v = max(c["lb"] - val, val - c["ub"], 0.0)
            if v > cons_viol:
                cons_viol, worst = v, (c["name"], val, c["lb"], c["ub"])
        return {
            "objective": obj,
            "missing": missing,
            "bound_violation": max(0.0, bound_viol),
            "integrality_violation": int_viol,
            "constraint_violation": cons_viol,
            "worst_constraint": worst,
            "feasible": not missing and max(0.0, bound_viol, int_viol, cons_viol) <= 1e-6,
        }


def load_model(path: str) -> Model:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"找不到模型文件：{p}")
    try:
        spec = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{p} 不是合法 JSON：{e}")
    return Model(spec, p)


# --------------------------------------------------------------------------- 求解


def _require_scipy():
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import csr_matrix
    except Exception as e:  # pragma: no cover
        raise SystemExit(
            "缺少 numpy/scipy。会话内置 python 往往没有装，请用项目虚拟环境：\n"
            "  .venv/Scripts/python.exe milp_lab.py ...\n"
            f"原始错误：{type(e).__name__}: {e}"
        )
    return np, csr_matrix, milp, LinearConstraint, Bounds


def solve_scipy(m: Model, relaxed: bool, time_limit: float, gap: float) -> dict:
    np, csr_matrix, milp, LinearConstraint, Bounds = _require_scipy()
    neg = m.sense == "max"
    rows, cols, vals, lo, hi = [], [], [], [], []
    for r, c in enumerate(m.cons):
        for j, coef in c["idx"].items():
            rows.append(r)
            cols.append(j)
            vals.append(coef)
        lo.append(c["lb"])
        hi.append(c["ub"])
    A = csr_matrix((vals, (rows, cols)), shape=(len(m.cons), m.nvars)) if vals else None
    cons = LinearConstraint(A, np.array(lo), np.array(hi)) if A is not None else ()
    t0 = time.perf_counter()
    res = milp(
        c=np.array(m.c(negate=neg)),
        constraints=cons,
        integrality=np.array(m.integrality(relaxed)),
        bounds=Bounds(np.array(m.lb), np.array(m.ub)),
        options={"time_limit": float(time_limit), "mip_rel_gap": float(gap), "disp": False},
    )
    dt = time.perf_counter() - t0
    sign = -1.0 if neg else 1.0
    fun = None if res.fun is None else sign * float(res.fun)
    db = getattr(res, "mip_dual_bound", None)
    dual = None if db is None else sign * float(db)
    if relaxed:
        dual = fun
    return {
        "solver": "HiGHS (scipy.optimize.milp)",
        "status_code": int(res.status),
        "status": {0: "OPTIMAL", 1: "LIMIT", 2: "INFEASIBLE", 3: "UNBOUNDED", 4: "OTHER"}.get(int(res.status), "OTHER"),
        "objective": fun,
        "dual_bound": dual,
        "mip_gap": None if getattr(res, "mip_gap", None) is None else float(res.mip_gap),
        "nodes": int(getattr(res, "mip_node_count", 0) or 0),
        "runtime": dt,
        "x": None if res.x is None else {n: float(res.x[i]) for i, n in enumerate(m.names)},
        "message": str(getattr(res, "message", "")),
        "relaxed": relaxed,
    }


def _gurobi_available() -> bool:
    try:
        import gurobipy as gp
    except Exception:
        return False
    try:
        env = gp.Env(empty=True)
        env.setParam("OutputFlag", 0)
        env.start()
        env.dispose()
        return True
    except Exception:
        return False


def solve_gurobi(m: Model, relaxed: bool, time_limit: float, gap: float) -> dict:
    import gurobipy as gp
    from gurobipy import GRB

    mdl = gp.Model(m.name)
    mdl.Params.OutputFlag = 0
    mdl.Params.TimeLimit = float(time_limit)
    mdl.Params.MIPGap = float(gap)
    vtype = {"bin": GRB.BINARY, "int": GRB.INTEGER, "cont": GRB.CONTINUOUS}
    xs = []
    for i, n in enumerate(m.names):
        # gurobipy 不接受 None：无界必须写 ±GRB.INFINITY
        lo = -GRB.INFINITY if (math.isinf(m.lb[i]) and m.lb[i] < 0) else m.lb[i]
        hi = GRB.INFINITY if (math.isinf(m.ub[i]) and m.ub[i] > 0) else m.ub[i]
        xs.append(mdl.addVar(lb=lo, ub=hi, obj=m.obj[i], vtype=vtype[m.types[i]], name=n))
    mdl.ModelSense = GRB.MAXIMIZE if m.sense == "max" else GRB.MINIMIZE
    for c in m.cons:
        expr = gp.LinExpr()
        for j, coef in c["idx"].items():
            expr.add(xs[j], coef)
        if c["lb"] > -INF and c["ub"] < INF and abs(c["lb"] - c["ub"]) <= TOL:
            mdl.addConstr(expr == c["lb"], name=c["name"])
        else:
            if c["lb"] > -INF:
                mdl.addConstr(expr >= c["lb"], name=c["name"] + "_lo")
            if c["ub"] < INF:
                mdl.addConstr(expr <= c["ub"], name=c["name"] + "_up")
    mdl.update()
    if relaxed:
        rlx = mdl.relax()
        rlx.Params.OutputFlag = 0
        rlx.Params.TimeLimit = float(time_limit)
        t0 = time.perf_counter()
        rlx.optimize()
        dt = time.perf_counter() - t0
        st = rlx.Status
        fun = rlx.ObjVal if rlx.SolCount else None
        return {
            "solver": "Gurobi (relaxed)",
            "status_code": int(st),
            "status": {2: "OPTIMAL", 3: "INFEASIBLE", 4: "INF_OR_UNBD", 5: "UNBOUNDED", 9: "LIMIT"}.get(int(st), "OTHER"),
            "objective": fun,
            "dual_bound": fun,
            "mip_gap": 0.0 if st == 2 else None,
            "nodes": 0,
            "runtime": dt,
            "x": None if not rlx.SolCount else {v.VarName: float(v.X) for v in rlx.getVars()},
            "message": "",
            "relaxed": True,
        }
    t0 = time.perf_counter()
    mdl.optimize()
    dt = time.perf_counter() - t0
    st = mdl.Status
    has_sol = mdl.SolCount > 0
    return {
        "solver": "Gurobi",
        "status_code": int(st),
        "status": {2: "OPTIMAL", 3: "INFEASIBLE", 4: "INF_OR_UNBD", 5: "UNBOUNDED", 9: "LIMIT"}.get(int(st), "OTHER"),
        "objective": float(mdl.ObjVal) if has_sol else None,
        "dual_bound": float(mdl.ObjBound) if has_sol or st == 2 else None,
        "mip_gap": float(mdl.MIPGap) if has_sol else None,
        "nodes": int(mdl.NodeCount),
        "runtime": dt,
        "x": {v.VarName: float(v.X) for v in mdl.getVars()} if has_sol else None,
        "message": "",
        "relaxed": False,
    }


def solve(m: Model, solver: str, relaxed: bool, time_limit: float, gap: float) -> dict:
    if solver == "highs":
        return solve_scipy(m, relaxed, time_limit, gap)
    if solver == "gurobi":
        return solve_gurobi(m, relaxed, time_limit, gap)
    if _gurobi_available():
        try:
            return solve_gurobi(m, relaxed, time_limit, gap)
        except Exception as e:
            print(f"ℹ️  Gurobi 调用失败（{type(e).__name__}），改用 HiGHS。")
    return solve_scipy(m, relaxed, time_limit, gap)


# --------------------------------------------------------------------------- 输出


def fmt(v, nd=6) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return f"{v:.{nd}f}"
    return str(v)


def print_solve(m: Model, r: dict, show_solution: bool) -> None:
    print(f"\n实例：{m.name}    变量 {m.nvars}  约束 {len(m.cons)}  非零 {m.nnz}")
    print(f"求解器：{r['solver']}    {'LP 松弛' if r['relaxed'] else 'MILP'}")
    print(f"状态：{r['status']}   目标 {fmt(r['objective'])}   对偶界 {fmt(r['dual_bound'])}"
          f"   gap {fmt(None if r['mip_gap'] is None else r['mip_gap'] * 100, 4)}%"
          f"   节点 {r['nodes']}   用时 {r['runtime']:.3f}s")
    if r["x"]:
        chk = m.evaluate(r["x"])
        print(f"自校验：约束最大违约 {chk['constraint_violation']:.2e}"
              f"，界违约 {chk['bound_violation']:.2e}"
              f"，整数违约 {chk['integrality_violation']:.2e}"
              f" → {'可行' if chk['feasible'] else '不可行'}")
        if show_solution:
            nz = {k: v for k, v in r["x"].items() if abs(v) > 1e-9}
            print("解：" + ", ".join(f"{k}={fmt(v, 4)}" for k, v in nz.items()))


def save_result(path: str, m: Model, r: dict) -> None:
    out = {
        "model": m.name,
        "sense": m.sense,
        "solver": r["solver"],
        "relaxed": r["relaxed"],
        "status": r["status"],
        "objective": r["objective"],
        "dual_bound": r["dual_bound"],
        "mip_gap": r["mip_gap"],
        "nodes": r["nodes"],
        "runtime": r["runtime"],
        "nvars": m.nvars,
        "ncons": len(m.cons),
        "nnz": m.nnz,
        "x": r["x"],
    }
    Path(path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入 {path}")


# --------------------------------------------------------------------------- 子命令


def cmd_solve(args) -> int:
    m = load_model(args.spec)
    r = solve(m, args.solver, relaxed=args.relax, time_limit=args.time_limit, gap=args.gap)
    print_solve(m, r, show_solution=not args.quiet)
    if args.out:
        save_result(args.out, m, r)
    if r["status"] == "OPTIMAL":
        return 0
    if r["status"] in ("INFEASIBLE", "UNBOUNDED", "INF_OR_UNBD"):
        return 4
    return 3


def cmd_compare(args) -> int:
    specs = [load_model(p) for p in args.specs]
    if len(specs) < 2:
        raise SystemExit("compare 至少需要两个模型文件（例如 strong.json weak.json）")
    print(f"\n{'模型':<22}{'变量':>6}{'约束':>6}{'非零':>7}{'LP 界':>14}{'整数解':>14}{'int.gap%':>10}{'节点':>7}{'时间(s)':>9}")
    print("-" * 100)
    rows = []
    for m in specs:
        lp = solve(m, args.solver, relaxed=True, time_limit=args.time_limit, gap=0.0)
        ip = solve(m, args.solver, relaxed=False, time_limit=args.time_limit, gap=args.gap)
        lp_v, ip_v = lp["objective"], ip["objective"]
        gap = None
        if lp_v is not None and ip_v not in (None, 0) and abs(ip_v) > TOL:
            gap = (ip_v - lp_v) / abs(ip_v) * 100
        print(f"{m.name:<22}{m.nvars:>6}{len(m.cons):>6}{m.nnz:>7}{fmt(lp_v, 4):>14}"
              f"{fmt(ip_v, 4):>14}{fmt(gap, 4):>10}{ip['nodes']:>7}{ip['runtime']:>9.3f}")
        rows.append({"model": m.name, "lp": lp_v, "ip": ip_v, "gap": gap,
                     "nvars": m.nvars, "ncons": len(m.cons), "nnz": m.nnz,
                     "nodes": ip["nodes"], "runtime": ip["runtime"],
                     "solver": ip["solver"],
                     "lp_integral": _lp_is_integral(m, lp)})
        if lp_v is not None and ip_v is not None:
            tol = 1e-6 * max(1.0, abs(ip_v))
            if lp_v > ip_v + tol:
                print(f"   ⚠️  下界非法：LP 目标 {fmt(lp_v, 4)} > 整数解 {fmt(ip_v, 4)}，检查模型是否写错。")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.out}")
    if len(rows) >= 2 and all(x["lp"] is not None for x in rows):
        gaps = [x["gap"] for x in rows if x["gap"] is not None]
        solvers = {x.get("solver", "?") for x in rows}
        print(f"\n求解器：{'、'.join(sorted(solvers))}——"
              f"**节点数与时间只在同一求解器内可比**，换求解器后数值不可直接对比。")
        if len(gaps) >= 2:
            print(f"\n判定：integrality gap 最小 {min(gaps):.4f}%（{rows[gaps.index(min(gaps))]['model']}）"
                  f"，最大 {max(gaps):.4f}%（{rows[gaps.index(max(gaps))]['model']}）。"
                  "只写「在本次算例上」，不宣称 formulation 理论上 ideal。")
    return 0


def _lp_is_integral(m: Model, lp: dict) -> bool | None:
    if not lp.get("x"):
        return None
    return all(abs(lp["x"][n] - round(lp["x"][n])) <= 1e-6 for i, n in enumerate(m.names) if m.types[i] != "cont")


def cmd_check(args) -> int:
    m = load_model(args.spec)
    sol = json.loads(Path(args.solution).read_text(encoding="utf-8"))
    raw = sol.get("x", sol)
    if isinstance(raw, list):
        if len(raw) != m.nvars:
            raise SystemExit(f"解向量长度 {len(raw)} 与变量数 {m.nvars} 不一致")
        x = {n: float(raw[i]) for i, n in enumerate(m.names)}
    elif isinstance(raw, dict):
        x = {k: float(v) for k, v in raw.items() if k in m.index}
    else:
        raise SystemExit("解文件格式应为 {\"x\": {变量: 值}} 或 {\"x\": [值...]}")

    chk = m.evaluate(x)
    print(f"\n校验：{m.name}")
    print(f"目标值（按本模型重算）：{fmt(chk['objective'])}")
    if chk["missing"]:
        print(f"缺失变量 {len(chk['missing'])} 个：{', '.join(chk['missing'][:8])}"
              + (" ..." if len(chk["missing"]) > 8 else ""))
    print(f"界违约 {chk['bound_violation']:.3e}　整数违约 {chk['integrality_violation']:.3e}"
          f"　约束最大违约 {chk['constraint_violation']:.3e}")
    if chk["worst_constraint"]:
        nm, val, lo, hi = chk["worst_constraint"]
        print(f"最差约束：{nm}  左端 {fmt(val)}  区间 [{fmt(lo)}, {fmt(hi)}]")
    print("结论：" + ("可行 ✅" if chk["feasible"] else "不可行 ❌（上面的违约项即证据）"))
    return 0 if chk["feasible"] else 2


def cmd_demo(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    m_fac, n_dem, p, seed = 12, 30, 3, 7
    import random

    rng = random.Random(seed)
    fac = [(rng.random(), rng.random()) for _ in range(m_fac)]
    dem = [(rng.random(), rng.random()) for _ in range(n_dem)]
    w = [rng.randint(1, 10) for _ in range(n_dem)]
    d = [[math.dist(dem[j], fac[i]) for j in range(n_dem)] for i in range(m_fac)]

    def build(strong: bool) -> dict:
        vars_: dict[str, dict] = {f"y{i}": {"type": "bin", "obj": 0.0} for i in range(m_fac)}
        for i in range(m_fac):
            for j in range(n_dem):
                vars_[f"x{i}_{j}"] = {"type": "cont", "obj": w[j] * d[i][j], "lb": 0, "ub": 1}
        cons = []
        for j in range(n_dem):
            cons.append({"name": f"assign_{j}", "terms": {f"x{i}_{j}": 1 for i in range(m_fac)}, "lb": 1, "ub": 1})
        cons.append({"name": "cardinality", "terms": {f"y{i}": 1 for i in range(m_fac)}, "lb": p, "ub": p})
        if strong:
            for i in range(m_fac):
                for j in range(n_dem):
                    cons.append({"name": f"link_{i}_{j}", "terms": {f"x{i}_{j}": 1, f"y{i}": -1}, "ub": 0})
        else:
            for i in range(m_fac):
                terms = {f"x{i}_{j}": 1 for j in range(n_dem)}
                terms[f"y{i}"] = -n_dem
                cons.append({"name": f"link_{i}", "terms": terms, "ub": 0})
        return {"name": "pmed_strong" if strong else "pmed_weak", "sense": "min", "vars": vars_, "cons": cons}

    (out / "pmed_strong.json").write_text(json.dumps(build(True), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "pmed_weak.json").write_text(json.dumps(build(False), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "instance.json").write_text(json.dumps(
        {"m": m_fac, "n": n_dem, "p": p, "seed": seed, "facilities": fac, "demands": dem, "weights": w},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 已生成 p-median 样例到 {out}")
    print(f"   规模 m={m_fac} n={n_dem} p={p} seed={seed}；强形式 {m_fac * n_dem + m_fac} 变量 / "
          f"强 {n_dem + 1 + m_fac * n_dem} 条约束 vs 弱 {n_dem + 1 + m_fac} 条约束")
    print(f'   下一步：python milp_lab.py compare "{out / "pmed_strong.json"}" "{out / "pmed_weak.json"}"')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="MILP 模型实验室：解 / 松弛 / 强弱对比 / 解校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--solver", choices=["auto", "highs", "gurobi"], default="auto")
        p.add_argument("--time-limit", type=float, default=60.0, help="秒，默认 60")
        p.add_argument("--gap", type=float, default=0.0, help="相对 MIP gap，默认 0（求到最优）")

    p = sub.add_parser("solve", help="解 MILP（或加 --relax 求 LP）")
    p.add_argument("spec")
    p.add_argument("--relax", action="store_true", help="只求 LP 松弛")
    p.add_argument("--out", help="把结果写成 JSON")
    p.add_argument("--quiet", action="store_true", help="不打印解向量")
    common(p)
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("relax", help="等价于 solve --relax")
    p.add_argument("spec")
    p.add_argument("--out")
    p.add_argument("--quiet", action="store_true")
    common(p)
    p.set_defaults(func=lambda a: cmd_solve(argparse.Namespace(**{**vars(a), "relax": True})))

    p = sub.add_parser("compare", help="强弱 formulation 体检：LP 界 / 整数解 / integrality gap")
    p.add_argument("specs", nargs="+")
    p.add_argument("--out", help="把对比结果写成 JSON")
    common(p)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("check", help="独立校验一个解是否可行，并重算目标值")
    p.add_argument("spec")
    p.add_argument("solution")
    p.set_defaults(func=cmd_check, solver="auto", time_limit=60.0, gap=0.0)

    p = sub.add_parser("demo", help="生成 p-median 强弱两套模型")
    p.add_argument("--out", default="./lab")
    p.set_defaults(func=cmd_demo, solver="auto", time_limit=60.0, gap=0.0)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
