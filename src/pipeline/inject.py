"""缺陷注入器：把干净的公开数据变成"脏数据"，并同步落一份真值。

为什么需要注入：只有知道"哪些位置本来是什么"，才能定量评估智能体的
**检测准确率**与**修复准确率**，而不是只给几张截图说"看起来变好了"。

注入 8 类问题：
  1 缺失值        2 重复数据（完全重复 + 近似重复）   3 异常值/噪声
  4 类型/格式不一致  5 字段冲突      6 标签错误
  7 类别不平衡（数据本身固有，仅统计）                8 跨表实体匹配（档案表扰动）

运行：python -m src.pipeline.inject
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from ..config import (INJ_MAIN, INJ_REF, INJECT, RAW_MAIN, RAW_REF, SEED,
                      TARGET, TRUTH_DIR, TRUTH_FILE)

MISSING_FIELDS = ["education", "contact", "job", "balance", "城市", "pdays"]
OUTLIER_FIELDS = ["age", "balance", "duration", "campaign", "pdays", "day"]
MISSING_MARKS = ["", "?", "N/A", "null", "None", "。"]


def _rows_of(df: pd.DataFrame) -> list[dict]:
    return df.to_dict(orient="records")


def _as_int(v):
    """尽力取出单元格开头的整数（已被前一步格式化过的单元格也能解析）。"""
    m = re.match(r"^\s*-?\d+", str(v).replace(",", "").replace("，", "").strip())
    return int(m.group()) if m else None


def _pick(rng: random.Random, n: int, cols: list[str], touched: set,
          tries: int = 40) -> tuple[int | None, str | None]:
    """挑一个尚未被其它缺陷占用的 (行, 列)，避免同一单元格被注入两次导致真值歧义。"""
    for _ in range(tries):
        i = rng.randrange(n)
        c = rng.choice(cols)
        if (i, c) not in touched:
            touched.add((i, c))
            return i, c
    return None, None


def inject_main(df: pd.DataFrame, rng: random.Random) -> tuple[pd.DataFrame, dict]:
    """在主表上注入缺陷，返回 (注入后表, 真值)。"""
    n = len(df)
    recs = _rows_of(df)
    truth: dict = {
        "missing": [], "outliers": [], "format": [], "conflicts": [],
        "label_errors": [], "duplicates": {"groups": [], "clean_rows_of_group": []},
    }
    touched: set = set()          # 已注入缺陷的 (行号, 列名)，保证各类缺陷互不重叠

    # ------------------------------------------------ 1 缺失值（MCAR/MAR 混合）
    n_missing = int(n * INJECT.missing_ratio)
    for _ in range(n_missing):
        i, c = _pick(rng, n, MISSING_FIELDS, touched)
        if i is None:
            continue
        recs[i][c] = rng.choice(MISSING_MARKS)
        truth["missing"].append([i, c])
    # MAR：低 education 的人更容易缺 contact（与其它字段相关，不是纯随机）
    for i in range(n):
        if recs[i]["education"] == "primary" and rng.random() < 0.18:
            if (i, "contact") in touched:
                continue
            touched.add((i, "contact"))
            recs[i]["contact"] = ""
            truth["missing"].append([i, "contact"])

    # ------------------------------------------------ 3 异常值/噪声
    n_out = int(n * INJECT.outlier_ratio)
    outlier_pool = {
        "age": [-1, 0, 130, 199, 999],
        "balance": [-999999, 99999999, 123456789],
        "duration": [999999, -5, 0],
        "campaign": [9999, 0, -3],
        "pdays": [99999, -999],
        "day": [0, 32, 45, 99],
    }
    for _ in range(n_out):
        i, c = _pick(rng, n, OUTLIER_FIELDS, touched)
        if i is None:
            continue
        recs[i][c] = rng.choice(outlier_pool[c])
        truth["outliers"].append([i, c])

    # ------------------------------------------------ 4 类型/格式不一致
    n_fmt = int(n * INJECT.format_ratio)
    fmt_cols = {"thousand": ["balance"], "month_case": ["month"], "day_str": ["day"],
                "duration_unit": ["duration"], "bool_mix": [TARGET],
                "space": ["job", "education", "housing", "loan"],
                "fullwidth": ["day"], "unit_suffix": ["pdays", "previous"]}
    for _ in range(n_fmt):
        kind = rng.choice(list(fmt_cols))
        i, c = _pick(rng, n, fmt_cols[kind], touched)
        if i is None:
            continue
        if kind == "thousand":
            v = _as_int(recs[i]["balance"])
            if v is None or abs(v) < 1000:
                continue      # 三位数加千分位等于没改，会造成"不可检测的真值"，直接跳过
            recs[i]["balance"] = f"{v:,}"
            truth["format"].append([i, "balance"])
        elif kind == "month_case":
            recs[i]["month"] = str(recs[i]["month"]).upper() if rng.random() < .5 else str(recs[i]["month"]).capitalize()
            truth["format"].append([i, "month"])
        elif kind == "day_str":
            v = _as_int(recs[i]["day"])
            recs[i]["day"] = f"{v:02d}日" if (v is not None and rng.random() < .5) else f" {recs[i]['day']} "
            truth["format"].append([i, "day"])
        elif kind == "duration_unit":
            v = _as_int(recs[i]["duration"])
            recs[i]["duration"] = f"{v if v is not None else 100} min"
            truth["format"].append([i, "duration"])
        elif kind == "bool_mix":
            recs[i][TARGET] = rng.choice(["1", "YES", "Yes", "0", "NO"])
            truth["format"].append([i, TARGET])
        elif kind == "space":
            recs[i][c] = f"  {recs[i][c]} "
            truth["format"].append([i, c])
        elif kind == "fullwidth":
            recs[i]["day"] = str(recs[i]["day"]).translate(str.maketrans("0123456789", "０１２３４５６７８９"))
            truth["format"].append([i, "day"])
        elif kind == "unit_suffix":
            # 原来是写 "N/A"，但那是"缺失"的写法，检测器会判成 missing 而不是 format，
            # 造成同类缺陷跨类型标注（真值 format / 预测 missing）。改成单位后缀，语义纯正。
            v = _as_int(recs[i][c])
            recs[i][c] = f"{v if v is not None else 0} days"
            truth["format"].append([i, c])

    # ------------------------------------------------ 5 字段冲突（年龄 vs 开户年份）
    n_cf = int(n * INJECT.conflict_ratio)
    for _ in range(min(n_cf, n // 3)):
        i, _c = _pick(rng, n, ["开户年份"], touched)
        if i is None:
            continue
        age = _as_int(recs[i]["age"])
        if age is None or not (18 <= age <= 100):
            continue                    # 年龄已被异常值污染时不再叠加冲突，避免真值含义不清
        birth = 2026 - age
        if rng.random() < 0.5:
            # 开户年份早于出生后 18 年（开户时年龄为负/未成年 → 不可能）
            recs[i]["开户年份"] = birth - rng.randint(2, 20)
        else:
            # 开户年份在未来
            recs[i]["开户年份"] = 2026 + rng.randint(1, 15)
        truth["conflicts"].append([i, "开户年份"])

    # 同一客户编号多套属性（主键冲突）
    for _ in range(max(5, n // 2000)):
        i, _c = _pick(rng, n, ["客户编号"], touched)
        if i is None:
            continue
        recs[i]["客户编号"] = recs[rng.randrange(n)]["客户编号"]
        truth["conflicts"].append([i, "客户编号"])

    # ------------------------------------------------ 6 标签错误
    n_lb = int(n * INJECT.label_error_ratio)
    for _ in range(n_lb):
        i, _c = _pick(rng, n, [TARGET], touched)
        if i is None:
            continue
        recs[i][TARGET] = "no" if str(recs[i][TARGET]).strip().lower() in ("yes", "1") else "yes"
        truth["label_errors"].append(i)

    # ------------------------------------------------ 2 重复数据
    df_clean = pd.DataFrame(recs)
    n_dup = int(n * INJECT.dup_ratio)
    n_near = int(n * INJECT.near_dup_ratio)

    dup_records, origins = [], []
    picked = rng.sample(range(n), min(n_dup, n))
    for i in picked:
        dup_records.append(dict(recs[i]))
        origins.append(("exact", i))
    near_picked = rng.sample(range(n), min(n_near, n))
    for i in near_picked:
        r = dict(recs[i])
        for c in ("job", "education", "housing", "loan"):
            r[c] = f" {r[c]} "
        r["month"] = str(r["month"]).capitalize()
        dup_records.append(r)
        origins.append(("near", i))

    all_recs = recs + dup_records
    all_origins = [("base", i) for i in range(n)] + origins

    # 打乱行序（让重复行不集中在表尾，检测难度更真实）
    order = list(range(len(all_recs)))
    rng.shuffle(order)
    final = [all_recs[k] for k in order]
    final_origin = [all_origins[k] for k in order]

    out = pd.DataFrame(final)
    # 真值里的行号要跟着重排
    pos_of_base = {orig: pos for pos, (kind, orig) in enumerate(final_origin) if kind == "base"}
    for key in ("missing", "outliers", "format", "conflicts"):
        truth[key] = [[pos_of_base[i], c] for i, c in truth[key]]
    truth["label_errors"] = [pos_of_base[i] for i in truth["label_errors"]]

    # 重复组：同一原始行在最终表中的全部位置（>1 即为一组）
    groups: dict[int, list[int]] = {}
    for pos, (kind, orig) in enumerate(final_origin):
        groups.setdefault(orig, []).append(pos)
    dup_groups = [sorted(v) for v in groups.values() if len(v) > 1]
    truth["duplicates"] = {
        "groups": dup_groups,
        "n_groups": len(dup_groups),
        "n_redundant_rows": sum(len(g) - 1 for g in dup_groups),
    }
    truth["_origin"] = final_origin
    return out, truth


def inject_ref(ref: pd.DataFrame, rng: random.Random, n_main: int, seed: int) -> tuple[pd.DataFrame, dict]:
    """在档案表上注入 ID 书写差异、姓名错字、一对多重复、字段冲突。"""
    recs = _rows_of(ref)
    n = len(recs)
    info = {"id_variant_rows": [], "id_typo_rows": [], "name_typo_rows": [],
            "dup_rows": [], "edu_conflict_rows": [], "true_map": {}}

    for i in range(n):
        info["true_map"][i] = i          # 扰动前：档案表第 i 行对应主表第 i 行

    for i in range(n):
        r = recs[i]
        raw_id = str(r["客户ID"])                 # C000123
        num = re.sub(r"\D", "", raw_id)
        p = rng.random()
        if p < 0.25:
            r["客户ID"] = f"cus-{num}"             # 前缀变体
            info["id_variant_rows"].append(i)
        elif p < 0.45:
            r["客户ID"] = f" {num} "               # 空白 + 去前缀
            info["id_variant_rows"].append(i)
        elif p < 0.55:
            r["客户ID"] = raw_id.lower()           # 小写
            info["id_variant_rows"].append(i)
        elif p < 0.60:
            # 数字打错一位 → 不可召回（现实中的不可修复情况）
            pos = rng.randrange(len(num))
            bad = num[:pos] + str((int(num[pos]) + rng.randint(1, 8)) % 10) + num[pos + 1:]
            r["客户ID"] = f"C{bad}"
            info["id_typo_rows"].append(i)

        # 姓名扰动：插入空格 / 全角空格 / 错字
        if rng.random() < INJECT.ref_noise_ratio:
            nm = str(r["客户姓名"])
            style = rng.choice(["space", "fwspace", "typo"])
            if style == "space" and len(nm) >= 2:
                r["客户姓名"] = nm[0] + " " + nm[1:]
            elif style == "fwspace":
                r["客户姓名"] = nm[0] + "\u3000" + nm[1:] if len(nm) >= 2 else nm
            else:
                r["客户姓名"] = nm + "生"
            info["name_typo_rows"].append(i)

        # 教育水平冲突（与主表不一致）
        if rng.random() < 0.10:
            r["教育水平(档案)"] = rng.choice(["primary", "secondary", "tertiary", "unknown"])
            info["edu_conflict_rows"].append(i)

    # 一对多：抽取 3% 的实体，在档案表里再插一条属性略有出入的记录
    extra = []
    for i in rng.sample(range(n), max(3, int(n * 0.03))):
        r = dict(recs[i])
        r["城市"] = rng.choice(["北京", "上海", "广州", "深圳"])
        r["教育水平(档案)"] = rng.choice(["primary", "secondary", "tertiary"])
        extra.append(r)
        info["dup_rows"].append(i)

    out = pd.DataFrame(recs + extra).reset_index(drop=True)
    out["_true_main"] = list(range(n)) + list(info["dup_rows"])   # 仅供调试，写盘前删除
    # 打乱档案表顺序
    order = list(range(len(out)))
    rng.shuffle(order)
    out = out.iloc[order].reset_index(drop=True)

    # 记录最终档案表位置 → 对应主表行
    info["final_ref_to_main"] = {
        int(pos): int(out.at[pos, "_true_main"]) for pos in range(len(out))
    }
    info["true_map"] = {int(m): int(r) for r, m in info["final_ref_to_main"].items()}
    out = out.drop(columns=["_true_main"])
    return out, info


def main() -> None:
    rng = random.Random(SEED)
    main_df = pd.read_csv(RAW_MAIN, dtype=str, keep_default_na=False)
    ref_df = pd.read_csv(RAW_REF, dtype=str, keep_default_na=False)

    # 数值列还原为数值，再注入（否则注入的值都是字符串，失去"类型不一致"的意义）
    for c in ["age", "balance", "day", "duration", "campaign", "pdays", "previous"]:
        main_df[c] = pd.to_numeric(main_df[c], errors="coerce")
    main_df["开户年份"] = pd.to_numeric(main_df["开户年份"], errors="coerce")

    inj_main, truth = inject_main(main_df, rng)
    inj_ref, ref_truth = inject_ref(ref_df, rng, len(main_df), SEED)

    # 写盘：统一转字符串并保留空值
    inj_main.astype(object).where(pd.notna(inj_main), "").to_csv(
        INJ_MAIN, index=False, encoding="utf-8-sig")
    inj_ref.to_csv(INJ_REF, index=False, encoding="utf-8-sig")

    # 干净基准（供修复准确率评估用）
    clean_main = main_df.astype(object).where(pd.notna(main_df), "")
    clean_main.to_csv(TRUTH_DIR / "主表_干净基准.csv", index=False, encoding="utf-8-sig")

    payload = {
        "seed": SEED,
        "inject_config": INJECT.__dict__,
        "n_rows_injected": int(len(inj_main)),
        "counts": {
            "missing_cells": len(truth["missing"]),
            "outlier_cells": len(truth["outliers"]),
            "format_cells": len(truth["format"]),
            "conflict_cells": len(truth["conflicts"]),
            "label_error_rows": len(truth["label_errors"]),
            "duplicate_groups": truth["duplicates"]["n_groups"],
            "redundant_rows": truth["duplicates"]["n_redundant_rows"],
            "ref_id_variants": len(ref_truth["id_variant_rows"]),
            "ref_id_typos": len(ref_truth["id_typo_rows"]),
            "ref_edu_conflicts": len(ref_truth["edu_conflict_rows"]),
        },
        "missing": truth["missing"],
        "outliers": truth["outliers"],
        "format": truth["format"],
        "conflicts": truth["conflicts"],
        "label_errors": truth["label_errors"],
        "duplicates": truth["duplicates"],
        # 每行来源：["base", i] / ["exact", i] / ["near", i]。评估需要用它在算误报时
        # 排除"重复副本行"（副本上的空格/大小写差异属于重复问题，不该算格式误报）。
        "_origin": [[str(k), int(v)] for k, v in truth["_origin"]],
        "entity": {k: v for k, v in ref_truth.items()},
    }
    TRUTH_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    print(f"[注入完成] 主表 {inj_main.shape} → {INJ_MAIN.name}")
    print(f"[注入完成] 档案表 {inj_ref.shape} → {INJ_REF.name}")
    print(f"[真值] {TRUTH_FILE}")
    for k, v in payload["counts"].items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
