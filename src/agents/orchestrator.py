"""Orchestrator：Plan-and-Execute 的主控中枢。

整体流程（每一阶段都是一次独立的智能体调用，阶段间只通过 Context 传数据）：

  1. Plan      —— 读画像与任务目标，决定本轮策略取向（保守/均衡/激进）
  2. Detect    —— DetectorAgent：ReAct 循环挑检测工具，产出问题清单
  3. Re-plan   —— PlannerAgent：把问题清单翻译成有序的修复计划
  4. Execute   —— ExecutorAgent：逐步调用修复工具，每步留快照
  5. Verify    —— VerifierAgent：重新检测，残留超标则带目标回到第 3 步
  6. Downstream—— 分类任务：脏数据基线 vs 清洗后，同一套建模流程对比
  7. Report    —— ReporterAgent：生成中文报告素材与指标 JSON

为什么要"检测—规划—执行—验证"闭环，而不是一条直线：
单次修复经常过头或不足（例如填补把异常值又引回来），所以必须有验证回流；
但回流次数必须封顶（AgentConfig.max_verify_rounds），否则可能陷入无限重试。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from ..config import AgentConfig, LLM, TARGET, load_env_file, resolve_llm_config
from ..llm.client import LLMClient
from ..utils import RunLogger
from .base import Context
from .detector import DetectorAgent
from .executor import ExecutorAgent
from .planner import PlannerAgent
from .verifier import VerifierAgent

PLAN_SYSTEM = """你是多智能体数据处理流水线的调度者。先读数据画像，决定本轮的处理取向。
取向含义：
- conservative：只做低风险修复，标签等高风险动作完全跳过；
- balanced：覆盖所有问题类型，风险动作使用严格阈值；
- aggressive：追求下游指标，风险动作放宽阈值（可能引入假修复）。
输出 JSON：{"aggressiveness":"conservative|balanced|aggressive","focus":["优先关注的问题类型"],
"phases":["阶段名"],"rationale":"为什么这样取向"}"""


class Orchestrator:
    """把六个智能体串成一条可验证的流水线。"""

    def __init__(self, main_df: pd.DataFrame, ref_df: pd.DataFrame | None = None,
                 truth: dict | None = None, run_id: str | None = None,
                 cfg: AgentConfig | None = None, llm: LLMClient | None = None):
        load_env_file()
        self.run_id = run_id or time.strftime("run_%Y%m%d_%H%M%S")
        self.logger = RunLogger(self.run_id)
        self.cfg = cfg or AgentConfig()
        self.llm = llm or LLMClient(resolve_llm_config(), self.logger)
        self.ctx = Context(main_df=main_df.copy(),
                           ref_df=None if ref_df is None else ref_df.copy(),
                           logger=self.logger, llm=self.llm, cfg=self.cfg,
                           truth=truth or {})
        self.results: dict = {"run_id": self.run_id}

    # ---------------------------------------------------------------- 1 Plan
    def make_plan(self) -> dict:
        from ..tools.profiling import compact_profile, profile_table
        prof = profile_table(self.ctx.main_df, "主表", 3)
        self.ctx.note("profile", prof)
        self.ctx.state["profile_brief"] = compact_profile(prof)
        user = (f"## 主表画像\n{self.ctx.state['profile_brief']}\n\n"
                f"## 任务\n数据清洗 + 二分类（目标列 {TARGET}），参照表可用："
                f"{'是' if self.ctx.ref_df is not None else '否'}\n\n请给出本轮取向与阶段安排。")
        dec = self.llm.decide(
            agent="Orchestrator", system=PLAN_SYSTEM, user=user,
            mock_decision={"aggressiveness": "balanced",
                           "focus": ["missing", "duplicate", "outlier", "format"],
                           "phases": ["profile", "detect", "plan", "execute", "verify",
                                      "downstream", "report"],
                           "rationale": "规则兜底：四类核心问题全覆盖，风险动作使用严格阈值"})
        agg = str(dec.get("aggressiveness", "balanced")).lower()
        if agg not in ("conservative", "balanced", "aggressive"):
            agg = "balanced"
        self.ctx.note("aggressiveness", agg)
        self.ctx.note("plan_overview", dec)
        self.logger.stage("Orchestrator", f"策略取向={agg}，优先关注={dec.get('focus')}")
        self.results["plan"] = dec
        return dec

    # ------------------------------------------------------------ 2/3/4/5 主循环
    def run_quality_loop(self) -> dict:
        det = DetectorAgent(self.ctx)
        det_summary = det.run()
        self.results["detection"] = det_summary
        findings = self.ctx.state.get("findings", [])
        self.ctx.state["findings_baseline"] = findings

        rounds = []
        max_rounds = self.cfg.max_verify_rounds
        for rnd in range(1, max_rounds + 1):
            planner = PlannerAgent(self.ctx)
            plan_summary = planner.run(findings)
            self.results.setdefault("plans", []).append(plan_summary)
            plan = self.ctx.state.get("plan", [])
            if not plan:
                self.logger.stage("Orchestrator", "计划为空，跳过执行")
                break

            executor = ExecutorAgent(self.ctx)
            exec_summary = executor.run(plan)
            self.results.setdefault("executions", []).append(exec_summary)

            verifier = VerifierAgent(self.ctx)
            ver = verifier.run(round_id=rnd)
            rounds.append({"round": rnd, "plan": plan_summary, "execute": exec_summary,
                           "verify": ver})
            if ver["passed"]:
                self.logger.stage("Orchestrator", f"第 {rnd} 轮验证通过，质量闭环结束")
                break
            # 未通过：只针对残留超标的类型重新规划（避免把已修好的再改一次）
            targets = {v["problem_type"] for v in ver["violations"]}
            findings = [f for f in self.ctx.state.get("findings_after", [])
                        if f.problem_type in targets]
            self.logger.stage("Orchestrator",
                              f"第 {rnd} 轮未通过，残留类型 {sorted(targets)}，进入下一轮规划")
            if not findings:
                break

        self.results["quality_rounds"] = [
            {"round": r["round"], "actions": r["plan"]["actions"],
             "passed": bool(r["verify"]["passed"]),
             "quality_score": r["verify"]["quality_score"],
             "violations": r["verify"]["violations"]} for r in rounds]
        self.results["clean_df"] = self.ctx.main_df
        self.results["verification"] = self.ctx.state.get("verification", [])
        return self.results

    # ------------------------------------------------------- 6 Downstream
    def run_downstream(self) -> dict:
        from ..pipeline.classify import compare
        from ..tools.repair import drop_duplicates, standardize_format
        dirty = self.ctx.state.get("df_dirty")
        if dirty is None:
            dirty = self.ctx.truth.get("_dirty_df")
        clean = self.ctx.main_df
        self.logger.stage("Orchestrator", "下游分类任务：脏数据基线 vs 清洗后")
        precise = None
        if dirty is not None:
            # 中间档对照：只做“可精确还原”的修复（格式统一 + 去重），
            # 用来把“清洗带来的收益”与“近似修复（填补/裁剪）的代价”分开。
            try:
                p1, _ = standardize_format(dirty)
                p2, _ = drop_duplicates(p1)
                precise = p2
            except Exception as exc:  # noqa: BLE001
                self.logger.log_error(where="Orchestrator.precise_only", error=str(exc))
        cmp = compare(dirty, clean, precise_only=precise) if dirty is not None else {
            "note": "缺少脏数据基线，仅评估清洗后数据",
            "after": {"label": "智能体处理后"},
        }
        self.results["downstream"] = cmp
        self.logger.stage("Orchestrator", f"下游对比完成：{cmp.get('delta', {})}")
        return cmp

    # ------------------------------------------------------------- 全流程
    def run(self, downstream: bool = True) -> dict:
        t0 = time.perf_counter()
        self.logger.stage("Orchestrator", f"开始运行 {self.run_id}")
        self.ctx.note("df_dirty", self.ctx.main_df.copy())
        self.make_plan()
        self.run_quality_loop()
        if downstream:
            self.run_downstream()
        self.results["elapsed_sec"] = round(time.perf_counter() - t0, 1)
        self.results["llm_usage"] = {
            "provider": self.llm.cfg.base_url or "(mock)",
            "model": self.llm.cfg.model,
            "mode": "mock" if self.llm.mock else "api",
            "calls": self.logger.llm_calls,
            "prompt_tokens": self.logger.total_prompt_tokens,
            "completion_tokens": self.logger.total_completion_tokens,
            "cost_usd": round(self.logger.total_cost, 6),
        }
        self.logger.save_summary({k: v for k, v in self.results.items()
                                  if k not in ("clean_df",)})
        return self.results
