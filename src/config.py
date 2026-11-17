"""全局配置：路径、缺陷注入参数、模型与计价参数。

所有可调参数集中在此，便于复现实验（报告里"参数"一节直接引用本文件）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INJECTED_DIR = DATA_DIR / "injected"
PROCESSED_DIR = DATA_DIR / "processed"
TRUTH_DIR = DATA_DIR / "ground_truth"
RUNS_DIR = PROJECT_ROOT / "runs"
DOCS_DIR = PROJECT_ROOT / "docs"

for _d in (RAW_DIR, INJECTED_DIR, PROCESSED_DIR, TRUTH_DIR, RUNS_DIR, DOCS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 数据文件
RAW_MAIN = RAW_DIR / "主表_银行营销_原始.csv"
RAW_REF = RAW_DIR / "客户档案表_原始.csv"
INJ_MAIN = INJECTED_DIR / "主表_注入缺陷.csv"
INJ_REF = INJECTED_DIR / "客户档案表_注入缺陷.csv"
TRUTH_FILE = TRUTH_DIR / "缺陷真值.json"

PRED_MAIN = PROCESSED_DIR / "主表_智能体处理后.csv"
PRED_REF = PROCESSED_DIR / "客户档案表_智能体处理后.csv"

# ---------------------------------------------------------------- 随机种子
SEED = 20251231

# ---------------------------------------------------------------- 缺陷注入比例
@dataclass
class InjectConfig:
    """每类缺陷的注入强度（相对全表记录数）。"""
    missing_ratio: float = 0.06          # 缺失值：约 6% 单元格
    dup_ratio: float = 0.04              # 重复：追加约 4% 完全重复行
    near_dup_ratio: float = 0.015        # 近似重复：大小写/空白扰动
    outlier_ratio: float = 0.02          # 异常值
    format_ratio: float = 0.05           # 类型/格式不一致
    conflict_ratio: float = 0.02         # 字段冲突
    label_error_ratio: float = 0.015     # 标签错误
    ref_noise_ratio: float = 0.15        # 跨表档案表的字段噪声比例


INJECT = InjectConfig()

# ---------------------------------------------------------------- 目标字段
TARGET = "y"
POSITIVE = "yes"

NUMERIC_COLS = ["age", "balance", "day", "duration", "campaign", "pdays", "previous"]
CATEGORICAL_COLS = [
    "job", "marital", "education", "default", "housing",
    "loan", "contact", "month", "poutcome",
]

# ---------------------------------------------------------------- LLM 配置
@dataclass
class LLMConfig:
    """模型与计价。base_url/api_key 从环境变量读取，不落盘。"""
    base_url: str = os.environ.get("LLM_BASE_URL", "")
    api_key: str = os.environ.get("LLM_API_KEY", "")
    model: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    temperature: float = 0.0
    max_tokens: int = 800
    # 计价（美元 / 1K token），按所用模型填写；未填则成本记为 0 并标注"未定价"
    price_in_per_1k: float = float(os.environ.get("LLM_PRICE_IN", "0.00015"))
    price_out_per_1k: float = float(os.environ.get("LLM_PRICE_OUT", "0.0006"))
    timeout: int = 90
    max_retries: int = 3
    # mock=True 时不发起真实请求，用于离线跑通链路
    mock: bool = os.environ.get("LLM_MOCK", "1") == "1"


LLM = LLMConfig()

# ---------------------------------------------------------------- 智能体运行参数
@dataclass
class AgentConfig:
    max_verify_rounds: int = 3     # 检测-修复-校验 最大迭代轮数
    max_react_steps: int = 9       # 单个检测 agent 的 ReAct 最大步数（8 个检测工具 + 1 步收尾）
    residual_tolerance: float = 0.02   # 残留问题行占比低于此值即认为收敛
    cv_folds: int = 5


AGENT = AgentConfig()


def _api_key() -> str:
    """密钥来源优先级：环境变量 LLM_API_KEY > LLM_API_KEY_FILE 指向的文件（建议放在项目之外）。"""
    key = os.environ.get("LLM_API_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("LLM_API_KEY_FILE", "").strip()
    if not key_file:
        return ""
    p = Path(os.path.expanduser(key_file))
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return ""


def mask_secret(text: str) -> str:
    """任何需要展示配置的地方都用它，避免密钥进入日志、文档或截图。"""
    text = str(text or "")
    if not text:
        return "(未设置)"
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:5]}{'*' * 8}{text[-2:]}"


def resolve_llm_config() -> LLMConfig:
    """支持运行中（如读入 .env 或传 --live）重新读取环境变量。"""
    return LLMConfig(
        mock=os.environ.get("LLM_MOCK", "1") == "1",
        base_url=os.environ.get("LLM_BASE_URL", LLM.base_url),
        api_key=_api_key(),
        model=os.environ.get("LLM_MODEL", LLM.model),
        temperature=float(os.environ.get("LLM_TEMPERATURE", LLM.temperature)),
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", LLM.max_tokens)),
        price_in_per_1k=float(os.environ.get("LLM_PRICE_IN", LLM.price_in_per_1k)),
        price_out_per_1k=float(os.environ.get("LLM_PRICE_OUT", LLM.price_out_per_1k)),
    )


def load_env_file(path: Path | None = None) -> None:
    """极简 .env 解析（避免额外依赖 python-dotenv）。"""
    path = path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)
