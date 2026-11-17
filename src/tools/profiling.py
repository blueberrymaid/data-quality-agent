"""数据画像（Profiling）：智能体"先看再动手"的第一批工具。

全部为确定性实现，不依赖大模型，保证结果可复现、可审计。
输入一律按"原始脏数据"处理：默认全部读成字符串，由这里推断真实类型，
这样才能发现"数值列里混着 '1,234' 这种字符串"的问题。
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

# 常见缺失值/哨兵值写法
# 注意：不含 "unknown"。本数据集（UCI Bank Marketing）里 unknown 是 contact/poutcome/job
# 等字段的合法类别取值，把它当缺失会把 3.9 万条正常记录误判（见评估 precision 崩塌）。
MISSING_TOKENS = {"", " ", "na", "n/a", "nan", "null", "none", "?",
                  "未知", "-", "--", "无", "缺失", "。", "．"}

THOUSAND_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def is_missing_token(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and np.isnan(v):
        return True
    s = str(v).strip().lower()
    return s in MISSING_TOKENS


def looks_thousand_separated(v: Any) -> bool:
    return bool(THOUSAND_RE.match(str(v).strip()))


def parse_number(v: Any) -> float | None:
    """尽最大努力把任意写法解析成数字，失败返回 None。"""
    if v is None:
        return None
    s = str(v).strip().replace(" ", "")
    if s == "" or s.lower() in MISSING_TOKENS:
        return None
    s = s.replace(",", "").replace("，", "")
    s = s.replace("元", "").replace("岁", "").replace("分钟", "").replace("min", "")
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        return None


def infer_column_type(values: pd.Series) -> str:
    """推断列的真实类型：numeric / categorical / mixed。"""
    vals = [v for v in values.tolist() if not is_missing_token(v)]
    if not vals:
        return "empty"
    n = len(vals)
    num = sum(1 for v in vals if parse_number(v) is not None)
    raw_num = sum(1 for v in vals if NUMERIC_RE.match(str(v).strip()))
    if num == n:
        # 全可解析为数字：区分"原生数字"和"带格式的数字字符串"
        return "numeric" if raw_num == n else "numeric_formatted"
    if num / n >= 0.5:
        return "mixed"
    return "categorical"


def profile_table(df: pd.DataFrame, name: str = "main", top_k: int = 5) -> dict:
    """输出整表画像。"""
    cols: dict[str, dict] = {}
    for c in df.columns:
        s = df[c]
        n = len(s)
        miss = int(s.map(is_missing_token).sum())
        nonmiss = s[~s.map(is_missing_token)]
        ctype = infer_column_type(s)
        info: dict[str, Any] = {
            "dtype_inferred": ctype,
            "missing": miss,
            "missing_pct": round(100 * miss / n, 2) if n else 0.0,
            "unique": int(nonmiss.nunique()),
            "samples": [str(v)[:24] for v in nonmiss.head(4).tolist()],
        }
        if ctype in ("numeric", "numeric_formatted", "mixed"):
            nums = pd.Series([parse_number(v) for v in nonmiss.tolist()], dtype="float64").dropna()
            if len(nums):
                info.update({
                    "min": float(nums.min()), "max": float(nums.max()),
                    "mean": round(float(nums.mean()), 3),
                    "median": float(nums.median()),
                    "std": round(float(nums.std()), 3) if len(nums) > 1 else 0.0,
                    "q1": float(nums.quantile(.25)), "q3": float(nums.quantile(.75)),
                })
            if ctype == "mixed":
                bad = [str(v)[:24] for v in nonmiss[nonmiss.map(lambda x: parse_number(x) is None)].head(5)]
                info["non_numeric_examples"] = bad
        if ctype == "categorical":
            vc = nonmiss.astype(str).str.strip().value_counts()
            info["top_values"] = {str(k): int(v) for k, v in vc.head(top_k).items()}
            info["n_rare_categories"] = int((vc < max(3, int(0.001 * n))).sum())
        cols[c] = info

    n_rows, n_cols = df.shape
    total_cells = n_rows * n_cols
    total_missing = sum(v["missing"] for v in cols.values())
    return {
        "table": name,
        "n_rows": int(n_rows),
        "n_cols": int(n_cols),
        "n_cells": int(total_cells),
        "missing_rate_overall": round(100 * total_missing / total_cells, 3) if total_cells else 0.0,
        "duplicate_rows": int(df.duplicated().sum()),
        "columns": cols,
        "type_summary": pd.Series([v["dtype_inferred"] for v in cols.values()]).value_counts().to_dict(),
    }


def compact_profile(profile: dict, max_cols: int = 40) -> str:
    """把画像压成给 LLM 看的紧凑文本（省 token，又不丢关键信息）。"""
    lines = [
        f"表={profile['table']} 行={profile['n_rows']} 列={profile['n_cols']} "
        f"整体缺失率={profile['missing_rate_overall']}% 完全重复行={profile['duplicate_rows']}",
        f"列类型分布：{profile['type_summary']}",
        "列清单（列名|推断类型|缺失数|唯一值数|样本|统计）：",
    ]
    for c, info in list(profile["columns"].items())[:max_cols]:
        stat = ""
        if "min" in info:
            stat = f"min={info['min']} max={info['max']} mean={info['mean']}"
        elif "top_values" in info:
            stat = "top=" + str(list(info["top_values"].items())[:3])
        bad = ""
        if info.get("non_numeric_examples"):
            bad = f" 非数值样本={info['non_numeric_examples']}"
        lines.append(
            f"- {c} | {info['dtype_inferred']} | 缺失={info['missing']} | "
            f"唯一={info['unique']} | 样本={info['samples']} | {stat}{bad}"
        )
    return "\n".join(lines)


def numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    """把任意脏列转成数值列（无法解析→NaN），供统计类工具复用。"""
    return pd.Series([parse_number(v) for v in df[col].tolist()], index=df.index, dtype="float64")
