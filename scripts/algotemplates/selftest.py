"""算法实现自测：增量评价、可行性一致性、可复现性。

为什么需要它：增量评价写错了**不会报错**，只会让搜索方向系统性偏移，
表现为「算法能跑，但总也搜不到好解」，而且极难从结果反推原因。

三项检查：
1. delta vs 全量重算 —— 逐移动对撞，相对误差超过容差即失败
2. 可行性一致性 —— 增量维护的可行性标记 vs 独立校验器
3. 同种子可复现 —— 两次运行结果逐位一致

用法（接入自己的问题）：
    from selftest import check_delta, check_feasibility, check_determinism
    check_delta(MyProblem(), n_cases=50)
    check_feasibility(MyProblem(), checker=lambda s: my_validator(s), n_cases=20)

命令行：
    python selftest.py        # 用 demo_tsp 跑一遍，并验证「写错能被抓到」
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    name: str
    passed: bool
    cases: int
    worst_error: float = 0.0
    failures: list = field(default_factory=list)

    def report(self) -> str:
        flag = "PASS" if self.passed else "FAIL"
        msg = f"[{flag}] {self.name}: {self.cases} 个用例, 最大相对误差 {self.worst_error:.3e}"
        for f in self.failures[:3]:
            msg += f"\n        {f}"
        return msg


def check_delta(prob, n_cases: int = 50, moves_per_case: int = 20,
                seed: int = 0, tol: float = 1e-9) -> CheckResult:
    """逐移动比较 prob.delta 与 evaluate(apply) - evaluate。

    采用随机游走：每步都基于上一个新解继续走，能覆盖到解结构变化的各个阶段，
    比「只在初始解上测」更容易暴露边界错误（如 i=0、j=n-1）。
    """
    rng = random.Random(seed)
    worst = 0.0
    total = 0
    failures: list[str] = []

    for _ in range(n_cases):
        sol = prob.initial(rng)
        if not prob.is_feasible(sol):
            failures.append("initial() 返回了不可行解")
            break
        for _ in range(moves_per_case):
            moves = list(prob.moves(sol))
            if not moves:
                failures.append("moves() 为空")
                return CheckResult("增量评价 vs 全量", False, total, worst, failures)
            mv = rng.choice(moves)
            new = prob.apply(sol, mv)
            if not prob.is_feasible(new):
                # 邻域产生了不可行解：这是设计问题，但不属于增量错误，跳过
                continue
            d_inc = prob.delta(sol, mv)
            d_full = prob.evaluate(new) - prob.evaluate(sol)
            err = abs(d_inc - d_full) / max(1.0, abs(d_full), abs(d_inc))
            worst = max(worst, err)
            total += 1
            if err > tol:
                failures.append(
                    f"move={mv!r}: delta={d_inc:.10f} 全量={d_full:.10f} 相对误差={err:.2e}"
                )
                if len(failures) >= 5:
                    return CheckResult("增量评价 vs 全量", False, total, worst, failures)
            sol = new

    return CheckResult("增量评价 vs 全量", not failures, total, worst, failures)


def check_feasibility(prob, checker, n_cases: int = 20, moves_per_case: int = 20,
                      seed: int = 0) -> CheckResult:
    """比较 prob.is_feasible 与独立校验器。

    checker 必须是**不依赖 Problem 内部实现**的函数（例如直接读数据重算约束），
    否则测的是同一份逻辑，等于没测。
    """
    rng = random.Random(seed)
    total = 0
    failures: list[str] = []
    for _ in range(n_cases):
        sol = prob.initial(rng)
        for _ in range(moves_per_case):
            if not prob.is_feasible(sol):
                break
            moves = list(prob.moves(sol))
            if not moves:
                break
            sol = prob.apply(sol, rng.choice(moves))
            total += 1
            mine = prob.is_feasible(sol)
            theirs = bool(checker(sol))
            if mine != theirs:
                failures.append(f"不一致: is_feasible={mine} 校验器={theirs} sol={sol!r}")
                if len(failures) >= 5:
                    break
    return CheckResult("可行性标记 vs 独立校验器", not failures, total, 0.0, failures)


def check_determinism(run_fn, seed: int = 0) -> CheckResult:
    """同一 seed 两次运行必须完全一致。run_fn() 内部必须自己设好种子。"""
    a = run_fn()
    b = run_fn()
    same = (abs(a.best_obj - b.best_obj) < 1e-12) and (a.iterations == b.iterations)
    failures = [] if same else [
        f"obj {a.best_obj:.12f} vs {b.best_obj:.12f}; iters {a.iterations} vs {b.iterations}"
    ]
    return CheckResult(f"同种子可复现 (seed={seed})", same, 2, 0.0, failures)


# ---------------- 命令行：验证 demo 与「测试本身有效」 ----------------

if __name__ == "__main__":
    from base import Problem
    from demo_tsp import TSP

    prob = TSP(n=40, seed=1)
    results = [
        check_delta(prob, n_cases=30, moves_per_case=20),
    ]

    # 负对照：故意把 delta 写成常数，自测必须报 FAIL，否则说明自测无效
    class BuggyTSP(TSP):
        def delta(self, sol, move):
            return 0.0

    neg = check_delta(BuggyTSP(n=20, seed=2), n_cases=5, moves_per_case=5)
    results.append(
        CheckResult("负对照：故意写错的 delta 应被抓住",
                    not neg.passed, neg.cases, neg.worst_error,
                    [] if not neg.passed else ["自测未能识别错误增量，测试本身失效"])
    )

    # 可复现性
    from alns import ALNS
    from ga import GA
    results.append(check_determinism(lambda: ALNS(prob, max_iters=200, seed=7).run()))
    results.append(check_determinism(lambda: GA(prob, pop_size=20, max_iters=40, seed=7).run()))

    ok = True
    for r in results:
        print(r.report())
        ok = ok and r.passed

    print("\n总体:", "全部通过" if ok else "存在失败项，修好再跑")
    raise SystemExit(0 if ok else 1)
