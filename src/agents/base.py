"""多智能体基类：统一持有日志器、LLM 客户端与共享状态。

约定（贯穿整个项目）：
- LLM 只负责**决策与解释**（选哪个工具、参数怎么给、为什么这么做）；
- 所有数值计算、检测、修复都由 Python 工具函数完成，LLM 碰不到数据；
- 每个 Agent 的输入输出都落到事件日志，最后直接转成"运行记录"交付材料。
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from ..config import AgentConfig
from ..llm.client import LLMClient
from ..utils import RunLogger


@dataclass
class Context:
    """一次运行共享的可变状态，Agent 之间只通过它交换数据。"""
    main_df: pd.DataFrame
    ref_df: pd.DataFrame | None
    logger: RunLogger
    llm: LLMClient
    cfg: AgentConfig
    truth: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)

    def note(self, key: str, value: Any) -> None:
        self.state[key] = value


class Agent:
    """所有智能体的父类：提供计时、日志、LLM 便捷调用。"""

    name = "agent"
    role = "未定义"

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.logger = ctx.logger
        self.llm = ctx.llm

    # ---------- 子类实现 ----------
    def run(self) -> dict:
        raise NotImplementedError

    # ---------- 通用能力 ----------
    def say(self, message: str, **extra) -> None:
        self.logger.stage(self.name, message)
        if extra:
            self.logger.log("thought", agent=self.name, message=message, **extra)

    def call_tool(self, tool: str, fn: Callable, *args, **kwargs):
        from ..utils import guard
        return guard(self.name, tool, fn, *args, logger=self.ctx.logger, **kwargs)

    def decide(self, purpose: str, system: str, user: str,
               options: list[str] | None = None,
               mock_decision: dict | None = None) -> dict:
        """向 LLM 要一个结构化决策；mock/降级时用规则兜底。"""
        t0 = time.perf_counter()
        out = self.llm.decide(agent=self.name, system=system, user=user,
                              mock_decision=mock_decision or {})
        self.logger.log("decision", agent=self.name, purpose=purpose,
                        options=options or [],
                        elapsed_ms=round((time.perf_counter() - t0) * 1000),
                        result=out)
        return out

    def finish(self, summary: dict) -> dict:
        self.logger.stage(self.name, "完成", summary=summary)
        return summary
