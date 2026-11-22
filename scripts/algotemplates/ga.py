"""通用遗传算法引擎（纯标准库）。

补的是「GA 就是交叉+变异」这个说法留下的空洞：GA 用在运筹问题上，
真正决定成败的不是算子名字，而是**交叉后还能不能是可行解**。

三种编码的处理，按问题结构选，不要混：

| 编码 | 交叉 | 可行性 | 适用 |
|---|---|---|---|
| 排列（路径、排产顺序） | OX / PMX | 天然可行（每个元素恰出现一次） | TSP、调度、指派 |
| 0-1 / 整数向量 | 均匀交叉 | 可能越界 → 需 repair 或拒绝 | 选址、装箱、分配 |
| 实数向量 | SBX / 算术交叉 | 通常可行（受边界裁剪） | 参数标定、连续松弛 |

本引擎默认按**排列**处理（OX/PX），因为运筹里最常见的 GA 用法就是排序列。
非排列问题请实现 `Problem.crossover` / `Problem.mutate` / `Problem.repair`，
否则交叉产生的不可行子代会被 `is_feasible` 大量拒绝，表现为「跑了很多代却没进展」。

用法：
    from ga import GA
    res = GA(MyProblem(), pop_size=80, max_iters=600, seed=42).run()

纪律：
- 不许用罚函数掩盖不可行（见 references/算法手册.md §6）：被拒绝的子代要计数，
  拒绝率 > 50% 说明编码或交叉算子选错了，不是参数没调好。
- 种群要去重（按解签名），否则十几代后整群都是同一个体的副本，交叉退化为复制。
"""
from __future__ import annotations

import random
import time

from base import Problem, Result, Trace


