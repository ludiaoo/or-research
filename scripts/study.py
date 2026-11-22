#!/usr/bin/env python3
"""运筹优化研究的「阶段门」状态机、双向决策采集与研究目录骨架生成器。

零第三方依赖，任意 Python 3.10+ 可运行。用法：

    python study.py init <name> --root <工作目录> --idea "<用户原话>"
    python study.py ask <阶段号> --study <研究目录>          # 开场：采集使用者决策
    python study.py status --study <研究目录>
    python study.py pass <阶段号> --note "..." --decision "0.1=<用户选择>" \
        --rationale "<用户的思路>" --study <研究目录>          # 收尾：复述确认 + 留痕
    python study.py reopen <阶段号> --note "<原因>" --study <研究目录>
    python study.py decisions --study <研究目录>               # Stage 7：汇总决策与思路

设计要点：
  * state.json 是跨会话唯一的进度依据；结论数字不写进它，只写进阶段文档与 results/。
  * 每道门两段交互：ask 采集决策点 → 按回答产出 → pass 时逐条复述确认并留痕。
  * --decision 是用户拍板，--suggestion 是 [AI 建议]，两者严格区分，不许混写。
  * pass 只接受「当前」阶段号，防止跳门；reopen 会清空该阶段及其之后的确认。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA = 2  # 2 = 每阶段把 name/file/skeleton 写进 state.json，改版不再破坏旧目录

STAGES = [
    {
        "name": "问题界定与需求澄清",
        "file": "00_问题界定.md",
        "body": """## 0. 入口分诊（真正的第一问：你手上已有什么）

四种起点，四个不同的第一步。分诊错了，要么白找数据，要么白建错模。

| 项 | 内容 |
|---|---|
| E1 入口状态 | 场景+数据 / 场景无数据 / 数据无场景 / 只有一句 idea |
| E2 目标来源（仅「数据无场景」时填） | 我从数据提候选场景、你选 / 你直接给目标 / 先做描述性分析再定 |

### 四条路径

| 入口 | 第一步做什么 | 第一步产出 | 不许做 |
|---|---|---|---|
| **场景+数据** | 只做数据审计，不找新数据 | 数据充足性判定（A/B/C） | 不许「顺手」换数据源或换算例 |
| **场景无数据** | 场景冻结成假设；按决策变量反查数据需求，再取数 | 数据需求表 + 取数方案 | 不许拿合成数据当真实场景的证据 |
| **数据无场景** | 数据画像 → 3 个目标不同的候选场景 → 你选 | `data/profile.md` + 候选场景卡 | 不许因为某列存在就把目标设成它 |
| **只有 idea** | 场景化四问：谁 / 何时 / 决定什么 / 怎么算好 | 一句话问题陈述 | 不许直接跳到建模 |

> 规程：数据→场景见 `references/场景构思.md`；场景→数据见 `references/算例与基准.md` §0。
> 论文基准只是「场景无数据」的一种取数方式，不是独立入口。

## 0.5 使用意图与求解路线（第二问：它决定后面每一步的强度与交付形态）

同一句「帮我优化一下」，可以指向三种完全不同的活：

| 项 | 内容 |
|---|---|
| M1 交付形态 | 求解器直用 / 自研算法 / 完整研究 / 业务方案 |
| M2 优化目标（M1=求解器直用时必填） | 总求解时间 / 首个可行解时间 / 证明最优时间 / 稳定性 |
| M3 允许动到哪一层 | 只调参数 / 动本次研究的 formulation / 动求解方式（分解、warm start）——对象是本轮自己写的模型，不涉及他人代码或原文 |

### 按 M1 裁剪（不做这一步，等于用论文口径交付一个查表需求）

| M1 | 必做检查点 | 可省 | 主产出 | 不许说 |
|---|---|---|---|---|
| **求解器直用** | 0 数据契约 → 2 模型（含性能档案）→ 4 求解与调优 | 1 反例库、5 自研算法、6 统计检验 | 调优前后对照表 + 参数档 + 规模边界 | 不许把「解出来了」写成「问题解决了」 |
| 自研算法 | 0 → 1 → 2 → 3 → 4（下界）→ 5 → 6 | — | 算法 + gap 表 + 自测 | 没有下界不许写「接近最优」 |
| 完整研究 | 全部 8 个 | — | 论文 / 技术报告 | — |
| 业务方案 | 全部 8 个（6 可轻） | — | 执行清单 + 结论分级 | 合成数据不许声称实际效益 |

> M1=求解器直用时，调优规程见 `references/求解器调优.md`，实验台 `scripts/solver_bench.py`。
> **铁律：加速不许改解**——同模型、同 gap 目标下各参数档的 `ObjVal` 必须一致（相对差 ≤ 1e-6）。

## 0.6 数据契约（先于一切建模；来源未定，不许进 Stage 1）

数据不是 Stage 3 才处理的事。**来源决定这个研究能不能做、能做到多强。**
本节由用户拍板，AI 不许替他假定「反正有公开算例」。

| 项 | 内容 |
|---|---|
| 来源类型 | 使用者提供 / 公开基准（AI 检索） / AI 合成 / 混合 |
| 具体来源 | 文件路径、DOI/URL、数据库名、论文附表编号 |
| 版本与校验 | 发布版本、下载日期、校验和 |
| 是否含参考值 | 有（OPT / BKS / LB） / 无 / 需自行求解 |
| 处理权 | 原样使用 / 允许清洗裁剪 / 允许重采样补齐 |
| 规模分档 | 每档来自数据本身 / 需生成补齐 |
| 使用边界 | 能否外推规模与分布；能否声称实际效益 |

### 三类来源的强制动作（缺一项不算过门）

| 来源 | 必须做 | 不许说 |
|---|---|---|
| 使用者提供 | 完整性审计：矩阵覆盖、对称性、三角不等式、孤立点、量纲与坐标范围；原始文件只读；转换脚本留痕；许可与可发布性 | 没有抽样依据不说「有代表性」 |
| 公开基准 | 来源白名单 + 版本锁定 + 校验和；官方参考值随数据一并落地并标 OPT/BKS/LB；不同版本不混用 | 不自行编造参考值 |
| AI 合成 | 生成器规格（分布、参数、种子、规模）；可复现脚本；与真实分布的偏差说明 | **不得用合成数据声称实际节省 / 收益** |

### 来源缺口的降级顺序

1. 使用者有数据 → 审计后直接使用
2. 有公开基准 → 锁版本使用，参考值随数据落地
3. 两者皆无 → 合成 + 全程标注「合成算例」，结论只写算法相对表现
4. 规模档也凑不齐 → 收窄研究边界（见 §6），不许假装已覆盖

## 0.7 数据底账与清洗（先画像，再动手；规程见 `references/数据清洗与挖掘.md`）

**立场**：清洗不是「预处理」，是参数生产——它改可行域，改解，改结论。

### 数据底账

| 文件 | 内容 | 行 × 列 | 时间范围 | 粒度 | 主键 | 来源 | 版本/校验和 | 只读 |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  | 是 |

### 客观画像（工具出事实，人做判断）

```bash
python "$SKILL_DIR/scripts/data_profile.py" <csv> --key <主键> --time <时间列> \\
    --rule "<列> >= <列>" --out data/raw/_profile.md
```

四项必须报数字：缺失率与**缺失结构**（某列缺失是否被另一列完全解释）、主键重复、量级异常（最大值/P95）、规则违规行数。

### 清洗动作表（每步留痕，缺这一列就无法回答「清洗改变了多少」）

| # | 对象 | 动作 | 规则 / 判据 | 影响行数 | 理由 | 可逆 |
|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  |  |

### 三档清洗策略（Stage 6 要做敏感性）

| 档 | 定义 | 将在哪一节比较 |
|---|---|---|
| 保守 | 只删可证录入错误 |  |
| 基线 | 用户认可的口径 |  |
| 激进 | 截尾 + 插值 + 聚合 |  |

> 只跑过一种清洗策略，不许在结论里写「稳健」。

## 0.8 参数来源与挖掘（落不到模型位置的挖掘，不做）

### 参数来源表

| 参数 | 符号 | 值 | 单位 | 标注 | 来源类型 | 依据 / 脚本 |
|---|---|---|---|---|---|---|
|  |  |  |  | [给定]/[假设]/[扩展] | 查表/实测/拟合/挖掘/假设 |  |

### 挖掘产物表

| 产物 | 方法 | 进模型的位置（参数 / 情景 / 约束 / 初始解） | 验证方式 | 不采用时的影响 |
|---|---|---|---|---|
|  |  |  |  |  |

> 三类用途：造参数（点估计）、造不确定性（分布/情景/区间）、造结构（规律→约束）。
> 只报「聚类轮廓系数」不算验证，要报**解质量损失**。

## 0.85 场景化四问（仅 E1 = 只有 idea 时填；其余入口写「不适用，原因」）

