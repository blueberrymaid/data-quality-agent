"""把交付所需内容整理成一个可提交的压缩包。

默认排除：`.env`（含 API Key）、`.pylibs/`（第三方依赖）、`.pipcache/`、`__pycache__/`、
以及可重新生成的逐步中间快照与历史运行留档（只保留最新一次完整留档）。

用法：
    python scripts/08_package.py
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT.parent / "数据处理智能体_提交包.zip"

# 必须整目录打包
WHOLE_DIRS = ["src", "scripts", "docs"]

# 数据：保留原始、注入后、处理后终版与真值；中间快照可重新生成，不打包
DATA_FILES = [
    "data/raw/主表_银行营销_原始.csv",
    "data/raw/客户档案表_原始.csv",
    "data/raw/数据来源说明.txt",
    "data/injected/主表_注入缺陷.csv",
    "data/injected/客户档案表_注入缺陷.csv",
    "data/processed/主表_智能体处理后.csv",
    "data/ground_truth/缺陷真值.json",
    "data/ground_truth/主表_干净基准.csv",
]

SINGLE_FILES = ["README.md", "requirements.txt", "docs/交付物清单.md", ".gitignore", ".env.example"]

EXCLUDE_DIRS = {"__pycache__", ".pylibs", ".pipcache", ".venv", ".git"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}


def latest_run() -> Path | None:
    runs = [p for p in (ROOT / "runs").glob("run_*") if (p / "事件记录.jsonl").exists()]
    if not runs:
        return None
    return max(runs, key=lambda p: (p / "事件记录.jsonl").stat().st_mtime)


def wanted(path: Path) -> bool:
    parts = set(path.relative_to(ROOT).parts)
    if parts & EXCLUDE_DIRS:
        return False
    if path.suffix in EXCLUDE_SUFFIX:
        return False
    if path.name in {".env"}:
        return False
    return True


def collect() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []

    for d in WHOLE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and wanted(p):
                items.append((p, str(p.relative_to(ROOT)).replace("\\", "/")))

    for rel in DATA_FILES + SINGLE_FILES:
        p = ROOT / rel
        if p.is_file() and wanted(p):
            items.append((p, rel.replace("\\", "/")))

    run = latest_run()
    if run:
        keep = ["事件记录.jsonl", "运行摘要.json", "智能体运行摘要.md", "智能体输出.json"]
        for name in keep:
            p = run / name
            if p.is_file():
                items.append((p, f"runs/{run.name}/{name}"))

    # 去重并排序（docs 里的清单与 SINGLE_FILES 可能重复）
    seen, uniq = set(), []
    for p, rel in items:
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append((p, rel))
    return sorted(uniq, key=lambda x: x[1])


def main() -> int:
    items = collect()
    if OUT.exists():
        OUT.unlink()
    total = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p, rel in items:
            z.write(p, arcname=rel)
            total += p.stat().st_size
    print(f"[输出] {OUT}")
    print(f"[统计] {len(items)} 个文件，原始 {total / 1048576:.1f} MB，压缩后 {OUT.stat().st_size / 1048576:.1f} MB")
    run = latest_run()
    if run:
        print(f"[留档] 运行留档取最新一次：{run.name}")
    print("[提示] 已排除 .env / .pylibs / .pipcache / __pycache__ / 逐步中间快照 / 历史运行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
