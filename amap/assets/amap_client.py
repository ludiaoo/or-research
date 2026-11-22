#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高德 Web 服务客户端：真实 POI + 真实路网距离矩阵

数据真假分界（任何下游文档都必须照抄这两行）：
  · 真实 —— 门店与仓库的地理位置、门店间实际驾车距离与耗时（高德路网）
  · 假设 —— 需求量、收货时间窗、车辆台数与载重、运价（这些只存在于企业系统里）

三条口径都是**实测**出来的，不是照抄文档：
  1. 坐标系全链路 GCJ-02：POI 返回 GCJ-02、距离接口吃 GCJ-02、地图展示用 GCJ-02。
     一次转换都不做——少一次转换就少一类几百米的偏移错误。
  2. `type=1` 才是驾车路网距离。实测同一对点：type=0 给 808 m 且 duration=0（直线），
     type=1 给 1527 m / 453 s。用 type=0 等于花钱买了个假折扣。
  3. `type=1` 的 duration 含**当时路况**。连续三次实测一致，但不保证跨时段稳定，
     所以采集完立刻落盘快照（sha256 + 时间窗），之后所有实验只读缓存。

配额：`/v3/distance` 一次 1 个 destination + 最多 100 个 origins。
n 个点的全量有向矩阵 = n 次请求（不是 n²）。61 点 = 61 次 ≈ 30 s。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.parse
from datetime import datetime

import requests

ROOT = pathlib.Path(__file__).resolve().parent
AMAP_ROOT = ROOT.parent                 # or-research/amap：可视化模块根目录
SKILL_ROOT = AMAP_ROOT.parent           # or-research：合并后的单一 skill 根目录
HOST = "https://restapi.amap.com"
QPS_PAUSE = 0.34                 # 个人 key 上限约 3 QPS，留一点余量
ORIGIN_LIMIT = 100               # /v3/distance 的 origins 上限


# ---------------------------------------------------------------- key 与请求

ENV_VARS = ("AMAP_KEY", "AMAP_JS_KEY", "AMAP_JS_SECRET")


def _parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """极简 .env 解析：`KEY=VALUE`、`#` 注释、可带引号、可写 `export` 前缀。

    不依赖 python-dotenv：少一个安装步骤，行为可预期（不做变量插值）。
    """
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, _, val = line.partition("=")
        name, val = name.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if name:
            out[name] = val
    return out


def _env_candidates() -> list[pathlib.Path]:
    """`.env` 搜索顺序（由远及近，近的覆盖远的）。

    主目录 → skill 根 → 当前目录逐级向上（8 级）→ `AMAP_ENV_FILE`。
    上溯 8 级是为了够到项目根的 `.env`：调用方常在 `项目/code/` 或更深的 demo
    目录里跑脚本，4 级只到中间层，读不到项目根的凭据。
    """
    here = pathlib.Path.cwd().resolve()
    out = [pathlib.Path.home() / ".env",
           SKILL_ROOT / ".env", SKILL_ROOT.parent / ".env"]
    for d in reversed([here, *here.parents[:8]]):
        out.append(d / ".env")
    override = os.environ.get("AMAP_ENV_FILE", "").strip()
    if override:
        p = pathlib.Path(override).expanduser()
        out.append(p if p.is_file() or p.suffix else p / ".env")
    return out


def load_env(reload: bool = False) -> dict[str, str]:
    """合并 `.env` 与进程环境变量，**环境变量优先**（便于临时覆盖、CI 注入）。"""
    if not reload and hasattr(load_env, "_cache"):
        return load_env._cache
    vals: dict[str, str] = {}
    seen: set[str] = set()
    for f in _env_candidates():
        try:
            if not f.is_file():
                continue
            tag = str(f.resolve())
        except OSError:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        # 空值不覆盖已读到的非空值：近处若只留了一个 `AMAP_KEY=` 的空壳副本，
        # 会把远处项目根的有效 key 顶成空串，症状是「明明配了 key 却报缺 key」。
        for name, v in _parse_env_file(f).items():
            if v or name not in vals:
                vals[name] = v
    for name in ENV_VARS:
        v = os.environ.get(name, "").strip()
        if v:
            vals[name] = v
    load_env._cache = vals
    return vals