规程见 `references/场景构思.md` §6。**这一节就是「只有 idea」入口的第一步产出**——四行填不出来，不许往下写模型。

| 问 | 必须落到什么 | 本轮答案 | 追问 / 反例 |
|---|---|---|---|
| **谁**做这个决定 | 岗位名（不是部门名）+ 他现在是怎么做的 |  | 写「相关部门」「领导」= 未答 |
| **什么时候**做 | 决策频率 + 触发条件 + 覆盖的时间窗 |  | 写「随时」「定期」= 未答 |
| **决定什么** | 从哪几个可选动作里选一个，动作必须可执行 |  | 写「优化资源配置」= 未答 |
| **怎么算好** | 度量 + 方向 + 单位 + **分母** + 现状值 |  | 只写「降本」不给分母 = 未答 |

> 最大的坑在第四问：**分母决定最优解长什么样**。同一个「降成本」，按 元/月、元/单、元/吨公里、元/店·次
> 去优化，会得到四个互相冲突的方案（合并频次降 元/单，却抬高 元/店·次）。分母没定，后面每个百分比都不可比。
> 现状值必须写清是谁给的、按什么口径 ── 两个部门报出两个数，本身就是第一个结论。

**四问答不上来** → 退回 §0.9 先找场景，或请用户带着「一个具体岗位的一次具体决策」回来。

## 0.86 数据需求反查与取数方案（仅 E1 = 场景无数据 / 只有 idea 时填；其余写「不适用，原因」）

顺序是**先冻结决策变量，再反查字段**，不是先看手上有什么表。每个决策变量至少一行；
没有采集方案的那一项，写进 §6 研究边界当「本轮不做」。

| # | 决策变量（谁·何时·选什么） | 需要的参数 | 对应字段 / 口径 | 取数来源与方式 | 时间窗与粒度 | 责任人 / 何时到位 | 缺了怎么降级 |
|---|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  |  |  |

**数据契约状态**：已闭合 / **未闭合**（卡在哪、什么时候能闭合）

> 未闭合不等于过不了门，但必须接下两个限制：Stage 1 只做**构造性可行解**；
> 结论里不许出现「改善 x% / 节省 y 元」；Stage 6 的敏感性用假设区间做，不用点估计做。

## 0.9 候选场景卡（E3 必填；E4 仅当 §0.85 第四问答出多个互斥分母时填，否则写「不适用，原因」）

规则：**≥3 个候选、目标必须不同**、每个标数据充分性 A/B/C、必须含一个保守选项。
C 级不进候选（除非用户接受「先补数据」并写进研究边界）。规程见 `references/场景构思.md`。

| # | 场景一句话 | 目标（度量 + 方向 + 单位） | 数据支撑 | 规模量级 | 预期难点 | 结论 |
|---|---|---|---|---|---|---|
| 1 |  |  | A / B / C |  |  | 采用 / 排除 |
| 2 |  |  |  |  |  |  |
| 3 |  |  |  |  |  |  |

**保守选项**（数据最全、结论最弱、一轮内可交付）是第 __ 个，为什么它保守：

**被排除的候选与原因**（含 C 级及其缺什么）：

**选定**：第 __ 个 ｜ **为什么不是另外两个**：

**场景来源标注**（必填，二选一）：
- [ ] 本场景由数据画像推得，**尚未经业务方确认**
- [ ] 用户即业务方，已确认「这就是我要决定的那个事」

## 1. 一句话问题陈述

在 ____ 约束下，选择 ____（决策），最小化/最大化 ____；
决策粒度 ____，时间尺度 ____，空间尺度 ____。

## 2. 问题归类（决定查哪批文献、套哪些已知结论）

| 项 | 内容 |
|---|---|
| 最接近的标准问题 | 例：p-median / UFL / CVRP / VRPTW / FSTSP / 单机调度 / 装箱 |
| 与标准模型的差异 | 新增约束 / 新增目标项 / 随机性 / 多目标 / 动态 |
| 决策变量维度 |  |
| 规模量级（m, n, T） |  |
| 数据可得性 | 见 §0.6 数据契约：来源类型 + 是否含参考值 |

## 3. 已知结论定位（每条都标「是否核对原文」）

| 结论类型 | 内容 | 来源（作者, 年） | 适用条件 | 核对原文 |
|---|---|---|---|---|
| 复杂度 |  |  |  |  |
| 精确算法 / 可解规模 |  |  |  |  |
| 近似比 / 下界 |  |  |  |  |
| 标准算例 |  |  |  |  |

> 未核对原文的写「定位用，未验证」，不许直接进结论。

## 4. 贡献点定位

- 类型：新问题 / 已有模型的变体 / 复现与改进 / 算法贡献 / 应用贡献
- 与最近 3 篇工作的差异（一句话一条）：
  1.
  2.
  3.

## 5. 参数台账（[给定] / [假设] / [扩展]）

| 参数 | 符号 | 值 | 单位 | 标注 | 依据 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 6. 研究边界

- 明确不做：
- 做不到时的降级方案：
""",
        "checks": [
            "M1 交付形态已明确，并据此裁剪了要走的检查点（求解器直用不必自研算法；论文形态不许省下界）",
            "M1=求解器直用时，M2 优化目标已选定（总时间 / 首解时间 / 证明时间 / 稳定性），且与业务诉求一致",
            "M3 允许动到哪一层已定（只调参 / 动本次研究的 formulation / 动求解方式），对象仅限本轮自己的模型；越界项必须重新征得同意",
            "入口分诊已定（场景+数据 / 场景无数据 / 数据无场景 / 只有 idea），并写明第一步动作",
            "数据无场景时：§0.9 已填 ≥3 个目标不同的候选场景 + 各自 A/B/C + 一个保守选项 + 排除理由 + 场景来源标注（未确认 / 已确认）",
            "只有 idea 时：§0.85 四问逐条落到岗位名 / 决策频率 / 可执行动作 / 含分母与现状值的指标（写「相关部门」「定期」「优化资源配置」= 未答）",
            "场景无数据 或 只有 idea 时：§0.86 每个决策变量都有采集方案；数据契约未闭合已写明卡点与到位时间，并接受「Stage 1 只做构造解、不报改善幅度」",
            "数据契约写清来源类型与具体来源（路径 / DOI / 数据库），并锁定版本或校验和",
            "按来源类型完成强制动作：提供→审计；公开→锁版本与参考值；合成→生成器规格与种子",
            "参考值标注了 OPT / BKS / LB 之一，并说明来源；没有参考值时明确「只能自建基线」",
            "数据使用边界已写定：能否外推规模与分布、能否声称实际效益",
            "已用 data_profile.py 出画像，并对缺失率与缺失结构 / 主键重复 / 量级异常 / 规则违规四项给了具体数字",
            "清洗口径已定：哪些记录可删可改、判据是什么；原始文件只读，派生层可重跑",
            "清洗动作表已建：每步有对象、动作、判据、影响行数与理由",
            "三档清洗策略（保守/基线/激进）已定义，并已排入 Stage 6 的敏感性小节",
            "参数来源表已建：每个参数标了查表/实测/拟合/挖掘/假设之一，无出处的标[假设]",
            "挖掘产物落到了模型的具体位置（参数/情景/约束/初始解），并写了验证方式与不采用时的影响",
            "一句话陈述含决策、目标、约束、粒度与尺度",
            "已归入某个标准问题族，并写明与标准模型的差异",
            "复杂度 / 可解规模 / 近似比等已知结论已定位，并标注是否核对原文",
            "贡献点能用一句话说清，且与最近工作有可比较的差异",
            "参数台账三标注齐全，[假设] / [扩展] 逐条经用户确认",
            "明确写了不做什么与降级方案",
        ],
    },
    {
        "name": "可行解与校验器",
        "file": "01_可行解.md",
        "body": """## 1. 一个具体可行解

（构造方式 + 具体取值 + 逐条约束核对；不许只写「显然可行」）

## 2. 校验器（与求解器解耦，不许调用求解器判断可行性）

```python
def validate(sol, inst) -> tuple[bool, list[str]]:
    \"\"\"返回 (是否可行, 违约码列表)。\"\"\"
```

- 输入 / 输出约定：
- 覆盖的约束编号：

## 3. 违约码清单

| 码 | 约束编号 | 判定条件 | 典型触发场景 |
|---|---|---|---|
|  |  |  |  |

## 4. 反例测试

| 算例 | 故意构造的违约 | 期望 | 实测 |
|---|---|---|---|
|  |  | 拒绝并给出违约码 |  |

## 5. 起点界

- 该可行解目标值 $z_{UB}$ =
- 平凡下界 $z_{LB0}$（如逐元素最小 / 全放松）=
- 初始相对 gap = $(z_{UB}-z_{LB0})/z_{LB0}$ =
""",
        "checks": [
            "构造出至少一个具体可行解，并逐条核对约束",
            "校验器不依赖求解器，能返回具体违约码",
            "违约码覆盖全部约束类型，含耦合型约束",
            "至少一组反例被正确拒绝",
            "给出初始上界与平凡下界，知道起点 gap 多宽",
        ],
    },
    {
        "name": "数学模型",
        "file": "02_模型卡.md",
        "body": """## 1. 集合与索引

