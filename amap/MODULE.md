# 高德底图的调度线路可视化（or-research 内置模块）

> 模块入口在 `../SKILL.md`。本文件是**详细操作手册**，不再是独立 skill：触发、阶段门与证据纪律由 `../SKILL.md` 统一规定，这里只讲高德侧的具体字段、key、命令与坑。

把「一堆线路编号 + 一串点位」变一张**能摊在会议桌上、每一米都站得住**的地图。

> **首要判据**：如果这张图上的线路走的是"两点之间的直线"，或者里程和求解器报表对不上，那它就只是示意图，不是结果。**图必须能被数据反查。**

---

## 1. 先确认 key 的类型（90% 的失败从这里开始）

高德的 key 分平台，**不通用**：

| 你要做的事 | 需要的 key 类型 | 怎么判断 |
|---|---|---|
| 距离矩阵、POI 搜索、**静态地图**、驾车路径 | **Web 服务** | 浏览器直接打开 `https://restapi.amap.com/v3/staticmap?location=116.39,39.90&zoom=12&size=512*512&key=KEY`，回 PNG 就是对的 |
| 网页里嵌可拖拽的地图（JS API 2.0） | **Web 端 (JS API)** | 页面里 `new AMap.Map()` 能出图且无 `USERKEY_PLAT_NOMATCH` 水印 |
| 小程序 / iOS / Android | 各自的平台类型 | — |

**默认走 Web 服务 key + 静态地图**。原因见第 4 节。

拿不到 key 时不要假装能画——没有 key 就没有真实底图，只能出无底图的平面示意。

---

## 2. 数据契约（payload.json）

```jsonc
{
  "meta": {
    "title": "杭州 · 真实路网 VRP",
    "subtitle": "60 家真实门店 · 6 辆车 · 高德真实路网距离",
    "crs": "GCJ-02",
    "disclaimer": "门店位置与距离来自高德；需求量、时间窗、运价为假设值。"
  },
  "depot": { "name": "仓库名", "lng": 119.973505, "lat": 30.330578 },
  "stores": [
    { "id": "S001", "name": "某某超市", "lng": 119.97, "lat": 30.34, "cold": false }
  ],
  "scenarios": [
    {
      "key": "status_quo",            // 唯一；第一个场景被当作基线，页面上的百分比都相对它算
      "label": "现状",
      "total_km": 232.7, "total_cost": 961.0, "max_min": 369, "covered": 60,
      "routes": [
        {
          "veh": "O1", "kind": "own",        // own | out（外协）
          "color": "#2563eb",                // 有就用自己的，没有就按调色板循环
          "km": 71.4, "n": 13, "load": 620, "cap": 650, "min": 210,
          "is_cold": true,
          "path": [[119.97, 30.33], [119.98, 30.34]]   // 真实路径点，[lng, lat]，至少 2 个
        }
      ]
    }
  ]
}
```

**path 从哪来**：`/v3/direction/driving` 每个 step 的 `polyline`，按 `;` 拆开、`lng,lat` 解析、去掉相邻重复点。
**绝对不要**用门店顺序连直线代替 path——直线会横穿江、穿小区、穿铁轨，看着像那么回事，实际不是车会走的路。

字段缺失的后果：
- 没有 `depot` → 视野少一个点，配送中心不画
- 没有 `path` 或点数 < 2 → 该条线被跳过（不报错，容易以为画了）
- `scenarios[0]` 不是基线 → 所有"相对现状"的百分比都错

### 2.1 可选扩展：无人机-卡车（drone-truck / mFSTSP / TDDO）

同一套模板也画"卡车 + 无人机架次"。**不写 `sorties` 就完全是普通配送图，行为不变。**

