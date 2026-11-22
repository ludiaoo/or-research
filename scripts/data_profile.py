#!/usr/bin/env python3
"""数据画像：把「数据脏不脏」变成一张可核对的表。

纯标准库，不依赖 pandas。输出 markdown，可直接粘进 00_问题界定.md 的
「§0.7 数据底账」。

它只报**事实**，不做判断：
  - 每列的类型、缺失率、唯一值、分位数、IQR 外点数
  - 整行重复、主键重复
  - 时间列的跨度、粒度、间隔异常
  - 用户给定规则的违规行数与样例

用法：
    python data_profile.py data/raw/orders.csv --out data/raw/_profile.md
    python data_profile.py orders.csv --key order_id --time created_at \\
        --rule "promised_at >= created_at" --rule "weight_kg > 0"

退出码：正常 0；文件读不了 2；某条规则解析失败 3（其余规则仍执行）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1")

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y%m%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
)

MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "未知", "空"}


# ---------------------------------------------------------------- 读取

def read_table(path: Path, sep: str | None, max_rows: int | None):
    """返回 (header, rows, encoding, delimiter)。编码与分隔符都做兜底。"""
    raw = path.read_bytes()
    text = None
    used = None
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SystemExit(f"无法解码文件：{path}")

    if sep is None:
        sample = text[:8192]
        try:
            sep = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            sep = ","

    reader = csv.reader(io.StringIO(text), delimiter=sep)
    try:
        header = next(reader)
    except StopIteration:
        raise SystemExit(f"空文件：{path}")
    header = [h.strip() or f"col{i}" for i, h in enumerate(header)]

    rows = []
    for i, r in enumerate(reader):
        if max_rows is not None and i >= max_rows:
            break
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        rows.append(r[: len(header)])
    return header, rows, used, sep


# ---------------------------------------------------------------- 类型推断

def is_missing(v: str) -> bool:
    return v.strip().lower() in MISSING_TOKENS


def parse_number(v: str):
    s = v.strip().replace(",", "").replace(" ", "")
    if s.startswith("¥") or s.startswith("$"):
        s = s[1:]
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        f = float(s)
    except ValueError:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def numeric_prefix(v: str):
    """从 `240cm` / `3 支` / `¥18` 这类值里取数字部分；取不到返回 None。

    只用于诊断「单位混用」：值本身不可解析，但数字部分能与本列其他值对上，
    说明不是脏值而是口径不一致。
    """
    m = re.match(r"^[\s¥$]*([-+]?\d+(?:\.\d+)?)", str(v))
    if not m:
        return None
    try:
        f = float(m.group(1))
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def parse_date(v: str):
    s = v.strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # ISO 带时区：2024-01-02T03:04:05+08:00
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def infer_type(values: list[str]):
    """在一列的非缺失值上判定：int / float / date / str。

    阈值取 0.8 而不是 1.0：真实数据常有 5%~15% 的脏值，若要求全列干净，
    整列会被判成文本，后续规则检查全部跳过——等于把问题藏起来。
    脏值本身在 profile() 里单独计数并报警。
    """
    vals = [v for v in values if not is_missing(v)]
    if not vals:
        return "empty", None
    sample = vals[:2000]
    n_int = sum(1 for v in sample if re.fullmatch(r"[-+]?\d+", v.strip()))
    if n_int == len(sample):
        return "int", [float(v) for v in sample]
    nums = [parse_number(v) for v in sample]
    if sum(1 for x in nums if x is not None) >= 0.8 * len(sample):
        return "float", [x for x in nums if x is not None]
    dates = [parse_date(v) for v in sample]
    if sum(1 for d in dates if d is not None) >= 0.8 * len(sample):
        return "date", [d for d in dates if d is not None]
    return "str", None


def quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


# ---------------------------------------------------------------- 规则

RULE_RE = re.compile(r"^\s*([^\s<>=!]+)\s*(<=|>=|==|!=|<|>)\s*(.+?)\s*$")
OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def check_rule(rule: str, header: list[str], rows: list[list[str]]):
    """单条规则的违规检查。返回 (文本, 可检查行数, 违规数, 样例列表) 或抛 ValueError。

    数值与日期都支持：左列整体判定为日期时按日期比，否则按数值比。
    时间先后（promised_at >= created_at）是最常用的一条，必须能查。
    """
    m = RULE_RE.match(rule)
    if not m:
        raise ValueError(f"规则写法不对：{rule}（应形如 `a >= b` 或 `weight_kg > 0`）")
    left, op, right = m.group(1), m.group(2), m.group(3)
    if left not in header:
        raise ValueError(f"规则左侧列不存在：{left}")
    li = header.index(left)

    right_col = right if right in header else None
    ri = header.index(right_col) if right_col else None

    ltyp, _ = infer_type([r[li] for r in rows])
    use_date = ltyp == "date"
    parser = parse_date if use_date else parse_number
    const_val = None if right_col else parser(right)

    bad = []
    checked = 0
    for r in rows:
        lv = parser(r[li])
        if lv is None:
            continue
        if right_col:
            rv = parser(r[ri])
            if rv is None:
                continue
        elif const_val is not None:
            rv = const_val
        else:
            # 常数既非数值也非日期：只支持 == / !=
            lv_s = r[li].strip()
            if op in ("==", "!="):
                checked += 1
                ok = (lv_s == right) if op == "==" else (lv_s != right)
                if not ok:
                    bad.append(lv_s)
            continue
        checked += 1
        if not OPS[op](lv, rv):
            bad.append(f"{r[li]} ⊥ {r[ri] if right_col else right}")

    desc = f"`{left} {op} {right}`"
    return desc, checked, len(bad), bad[:3]


# ---------------------------------------------------------------- 报告

def fmt(x: float, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if abs(x) >= 1e6 or (0 < abs(x) < 1e-3):
        return f"{x:.3e}"
    return f"{x:.{nd}f}".rstrip("0").rstrip(".")


def explain_missing(header: list[str], rows: list[list[str]], targets: list[str],
                    max_groups: int = 12):
    """找「另一列能完全解释某列缺失」的情形。

    OR 数据里最常见的结构性缺失不是「随机没填」，而是「这个字段只对某类行适用」：
    交期只对加急单填、成本只对自产件填、到货时间只对外购件填。
    这种列一旦当全量字段用（算平均值、算覆盖率、算准时率），结论就是错的。

    判据：按候选列取值分组后，目标列的缺失在每组内**完全一致**（整组全空或整组全有），
    且同时存在全空组与全有组。返回 {目标列: (解释列, 全空组, 全空行数, 全有组, 全有行数)}。
    """
    n = len(rows)
    if n == 0:
        return {}
    out = {}
    for t in targets:
        ti = header.index(t)
        best = None
        for c in header:
            if c == t:
                continue
            ci = header.index(c)
            groups: dict[str, list[int]] = {}
            for r in rows:
                g = r[ci].strip()
                if is_missing(g):
                    continue
                cell = groups.setdefault(g, [0, 0])
                if is_missing(r[ti]):
                    cell[0] += 1
                else:
                    cell[1] += 1
            if not (2 <= len(groups) <= max_groups):
                continue
            empty = [g for g, (m, p) in groups.items() if p == 0 and m > 0]
            full = [g for g, (m, p) in groups.items() if m == 0 and p > 0]
            if empty and full and len(empty) + len(full) == len(groups):
                cand = (len(groups), c, empty, sum(groups[g][0] for g in empty),
                        full, sum(groups[g][1] for g in full))
                if best is None or cand[0] < best[0]:
                    best = cand
        if best:
            _, c, empty, n_empty, full, n_full = best
            out[t] = (c, empty, n_empty, full, n_full)
    return out


def profile(path: Path, header, rows, enc, sep, keys, timecol, rules) -> tuple[str, int]:
    n = len(rows)
    lines: list[str] = []
    warn: list[str] = []
    rule_fail = 0

    sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    lines.append(f"# 数据画像 · `{path.name}`\n")
    lines.append("| 项 | 值 |\n|---|---|")
    lines.append(f"| 文件 | `{path}` |")
    lines.append(f"| 大小 | {path.stat().st_size / 1024:.1f} KB |")
    lines.append(f"| sha256（前16位） | `{sha}` |")
    lines.append(f"| 编码 / 分隔符 | {enc} / `{sep}` |")
    lines.append(f"| 行 × 列 | {n} × {len(header)} |")

    # 整行重复
    seen: dict[tuple, int] = {}
    for r in rows:
        t = tuple(x.strip() for x in r)
        seen[t] = seen.get(t, 0) + 1
    dup_rows = sum(c - 1 for c in seen.values() if c > 1)
    lines.append(f"| 完全重复行 | {dup_rows}（{dup_rows / n:.2%}） |" if n else "| 完全重复行 | 0 |")
    if dup_rows:
        warn.append(f"整行重复 {dup_rows} 条：先确认是重复录入还是天然多事件（同客户同日多单）")

    # 列画像
    lines.append("\n## 列画像\n")
    lines.append("| 列 | 类型 | 缺失 | 缺失率 | 唯一值 | 最小 | P5 | P50 | P95 | 最大 | IQR 外点 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")

    numeric_cols: dict[str, list[float]] = {}
    date_cols: dict[str, list[datetime]] = {}
    sparse: list[tuple[str, int, float]] = []      # (列, 缺失数, 缺失率)，留给「缺失结构」判
    for j, name in enumerate(header):
        col = [r[j] for r in rows]
        nonmiss = [v for v in col if not is_missing(v)]
        miss = len(col) - len(nonmiss)
        miss_rate = miss / n if n else 0.0
        uniq = len({v.strip() for v in nonmiss})
        typ, parsed = infer_type(col)

        mn = p5 = p50 = p95 = mx = outl = "—"
        if typ in ("int", "float") and parsed:
            vals = sorted(parsed)
            numeric_cols[name] = vals
            mn, mx = fmt(vals[0]), fmt(vals[-1])
            p50 = fmt(quantile(vals, 0.5))
            if len(vals) >= 20:
                p5v, p95v = quantile(vals, 0.05), quantile(vals, 0.95)
                p5, p95 = fmt(p5v), fmt(p95v)
                q1, q3 = quantile(vals, 0.25), quantile(vals, 0.75)
                iqr = q3 - q1
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                outl = sum(1 for v in vals if v < lo or v > hi)
                if outl / len(vals) > 0.05:
                    warn.append(f"`{name}` IQR 外点占 {outl / len(vals):.1%}：**不要直接删**，先区分录入错误与真实极端")
                # 量级错误是 OR 数据最常见的病：单位差 100~1000 倍
                if p95v > 0 and vals[-1] > 8 * p95v:
                    mult = vals[-1] / p95v
                    tag = ("**强烈疑似单位/量级错误**（如 m 与 km、元与万元）"
                           if mult > 50 else "疑似极端值或量级错误")
                    warn.append(
                        f"`{name}` 最大值 {fmt(vals[-1])} 是 P95 的 {mult:.0f} 倍：{tag}，"
                        f"先查口径与原始凭证，再决定改/删/保留"
                    )
                if p5v > 0 and vals[0] > 0 and vals[0] * 50 < p5v:
                    warn.append(
                        f"`{name}` 最小值 {fmt(vals[0])} 比 P5 小 {p5v / vals[0]:.0f} 倍："
                        f"疑似**单位错（kg 与 g）或负值/冲销**，确认后再处置"
                    )
            # 下面几项与小样本无关，必须每列都查：静默丢值比报错危险
            zeros = sum(1 for v in vals if v == 0)
            if zeros:
                warn.append(
                    f"`{name}` 含 {zeros} 个 0 值：先分清**真实零**（该笔确实为 0）与**占位/漏录**"
                    f"（本该有值却填 0）。后者直接参与计算会把结果系统性拉低"
                )
            uniq_num = set(vals)
            bad_vals = [v for v in nonmiss if parse_number(v) is None]
            if bad_vals:
                suspects = []
                for v in bad_vals:
                    p = numeric_prefix(v)
                    if p is None or p == 0:
                        continue
                    for k in (10, 100, 1000):
                        t = p * k
                        if any(abs(u - t) <= 0.005 * t for u in uniq_num):
                            suspects.append((v.strip(), f"×{k}"))
                            break
                if suspects:
                    show = "、".join(f"`{s}`（{k}）" for s, k in suspects[:3])
                    warn.append(
                        f"`{name}` 有 {len(bad_vals)} 个值无法按数值解析，其中 {len(suspects)} 个的数字部分"
                        f"乘 10/100 后恰好等于本列其他值：{show} —— **极可能是单位混用**，"
                        f"按列内主单位换算，别直接丢（丢掉会让总量悄悄变小）"
                    )
                else:
                    warn.append(
                        f"`{name}` 有 {len(bad_vals)} 个值无法按数值解析：**它们既不计入分位数、"
                        f"也不参与规则检查**，先确认是格式问题还是错列"
                    )
            # 单位混用的另一种形态：两个值本身呈 10^k 关系，且成对出现多次
            # 只对「离散型」数值列查：连续量测列里 1.2 与 12 这种巧合太多，查了全是噪音
            pairs = []
            if len(uniq_num) <= max(20, 0.1 * n):
                for v in sorted(uniq_num):
                    if v <= 0:
                        continue
                    for k in (10, 100, 1000):
                        t = v * k
                        if any(abs(u - t) <= 0.005 * t for u in uniq_num):
                            pairs.append((v, t))
                            break
            if len(pairs) >= 3:
                warn.append(
                    f"`{name}` 有 {len(pairs)} 组值呈 10 倍关系（如 {fmt(pairs[0][0])} 与 {fmt(pairs[0][1])}）："
                    f"**疑似单位混用**（cm/mm、元/万元），先统一口径再算"
                )
        elif typ == "date" and parsed:
            ds = sorted(d for d in parsed if d is not None)
            date_cols[name] = ds
            mn, mx = ds[0].strftime("%Y-%m-%d"), ds[-1].strftime("%Y-%m-%d")
            p50 = ds[len(ds) // 2].strftime("%Y-%m-%d")
            bad = len(nonmiss) - len(parsed)
            if bad:
                warn.append(f"`{name}` 有 {bad} 个值无法解析为日期：规则检查会跳过这些行，先确认是否为脏值")

        if miss_rate > 0.2:
            sparse.append((name, miss, miss_rate))

        lines.append(
            f"| `{name}` | {typ} | {miss} | {miss_rate:.1%} | {uniq} | {mn} | {p5} | {p50} | {p95} | {mx} | {outl} |"
        )

    # 缺失结构：缺失是不是「随机没填」，还是「这个字段只对某类行适用」
    lines.append("\n## 缺失结构\n")
    if not sparse:
        lines.append("- 无缺失率 > 20% 的列")
    else:
        exp = explain_missing(header, rows, [c for c, _, _ in sparse])
        for name, miss, rate in sparse:
            if name in exp:
                c, empty, n_empty, full, n_full = exp[name]
                eg = "、".join(f"`{g}`" for g in empty[:3])
                fg = "、".join(f"`{g}`" for g in full[:3])
                lines.append(
                    f"- `{name}` 缺失 {miss}（{rate:.1%}）**由 `{c}` 完全解释**："
                    f"{c}={eg} 的 {n_empty} 行全空，{c}={fg} 的 {n_full} 行全有"
                )
                warn.append(
                    f"`{name}` 是**条件字段**：只在 `{c}`={fg} 的 {n_full} 行上有值"
                    f"（`{c}`={eg} 的 {n_empty} 行全空）。当全量字段用会把结论算错——"
                    f"均值、覆盖率、达标率都只在这 {n_full} 行上有效，且必须写明范围"
                )
            else:
                lines.append(f"- `{name}` 缺失 {miss}（{rate:.1%}）：未找到能解释它的列，按随机缺失处理也要标注")
                warn.append(
                    f"`{name}` 缺失率 {rate:.1%} 且无法由其他列解释：先查是否成块缺失"
                    f"（停业/系统切换/某类单不填），不可当随机缺失"
                )

    # 主键
    lines.append("\n## 主键与重复\n")
    keys = keys or []
    if keys:
        idx = [header.index(k) for k in keys if k in header]
        if len(idx) < len(keys):
            missing = [k for k in keys if k not in header]
            lines.append(f"- ⚠ 未找到列：{missing}")
        if idx:
            cnt: dict[tuple, int] = {}
            for r in rows:
                t = tuple(r[i].strip() for i in idx)
                cnt[t] = cnt.get(t, 0) + 1
            dup = sum(c - 1 for c in cnt.values() if c > 1)
            uniq = len(cnt)
            lines.append(f"- 键 `{'+'.join(keys)}`：唯一值 {uniq}，重复 {dup}（{dup / n:.1%}）" if n else "")
            if dup:
                sample = [k for k, c in cnt.items() if c > 1][:3]
                lines.append(f"- 重复样例：{sample}")
                warn.append(f"主键重复 {dup} 条：**先定聚合口径再去重**，顺序反了会把一单多行误删成一行")
    else:
        lines.append("- 未指定 `--key`；若一行代表一个实体，建议补上，否则重复难以发现")

    # 时间
    lines.append("\n## 时间列\n")
    if timecol:
        if timecol not in header:
            lines.append(f"- ⚠ 列 `{timecol}` 不存在")
        else:
            ds = sorted(d for d in (parse_date(r[header.index(timecol)]) for r in rows) if d)
            if not ds:
                lines.append(f"- ⚠ `{timecol}` 无可解析日期")
            else:
                days = sorted({d.date() for d in ds})
                span = (days[-1] - days[0]).days
                gaps = [(ds[i] - ds[i - 1]).total_seconds() for i in range(1, len(ds)) if ds[i] > ds[i - 1]]
                med = quantile(sorted(gaps), 0.5) if gaps else 0
                unit = "秒" if med < 60 else "分钟" if med < 3600 else "小时" if med < 86400 else "天"
                scale = 1 if unit == "秒" else 60 if unit == "分钟" else 3600 if unit == "小时" else 86400
                lines.append(f"- 范围：{ds[0]} ~ {ds[-1]}（跨 {span} 天）")
                lines.append(f"- 覆盖 {len(days)} / {span + 1} 天")
                if gaps:
                    lines.append(f"- 相邻间隔中位数：{med / scale:.2f} {unit}（跨夜/跨周会拉大，属正常节奏）")
                # 断档按「整天缺失」判，不按间隔判（16:00→次日 08:00 不是断档）
                have = set(days)
                cur = days[0]
                holes = []
                while cur <= days[-1]:
                    if cur not in have:
                        start = cur
                        while cur <= days[-1] and cur not in have:
                            cur += timedelta(days=1)
                        holes.append((start, cur - timedelta(days=1)))
                    else:
                        cur += timedelta(days=1)
                long_holes = [h for h in holes if (h[1] - h[0]).days + 1 >= 2]
                # 周末无业务是正常节奏，不算断档
                def _weekend_only(a, b):
                    d = a
                    while d <= b:
                        if d.weekday() < 5:
                            return False
                        d += timedelta(days=1)
                    return True

                long_holes = [h for h in long_holes if not _weekend_only(*h)]
                if long_holes and span >= 14:
                    show = "、".join(f"{a}~{b}" for a, b in long_holes[:3])
                    lines.append(f"- ⚠ 连续缺失 ≥2 天的区间 {len(long_holes)} 段：{show}")
                    warn.append(
                        f"时间列有 {len(long_holes)} 段连续 ≥2 天的空档（{show}）："
                        f"查是停业/系统切换还是数据没导出，用「日均」时会失真"
                    )
                elif long_holes:
                    show = "、".join(f"{a}~{b}" for a, b in long_holes[:3])
                    lines.append(f"- 有 {len(long_holes)} 段整天无记录（{show}），但窗口仅 {span} 天，不作断档告警")
    elif date_cols:
        lines.append(f"- 自动识别到日期列：{list(date_cols)[:5]}；关键时间列请用 `--time` 明确指定")
    else:
        lines.append("- 未识别到日期列")

    # 规则
    lines.append("\n## 逻辑规则\n")
    if rules:
        lines.append("| 规则 | 可检查行数 | 违规数 | 违规率 | 样例 |")
        lines.append("|---|---|---|---|---|")
        for rule in rules:
            try:
                desc, checked, bad, sample = check_rule(rule, header, rows)
            except ValueError as e:
                lines.append(f"| `{rule}` | — | — | — | ⚠ {e} |")
                rule_fail += 1
                continue
            rate = bad / checked if checked else 0.0
            lines.append(f"| {desc} | {checked} | {bad} | {rate:.1%} | {sample} |")
            if bad:
                warn.append(f"规则 `{rule}` 违规 {bad} 行：**这是建模问题不是清洗问题**，要让校验器炸出来")
    else:
        lines.append("- 未提供 `--rule`。建议至少加两条：时间先后、量值非负")

    # 下一步
    lines.append("\n## 建议下一步\n")
    if not warn:
        lines.append("- 画像未见明显问题；仍需人工确认「一行代表什么」与单位口径（工具看不出语义）")
    else:
        for i, w in enumerate(warn, 1):
            lines.append(f"{i}. {w}")
    lines.append("")
    lines.append("> 以上均为事实陈述。处置方式须经 D5（清洗口径）确认，并记入清洗动作表。")

    return "\n".join(lines) + "\n", rule_fail


# ---------------------------------------------------------------- CLI

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="CSV 数据画像（纯标准库）")
    ap.add_argument("path", help="CSV / TSV 文件")
    ap.add_argument("--key", default="", help="主键列，逗号分隔")
    ap.add_argument("--time", default="", help="时间列名")
    ap.add_argument("--rule", action="append", default=[], help="逻辑规则，如 `a >= b`，可重复")
    ap.add_argument("--sep", default=None, help="分隔符，默认自动嗅探")
    ap.add_argument("--max-rows", type=int, default=None, help="只读前 N 行（大文件抽样）")
    ap.add_argument("--out", default=None, help="输出 markdown 路径；缺省打到 stdout")
    args = ap.parse_args(argv)

    p = Path(args.path).expanduser()
    if not p.is_file():
        print(f"文件不存在：{p}", file=sys.stderr)
        return 2

    header, rows, enc, sep = read_table(p, args.sep, args.max_rows)
    keys = [k.strip() for k in args.key.split(",") if k.strip()]
    report, rule_fail = profile(p, header, rows, enc, sep, keys, args.time or None, args.rule)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"画像已写入 {out}（{len(rows)} 行 × {len(header)} 列）")
    else:
        print(report)
    return 3 if rule_fail else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
