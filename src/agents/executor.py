"""执行智能体：严格按计划逐步调用修复工具，并记录每步的增减量。

安全约束：
- 每一步都记录"行数/列数/变更单元格"前后对比，任何一步异常都不静默吞掉；
- 主键去重这类**不可逆**操作单独标注，方便报告里做风险说明；
- 执行完保存中间快照（data/processed/中间快照_第N步.csv），便于复现与回滚。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ..config import PROCESSED_DIR
from ..tools.repair import ACTION_FUNCS
from .base import Agent

IRREVERSIBLE = {"drop_duplicates": "删除重复行不可恢复（已在去重前落快照）",
                "resolve_conflicts": "主键重复时保留信息最全的一条，其余被丢弃"}


class ExecutorAgent(Agent):
    name = "ExecutorAgent"
    role = "修复动作执行"

    def run(self, plan: list[dict]) -> dict:
        df = self.ctx.main_df
        ref = self.ctx.ref_df
        match_map = self.ctx.state.get("match_map", {}) or {}
        self.say(f"开始执行修复计划，共 {len(plan)} 步")

        trace = []
        for i, step in enumerate(plan, 1):
            action = step["action"]
            params = dict(step.get("params") or {})
            fn = ACTION_FUNCS.get(action)
            if fn is None:
                self.logger.log_error(where=f"{self.name}.run",
                                      error=f"未知动作 {action}，跳过",
                                      fixed="跳过并保留原数据")
                continue

            before_shape = df.shape
            # 动作参数与智能体状态的对齐（不是所有动作都吃 ref/match_map）
            if action in ("resolve_conflicts", "enrich_from_ref"):
                params.setdefault("ref", ref)
                if action == "resolve_conflicts":
                    params.setdefault("match_map", match_map)
                else:
                    params["match_map"] = match_map
            if action == "rebalance_training":
                # 该动作不修改数据，只登记训练侧配置
                trace.append({"step": i, "action": action, "params": params,
                              "note": "仅登记训练侧配置，不修改数据"})
                self.say(f"第 {i} 步 {action} → 仅记录配置，数据不变")
                continue

            res = self.call_tool(action, fn, df, **params)
            if isinstance(res, dict) and res.get("error"):
                self.logger.log_error(where=f"{self.name}.run(step={i}, {action})",
                                      error=res["error"],
                                      fixed="该步失败，保留上一步的数据继续执行后续步骤")
                trace.append({"step": i, "action": action, "params": params,
                              "error": res["error"]})
                continue
            df, info = res
            after_shape = df.shape
            rec = {"step": i, "action": action, "params": params,
                   "rows_before": int(before_shape[0]), "rows_after": int(after_shape[0]),
                   "cols_before": int(before_shape[1]), "cols_after": int(after_shape[1]),
                   "info": info, "irreversible": IRREVERSIBLE.get(action, "")}
            trace.append(rec)
            self.ctx.main_df = df

            snap = PROCESSED_DIR / f"中间快照_第{i}步_{action}.csv"
            df.to_csv(snap, index=False, encoding="utf-8-sig")
            self.say(f"第 {i} 步 {action} → {info.get('rows_before', before_shape[0])}→{after_shape[0]} 行，"
                     f"快照 {snap.name}")

        self.ctx.note("execution_trace", trace)
        self.ctx.note("df_after_exec", df)
        return self.finish({
            "n_steps_done": len([t for t in trace if "info" in t]),
            "rows_final": int(len(df)),
            "cols_final": int(df.shape[1]),
            "irreversible_steps": [t["action"] for t in trace if t.get("irreversible")],
        })
