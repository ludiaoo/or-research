# or-research

[![tests](https://github.com/ludiaoo/or-research/actions/workflows/ci.yml/badge.svg)](https://github.com/ludiaoo/or-research/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)

一个面向运筹优化的 AI Skill：把一句模糊的业务设想，变成**能执行、能解释、能复算**的优化方案；需要时再升级成论文。

它既是 Agent 可读的工作规程（`SKILL.md`），也是一组可独立运行的命令行工具——建模、求解、统计验证与地图可视化都在同一个仓库里，不预设问题领域或求解器。

仓库自带 MIT 许可、离线测试与合成示例数据，可直接克隆使用。

## 特性

- **阶段门驱动**：四种入口分诊、六环节、八检查点；`state.json` 是跨会话唯一进度依据
- **工件齐全**：模型卡、可行解、独立校验器、LP 界、integrality gap、敏感性曲线
- **求解器与算法**：MILP（HiGHS / 可选 Gurobi）+ ALNS / SA / TS / GA 四引擎
- **统计纪律**：强制同 `(算例, 种子)` 配对，口径混用直接返回非零码
- **结果可视化**：高德静态底图 + 可选 JS 层；方案切换、逐条/架次显隐、无人机架次、禁飞区
- **参考文档**：20 篇，覆盖建模规范、算法手册、证明规程、实验统计、能力边界与无人机配送专题

## 安装

本项目不是 Python 包，无需 `pip install`。克隆后放到 Agent 的 skills 目录即可：

```bash
git clone https://github.com/ludiaoo/or-research.git or-research
# 目标位置：~/.workbuddy/skills/or-research/
```

环境要求：**Python 3.10+**。核心脚本只用标准库。

| 依赖 | 谁需要 | 必需性 |
|---|---|---|
| 标准库 | `study.py`、`data_profile.py`、`expstats.py`、`paired_stats.py`、`algotemplates/`、`adapt_tddo.py`、`build_map.py` | 自带 |
| `numpy` + `scipy` | `milp_lab.py` 的 HiGHS 后端（`scipy.optimize.milp`） | 用 MILP 功能时 |
| `gurobipy` | `milp_lab.py` 可选优先后端；`solver_bench.py` **硬依赖**（需许可证） | 用求解器调优时 |
| `requests` | `amap/assets/amap_client.py`（POI、矩阵、路径、静态地图） | 用高德接口时 |

## 快速开始

在仓库根目录执行：

```bash
# 1. 自检：四引擎 + 故意写错的负对照
python scripts/algotemplates/selftest.py

# 2. 生成一个完整研究目录（8 个阶段文档 + state.json）
python scripts/study.py init demo --root ./demo --idea "把这句换成你的业务设想"
python scripts/study.py status --study ./demo

# 3. 生成 MILP 样例并对比强弱 formulation
python scripts/milp_lab.py demo --out ./lab
python scripts/milp_lab.py compare ./lab/pmed_strong.json ./lab/pmed_weak.json

# 4. 跑一次算法演示（最近邻基线 vs ALNS / SA / TS / GA）
python scripts/algotemplates/demo_tsp.py

# 5. 仓库级测试（9 项，离线、无需 Gurobi 与高德 key）
python -m unittest discover -s tests -v
```

## 示例数据

`examples/` 提供两份合成样例，用于验证转换链路，不含真实业务数据：

- `sample_solution.json`：`adapt_tddo.py` 的输入，结构与 `export_web.py` 的 `solution_set*.json` 一致
- `sample_payload.json`：适配器实际输出，可直接喂给 `build_map.py`

```bash
python amap/scripts/adapt_tddo.py examples/sample_solution.json -o /tmp/payload.json
python amap/scripts/build_map.py examples/sample_payload.json -o /tmp/sample_map.html --no-js
```

第二条命令需要 `AMAP_KEY`；不配 key 时仍可通过 `unittest` 离线验证 payload 契约与视野计算。

## 使用方式

### 作为 Skill

入口是根目录 `SKILL.md`。支持四类起点：场景+数据、场景缺数据、数据缺场景、只有一句 idea；也支持「只想把模型解快点」的求解器直用路线，以及把已有定稿解画成地图。每道门都要求先采集使用者决策、产出工件，再复述确认留痕。

### 作为命令行工具

各脚本可脱离 Agent 独立运行。参数写法见各脚本 `--help`，速查见下文。

### 高德可视化

高德模块只画**已定稿的解**，不求解、不产生也不修改数字。输入 payload 的字段契约见 `amap/MODULE.md`；仓库未附带示例 payload，需按契约自行准备。

```bash
python amap/scripts/adapt_tddo.py solution_set112.json -o payload.json
python amap/scripts/build_map.py payload.json -o map.html --no-js
```

`--no-js` 不注入任何前端凭据，输出可提交的纯静态页面；不加该参数时会从 `.env` 读取 JS key，生成可拖拽版本。

## 配置

需要调用高德接口时，复制 `amap/assets/.env.example` 为 `.env` 并填值：

```ini
AMAP_KEY=...          # Web 服务：距离矩阵 / POI / 静态地图 / 驾车路径
AMAP_JS_KEY=...       # JS API：注入页面，靠控制台域名白名单兜底
AMAP_JS_SECRET=...    # 与 JS key 配套的安全密钥（securityJsCode）
```

两类 key 权限不同，不能混用：`AMAP_KEY` 能扣配额，只在服务端使用，**绝不写进页面**；JS key 必须公开在页面里，安全性靠域名白名单。没有 JS key 也能出完整图，只是不能拖拽。

`amap_client.py` 的查找顺序为：用户主目录 → skill 根 → 当前目录向上 8 级 → `AMAP_ENV_FILE` 显式指定；进程环境变量优先级最高，空值不覆盖已读到的非空值。`.env` 不要提交。

## 目录结构

```
or-research/
├─ SKILL.md                  唯一入口：分诊 / 六环节 / 八检查点 / 硬红线 / 可视化路线
├─ README.md                 本文件
├─ LICENSE                   MIT（纯许可证原文）
├─ NOTICE                    第三方材料说明：高德数据条款、示例数据性质、依赖许可
├─ .gitignore                忽略 .env、缓存、生成目录与底图缓存
├─ .github/workflows/ci.yml  多 Python 版本离线测试
├─ examples/                 合成样例：solution → payload
├─ tests/test_repo.py        9 项仓库级测试（离线）
├─ references/               20 篇领域参考
│  ├─ 运筹内核.md 模型库.md 建模规范.md 算法手册.md 算法实现模板.md
│  ├─ 求解器调优.md 松弛与下界.md 证明规程.md 能力边界.md
│  ├─ 算例与基准.md 数据清洗与挖掘.md 场景构思.md 业务流程.md
│  └─ 交互协议.md 阶段门判据.md 实验与统计.md 写作与审稿.md 交付规范.md 通用踩坑清单.md 无人机配送专题.md
├─ scripts/
│  ├─ study.py               阶段门状态机
│  ├─ data_profile.py        CSV/TSV 数据画像
│  ├─ milp_lab.py            MILP 求解 / 松弛 / 强弱对比 / 解校验
│  ├─ solver_bench.py        Gurobi 参数档案对比
│  ├─ expstats.py            gap 表 / 透视表 / 性能剖面
│  ├─ paired_stats.py        配对符号检验
│  └─ algotemplates/         base + ALNS / SA / TS / GA + selftest / demo
└─ amap/                     高德可视化模块
   ├─ MODULE.md              详细操作手册：payload 全字段、key 类型、公式、五关校验
   ├─ assets/
   │  ├─ amap_client.py      POI / 矩阵 / 路径 / 静态地图 / 墨卡托 / .env 加载
   │  ├─ map_template.html   成品页面模板
   │  └─ .env.example        凭据字段模板
   ├─ scripts/
   │  ├─ adapt_tddo.py       solution_set*.json → payload
   │  └─ build_map.py        payload → 静态底图 → 自包含 HTML
   └─ references/pitfalls.md 七类地图坑，按症状查
```

## 命令速查

```bash
# 阶段门
python scripts/study.py init <name> --root <dir> --idea "<用户原话>"
python scripts/study.py ask <阶段号> --study <dir>
python scripts/study.py status --study <dir>
python scripts/study.py pass <阶段号> --note "..." --decision "0.1=<选择>" --rationale "<思路>" --study <dir>

# 数据与统计
python scripts/data_profile.py orders.csv --key order_id --time ts --out profile.md
python scripts/paired_stats.py A.csv B.csv --keys instance,seed --value objective --expect-n 160 --csv pairs.csv
python scripts/expstats.py --help       # gap / table / profile

# 建模与求解
python scripts/milp_lab.py solve spec.json --solver auto --time-limit 60 --gap 0
python scripts/solver_bench.py spec.json --threads 1 --repeat 3 --out bench.csv

# 算法引擎与出图
python scripts/algotemplates/selftest.py
python scripts/algotemplates/demo_tsp.py
python amap/scripts/build_map.py payload.json -o map.html --no-js

# 仓库级测试
python -m unittest discover -s tests -v
```

`build_map.py` 其他常用参数：`--size 1024x1024`、`--scale 2`、`--pad 0.86`、`--shift-px -80`、`--cache-dir`。

## 设计原则

- **工件优先**：每个环节必须交付可检验工件（公式、可行解、校验器、gap、敏感性曲线）
- **路线先定**：求解器直用 / 自研算法 / 完整研究 / 业务方案四条路线的判据不同，别把「只想解快点」按论文标准做
- **数字唯一来源**：里程与费用照抄定稿 `results/`；口径冲突先查矩阵累加 vs 路径几何，不许让图去凑报表
- **如实写边界**：每个「提升 x%」都要有分母、算例数与前提；超时、失败、负结果都进表

## 已知边界

- 分支定价 / 分支割：仅设计要点，无实现
- 证明：提供模板，不做机器验证
- CP-SAT / OR-Tools：有建模对照表，但不在依赖内
- 遗传算法：完整实现，弱于 ALNS/TS 时属方法特性，选型前先看 `references/算法手册.md`
- 高德出图：只画定稿解，不做实时导航或轨迹回放；需要自备 key 与 payload
- `solver_bench.py`：依赖 Gurobi 许可证，默认固定 1 线程以保证时间可比

## 测试与 CI

`tests/test_repo.py` 有 9 项测试，覆盖目录结构、示例转换、payload 契约、视野计算、凭据零明文、脚本帮助与算法引擎自测；全部离线，不需要 Gurobi 或高德 key。

`.github/workflows/ci.yml` 在 Python 3.10 与 3.13 上跑同一套测试，另加算法引擎的 `selftest.py` 与 `demo_tsp.py`。

## 许可

MIT，见 `LICENSE`。高德返回的底图与坐标数据受其自身服务条款约束，本仓库不含任何凭据或下载的瓦片；示例数据为合成数据——这些第三方说明单独放在 `NOTICE`，以免影响许可证识别。
