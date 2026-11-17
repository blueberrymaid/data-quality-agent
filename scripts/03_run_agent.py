"""步骤 3：把注入缺陷的数据交给多智能体流水线处理。

用法：
    python scripts/03_run_agent.py            # mock 模式（无需 API Key，默认）
    python scripts/03_run_agent.py --live     # 用 .env 里的真实 API
"""
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

import pandas as pd

from src.agents.orchestrator import Orchestrator
from src.agents.reporter import ReporterAgent
from src.config import (DOCS_DIR, INJ_MAIN, INJ_REF, PRED_MAIN, RUNS_DIR,
                        TRUTH_FILE, load_env_file, resolve_llm_config)


def load_table(path: Path) -> pd.DataFrame:
    # 全部按字符串读入：脏数据里的 "1,234"、"?"、"unknown" 不能被自动转成 NaN
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def main() -> None:
    load_env_file()
    if "--live" in sys.argv:
        import os
        os.environ["LLM_MOCK"] = "0"
    cfg = resolve_llm_config()
    print(f"[配置] LLM 模式={'mock' if cfg.mock else 'live'} 模型={cfg.model} 端点={cfg.base_url or '(未设置)'}")

    dirty = load_table(INJ_MAIN)
    ref = load_table(INJ_REF)
    truth = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
    print(f"[数据] 主表 {dirty.shape[0]} 行 × {dirty.shape[1]} 列；参照表 {ref.shape[0]} 行 × {ref.shape[1]} 列")

    orch = Orchestrator(main_df=dirty, ref_df=ref, truth=truth)
    results = orch.run(downstream=True)

    # 保存清洗后的表
    clean_df = results.pop("clean_df")
    clean_df.to_csv(PRED_MAIN, index=False, encoding="utf-8-sig")
    print(f"[输出] 清洗后主表 → {PRED_MAIN}（{clean_df.shape[0]} 行 × {clean_df.shape[1]} 列）")

    # 汇报智能体
    reporter = ReporterAgent(orch.ctx)
    rep = reporter.run(dict(results))
    results["report"] = {"summary_text": rep.get("summary_text")}

    def default(o):
        if is_dataclass(o):
            return asdict(o)
        if isinstance(o, (set, tuple)):
            return list(o)
        return str(o)

    # 供 evaluate 使用：检测结果、匹配映射、下游指标
    run_dir = orch.logger.dir
    findings = orch.ctx.state.get("findings", []) or []
    payload = {
        "run_id": results.get("run_id"),
        "findings": [asdict(f) if is_dataclass(f) else f for f in findings],
        "match_map": {str(k): int(v) for k, v in (orch.ctx.state.get("match_map") or {}).items()},
        "downstream": results.get("downstream", {}),
        "llm_usage": results.get("llm_usage", {}),
        "quality_rounds": results.get("quality_rounds", []),
    }
    (run_dir / "智能体输出.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=default), encoding="utf-8")

    print("\n=== 运行结果 ===")
    print(json.dumps({k: v for k, v in results.items()
                      if k not in ("clean_df", "downstream")},
                     ensure_ascii=False, indent=2, default=default)[:2500])
    ds = results.get("downstream") or {}
    print("\n=== 下游任务 ===")
    print(json.dumps(ds.get("delta", ds), ensure_ascii=False, indent=2, default=default)[:1200])
    print(f"\n[日志] {run_dir}")


if __name__ == "__main__":
    main()