（$I$ 设施、$J$ 需求、$K$ 车辆、$T$ 时段……逐个说明基数与含义）

## 2. 参数

| 符号 | 含义 | 单位 | 取值 | 标注 |
|---|---|---|---|---|
|  |  |  |  |  |

## 3. 决策变量

| 符号 | 类型 | 维度 | 取值范围 | 业务含义 |
|---|---|---|---|---|
|  |  |  |  |  |

## 4. 目标函数

$$
\\min\\; z = \\dots
$$

- 逐项解释：
- 量纲检查（每一项是否同单位）：
- 各项量级占比（用基准算例估一次）：

## 5. 约束（逐条编号）

| # | 表达式 | 业务解释 | 硬/软 | 违约码 | 规模（条数） |
|---|---|---|---|---|---|
| (1) |  |  |  |  |  |

## 6. formulation 对照（至少两套对整数解等价的写法）

| 形式 | 关键写法 | 变量数 | 约束数 | LP 松弛值 | 备注 |
|---|---|---|---|---|---|
| 强 |  |  |  |  |  |
| 弱 |  |  |  |  |  |

## 7. 线性化与合法化

- 非线性项 → 线性：
- Big-M 取值与来源（必须是最小合法值）：
- 对称性处理（破缺约束 / 声明不处理的原因）：

## 8. 理论性质

- 复杂度：
- 可行域结构（是否全幺模、多面体维度、极点多解风险）：
- 有效不等式 / 加强约束：
- 与松弛的关系（LP 是否可能给整数解）：
""",
        "checks": [
            "集合 / 参数 / 变量 / 目标 / 约束齐备，量纲一致",
            "每条约束有业务解释、硬软标注、违约码与规模",
            "至少两套等价 formulation，并实测 LP 松弛值对比（不是凭直觉说谁更强）",
            "非线性项已线性化；Big-M 取最小合法值并说明来源",
            "复杂度与精确可解规模有明确表述",
            "对称性已处理或说明不处理的原因",
        ],
    },
    {
        "name": "数据与基线",
        "file": "03_算例与基线.md",
        "body": """## 1. 算例来源

| 来源 | 名称 / 版本 | 规模 | 用途 | 获取方式 |
|---|---|---|---|---|
|  |  |  |  |  |

## 2. 生成器（若用生成算例）

- 生成参数：
- 随机种子（全部列出）：
- 生成器代码路径：
- 与真实数据分布的差异（必须写）：

## 3. 规模分档

| 档 | m / n / T | 用途 | 时间上限 |
|---|---|---|---|
| S |  | 验证正确性（可与精确解对照） |  |
| M |  | 调参 |  |
| L |  | 主实验 |  |
| XL |  | 压力测试 / 边界 |  |

## 4. 基线

| 基线 | 类型 | 有最优性证书？ | 实现 / 来源 | 参数 |
|---|---|---|---|---|
|  |  |  |  |  |

## 5. 参考值（BKS / 下界 / 最优值）

| 算例 | 参考值 | 类型（BKS / LB / OPT） | 来源 |
|---|---|---|---|
|  |  |  |  |

> 启发式的最好解不是 BKS；下界不是最优值。类型必须写清。

## 6. 硬件与时间口径

- CPU / 内存 / 是否单线程：
- 各档时间上限：
- 是否允许重启 / 多次运行取最好（若是，必须写明次数）：
""",
        "checks": [
            "算例来源可复现（标准库版本，或生成器 + 全部种子 + 参数）",
            "规模分档有明确用途，验证档与主实验档分开",
            "基线可复现，并标明是否带最优性证书",
            "参考值类型（BKS / LB / OPT）写清，不把启发式最好解当 BKS",
            "硬件与时间口径写明，跨论文比较有前提",
        ],
    },
    {
        "name": "精确求解与可信度",
        "file": "04_精确解与下界.md",
        "body": """## 1. 精确方法选型

| 方法 | 适用条件 | 规模上界 | 给证书？ |
|---|---|---|---|
| MILP（B&B / B&P） |  |  |  |
| CP-SAT |  |  |  |
| DP / 枚举 |  |  |  |

## 2. 求解器配置

- 求解器与版本 / 许可证：
- 关键参数：`time_limit` / `mip_rel_gap` / `threads` / cuts / presolve
- 输出字段：`ObjVal` / `ObjBound` / `MIPGap` / `NodeCount` / `Runtime` / `Status`

## 3. 下界

| 下界 | 构造方式 | 算例 1 值 | 相对 LP 紧度 | 计算代价 |
|---|---|---|---|---|
| 平凡界 |  |  |  |  |
| LP 松弛 |  |  |  |  |
| Lagrangian / 组合界 |  |  |  |  |

## 4. 最优性证书

| 算例 | $z_{IP}$ | 下界 | MIPGap | Nodes | Time (s) | Status | 独立验证 |
|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |

## 5. 小规模独立精确解

- 独立方法（枚举 / DP / 另一求解器）：
- 与 MILP 逐位对照（差值）：

## 6. 精确可解边界

- 时间上限内能解到的最大的算例：
- 超界后的降级（只给下界 / 启发式 + 报告 gap）：

## 7. 求解性能档案（M1=求解器直用，或需要报求解代价时必填）

| 档案 | 关键参数 | 目标值 | 下界 | gap | 节点 | 时间 (s) | 状态 |
|---|---|---|---|---|---|---|---|
| baseline | 默认 |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |

- 口径：`Threads=` / `Seed=` / `TimeLimit=` / `MIPGap=` / 重复次数=
- 加速比：____×（分母为本次重跑的 baseline，不引用历史数字）
- **解一致性**：各档目标值相对差 = ____（> 1e-6 即视为换了答案，不可用于定稿）
- 首个可行解时间（业务口径需要时）：____ s
- 有效手段归因：哪一档起作用、属于 L1/L2/L3 哪一层、为什么
- 已尝试但无效的参数（照写，避免下次重复）：
- 规模边界：这套配置在 ____ 规模内可证最优；超界给 ____
""",
        "checks": [
            "精确方法选型有理由，写明时间与规模上界",
            "求解器参数与诊断字段齐全，time_limit 明确",
            "至少一个合法下界，并验证 $LB \\le z_{IP}$",
            "小规模有独立精确解（枚举 / DP / 另一求解器）逐位对照",
            "明确精确可解边界与超界降级方案",
            "（求解器直用）调优前后同口径对比：同机、同线程、同时限、同 gap，且各档目标值一致",
            "（求解器直用）加速归因到具体层级（formulation / 参数 / 求解方式），失效参数也照写",
        ],
    },
    {
        "name": "算法设计",
        "file": "05_算法设计.md",
        "body": """## 1. 算法总体

（伪代码或流程图：构造 → 改进 → 接受准则 → 终止）

```
Algorithm 1
1:
2:
```

## 2. 解的表示与增量评价

- 表示（数组 / 图 / 集合）：
- 目标增量怎么算（避免每次全量重算）：
- 可行性怎么维护（何时检查、检查哪些约束）：

## 3. 算子库

| 算子 | 类型（构造 / 破坏 / 修复 / 局部） | 作用 | 单次复杂度 |
|---|---|---|---|
|  |  |  |  |

## 4. 参数与调参

| 参数 | 含义 | 取值 | 调参方式 | 敏感性 |
|---|---|---|---|---|
|  |  |  |  |  |

> 调参只能用 M 档，不许在最终测试集上调；调参预算要写。

## 5. 终止判据与 anytime 行为

- 终止：时间 / 迭代 / 无改进轮数 / 达到下界
- 任意时刻都有可行解？解质量随时间怎么变？

## 6. 理论义务（先选类型，再写内容）

按 `证明规程.md` §0 分清四类，**只勾你真的做了的**；没做的留空，不许含糊：

| 类型 | 要做的工作 | 本文档位置 | 状态 |
|---|---|---|---|
| C1 算法正确性 | 交换论证 / 归纳不变量 |  | 未做 / 写了 / 完整 |
| C2 近似比 | 三步法（下界 + 界住算法 + 合并）+ 紧例 |  | 未做 / 写了 / 完整 |
| C3 locality gap | 邻域定义 + 上界证明 + 下界实例族 |  | 未做 / 写了 / 完整 |
| D 问题难度 | 双向归约 + 多项式性 |  | 未做 / 引用 / 自证 |

- 理论保证的**确切适用条件**（度量 / 权重 / 容量 / 哪个参数固定）：
- 与精确解的关系（能否证明最优 / 只能经验）：
- **若无保证，此处必须写「无理论保证，仅经验评估」**，且全文不得出现「保证最优」「ρ-近似」等词：

## 7. 复现性