```jsonc
{
  "meta": { "default": "final" },          // 打开就落在最终解；不给则落在第一个场景
  "nfz": [{ "lng": 120.1, "lat": 30.3, "r": 800, "label": "禁飞区" }],   // r 单位：米
  "stores": [{ "name": "C1", "lng": 120.1, "lat": 30.3, "color": "#c0392b", "r": 3.2 }],
  "scenarios": [{
    "key": "final", "label": "最终解",
    "headline": "377 → 154 元",            // 大号数字（可选，覆盖默认的里程头条）
    "headline_tail": "-59.1%<br>2 卡车 · 4 架次",
    "metrics": [                            // 有就用它列表；没有则退回配送 VRP 的默认几行
      { "label": "成本", "value": 154, "unit": "元", "better": "down" },
      { "label": "使用卡车", "value": 2, "unit": "辆", "better": "down" },
      { "label": "架次数", "value": 4, "unit": "", "better": "none" },
      { "label": "无人机服务", "value": 13, "unit": "客户", "better": "up" },
      { "label": "完工时间", "value": 3.37, "unit": "h", "better": "down" }
    ],
    "routes": [{
      "veh": "T1", "color": "#1F4E79", "km": 25.5, "n": 5,
      "path": [[120.1, 30.3]],                     // 卡车：真实路网路径
      "stops": [{ "lng": 120.1, "lat": 30.3, "pos": 3, "node": 42 }],
      "sorties": [{
        "customers": 4,                             // 数量即可；传节点数组也认
        "path": [[120.1, 30.3], [120.2, 30.4], [120.15, 30.35]],  // 起飞站→客户→归队站
        "t_launch_min": 49.4, "t_land_min": 87.6,
        "launch_node": 42, "recover_node": 57
      }]
    }]
  }]
}
```

四条硬规则：

- **无人机航段是直线**，不要拿去调驾车路径 API——飞机不走路网，硬套会画出"无人机绕高架"的荒谬图。
- **起飞点 ≠ 归队点**：送货那段时间卡车还在往前开，归队落在后面某一站。所以图上用**空心圆环（起飞）/ 实心方块（归队）**区分，图例要写清。
- 同一条卡车线的**地面实线与空中虚线同色**，否则读者要来回查图例。
- `metrics` 的 `better` 决定百分比颜色：`down` 表示越小越好（成本/时间），`up` 表示越大越好（服务客户数），`none` 不显示变化。

---

### 2.2 上游：这份 payload 从哪来

本模块只管「把已有的解画对」，**不求解、不算距离、不产生任何数字**。payload 有三个来源：

| 来源 | 怎么变成 payload |
|---|---|
| 运筹流程跑完的 results（CSV/JSON） | 按第 2 节的字段自己拼一张；里程与费用**照抄 results**，不要在出图时重算 |
| `solution_set*.json`（`{initial, final}` + 架次） | `scripts/adapt_tddo.py`，见 3.5。**主项目 `scripts/export_web.py` 已切到这条链**：只解算 + 写 solution JSON，渲染全部经本 skill |
| 手工整理的门店表 + 线路顺序 | 先跑第 3 节的 1–3 步拿坐标、矩阵、路径几何，再拼 |

如果上游是本 skill 的环节 4–6：解必须已经过环节 3 的独立校验器、`results/` 已定稿，才能进这里。**图上数字与 CSV 不一致时，先查两套口径（矩阵累加 vs 路径几何同点可差 4%），不许让地图去凑报表。**

反过来也成立：图上出现「线穿河」「架次起降点飘到几公里外」「绕行路段被裁在画布边」，多半是解或索引本身的问题（典型是拿 `nodes` 的位置下标当节点号），要回上游复查，不要只改画法把它盖住。

---

## 3. 标准流程（四步，全部可复跑）

```bash
# 0) 环境
python assets/amap_client.py          # 自检：key 通不通、静态地图能不能出图
# 缺 requests 就装：pip install requests

# 1) 坐标从哪来：有真实门店就用 POI 取（无则跳过）
python - <<'PY'
import sys; sys.path.insert(0, "assets")
import amap_client as amap
c = amap.Client()
pois = amap.pois_around(c, 119.973505, 30.330578, radius=8000, types="060400")
clean = amap.normalize_pois(pois, 119.973505, 30.330578)   # 过滤注册地/重复挂点
pick = amap.stratified_pick(clean, 60, max_km=8.0)         # 分层抽样，避免全挤在仓附近
PY

# 2) 距离矩阵（n 个点只需 n 次请求，不是 n²）
#    amap.distance_matrix(c, pts, cache_path) —— pts[0] 必须是仓
#    缓存会回填矩阵；回填漏掉会得到"看着完美"的全零矩阵

# 3) 驾车路径几何（每段一次请求，带 JSONL 缓存）
#    近邻段复用上一段末点：path_all.extend(path[1:])，否则线会断

# 4) 出图（本 skill 的主入口）
python scripts/build_map.py payload.json -o routes.html
#    --size 1024x1024   --scale 2（高清）   --pad 0.86（留白）   --shift-px -80（避开左面板）
#    --no-js 不写任何前端凭据，出可提交到公开仓库的纯静态页面
```

