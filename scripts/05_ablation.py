"""05 消融实验：按计划顺序逐步累加修复动作，看每一步对"质量残留"和"下游任务"的贡献。

问题意识：全量修复之后，下游指标到底是被哪一步拉起来的？哪一步可能是净损失？
做法：从脏数据出发，按 orchestrator 的计划顺序做前缀累加，每个前缀都
  1) 重跑检测器统计"硬口径"残留（缺失/格式/重复/越界/自洽冲突）；
  2) 用与主流程完全相同的建模流程（5 折 CV + 逻辑回归/GBDT 取优）算下游指标。
输出：docs/消融实验.json
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

import pandas as pd

from src.config import (DOCS_DIR, INJ_MAIN, INJ_REF, SEED, TARGET, RUNS_DIR)
from src.pipeline import classify
from src.tools import quality, repair

ORDER = ["enrich_from_ref", "standardize_format", "drop_duplicates", "impute_missing",
         "fix_outliers", "resolve_conflicts", "fix_label_errors"]


def hard_residuals(df: pd.DataFrame, ref: pd.DataFrame, match_map: dict) -> dict:
    """只统计硬口径残留：缺失标记、格式异常、跨表不可匹配、越界值、自洽冲突、重复组。"""
    out = {}
    miss = quality.detect_missing(df)
    out["missing_cells"] = sum(f.n_affected for f in miss)
    fmt = quality.detect_format_issues(df)
    out["format_cells"] = sum(f.n_affected for f in fmt)
    dup = quality.detect_duplicates(df)
    out["duplicate_rows_flagged"] = sum(f.n_affected for f in dup)
    # 越界：按业务边界逐格重判，避免把 IQR 软口径算进来
    oob = 0
    from src.tools.profiling import parse_number
    from src.tools.quality import DOMAIN_BOUNDS
    for col, (lo, hi) in DOMAIN_BOUNDS.items():
        if col not in df.columns:
            continue
        nums = pd.Series([parse_number(v) for v in df[col].tolist()], dtype="float64")
        oob += int(((nums < lo) | (nums > hi)).fillna(False).sum())
    out["out_of_bounds_cells"] = oob
    conf = quality.detect_conflicts(df, ref, _kept(match_map, df))
    out["self_inconsistent_conflicts"] = sum(
        f.n_affected for f in conf if (f.evidence or {}).get("kind") == "self_inconsistent")
    return out


def _kept(match_map: dict, df: pd.DataFrame) -> dict:
    """去重后行号不再连续：只保留仍然存在的行，否则 df.at[mi] 会 KeyError。"""
    keep = set(df.index)
    return {k: v for k, v in match_map.items() if k in keep}


def apply_action(name: str, df: pd.DataFrame, ref: pd.DataFrame, match_map: dict) -> pd.DataFrame:
    mm = _kept(match_map, df)
    if name == "enrich_from_ref":
        out, _ = repair.enrich_from_ref(df, ref, mm)
    elif name == "standardize_format":
        out, _ = repair.standardize_format(df)
    elif name == "drop_duplicates":
        out, _ = repair.drop_duplicates(df, near=True)
    elif name == "impute_missing":
        out, _ = repair.impute_missing(df, strategy="group_mode")
    elif name == "fix_outliers":
        out, _ = repair.fix_outliers(df, strategy="clip")
    elif name == "resolve_conflicts":
        out, _ = repair.resolve_conflicts(df, ref, mm, policy="recompute_from_age")
    elif name == "fix_label_errors":
        out, _ = repair.fix_label_errors(df, TARGET, threshold=0.02, max_rows=500)
    else:
        raise ValueError(name)
    return out


def main() -> None:
    print("[消融] 载入脏数据与档案表…")
    dirty = pd.read_csv(INJ_MAIN, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    ref = pd.read_csv(INJ_REF, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    print("[消融] 计算实体匹配映射（脏数据行号基准）…")
    _findings, match_map = quality.detect_entity_match(dirty, ref)
    print(f"  匹配 {len(match_map)} 对")

    arms = [("dirty", [])] + [(f"+{ORDER[i]}", ORDER[:i + 1]) for i in range(len(ORDER))]
    rows = []
    baseline = None
    for label, actions in arms:
        df = dirty.copy()
        for a in actions:
            df = apply_action(a, df, ref, match_map)
        resid = hard_residuals(df, ref, match_map)
        full = classify.train_and_evaluate(df, label, TARGET, seed=SEED)
        if "error" in full:
            print(f"  {label:22s} 行数={len(df):6d} 建模失败：{full['error']}")
            rows.append({"arm": label, "n_actions": len(actions), "rows": int(len(df)),
                         "residuals": resid, "error": full["error"]})
            continue
        cur = full["best"]
        keys = ("accuracy", "precision", "recall", "f1", "roc_auc", "cv_f1_mean", "cv_f1_std", "threshold")
        if baseline is None:
            baseline = cur
        delta = {k: round(float(cur[k]) - float(baseline[k]), 4) for k in keys}
        rows.append({
            "arm": label,
            "n_actions": len(actions),
            "rows": int(len(df)),
            "residuals": resid,
            "best_model": full["best_model"],
            "downstream": {k: round(float(cur[k]), 4) for k in keys},
            "delta_vs_dirty": delta,
        })
        print(f"  {label:22s} 行数={len(df):6d} f1={cur['f1']:.4f} auc={cur['roc_auc']:.4f} 残留={resid}")

    payload = {"seed": SEED, "order": ORDER, "arms": rows,
               "note": "delta_vs_dirty 为相对脏数据基线的指标差；residuals 只含硬口径缺陷"}
    out = DOCS_DIR / "消融实验.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[输出] {out}")


if __name__ == "__main__":
    main()
