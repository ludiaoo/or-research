"""通用模拟退火引擎（纯标准库）。

补的是「SA 只有一句『模拟退火最稳』」这个空缺：四个量全部自动标定或可追溯。

    T0     ← 采样邻域劣化幅度，使初始接受率约 80%
    alpha  ← 由总迭代预算与末温反推，而不是拍一个 0.99
    L      ← 每个温度层的尝试次数（链长），与邻域规模同阶
    T_end  ← 使最差移动接受概率 < 1%

用法：
    from sa import SA
    res = SA(MyProblem(), max_iters=200_000, L=200, seed=42).run()
"""
from __future__ import annotations

import math
import random
import time

from base import Problem, Result, Trace


class SA:
    def __init__(
        self,
        prob: Problem,
        *,
        max_iters: int = 100_000,
        time_limit: float | None = None,
        L: int = 100,
        T0: float | None = None,
        T_end_ratio: float = 1e-3,
        alpha: float | None = None,
        reheat_after: int | None = None,
        reheat_factor: float = 2.0,
        seed: int = 0,
        record_every: int = 1,
        log_every: int = 0,
    ) -> None:
        self.prob = prob
        self.max_iters = max_iters
        self.time_limit = time_limit
        self.L = max(1, L)
        self.T0_given = T0
        self.T_end_ratio = T_end_ratio
        self.alpha_given = alpha
        self.reheat_after = reheat_after
        self.reheat_factor = reheat_factor
        self.seed = seed
        self.record_every = max(1, record_every)
        self.log_every = log_every

    def _calibrate_t0(self, rng: random.Random, sol, base: float, probes: int = 100) -> float:
        if self.T0_given is not None:
            return float(self.T0_given)
        pos: list[float] = []
        for _ in range(probes):
            cand, _ = self.prob.neighbor(sol, rng)
            if not self.prob.is_feasible(cand):
                continue
            d = self.prob.evaluate(cand) - base
            if d > 0:
                pos.append(d)
        if not pos:
            return 1.0
        # 使平均劣化的接受概率为 0.8
        return -sum(pos) / len(pos) / math.log(0.8)

    def run(self) -> Result:
        rng = random.Random(self.seed)
        t_start = time.perf_counter()

        cur = self.prob.initial(rng)
        if not self.prob.is_feasible(cur):
            raise ValueError("初始解不可行：请先修 initial()，不要在 SA 里用罚函数兜底")
        cur_obj = self.prob.evaluate(cur)
        best, best_obj = cur, cur_obj

        T0 = self._calibrate_t0(rng, cur, cur_obj)
        T_end = max(T0 * self.T_end_ratio, 1e-12)

        if self.alpha_given is not None:
            alpha = float(self.alpha_given)
        else:
            n_levels = max(1, self.max_iters // self.L)
            alpha = (T_end / T0) ** (1.0 / n_levels) if T0 > 0 else 1.0

        trace = Trace()
        trace.add(0, cur_obj, best_obj)

        T = T0
        it = 0
        stall = 0          # 连续无改进的温度层数
        reheats = 0
        eps = 1e-9 * max(1.0, abs(best_obj))

        while it < self.max_iters:
            if self.time_limit is not None and time.perf_counter() - t_start > self.time_limit:
                break

            layer_start = cur_obj
            for _ in range(self.L):
                if it >= self.max_iters:
                    break
                it += 1
                cand, _mv = self.prob.neighbor(cur, rng)
                if not self.prob.is_feasible(cand):
                    continue
                obj = self.prob.evaluate(cand)
                d = obj - cur_obj

                if d <= 0 or rng.random() < math.exp(-d / T if T > 0 else 0.0):
                    cur, cur_obj = cand, obj
                    if obj < best_obj - eps:
                        best, best_obj = cand, obj

                if self.record_every == 1 or it % self.record_every == 0:
                    trace.add(it, cur_obj, best_obj)

            # 停滞判据看「本层是否改进了当前解」，而不是是否有新全局最优
            if cur_obj >= layer_start - eps:
                stall += 1
            else:
                stall = 0

            T *= alpha

            if self.reheat_after and stall >= self.reheat_after:
                T = min(max(T * self.reheat_factor, T0 * 0.1), T0)
                stall = 0
                reheats += 1
                if self.log_every:
                    print(f"  [reheat] it={it} -> T={T:.4g}")

            if self.log_every and it % self.log_every < self.L:
                print(f"  it={it} cur={cur_obj:.4f} best={best_obj:.4f} T={T:.4g}")

        elapsed = time.perf_counter() - t_start
        meta = {
            "T0": round(T0, 6),
            "T_end": round(T_end, 8),
            "alpha": round(alpha, 6),
            "L": self.L,
            "reheats": reheats,
        }
        return Result("SA", best, best_obj, trace, it, elapsed, meta)


if __name__ == "__main__":
    from demo_tsp import TSP

    p = TSP(n=40, seed=1)
    res = SA(p, max_iters=40_000, L=200, seed=42, reheat_after=8, log_every=10_000).run()
    print(res.summary())
    print("  首达最终值×1.02 所需迭代:", res.trace.steps_to(res.best_obj * 1.02))