想额外存一张**带线路**的 PNG（不是内嵌底图）时，复用同一客户端，别在调用方重写 HTTP：

```python
import pathlib, sys
sys.path.insert(0, "assets")
import amap_client as amap
c = amap.Client()
paths = ["6,0x1F4E79,0.9,,0:120.15,30.28;120.16,30.29"]   # 高德 paths 原样串，最多 4 条
png, hit = amap.staticmap(c, (120.15, 30.28), 14, 1024, 1024, scale=2,
                          cache_dir="figs/basemap", paths=paths)
figs_dir = pathlib.Path("figs"); figs_dir.mkdir(exist_ok=True)
(figs_dir / "amap_static.png").write_bytes(png)
```

`staticmap()` 的缓存名含 paths 摘要：先出纯底图、再出带线图不会串缓存。

第 2、3 步的具体函数见 `assets/amap_client.py` 的 `distance_matrix()` 与 `direction()`：前者一次吃 1 目的 + ≤100 起点，n 个点只需 n 次请求；后者每段一次、带 JSONL 缓存，近邻段要 `path_all.extend(path[1:])` 复用上一段末点，否则线会断开。

### 3.5 已有"无人机-卡车"解？一条命令转换

`scripts/adapt_tddo.py` 认识 `export_web.py` 那种 `{initial, final}` 结构（`solution_set*.json`）：

```bash
python scripts/adapt_tddo.py docs/web/solution_set112.json -o tddo_payload.json
python scripts/build_map.py tddo_payload.json -o tddo_map.html            # 含 JS key，本地看
python scripts/build_map.py tddo_payload.json -o tddo_map_public.html --no-js  # 无凭据，可提交
```

适配器做三件事：坐标原样沿用 GCJ-02；校验每个架次的起降点是否落在卡车停靠点上（离最近停靠 > 80 m 就报警——说明又拿 `nodes` 的位置下标当节点号了）；给每条线补里程、给每个场景补 `metrics`。输出直接就是上面 2.1 的 payload。

---

## 4. 两层底图：静态打底，JS 增强

页面里同时存在两层，按可用性自动切换：

| 层 | 何时生效 | 用什么 key | 交互 |
|---|---|---|---|
| **静态底图**（base64 内嵌） | 始终先渲染，秒开 + 兜底 | Web 服务 | 固定视野，缩放靠浏览器 |
| **高德 JS 地图** | 配了 JS key 且 SDK 加载成功 | Web 端 (JS API) + 安全密钥 | 拖拽、滚轮缩放、工具条 |

流程：先出静态图（一定成功）→ SDK 到了无缝切在线地图 → SDK 挂了 / 没配 key / 渲染报错，全部自动退回静态图并提示原因。**任何情况下都必须有图。**

### 两个 key 必须分开，不能混用

| key | 能干什么 | 放哪 | 泄漏后果 |
|---|---|---|---|
| Web 服务 key | 距离矩阵、POI、静态地图、路径 | 只在服务端（`.env` 的 `AMAP_KEY` / 环境变量） | **直接刷掉你的配额**（距离矩阵按次计费） |
| JS API key + `securityJsCode` | 浏览器里画地图 | 页面里（不可避免） | 靠控制台**域名白名单**兜底 |

### 凭据只放 `.env`，代码里零明文

三个变量同存一份 `.env`：`AMAP_KEY`、`AMAP_JS_KEY`、`AMAP_JS_SECRET`。

```bash
cp assets/.env.example .env        # 在项目根或 skill 根，视你在哪跑
# 编辑 .env 填值；.env 必须写进 .gitignore
```

`assets/amap_client.py` 的 `load_env()` 会自动向上找 `.env`（当前目录 → 各级父目录 → skill 根 → 用户主目录），也接受 `AMAP_ENV_FILE=/path/to/.env` 显式指定；**进程环境变量优先级最高**，方便 CI 或临时覆盖。解析器是内置的（约 30 行），不依赖 `python-dotenv`。

查找顺序由远及近，所以项目根的 `.env` 会覆盖主目录的、当前目录的又会覆盖项目根的 —— 就近原则，符合直觉。

