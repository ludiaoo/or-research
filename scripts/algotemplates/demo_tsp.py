"""完整适配器示例：欧氏 TSP。

这个文件的作用有两个：
1. 演示 Problem 接口该怎么实现——照它改成你的问题即可；
2. 作为四个引擎的固定基准（同一算例、同一种子），用来验证骨架本身没坏。

故意保留的两处设计，正好对应手册里的纪律：
- `delta` 给出 O(1) 的 2-opt 增量（正确做法）；
- `moves` 只生成 i>=1 的 2-opt，避免与「反转补集」重复。

运行：
    python demo_tsp.py            # 四个引擎各跑一遍并对比
"""
from __future__ import annotations

import math
import random

from base import Problem


class TSP(Problem):
    name = "TSP-euclid"

    def __init__(self, n: int = 40, seed: int = 1) -> None:
        rng = random.Random(seed)
        self.cities = list(range(n))
        self.xy = [(rng.random() * 100.0, rng.random() * 100.0) for _ in range(n)]
        self.d = [[0.0] * n for _ in range(n)]
        for a in range(n):
            for b in range(a + 1, n):
                dx = self.xy[a][0] - self.xy[b][0]
                dy = self.xy[a][1] - self.xy[b][1]
                dist = math.hypot(dx, dy)
                self.d[a][b] = self.d[b][a] = dist

    # ---------- 共用 ----------

    def initial(self, rng: random.Random):
        """最近邻构造。基线必须像样，否则任何改进都会被高估。"""
        n = len(self.cities)
        start = rng.randrange(n)
        tour = [start]
        left = set(self.cities) - {start}
        while left:
            cur = tour[-1]
            nxt = min(left, key=lambda c: self.d[cur][c])
            tour.append(nxt)
            left.remove(nxt)
        return tour

    def evaluate(self, sol) -> float:
        n = len(sol)
        return sum(self.d[sol[i]][sol[(i + 1) % n]] for i in range(n))

    def is_feasible(self, sol) -> bool:
        return len(sol) == len(self.cities) and len(set(sol)) == len(sol)

    # ---------- ALNS ----------

    def destroy_ops(self):
        return [
            ("random_remove", self._destroy_random),
            ("worst_remove", self._destroy_worst),
            ("segment_remove", self._destroy_segment),
        ]

    def repair_ops(self):
        return [
            ("greedy_insert", self._repair_greedy),
            ("noisy_greedy", self._repair_noisy),
        ]

    def _k(self, n: int, frac: float = 0.2) -> int:
        return max(1, min(n - 2, int(round(n * frac))))

    def _missing(self, partial):
        present = set(partial)
        return [c for c in self.cities if c not in present]

    def _destroy_random(self, sol, rng):
        k = self._k(len(sol))
        drop = set(rng.sample(sol, k))
        return [c for c in sol if c not in drop]

    def _destroy_worst(self, sol, rng):
        """移除「绕行代价」最大的城市——它们的相邻边最长。"""
        n = len(sol)
        cost = []
        for i, c in enumerate(sol):
            a, b = sol[i - 1], sol[(i + 1) % n]
            cost.append((self.d[a][c] + self.d[c][b] - self.d[a][b], c))
        cost.sort(reverse=True)
        drop = {c for _, c in cost[: self._k(n)]}
        return [c for c in sol if c not in drop]

    def _destroy_segment(self, sol, rng):
        n = len(sol)
        k = self._k(n)
        start = rng.randrange(n)
        drop = {sol[(start + t) % n] for t in range(k)}
        return [c for c in sol if c not in drop]

    def _insert_cost(self, tour, c, p):
        a = tour[p - 1]
        b = tour[p] if p < len(tour) else tour[0]
        return self.d[a][c] + self.d[c][b] - self.d[a][b]

    def _repair_greedy(self, partial, rng):
        tour = list(partial)
        for c in self._missing(partial):
            best_p, best_c = 0, float("inf")
            for p in range(max(1, len(tour))):
                val = self._insert_cost(tour, c, p)
                if val < best_c:
                    best_p, best_c = p, val
            tour.insert(best_p, c)
        return tour

    def _repair_noisy(self, partial, rng):
        """带噪声的贪婪插入：保留探索，避免修复算子同质。"""
        tour = list(partial)
        for c in self._missing(partial):
            scored = [(self._insert_cost(tour, c, p) * (1.0 + 0.3 * rng.random()), p)
                      for p in range(max(1, len(tour)))]
            _, p = min(scored)
            tour.insert(p, c)
        return tour

    # ---------- SA / TS ----------

    def neighbor(self, sol, rng):
        n = len(sol)
        i = rng.randrange(1, n - 1)
        j = rng.randrange(i + 1, n)
        mv = ("2opt", i, j)
        return self.apply(sol, mv), mv

    def moves(self, sol):
        n = len(sol)
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                yield ("2opt", i, j)

    def apply(self, sol, move):
        _kind, i, j = move
        t = list(sol)
        t[i:j + 1] = reversed(t[i:j + 1])
        return t

    def delta(self, sol, move) -> float:
        """O(1) 的 2-opt 增量。写错不会报错，只会让搜索方向系统性偏移——
        所以必须用 selftest.py 与全量重算对撞。"""
        _kind, i, j = move
        n = len(sol)
        a, b = sol[i - 1], sol[i]
        c, e = sol[j], sol[(j + 1) % n]
        return self.d[a][c] + self.d[b][e] - self.d[a][b] - self.d[c][e]

    def move_key(self, move):
        _kind, i, j = move
        if i > j:
            i, j = j, i
        return ("2opt", i, j)


if __name__ == "__main__":
    from alns import ALNS
    from sa import SA
    from ts import TabuSearch

    prob = TSP(n=40, seed=1)
    base = prob.evaluate(prob.initial(random.Random(0)))
    print(f"最近邻基线 obj={base:.3f}\n")

    results = [
        ALNS(prob, max_iters=1500, seed=42).run(),
        SA(prob, max_iters=20_000, L=200, seed=42, reheat_after=8).run(),
        TabuSearch(prob, max_iters=1500, tenure=10, seed=42, diversify_after=300).run(),
    ]
    for r in results:
        gain = (base - r.best_obj) / base * 100
        print(f"{r.summary()}  相对基线 {gain:+.2f}%")
