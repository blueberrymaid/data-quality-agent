"""验证智能体：修完之后重新检测一遍，用"残留率"判断是否需要再规划。

判定口径（写进报告）：
- 格式与重复是**可精确还原**的问题，容忍度为 0（残留任何一条都算失败）；
- 缺失、异常、冲突是**近似修复**，容忍少量残留（阈值见 TOLERANCE）；
- 标签错误本身是概率判断，不设硬门槛，只报告"修复条数"与"复核清单规模"。
验证不通过时输出需要重做的目标类型，交回 Orchestrator 触发下一轮 Plan。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ..config import TARGET
from ..tools.quality import run_all_detectors
from .base import Agent

TOLERANCE = {
    "format": 0.0,
    "duplicate": 0.0,
    "missing": 0.005,
    "outlier": 0.0,
    "conflict": 0.0,
    "entity_match": 1.0,
    "label_error": 1.0,
    "imbalance": 1.0,
}

# 只作提示、不作验收门槛的检测口径：
# - statistical：IQR/z-score 判出的长尾值，银行余额这类重尾字段的真实大值本就该保留；
# - cross_table：主表与档案表不一致，按“主表为准”的既定策略记录差异而不改数据，
#   若把它当硬门槛，验证环节会永远无法收敛（策略本身决定了它不会消失）。
SOFT_KINDS = {"statistical", "cross_table"}


class VerifierAgent(Agent):
    name = "VerifierAgent"
    role = "修复结果回归校验"

    def run(self, round_id: int = 1) -> dict:
        df = self.ctx.main_df
        ref = self.ctx.ref_df
        before = self.ctx.state.get("findings_baseline") or []
        self.say(f"第 {round_id} 轮验证：重新检测修复后的数据（{len(df)} 行）")

        findings, match_map = self.call_tool("run_all_detectors", run_all_detectors,
                                             df, ref, TARGET)
        # 注意：这里的 match_map 是「处理后的数据行号 → 档案表行号」，行号基准已经变了。
        # 绝不能覆盖 ctx.state["match_map"]（那是脏数据行号基准，执行器与评估都在用它），
        # 否则 enrich/resolve 会错位、评估的实体匹配精确率会假性崩塌（曾跌到 0.0204）。
        self.ctx.note("match_map_after", match_map)
        self.ctx.note("findings_after", findings)

        n = max(len(df), 1)
        before_cnt: dict[str, int] = {}
        for f in before:
            before_cnt[f.problem_type] = before_cnt.get(f.problem_type, 0) + 1
        after_cnt: dict[str, int] = {}
        after_rate: dict[str, float] = {}
        soft_rate: dict[str, float] = {}
        for f in findings:
            after_cnt[f.problem_type] = after_cnt.get(f.problem_type, 0) + 1
            kind = (f.evidence or {}).get("kind")
            if kind in SOFT_KINDS:
                soft_rate[f.problem_type] = max(soft_rate.get(f.problem_type, 0.0),
                                                float(f.rate or 0.0))
            else:
                after_rate[f.problem_type] = max(after_rate.get(f.problem_type, 0.0),
                                                 float(f.rate or 0.0))

        checks = {}
        violations = []
        for ptype in sorted(set(list(before_cnt) + list(after_cnt))):
            tol = TOLERANCE.get(ptype, 0.01)
            rate = after_rate.get(ptype, 0.0)
            ok = rate <= tol + 1e-12
            checks[ptype] = {
                "before_findings": before_cnt.get(ptype, 0),
                "after_findings": after_cnt.get(ptype, 0),
                "residual_rate": round(rate, 5),
                "tolerance": tol,
                "passed": ok,
                "soft_residual_rate": round(soft_rate.get(ptype, 0.0), 5),
            }
            if not ok:
                violations.append({"problem_type": ptype, "residual_rate": round(rate, 5),
                                   "tolerance": tol})

        # 数据质量指数：按残留率加权（仅用于报告展示趋势，不作为验收唯一依据）
        weights = {"missing": .2, "duplicate": .2, "outlier": .2, "format": .2,
                   "conflict": .1, "label_error": .1}
        score = 100.0
        for ptype, w in weights.items():
            score -= w * min(1.0, after_rate.get(ptype, 0.0) / 0.05) * 100
        score = round(max(0.0, score), 1)

        result = {
            "round": round_id,
            "passed": not violations,
            "violations": violations,
            "checks": checks,
            "quality_score": score,
            "n_rows": int(n),
            "residual_findings": len(findings),
        }
        self.ctx.state.setdefault("verification", []).append(result)
        verdict = "通过" if not violations else f"未通过（{len(violations)} 类残留超标）"
        self.say(f"第 {round_id} 轮验证{verdict}，质量分 {score}")
        return self.finish(result)
