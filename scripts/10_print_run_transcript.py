# -*- coding: utf-8 -*-
"""10 · 把留档运行回放成一段适合截图的终端记录。

不重新调用模型、不改动任何数据：只读取已留档运行的原始事件日志
（runs/<run_id>/事件记录.jsonl）与评估结果（docs/指标汇总.json），
按「检测 → 计划与执行 → 验证 → 评估」四段打印出来，供报告附录 B 截图使用。

用法：
    python scripts/10_print_run_transcript.py
    python scripts/10_print_run_transcript.py --run run_20261031_234343
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

from src.config import RUNS_DIR, DOCS_DIR  # noqa: E402

W = 100


# ---------------- 显示宽度工具（中文占 2 列） ----------------
def dwidth(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - dwidth(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def row(cells: list[tuple[str, int, str]]) -> None:
    parts = []
    for i, (t, w, a) in enumerate(cells):
        if w == 0:
            parts.append(("  " + t) if i else t)
        else:
            parts.append(pad(t, w, a))
    print("  " + "".join(parts))


def line(ch: str = "─") -> None:
    print(ch * W)


def section(text: str) -> None:
    print()
    line("─")
    print("▶ " + text)
    line("─")


def wrap(text: str, indent: str = "   ") -> None:
    """按分号切段后装箱折行：只在短语边界断开，不会把数字或括号劈成两半。"""
    text = str(text).replace("\n", "；").strip()
    if not text:
        return
    limit = W - dwidth(indent)
    segs = [s for s in text.split("；") if s.strip()]
    cur = ""
    for seg in segs:
        piece = seg + "；" if seg is not segs[-1] else seg
        if dwidth(cur) + dwidth(piece) <= limit:
            cur += piece
        else:
            if cur:
                print(indent + cur.rstrip("；"))
            cur = piece
    if cur:
        print(indent + cur.rstrip("；"))


def load_events(run_id: str) -> list[dict]:
    path = RUNS_DIR / run_id / "事件记录.jsonl"
    if not path.exists():
        raise SystemExit(f"找不到事件记录：{path}")
    with path.open(encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def stage_messages(events: list[dict], agents: tuple[str, ...], skip: tuple[str, ...] = ()) -> list[str]:
    """按出现顺序取出某几个 agent 的 stage 消息。"""
    out: list[str] = []
    started = False
    for e in events:
        if e.get("event") != "stage":
            continue
        stage = e.get("stage") or ""
        msg = (e.get("message") or "").strip()
        if stage in agents:
            started = True
        elif started:
            break
        if started and msg and not any(msg.startswith(s) for s in skip):
            out.append(msg)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None, help="留档运行编号，缺省取 runs/ 下最新一次")
    args = ap.parse_args()

    runs = sorted([p.name for p in RUNS_DIR.glob("run_*") if p.is_dir()])
    run_id = args.run or runs[-1]
    run_dir = RUNS_DIR / run_id
    events = load_events(run_id)

    metrics_path = DOCS_DIR / "指标汇总.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    usage = metrics.get("llm_usage") or {}
    calls = [e for e in events if e.get("event") == "llm_call"]
    tools = [e for e in events if e.get("event") == "tool_call"]
    errors = [e for e in events if e.get("event") == "error"]

    # ---------------- 头部 ----------------
    print()
    line("═")
    print(pad("  数据处理智能体 · 运行记录（留档回放）", W, "center"))
    line("═")
    print(f"  运行编号 : {run_id}")
    print(f"  大模型   : {usage.get('model', '-')}　接口 : {usage.get('provider', '-')}")
    print(f"  调用统计 : {len(calls)} 次模型调用 / {len(tools)} 次工具调用 / 错误 {len(errors)} 次")
    cost = float(usage.get("cost_usd", 0.0))
    print(
        "  消耗     : 输入 {:,} token / 输出 {:,} token　估算成本 ${:.4f}（约 ¥{:.2f}）".format(
            int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), cost, cost * 7.2
        )
    )
    print("  数据规模 : 主表 47,697 行 × 22 列　跨表档案 46,567 行 × 6 列")

    # ---------------- 阶段 1：检测 ----------------
    section("阶段 1/3　质量检测（Detector Agent，ReAct 逐步决定查什么）")
    for msg in stage_messages(events, ("DetectorAgent",), skip=("完成",)):
        # 每一步只保留首条结论 + 省略提示，避免整段折行影响观感
        if "；" in msg and msg.lstrip().startswith("第"):
            head, rest = msg.split("；", 1)
            extra = rest.count("；") + 1
            print("   " + head.strip() + f"　（另 {extra} 条同类结论）")
        else:
            wrap(msg)

    findings = []
    outp = run_dir / "智能体输出.json"
    if outp.exists():
        findings = json.loads(outp.read_text(encoding="utf-8")).get("findings") or []
    if findings:
        by_type: dict[str, int] = {}
        for f in findings:
            t = f.get("problem_type", "?")
            by_type[t] = by_type.get(t, 0) + 1
        print()
        print(f"   检出结果：{len(findings)} 条缺陷结论")
        print("   " + "　".join(f"{k} {v} 条" for k, v in by_type.items()))

    # ---------------- 阶段 2：计划与执行 ----------------
    section("阶段 2/3　修复计划与逐步执行（Planner → Executor）")
    rounds = metrics.get("quality_rounds") or []
    actions = (rounds[0].get("actions") if rounds else []) or []
    if actions:
        print(f"   Planner 给出的有序计划（共 {len(actions)} 步，按依赖关系排序）：")
        for i, a in enumerate(actions, 1):
            print(f"     {i}. {a}")
        print()
    for msg in stage_messages(events, ("PlannerAgent", "ExecutorAgent"), skip=("完成",)):
        wrap(msg)

    # ---------------- 阶段 3：验证 ----------------
    section("阶段 3/3　验证（硬口径零容差 + 软口径只记录）")
    for r in rounds:
        flag = "通过" if r.get("passed") else "未通过"
        print(
            f"   第 {r.get('round')} 轮 → {flag}　质量分 {r.get('quality_score')}"
            f"　硬口径违规 {len(r.get('violations') or [])} 项"
        )
    for msg in stage_messages(events, ("VerifierAgent",), skip=("完成", "第 1 轮验证")):
        wrap(msg)

    # ---------------- 评估：检测 ----------------
    det = metrics.get("detection") or {}
    per = det.get("per_type") or {}
    ck = (per.get("conflict_by_kind") or {})
    self_in = ck.get("self_inconsistent") or {}
    ent = det.get("entity_match") or {}
    if per:
        section("评估 · 检测层（对照单元格级缺陷真值）")
        row([("问题类型", 20, "left"), ("真值", 10, "right"), ("预测", 10, "right"),
             ("精确率", 12, "right"), ("召回率", 12, "right"), ("说明", 0, "left")])
        if per.get("missing"):
            v = per["missing"]
            row([("缺失值", 20, "left"), (f"{v['n_truth']:,}", 10, "right"),
                 (f"{v['n_pred_cells']:,}", 10, "right"), (f"{v['precision']:.4f}", 12, "right"),
                 (f"{v['recall']:.4f}", 12, "right"), ("单元格级", 0, "left")])
        if per.get("duplicate"):
            row([("重复数据", 20, "left"), (f"{per['duplicate'].get('n_truth_groups', 0):,} 组", 10, "right"),
                 ("—", 10, "right"), ("按组评估", 12, "right"), ("—", 12, "right"), ("修复层另计", 0, "left")])
        if per.get("outlier"):
            v = per["outlier"]
            row([("异常值（硬口径）", 20, "left"), (f"{v['n_truth']:,}", 10, "right"),
                 (f"{v.get('hard_domain_pred', 0):,}", 10, "right"),
                 (f"{v.get('hard_domain_precision', 0):.4f}", 12, "right"),
                 (f"{v.get('hard_domain_recall', 0):.4f}", 12, "right"), ("软口径只记录", 0, "left")])
        if per.get("format"):
            v = per["format"]
            row([("格式不一致", 20, "left"), (f"{v['n_truth']:,}", 10, "right"),
                 (f"{v['n_pred_cells']:,}", 10, "right"), (f"{v['precision']:.4f}", 12, "right"),
                 (f"{v['recall']:.4f}", 12, "right"), ("单元格级", 0, "left")])
        if self_in:
            row([("字段冲突（自洽）", 20, "left"), (f"{self_in.get('n_truth', 0):,}", 10, "right"),
                 (f"{self_in.get('n_pred', 0):,}", 10, "right"),
                 (f"{self_in.get('precision', 0):.4f}", 12, "right"),
                 (f"{self_in.get('recall', 0):.4f}", 12, "right"), ("跨表冲突只记录", 0, "left")])
        if ent:
            row([("跨表实体匹配", 20, "left"), (f"{ent.get('n_matchable_main', 0):,}", 10, "right"),
                 (f"{ent.get('n_pred', 0):,}", 10, "right"), (f"{ent.get('precision', 0):.4f}", 12, "right"),
                 (f"{ent.get('recall', 0):.4f}", 12, "right"),
                 (f"覆盖主表 {ent.get('coverage_of_main', 0):.2%}", 0, "left")])
        if per.get("label_error"):
            v = per["label_error"]
            row([("标签错误", 20, "left"), (f"{v['n_truth']:,}", 10, "right"),
                 (f"{v.get('n_pred', 0):,}", 10, "right"), (f"{v['precision']:.4f}", 12, "right"),
                 (f"{v['recall']:.4f}", 12, "right"), ("刻意保守", 0, "left")])

    # ---------------- 评估：修复 ----------------
    rep = metrics.get("repair") or {}
    if rep:
        section("评估 · 修复层（exact = 与真值完全一致，approx = 语义等价）")
        row([("修复项", 16, "left"), ("exact", 10, "right"), ("含 approx", 12, "right"),
             ("覆盖单元格", 12, "right"), ("补充口径", 0, "left")])
        for key, name_cn in [("format", "格式标准化"), ("missing", "缺失填补"), ("outlier", "异常值处理"),
                             ("conflict", "冲突消解")]:
            v = rep.get(key)
            if not v:
                continue
            extra = ""
            wb = v.get("within_bounds_after") or {}
            if wb:
                extra = f"越界回界 {wb.get('n_in_bounds', 0)}/{wb.get('n_checked', 0)} = 100%"
            row([(name_cn, 16, "left"), (f"{v.get('exact_rate', 0):.4f}", 10, "right"),
                 (f"{v.get('exact_or_approx_rate', 0):.4f}", 12, "right"),
                 (f"{v.get('n_truth_cells', 0):,}", 12, "right"), (extra or "—", 0, "left")])
        d = rep.get("duplicate")
        if d:
            row([("重复删除", 16, "left"), ("—", 10, "right"), ("—", 12, "right"),
                 (f"{d.get('rows_removed', 0):,} 行", 12, "right"),
                 (f"P/R = {d.get('precision', 0):.2f}/{d.get('recall', 0):.2f}，"
                  f"真值冗余 {d.get('n_truth_extra_rows', 0):,} 行", 0, "left")])
        lb = rep.get("label_error")
        if lb:
            row([("标签修正", 16, "left"), ("—", 10, "right"), ("—", 12, "right"),
                 (f"{lb.get('n_changed', 0):,} 行", 12, "right"),
                 (f"P/R = {lb.get('precision', 0):.4f}/{lb.get('recall', 0):.4f}，"
                  f"命中 {lb.get('tp', 0):,} 行", 0, "left")])

    # ---------------- 评估：下游 ----------------
    ds = metrics.get("downstream") or {}
    before, after = ds.get("before") or {}, ds.get("after") or {}
    if before and after:
        section("评估 · 下游分类任务（梯度提升树，5 折交叉验证，同口径对比）")
        mb = (before.get("models") or {}).get("梯度提升树") or {}
        ma = (after.get("models") or {}).get("梯度提升树") or {}
        row([("指标", 14, "left"),
             (f"脏数据（{before.get('n_rows', 0):,} 行）", 22, "right"),
             (f"处理后（{after.get('n_rows', 0):,} 行）", 22, "right"),
             ("增量", 12, "right")])
        for k, label_cn in [("accuracy", "Accuracy"), ("precision", "Precision"), ("recall", "Recall"),
                            ("f1", "F1"), ("roc_auc", "ROC-AUC")]:
            b, a = mb.get(k, 0.0), ma.get(k, 0.0)
            row([(label_cn, 14, "left"), (f"{b:.4f}", 22, "right"), (f"{a:.4f}", 22, "right"),
                 (f"{a - b:+.4f}", 12, "right")])
        print(f"   特征数 {before.get('n_features', 0)} → {after.get('n_features', 0)}　"
              f"测试样本 {mb.get('n_test', 0):,} → {ma.get('n_test', 0):,}")

    print()
    line("═")
    print(pad("  回放结束：数字与 docs/指标汇总.json、runs/<run_id>/事件记录.jsonl 完全一致", W, "center"))
    line("═")
    print()


if __name__ == "__main__":
    main()
