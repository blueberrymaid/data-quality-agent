"""修复规划智能体（Plan-and-Execute 的 Plan 阶段）。

输入：DetectorAgent 的 Finding 列表 + 数据画像
输出：有序的修复计划（每步 = 动作名 + 参数 + 理由）

约束：模型只能从 ACTION_CATALOG 里挑动作、从该动作声明的参数域里取值；
不在目录里的动作一律丢弃（防止模型凭空发明改数据操作）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ..tools.repair import ACTION_CATALOG
from .base import Agent

# 一类的默认处理策略（LLM 不可用时的兜底，也是最保守的常识做法）
DEFAULT: dict[str, dict] = {
    "format": {"action": "standardize_format", "params": {}},
    "missing": {"action": "impute_missing", "params": {"strategy": "group_mode"}},
    "duplicate": {"action": "drop_duplicates", "params": {"near": True}},
    "outlier": {"action": "fix_outliers", "params": {"strategy": "clip"}},
    "conflict": {"action": "resolve_conflicts", "params": {"policy": "recompute_from_age"}},
    "label_error": {"action": "fix_label_errors", "params": {"threshold": 0.02, "max_rows": 500}},
    "entity_match": {"action": "enrich_from_ref", "params": {}},
    "imbalance": {"action": "rebalance_training", "params": {"method": "class_weight"}},
}

# 执行顺序不是随便排的：上游动作会改变下游检测的前提条件
ORDER = ["entity_match", "format", "duplicate", "missing", "outlier",
         "conflict", "label_error", "imbalance"]
ORDER_REASON = ("先做跨表匹配（后续冲突检测依赖匹配映射）→ 统一格式（类型正确后数值计算才可信）"
                "→ 去重（避免同一条记录被填补多次）→ 填补缺失 → 处理异常值 → 消解冲突 "
                "→ 修正标签 → 不平衡仅记录训练侧配置")

SYSTEM = """你是数据修复规划专家。根据检测到的问题清单，输出一个修复计划。
可用的动作（只能从这里选，不能自创）：
{catalog}

输出 JSON：
{{"plan":[{{"action":"动作名","params":{{...}},"targets":["字段或问题类型"],"rationale":"为什么这样修，涉及什么风险"}}],
 "order_note":"对执行顺序的说明",
 "risk":"整体风险与不可逆之处"}}
要求：每个问题类型至少被一个动作覆盖；对"只能近似修复"的问题在 rationale 里说明局限。"""


class PlannerAgent(Agent):
    name = "PlannerAgent"
    role = "修复策略规划"

    def run(self, findings: list) -> dict:
        by_type: dict[str, list] = {}
        for f in findings:
            by_type.setdefault(f.problem_type, []).append(f)

        catalog_text = "\n".join(
            f"- {a['name']}（解决问题：{'/'.join(a['solves'])}；参数：{a['params'] or '无'}）：{a['desc']}"
            for a in ACTION_CATALOG)
        issues_text = "\n".join(
            f"- {t}：{len(fs)} 项，涉及字段 {sorted({x.field for x in fs if x.field})[:8]}，"
            f"最高严重度 {max((x.severity for x in fs), key=lambda s: {'high': 3, 'medium': 2, 'low': 1}.get(s, 0))}，"
            f"例：{fs[0].problem_type} on {fs[0].field}，影响 {fs[0].n_affected} 行({fs[0].rate:.2%})"
            for t, fs in by_type.items())
        profile_text = self.ctx.state.get("profile_brief", "")

        mock_plan = [dict(DEFAULT[t], targets=[t], rationale=f"规则兜底：{t} 用默认保守策略")
                     for t in ORDER if t in by_type]
        dec = self.decide(
            "生成修复计划", SYSTEM.format(catalog=catalog_text),
            f"## 数据画像\n{profile_text or '（略）'}\n\n## 问题清单\n{issues_text}\n\n"
            f"请给出修复计划。",
            mock_decision={"plan": mock_plan, "order_note": ORDER_REASON,
                           "risk": "缺失/异常/冲突/标签均为近似修复，无法精确还原"})

        valid_actions = {a["name"]: a for a in ACTION_CATALOG}
        plan: list[dict] = []
        dropped: list[str] = []
        for step in dec.get("plan", []) or []:
            act = str(step.get("action", "")).strip()
            if act not in valid_actions:
                dropped.append(act)
                continue
            problems = valid_actions[act]["solves"]
            plan.append({
                "action": act,
                "covers": problems,
                "params": step.get("params") or {},
                "rationale": step.get("rationale", ""),
            })
        # 补齐模型漏掉的问题类型（宁可多做一步，也不能漏修）
        covered = {p for s in plan for p in s["covers"]}
        for t in by_type:
            if t not in covered and t in DEFAULT:
                plan.append({"action": DEFAULT[t]["action"], "covers": [t],
                             "params": dict(DEFAULT[t]["params"]),
                             "rationale": "规划遗漏，按默认策略补齐"})
                covered.add(t)
        # 按依赖顺序稳定排序
        plan.sort(key=lambda s: min(ORDER.index(p) for p in s["covers"] if p in ORDER))

        if dropped:
            self.logger.log_error(where=f"{self.name}.run",
                                  error=f"模型给出的动作不在目录中，已丢弃：{dropped}",
                                  fixed="丢弃非法动作，改用 ACTION_CATALOG 中的默认动作")
        self.ctx.note("plan", plan)
        self.ctx.note("plan_risk", dec.get("risk", ""))
        return self.finish({
            "n_steps": len(plan),
            "actions": [s["action"] for s in plan],
            "dropped": dropped,
            "order_note": dec.get("order_note", ORDER_REASON),
            "risk": dec.get("risk", ""),
        })
