"""运行记录基础设施：结构化事件日志（JSONL）+ 运行目录管理。

作业要求"智能体运行记录：提示词、工具调用、错误与修正过程"，
这里的 RunLogger 就是该材料的唯一数据源：所有 LLM 调用、工具调用、
错误与重试都 append 到同一次运行的 JSONL 文件里，后续可一键转成 Markdown/表格。
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class RunLogger:
    """一次智能体运行的日志收集器。"""

    def __init__(self, run_id: str | None = None, root: Path | None = None):
        self.run_id = run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.root = Path(root) if root else Path("runs")
        self.dir = self.root / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.dir / "事件记录.jsonl"
        self._t0 = time.time()
        self.events: list[dict] = []
        self.llm_calls: list[dict] = []
        self.tool_calls: list[dict] = []
        self.errors: list[dict] = []

    # ------------------------------------------------------------- 写事件
    def log(self, event: str, **payload: Any) -> dict:
        rec = {
            "ts": _now(),
            "elapsed_s": round(time.time() - self._t0, 3),
            "run_id": self.run_id,
            "event": event,
            **payload,
        }
        self.events.append(rec)
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    # ------------------------------------------------------------- 阶段
    def stage(self, name: str, message: str | None = None, **kw: Any) -> None:
        payload = dict(kw)
        if message:
            payload["message"] = message
        self.log("stage", stage=name, **payload)

    # ------------------------------------------------------------- LLM
    def log_llm(
        self,
        *,
        agent: str,
        model: str,
        prompt: str,
        response: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_s: float,
        mock: bool = False,
        attempt: int = 1,
    ) -> None:
        rec = self.log(
            "llm_call",
            agent=agent,
            model=model,
            prompt=prompt,
            response=response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost_usd, 8),
            latency_s=round(latency_s, 3),
            mock=mock,
            attempt=attempt,
        )
        self.llm_calls.append(rec)

    # ------------------------------------------------------------- 工具
    def log_tool(
        self,
        *,
        agent: str,
        tool: str,
        args: dict,
        result_summary: Any,
        ok: bool = True,
        latency_s: float = 0.0,
    ) -> None:
        rec = self.log(
            "tool_call",
            agent=agent,
            tool=tool,
            args=args,
            result=result_summary,
            ok=ok,
            latency_s=round(latency_s, 3),
        )
        self.tool_calls.append(rec)

    # ------------------------------------------------------------- 错误
    def log_error(self, *, where: str, error: Exception | str, fixed: str | None = None) -> None:
        if isinstance(error, Exception):
            detail = f"{type(error).__name__}: {error}"
            tb = traceback.format_exc(limit=3)
        else:
            detail = str(error)
            tb = None
        rec = self.log("error", where=where, error=detail, traceback=tb, fixed=fixed)
        self.errors.append(rec)

    # ------------------------------------------------------------- 汇总
    @property
    def total_cost(self) -> float:
        return round(sum(c["cost_usd"] for c in self.llm_calls), 8)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(c["prompt_tokens"] for c in self.llm_calls)

    @property
    def total_completion_tokens(self) -> int:
        return sum(c["completion_tokens"] for c in self.llm_calls)

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "duration_s": round(time.time() - self._t0, 1),
            "llm_calls": len(self.llm_calls),
            "tool_calls": len(self.tool_calls),
            "errors": len(self.errors),
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "cost_usd": self.total_cost,
            "mock": bool(self.llm_calls and all(c["mock"] for c in self.llm_calls)),
        }

    def save_summary(self, extra: dict | None = None) -> Path:
        data = self.summary()
        if extra:
            data.update(extra)
        p = self.dir / "运行摘要.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


def guard(agent: str, tool: str, fn, *args, logger: RunLogger | None = None, **kwargs):
    """统一的工具调用包装：计时、记录、异常兜底。

    智能体运行中最常见的失败是工具抛异常导致整条链路中断，
    这里保证任何工具失败都被记录并返回结构化错误，由上层决定是否重试。
    """
    t0 = time.time()
    safe_args = {
        k: (v if isinstance(v, (int, float, str, bool, list, tuple, dict, type(None))) else str(v))
        for k, v in kwargs.items()
    }
    try:
        out = fn(*args, **kwargs)
        if logger:
            logger.log_tool(
                agent=agent, tool=tool, args=safe_args,
                result_summary=_summarize(out), ok=True, latency_s=time.time() - t0,
            )
        return out
    except Exception as e:  # noqa: BLE001
        if logger:
            logger.log_error(where=f"{agent}.{tool}({safe_args})", error=e)
            logger.log_tool(
                agent=agent, tool=tool, args=safe_args,
                result_summary=f"ERROR: {type(e).__name__}: {e}", ok=False,
                latency_s=time.time() - t0,
            )
        return {"error": f"{type(e).__name__}: {e}", "tool": tool}


def _summarize(out: Any, limit: int = 600) -> Any:
    """把工具返回值压成可写入日志的小摘要。"""
    if isinstance(out, dict):
        small = {}
        for k, v in out.items():
            if isinstance(v, (int, float, str, bool, type(None))):
                small[k] = v if not isinstance(v, str) or len(v) <= 200 else v[:200] + "…"
            elif isinstance(v, (list, tuple)):
                small[k] = f"list(len={len(v)})"
            elif isinstance(v, dict):
                small[k] = f"dict(keys={list(v)[:8]})"
            else:
                small[k] = str(type(v))
        s = json.dumps(small, ensure_ascii=False)
        return s[:limit]
    s = str(out)
    return s[:limit]
