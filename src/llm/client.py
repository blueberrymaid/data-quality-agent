"""LLM 客户端：OpenAI 兼容接口 + 重试 + 成本记账 + 无 Key 降级。

设计原则（写进报告的关键决策）：
1. 大模型只做**决策与解释**，不做数值计算——所有统计/修复由确定性工具完成，
   这样即使模型输出不稳定也不会污染数据。
2. 每次调用都落盘：模型名、完整提示词、原始响应、token、成本、耗时、是否 mock。
3. 没有 API Key 时自动降级为 `mock`：调用方必须同时提供 `mock_decision`（规则兜底决策），
   因此整条链路在离线状态下依然可跑，且日志里 mock=True 明确标注，不会伪装成真实调用。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..config import LLMConfig
from ..utils import RunLogger


class LLMClient:
    def __init__(self, cfg: LLMConfig, logger: RunLogger):
        self.cfg = cfg
        self.logger = logger
        self._client = None
        self.mock = cfg.mock or not (cfg.api_key and cfg.base_url)
        if self.mock:
            self.logger.log(
                "llm_init", mode="mock",
                reason="LLM_MOCK=1 或缺少 LLM_API_KEY / LLM_BASE_URL",
                model=cfg.model,
            )
        else:
            try:
                from openai import OpenAI  # 延迟导入，缺包时不影响 mock 模式
                self._client = OpenAI(
                    api_key=cfg.api_key, base_url=cfg.base_url,
                    timeout=cfg.timeout, max_retries=0,
                )
                self.logger.log("llm_init", mode="live", model=cfg.model, base_url=cfg.base_url)
            except Exception as e:  # noqa: BLE001
                self.logger.log_error(where="LLMClient.__init__", error=e,
                                      fixed="降级为 mock 模式")
                self.mock = True

    # ------------------------------------------------------------------ 底层
    def _estimate_tokens(self, text: str) -> int:
        """粗略估算：中文按 1.5 字/token，英文按 4 字符/token。"""
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        other = max(len(text) - cjk, 0)
        return int(cjk / 1.5 + other / 4) + 1

    def _price(self, pt: int, ct: int) -> float:
        return (pt / 1000) * self.cfg.price_in_per_1k + (ct / 1000) * self.cfg.price_out_per_1k

    def chat(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        json_mode: bool = False,
        mock_decision: Any = None,
        temperature: float | None = None,
    ) -> str:
        """返回模型原始文本。mock 模式下返回 mock_decision 的 JSON 文本。"""
        if self.mock:
            payload = mock_decision if mock_decision is not None else {"note": "mock 模式无兜底决策"}
            text = json.dumps(payload, ensure_ascii=False) if json_mode else str(payload)
            pt, ct = self._estimate_tokens(system + user), self._estimate_tokens(text)
            self.logger.log_llm(
                agent=agent, model=f"{self.cfg.model}(mock)", prompt=f"[system]\n{system}\n[user]\n{user}",
                response=text, prompt_tokens=pt, completion_tokens=ct,
                cost_usd=0.0, latency_s=0.0, mock=True,
            )
            return text

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_err: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 1):
            t0 = time.time()
            try:
                resp = self._client.chat.completions.create(**kwargs)
                text = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                pt = getattr(usage, "prompt_tokens", None) or self._estimate_tokens(system + user)
                ct = getattr(usage, "completion_tokens", None) or self._estimate_tokens(text)
                self.logger.log_llm(
                    agent=agent, model=self.cfg.model,
                    prompt=f"[system]\n{system}\n[user]\n{user}", response=text,
                    prompt_tokens=pt, completion_tokens=ct,
                    cost_usd=self._price(pt, ct), latency_s=time.time() - t0, attempt=attempt,
                )
                return text
            except Exception as e:  # noqa: BLE001
                last_err = e
                self.logger.log_error(
                    where=f"LLMClient.chat(agent={agent}, attempt={attempt}/{self.cfg.max_retries})",
                    error=e, fixed="指数退避重试" if attempt < self.cfg.max_retries else "重试耗尽，降级规则决策",
                )
                time.sleep(min(2 ** attempt, 8))

        # 全部失败 → 降级
        self.logger.log("llm_fallback", agent=agent, error=str(last_err))
        payload = mock_decision if mock_decision is not None else {"note": "LLM 调用失败且无兜底决策"}
        return json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------ 结构化
    def decide(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        mock_decision: dict,
        temperature: float | None = None,
    ) -> dict:
        """要求模型返回 JSON 对象，并做健壮解析（剥 ```、截取花括号、逐项校验）。"""
        raw = self.chat(agent=agent, system=system, user=user, json_mode=True,
                        mock_decision=mock_decision, temperature=temperature)
        obj = parse_json_loose(raw)
        if obj is None:
            self.logger.log_error(
                where=f"LLMClient.decide(agent={agent})",
                error=f"JSON 解析失败，原始响应前 200 字符：{raw[:200]}",
                fixed="使用规则兜底决策 mock_decision",
            )
            return dict(mock_decision)
        # 兜底键补齐：模型漏字段时用规则值补上，避免下游 KeyError
        for k, v in mock_decision.items():
            obj.setdefault(k, v)
        return obj


def parse_json_loose(raw: str) -> dict | None:
    """尽最大努力从模型输出里抠出 JSON 对象。"""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"value": obj}
    except json.JSONDecodeError:
        pass
    # 截取第一个 { 到最后一个 }
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        try:
            obj = json.loads(text[i:j + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None