- 种子策略 / RNG 使用：
- 并行是否影响结果：
- 同一命令重跑是否逐位一致：

## 8. 实现自测（三项全过才可交付）

| 测试 | 方法 | 结果 |
|---|---|---|
| 增量 vs 全量 | `selftest.check_delta` 或等价实现 | 通过 / 失败：___ |
| 可行性一致 | `selftest.check_feasibility` | 通过 / 失败：___ |
| 同种子可复现 | `selftest.check_determinism` | 通过 / 失败：___ |

- 单步成本随规模的变化（$n$ 翻倍，单步时间约 ×?）：
- profile 结论：时间主要花在 ___
""",
        "checks": [
            "算法结构清晰，伪代码可照实现",
            "解的表示与增量评价明确，单步复杂度可控",
            "算子库完整，每个算子有作用与复杂度",
            "参数、调参方式、调参预算、终止判据齐全，且未在测试集上调参",
            "理论义务按四类勾选，未做的不冒充；无保证时明写",
            "随机性可控，种子与并行影响已说明",
            "增量评价已与全量重算对撞通过，三项自测有记录",
        ],
    },
    {
        "name": "结果验证与灵敏度",
        "file": "06_计算实验.md",
        "body": """## 1. 实验设计（**跑之前**写定）

| 项 | 内容 |
|---|---|
| 对照臂 |  |
| 配对键 | 算例 (+ 种子) |
| 每算例重复次数 |  |
| 主指标 |  |
| 时间上限 |  |
| 主表 / 附表分工 |  |

## 2. 主结果表

| 算例 | 规模 | BKS / LB | 基线 obj (gap%) | 本方法 obj (gap%) | 基线时间 | 本方法时间 |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |

## 3. 汇总指标

- 平均 / 中位 gap：
- 求得最优（或达到 BKS）的算例数：
- 时间到目标（time-to-target）：
- primal integral（若报 anytime）：
- 未在时限内完成的算例数（必须单列）：

## 4. 统计检验

- 配对方式 / 检验方法（符号检验 / Wilcoxon / Friedman）：
- 效应量与置信区间：
- 并列怎么处理：
- 多重比较校正（若同时比多个臂）：

## 5. 消融与敏感性

### 5.1 算法与参数

| 项 | 去掉 / 改变什么 | 主指标变化 | 结论是否翻转 |
|---|---|---|---|
|  |  |  |  |

### 5.2 数据口径（与 5.1 并列，别合并）

| 清洗策略 | 目标值 | 解的结构 | 关键结论 | 是否翻转 |
|---|---|---|---|---|
| 保守 |  |  |  |  |
| 基线 |  |  |  |  |
| 激进 |  |  |  |  |

| 假设参数 | 保守值 | 乐观值 | 结论是否变 |
|---|---|---|---|
|  |  |  |  |

> 5.1 回答「算法/参数动一下结论还在吗」；5.2 回答「数据怎么洗、假设取多少，结论还在吗」。
> 两问都过，才能写「稳健」。

## 6. 失败与异常案例

| 算例 | 现象 | 原因假设 | 是否已验证 |
|---|---|---|---|
|  |  |  |  |
""",
        "checks": [
            "口径在跑之前写下，对照臂与配对键明确",
            "主表含规模、参考值、各臂 obj 与 gap、时间",
            "汇总指标含 gap 分布与最优解个数，不只报平均；超时单列",
            "统计检验方法、效应量、并列处理都写明",
            "消融 / 敏感性有清单，结论翻转照写",
            "清洗三档（保守/基线/激进）与假设参数的保守/乐观值都测过，翻转的已降级",
            "失败案例有分析，不藏",
        ],
    },
    {
        "name": "方案交付与结论",
        "file": "07_交付与结论.md",
        "body": """## 1. 结论清单（必须分级）

| # | 结论 | 关键数字 | 强度 | 适用条件 | 来源 |
|---|---|---|---|---|---|
| 1 |  |  |  |  |  |

> 强度三档：**确定**（有最优性证书，或多算例一致）/ **有条件**（在某参数区间成立）/ **仅本算例**。
> 没标强度的结论不许写进摘要。

## 2. 执行清单（业务方要能直接排期）

| 动作 | 责任人 | 时间 | 依赖 | 判断是否完成的信号 |
|---|---|---|---|---|
|  |  |  |  |  |

## 3. 方案对照（现状 vs 优化）

| 维度 | 现状 | 优化方案 | 变化 | 变化的分母 |
|---|---|---|---|---|
|  |  |  |  |  |

- 每个百分比必须写清分母与口径（相对谁、哪些对象、什么时间段）。

## 4. 稳健性与适用条件

- 关键参数及稳健区间：
- 翻转点：哪个参数越过多少，结论就变
- 业务方需要监控的指标：

## 5. 局限与未测

- 未覆盖的规模 / 分布：
- 未做的对比：
- 方法本身的假设：
- 已知的偏差（如口径偏离、简化）：

## 6. 复现包

- 代码 / 数据版本 / 环境 / 命令 / 预期输出：
- 从干净环境逐条可执行：

## 7. 若走论文路线（可选，业务方案可整节删除）

- 文档结构：摘要 / 引言 / 文献 / 问题定义 / 模型 / 理论 / 算法 / 实验 / 结论
- 图表清单与正文数字一致性自检：

| 图表 | 数据来源（CSV / 脚本） | 与正文一致？ |
|---|---|---|
|  |  |  |

- 审稿人可能问什么：

