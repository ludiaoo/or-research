# 示例数据

本目录的两份 JSON 是**合成样例**，只用于验证脚本链路，不含真实订单、真实坐标或商业合同。

| 文件 | 用途 |
|---|---|
| `sample_solution.json` | `adapt_tddo.py` 的输入，结构与 `export_web.py` 的 `solution_set*.json` 一致（`{depot, customers, nfz, initial, final}`） |
| `sample_payload.json` | `adapt_tddo.py` 的实际输出，可直接作为 `build_map.py` 的输入 |

复现转换：

```bash
python amap/scripts/adapt_tddo.py examples/sample_solution.json -o /tmp/payload.json
```

出图（需要 `AMAP_KEY`，参见 `amap/assets/.env.example`）：

```bash
python amap/scripts/build_map.py examples/sample_payload.json -o /tmp/sample_map.html --no-js
```

样例只有 4 个客户、2 条卡车路线和 1 个无人机架次，坐标在杭州附近，但**是手工构造的示意点**，不对应任何真实门店。架次起降点与卡车停靠点严格重合，因此适配器不会报「端点飘移」警告。
