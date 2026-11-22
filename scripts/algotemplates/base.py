"""算法骨架的公共协议。

四个引擎（ALNS / SA / TS / GA）都只依赖这里的 Problem 协议，与具体问题解耦。
接入一个新问题 = 实现一个 Problem 子类，不改引擎代码。

哪些方法给哪个引擎用：

| 方法 | ALNS | SA | TS | GA | 说明 |
|---|---|---|---|---|---|
| initial(rng) | 必需 | 必需 | 必需 | 必需 | 初始可行解 |
| evaluate(sol) | 必需 | 必需 | 必需 | 必需 | 目标值，越小越好 |
| is_feasible(sol) | 可选 | 可选 | 可选 | 可选 | 默认 True；建议接独立校验器 |
| copy(sol) | 可选 | 可选 | 可选 | 可选 | 默认 deepcopy |
| destroy_ops() | 必需 | - | - | - | [(名字, fn(sol, rng)->sol)] |
| repair_ops() | 必需 | - | - | - | 同上 |
| neighbor(sol, rng) | - | 必需 | - | - | 返回 (新解, 移动) |
| moves(sol) | - | - | 必需 | 可选 | TS 枚举用；GA 的 local_search 也用它 |
| apply(sol, move) | - | - | 必需 | 可选 | 应用移动，返回新解 |
| delta(sol, move) | - | 可选 | 可选 | 可选 | 增量目标差；默认全量重算（慢但正确） |
| move_key(move) | - | - | 必需 | - | 禁忌表的键，需规范化 |
| crossover(a, b, rng) | - | - | - | 可选 | 默认 OX；排列问题可换 PMX |
| mutate(sol, rng) | - | - | - | 可选 | 默认 swap/insert/reverse 三选一 |
| repair(sol, rng) | - | - | - | 可选 | 非排列编码必填，否则不可行子代被大量拒绝 |
| signature(sol) | - | - | - | 可选 | 种群去重键；默认 tuple(sol) |

约定：
- 解对象必须是可复制、不可变的语义（apply/neighbor 返回新对象，不改入参）。
  纯 Python 下 deepcopy 一个列表的开销通常远小于评价本身；若解很大，
  自己实现 copy 并复用，或在文档里说明只读约定。
- 所有随机性必须来自传入的 rng，禁止使用全局 random / np.random。
  这是「同一种子逐位可复现」的前提。
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence


class Problem:
    """问题适配器基类。只有 initial / evaluate 是三种引擎共用的必需项。"""

    name: str = "unnamed"

    # ---- 三种引擎共用 ----
    def initial(self, rng: random.Random) -> Any:
        raise NotImplementedError

    def evaluate(self, sol: Any) -> float:
        raise NotImplementedError

    def is_feasible(self, sol: Any) -> bool:
        return True

    def copy(self, sol: Any) -> Any:
        return copy.deepcopy(sol)

    # ---- ALNS 专用 ----
    def destroy_ops(self) -> Sequence[tuple[str, Callable[[Any, random.Random], Any]]]:
        return ()

    def repair_ops(self) -> Sequence[tuple[str, Callable[[Any, random.Random], Any]]]:
        return ()

    # ---- SA / TS 专用 ----
    def neighbor(self, sol: Any, rng: random.Random) -> tuple[Any, Any]:
        """随机邻域解。返回 (新解, 移动描述)。SA 用。"""
        raise NotImplementedError

    def moves(self, sol: Any) -> Iterable[Any]:
        """枚举候选移动。TS 用；大规模问题应在此做抽样。"""
        raise NotImplementedError

    def apply(self, sol: Any, move: Any) -> Any:
        """应用移动。必须返回新对象，不改 sol。"""
        raise NotImplementedError

    def delta(self, sol: Any, move: Any) -> float:
        """目标增量。默认全量重算——正确但慢；性能敏感处必须重写为 O(1)/O(n)。"""
        return self.evaluate(self.apply(sol, move)) - self.evaluate(sol)

    def move_key(self, move: Any) -> Any:
        """禁忌键。默认用 repr，但**应重写为规范化形式**，否则等价移动不被识别。"""
        return repr(move)


@dataclass
class Trace:
    """迭代轨迹：同时记录当前解与历史最优，避免只画最优曲线掩盖波动。"""

    iters: list[int] = field(default_factory=list)
    cur: list[float] = field(default_factory=list)
    best: list[float] = field(default_factory=list)

    def add(self, it: int, cur: float, best: float) -> None:
        self.iters.append(it)
        self.cur.append(cur)
        self.best.append(best)

    def area_above_final(self) -> float:
        """最优曲线与最终值之间的面积（梯形积分）。

        越小说明越早收敛到最终质量。注意这不是严格意义的 primal integral
        （后者需要参考值），因此报告时要写清定义，不要与文献数值直接比。
        """
        if len(self.best) < 2:
            return 0.0
        final = self.best[-1]
        area = 0.0
        for k in range(1, len(self.best)):
            dt = self.iters[k] - self.iters[k - 1]
            area += 0.5 * ((self.best[k - 1] - final) + (self.best[k] - final)) * dt
        return area

    def steps_to(self, target: float) -> int | None:
        """首次达到给定目标值（含容差）的迭代数；达不到返回 None。"""
        for it, v in zip(self.iters, self.best):
            if v <= target + 1e-9 * max(1.0, abs(target)):
                return it
        return None


@dataclass
class Result:
    algorithm: str
    best_sol: Any
    best_obj: float
    trace: Trace
    iterations: int
    seconds: float
    meta: dict = field(default_factory=dict)

    def summary(self) -> str:
        head = (f"{self.algorithm}: obj={self.best_obj:.6f} "
                f"iters={self.iterations} time={self.seconds:.2f}s")
        if self.meta:
            extra = " ".join(f"{k}={v}" for k, v in self.meta.items())
            head = f"{head} | {extra}"
        return head


def fmt_hist(hist: Sequence[float], last: int = 5) -> str:
    if not hist:
        return "[]"
    return str([round(x, 4) for x in list(hist)[-last:]])
