"""汇报智能体：把整条流水线的结构化结果整理成人类可读材料。

产出（都落在 runs/<run_id>/ 与 docs/ 下）：
- 智能体运行摘要.md：面向报告第 4-6 节与"运行记录"交付项
- 指标汇总.json：所有可量化结果，报告里直接引用
- 摘要段落：由 LLM 撰写（mock 模式用模板拼装，保证无 key 也能交付）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ..config import DOCS_DIR
from .base import Agent

SYSTEM = """你是课程项目的汇报撰写者。根据流水线的结构化结果，写一段 200-300 字的中文小结，
必须包含：检测到的问题规模、修复动作与不可逆之处、验证结论、下游任务指标变化，
以及诚实说明"哪些修复只能近似、不能声称还原"。不要编造数字，只用给定数据。"""


class ReporterAgent(Agent):
    name = "ReporterAgent"
    role = "结果汇报与材料生成"

    def run(self, results: dict) -> dict:
        ctx = self.ctx
        det = results.get("detection", {})
        ver = results.get("verification", [])
        ds = results.get("downstream", {})
        usage = results.get("llm_usage", {})

        facts = {
            "行数列数": [len(ctx.main_df), ctx.main_df.shape[1]],
            "检测问题数": det.get("n_findings"),
            "按类型": det.get("by_type"),
            "跨表匹配行数": det.get("n_matched"),
            "验证轮次": len(ver),
            "最终质量分": ver[-1]["quality_score"] if ver else None,
            "下游指标": {k: ds.get("after", {}).get("best", {}).get(k)
                        for k in ("accuracy", "f1", "roc_auc")} if ds else None,
            "指标提升": ds.get("delta") if ds else None,
        }
        user = f"## 事实数据\n{facts}\n\n## 执行轨迹\n{results.get('quality_rounds')}\n\n请写小结。"
        mock = self._template(facts, ds)
        text = self.llm.chat(agent=self.name, system=SYSTEM, user=user,
                             json_mode=False, mock_decision=mock)

        md = self._render_markdown(results, facts, text)
        out_path = ctx.logger.dir / "智能体运行摘要.md"
        out_path.write_text(md, encoding="utf-8")
        docs_path = DOCS_DIR / "智能体运行摘要.md"
        docs_path.write_text(md, encoding="utf-8")
        self.say(f"已生成汇报材料：{out_path.name}（正文 {len(text)} 字）")

        return self.finish({"summary_text": text,
                            "files": [str(out_path), str(docs_path)],
                            "facts": facts})

    # ------------------------------------------------------------------
    def _template(self, facts: dict, ds: dict) -> str:
        d = facts.get("指标提升") or {}
        delta = "，".join(f"{k} {v:+.4f}" for k, v in d.items()) or "（缺少基线对比）"
        return (f"本次运行在主表 {facts['行数列数'][0]} 行 × {facts['行数列数'][1]} 列上共检出 "
                f"{facts['检测问题数']} 项质量问题，按类型分布为 {facts['按类型']}。"
                f"跨表实体匹配成功 {facts['跨表匹配行数']} 行，用于补全与冲突校验。"
                f"流水线经过 {facts['验证轮次']} 轮验证，最终数据质量分 "
                f"{facts['最终质量分']}；下游分类任务指标变化：{delta}。"
                f"需要说明的是，格式与重复属于可精确还原的修复，而缺失值、异常值、字段冲突与"
                f"标签错误的修复只能是近似估计，报告中按两类分别统计，不宣称完全还原原始数据。")

    def _render_markdown(self, results: dict, facts: dict, text: str) -> str:
        det = results.get("detection", {})
        rounds = results.get("quality_rounds", [])
        ds = results.get("downstream", {})
        usage = results.get("llm_usage", {})
        lines = [f"# 智能体运行摘要（{results.get('run_id')}）", "",
                 "## 一、小结", text, "",
                 "## 二、问题检测", "",
                 f"- 检出问题条目：**{det.get('n_findings')}**", ]
        for t, n in (det.get("by_type") or {}).items():
            lines.append(f"  - {t}：{n} 项")
        lines += [f"- ReAct 步数：{det.get('n_steps')}，使用的检测工具：{det.get('tools_used')}",
                  "", "## 三、修复与验证", ""]
        for r in rounds:
            lines.append(f"- 第 {r['round']} 轮：动作 {r['actions']}，"
                         f"验证{'通过' if r['passed'] else '未通过'}，质量分 {r['quality_score']}")
            for v in r.get("violations") or []:
                lines.append(f"  - 残留超标：{v['problem_type']} {v['residual_rate']} > {v['tolerance']}")
        lines += ["", "## 四、下游分类任务", ""]
        if ds.get("before") or ds.get("after"):
            for tag in ("before", "after"):
                blk = ds.get(tag) or {}
                best = blk.get("best") or {}
                if not best:
                    continue
                lines.append(f"- **{blk.get('label', tag)}**（最优模型：{blk.get('best_model')}，"
                             f"样本 {blk.get('n_rows')}）："
                             f"acc={best.get('accuracy')} f1={best.get('f1')} auc={best.get('roc_auc')}")
            if ds.get("delta"):
                lines.append(f"- 指标变化：{ds['delta']}")
        lines += ["", "## 五、LLM 用量与成本", "",
                  f"- 模式：{usage.get('mode')}，模型：{usage.get('model')}",
                  f"- 调用次数：{len(usage.get('calls') or [])}，"
                  f"prompt {usage.get('prompt_tokens')} tok，completion {usage.get('completion_tokens')} tok",
                  f"- 估算成本：${usage.get('cost_usd')}", ""]
        return "\n".join(lines)
