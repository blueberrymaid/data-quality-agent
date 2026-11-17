"""质量检测工具集：四类核心问题 + 四类复杂问题的检测器。

每个检测器都是纯函数，输入 DataFrame、输出结构化 Finding 列表。
检测器不认识"真值"（真值只在评估阶段使用），因此检测指标是可信的。

四类核心问题：missing / duplicate / outlier / format
四类复杂问题：conflict / label_error / imbalance / entity_match
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import pandas as pd

from .profiling import (
    MISSING_TOKENS, is_missing_token, looks_thousand_separated, parse_number,
)

# 业务常识边界（数据工程中常见的手写规则，报告中作为"领域规则注入"说明）
DOMAIN_BOUNDS: dict[str, tuple[float, float]] = {
    "age": (18, 100),
    "day": (1, 31),
    "duration": (0, 6000),
    "campaign": (1, 100),
    "pdays": (-1, 1000),
    "previous": (0, 100),
    "balance": (-20000, 200000),
}

FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


@dataclass
class Finding:
    problem_type: str
    field: str | None
    severity: str
    n_affected: int
    total: int
    rate: float
    method: str
    evidence: dict = field(default_factory=dict)
    cells: list[tuple[int, str]] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)

    def brief(self) -> dict:
        """给 LLM 看的精简版（不含逐单元格清单）。"""
        d = asdict(self)
        d.pop("cells")
        d["rows"] = self.rows[:10]
        return d


def _severity(rate: float) -> str:
    if rate >= 0.2:
        return "high"
    if rate >= 0.05:
        return "medium"
    if rate > 0:
        return "low"
    return "none"


# --------------------------------------------------------------- 1 缺失值
def detect_missing(df: pd.DataFrame) -> list[Finding]:
    out: list[Finding] = []
    n = len(df)
    for c in df.columns:
        mask = df[c].map(is_missing_token)
        k = int(mask.sum())
        if k == 0:
            continue
        cells = [(int(i), c) for i in df.index[mask]]
        out.append(Finding(
            problem_type="missing", field=c, severity=_severity(k / n),
            n_affected=k, total=n, rate=round(k / n, 4),
            method="缺失标记扫描（空串/NA/NULL/unknown/?/未知 等 16 种写法）",
            evidence={"tokens": sorted({str(df.at[i, c])[:12] for i, _ in cells[:50]})},
            cells=cells,
        ))
    return out


# --------------------------------------------------------------- 2 重复数据
def _norm_cell(v: Any) -> str:
    if is_missing_token(v):
        return "<NA>"
    s = str(v).strip().lower()
    s = s.translate(FULLWIDTH_DIGITS)
    s = re.sub(r"[\s\u3000]+", "", s)
    s = s.replace("，", ",")
    return s


def detect_duplicates(df: pd.DataFrame, key_cols: list[str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    n = len(df)

    # (a) 完全重复（原始字面值相同）
    dup_mask = df.duplicated(keep="first")
    if dup_mask.any():
        rows = [int(i) for i in df.index[dup_mask]]
        out.append(Finding(
            problem_type="duplicate", field=None, severity=_severity(len(rows) / n),
            n_affected=len(rows), total=n, rate=round(len(rows) / n, 4),
            method="全字段逐字比对，保留首次出现",
            evidence={"sample_groups": rows[:5]}, rows=rows,
        ))

    # (b) 近似重复：按列名降噪后比对
    norm = pd.DataFrame({c: df[c].map(_norm_cell) for c in df.columns})
    if key_cols:
        norm_key = norm[key_cols].agg("|".join, axis=1)
    else:
        norm_key = norm.agg("|".join, axis=1)
    grp = norm_key.groupby(norm_key).groups
    near_rows: list[int] = []
    groups: list[list[int]] = []
    for _, idx in grp.items():
        idx = [int(i) for i in idx]
        if len(idx) > 1:
            # 排除已被"完全重复"覆盖的行
            base = norm_key.iloc[idx[0]]
            if len({norm_key.iloc[i] for i in idx}) == 1:
                groups.append(idx)
                near_rows.extend(idx[1:])
    if near_rows:
        out.append(Finding(
            problem_type="duplicate", field=None, severity=_severity(len(near_rows) / n),
            n_affected=len(near_rows), total=n, rate=round(len(near_rows) / n, 4),
            method="去空白/大小写/全角后比对（近似重复）",
            evidence={"n_groups": len(groups), "sample_group": groups[0][:6] if groups else []},
            rows=sorted(set(near_rows)),
        ))
    return out


# --------------------------------------------------------------- 3 异常值/噪声
def detect_outliers(df: pd.DataFrame, iqr_k: float = 3.0) -> list[Finding]:
    out: list[Finding] = []
    n = len(df)
    for c in df.columns:
        nums = pd.Series([parse_number(v) for v in df[c].tolist()], index=df.index, dtype="float64")
        if nums.notna().sum() < max(10, 0.5 * n):
            continue
        bad_idx: set[int] = set()
        reasons: dict[str, list[int]] = {}

        # (a) 业务边界
        if c in DOMAIN_BOUNDS:
            lo, hi = DOMAIN_BOUNDS[c]
            m = nums.notna() & ((nums < lo) | (nums > hi))
            if m.any():
                reasons["越界(业务规则)"] = [int(i) for i in df.index[m]]
                bad_idx |= set(reasons["越界(业务规则)"])

        # (b) 稳健 IQR（k=3 已很保守，避免把长尾真实值误判）
        q1, q3 = nums.quantile(.25), nums.quantile(.75)
        iqr = q3 - q1
        if iqr > 0:
            m = nums.notna() & ((nums < q1 - iqr_k * iqr) | (nums > q3 + iqr_k * iqr))
            if m.any():
                reasons[f"IQR(k={iqr_k})"] = [int(i) for i in df.index[m]]
                bad_idx |= set(reasons[f"IQR(k={iqr_k})"])

        # (c) 极端 z-score
        mu, sd = nums.mean(), nums.std()
        if sd and sd > 0:
            m = nums.notna() & (((nums - mu) / sd).abs() > 6)
            if m.any():
                reasons["z-score>6"] = [int(i) for i in df.index[m]]
                bad_idx |= set(reasons["z-score>6"])

        if bad_idx:
            n_domain = len(reasons.get("越界(业务规则)", []))
            out.append(Finding(
                problem_type="outlier", field=c, severity=_severity(len(bad_idx) / n),
                n_affected=len(bad_idx), total=n, rate=round(len(bad_idx) / n, 4),
                method="业务边界 + 稳健 IQR + 极端 z-score",
                evidence={
                    "kind": "domain_violation" if n_domain else "statistical",
                    "n_domain": n_domain,
                    "n_statistical": len(bad_idx) - n_domain,
                    "reasons": {k: len(v) for k, v in reasons.items()},
                    "sample_values": [str(df.at[i, c]) for i in sorted(bad_idx)[:5]],
                    "bounds": list(DOMAIN_BOUNDS.get(c, ())),
                },
                cells=[(i, c) for i in sorted(bad_idx)],
            ))
    return out


# --------------------------------------------------------------- 4 类型/格式不一致
def detect_format_issues(df: pd.DataFrame) -> list[Finding]:
    out: list[Finding] = []
    n = len(df)
    for c in df.columns:
        vals = df[c].tolist()
        nonmiss = [(i, v) for i, v in enumerate(vals) if not is_missing_token(v)]
        if not nonmiss:
            continue
        bad: list[int] = []
        kinds: set[str] = set()

        n_num = sum(1 for _, v in nonmiss if parse_number(v) is not None)
        n_raw = sum(1 for _, v in nonmiss if re.match(r"^-?\d+(\.\d+)?$", str(v).strip()))
        n_thou = sum(1 for _, v in nonmiss if looks_thousand_separated(v))

        # (a) 数值列里混入非数值
        if 0 < n_num < len(nonmiss) and n_num / len(nonmiss) >= 0.5:
            for i, v in nonmiss:
                if parse_number(v) is None:
                    bad.append(i)
            kinds.add("数值列混入非数值文本")
        # (b) 千分位
        if n_thou:
            for i, v in nonmiss:
                if looks_thousand_separated(v):
                    bad.append(i)
            kinds.add("千分位分隔符")
        # (c) 全角数字/全角空白
        for i, v in nonmiss:
            s = str(v)
            if any(ch in s for ch in "０１２３４５６７８９") or "\u3000" in s:
                bad.append(i)
                kinds.add("全角字符")
        # (d) 首尾空白
        ws = [i for i, v in nonmiss if str(v) != str(v).strip()]
        if ws:
            bad.extend(ws)
            kinds.add("首尾空白")
        # (d2) 数值带单位后缀："171 min" / "5日" / "89 days" —— 能被 parse_number 解析出数，
        # 所以不会被 (a) 抓到，但它确实是"数值列混入了非数值文本"。
        for i, v in nonmiss:
            s = str(v).strip()
            if s and re.match(r"^-?\d[\d,]*(\.\d+)?\s*[^\d\s.]", s) and parse_number(s) is not None:
                bad.append(i)
                kinds.add("数值带单位后缀")
        # (d3) 布尔/标签类取值写法混杂：yes/YES/1 语义同一但写法不同，
        # 只标出与"同类里最常见写法"不一致的那部分（不能整列标，否则 yes 行全被判异常）。
        LABEL_CLASS = {"yes": 1, "no": 0, "1": 1, "0": 0, "y": 1, "n": 0,
                       "true": 1, "false": 0, "是": 1, "否": 0}
        classes = [LABEL_CLASS.get(str(v).strip().lower()) for _, v in nonmiss]
        if all(c is not None for c in classes) and len(set(classes)) == 2:
            per: dict[int, dict[str, int]] = {}
            for (i, v), cl in zip(nonmiss, classes):
                raw = str(v).strip()
                per.setdefault(cl, {})
                per[cl][raw] = per[cl].get(raw, 0) + 1
            canon_c = {cl: sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                       for cl, d in per.items()}
            for (i, v), cl in zip(nonmiss, classes):
                if str(v).strip() != canon_c[cl]:
                    bad.append(i)
            kinds.add("布尔/标签取值写法混杂")
        # (e) 类别列大小写不一致
        if n_num == 0 or n_num / len(nonmiss) < 0.5:
            low_map: dict[str, dict[str, int]] = {}
            for i, v in nonmiss:
                key = str(v).strip().lower()
                var = str(v).strip()
                low_map.setdefault(key, {})
                low_map[key][var] = low_map[key].get(var, 0) + 1
            inconsistent = {k: v for k, v in low_map.items() if len(v) > 1}
            if inconsistent:
                # 规范形 = 该类别下出现次数最多的那个写法（并列时取字典序最小），
                # 而不是 sorted(...)[0]：否则 {APR, Apr, apr} 里占多数的写法反被判异常。
                canon = {k: sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                         for k, v in inconsistent.items()}
                for i, v in nonmiss:
                    key = str(v).strip().lower()
                    if key in canon and str(v).strip() != canon[key]:
                        bad.append(i)
                kinds.add("类别大小写/拼写不一致")

        if bad:
            bad = sorted(set(bad))
            out.append(Finding(
                problem_type="format", field=c, severity=_severity(len(bad) / n),
                n_affected=len(bad), total=n, rate=round(len(bad) / n, 4),
                method="类型混杂 + 千分位 + 全角 + 空白 + 大小写一致性检查",
                evidence={
                    "kinds": sorted(kinds),
                    "sample": [str(df.at[i, c])[:20] for i in bad[:5]],
                    "numeric_ratio": round(n_num / len(nonmiss), 3),
                },
                cells=[(i, c) for i in bad],
            ))
    return out


# --------------------------------------------------------------- 5 字段冲突
def detect_conflicts(df: pd.DataFrame, ref: pd.DataFrame | None = None,
                     match_map: dict[int, int] | None = None) -> list[Finding]:
    out: list[Finding] = []
    n = len(df)

    # (a) 年龄 与 开户年份 互推冲突：开户年份 - 出生年份(=2026-age) 必须落在 [18, age]
    if "age" in df.columns and "开户年份" in df.columns:
        age = pd.Series([parse_number(v) for v in df["age"].tolist()], index=df.index, dtype="float64")
        yr = pd.Series([parse_number(v) for v in df["开户年份"].tolist()], index=df.index, dtype="float64")
        birth = 2026 - age
        implied_open_age = yr - birth
        inconsistent = ~(
            (implied_open_age >= 18) & (implied_open_age <= age) & (yr <= 2026) & age.notna() & yr.notna()
        )
        rows = [int(i) for i in df.index[inconsistent.fillna(False)]]
        if rows:
            out.append(Finding(
                problem_type="conflict", field="age|开户年份",
                severity=_severity(len(rows) / n), n_affected=len(rows), total=n,
                rate=round(len(rows) / n, 4),
                method="字段间互推：开户年份 - (2026-age) 应落在 [18, age] 区间内",
                evidence={"kind": "self_inconsistent",
                          "sample": [(str(df.at[i, "age"]), str(df.at[i, "开户年份"])) for i in rows[:5]]},
                cells=[(i, "开户年份") for i in rows], rows=rows,
            ))

    # (b) 跨表：同一实体的教育水平不一致
    if ref is not None and match_map:
        col_a, col_b = "education", "教育水平(档案)"
        if col_a in df.columns and col_b in ref.columns:
            rows = []
            for mi, ri in match_map.items():
                a, b = df.at[mi, col_a], ref.at[ri, col_b]
                if is_missing_token(a) or is_missing_token(b):
                    continue
                if str(a).strip().lower() != str(b).strip().lower():
                    rows.append(mi)
            if rows:
                out.append(Finding(
                    problem_type="conflict", field=f"{col_a}|{col_b}",
                    severity=_severity(len(rows) / n), n_affected=len(rows), total=n,
                    rate=round(len(rows) / n, 4),
                    method="跨表字段一致性校验（主表 education vs 档案表 教育水平）",
                    evidence={"kind": "cross_table",
                              "sample": [(str(df.at[i, col_a]), str(ref.at[match_map[i], col_b])) for i in rows[:5]]},
                    cells=[(i, col_a) for i in rows], rows=rows,
                ))

    # (c) 同一客户编号出现多次但属性不一致
    if "客户编号" in df.columns:
        g = df.groupby(df["客户编号"].astype(str).str.strip())
        rows = []
        for _, grp in g:
            if len(grp) > 1 and grp.drop_duplicates().shape[0] > 1:
                rows.extend([int(i) for i in grp.index])
        if rows:
            out.append(Finding(
                problem_type="conflict", field="客户编号",
                severity=_severity(len(rows) / n), n_affected=len(rows), total=n,
                rate=round(len(rows) / n, 4),
                method="主键重复但属性不一致（同一客户编号多套记录）",
                evidence={"kind": "duplicate_key", "sample": sorted(set(rows))[:5]},
                rows=sorted(set(rows)),
            ))
    return out


# --------------------------------------------------------------- 6 标签错误
def detect_label_errors(df: pd.DataFrame, target: str, seed: int = 20251231) -> list[Finding]:
    """用"模型自洽性"找可疑标签：交叉验证的折外预测与标签严重背离的行。

    这不是真值，只是嫌疑排序；修复阶段只处理置信度极高的一小部分，
    并在运行记录里保留完整证据与人工复核入口。
    """
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import StratifiedKFold
    except Exception:
        return []
    if target not in df.columns:
        return []
    # 检测阶段可能还没做格式标准化（YES/Yes/ yes /1/0 混用），这里先做检测器自身的归一化，
    # 否则 uniq 会多于 2 个、整项检测被跳过（上一轮 label_error 恒为 n_affected=0 的原因）。
    LABEL_MAP = {"yes": 1, "no": 0, "1": 1, "0": 0, "y": 1, "n": 0,
                 "true": 1, "false": 0, "是": 1, "否": 0}
    mapped = df[target].map(lambda v: LABEL_MAP.get(str(v).strip().lower()))
    if mapped.isna().any() or mapped.nunique() != 2:
        bad = sorted({str(v) for v, m in zip(df[target].tolist(), mapped.tolist())
                      if m != m or m is None})[:6]
        return [Finding(
            problem_type="label_error", field=target, severity="none", n_affected=0,
            total=len(df), rate=0.0, method="跳过：目标列存在无法归一的取值（需先做格式修复）",
            evidence={"unmapped_sample": bad},
        )]
    y = mapped.astype(int).values

    feats = pd.DataFrame(index=df.index)
    for c in df.columns:
        if c == target:
            continue
        nums = pd.Series([parse_number(v) for v in df[c].tolist()], index=df.index, dtype="float64")
        if nums.notna().mean() > 0.8:
            feats[c] = nums.fillna(nums.median())
        else:
            codes = df[c].astype(str).str.strip().str.lower().astype("category").cat.codes
            feats[c] = codes
    X = feats.replace([np.inf, -np.inf], np.nan).fillna(0.0).values

    oof = np.zeros(len(y))
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    for tr, te in skf.split(X, y):
        m = HistGradientBoostingClassifier(max_iter=120, random_state=seed)
        m.fit(X[tr], y[tr])
        oof[te] = m.predict_proba(X[te])[:, 1]

    p_true = np.where(y == 1, oof, 1 - oof)
    susp = np.where(p_true < 0.15)[0]
    rows = [int(i) for i in susp]
    n = len(df)
    return [Finding(
        problem_type="label_error", field=target,
        severity=_severity(len(rows) / n) if rows else "none",
        n_affected=len(rows), total=n, rate=round(len(rows) / n, 4),
        method="5 折折外预测置信度 < 0.15 视为可疑标签（模型自洽性检验）",
        evidence={"threshold": 0.15, "sample_rows": rows[:10],
                  "sample_conf": [round(float(p_true[i]), 4) for i in rows[:10]]},
        rows=rows,
    )]


# --------------------------------------------------------------- 7 类别不平衡
def detect_imbalance(df: pd.DataFrame, target: str) -> list[Finding]:
    if target not in df.columns:
        return []
    vc = df[target].map(lambda v: str(v).strip().lower()).value_counts()
    if len(vc) < 2:
        return []
    ratio = vc.min() / vc.max()
    return [Finding(
        problem_type="imbalance", field=target,
        severity="high" if ratio < 0.1 else ("medium" if ratio < 0.3 else "low"),
        n_affected=int(vc.min()), total=int(vc.sum()), rate=round(float(ratio), 4),
        method="少数类/多数类 样本量比值",
        evidence={"counts": {str(k): int(v) for k, v in vc.items()},
                  "minority_ratio": round(100 * float(vc.min()) / float(vc.sum()), 2)},
    )]


# --------------------------------------------------------------- 8 跨表实体匹配
def detect_entity_match(df: pd.DataFrame, ref: pd.DataFrame) -> tuple[list[Finding], dict[int, int]]:
    """实体匹配：ID 规范化 → 候选召回 → 姓名/证件/城市 加权比对。

    返回 (findings, match_map)，match_map 为主表行号 → 档案表行号。
    """
    from rapidfuzz import fuzz

    def norm_id(v: Any) -> str:
        s = str(v).strip().lower().translate(FULLWIDTH_DIGITS)
        s = re.sub(r"[^0-9a-z]", "", s)
        s = re.sub(r"^(cus|c|cust)", "", s)
        return s.lstrip("0")

    def norm_name(v: Any) -> str:
        return re.sub(r"[\s\u3000·.]", "", str(v).strip().lower())

    id_a = df["客户编号"].map(norm_id) if "客户编号" in df.columns else pd.Series(dtype=str)
    id_b = ref["客户ID"].map(norm_id)
    # 姓名列名兼容
    name_a_col = next((c for c in ("客户姓名", "姓名") if c in df.columns), None)
    name_b_col = next((c for c in ("客户姓名", "姓名") if c in ref.columns), None)
    name_a = df[name_a_col].map(norm_name) if name_a_col else pd.Series([""] * len(df))
    name_b = ref[name_b_col].map(norm_name) if name_b_col else pd.Series([""] * len(ref))

    index_b: dict[str, list[int]] = {}
    for i, v in enumerate(id_b.tolist()):
        index_b.setdefault(v, []).append(i)

    match_map: dict[int, int] = {}
    ambiguous: list[int] = []
    unmatched: list[int] = []
    low_conf: list[tuple[int, float]] = []

    for i in range(len(df)):
        key = id_a.iloc[i]
        cands = index_b.get(key, [])
        if not cands:
            unmatched.append(i)
            continue
        if len(cands) == 1:
            j = cands[0]
            sim = fuzz.ratio(name_a.iloc[i], name_b.iloc[j]) / 100 if name_a.iloc[i] else 0.0
            match_map[i] = j
            if sim < 0.6:
                low_conf.append((i, sim))
        else:
            # 一对多：用姓名做消歧
            best = max(cands, key=lambda j: fuzz.ratio(name_a.iloc[i], name_b.iloc[j]))
            match_map[i] = int(best)
            ambiguous.append(i)

    out: list[Finding] = []
    n = max(len(df), 1)
    na = len(unmatched)
    if na:
        out.append(Finding(
            problem_type="entity_match", field="客户编号",
            severity=_severity(na / n), n_affected=na, total=n, rate=round(na / n, 4),
            method="ID 规范化（去前缀/前导零/非字母数字）+ 姓名模糊比对，未能召回候选",
            evidence={"sample_rows": unmatched[:10]},
            rows=unmatched,
        ))
    if ambiguous:
        out.append(Finding(
            problem_type="entity_match", field="客户编号",
            severity=_severity(len(ambiguous) / n), n_affected=len(ambiguous), total=n,
            rate=round(len(ambiguous) / n, 4),
            method="一对多候选，已按姓名相似度消歧（存在歧义风险）",
            evidence={"sample_rows": ambiguous[:10], "n_one_to_many": len(ambiguous)},
            rows=ambiguous,
        ))
    if low_conf:
        out.append(Finding(
            problem_type="entity_match", field="客户姓名",
            severity="low", n_affected=len(low_conf), total=n,
            rate=round(len(low_conf) / n, 4),
            method="ID 命中但姓名相似度 < 0.6，疑似错配",
            evidence={"sample": [(i, round(s, 3)) for i, s in low_conf[:10]]},
            rows=[i for i, _ in low_conf],
        ))
    return out, match_map


# --------------------------------------------------------------- 汇总入口
def run_all_detectors(df: pd.DataFrame, ref: pd.DataFrame | None = None,
                      target: str = "y") -> tuple[list[Finding], dict[int, int]]:
    findings: list[Finding] = []
    match_map: dict[int, int] = {}
    findings += detect_missing(df)
    findings += detect_duplicates(df)
    findings += detect_outliers(df)
    findings += detect_format_issues(df)
    if ref is not None:
        ent_findings, match_map = detect_entity_match(df, ref)
        findings += ent_findings
    findings += detect_conflicts(df, ref, match_map)
    findings += detect_label_errors(df, target)
    findings += detect_imbalance(df, target)
    return findings, match_map
