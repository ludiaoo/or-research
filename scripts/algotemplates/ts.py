"""通用禁忌搜索引擎（纯标准库）。

补的是「TS 只有禁忌长度和藐视准则两条」这个空缺。四个要素都落成了代码：

    禁忌对象  记移动键（Problem.move_key 规范化），不是整解
    禁忌表    哈希字典 + 到期迭代，O(1) 查询，不用线性扫描
    特赦      候选优于历史最优时无视禁忌
    多样化    长期停滞时从当前解做 k 步随机扰动重启

用法：
    from ts import TabuSearch
    res = TabuSearch(MyProblem(), max_iters=3000, tenure=10, seed=42).run()
"""
from __future__ import annotations

import random
import time
from collections import Counter

from base import Problem, Result, Trace


class TabuSearch:
    def __init__(
        self,
        prob: Problem,
        *,
        max_iters: int = 3000,
        tenure: int = 10,
        candidate_limit: int | None = 200,
        time_limit: float | None = None,
        aspiration: bool = True,
        freq_penalty: float = 0.0,
        diversify_after: int | None = None,
        perturb_steps: int = 20,
        recompute: bool = True,
        seed: int = 0,
        record_every: int = 1,
        log_every: int = 0,
    ) -> None:
        self.prob = prob
        self.max_iters = max_iters
        self.tenure = max(1, tenure)
        self.candidate_limit = candidate_limit
        self.time_limit = time_limit
        self.aspiration = aspiration
        self.freq_penalty = freq_penalty
        self.diversify_after = diversify_after
        self.perturb_steps = perturb_steps
        self.recompute = recompute
        self.seed = seed
        self.record_every = max(1, record_every)
        self.log_every = log_every

    def _sample_moves(self, sol, rng: random.Random) -> list:
        mv = list(self.prob.moves(sol))
        if self.candidate_limit is not None and len(mv) > self.candidate_limit:
            return rng.sample(mv, self.candidate_limit)
        return mv

    def _perturb(self, sol, rng: random.Random):
        for _ in range(self.perturb_steps):
            cand, _mv = self.prob.neighbor(sol, rng)
            if self.prob.is_feasible(cand):
                sol = cand
        return sol

    def run(self) -> Result:
        rng = random.Random(self.seed)
        t_start = time.perf_counter()

        cur = self.prob.initial(rng)
        if not self.prob.is_feasible(cur):
            raise ValueError("初始解不可行：请先修 initial()")
        cur_obj = self.prob.evaluate(cur)
        best, best_obj = cur, cur_obj

        trace = Trace()
        trace.add(0, cur_obj, best_obj)

        tabu: dict = {}            # move_key -> 解禁迭代
        freq: Counter = Counter()  # 长期频率记忆

        it = 0
        stall = 0
        restarts = 0
        all_tabu_hits = 0
        eps = 1e-9 * max(1.0, abs(best_obj))

        for it in range(1, self.max_iters + 1):
            if self.time_limit is not None and time.perf_counter() - t_start > self.time_limit:
                it -= 1
                break

            cands = self._sample_moves(cur, rng)
            if not cands:
                break

            best_mv = None
            best_val = float("inf")
            best_delta = 0.0
            fallback_mv = None
            fallback_val = float("inf")
            fallback_delta = 0.0

            for mv in cands:
                key = self.prob.move_key(mv)
                d = self.prob.delta(cur, mv)
                val = cur_obj + d
                if self.freq_penalty:
                    val += self.freq_penalty * freq[key]

                if val < fallback_val:
                    fallback_val, fallback_delta, fallback_mv = val, d, mv

                is_tabu = tabu.get(key, 0) > it
                if is_tabu and not (self.aspiration and val < best_obj - eps):
                    continue

                if val < best_val:
                    best_val, best_delta, best_mv = val, d, mv

            if best_mv is None:
                # 候选全部被禁忌：允许走最优候选作为逃逸，并计入统计
                all_tabu_hits += 1
                if fallback_mv is None:
                    break
                best_mv, best_delta = fallback_mv, fallback_delta

            key = self.prob.move_key(best_mv)
            cur = self.prob.apply(cur, best_mv)
            # 默认重算目标，避免增量误差沿迭代累积；性能敏感时可关掉
            cur_obj = self.prob.evaluate(cur) if self.recompute else cur_obj + best_delta

            if not self.prob.is_feasible(cur):
                raise RuntimeError(
                    "apply() 产生了不可行解：要么邻域只在可行域内移动，"
                    "要么把可行性交给校验器并在 apply 里修复"
                )

            tabu[key] = it + self.tenure
            freq[key] += 1

            if cur_obj < best_obj - eps:
                best, best_obj = cur, cur_obj
                stall = 0
            else:
                stall += 1

            if self.record_every == 1 or it % self.record_every == 0:
                trace.add(it, cur_obj, best_obj)

            if self.log_every and it % self.log_every == 0:
                print(f"  it={it} cur={cur_obj:.4f} best={best_obj:.4f} stall={stall}")

            if self.diversify_after and stall >= self.diversify_after:
                cur = self._perturb(cur, rng)
                cur_obj = self.prob.evaluate(cur)
                tabu.clear()
                stall = 0
                restarts += 1

        elapsed = time.perf_counter() - t_start
        meta = {
            "tenure": self.tenure,
            "restarts": restarts,
            "all_tabu_escape": all_tabu_hits,
            "distinct_moves": len(freq),
        }
        return Result("TS", best, best_obj, trace, it, elapsed, meta)


if __name__ == "__main__":
    from demo_tsp import TSP

    p = TSP(n=40, seed=1)
    res = TabuSearch(p, max_iters=2000, tenure=10, seed=42, log_every=500).run()
    print(res.summary())
    print("  首达最终值×1.02 所需迭代:", res.trace.steps_to(res.best_obj * 1.02))