class GA:
    def __init__(
        self,
        prob: Problem,
        *,
        pop_size: int = 60,
        max_iters: int = 500,
        time_limit: float | None = None,
        elite: int = 2,
        tournament: int = 3,
        crossover_rate: float = 0.9,
        mutation_rate: float = 0.2,
        mutation_boost: float = 1.5,
        crossover: str = "ox",
        stagnation_restart: int = 80,
        local_search: int = 0,
        local_search_frac: float = 0.35,
        immigrants: int = 2,
        seed: int = 0,
        record_every: int = 1,
        log_every: int = 0,
    ) -> None:
        self.prob = prob
        self.pop_size = max(4, pop_size)
        self.max_iters = max_iters
        self.time_limit = time_limit
        self.elite = max(0, min(elite, self.pop_size - 2))
        self.tournament = max(2, tournament)
        self.crossover_rate = crossover_rate
        self.mutation_rate = mutation_rate
        self.mutation_boost = mutation_boost
        self.crossover_kind = crossover.lower()
        self.stagnation_restart = stagnation_restart
        self.local_search = local_search
        self.local_search_frac = local_search_frac
        self.immigrants = max(0, immigrants)
        self.seed = seed
        self.record_every = max(1, record_every)
        self.log_every = log_every

    # ------------------------------------------------------------ 算子

    def _ox(self, a, b, rng):
        """顺序交叉（Order Crossover）：保留 a 的一段，其余按 b 的顺序补齐。"""
        n = len(a)
        if n < 2:
            return list(a)
        i, j = sorted(rng.sample(range(n), 2))
        child = [None] * n
        child[i:j + 1] = a[i:j + 1]
        fill = [x for x in b if x not in set(child[i:j + 1])]
        k = 0
        for p in range(n):
            if child[p] is None:
                child[p] = fill[k]
                k += 1
        return child

    def _pmx(self, a, b, rng):
        """部分匹配交叉（Partially Matched Crossover）。"""
        n = len(a)
        if n < 2:
            return list(a)
        i, j = sorted(rng.sample(range(n), 2))
        child = [None] * n
        child[i:j + 1] = a[i:j + 1]
        seg = set(child[i:j + 1])
        for p in range(i, j + 1):
            if b[p] in seg:
                continue
            pos = p
            while True:
                pos = b.index(a[pos])
                if child[pos] is None:
                    child[pos] = b[p]
                    break
        for p in range(n):
            if child[p] is None:
                child[p] = b[p]
        return child

    def _crossover(self, a, b, rng):
        fn = getattr(self.prob, "crossover", None)
        if callable(fn):
            return fn(a, b, rng)
        if self.crossover_kind == "pmx":
            return self._pmx(a, b, rng)
        return self._ox(a, b, rng)

    def _mutate(self, sol, rng):
        fn = getattr(self.prob, "mutate", None)
        if callable(fn):
            return fn(sol, rng)
        n = len(sol)
        if n < 2:
            return list(sol)
        t = list(sol)
        kind = rng.random()
        i, j = rng.sample(range(n), 2)
        if kind < 0.4:
            t[i], t[j] = t[j], t[i]                      # swap
        elif kind < 0.7:
            v = t.pop(i)                                  # insert
            t.insert(j, v)
        else:
            lo, hi = min(i, j), max(i, j)                 # reverse
            t[lo:hi + 1] = reversed(t[lo:hi + 1])
        return t

    def _signature(self, sol):
        fn = getattr(self.prob, "signature", None)
        if callable(fn):
            return fn(sol)
        try:
            return tuple(sol)
        except TypeError:
            return repr(sol)

    def _repair(self, sol, rng):
        fn = getattr(self.prob, "repair", None)
        return fn(sol, rng) if callable(fn) else sol

    def _local_search(self, sol, rng, budget):
        """随机抽 budget 个候选移动做首次改进（memetic 的一步）。

        GA 单独用于组合优化通常打不过「构造 + 局部搜索」，这是教材级事实：
        交叉提供重组，局部搜索提供精修。预算给小（几十次 delta），别做成完整爬山，
        否则每代开销与种群规模相乘，反而更慢。
        需要 Problem 实现 moves/apply/delta；未实现时静默跳过。
        """
        if budget <= 0:
            return sol
        try:
            moves = list(self.prob.moves(sol))
        except (NotImplementedError, AttributeError):
            return sol
        if not moves:
            return sol
        rng.shuffle(moves)
        cur = sol
        for mv in moves[:budget]:
            if self.prob.delta(cur, mv) < -1e-12:
                cand = self.prob.apply(cur, mv)
                if self.prob.is_feasible(cand):
                    cur = cand
        return cur

    # ------------------------------------------------------------ 主循环

    def _init_population(self, rng):
        pop, seen = [], set()
        first = self.prob.initial(rng)
        if not self.prob.is_feasible(first):
            raise ValueError("初始解不可行：请先修 initial()，不要在 GA 里用罚函数兜底")
        pop.append(first)
        seen.add(self._signature(first))

        attempts = 0
        while len(pop) < self.pop_size and attempts < self.pop_size * 20:
            attempts += 1
            base = self.prob.initial(rng) if attempts % 5 == 0 else rng.choice(pop)
            cand = self._repair(self._mutate(base, rng), rng)
            if not self.prob.is_feasible(cand):
                continue
            sig = self._signature(cand)
            if sig in seen:
                continue
            seen.add(sig)
            pop.append(cand)
        return pop

    def _select(self, pop, objs, rng):
        idx = rng.sample(range(len(pop)), min(self.tournament, len(pop)))
        return pop[min(idx, key=lambda i: objs[i])]

    def run(self) -> Result:
        rng = random.Random(self.seed)
        t0 = time.perf_counter()

        pop = self._init_population(rng)
        objs = [self.prob.evaluate(s) for s in pop]
        best_i = min(range(len(pop)), key=lambda i: objs[i])
        best, best_obj = pop[best_i], objs[best_i]

        trace = Trace()
        trace.add(0, best_obj, best_obj)

        it = 0
        stall = 0
        rejects = 0
        restarts = 0
        eps = 1e-9 * max(1.0, abs(best_obj))

        while it < self.max_iters:
            if self.time_limit is not None and time.perf_counter() - t0 > self.time_limit:
                break
            it += 1

            order = sorted(range(len(pop)), key=lambda i: objs[i])
            new_pop = [pop[i] for i in order[: self.elite]]
            new_objs = [objs[i] for i in order[: self.elite]]

            while len(new_pop) < self.pop_size:
                parent_a = self._select(pop, objs, rng)
                parent_b = self._select(pop, objs, rng)
                if rng.random() < self.crossover_rate:
                    child = self._crossover(parent_a, parent_b, rng)
                else:
                    child = list(parent_a)

                rate = min(0.6, self.mutation_rate * (self.mutation_boost if stall > 0 else 1.0))
                if rng.random() < rate:
                    child = self._mutate(child, rng)

                child = self._repair(child, rng)
                if not self.prob.is_feasible(child):
                    rejects += 1
                    child = list(parent_a)          # 拒绝不等于放行：退回父代
                elif self.local_search and rng.random() < self.local_search_frac:
                    child = self._local_search(child, rng, self.local_search)
                new_pop.append(child)
                new_objs.append(self.prob.evaluate(child))

            pop, objs = new_pop, new_objs

            # 随机移民：替换最差个体，防止整群收敛到同一个 basin
            if self.immigrants:
                bi_now = min(range(len(pop)), key=lambda i: objs[i])
                slots = [i for i in range(len(pop)) if i != bi_now]
                for i in range(min(self.immigrants, len(slots))):
                    worst = max(slots, key=lambda k: objs[k])
                    cand = self._repair(self._mutate(self.prob.initial(rng), rng), rng)
                    if self.prob.is_feasible(cand):
                        pop[worst] = cand
                        objs[worst] = self.prob.evaluate(cand)
                    slots.remove(worst)

            bi = min(range(len(pop)), key=lambda i: objs[i])
            if objs[bi] < best_obj - eps:
                best, best_obj = pop[bi], objs[bi]
                stall = 0
            else:
                stall += 1

            if self.record_every == 1 or it % self.record_every == 0:
                trace.add(it, min(objs), best_obj)

            # 停滞重启：保最优，其余重新播种（不整体清零，否则等于白跑）
            if self.stagnation_restart and stall >= self.stagnation_restart:
                restarts += 1
                keep = [best]
                keep_obj = [best_obj]
                while len(keep) < self.pop_size:
                    # 一半围绕最优解扰动，一半重新构造：只围绕最优会迅速同质化
                    base = best if len(keep) % 2 else self.prob.initial(rng)
                    cand = self._repair(self._mutate(base, rng), rng)
                    if not self.prob.is_feasible(cand):
                        continue
                    keep.append(cand)
                    keep_obj.append(self.prob.evaluate(cand))
                pop, objs = keep, keep_obj
                stall = 0
                if self.log_every:
                    print(f"  [restart] it={it} best={best_obj:.4f}")

            if self.log_every and it % self.log_every == 0:
                print(f"  it={it} best={best_obj:.4f} mean={sum(objs) / len(objs):.4f}")

        elapsed = time.perf_counter() - t0
        meta = {
            "pop": self.pop_size,
            "cross": "custom" if callable(getattr(self.prob, "crossover", None)) else self.crossover_kind,
            "mut": round(self.mutation_rate, 3),
            "rejects": rejects,
            "restarts": restarts,
            "uniq": len({self._signature(s) for s in pop}),
        }
        return Result("GA", best, best_obj, trace, it, elapsed, meta)


if __name__ == "__main__":
    from demo_tsp import TSP

    p = TSP(n=40, seed=1)
    base = p.evaluate(p.initial(random.Random(0)))
    print(f"最近邻基线 {base:.3f}")

    plain = GA(p, pop_size=60, max_iters=400, seed=42, stagnation_restart=60).run()
    print(plain.summary(), f"  相对基线 {(base - plain.best_obj) / base * 100:+.2f}%")

    # 加一步局部搜索（memetic）：交叉负责重组，局部搜索负责精修
    memetic = GA(p, pop_size=60, max_iters=400, seed=42, stagnation_restart=60,
                 local_search=30).run()
    print(memetic.summary(), f"  相对基线 {(base - memetic.best_obj) / base * 100:+.2f}%")