| 问题 | 回答 | 证据位置 |
|---|---|---|
| 为什么不直接用求解器？ |  |  |
| 基线是不是太弱？ |  |  |
| 调参是否公平？ |  |  |
| 有没有下界 / 最优性证据？ |  |  |
| 算例是否挑过？ |  |  |
""",
        "checks": [
            "结论按「确定 / 有条件 / 仅本算例」分级，每条带数字与适用条件",
            "执行清单可排期：有责任人、时间、依赖与完成信号",
            "方案对照按维度拆分，每个百分比有分母与口径",
            "稳健性写明区间与翻转点，并给出监控指标",
            "局限与未测 ≥3 条，负结果不删",
            "复现包能从干净环境跑通",
            "走论文路线时，图表与正文数字逐格一致、已预设 ≥5 个审稿问题",
        ],
    },
]

# 每阶段必须由使用者拍板的决策点：(编号, 问题, AI 建议默认值)
# 与 references/交互协议.md 保持一致；ask 打印、骨架生成、pass 留痕都用它。
DECISIONS = [
    [
        ("E1", "入口分诊：你手上已有什么——场景+数据 / 场景无数据 / 数据无场景 / 只有一句 idea",
         "建议：先按「有无场景 × 有无数据」问清，再决定第一步是审计、取数、还是构思场景"),
        ("E2", "若只有数据：目标从哪来——我从数据提 3 个候选场景你选 / 你直接给目标 / 先做描述性分析再定",
         "建议：我提 3 个目标不同的候选场景供你选；不许因为数据里有某列就把目标设成它"),
        ("M1", "这次要什么：求解器直用（只想解出来、越快越好）/ 自研算法 / 完整研究 / 业务方案",
         "建议：能上求解器就先上；只有规模明显超出求解器能力或需要算法贡献时才自研"),
        ("M2", "若走求解器直用，优先优化哪一个：总求解时间 / 首个可行解时间 / 证明最优时间 / 稳定性",
         "建议：业务要方案选「首解时间」，要证书选「证明时间」；两者调法相反，必须二选一"),
        ("M3", "允许动到哪一层：只调参数 / 动本次研究的 formulation / 动求解方式（分解、warm start）。对象是本轮自己写的模型，不涉及他人代码或原文",
         "建议：先做 formulation 与参数（L1/L2）；动到 L3 就要把结论降级为启发式解"),
        ("D1", "数据从哪来：使用者提供 / 公开基准（我去找）/ 我生成 / 混合",
         "建议：优先使用者已有数据；无则锁定一个公开基准并写清来源；最后才合成"),
        ("D2", "数据里有没有官方参考值（最优解 / BKS / 下界）？由谁核验",
         "建议：有就逐条落地并标 OPT/BKS/LB；没有就明确「只能自建基线」，不许自造参考值"),
        ("D3", "允许对原始数据做哪些处理（清洗 / 裁剪 / 重采样 / 补齐规模档）",
         "建议：原始文件只读，处理写成脚本，保留处理前后对照与校验和"),
        ("D4", "数据的使用边界：能否外推到其他规模与分布，能否声称实际效益",
         "建议：合成或小规模数据只写算法相对表现，不写实际节省"),
        ("D5", "清洗口径：哪些记录允许删 / 改 / 补，判据是什么（原始文件默认只读）",
         "建议：只删可证录入错误，逐条留理由；缺失与极端值默认保留并标注，不做一刀切截尾"),
        ("D6", "模型参数从哪来：查表 / 实测 / 拟合 / 挖掘 / 假设；挖掘产物由谁验证",
         "建议：每个参数标来源类型；无出处的标[假设]并进灵敏度；落不到模型位置的挖掘一律不做"),
        ("0.1", "这个问题最接近哪个标准问题族，与它的差异是什么", "建议：先归入最接近的标准模型，差异逐条列"),
        ("0.2", "目标函数包含哪些项，什么算「更好」", "建议：先单目标、只计直接成本"),
        ("0.3", "规模与时间尺度上界（决定精确还是启发式路线）", "建议：给出 S/M/L 三档的具体量级"),
        ("0.4", "这项研究的贡献点定位（新问题 / 变体 / 复现改进 / 算法）", "建议：按现有材料能支撑的最强定位往下取一档"),
    ],
    [
        ("1.1", "什么算合法解，哪些约束绝对不可违反", "建议：先全部按硬约束"),
        ("1.2", "校验器要不要独立于求解器（不调用求解器判可行）", "建议：必须独立"),
        ("1.3", "初始可行解怎么构造（贪心 / 构造式 / 手工）", "建议：贪心 + 手工各一个"),
        ("1.4", "真出现不可行时怎么处理（放松 / 报错 / 证明不可行）", "建议：先报错并给违约码，不偷偷放松"),
    ],
    [
        ("2.1", "变量建模方式（指派 / 弧流 / 时间索引 / 析取图）", "建议：先选维度最小的等价写法"),
        ("2.2", "非线性项怎么线性化，Big-M 从哪来", "建议：取最小合法 M，并做 M 的敏感性"),
        ("2.3", "拿哪两套 formulation 做强度对比", "建议：逐条约束 vs 聚合约束"),
        ("2.4", "软约束的惩罚系数与多目标的处理方式", "建议：先全硬；多目标用加权并做权重敏感性"),
    ],
    [
        ("3.1", "把 D1 的数据契约落到具体算例清单（哪些文件 / 多少个 / 什么版本）", "建议：优先标准库，缺则生成器 + 全种子"),
        ("3.2", "规模分档与每档用途", "建议：S 验证、M 调参、L 主实验、XL 压力测试"),
        ("3.3", "基线清单，是否要求带最优性证书", "建议：至少一个可证最优的基线"),
        ("3.4", "时间上限与硬件口径（能否与文献直接比）", "建议：单线程 + 明确时限，不与文献绝对时间比"),
    ],
    [
        ("4.1", "精确方法选型（MILP / CP-SAT / DP / B&P）", "建议：先 MILP + 小规模枚举验证"),
        ("4.2", "下界来源（LP / Lagrangian / 组合 / 列生成）", "建议：先 LP，再补一个组合界"),
        ("4.3", "可接受 gap 与时间上限", "建议：小规模 gap=0，大规模 gap≤1% 或时限"),
        ("4.4", "小规模独立精确解的验证方式", "建议：暴力枚举或 DP，逐位对照"),
    ],
    [
        ("5.1", "算法范式（构造+局部搜索 / ALNS / GA / TS / 数学启发），并说明为什么匹配问题结构", "建议：构造 + 局部搜索起步，再上 ALNS；按算法手册 §1.1 映射表核对"),
        ("5.2", "邻域算子清单与优先级", "建议：relocate / swap / 2-opt / ruin-recreate"),
        ("5.3", "调参预算与调参集（不许用测试集调参）", "建议：只用 M 档，预算固定并写明"),
        ("5.4", "随机性与多次重启策略", "建议：固定种子 + 5 次独立重启取最好"),
        ("5.5", "理论义务到哪一档（正确性证明 / 近似比 / locality gap / 只做经验评估）", "建议：先明确只做经验评估，再判断值不值得补证明；见证明规程 §0"),
        ("5.6", "算法实现交付形态（伪代码 / 可运行代码 / 骨架接入 + 三项自测）", "建议：可运行代码 + 三项自测；骨架见 scripts/algotemplates"),
    ],
    [
        ("6.1", "主指标口径（gap to BKS / gap to LB / 时间到目标）", "建议：同时报 gap to BKS 与 gap to LB"),
        ("6.2", "对照臂与配对方式", "建议：同算例同种子配对，超时单列"),
        ("6.3", "显著性方法与效应量阈值", "建议：符号检验 + α=0.05，报效应量"),
        ("6.4", "消融 / 敏感性清单（算法侧与数据侧分开列）", "建议：算子消融 + 关键参数 + 规模敏感性；再加清洗三档与关键假设参数的保守/乐观值"),
    ],
    [
        ("7.1", "结论能写到多强（最强可辩护的措辞）", "建议：按证据强度逐条降级到可辩护"),
        ("7.2", "交付形态（论文 / 技术报告 / 内部备忘）", "建议：先技术报告，再压缩成论文"),
        ("7.3", "局限与未测清单写多细", "建议：至少 3 条，含未测规模与未做对比"),
        ("7.4", "复现包范围（代码 / 数据 / 环境 / 一键脚本）", "建议：干净环境可粘贴执行"),
    ],
]

EXTRA_FILES = {
    "复现清单.md": """# 复现清单

| 项 | 内容 |
|---|---|
| 环境 |  |
| 解释器（**写绝对路径**，默认 `python` 常是缺库的精简版） |  |
| 关键依赖及版本 |  |
| 求解器与许可证 |  |
| 全局 / 算例 / 各臂种子 |  |
| 算例来源与生成参数 |  |
| 命令顺序（生成 → 各臂 → 配对 → 出图 → 守门） |  |
| 预期产物与配对数 n |  |
| 守门命令 |  |
| 缺少依赖时的降级路径 |  |

```bash
# 从干净环境开始，逐条粘贴
```
""",
    "results/README.md": """# results/

原始结果目录，**只读证据**。

- 数字不对就重跑，禁止手改 CSV/JSON。
- 每个实验臂用独立文件名，绝不覆盖基线。
- 快速链路验证（小轮数/少种子/单算例）用独立文件名，其数字不进报告。
""",
    "data/README.md": """# data/

算例与生成参数。

- 记录生成器版本、生成参数与随机种子；换生成参数等于换样本，历史结果不可直接比较。
- 金标准算例改动要有意为之，并全量重跑。
""",
    "figures/README.md": """# figures/

图件。图与表逐格一致，同一算例的数字须来自同一次求解。

