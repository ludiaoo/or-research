#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无人机-卡车解（solution_set*.json）→ or-research/amap 出图 payload

输入是 `scripts/export_web.py` 导出的那份结构：

    { depot:[lon,lat], customers:[[lon,lat]...], nfz:[...],
      initial:{cost, routes:[{truck, nodes, path, stops, sorties}]},
      final:  {...} }

输出是地图模板要的 payload（depot / stores / nfz / scenarios），再由 build_map.py 出图：

    python adapt_tddo.py docs/web/solution_set112.json -o tddo_payload.json
    python build_map.py tddo_payload.json -o tddo_map.html

三条硬规则（都是踩过的）：
  1. 架次端点必须落在卡车**停靠点**上——起飞站/归队站在 nodes 里是位置下标，不是节点号；
  2. 无人机航段不走向量路径（飞机不走路网），保持两点直连；
  3. 颜色按卡车序号固定，同一辆车的地面线与空中虚线同色，减少图例负担。
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib

PALETTE = ["#1F4E79", "#2E7D5A", "#8E5A9E", "#B26A00",
           "#2A6F7F", "#7A4E2D", "#4A5FA5", "#7D6B3F"]


def km_between(a: list[float], b: list[float]) -> float:
    """两点球面距离（米→千米）。只用来报每条线的里程，与选址/路径计算无关。"""
    lon1, lat1, lon2, lat2 = map(float, (a[0], a[1], b[0], b[1]))
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def path_km(path: list[list[float]]) -> float:
    return sum(km_between(a, b) for a, b in zip(path, path[1:]))


def convert_route(r: dict, i: int) -> dict:
    path = [[float(p[0]), float(p[1])] for p in r.get("path", [])]
    stops = [{"lng": float(st["ll"][0]), "lat": float(st["ll"][1]),
              "pos": st.get("pos"), "node": st.get("node")}
             for st in (r.get("stops") or [])]

    sorties = []
    for s in r.get("sorties") or []:
        sp = [[float(p[0]), float(p[1])] for p in s.get("path", [])]
        if len(sp) < 2:
            continue
        item = {"customers": len(s.get("customers") or []), "path": sp,
                "launch_node": s.get("launch_node"), "recover_node": s.get("recover_node")}
        if s.get("t_launch_min") is not None:
            item["t_launch_min"] = s["t_launch_min"]
            item["t_land_min"] = s.get("t_land_min")
        # 守门：起降点离最近停靠点超过 80 m，说明端点又拿位置下标当节点号用了
        for tag, pt in (("起飞", sp[0]), ("归队", sp[-1])):
            near = min((km_between(pt, [st["lng"], st["lat"]]) * 1000 for st in stops),
                       default=0.0)
            if stops and near > 80:
                print(f"  ⚠️  卡车 {r.get('truck')} 的{tag}点离最近停靠 {near:.0f} m，端点可能不对")
        sorties.append(item)

    return {
        "veh": f"T{r.get('truck')}",
        "color": PALETTE[i % len(PALETTE)],
        "kind": "own",
        "n": max(0, len(r.get("nodes") or []) - 2),
        "km": round(path_km(path), 1),
        "path": path,
        "stops": stops,
        "sorties": sorties,
    }


def convert_solution(src: dict) -> dict:
    depot_ll = src["depot"]
    n = int(src.get("n_customers") or max(0, len(src.get("customers") or [])))

    def scenario(key: str, label: str, headline: str, tail: str) -> dict:
        sol = src[key]
        routes = [convert_route(r, i) for i, r in enumerate(sol.get("routes") or [])]
        metrics = [
            {"label": "成本", "value": sol.get("cost"), "unit": "元", "better": "down"},
            {"label": "使用卡车", "value": sol.get("n_trucks_used"), "unit": "辆", "better": "down"},
            {"label": "架次数", "value": sol.get("n_sorties"), "unit": "", "better": "none"},
            {"label": "无人机服务", "value": sol.get("drone_served"), "unit": "客户", "better": "up"},
            {"label": "完工时间", "value": round((sol.get("makespan_s") or 0) / 3600, 2),
             "unit": "h", "better": "down"},
        ]
        return {
            "key": key, "label": label,
            "total_km": round(sum(r["km"] for r in routes), 1),
            "total_cost": sol.get("cost"),
            "covered": n,
            "max_min": round((sol.get("makespan_s") or 0) / 60, 1),
            "headline": headline, "headline_tail": tail,
            "metrics": metrics,
            "routes": routes,
        }

    ini, fin = src["initial"], src["final"]
    base = scenario("initial", "初始解", f"{ini['cost']:,.0f} 元",
                    f"初始解<br>{ini.get('n_trucks_used')} 卡车 · {ini.get('n_sorties')} 架次")
    pct = (fin["cost"] - ini["cost"]) / abs(ini["cost"]) * 100 if ini.get("cost") else 0
    opt = scenario("final", "最终解", f"{ini['cost']:,.0f} → {fin['cost']:,.0f} 元",
                   f"{pct:+.1f}%<br>{fin.get('n_trucks_used')} 卡车 · "
                   f"{fin.get('n_sorties')} 架次 · 无人机送 {fin.get('drone_served')} 单")

    return {
        "meta": {
            "title": f"{src.get('city', '')} · 卡车-无人机协同配送".lstrip(" ·"),
            "subtitle": f"算例 set{src.get('set')} · seed {src.get('seed')} · "
                        f"{n} 客户 · {src.get('n_trucks')} 卡车 · {src.get('iters')} 轮",
            "disclaimer": "卡车路径为真实路网最短路；无人机航段按两点直连示意，"
                          "不含绕行、风场与爬升。需求量与时间窗由算例给定。",
            "crs": "GCJ-02",
            "depot_name": "配送中心（depot）",
            "default": "final",      # 打开就落在最终解；点"初始解"看基线
        },
        "depot": {"lng": float(depot_ll[0]), "lat": float(depot_ll[1]), "name": "配送中心"},
        "stores": [{"name": f"C{i + 1}", "lng": float(p[0]), "lat": float(p[1]),
                    "color": "#c0392b", "r": 3.2}
                   for i, p in enumerate(src.get("customers") or [])],
        "nfz": [{"lng": float(z["center"][0]), "lat": float(z["center"][1]),
                 "r": float(z.get("r") or 0), "label": z.get("label") or "禁飞区"}
                for z in (src.get("nfz") or [])],
        "scenarios": [base, opt],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="无人机-卡车解 → 地图 payload")
    ap.add_argument("solution", help="solution_set*.json（export_web.py 的产物）")
    ap.add_argument("-o", "--out", default=None, help="输出 payload JSON（默认同名 _payload.json）")
    args = ap.parse_args()

    src_path = pathlib.Path(args.solution)
    src = json.loads(src_path.read_text(encoding="utf-8"))
    for k in ("depot", "initial", "final"):
        if k not in src:
            raise SystemExit(f"输入不像无人机-卡车解：缺 {k}（应来自 export_web.py）")

    payload = convert_solution(src)
    out = pathlib.Path(args.out) if args.out else src_path.with_name(src_path.stem + "_payload.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    for sc in payload["scenarios"]:
        ns = sum(len(r["sorties"]) for r in sc["routes"])
        print(f"  {sc['label']}: {len(sc['routes'])} 卡车 · {ns} 架次 · "
              f"{sc['total_km']:.1f} km · {sc['total_cost']:.0f} 元")
    print(f"payload → {out}")


if __name__ == "__main__":
    main()