**没有 JS key 也能出完整图**，只是不能拖拽。用静态底图时页面不含任何凭据，这是更安全的默认。

底图尺寸：单边 ≤ 1024，`--scale 2` 返回双倍像素（1024×1024 → 2048×2048，文件大 3–4 倍，屏幕上看更锐）。

---

## 5. 对齐：一套公式，两边一字不差

Python 侧 `amap.mercator(lng, lat, z)` 与页面侧 `mx()/my()` 必须**逐字符一致**，否则线会整体偏移几百米（而地图照样好看，最容易被忽略）。

```js
// 标准 Web 墨卡托，256px 瓦片
const N = 256 * Math.pow(2, zoom);
mx = lng => (lng + 180) / 360 * N;
my = lat => { const s = Math.sin(lat*Math.PI/180);
              return (0.5 - Math.log((1+s)/(1-s)) / (4*Math.PI)) * N; };
// 底图中心 (cx, cy) 对应画布中点
x = mx(lng) - cx + W/2;   y = my(lat) - cy + H/2;
```

**GCJ-02 全程不转换**：高德 POI 返回 GCJ-02，距离接口吃 GCJ-02，高德瓦片也是 GCJ-02。中间插一次 WGS-84 转换就白送 100–700 m 偏移。数据本身是 WGS-84（GPS 原始）时才需要转一次，且只转一次。

---

## 6. 页面自带的能力（不用重写）

`assets/map_template.html` 已经是成品，改 payload 即可：

- **方案切换**：`scenarios` 有几个 tab 就有几个，除第一个外都会叠加**现状灰色虚线残影**（白底 + 深灰，防止在彩色道路上看不见）
- **逐条显隐**：线路列表带 checkbox，取消勾选即隐藏；「清空 + 勾一条」= 只看单条；「全选/清空/反选」批量操作；顶部显示「已选 n / m 条 · xx km」
- **架次级显隐**（有 `sorties` 时）：卡车行下面挂架次行，勾掉单条只隐藏那条虚线；勾掉卡车连带它所有架次；计数变成「已选 2 / 2 条 · 3 / 4 架次 · 58.5 km」
- **禁飞区**：`nfz` 画成半透明红圈，半径按米换算成像素（`156543.03·cos(lat)/2^z`）
- **只看一两条时自动加粗**线宽，方便看走向
- **悬停显示店名**（SVG `<title>`，无需 JS 事件）
- **等比 contain 不裁切**：窗口怎么变，底图与线路同步缩放，坐标永不错位
- 底图内嵌，`file://` 双击就能开

改配色只动 `color`；改文案动 `meta`。

---

## 7. 出图前必须过的五关

1. **矩阵无零值**：任何一对点距离为 0 都是回填漏了或请求失败，不是"很近"。
2. **路网/直线比值**：中位应落在 1.15–1.75。接近 1.0 说明拿到了 `type=0` 直线距离（假折扣）；大于 2 说明点的坐标有问题。
3. **线路贴合道路**：随机挑两段，放大看线是否压在道路上；有整体平移就是投影公式不一致。
4. **图与报表同口径**：页面上的总里程/总费用必须等于结果 CSV 的数字，**用矩阵口径算，不要用路径几何累加**——两个接口同点差 4% 很正常。
5. **key 没落进产物**：`grep` 一遍 html 和 payload，出现 key 就说明静态内嵌做错了。

这五条就是 `verify_real.py` 里 A/C/D/E/F 五组在查的东西，新项目照抄即可。

---

## 8. 常见坑

见 `references/pitfalls.md`。最常踩的四个：

- **同一个 key 既当 JS API 又当 Web 服务用** → 前端暴露，配额被刷
- **用 `type=0` 算距离** → 拿到直线距离，优化收益凭空变大
- **视野只按门店算** → 绕行路段/无人机航段被切在画布边缘，看着像线路断了
- **无人机架次的起降点画到几百米外** → 把 `nodes` 的位置下标当成了节点编号（`nodes[s.launch]` 才对）

---

## 9. 交付话术

给结论时必须同时给**真实/假设分界**：

> 门店位置、门店间距离、路线几何为真实数据；需求量、时间窗、车辆台数、运价为假设值。
> 因此「线路形态、里程」可以说，**「省了多少钱」在换进真实派车单之前不要对外说**。

图上的黄色提示框（`meta.disclaimer`）就是干这个的，别删。