def load_key() -> str:
    """读高德 Web 服务 key：`.env` → 环境变量 → 旧版 `.amap_key` 单文件。

    key 绝不写进源码：它能直接扣配额，等同密码。仓库里只提交 `.env.example`。
    """
    k = load_env().get("AMAP_KEY", "").strip()
    if k:
        return k
    for base in (pathlib.Path.cwd(), SKILL_ROOT, pathlib.Path.home()):
        f = base / ".amap_key"
        if f.is_file():
            k = f.read_text(encoding="utf-8-sig").strip()
            if k:
                return k
    raise SystemExit(
        "缺高德 Web 服务 key。\n"
        f"  1) 在 {pathlib.Path.cwd() / '.env'} 写一行：AMAP_KEY=你的key\n"
        "     （模板见 skill 内 assets/.env.example；.env 必须 gitignore）\n"
        "  2) 或临时用环境变量：set AMAP_KEY=你的key（bash: export AMAP_KEY=...）\n"
        "  申请：https://console.amap.com/dev/key/app → 新建 key → 勾「Web服务」\n"
        "  用途：距离矩阵、POI 搜索、静态地图、驾车路径。"
    )


def load_js_keys() -> tuple[str, str]:
    """读 JS API 的 key 与安全密钥（securityJsCode）。没有就返回 ("", "")。

    为什么 JS key 允许进页面：它是**浏览器端凭据**，必须公开才能用，安全性靠控制台的
    域名白名单。而业务 key（距离矩阵/静态地图）能直接扣配额，绝不能进页面 —— 两者
    必须分开申请、分开存放（`.env` 里就是两个不同变量）。
    """
    vals = load_env()
    k = vals.get("AMAP_JS_KEY", "").strip()
    j = vals.get("AMAP_JS_SECRET", "").strip()
    if k and j:
        return k, j

    def rd(name: str) -> str:
        for base in (pathlib.Path.cwd(), SKILL_ROOT):
            f = base / name
            if f.is_file():
                v = f.read_text(encoding="utf-8-sig").strip()
                if v:
                    return v
        return ""

    return (k or rd(".amap_js_key")), (j or rd(".amap_jscode"))


class Client:
    """带重试、限速、请求计数的极简客户端。

    不走系统代理：高德是国内域名，Clash 之类的全局代理会让它绕一圈，
    反而容易超时。`trust_env = False` 把环境变量里的代理关掉。
    """

    def __init__(self, key: str | None = None, pause: float = QPS_PAUSE):
        self.key = key or load_key()
        self.pause = pause
        self.calls = 0
        self.retries = 0
        self._last = 0.0
        self.s = requests.Session()
        self.s.trust_env = False
        self.t_start = datetime.now()

    def _throttle(self) -> None:
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def get(self, path: str, params: dict, tries: int = 4) -> dict:
        params = {"key": self.key, **params}
        url = HOST + path
        last_err = ""
        for i in range(tries):
            self._throttle()
            self.calls += 1
            try:
                r = self.s.get(url, params=params, timeout=12)
                d = r.json()
            except Exception as e:                       # 网络层：重试
                last_err = f"{type(e).__name__}: {e}"
                self.retries += 1
                time.sleep(2 ** i)
                continue
            code = str(d.get("infocode", ""))
            if str(d.get("status")) == "1":
                return d
            last_err = f"status={d.get('status')} info={d.get('info')} infocode={code}"
            # 10001 key 错 / 10009 权限不足 / 10003 日配额尽 —— 重试没意义，直接失败
            if code in ("10001", "10009", "10003", "10010", "10012"):
                raise SystemExit(f"高德拒绝请求（不可重试）：{last_err}\n"
                                 f"  10001 key 无效 / 10009 未开通该服务 / 10003 日配额用尽")
            self.retries += 1
            time.sleep(1.5 * (i + 1))                    # 10004 QPS 超限这类，退避后重试
        raise RuntimeError(f"高德请求连续失败 {tries} 次：{path} {last_err}")

    def get_raw(self, path: str, params: dict, tries: int = 3) -> bytes:
        """二进制接口（静态地图）。复用同一套限速与计数，但拿 bytes 不解析 JSON。"""
        params = {"key": self.key, **params}
        url = HOST + path
        last_err = ""
        for i in range(tries):
            self._throttle()
            self.calls += 1
            try:
                r = self.s.get(url, params=params, timeout=25)
                ctype = r.headers.get("content-type", "")
                if r.status_code == 200 and ctype.startswith("image/"):
                    return r.content
                last_err = f"http={r.status_code} type={ctype} body={r.text[:120]}"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
            self.retries += 1
            time.sleep(1.5 * (i + 1))
        raise RuntimeError(f"静态地图请求失败：{last_err}\n"
                           f"  size 上限 1024*1024；zoom 1–17；key 需开通「静态地图」服务")