- 风格统一走同一个 style 入口；图内语言统一，图例挂坐标区框外。
- 敏感性图每点多个独立种子取均值并标误差棒。
""",
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stage_meta(state: dict, idx: int) -> dict:
    """阶段名 / 文件名以 state.json 为准（init 时冻结），兼容改版后的旧研究目录。"""
    entry = state["stages"][idx]
    base = STAGES[idx] if idx < len(STAGES) else {"name": "", "file": "", "checks": []}
    return {
        "name": entry.get("name") or base["name"],
        "file": entry.get("file") or base["file"],
        "checks": base.get("checks", []),
    }


def _route_of(state: dict) -> str:
    """取 M1（交付形态）的留痕，供 status 显示路线；未采集则返回空串。"""
    for s in state.get("stages", []):
        for d in s.get("decisions", []):
            if d.get("id") == "M1":
                return str(d.get("answer", "")).strip()
    return ""


def skeleton(idx: int, idea: str) -> str:
    s = STAGES[idx]
    checks = "\n".join(f"- [ ] {c}" for c in s["checks"])
    decision_rows = "\n".join(f"| {d[0]} | {d[1]} |  |  |" for d in DECISIONS[idx])
    head = (
        f"# Stage {idx} · {s['name']}\n\n"
        f"> 状态：⬜ 待确认　|　判据详见 `references/阶段门判据.md`\n"
        f"> 交互协议见 `references/交互协议.md`；开工先跑 `study.py ask {idx}` 采集决策点\n"
    )
    if idx == 0:
        head += f"> 原始 idea：{idea}\n"
    return (
        f"{head}\n{s['body']}\n"
        "## 本阶段决策与思路\n\n"
        "> 来源列严格区分 `用户` 与 `[AI 建议]`；「没反对」不算用户决策。\n\n"
        "| 编号 | 决策点 | 用户的选择 | 来源 |\n"
        "|---|---|---|---|\n"
        f"{decision_rows}\n\n"
        "**用户的思路 / 理由**：（由 `study.py pass --rationale` 填入）\n\n"
        f"## 出口判据\n\n{checks}\n\n"
        "## 待确认事项（gate 汇报时逐条问）\n\n- \n\n"
        "## 确认记录\n\n（由 `study.py pass` 追加，请勿手改）\n"
    )


def load_state(study: Path) -> dict:
    p = study / "state.json"
    if not p.exists():
        raise SystemExit(f"找不到 {p}\n请先用 `study.py init` 创建研究目录，或用 --study 指定正确路径。")
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_state(study: Path, state: dict) -> None:
    with (study / "state.json").open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    study = root / args.name
    if (study / "state.json").exists():
        raise SystemExit(f"{study} 已有 state.json；请先跑 `status` 查看进度，不要重复 init。")
    study.mkdir(parents=True, exist_ok=True)
    for sub in ("data", "results", "figures"):
        (study / sub).mkdir(exist_ok=True)

    state = {
        "schema": SCHEMA,
        "name": args.name,
        "idea": args.idea,
        "created": now(),
        "current": 0,
        "mode": "gate",
        "stages": [
            {
                "id": i,
                "name": s["name"],
                "file": s["file"],
                "skeleton": skeleton(i, args.idea),
                "status": "pending",
                "confirmed_at": None,
                "note": "",
                "forced": False,
                "decisions": [],
                "rationale": "",
            }
            for i, s in enumerate(STAGES)
        ],
        "history": [],
    }
    for i, s in enumerate(STAGES):
        (study / s["file"]).write_text(state["stages"][i]["skeleton"], encoding="utf-8")
    for name, text in EXTRA_FILES.items():
        (study / name).write_text(text, encoding="utf-8")
    save_state(study, state)

    print(f"✅ 研究目录已创建：{study}")
    print("   8 个阶段文档 + state.json + 复现清单.md + data/ results/ figures/")
    print("   六环节：需求澄清 → 数学建模 → 算法求解 → 结果验证 → 灵敏度分析 → 方案交付")
    print("   工程检查点：" + " → ".join(s["name"] for s in STAGES))
    print("\n下一步（务必先问后做）：先跑 ask 0 采集 Stage 0 的决策点，")
    print("       再按用户回答填写 00_问题界定.md，收尾逐条复述确认后才 pass。")
    print(f'  python "{Path(__file__).resolve()}" ask 0 --study "{study}"')
    print(f'  python "{Path(__file__).resolve()}" status --study "{study}"')
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    study = Path(args.study).resolve()
    state = load_state(study)
    cur = state["current"]
    marks = " ".join(
        f"{i}{'✅' if s['status'] == 'confirmed' else ('⏳' if i == cur else '⬜')}"
        for i, s in enumerate(state["stages"])
    )
    print(f"研究：{state['name']}")
    print(f"目录：{study}")
    print(f"模式：{state['mode']}（每道门双向确认：采集决策 → 产出 → 复述确认）")
    print(f"进度：{marks}")
    print(f"原始 idea：{state['idea']}")
    syn = state.get("synthetic")
    if syn:
        print(f"⚠️  算例状态：合成数据（{syn.get('accepted_at', '')} 起接受）——"
              "结论只写算法相对表现，不得声称实际效益")
    if cur >= len(STAGES):
        print("\n🎉 8 道门全部确认。先跑 decisions 复述用户决策，再按交付规范做最终验收。")
        print(f'  python "{Path(__file__).resolve()}" decisions --study "{study}"')
        return 0
    s = stage_meta(state, cur)
    print(f"\n当前门：Stage {cur} · {s['name']}（{s['file']}）")
    recorded = len(state["stages"][cur].get("decisions", []))
    print(f"决策点：{len(DECISIONS[cur])} 个（已记录 {recorded} 条）；用 ask {cur} 查看并一次性提问")
    intent = _route_of(state)
    if intent:
        print(f"路线（M1）：{intent}")
    elif cur == 0:
        print("路线（M1）：未定 —— 先问「求解器直用 / 自研算法 / 完整研究 / 业务方案」，"
              "它决定后面走哪条裁剪路径")
    print("出口判据：")
    for c in s["checks"]:
        print(f"  - [ ] {c}")
    confirmed = [x for x in state["stages"] if x["status"] == "confirmed"]
    if confirmed:
        last = confirmed[-1]
        print(f"\n上次确认：Stage {last['id']} 于 {last['confirmed_at']} — {last['note']}")
        if last.get("rationale"):
            print(f"  用户思路：{last['rationale']}")
    print("\n下一步：ask 采集决策 → 按回答填写本阶段文档 → gate 逐条复述确认 → 用户认可后：")
    print(f'  python "{Path(__file__).resolve()}" pass {cur} --note "<用户确认了什么>" '
          f'--decision "<编号>=<用户选择>" --rationale "<用户的思路>" --study "{study}"')
    return 0


def mark_doc(study: Path, state: dict, idx: int, line: str, status: str) -> None:
    p = study / stage_meta(state, idx)["file"]
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8")
    text = text.replace("> 状态：⬜ 待确认", f"> 状态：{status}")
    text = text.replace("> 状态：✅ 已确认", f"> 状态：{status}")
    if not text.endswith("\n"):
        text += "\n"
    p.write_text(text + f"- {line}\n", encoding="utf-8")


def parse_choices(items: list[str], stage: int) -> list[dict]:
    """解析 `编号=用户的选择`，并校验编号属于该阶段。"""
    valid = {d[0] for d in DECISIONS[stage]}
    out = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--decision / --suggestion 需要 `编号=选择`，收到：{item}")
        did, ans = item.split("=", 1)
        did, ans = did.strip(), ans.strip()
        if did not in valid:
            raise SystemExit(f"Stage {stage} 没有编号 {did}；可用：{', '.join(sorted(valid))}")
        if not ans:
            raise SystemExit(f"{did} 的选择不能为空")
        out.append({"id": did, "answer": ans})
    return out


def fill_decisions(study: Path, state: dict, idx: int,
                   choices: dict[str, tuple[str, str]], rationale: str) -> None:
    """把用户的决策与思路写回本阶段文档的表格；choices = {编号: (选择, 来源)}。"""
    p = study / stage_meta(state, idx)["file"]
    if not p.exists():
        return
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        replaced = False
        for did, (ans, src) in choices.items():
            if line.startswith(f"| {did} |"):
                parts = line.split("|")
                q = parts[2].strip() if len(parts) > 2 else ""
                out.append(f"| {did} | {q} | {ans} | {src} |")
                replaced = True
                break
        if replaced:
            continue
        if line.startswith("**用户的思路 / 理由**："):
            out.append(f"**用户的思路 / 理由**：{rationale or '（未记录，见交互协议 §5）'}")
            continue
        out.append(line)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def reset_decisions(study: Path, state: dict, idx: int) -> None:
    """reopen 时清空本阶段决策表，避免旧决策被误当成新确认。"""
    p = study / stage_meta(state, idx)["file"]
    if not p.exists():
        return
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        hit = False
        for did, q, _ in DECISIONS[idx]:
            if line.startswith(f"| {did} |"):
                out.append(f"| {did} | {q} |  |  |")
                hit = True
                break
        if hit:
            continue
        if line.startswith("**用户的思路 / 理由**："):
            out.append("**用户的思路 / 理由**：（由 `study.py pass --rationale` 填入）")
            continue
        out.append(line)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def cmd_ask(args: argparse.Namespace) -> int:
    """开场：打印该阶段必须由用户拍板的决策点，供 AI 一次性打包提问。"""
    study = Path(args.study).resolve()
    state = load_state(study)
    idx = args.stage if args.stage is not None else state["current"]
    if not 0 <= idx < len(STAGES):
        raise SystemExit("所有阶段已确认；如需返工请用 `reopen`。")
    print(f"【Stage {idx} · {stage_meta(state, idx)['name']} 开工前 · 请先定这几件事】")
    print("把下列问题一次性打包给用户；回答格式：编号 + 选择 + 一句理由。\n")
    group = None
    for did, q, hint in DECISIONS[idx]:
        if idx == 0:
            want = ("E" if did.startswith("E") else "M" if did.startswith("M")
                    else "D" if did.startswith("D") else "Q")
            if want != group:
                if want == "E":
                    print("  ── A. 入口分诊（第一问：你手上已有什么，决定这一步先做什么）──")
                elif want == "M":
                    print("  ── B. 使用意图与求解路线（决定后面每一步的强度与产出形态）──")
                elif want == "D":
                    print("  ── C. 数据契约（来源 → 能用到哪 → 清洗口径 → 参数来源；来源未定，后面全是空转）──")
                else:
                    print("  ── D. 问题定位 ──")
                group = want
        print(f"  {did}. {q}")
        print(f"      [AI 建议] {hint}")
    if idx == 0:
        print("\n  ── 按 E1 分诊：这一步的「第一步产出」是哪一个（缺了过不了门）──")
        print("     场景+数据   → 数据充足性判定 A/B/C，写 §0.6 / §0.7")
        print("     场景无数据 → 数据需求反查表 + 取数方案，写 §0.86")
        print("     数据无场景 → ≥3 个目标不同的候选场景卡，写 §0.9")
        print("     只有 idea  → 场景化四问（谁/何时/决定什么/怎么算好），写 §0.85")
        print("     ↳ 只有 idea 时，四问答完若「怎么算好」出现多个互斥分母，再补 §0.9 让用户选口径。")
        print("     注：E1 未定时 D1–D6 允许先答「待定 + 为什么现在答不了」；"
              "场景冻结后回填不算跳门，但要在 §0.86 写清卡点。")
    print("\n规则：用户明确给出的用 --decision；未指定而由 AI 定的用 --suggestion，"
          "并在 gate 单独复述确认。")
    print("      「都行」「你看着办」不算决策，按 references/交互协议.md §4 追问，不许自行补全。")
    first = DECISIONS[idx][0][0]
    print("\n收尾确认时的 pass 模板：")
    print(f'  python "{Path(__file__).resolve()}" pass {idx} '
          f'--note "<用户确认了什么>" --decision "{first}=<用户选择>" '
          f'--rationale "<用户的思路>" --study "{study}"')
    return 0


def cmd_decisions(args: argparse.Namespace) -> int:
    """汇总 8 个阶段的用户决策与思路，供 Stage 7 收尾复述。"""
    study = Path(args.study).resolve()
    state = load_state(study)
    print(f"用户决策与思路汇总 · {state['name']}")
    print("Stage 7 收尾必须逐条向用户复述；[AI 建议] 不是用户决策。\n")
    ai_total = 0
    for i, s in enumerate(state["stages"]):
        nm = stage_meta(state, i)["name"]
        if s["status"] != "confirmed":
            print(f"Stage {i} · {nm}　⬜ 未确认")
            continue
        print(f"Stage {i} · {nm}　✅ {s.get('confirmed_at', '')}")
        ds = s.get("decisions", [])
        if not ds:
            print("   （无决策留痕 —— 检查是否违规跳过了 ask）")
        for d in ds:
            src = d.get("source", "用户")
            if "[AI 建议]" in src:
                ai_total += 1
            tag = src if src.startswith("[") else f"[{src}]"
            print(f"   {d['id']}: {d['answer']}  {tag}")
        uncovered = s.get("uncovered", [])
        if uncovered:
            print(f"   ⚠️  未采集的决策点：{', '.join(uncovered)}"
                  "（须说明原因，见交互协议 §4）")
        print(f"   思路：{s.get('rationale') or '（未记录）'}")
    print(f"\n合计 [AI 建议] {ai_total} 条。这些项不是用户拍板，收尾需单独复述确认，"
          "最终交付里不得写成「用户决定」。")
    return 0


def cmd_pass(args: argparse.Namespace) -> int:
    study = Path(args.study).resolve()
    state = load_state(study)
    idx = args.stage
    if not 0 <= idx < len(STAGES):
        raise SystemExit(f"阶段号须在 0–{len(STAGES) - 1} 之间")
    cur = state["current"]
    if cur >= len(STAGES):
        raise SystemExit("8 道门已全部确认；若要返工请用 `reopen`。")
    if idx != cur and not args.force:
        raise SystemExit(
            f"当前门是 Stage {cur}，不能确认 Stage {idx}。\n"
            "阶段门必须逐道确认；确需跳门请显式加 --force（会留痕）。"
        )
    meta = stage_meta(state, idx)
    doc = study / meta["file"]
    if not doc.exists():
        raise SystemExit(f"缺少阶段文档：{doc}")

    # 决策采集校验：至少一条用户拍板；只有 [AI 建议] 时按协议要追问。
    user_choices = parse_choices(args.decision, idx)
    ai_choices = parse_choices(args.suggestion, idx)
    overlap = {d["id"] for d in user_choices} & {d["id"] for d in ai_choices}
    if overlap:
        raise SystemExit(f"同一编号不能既是用户决策又是 [AI 建议]：{', '.join(sorted(overlap))}")
    if not user_choices and not args.force:
        raise SystemExit(
            "本阶段没有记录任何用户决策。\n"
            "按 references/交互协议.md：先跑 `ask <N>` 采集决策点，用户含糊时按 §4 追问；\n"
            "确属用户明确表示全权委托，请加 --force（会留痕，并在交付里列为 [AI 建议]）。"
        )
    # 空骨架检查只看模型/产出正文；决策表由上面的采集校验单独把关。
    # 优先用 init 时冻结的骨架快照，改版后旧目录也不会误判。
    saved = state["stages"][idx].get("skeleton")
    if saved is None:
        print("   ℹ️  旧研究目录无骨架快照，跳过「空文档」检查（决策采集校验仍生效）。")
    else:
        base = saved.split("## 本阶段决策与思路")[0].strip()
        filled = doc.read_text(encoding="utf-8").split("## 本阶段决策与思路")[0].strip()
        if not args.force and len(filled) < len(base) + 20:
            raise SystemExit(
                f"{meta['file']} 看起来仍是空骨架。\n"
                "请先按用户回答填写实质内容并完成 gate 复述；确需强行通过请加 --force。"
            )

    # 入口专项：四个入口各有自己的「第一步产出」，只填了分诊不填产出 = 没过门
    # （--force 可显式越过；越过会记 forced=True 留痕）
    if idx == 0 and not args.force:
        e1 = next((d["answer"] for d in user_choices if d["id"] == "E1"), "")
        text = doc.read_text(encoding="utf-8")

        def _sec(head: str) -> str:
            """取某个二级标题到下一个二级标题之间的正文。"""
            if head not in text:
                return ""
            return text.split(head, 1)[1].split("\n## ", 1)[0]

        def _rows(sec: str) -> list:
            """取出表格数据行（去掉表头与分隔线），并按单元格切分。"""
            lines = [ln for ln in sec.splitlines()
                     if ln.startswith("|") and not set(ln) <= set("|-: ")]
            return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines[1:]]

        # 入口识别：按关键词判，不用选项原文——选项写「只有一句 idea」，按「只有 idea」匹配会静默失效
        low = e1.lower()
        entry = ("E3" if "数据无场景" in e1 else
                 "E2" if "场景无数据" in e1 else
                 "E4" if "idea" in low else
                 "E1" if "场景+数据" in e1 or "场景＋数据" in e1 else "")
        if e1 and not entry:
            print(f"   ⚠️  E1 的答案「{e1}」对不上任何已知入口，过门校验已跳过——请检查选项措辞。")

        if entry == "E3":
            sec = _sec("## 0.9")
            filled_rows = [c for c in _rows(sec) if len([x for x in c if x]) >= 5]
            # 等级可能写成 **B** / B（仅子集），先去掉加粗与空白再取首字母
            grades = [c[3].strip("*_ `　")[:1] for c in filled_rows
                      if len(c) > 3 and c[3].strip("*_ `　")[:1] in ("A", "B", "C")]
            adopted = [c for c in filled_rows if c and "采用" in c[-1]]
            tagged = "[x]" in sec or "[X]" in sec
            if len(filled_rows) < 3 or len(grades) < 3 or not adopted or not tagged:
                raise SystemExit(
                    "入口是「数据无场景」，但 00 的 §0.9 没填够：\n"
                    f"  填满的候选卡 {len(filled_rows)} 条（要 ≥3，每行至少 5 个单元格非空）\n"
                    f"  带 A/B/C 标注的 {len(grades)} 条（每条都要标）\n"
                    f"  『采用』标记 {len(adopted)} 处（必须明确选了哪个）\n"
                    f"  场景来源标注 {'已勾选' if tagged else '未勾选'}\n"
                    "按 references/场景构思.md §4：≥3 个目标不同的候选 + 各自 A/B/C + 一个保守选项，\n"
                    "并在 §0.9 末尾勾选「由画像推得未确认 / 用户即业务方已确认」。"
                )

        elif entry == "E4":

            sec = _sec("## 0.85")
            rows = _rows(sec)
            # 四问的答案在表格第 3 列；只看答案列，别把追问列里的反例词误判成答案
            answers = [r[2] for r in rows if len(r) > 2 and r[2]]
            weak = [w for w in ("相关部门", "定期", "优化资源配置", "有待明确", "暂定")
                    if any(w in a for a in answers)]
            solid = [a for a in answers if len(a) >= 8]
            if len(rows) < 4 or len(solid) < 4 or weak:
                raise SystemExit(
                    "入口是「只有 idea」，但 00 的 §0.85 场景化四问没答完：\n"
                    f"  表格行 {len(rows)} 条（要 4 问齐全）\n"
                    f"  答案≥8 字的 {len(solid)} 条（四问都要落到具体）\n"
                    f"  含含糊词 {('、'.join(weak) if weak else '无')}\n"
                    "按 references/场景构思.md §6：谁做决定（岗位名）/ 什么时候做（频率+时间窗）/\n"
                    "决定什么（可执行动作）/ 怎么算好（含分母与现状值）。\n"
                    "四问答不上来就退回 §0.9 找场景，不要直接建模。"
                )
            sec5 = _sec("## 0.86")
            need = [c for c in _rows(sec5) if len([x for x in c if x]) >= 6]
            if not need:
                raise SystemExit(
                    "入口是「只有 idea」，但 00 的 §0.86 数据需求反查表还是空的。\n"
                    "场景冻结之后，第一步就是按决策变量反查字段：每个决策变量一行，\n"
                    "写清需要的参数、对应字段、取数来源、时间窗、责任人、缺了怎么降级。"
                )

        elif entry == "E2":
            sec5 = _sec("## 0.86")
            need = [c for c in _rows(sec5) if len([x for x in c if x]) >= 5]
            if len(need) < 2:
                raise SystemExit(
                    "入口是「场景无数据」，但 00 的 §0.86 数据需求反查表不够：\n"
                    f"  填满的行 {len(need)} 条（至少 2 条：一个决策变量一行）\n"
                    "场景已有，第一步是反查「要做这个决策需要哪些数」并给取数方案，\n"
                    "不是拿合成数据顶上（合成算例只能写算法相对表现）。"
                )

        # 数据契约未闭合的入口（E2/E4）允许过门，但必须接下限制，否则算偷偷越界
        if entry in ("E2", "E4"):
            c6 = _sec("## 0.6")
            if "未闭合" in c6:
                print("   ⚠️  数据契约状态=未闭合：Stage 1 只能做构造性可行解，"
                      "结论里不许出现「改善 x% / 节省 y 元」；Stage 6 敏感性用假设区间做。")

    # Stage 3 专项：算例是合成的，不许静默过门。
    # 原判据写「生成器 + 全部种子 + 参数」即可，于是全合成的研究能合规一路走到 Stage 7，
    # 而唯一拦住它的是 00 §0.6 的一句文字纪律——没有程序化校验。这里补上。
    if idx == 3 and not args.force:
        text = doc.read_text(encoding="utf-8")
        sec = text
        if "## 1. 算例来源" in text:
            sec = text.split("## 1. 算例来源", 1)[1].split("\n## ", 1)[0]
        synthetic = "合成" in sec or "synthetic" in sec.lower()
        if synthetic and not getattr(args, "accept_synthetic", False):
            raise SystemExit(
                "Stage 3 的算例是**合成数据**，不能直接过门。\n"
                "下一步二选一：\n"
                "  ① 拿到真实数据（哪怕只有订单 + 派车记录）→ 重跑 Stage 3；\n"
                "  ② 确认这一轮只为「打通流程 / 内部探路」，加 --accept-synthetic 显式认下三条限制：\n"
                "     · Stage 4–7 的结论只写算法相对表现，不写任何实际效益（不出现「降 x% / 省 y 元」）\n"
                "     · Stage 6 的敏感性用假设区间做，不用点估计做\n"
                "     · Stage 7 的交付物必须带「数据为合成，结论仅供内部参考」字样\n"
                f"\n03_算例与基线.md §1 的来源行：{sec.strip().splitlines()[:3]}"
            )
        if synthetic:
            state["synthetic"] = {"accepted_at": now(), "note": args.note,
                                  "limits": "结论只写算法相对表现；不得声称实际效益；交付须带合成标注"}
            print("   ⚠️  已接受合成算例（--accept-synthetic）：Stage 4–7 不得出现任何实际效益数字；"
                  "Stage 7 交付须带「数据为合成」字样。")

    # Stage 7 专项：合成算例下，交付文档里出现效益词却不见免责字样时提醒（不拦，只警示）
    if idx == 7 and state.get("synthetic"):
        text = doc.read_text(encoding="utf-8")
        claims = [w for w in ("降本", "节省", "省了", "提升", "下降") if w in text]
        if claims and not any(w in text for w in ("合成", "不对外", "仅供内部")):
            print(f"   ⚠️  交付文档出现效益词 {claims}，但找不到「合成 / 不对外 / 仅供内部」的免责说明。")

    decisions = [dict(d, source="用户") for d in user_choices] + [
        dict(d, source="[AI 建议]") for d in ai_choices
    ]
    covered = {d["id"] for d in decisions}
    missing = [d[0] for d in DECISIONS[idx] if d[0] not in covered]
    choices = {d["id"]: (d["answer"], d["source"]) for d in decisions}
    fill_decisions(study, state, idx, choices, args.rationale)

    stamp = now()
    entry = state["stages"][idx]
    entry.update(status="confirmed", confirmed_at=stamp, note=args.note,
                 forced=bool(args.force), decisions=decisions, rationale=args.rationale,
                 uncovered=missing)
    state["current"] = max(cur, idx + 1) if args.force else idx + 1
    state["history"].append({"at": stamp, "action": "pass", "stage": idx, "note": args.note,
                             "forced": bool(args.force), "decisions": decisions,
                             "rationale": args.rationale})
    save_state(study, state)
    mark_doc(study, state, idx, f"Stage {idx} 确认于 {stamp} — {args.note}" + ("（--force 跳门）" if args.force else ""),
             "✅ 已确认")

    print(f"✅ Stage {idx} · {meta['name']} 已确认：{args.note}")
    print(f"   用户决策 {len(user_choices)} 条 / [AI 建议] {len(ai_choices)} 条；"
          f"思路：{args.rationale or '（未记录，见交互协议 §5）'}")
    if ai_choices:
        print("   ⚠️  [AI 建议] 项不是用户拍板，收尾需单独复述确认。")
    if missing:
        print(f"   ⚠️  未采集的决策点：{', '.join(missing)} —— gate 汇报里须说明"
              "（用户全权委托 / 仍需追问），不许静默略过。")
    nxt = state["current"]
    if nxt >= len(STAGES):
        print("🎉 8 道门全部确认。跑 decisions 复述用户决策，再按交付规范做最终验收。")
    else:
        nxt_meta = stage_meta(state, nxt)
        print(f"⏳ 当前门推进到 Stage {nxt} · {nxt_meta['name']}（{nxt_meta['file']}）")
        print("   提醒：先跑 ask 采集下一阶段决策，不要自行连续推进。")
    return 0


def cmd_reopen(args: argparse.Namespace) -> int:
    study = Path(args.study).resolve()
    state = load_state(study)
    idx = args.stage
    if not 0 <= idx < len(STAGES):
        raise SystemExit(f"阶段号须在 0–{len(STAGES) - 1} 之间")
    stamp = now()
    for s in state["stages"]:
        if s["id"] >= idx and s["status"] == "confirmed":
            s.update(status="pending", confirmed_at=None, note="", forced=False,
                     decisions=[], rationale="", uncovered=[])
            reset_decisions(study, state, s["id"])
            mark_doc(study, state, s["id"], f"Stage {s['id']} 于 {stamp} 重新打开 — {args.note}", "⬜ 待确认")
    state["current"] = idx
    state["history"].append({"at": stamp, "action": "reopen", "stage": idx, "note": args.note})
    save_state(study, state)
    print(f"↩️  Stage {idx} 及其之后的确认已清空，当前门回退到 Stage {idx}。")
    print(f"   原因：{args.note}")
    print("   决策表已清空，请重新跑 ask 采集；后续受影响的结果需要重跑，不要沿用旧数字。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="创建研究目录与 8 个阶段文档")
    p.add_argument("name", help="研究目录名")
    p.add_argument("--root", default=".", help="父目录（默认当前目录）")
    p.add_argument("--idea", default="（未记录）", help="用户的原始场景描述")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ask", help="开场：打印该阶段必须由用户拍板的决策点")
    p.add_argument("stage", type=int, nargs="?", default=None, help="阶段号 0–7（默认当前门）")
    p.add_argument("--study", default=".", help="研究目录（默认当前目录）")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("status", help="查看当前阶段门、判据与进度")
    p.add_argument("--study", default=".", help="研究目录（默认当前目录）")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("pass", help="收尾：复述确认当前门，并留痕用户决策与思路")
    p.add_argument("stage", type=int, help="阶段号 0–7")
    p.add_argument("--note", required=True, help="用户确认了什么（留痕）")
    p.add_argument("--decision", action="append", default=[], metavar="编号=选择",
                    help="用户拍板的决策，可重复；编号来自 ask")
    p.add_argument("--suggestion", action="append", default=[], metavar="编号=选择",
                    help="用户未指定而由 AI 定的值，标为 [AI 建议]，可重复")
    p.add_argument("--rationale", default="", help="用户的思路 / 理由（不是 AI 的解释）")
    p.add_argument("--study", default=".", help="研究目录（默认当前目录）")
    p.add_argument("--force", action="store_true",
                    help="跳门、跳过空骨架检查，或用户全权委托（会留痕）")
    p.add_argument("--accept-synthetic", action="store_true",
                    help="Stage 3 专用：算例是合成数据时，显式确认「结论不对外」后才能过门")
    p.set_defaults(func=cmd_pass)

    p = sub.add_parser("decisions", help="汇总 8 个阶段的用户决策与思路")
    p.add_argument("--study", default=".", help="研究目录（默认当前目录）")
    p.set_defaults(func=cmd_decisions)

    p = sub.add_parser("reopen", help="推翻某道门，回退进度")
    p.add_argument("stage", type=int, help="阶段号 0–7")
    p.add_argument("--note", required=True, help="回退原因")
    p.add_argument("--study", default=".", help="研究目录（默认当前目录）")
    p.set_defaults(func=cmd_reopen)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
