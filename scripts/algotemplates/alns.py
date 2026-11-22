"""通用 ALNS / LNS 引擎（纯标准库，与问题解耦）。

设计取舍：
- 接受准则用模拟退火，温度按「总迭代数」反推衰减率，避免拍系数。
- 算子权重用分段自适应（Ropke & Pisinger 的经典方案），保留最低权重防止算子被永久淘汰。
- 只做骨架该做的事：选算子、接受、记账、留痕。破坏与修复的语义由 Problem 适配器决定。

用法：
    from base import Problem
    from alns import ALNS
    res = ALNS(MyProblem(), max_iters=5000, seed=42).run()
    print(res.summary())
"""
from __future__ import annotations

import random
import time

from base import Problem, Result, Trace


class ALNS:
    def __init__(
        self,
        prob: Problem,
        *,
        max_iters: int = 2000,
        time_limit: float | None = None,
        segment: int = 50,
        reaction: float = 0.1,
        sigma: tuple[float, float, float] = (33.0, 9.0, 13.0),
        min_weight: float = 0.05,
        T0: float | None = None,
        T_end_ratio: float = 1e-3,
        seed: int = 0,
        record_every: int = 1,
        max_fail_streak: int = 200,
        log_every: int = 0,
    ) -> None:
        self.prob = prob
        self.max_iters = max_iters
        self.time_limit = time_limit
        self.segment = max(1, segment)
        self.reaction = reaction
        self.sigma = sigma
        self.min_weight = min_weight
        self.T0_given = T0
        self.T_end_ratio = T_end_ratio
        self.seed = seed
        self.record_every = max(1, record_every)
        self.max_fail_streak = max_fail_streak
        self.log_every = log_every

        self.destroy = list(prob.destroy_ops())
        self.repair = list(prob.repair_ops())
        if not self.destroy or not self.repair:
            raise ValueError("Problem 必须提供 destroy_ops() 与 repair_ops()")
        self.names_d = [n for n, _ in self.destroy]
        self.names_r = [n for n, _ in self.repair]

    # ---------- 内部工具 ----------

    @staticmethod
    def _roulette(weights: list[float], rng: random.Random) -> int:
        total = sum(weights)
        r = rng.random() * total
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r <= acc:
                return i
        return len(weights) - 1

    def _calibrate_t0(self, rng: random.Random, probes: int = 30) -> float:
        """用初始解附近的劣化幅度标定初温，使初始接受率约 80%。

        T0 = -mean(Δ+) / ln(0.8) ≈ 4.48 * mean(Δ+)
        """
        if self.T0_given is not None:
            return float(self.T0_given)
        cur = self.prob.initial(rng)
        base = self.prob.evaluate(cur)
        deltas: list[float] = []
        for _ in range(probes):
            cand = cur
            try:
                _, d_fn = self.destroy[rng.randrange(len(self.destroy))]
                _, r_fn = self.repair[rng.randrange(len(self.repair))]
                cand = r_fn(d_fn(self.prob.copy(cand), rng), rng)
            except Exception:
                continue
            if not self.prob.is_feasible(cand):
                continue
            d = self.prob.evaluate(cand) - base
            if d > 0:
                deltas.append(d)
        if not deltas:
            return 1.0
        return 4.48 * (sum(deltas) / len(deltas))

    def _cooling(self, T0: float, T_end: float) -> float:
        if self.time_limit is not None:
            return 0.9995  # 无迭代预算时用保守固定衰减
        if T0 <= 0:
            return 1.0
        return (T_end / T0) ** (1.0 / max(1, self.max_iters))

    # ---------- 主流程 ----------

    def run(self) -> Result:
        rng = random.Random(self.seed)
        t_start = time.perf_counter()

        T0 = self._calibrate_t0(rng)
        T_end = T0 * self.T_end_ratio
        cooling = self._cooling(T0, T_end)

        cur = self.prob.initial(rng)
        cur_obj = self.prob.evaluate(cur)
        best, best_obj = cur, cur_obj

        wd = [1.0] * len(self.destroy)
        wr = [1.0] * len(self.repair)
        score_d = [0.0] * len(self.destroy)
        score_r = [0.0] * len(self.repair)
        cnt_d = [0] * len(self.destroy)
        cnt_r = [0] * len(self.repair)

        trace = Trace()
        trace.add(0, cur_obj, best_obj)

        T = T0
        it = 0
        fails = 0
        fail_streak = 0
        eps = 1e-9 * max(1.0, abs(best_obj))

        for it in range(1, self.max_iters + 1):
            if self.time_limit is not None and time.perf_counter() - t_start > self.time_limit:
                it -= 1
                break

            di = self._roulette(wd, rng)
            ri = self._roulette(wr, rng)
            cnt_d[di] += 1
            cnt_r[ri] += 1

            try:
                cand = self.destroy[di][1](self.prob.copy(cur), rng)
                cand = self.repair[ri][1](cand, rng)
                feasible = self.prob.is_feasible(cand)
            except Exception:
                feasible = False
                cand = None

            if not feasible:
                fails += 1
                fail_streak += 1
                if fail_streak > self.max_fail_streak:
                    raise RuntimeError(
                        f"连续 {fail_streak} 次破坏-修复后不可行。"
                        f"检查修复算子是否保证可行，或把惩罚放进目标。"
                    )
                continue
            fail_streak = 0

            obj = self.prob.evaluate(cand)
            s1, s2, s3 = self.sigma

            if obj < best_obj - eps:
                best, best_obj = cand, obj
                cur, cur_obj = cand, obj
                score_d[di] += s1
                score_r[ri] += s1
            elif obj < cur_obj - eps:
                cur, cur_obj = cand, obj
                score_d[di] += s2
                score_r[ri] += s2
            elif rng.random() < _accept(cur_obj - obj, T):
                cur, cur_obj = cand, obj
                score_d[di] += s3
                score_r[ri] += s3

            T *= cooling

            if self.record_every == 1 or it % self.record_every == 0:
                trace.add(it, cur_obj, best_obj)
            if self.log_every and it % self.log_every == 0:
                print(f"  it={it} cur={cur_obj:.4f} best={best_obj:.4f} T={T:.4g} fails={fails}")

            if it % self.segment == 0:
                _update(wd, score_d, cnt_d, self.reaction, self.min_weight)
                _update(wr, score_r, cnt_r, self.reaction, self.min_weight)
                score_d = [0.0] * len(self.destroy)
                score_r = [0.0] * len(self.repair)

        elapsed = time.perf_counter() - t_start
        meta = {
            "T0": round(T0, 4),
            "fails": fails,
            "final_weight_d": {n: round(w, 3) for n, w in zip(self.names_d, wd)},
            "final_weight_r": {n: round(w, 3) for n, w in zip(self.names_r, wr)},
        }
        return Result("ALNS", best, best_obj, trace, it, elapsed, meta)


def _accept(delta: float, T: float) -> float:
    """delta = 当前 - 候选（越小越好）。返回接受概率。"""
    if delta >= 0:
        return 1.0
    if T <= 0:
        return 0.0
    return pow(2.718281828459045, delta / T)


def _update(weights: list[float], score: list[float], cnt: list[int],
            reaction: float, min_weight: float) -> None:
    for i in range(len(weights)):
        if cnt[i] == 0:
            continue
        obs = score[i] / cnt[i]
        weights[i] = (1.0 - reaction) * weights[i] + reaction * obs
        if weights[i] < min_weight:
            weights[i] = min_weight
        cnt[i] = 0


if __name__ == "__main__":
    from demo_tsp import TSP

    p = TSP(n=40, seed=1)
    res = ALNS(p, max_iters=1500, seed=42, log_every=250).run()
    print(res.summary())
    print("  首达最终值×1.02 所需迭代:", res.trace.steps_to(res.best_obj * 1.02))
    print("  面积(高于最终值):", round(res.trace.area_above_final(), 2))