# ---------------------------------------------------------------- POI

def pois_around(c: Client, lng: float, lat: float, radius: int = 8000,
                types: str = "060400", max_pages: int = 5) -> list[dict]:
    """周边搜索。`types=060400` 是「超级市场」分类码，比关键词「超市」干净——
    关键词会召回「xx超市有限公司」这种注册地，那不是配送点。
    """
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        d = c.get("/v3/place/around", {
            "location": f"{lng:.6f},{lat:.6f}", "radius": radius,
            "types": types, "offset": 25, "page": page, "extensions": "base",
        })
        batch = d.get("pois", [])
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 25 or len(out) >= int(d.get("count", 0) or 0):
            break
    return out


def normalize_pois(pois: list[dict], depot_lng: float, depot_lat: float) -> list[dict]:
    """过滤 + 去重 + 算到仓距离。

    过滤规则（每条都对应一种会污染 VRP 实例的脏数据）：
      · 名称含「有限公司 / 分公司 / 办事处」→ 注册地，不是门店
      · 坐标缺失或为 []                        → 无法路由
      · 与仓距离 < 150 m                       → 多半是仓本身或隔壁
      · 相邻两个 POI 相距 < 40 m               → 同一栋楼的重复挂点
    """
    import math

    def hav(a, b):
        R = 6371.0088
        p1, p2 = math.radians(a[1]), math.radians(b[1])
        dp, dl = p2 - p1, math.radians(b[0] - a[0])
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * R * math.asin(math.sqrt(h))

    depot = (depot_lng, depot_lat)
    out: list[dict] = []
    for p in pois:
        name = (p.get("name") or "").strip()
        loc = p.get("location")
        if not name or not loc or loc == "[]":
            continue
        if any(w in name for w in ("有限公司", "分公司", "办事处", "筹备处")):
            continue
        try:
            lng, lat = (float(v) for v in loc.split(","))
        except Exception:
            continue
        if not (118.0 < lng < 122.0 and 29.0 < lat < 31.5):   # 杭州兜底范围
            continue
        d = hav(depot, (lng, lat))
        if d < 0.15:
            continue
        if any(hav((lng, lat), (o["lng"], o["lat"])) < 0.040 for o in out):
            continue
        out.append({"name": name, "lng": round(lng, 6), "lat": round(lat, 6),
                    "km_depot": round(d, 3), "address": str(p.get("address", ""))[:60],
                    "typecode": str(p.get("typecode", ""))})
    return out


