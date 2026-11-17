"""步骤 4：用缺陷真值评估"检测准不准 / 修得对不对"，并汇总下游指标。

产出 docs/指标汇总.json（报告直接引用）。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

import pandas as pd

from src.config import (DOCS_DIR, INJ_MAIN, INJ_REF, PRED_MAIN, RUNS_DIR,
                        TRUTH_DIR, TRUTH_FILE)
from src.pipeline.evaluate import evaluate_all


def load(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def latest_run() -> Path:
    cands = sorted([p for p in RUNS_DIR.iterdir() if (p / "智能体输出.json").exists()],
                   key=lambda p: (p / "智能体输出.json").stat().st_mtime, reverse=True)
    if not cands:
        raise SystemExit("未找到运行产物，请先执行 scripts/03_run_agent.py")
    return cands[0]


def main() -> None:
    run_dir = latest_run()
    payload = json.loads((run_dir / "智能体输出.json").read_text(encoding="utf-8"))
    truth = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
    dirty = load(INJ_MAIN)
    ref = load(INJ_REF)
    processed = load(PRED_MAIN)
    clean = load(TRUTH_DIR / "主表_干净基准.csv")

    findings = [SimpleNamespace(**f) for f in payload.get("findings", [])]
    match_map = {int(k): int(v) for k, v in (payload.get("match_map") or {}).items()}

    res = evaluate_all(dirty, processed, clean, truth, findings, match_map,
                       downstream=payload.get("downstream"), ref=ref)
    res["run_id"] = payload.get("run_id") or run_dir.name
    res["llm_usage"] = payload.get("llm_usage")
    res["quality_rounds"] = payload.get("quality_rounds")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = DOCS_DIR / "指标汇总.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 检测评估（对比注入真值）===")
    for k, v in res["detection"]["per_type"].items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
    print("  实体匹配:", json.dumps(res["detection"]["entity_match"], ensure_ascii=False))
    print("\n=== 修复评估（对比干净基准）===")
    for k, v in res["repair"].items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
    print("\n=== 下游任务 ===")
    ds = res.get("downstream") or {}
    print("  delta:", json.dumps(ds.get("delta", {}), ensure_ascii=False))
    print(f"\n[输出] {out}")


if __name__ == "__main__":
    main()
