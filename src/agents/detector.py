"""质量检测智能体：内部使用 ReAct 循环挑选检测工具。

ReAct 结构：
  Thought  → 基于当前画像与已查结果，决定下一步查哪类问题（或停止）
  Action   → 调用一个检测工具
  Observation → 工具返回的 Finding 摘要，进入下一轮上下文

LLM 不可用（mock/无 key）时退化为"按画像给出的确定性顺序全部查一遍"，
保证流水线在任何环境都能跑出结果。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ..config import TARGET
from ..tools import quality
from ..tools.profiling import compact_profile, profile_table
from .base import Agent

SYSTEM = """你是数据质量检测专家，负责在给定的营销数据表上找出质量问题。
你可以调用以下检测工具（每次只能调用一个）：
{tools}

规则：
- 先看数据画像再决定顺序；发现某类问题已足够严重时优先补齐其他类型；
- "字段冲突"依赖"跨表实体匹配"的结果，必须先跑实体匹配；
- 全部查完后输出 {"next":"stop","reason":"..."}；
- 只输出 JSON：{"thought":"...","next":"工具名或stop","params":{...},"reason":"为什么先查这个"}"""


class DetectorAgent(Agent):
    name = "DetectorAgent"
    role = "数据质量检测（ReAct）"

    TOOLS = [
        ("detect_missing", "检查缺失值（含 ?、N/A、全角句号等伪缺失标记）"),
        ("detect_duplicates", "检查完全重复行与近似重复行（去空白/大小写归一后）"),
        ("detect_outliers", "检查数值异常（业务边界 + IQR + z-score）"),
        ("detect_format_issues", "检查格式不一致（数值混文本、千分位、全角、单位、大小写）"),
        ("detect_entity_match", "跨表实体匹配（主表 ↔ 客户档案表），并输出匹配映射"),
        ("detect_conflicts", "检查字段冲突（档案 vs 主表，年龄/开户年份自洽性）"),
        ("detect_label_errors", "检查标签错误（5 折折外预测，高置信错的样本）"),
        ("detect_imbalance", "检查类别不平衡程度"),
    ]

    def run(self) -> dict:
        df = self.ctx.main_df
        ref = self.ctx.ref_df

        self.say(f"开始质量检测，主表 {len(df)} 行 × {df.shape[1]} 列")
        profile = self.call_tool("profile_table", profile_table, df, "主表", 5)
        self.ctx.note("profile", profile)
        profile_text = compact_profile(profile)
        ref_text = ""
        if ref is not None:
            ref_profile = self.call_tool("profile_table", profile_table, ref, "客户档案表", 3)
            self.ctx.note("ref_profile", ref_profile)
            ref_text = compact_profile(ref_profile)

        available = [t for t, _ in self.TOOLS]
        done: list[str] = []
        findings = []
        match_map: dict = {}
        steps = []

        for step in range(1, self.ctx.cfg.max_react_steps + 1):
            todo = [t for t in available if t not in done]
            if not todo:
                break
            tool_desc = "\n".join(f"- {t}：{d}" for t, d in self.TOOLS if t in todo)
            user = (f"## 主表画像\n{profile_text}\n\n## 参照表画像\n{ref_text or '（无参照表）'}\n\n"
                    f"## 已完成的检测\n{done or '（无）'}\n\n"
                    f"## 待选工具\n{tool_desc}\n\n"
                    f"已发现 {len(findings)} 个问题。请决定下一步。")
            mock = {"thought": f"按优先级查 {todo[0]}", "next": todo[0], "params": {},
                    "reason": "规则兜底：按依赖顺序逐个覆盖"}
            dec = self.decide(f"检测第 {step} 步", SYSTEM.replace("{tools}", tool_desc),
                              user, options=todo + ["stop"], mock_decision=mock)
            nxt = str(dec.get("next", "")).strip()
            if nxt not in todo:
                if nxt == "stop":
                    self.say(f"第 {step} 步：模型决定停止检测")
                    steps.append({"step": step, "thought": dec.get("thought", ""),
                                  "action": "stop", "reason": dec.get("reason", "")})
                    break
                nxt = todo[0]

            # --- Action：调用工具 ---
            fs, mm = self._dispatch(nxt, df, ref, match_map)
            if mm:
                match_map.update(mm)
            findings.extend(fs)
            done.append(nxt)
            steps.append({"step": step, "thought": dec.get("thought", ""),
                          "action": nxt, "n_findings": len(fs),
                          "reason": dec.get("reason", "")})
            obs = "；".join(
                f"{f.problem_type}/{f.field}: 影响 {f.n_affected} 行({f.rate:.2%}, {f.severity})"
                for f in fs) if fs else "未发现问题"
            self.say(f"第 {step} 步 {nxt} → {obs[:120]}")

        by_type: dict[str, int] = {}
        for f in findings:
            by_type[f.problem_type] = by_type.get(f.problem_type, 0) + 1
        self.ctx.note("findings", findings)
        self.ctx.note("match_map", match_map)
        self.ctx.note("react_trace", steps)
        return self.finish({
            "n_findings": len(findings),
            "by_type": by_type,
            "tools_used": done,
            "n_matched": len(match_map),
            "n_steps": len(steps),
        })

    # ------------------------------------------------------------------
    def _safe(self, tool: str, fn, *args):
        """工具调用失败时返回 None（guard 已把异常记成日志条目），绝不中断整条链路。"""
        out = self.call_tool(tool, fn, *args)
        if isinstance(out, dict) and out.get("error"):
            self.logger.log_error(where=f"{self.name}._dispatch({tool})",
                                  error=out["error"],
                                  fixed="记为该类检测失败，继续下一步")
            return None
        return out

    def _dispatch(self, tool: str, df, ref, match_map):
        if tool == "detect_missing":
            return self._safe(tool, quality.detect_missing, df) or [], {}
        if tool == "detect_duplicates":
            return self._safe(tool, quality.detect_duplicates, df) or [], {}
        if tool == "detect_outliers":
            return self._safe(tool, quality.detect_outliers, df) or [], {}
        if tool == "detect_format_issues":
            return self._safe(tool, quality.detect_format_issues, df) or [], {}
        if tool == "detect_entity_match":
            out = self._safe(tool, quality.detect_entity_match, df, ref)
            if not out:
                return [], {}
            fs, mm = out
            return fs, mm or {}
        if tool == "detect_conflicts":
            return self._safe(tool, quality.detect_conflicts, df, ref, match_map) or [], {}
        if tool == "detect_label_errors":
            return self._safe(tool, quality.detect_label_errors, df, TARGET) or [], {}
        if tool == "detect_imbalance":
            return self._safe(tool, quality.detect_imbalance, df, TARGET) or [], {}
        return [], {}