def stratified_pick(pois: list[dict], n: int, max_km: float) -> list[dict]:
    """按到仓距离**分层**抽取，而不是取前 n 个。

    周边搜索默认按距离升序返回，直接取前 n 家会得到一个半径 2 km 的圆盘——
    那样的配送区在现实中不存在，也测不出路由优化的价值。
    做法：把 [0, max_km] 均分 n//4 段，每段取离仓最近的那家，不足则回填。
    """
    pool = sorted([p for p in pois if p["km_depot"] <= max_km], key=lambda p: p["km_depot"])
    if len(pool) < n:
        raise SystemExit(f"半径内只有 {len(pool)} 家门店，不够 {n} 家。放大 radius 或减 n。")
    segs = max(1, n // 4)
    width = max_km / segs
    picked, used = [], set()
    for k in range(segs):
        lo, hi = k * width, (k + 1) * width
        band = [i for i, p in enumerate(pool) if lo <= p["km_depot"] < hi and i not in used]
        take = n // segs
        for i in (band[:take] if len(band) >= take else band):
            picked.append(pool[i])
            used.add(i)
    for i, p in enumerate(pool):                      # 不足部分按距离顺序回填
        if len(picked) >= n:
            break
        if i not in used:
            picked.append(p)
            used.add(i)
    picked = picked[:n]
    picked.sort(key=lambda p: p["km_depot"])
    return picked


# ---------------------------------------------------------------- 距离矩阵

def distance_matrix(c: Client, pts: list[dict], cache_path: pathlib.Path,
                    refresh: bool = False) -> tuple[list[list[float]], list[list[float]]]:
    """全量有向矩阵。pts[0] 必须是仓。

    每个点当一次 destination，带全部 origins（n ≤ 100 时一次拿完）。
    缓存按方向存：A→B 与 B→A 是两条不同的路，不能靠对称性省请求。
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache: dict[str, tuple[float, float]] = {}
    if cache_path.exists() and not refresh:
        for ln in cache_path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                cache[r["k"]] = (r["m"], r["s"])
    if refresh and cache_path.exists():
        bak = cache_path.with_suffix(f".jsonl.bak-{datetime.now():%Y%m%d%H%M%S}")
        cache_path.replace(bak)

    def key(a: int, b: int) -> str:
        return f"{pts[a]['lng']},{pts[a]['lat']}|{pts[b]['lng']},{pts[b]['lat']}"

    n = len(pts)
    D = [[0.0] * n for _ in range(n)]
    T = [[0.0] * n for _ in range(n)]
    todo = [(a, b) for a in range(n) for b in range(n) if a != b]
    hits = sum(1 for a, b in todo if key(a, b) in cache)
    print(f"   点对 {len(todo):,}  ·  缓存命中 {hits:,}  ·  待请求 {len(todo) - hits:,}")

    # 先把缓存回填进矩阵。漏了这一步，第二次运行会得到全零矩阵，
    # 而全零矩阵的「不对称 0 对 / 三角违反 0 处」看着像完美数据 —— 最危险的那种假象。
    for a, b in todo:
        rec = cache.get(key(a, b))
        if rec is not None:
            D[a][b] = rec[0]
            T[a][b] = rec[1] / 60.0

    with cache_path.open("a", encoding="utf-8") as fh:
        for b in range(n):
            dest = pts[b]
            miss = [a for a in range(n) if a != b and key(a, b) not in cache]
            for start in range(0, len(miss), ORIGIN_LIMIT):
                chunk = miss[start:start + ORIGIN_LIMIT]
                origins = "|".join(f"{pts[a]['lng']},{pts[a]['lat']}" for a in chunk)
                d = c.get("/v3/distance", {
                    "origins": origins,
                    "destination": f"{dest['lng']},{dest['lat']}",
                    "type": 1,
                })
                got = 0
                for r in d.get("results", []):
                    if r.get("distance") in (None, "", "[]"):
                        continue
                    i = int(r["origin_id"]) - 1
                    if not (0 <= i < len(chunk)):
                        continue
                    a = chunk[i]
                    km = float(r["distance"]) / 1000.0
                    mn = float(r.get("duration") or 0) / 60.0
                    D[a][b], T[a][b] = km, mn
                    rec = {"k": key(a, b), "m": round(km, 4), "s": round(mn * 60, 1)}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    got += 1
                missing = len(chunk) - got
                if missing:
                    print(f"   ⚠️ 目的点 {b} 有 {missing} 个起点没返回距离，"
                          f"该方向留 0（下游校验器会把它当日程 0，须人工确认）")
            fh.flush()

    # 硬断言：任何一对点的距离为 0 都说明回填或请求有漏，不许带着零矩阵往下走
    zeros = [(a, b) for a, b in todo if D[a][b] <= 0]
    if zeros:
        raise SystemExit(
            f"矩阵不完整：{len(zeros)} 个点对距离为 0（例 {zeros[0]}）。\n"
            f"  常见原因：缓存未回填、某次请求返回 distance 为空、起点坐标重复。")
    return D, T


def manifest(pts: list[dict], D, T, cache_path: pathlib.Path, c: Client,
             extra: dict | None = None) -> dict:
    """快照清单。`matrix_sha256` 算在**矩阵数值**上，不是算在文件字节上——
    这样换行符、列顺序变了也不影响它是否代表同一份数据；同时另存缓存 sha256 供追溯。"""
    h = hashlib.sha256()
    for row in D:
        h.update(",".join(f"{v:.4f}" for v in row).encode())
    cache_h = hashlib.sha256(cache_path.read_bytes()).hexdigest() if cache_path.exists() else ""
    off = [(i, j) for i in range(len(D)) for j in range(len(D)) if i != j]
    asym = sum(1 for i, j in off if D[i][j] > 0 and D[j][i] > 0
               and abs(D[i][j] - D[j][i]) / max(D[i][j], D[j][i]) > 0.02)
    viol = sum(1 for i, j, k in ((i, j, k) for i in range(len(D)) for j in range(len(D))
                                 for k in range(len(D)))
               if i != j != k != i and D[i][j] > 0 and D[i][k] > 0 and D[j][k] > 0
               and D[i][j] > D[i][k] + D[k][j] + 1e-6)
    out = {
        "provider": "amap", "api": "/v3/distance", "type": 1,
        "type_meaning": "驾车导航距离（含当时路况）；type=0 是直线距离，不适用",
        "crs": "GCJ-02（高德坐标，与 POI、地图展示同源，全程无转换）",
        "points": len(pts), "directed_pairs": len(off),
        "requests": c.calls, "retries": c.retries,
        "fetched_at": f"{c.t_start:%Y-%m-%d %H:%M:%S} → {datetime.now():%Y-%m-%d %H:%M:%S}",
        "matrix_sha256": h.hexdigest(), "cache_sha256": cache_h,
        "asym_pairs_over_2pct": asym,
        "triangle_violations": viol,
        "depot": pts[0]["name"],
    }
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------- 静态底图（Web 墨卡托）

def mercator(lng: float, lat: float, z: int) -> tuple[float, float]:
    """GCJ-02 经纬度 → z 级下的世界像素坐标（256px 瓦片，标准 Web 墨卡托）。

    高德的瓦片与静态地图都用这套投影，且点位本身就是 GCJ-02，
    所以这里**不做任何坐标转换**：进 GCJ-02、出像素，中间不碰 WGS-84。
    页面端的 JS 用同一公式反算，两边必须一字不差 —— 差一点，线就压不到路上。
    """
    import math

    n = 256 * (2 ** z)
    x = (lng + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def choose_zoom(pts: list[tuple[float, float]], w: int, h: int, pad: float = 0.90) -> int:
    """能把这些点全装进 w×h 画布（留 pad 余量）的最大 zoom。越大越清晰。"""
    lngs = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    for z in range(17, 7, -1):
        x0, y0 = mercator(min(lngs), max(lats), z)
        x1, y1 = mercator(max(lngs), min(lats), z)
        if (x1 - x0) <= w * pad and (y1 - y0) <= h * pad:
            return z
    return 8


def shift_center(center: tuple[float, float], zoom: int,
                 dx_px: float, dy_px: float = 0.0) -> tuple[float, float]:
    """把视野中心按像素平移后反算经纬度。

    dx_px 为负 = 中心西移 = 地图内容相对东移（用来给左侧浮层让位）。
    dy_px 为正 = 中心南移 = 内容相对北移。
    """
    import math

    n = 256 * (2 ** zoom)
    x, y = mercator(center[0], center[1], zoom)
    x, y = x + dx_px, y + dy_px
    lng = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return round(lng, 6), round(lat, 6)


def staticmap(c: Client, center: tuple[float, float], zoom: int, w: int, h: int,
              scale: int = 1, cache_dir: pathlib.Path | None = None,
              paths: list[str] | None = None) -> tuple[bytes, bool]:
    """拉一张静态地图。w/h 是逻辑尺寸，scale=2 时返回 2w×2h 像素。

    按参数名缓存：同一视野重复出图不再扣配额。页面里用 base64 内嵌，
    好处是底图不依赖 JS SDK、不需要把 key 交给浏览器、断网也能打开。

    `paths` 是高德 `paths` 参数的原样字符串列表（如 `6,0x1F4E79,0.9,,0:lon,lat;...`）。
    不传就是纯底图；传了就出带线路的 PNG。缓存名把 paths 一起 hash，
    否则「先出底图、再出带线图」会命中同一份缓存，拿到没有线的图。
    一次最多 4 条 path（高德限制），调用方自己截断。
    """
    extra = "|".join(paths or [])
    tag = f"sm_{center[0]:.6f}_{center[1]:.6f}_z{zoom}_{w}x{h}_s{scale}"
    if extra:
        tag += "_p" + hashlib.sha1(extra.encode()).hexdigest()[:10]
    tag += ".png"
    if cache_dir is not None:
        f = pathlib.Path(cache_dir) / tag
        if f.exists() and f.stat().st_size > 1000:
            return f.read_bytes(), True
    params = {
        "location": f"{center[0]:.6f},{center[1]:.6f}",
        "zoom": str(zoom), "size": f"{w}*{h}", "scale": str(scale),
    }
    if extra:
        params["paths"] = "|".join(paths)
    png = c.get_raw("/v3/staticmap", params)
    if cache_dir is not None:
        pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)
        f.write_bytes(png)
    return png, False


if __name__ == "__main__":
    # 自检：只验证 key 通不通、静态地图能不能出图，不消耗业务配额
    c = Client()
    print(f"key 长度 {len(c.key)}  前4位 {c.key[:4]}****")
    png, hit = staticmap(c, (116.397428, 39.909230), 12, 512, 512)
    print(f"静态地图 OK：{len(png)/1024:.0f} KB {'（缓存）' if hit else ''}")
    jk, jc = load_js_keys()
    print(f"JS key：{'已配置（页面可用可拖拽地图）' if jk else '未配置（只能用静态底图）'}")
    print(f"请求 {c.calls} 次 / 重试 {c.retries} 次")
