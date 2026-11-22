#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""payload.json → 高德底图 → 自包含 HTML 线路图

一条命令出图：
    python build_map.py payload.json -o routes.html

它做四件事：
  1. 把所有坐标（仓/门店/路径点）纳入视野，自动选 zoom —— 只看门店会把绕行路段切在画布边上
  2. 拉一张高德静态地图，base64 内嵌（key 不落页面，断网也能开）
  3. 把底图的 center/zoom 写进 payload，页面用同一套墨卡托公式叠线路
  4. 套模板出单文件 HTML

payload 的最小结构见 ../SKILL.md「数据契约」。
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "assets"))

import amap_client as amap  # noqa: E402


def collect_points(payload: dict) -> list[tuple[float, float]]:
    """视野要包住所有会被画出来的东西：仓、门店、卡车路径、停靠点、无人机航段、禁飞区。"""
    pts: list[tuple[float, float]] = []
    d = payload.get("depot") or {}
    if "lng" in d and "lat" in d:
        pts.append((float(d["lng"]), float(d["lat"])))
    for s in payload.get("stores", []):
        pts.append((float(s["lng"]), float(s["lat"])))
    for sc in payload.get("scenarios", []):
        for r in sc.get("routes", []):
            for p in r.get("path", []):
                pts.append((float(p[0]), float(p[1])))
            for st in r.get("stops", []) or []:
                pts.append((float(st["lng"]), float(st["lat"])))
            for s in r.get("sorties", []) or []:
                for p in s.get("path", []):
                    pts.append((float(p[0]), float(p[1])))
    # 禁飞区按半径外扩：只看圆心会把圆圈切掉一半
    for z in payload.get("nfz", []) or []:
        lng, lat = float(z["lng"]), float(z["lat"])
        r = float(z.get("r", 0) or 0)
        dlat = r / 111_320.0
        dlng = r / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
        pts.extend([(lng - dlng, lat - dlat), (lng + dlng, lat + dlat)])
    if not pts:
        raise SystemExit("payload 里没有任何坐标：depot / stores[].lng,lat / routes[].path 至少要有一处")
    return pts


def main() -> None:
    ap = argparse.ArgumentParser(description="用高德静态底图给调度结果出线路图")
    ap.add_argument("payload", help="不含 basemap 的 payload JSON")
    ap.add_argument("-o", "--out", default=None, help="输出 HTML（默认 <payload所在目录>/map_<名字>.html）")
    ap.add_argument("--template", default=None, help="模板路径（默认 assets/map_template.html）")
    ap.add_argument("--size", default="1024x1024", help="底图逻辑尺寸，单边 ≤1024，例 1024x1024")
    ap.add_argument("--scale", type=int, default=1, choices=[1, 2], help="2 = 高清（返回双倍像素）")
    ap.add_argument("--pad", type=float, default=0.86, help="内容最多占画布比例，越小留白越多")
    ap.add_argument("--shift-px", type=float, default=-80.0,
                    help="视野中心横向位移（负=内容右移，给左侧浮层让位）")
    ap.add_argument("--cache-dir", default=None, help="底图缓存目录（默认 payload 同目录 basemap/）")
    ap.add_argument("--no-js", action="store_true",
                    help="不注入 JS key/安全密钥，出纯静态页面（可提交到公开仓库）")
    args = ap.parse_args()

    src = pathlib.Path(args.payload)
    payload = json.loads(src.read_text(encoding="utf-8"))
    if "basemap" in payload:
        print("payload 里已有 basemap，本次会覆盖")

    try:
        W, H = (int(v) for v in args.size.lower().split("x"))
    except Exception:
        raise SystemExit(f"--size 写错了：{args.size}，应该是 1024x1024 这种")
    if max(W, H) > 1024:
        raise SystemExit("高德静态地图单边上限 1024；想要更清晰把 --scale 设成 2")

    pts = collect_points(payload)
    zoom = amap.choose_zoom(pts, W * args.scale, H * args.scale, pad=args.pad)
    lngs = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    center0 = ((min(lngs) + max(lngs)) / 2.0, (min(lats) + max(lats)) / 2.0)
    center = amap.shift_center(center0, zoom, args.shift_px)

    cache = pathlib.Path(args.cache_dir) if args.cache_dir else src.parent / "basemap"
    c = amap.Client()
    png, hit = amap.staticmap(c, center, zoom, W, H, args.scale, cache_dir=cache)

    page = dict(payload)
    page["basemap"] = {
        "src": "data:image/png;base64," + base64.b64encode(png).decode(),
        "w": W * args.scale,
        "h": H * args.scale,
        "center": [center[0], center[1]],
        "zoom": zoom,
        "attribution": "高德地图",
    }

    tpl_path = pathlib.Path(args.template) if args.template else HERE.parent / "assets" / "map_template.html"
    tpl = tpl_path.read_text(encoding="utf-8")
    if "__PAYLOAD__" not in tpl:
        raise SystemExit(f"模板里没有 __PAYLOAD__ 占位：{tpl_path}")

    # JS API 凭据是可选的：配了页面就能拖拽缩放，没配就只用静态底图（省事且不暴露任何前端凭据）
    # --no-js 用于出「可提交到仓库」的页面：连 JS key 都不写进去，公开 clone 也不会泄露。
    js_key, js_code = ("", "") if args.no_js else amap.load_js_keys()
    html = (tpl.replace("__PAYLOAD__", json.dumps(page, ensure_ascii=False))
               .replace("__AMAP_JSKEY__", js_key)
               .replace("__AMAP_JSCODE__", js_code))

    out = pathlib.Path(args.out) if args.out else src.parent / f"map_{src.stem}.html"
    out.write_text(html, encoding="utf-8")

    print(f"视野 z={zoom} · 底图 {W*args.scale}x{H*args.scale} · {len(png)/1024:.0f} KB"
          f"{'（缓存）' if hit else ''} · 请求 {c.calls} 次")
    print(f"在线层 {'高德 JS API（可拖拽缩放）' if js_key else '未配 JS key / --no-js，仅静态底图'}")
    print(f"页面 → {out}  ({len(html)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
