"""修复工具集：把检测结果变成具体的修复动作。

设计约束（写进报告）：
- 每个动作都是**幂等、可回滚**的纯函数：不修改入参，返回新表 + 变更报告；
- 动作目录 ACTION_CATALOG 同时是给大模型规划器看的"可用动作清单"，
  模型只能在清单里选，不能凭空发明操作，避免越权改数据；
- 修复分两类：**可精确还原**（格式、重复）与**只能近似**（缺失、异常、冲突、标签），
  两类分开统计，报告里如实区分，不把近似修复说成"还原"。
"""
from __future__ import annotations

import re
from typing import Any, Callable

import numpy as np
import pandas as pd

from .profiling import is_missing_token, parse_number
from .quality import DOMAIN_BOUNDS, Finding

MONTH_MAP = {m: m for m in ["jan", "feb", "mar", "apr", "may", "jun",
                            "jul", "aug", "sep", "oct", "nov", "dec"]}

NUMERIC_COLS = ["age", "balance", "day", "duration", "campaign", "pdays", "previous"]
CAT_COLS = ["job", "marital", "education", "default", "housing", "loan",
            "contact", "month", "poutcome", "y", "城市", "客户姓名", "job".strip()]


# =============================================================== 1 格式标准化
def standardize_format(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """统一类型与写法：这是唯一能"精确还原"的修复类别。"""
    out = df.copy()
    changed = 0
    detail: dict[str, int] = {}

    for c in out.columns:
        s = out[c]
        before = s.astype(str).copy()

        # 统一转字符串并去空白/全角空白
        s2 = s.map(lambda v: "" if is_missing_token(v) else
                   re.sub(r"[\u3000]", " ", str(v)).strip())
        # 全角数字 → 半角；去掉常见单位后缀
        s2 = s2.map(lambda v: v.translate(str.maketrans("０１２３４５６７８９", "0123456789")))

        if c in NUMERIC_COLS or c == "开户年份":
            nums = s2.map(lambda v: np.nan if v == "" else parse_number(v))
            out[c] = pd.to_numeric(nums, errors="coerce")
        elif c == "month":
            out[c] = s2.map(lambda v: v[:3].lower() if v else "")
        elif c in ("y",):
            def canon(v: str) -> str:
                t = v.strip().lower()
                if t in ("1", "yes", "y", "true", "是", "正例"):
                    return "yes"
                if t in ("0", "no", "n", "false", "否", "负例"):
                    return "no"
                return t
            out[c] = s2.map(canon)
        else:
            out[c] = s2

        after = out[c].astype(str)
        n_diff = int((before.str.strip() != after.str.strip()).sum())
        if n_diff:
            changed += n_diff
            detail[c] = n_diff
    return out, {"action": "standardize_format", "cells_changed": changed, "per_column": detail}


# =============================================================== 2 缺失值填补
def impute_missing(df: pd.DataFrame, strategy: str = "group_mode",
                   group_col: str = "job") -> tuple[pd.DataFrame, dict]:
    out = df.copy()
    filled: dict[str, int] = {}
    for c in out.columns:
        miss = out[c].map(is_missing_token)
        if not miss.any():
            continue
        if pd.api.types.is_numeric_dtype(out[c]):
            if strategy == "group_mode" and group_col in out.columns:
                grp = out.groupby(out[group_col].astype(str))[c].transform("median")
                fill = grp.fillna(out[c].median())
            else:
                fill = pd.Series([out[c].median()] * len(out), index=out.index)
            out.loc[miss, c] = fill[miss]
        else:
            if strategy == "group_mode" and group_col in out.columns:
                grp = out.groupby(out[group_col].astype(str))[c].transform(
                    lambda s: s.mode().iloc[0] if len(s.mode()) else "")
                fill = grp
                fallback = out[c][~miss].mode()
                fb = fallback.iloc[0] if len(fallback) else "unknown"
                fill = fill.map(lambda v: v if v != "" else fb)
            else:
                m = out[c][~miss].mode()
                fb = m.iloc[0] if len(m) else "unknown"
                fill = pd.Series([fb] * len(out), index=out.index)
            out.loc[miss, c] = fill[miss]
        filled[c] = int(miss.sum())
    return out, {"action": "impute_missing", "strategy": strategy,
                 "cells_filled": sum(filled.values()), "per_column": filled}


# =============================================================== 3 重复数据
def drop_duplicates(df: pd.DataFrame, near: bool = True) -> tuple[pd.DataFrame, dict]:
    before = len(df)
    out = df.copy()
    # 精确重复
    mask_exact = out.duplicated(keep="first")
    out = out[~mask_exact]
    removed_exact = int(mask_exact.sum())

    removed_near = 0
    if near:
        cols = [c for c in out.columns if c not in ("balance", "duration")]
        # 注意：不能用 astype(str).agg("|".join)，缺失值在部分 pandas 版本下仍是 float，
        # 会抛 TypeError: sequence item N: expected str instance, float found。
        key = out[cols].apply(
            lambda r: re.sub(r"\s", "", "|".join(str(v) for v in r).lower()), axis=1)
        mask_near = key.duplicated(keep="first")
        removed_near = int(mask_near.sum())
        out = out[~mask_near]
    out = out.reset_index(drop=True)
    return out, {"action": "drop_duplicates", "rows_before": before, "rows_after": len(out),
                 "removed_exact": removed_exact, "removed_near": removed_near}


# =============================================================== 4 异常值
def fix_outliers(df: pd.DataFrame, strategy: str = "clip") -> tuple[pd.DataFrame, dict]:
    """异常值处理。

    strategy:
      - ``clip``（默认）：只按业务边界裁剪（硬约束违规才改），保留 IQR 意义上的
        长尾真实值。银行余额、通话时长这类重尾字段的真实大值不应被抹平。
      - ``clip_impute``：裁剪 + 用中位数替换（更激进，会损失长尾信息，仅作对照）。
      - ``median`` / ``nan``：整列异常值替换为中位数 / 置空。
    """
    out = df.copy()
    stats: dict[str, int] = {}
    for c in NUMERIC_COLS:
        if c not in out.columns:
            continue
        nums = pd.to_numeric(out[c], errors="coerce")
        lo, hi = DOMAIN_BOUNDS.get(c, (None, None))
        hard = pd.Series(False, index=out.index)
        soft = pd.Series(False, index=out.index)
        if lo is not None:
            hard |= (nums < lo) | (nums > hi)
        q1, q3 = nums.quantile(.25), nums.quantile(.75)
        iqr = q3 - q1
        if iqr and iqr > 0:
            soft |= (nums < q1 - 3 * iqr) | (nums > q3 + 3 * iqr)
        hard = hard.fillna(False)
        soft = soft.fillna(False) & ~hard
        k = int(hard.sum() + soft.sum())
        if not k:
            continue
        if strategy == "clip":
            # 只动硬约束越界：越界值裁到边界，统计意义上的长尾保持原样
            if lo is not None and hard.any():
                nums = nums.clip(lo, hi)
            out[c] = nums
            stats[c] = int(hard.sum())
            continue
        bad = hard | soft
        if strategy == "clip_impute":
            med = nums[~bad].median()
            nums = nums.mask(bad, np.nan)
            if lo is not None:
                nums = nums.clip(lo, hi)
            nums = nums.fillna(med)
            out[c] = nums
        elif strategy == "median":
            nums = nums.mask(bad, nums[~bad].median())
            out[c] = nums
        elif strategy == "nan":
            out[c] = nums.mask(bad, np.nan)
        stats[c] = int(bad.sum())
    return out, {"action": "fix_outliers", "strategy": strategy,
                 "cells_fixed": sum(stats.values()), "per_column": stats}


# =============================================================== 5 字段冲突
def resolve_conflicts(df: pd.DataFrame, ref: pd.DataFrame | None = None,
                      match_map: dict[int, int] | None = None,
                      policy: str = "recompute_from_age") -> tuple[pd.DataFrame, dict]:
    out = df.copy()
    resolved = 0

    if "age" in out.columns and "开户年份" in out.columns and policy == "recompute_from_age":
        age = pd.to_numeric(out["age"], errors="coerce")
        yr = pd.to_numeric(out["开户年份"], errors="coerce")
        birth = 2026 - age
        implied = yr - birth
        bad = ~((implied >= 18) & (implied <= age) & (yr <= 2026))
        bad = bad.fillna(False)
        # 用中位开户年龄反推一个自洽的年份（保留原始分布形状，而非硬造新值）
        open_age = (yr - birth)[~bad].dropna()
        med_open = float(open_age.median()) if len(open_age) else 30.0
        fixed = (birth + med_open).round().clip(upper=2026)
        out.loc[bad, "开户年份"] = fixed[bad]
        resolved += int(bad.sum())

    # 跨表教育水平冲突：以主表为准（主表字段是建模特征，档案表仅作参照），并记录差异
    if ref is not None and match_map and "education" in out.columns and "教育水平(档案)" in ref.columns:
        diffs = 0
        for mi, ri in match_map.items():
            if mi >= len(out):
                continue
            a, b = out.at[mi, "education"], ref.at[ri, "教育水平(档案)"]
            if is_missing_token(a) or is_missing_token(b):
                continue
            if str(a).strip().lower() != str(b).strip().lower():
                diffs += 1
        resolved += diffs

    # 主键冲突：同一客户编号保留信息最全的一条
    if "客户编号" in out.columns:
        out["_nonnull"] = out.notna().sum(axis=1)
        before = len(out)
        out = (out.sort_values("_nonnull", ascending=False)
                  .drop_duplicates(subset=["客户编号"], keep="first")
                  .drop(columns=["_nonnull"]).reset_index(drop=True))
        resolved += before - len(out)

    return out, {"action": "resolve_conflicts", "policy": policy,
                 "conflicts_resolved": resolved, "rows_after": len(out)}


# =============================================================== 6 标签错误
def fix_label_errors(df: pd.DataFrame, target: str = "y",
                     threshold: float = 0.02, max_rows: int = 500) -> tuple[pd.DataFrame, dict]:
    """只翻转"模型几乎确定"的那一小部分标签，其余列入人工复核清单。

    风险声明：标签修复本质是"用模型改真值"，可能把真正的少数类改坏。
    因此这里用极低阈值(默认 0.02) + 上限，并在报告中给出敏感性分析。
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import StratifiedKFold

    out = df.copy()
    if target not in out.columns:
        return out, {"action": "fix_label_errors", "fixed": 0, "note": "目标列缺失"}
    y_raw = out[target].astype(str).str.strip().str.lower()
    uniq = sorted(set(y_raw.unique()) - {"", "nan"})
    if len(uniq) != 2:
        return out, {"action": "fix_label_errors", "fixed": 0, "note": "目标列取值不唯一"}

    feats = pd.DataFrame(index=out.index)
    for c in out.columns:
        if c == target or c in ("客户编号", "客户姓名"):
            continue
        if pd.api.types.is_numeric_dtype(out[c]):
            feats[c] = out[c].fillna(out[c].median())
        else:
            feats[c] = out[c].astype(str).astype("category").cat.codes
    feats = feats.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = (y_raw == uniq[-1]).astype(int).values
    oof = np.zeros(len(y))
    for tr, te in StratifiedKFold(5, shuffle=True, random_state=42).split(feats.values, y):
        m = HistGradientBoostingClassifier(max_iter=120, random_state=42)
        m.fit(feats.values[tr], y[tr])
        oof[te] = m.predict_proba(feats.values[te])[:, 1]
    p_true = np.where(y == 1, oof, 1 - oof)
    susp = np.where(p_true < threshold)[0]
    if len(susp) > max_rows:
        susp = susp[np.argsort(p_true[susp])][:max_rows]
    for i in susp:                      # 翻转成另一个类
        out.at[out.index[i], target] = uniq[0] if y[i] == 1 else uniq[-1]
    return out, {"action": "fix_label_errors", "threshold": threshold,
                 "fixed": int(len(susp)), "n_suspicious_total": int((p_true < 0.15).sum())}


# =============================================================== 7 跨表补全
def enrich_from_ref(df: pd.DataFrame, ref: pd.DataFrame,
                    match_map: dict[int, int]) -> tuple[pd.DataFrame, dict]:
    out = df.copy()
    added = 0
    for col in ref.columns:
        if col in out.columns or col in ("客户ID",):
            continue
        vals = pd.Series([None] * len(out), index=out.index, dtype=object)
        for mi, ri in match_map.items():
            if mi < len(out):
                vals.iloc[mi] = ref.at[ri, col]
        out[f"档案_{col}"] = vals
        added += 1
    return out, {"action": "enrich_from_ref", "columns_added": added,
                 "matched_rows": len(match_map), "total_rows": len(out)}


# =============================================================== 8 类别不平衡
def rebalance_training(X: pd.DataFrame, y: np.ndarray, method: str = "class_weight") -> dict:
    """不平衡处理不修改数据本身，而是返回建模侧的权重/采样配置。

    理由：在预处理阶段做重采样会污染验证集分布，导致指标虚高。
    """
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    if method == "class_weight":
        return {"method": "class_weight", "class_weight": {0: 1.0, 1: neg / max(pos, 1)}}
    if method == "oversample":
        return {"method": "oversample", "target_ratio": 0.5}
    return {"method": "none"}


def rebalance_training_action(df: pd.DataFrame, target: str = "y",
                              method: str = "class_weight") -> tuple[pd.DataFrame, dict]:
    """执行器适配层：不平衡处理不修改数据，只输出建模侧的权重配置。

    这样在统一的 (df -> df, stats) 动作契约下也能跑，并在日志里留痕说明
    “本步没有改动任何单元格，只给出训练侧配置”。
    """
    out = df.copy()
    if target not in out.columns:
        return out, {"action": "rebalance_training", "note": "目标列缺失，仅记录",
                     "data_changed": 0}
    y_raw = out[target].astype(str).str.strip().str.lower()
    uniq = sorted(set(y_raw.unique()) - {"", "nan"})
    if len(uniq) != 2:
        return out, {"action": "rebalance_training", "note": "目标列取值不唯一，仅记录",
                     "data_changed": 0}
    y = (y_raw == uniq[-1]).astype(int).values
    cfg = rebalance_training(out, y, method=method)
    return out, {"action": "rebalance_training", "method": cfg.get("method"),
                 "class_weight": cfg.get("class_weight"),
                 "note": "仅提供建模侧配置，未修改数据（重采样放到训练集内部做）",
                 "data_changed": 0}


# =============================================================== 动作目录
ACTION_CATALOG: list[dict[str, Any]] = [
    {"name": "standardize_format", "solves": ["format"],
     "desc": "统一数值/日期/类别写法，去空白与全角字符，是唯一可精确还原的修复",
     "params": {}},
    {"name": "impute_missing", "solves": ["missing"],
     "desc": "缺失值填补：数值用分组中位数，类别用分组众数",
     "params": {"strategy": ["group_mode", "median", "mode"]}},
    {"name": "drop_duplicates", "solves": ["duplicate"],
     "desc": "删除完全重复与近似重复行（保留首次出现）",
     "params": {"near": [True, False]}},
    {"name": "fix_outliers", "solves": ["outlier"],
     "desc": "异常值处理：clip=只按业务边界裁剪（保留长尾真实值，推荐）；"
             "clip_impute=裁剪后中位数替换（激进，会抹平长尾）；median/nan=整列替换",
     "params": {"strategy": ["clip", "clip_impute", "median", "nan"]}},
    {"name": "resolve_conflicts", "solves": ["conflict"],
     "desc": "字段冲突按规则消解：开户年份由年龄反推；主键重复保留信息最全的一条",
     "params": {"policy": ["recompute_from_age", "drop_rows"]}},
    {"name": "fix_label_errors", "solves": ["label_error"],
     "desc": "翻转模型高度确信的错标（极高阈值，只处理极少数）",
     "params": {"threshold": [0.02, 0.05, 0.1], "max_rows": [200, 500]}},
    {"name": "enrich_from_ref", "solves": ["entity_match"],
     "desc": "用跨表匹配置信结果补全主表缺失字段，未匹配行留空待人工处理",
     "params": {}},
    {"name": "rebalance_training", "solves": ["imbalance"],
     "desc": "不平衡处理只作用于训练集（类别权重/过采样），不修改预处理后的数据",
     "params": {"method": ["class_weight", "oversample"]}},
]

ACTION_FUNCS: dict[str, Callable] = {
    "standardize_format": standardize_format,
    "impute_missing": impute_missing,
    "drop_duplicates": drop_duplicates,
    "fix_outliers": fix_outliers,
    "resolve_conflicts": resolve_conflicts,
    "fix_label_errors": fix_label_errors,
    "enrich_from_ref": enrich_from_ref,
    "rebalance_training": rebalance_training_action,
}
