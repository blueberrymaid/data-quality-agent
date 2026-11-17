"""评估：用注入缺陷的真值，量化"检测得准不准、修得对不对"。

三个层面：
1. 检测评估 —— 检测结果 vs 真值，按问题类型算 precision/recall/F1；
2. 修复评估 —— 修复后的单元格 vs 干净基准，按类型算"精确还原率"或"近似正确率"；
3. 下游评估 —— 同一建模流程在脏数据与清洗后数据上的指标差（在 classify.py）。

为什么要按类型分开：格式和重复是**可精确还原**的（应该接近 100%），
缺失/异常/冲突/标签只能近似（应该明确标为"近似"，不能用同一条线评判）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

KEY = "客户编号"


def _key_of(df: pd.DataFrame, row: int) -> str | None:
    if row is None or row < 0 or row >= len(df):
        return None
    try:
        return str(df.iloc[row][KEY]).strip()
    except Exception:  # noqa: BLE001
        return None


def _norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and np.isnan(v):
        return ""
    return str(v).strip().lower()


def _num(v) -> float | None:
    from ..tools.profiling import parse_number
    return parse_number(v)


# --------------------------------------------------------------- 1 检测评估
def detection_metrics(findings: list, truth: dict, dirty: pd.DataFrame) -> dict:
    """把检测到的 (行,列) 与真值集合求交并比。

    注意：findings 从 JSON 反序列化后可能没有 ``rows`` 字段（只保留 ``cells``），
    所以这里对两种形态都做兼容，否则会把"检测到了"错算成"一条都没检测到"。
    """
    # 近重复/精确重复副本行：注入器为了制造"近似重复"，会给副本行加首尾空格/改大小写。
    # 这些副本本身属于"重复数据"问题，去重后即消失，所以不把它们计入格式/缺失检测的误报。
    # 必须用 _origin 的类别来定位副本（kind != "base"），不能取组的首个元素——
    # 分组按最终表行序建立，组首元素可能就是副本行，会把"基准行"误当副本过滤掉。
    origin = truth.get("_origin") or []
    if origin:
        dup_copy_rows = {p for p, item in enumerate(origin)
                         if isinstance(item, (list, tuple)) and str(item[0]) != "base"}
    else:
        dup_groups = (truth.get("duplicates") or {}).get("groups") or []
        dup_copy_rows = {int(p) for g in dup_groups for p in list(g)[1:]}

    pred: dict[str, set] = {}
    for f in findings:
        ptype = f.problem_type
        cells = [c for c in (getattr(f, "cells", None) or []) if c]
        if cells:
            # 必须用单元格自己的列名：conflict 的 f.field 是 "age|开户年份" 这种复合名，
            # 直接拿 f.field 去和真值比会把全部冲突预测算成误报（曾出现 pred 5112 / tp 22）。
            for c in cells:
                pred.setdefault(ptype, set()).add((int(c[0]), str(c[1])))
        else:
            for r in (getattr(f, "rows", None) or []):
                pred.setdefault(ptype, set()).add((int(r), f.field))

    n_pred_raw = {k: len(v) for k, v in pred.items()}
    if dup_copy_rows:
        for k in list(pred):
            pred[k] = {x for x in pred[k] if x[0] not in dup_copy_rows}

    def truth_set(ptype: str) -> set:
        items = truth.get(ptype) or []
        out = set()
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out.add((int(item[0]), str(item[1])))
        return out

    mapping = {"missing": "missing", "outlier": "outliers", "format": "format",
               "conflict": "conflicts"}
    per_type = {}
    for ptype, tkey in mapping.items():
        t = truth_set(tkey)
        p = pred.get(ptype, set())
        tp = len(t & p)
        prec = tp / len(p) if p else None
        rec = tp / len(t) if t else None
        f1 = (2 * prec * rec / (prec + rec)) if prec and rec else 0.0
        per_type[ptype] = {
            "n_truth": len(t), "n_pred_cells": len(p), "tp": tp,
            "precision": None if prec is None else round(prec, 4),
            "recall": None if rec is None else round(rec, 4),
            "f1": round(f1, 4),
            "extra_pred": len(p - t), "missed": len(t - p),
            "excluded_near_dup_cells": n_pred_raw.get(ptype, 0) - len(p),
        }

    # 冲突按性质分口径：自洽性冲突（可修）/ 主键重复（可修）/ 跨表不一致（按策略只记录不改）
    t_conf = truth_set("conflicts")
    by_col: dict[str, set] = {}
    for cell in pred.get("conflict", set()):
        by_col.setdefault(cell[1], set()).add(cell)
    per_type["conflict_by_kind"] = {}
    for col, cells in sorted(by_col.items()):
        tc = {x for x in t_conf if x[1] == col}
        kind = {"开户年份": "self_inconsistent", "客户编号": "duplicate_key"}.get(col, "cross_table")
        per_type["conflict_by_kind"][kind] = {
            "column": col, "n_pred": len(cells), "n_truth": len(tc), "tp": len(cells & tc),
            "precision": round(len(cells & tc) / len(cells), 4) if cells else None,
            "recall": round(len(cells & tc) / len(tc), 4) if tc else None,
        }

    # 异常值额外给一个"硬口径"：只看业务边界越界的那些预测（统计口径本来就会多报）。
    # 不能直接用 finding 的 evidence.kind：一条 finding 只要该列有任一越界就整体标 domain_violation，
    # 会把 IQR 口径的单元格一起算进硬集合（曾出现 6312 个硬预测 vs 真值 904）。
    from ..tools.quality import DOMAIN_BOUNDS
    hard = set()
    for r, c in pred.get("outlier", set()):
        bounds = DOMAIN_BOUNDS.get(c)
        if not bounds or c not in dirty.columns or r < 0 or r >= len(dirty):
            continue
        v = _num(dirty.at[r, c])
        if v is None:
            continue
        lo, hi = bounds
        if v < lo or v > hi:
            hard.add((r, c))
    if "outlier" in per_type:
        t_out = truth_set("outliers")
        tp_h = len(t_out & hard)
        per_type["outlier"]["hard_domain_pred"] = len(hard)
        per_type["outlier"]["hard_domain_tp"] = tp_h
        per_type["outlier"]["hard_domain_precision"] = (
            round(tp_h / len(hard), 4) if hard else None)
        per_type["outlier"]["hard_domain_recall"] = (
            round(tp_h / len(t_out), 4) if t_out else None)

    # 重复：真值是"应删除的行组"，预测是"实际删了多少行" → 用集合比较能力有限，改看行数
    dup_truth = truth.get("duplicates", {}) or {}
    per_type["duplicate"] = {
        "n_truth_groups": len(dup_truth.get("groups", []) or []),
        "n_truth_extra_rows": dup_truth.get("n_extra_rows"),
        "note": "重复检测按行组评估，修复层面单独统计删除条数",
    }
    # 标签错误：真值是 (行号,) 列表
    lab_truth = {(int(x[0]) if isinstance(x, (list, tuple)) else int(x))
                 for x in (truth.get("label_errors") or [])}
    lab_pred = set()
    for f in findings:
        if f.problem_type == "label_error":
            lab_pred |= {int(r) for r in (f.rows or [])}
    tp = len(lab_truth & lab_pred)
    per_type["label_error"] = {
        "n_truth": len(lab_truth), "n_pred": len(lab_pred), "tp": tp,
        "precision": round(tp / len(lab_pred), 4) if lab_pred else None,
        "recall": round(tp / len(lab_truth), 4) if lab_truth else None,
    }

    # 实体匹配：精确率/召回率
    em_truth = (truth.get("entity") or {}).get("true_map") or {}
    em_truth = {int(k): int(v) for k, v in em_truth.items()}
    return {"per_type": per_type, "entity_truth_note":
            f"真值匹配 {len(em_truth)} 对，预测见修复评估"}


def entity_match_scores(match_map: dict, truth: dict,
                        dirty: pd.DataFrame | None = None,
                        ref: pd.DataFrame | None = None) -> dict:
    """跨表实体匹配评估。

    真值 true_map 用的是"扰动前"的行号，写入磁盘后主表被重排过，行号不再可比；
    因此这里改用**结构化口径**：主表 客户编号 与档案表 客户ID 归一化后相等，
    就认为这是一对真匹配（档案表只对 ID 做了书写扰动/错字，没有换人）。
    数字打错一位的那些行天然无法归一化命中，属于"不可召回"，在报告里单独说明。
    """
    def norm_id(v) -> str:
        s = re.sub(r"\D", "", str(v or ""))
        return s.lstrip("0")

    out: dict = {}
    if dirty is not None and ref is not None and "客户编号" in dirty.columns and "客户ID" in ref.columns:
        main_norm = [norm_id(v) for v in dirty["客户编号"].tolist()]
        ref_norm = [norm_id(v) for v in ref["客户ID"].tolist()]

        # 归一化后能对上的真匹配对数（一对多时一个主行可对应多个档案行，这里取可匹配主行集合）
        ref_by_id: dict[str, list[int]] = {}
        for ri, k in enumerate(ref_norm):
            if k:
                ref_by_id.setdefault(k, []).append(ri)
        matchable_main = {mi for mi, k in enumerate(main_norm) if k and k in ref_by_id}

        pred_pairs = {(int(k), int(v)) for k, v in (match_map or {}).items()}
        tp = sum(1 for mi, ri in pred_pairs
                 if mi < len(main_norm) and ri < len(ref_norm)
                 and main_norm[mi] and main_norm[mi] == ref_norm[ri])
        prec = tp / len(pred_pairs) if pred_pairs else None
        covered = {mi for mi, _ in pred_pairs} & matchable_main
        rec = len(covered) / len(matchable_main) if matchable_main else None
        f1 = (2 * prec * rec / (prec + rec)) if prec and rec else 0.0
        out = {
            "n_main_rows": len(dirty), "n_ref_rows": len(ref),
            "n_matchable_main": len(matchable_main),
            "n_pred": len(pred_pairs), "tp": tp,
            "precision": None if prec is None else round(prec, 4),
            "recall": None if rec is None else round(rec, 4),
            "f1": round(f1, 4),
            "coverage_of_main": round(len({mi for mi, _ in pred_pairs}) / max(len(dirty), 1), 4),
            "note": "真值按 ID 归一化后相等判定；档案表里数字被打错一位的行不可召回",
        }
        return out

    em = (truth.get("entity") or {}).get("true_map") or {}
    truth_pairs = {(int(k), int(v)) for k, v in em.items()}
    pred_pairs = {(int(k), int(v)) for k, v in (match_map or {}).items()}
    tp = len(truth_pairs & pred_pairs)
    prec = tp / len(pred_pairs) if pred_pairs else None
    rec = tp / len(truth_pairs) if truth_pairs else None
    f1 = (2 * prec * rec / (prec + rec)) if prec and rec else 0.0
    return {"n_truth": len(truth_pairs), "n_pred": len(pred_pairs), "tp": tp,
            "precision": None if prec is None else round(prec, 4),
            "recall": None if rec is None else round(rec, 4), "f1": round(f1, 4)}


# --------------------------------------------------------------- 2 修复评估
def repair_metrics(dirty: pd.DataFrame, processed: pd.DataFrame, clean: pd.DataFrame,
                   truth: dict) -> dict:
    proc = processed.copy()
    proc[KEY] = proc[KEY].astype(str).str.strip()
    proc = proc.drop_duplicates(subset=[KEY]).set_index(KEY)
    base = clean.copy()
    base[KEY] = base[KEY].astype(str).str.strip()
    base = base.drop_duplicates(subset=[KEY]).set_index(KEY)

    def check(ptype: str, tkey: str) -> dict:
        cells = truth.get(tkey) or []
        total = exact = approx = kept = 0
        for item in cells:
            if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                continue
            row, col = int(item[0]), str(item[1])
            k = _key_of(dirty, row)
            if k is None or k not in proc.index or col not in proc.columns:
                continue
            kept += 1
            total += 1
            got, want = proc.at[k, col], base.at[k, col] if k in base.index else None
            gn, wn = _num(got), _num(want)
            if gn is not None and wn is not None:
                if abs(gn - wn) <= 1e-6:
                    exact += 1
                elif abs(gn - wn) <= max(1.0, abs(wn) * 0.2):
                    approx += 1
            elif _norm(got) == _norm(want):
                exact += 1
            elif _norm(got) and _norm(want) and _norm(got) in _norm(want):
                approx += 1
        return {"n_truth_cells": len(cells), "n_checked": total,
                "exact_match": exact, "approx_match": approx,
                "exact_rate": round(exact / total, 4) if total else None,
                "exact_or_approx_rate": round((exact + approx) / total, 4) if total else None,
                "unrecoverable_overwritten": total - exact - approx}

    out = {
        "format": check("format", "format"),
        "missing": check("missing", "missing"),
        "outlier": check("outlier", "outliers"),
        "conflict": check("conflict", "conflicts"),
    }

    # 异常值另设"业务口径"：越界值修完后必须回到界内。原值不可还原，exact_rate 天然很低，
    # 单看 exact_rate 会误读成"修得几乎全错"（0.0996），必须同时给出在界内率。
    from ..tools.quality import DOMAIN_BOUNDS
    n_checked = n_in = 0
    for item in (truth.get("outliers") or []):
        row, col = int(item[0]), str(item[1])
        k = _key_of(dirty, row)
        bounds = DOMAIN_BOUNDS.get(col)
        if k is None or k not in proc.index or col not in proc.columns or not bounds:
            continue
        v = _num(proc.at[k, col])
        if v is None:
            continue
        n_checked += 1
        if bounds[0] <= v <= bounds[1]:
            n_in += 1
    out["outlier"]["within_bounds_after"] = {
        "n_checked": n_checked, "n_in_bounds": n_in,
        "rate": round(n_in / n_checked, 4) if n_checked else None,
        "note": "「越界已修正」口径：只看修复后是否回到业务边界内，原始值本身不可还原",
    }

    # 重复：用"同一客户编号在脏数据里的出现次数 vs 处理后"来评估，
    # 因为重复行与源行共享同一个主键，集合差集会永远得到 0（曾经踩过这个坑）。
    dup_truth = truth.get("duplicates", {}) or {}
    true_extra = set()
    for group in dup_truth.get("groups", []) or []:
        rs = [int(x) for x in group]
        true_extra |= set(rs[1:])          # 保留第一条，其余应删
    dirty_keys = dirty[KEY].astype(str).str.strip()
    dm = dirty_keys.value_counts()
    pm = proc.index.to_series().value_counts().reindex(dm.index).fillna(0)
    removed_per_key = (dm - pm).clip(lower=0)
    removed_keys = set(removed_per_key[removed_per_key > 0].index)
    truth_dup_keys = set(dm[dm > 1].index)
    hit = len(removed_keys & truth_dup_keys)
    out["duplicate"] = {
        "n_truth_duplicate_groups": len(truth_dup_keys),
        "n_truth_extra_rows": len(true_extra),
        "rows_before": len(dirty), "rows_after": len(proc),
        "rows_removed": int(removed_per_key.sum()),
        "n_keys_deduplicated": len(removed_keys),
        "removed_that_were_true_dups": hit,
        "precision": round(hit / len(removed_keys), 4) if removed_keys else None,
        "recall": round(hit / len(truth_dup_keys), 4) if truth_dup_keys else None,
    }

    # 标签：先找出"被改过标签"的主键，再与真值标签错误集合求交并比
    lab_truth = {(int(x[0]) if isinstance(x, (list, tuple)) else int(x))
                 for x in (truth.get("label_errors") or [])}
    lab_keys = {_key_of(dirty, r) for r in lab_truth}
    lab_keys.discard(None)
    dirty_y = dirty.assign(**{KEY: dirty_keys}).drop_duplicates(subset=[KEY]).set_index(KEY)["y"]
    changed = set()
    for k in proc.index:
        if k in dirty_y.index and _norm(proc.at[k, "y"]) != _norm(dirty_y.at[k]):
            changed.add(k)
    if lab_keys:
        tp_lab = len(changed & lab_keys)
        out["label_error"] = {
            "n_truth": len(lab_keys), "n_changed": len(changed),
            "tp": tp_lab,
            "precision": round(tp_lab / len(changed), 4) if changed else None,
            "recall": round(tp_lab / len(lab_keys), 4),
            "note": "precision = 改动的标签里有多少真的是注入的错标；recall = 注入的错标被改回多少",
        }
    return out


# --------------------------------------------------------------- 汇总
def evaluate_all(dirty: pd.DataFrame, processed: pd.DataFrame, clean: pd.DataFrame,
                 truth: dict, findings: list, match_map: dict,
                 downstream: dict | None = None,
                 ref: pd.DataFrame | None = None) -> dict:
    det = detection_metrics(findings, truth, dirty)
    det["entity_match"] = entity_match_scores(match_map, truth, dirty=dirty, ref=ref)
    rep = repair_metrics(dirty, processed, clean, truth)
    return {
        "detection": det,
        "repair": rep,
        "downstream": downstream or {},
        "truth_counts": truth.get("counts", {}),
    }
