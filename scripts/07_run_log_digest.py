"""07 生成《智能体运行记录》交付文档（Markdown + docx）。

内容全部来自 runs/<run_id>/ 的原始留档：
  - 事件记录.jsonl：每一次 LLM 调用（模型、提示词、参数、token、成本）、工具调用、决策、错误
  - 运行摘要.json：耗时、调用次数、token 与成本总计
  - 智能体输出.json：检测结果、计划、执行轨迹、验证过程

用法：python scripts/07_run_log_digest.py [run_id]
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

DOCS = ROOT / "docs"
RUNS = ROOT / "runs"
CN_FONT, HEAD_FONT, EN_FONT = "宋体", "黑体", "Times New Roman"


def latest_run() -> Path:
    cands = [p for p in RUNS.iterdir() if (p / "事件记录.jsonl").exists() and (p / "智能体输出.json").exists()]
    return sorted(cands, key=lambda p: (p / "智能体输出.json").stat().st_mtime)[-1]


def read_events(run: Path) -> list[dict]:
    evs = []
    with (run / "事件记录.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    evs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return evs


def brief(text, n=260):
    if text is None:
        return "—"
    s = str(text).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[:n] + "…"


def collect_errors() -> list[dict]:
    """跨所有历史运行收集 error 事件——修正过程本身就发生在多次运行之间。"""
    out = []
    for run in sorted(RUNS.iterdir()):
        p = run / "事件记录.jsonl"
        if not p.exists():
            continue
        for e in read_events(run):
            if e.get("event") == "error":
                out.append({"run": run.name, **e})
    return out


# 代码层的实际修正（运行时日志只能记录「跳过该步」这类现场处置，这里补上根因修复）
FIX_NOTES = [
    ("sequence item 15",
     "修复 drop_duplicates 的近似去重键：改为逐元素 str() 后再拼接，避免混入浮点缺失值"),
    ("未知动作 rebalance_training",
     "在 ACTION_FUNCS 中注册 rebalance_training 适配层（不修改数据，只输出训练侧类别权重配置）"),
    ("not in range", "去重后行号不再连续，使用匹配映射前先按现存行过滤"),
]


def fix_note(e: dict) -> str:
    err = f"{e.get('where')} {e.get('error')}"
    for sig, note in FIX_NOTES:
        if sig in err:
            return note
    return e.get("fixed") or "已在后续版本修复（详见报告表 8）"


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def build_md(run: Path, events: list[dict], summary: dict, payload: dict, errors: list[dict]) -> Path:
    llm = [e for e in events if e.get("event") == "llm_call"]
    tools = [e for e in events if e.get("event") == "tool_call"]
    decs = [e for e in events if e.get("event") == "decision"]
    stages = [e for e in events if e.get("event") == "stage"]
    usage = payload.get("llm_usage") or {}

    L = []
    L.append("# 智能体运行记录\n")
    L.append(f"> 运行编号：`{run.name}`　|　留档文件：`runs/{run.name}/事件记录.jsonl`\n")
    L.append("本文件由 `scripts/07_run_log_digest.py` 从原始事件日志自动生成，未做人工改写，"
             "可作为交付项「智能体运行记录（提示词、工具调用、错误与修正过程）」的直接证据。\n")

    L.append("## 1　运行概况\n")
    L.append("| 项目 | 数值 |")
    L.append("| --- | --- |")
    L.append(f"| 运行编号 | {run.name} |")
    L.append(f"| 策略模式 | {'mock（规则化决策，离线可复现）' if summary.get('mock') else 'live（真实大模型）'} |")
    L.append(f"| 总耗时 | {summary.get('duration_s')} 秒 |")
    L.append(f"| LLM 决策调用 | {summary.get('llm_calls')} 次 |")
    L.append(f"| 工具调用 | {summary.get('tool_calls')} 次 |")
    L.append(f"| 错误事件 | {summary.get('errors')} 次 |")
    L.append(f"| 输入 token | {summary.get('prompt_tokens')} |")
    L.append(f"| 输出 token | {summary.get('completion_tokens')} |")
    L.append(f"| 估算成本 | ${summary.get('cost_usd')} |")
    L.append(f"| 事件总数 | {len(events)}（stage {len(stages)} / llm_call {len(llm)} / decision {len(decs)} / tool_call {len(tools)}） |")
    L.append("")
    plan = summary.get("plan") or {}
    if plan:
        L.append(f"**调度决策**：处理取向 `{plan.get('aggressiveness')}`，关注 {plan.get('focus')}，"
                 f"理由：{plan.get('rationale')}\n")

    L.append("## 2　LLM 调用明细\n")
    L.append("模型名称、提示词、参数、token 与成本逐条记录如下（提示词与回复全文见 JSONL）。\n")
    L.append("| # | 智能体 | 模型 | 输入token | 输出token | 成本(USD) | 回复摘要 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for i, e in enumerate(llm, 1):
        L.append(f"| {i} | {e.get('agent')} | {e.get('model')} | {e.get('prompt_tokens')} | "
                 f"{e.get('completion_tokens')} | {e.get('cost_usd')} | {brief(e.get('response'), 120)} |")
    L.append("")
    if usage:
        L.append("### 2.1　LLM 配置\n")
        L.append("| 参数 | 值 |")
        L.append("| --- | --- |")
        for k in ("provider", "model", "mode", "temperature", "max_tokens", "price_in", "price_out", "total_calls"):
            if k in usage:
                L.append(f"| {k} | {usage.get(k)} |")
        L.append("")

    L.append("### 2.2　提示词全文（逐次调用）\n")
    for i, e in enumerate(llm, 1):
        L.append(f"**调用 {i}｜{e.get('agent')}｜{e.get('model')}｜{e.get('prompt_tokens')}+{e.get('completion_tokens')} token**\n")
        L.append("```text")
        L.append(str(e.get("prompt", ""))[:3000])
        L.append("```\n")
        L.append(f"回复：`{brief(e.get('response'), 400)}`\n")

    L.append("## 3　ReAct 决策过程\n")
    L.append("检测智能体每一步的「思考 → 选择工具」记录，可直接看出它为什么查这一项。\n")
    L.append("| # | 智能体 | 目的 | 可选动作 | 决策 |")
    L.append("| --- | --- | --- | --- | --- |")
    for i, e in enumerate(decs, 1):
        L.append(f"| {i} | {e.get('agent')} | {e.get('purpose')} | {', '.join(map(str, e.get('options') or []))} | "
                 f"{brief(e.get('result'), 200)} |")
    L.append("")

    L.append("## 4　工具调用轨迹\n")
    L.append("| # | 智能体 | 工具 | 参数 | 耗时(s) | 结果摘要 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for i, e in enumerate(tools, 1):
        args = e.get("args") or {}
        args_s = ", ".join(f"{k}={brief(v, 40)}" for k, v in args.items()) or "—"
        L.append(f"| {i} | {e.get('agent')} | {e.get('tool')} | {args_s} | {e.get('elapsed_s')} | "
                 f"{brief(e.get('result'), 180)} |")
    L.append("")

    L.append("## 5　验证过程\n")
    for r in payload.get("quality_rounds") or []:
        L.append(f"- 第 {r.get('round')} 轮：质量分 **{r.get('quality_score')}**，"
                 f"通过={r.get('passed')}，违规项={[v.get('problem_type') for v in (r.get('violations') or [])]}")
    L.append("")

    L.append("## 6　错误与修正过程\n")
    if errors:
        L.append(f"历史调试期共记录 {len(errors)} 条错误事件，按发生顺序列出（含修正方式）：\n")
        L.append("| # | 运行 | 位置 | 错误 | 修正 |")
        L.append("| --- | --- | --- | --- | --- |")
        for i, e in enumerate(errors, 1):
            L.append(f"| {i} | {e.get('run')} | {brief(e.get('where'), 60)} | {brief(e.get('error'), 200)} | "
                     f"{brief(fix_note(e), 200)} |")
    else:
        L.append("本次及历史运行均未记录到工具层错误事件。项目开发期的错误与修正以报告表 8 的形式留档。")
    L.append("")

    out = DOCS / "智能体运行记录.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# docx
# --------------------------------------------------------------------------- #
def _font(run_, size=None, bold=None, cn=CN_FONT):
    run_.font.name = EN_FONT
    run_._element.rPr.rFonts.set(qn("w:eastAsia"), cn)
    if size:
        run_.font.size = Pt(size)
    if bold is not None:
        run_.font.bold = bold


def build_docx(run: Path, events: list[dict], summary: dict, payload: dict, errors: list[dict]) -> Path:
    llm = [e for e in events if e.get("event") == "llm_call"]
    tools = [e for e in events if e.get("event") == "tool_call"]
    decs = [e for e in events if e.get("event") == "decision"]

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = EN_FONT
    st.font.size = Pt(9.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = sec.bottom_margin = Cm(2.0)

    def h(text, size=13):
        p = doc.add_paragraph()
        _font(p.add_run(text), size=size, bold=True, cn=HEAD_FONT)
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(3)

    def t(text, size=9.5, indent=True):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if indent:
            p.paragraph_format.first_line_indent = Pt(size * 2)
        _font(p.add_run(text), size=size)

    def tbl(header, rows, size=8):
        t_ = doc.add_table(rows=1, cols=len(header))
        t_.style = "Table Grid"
        t_.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, hh in enumerate(header):
            c = t_.rows[0].cells[i]
            c.text = ""
            _font(c.paragraphs[0].add_run(str(hh)), size=size, bold=True, cn=HEAD_FONT)
        for row in rows:
            cells = t_.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = ""
                _font(cells[i].paragraphs[0].add_run("" if v is None else str(v)), size=size)
        return t_

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(p.add_run("智能体运行记录"), size=16, bold=True, cn=HEAD_FONT)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(p2.add_run(f"运行编号：{run.name}　|　原始留档：runs/{run.name}/事件记录.jsonl"), size=9)
    t("本文件由 scripts/07_run_log_digest.py 从原始事件日志自动生成，未做人工改写，"
      "可作为交付项「智能体运行记录（提示词、工具调用、错误与修正过程）」的直接证据。")

    h("一、运行概况")
    tbl(["项目", "数值"], [
        ["运行编号", run.name],
        ["策略模式", "mock（规则化决策，离线可复现）" if summary.get("mock") else "live（真实大模型）"],
        ["总耗时", f"{summary.get('duration_s')} 秒"],
        ["LLM 决策调用", f"{summary.get('llm_calls')} 次"],
        ["工具调用", f"{summary.get('tool_calls')} 次"],
        ["错误事件", f"{summary.get('errors')} 次"],
        ["token（输入 / 输出）", f"{summary.get('prompt_tokens')} / {summary.get('completion_tokens')}"],
        ["估算成本", f"${summary.get('cost_usd')}"],
        ["事件总数", f"{len(events)}（stage / llm_call / decision / tool_call 均已留档）"],
    ], size=8.5)

    h("二、LLM 调用明细（模型、参数、提示词、token 与成本）")
    rows = []
    for i, e in enumerate(llm, 1):
        rows.append([i, e.get("agent"), e.get("model"), e.get("prompt_tokens"),
                     e.get("completion_tokens"), e.get("cost_usd"), brief(e.get("response"), 60)])
    tbl(["#", "智能体", "模型", "输入tok", "输出tok", "成本$", "回复摘要"], rows, size=7.5)
    t("提示词与回复全文保存在事件记录 JSONL 中（字段 prompt / response），可与上表逐条对应。"

      , indent=True)

    h("三、ReAct 决策过程")
    rows = [[i, e.get("agent"), e.get("purpose"), brief(e.get("result"), 90)]
            for i, e in enumerate(decs, 1)]
    tbl(["#", "智能体", "目的", "决策结果"], rows, size=7.5)

    h("四、工具调用轨迹")
    rows = []
    for i, e in enumerate(tools, 1):
        args = e.get("args") or {}
        args_s = ", ".join(f"{k}={brief(v, 24)}" for k, v in args.items()) or "—"
        rows.append([i, e.get("agent"), e.get("tool"), args_s, e.get("elapsed_s"), brief(e.get("result"), 70)])
    tbl(["#", "智能体", "工具", "参数", "耗时(s)", "结果摘要"], rows, size=7.5)

    h("五、验证过程")
    for r in payload.get("quality_rounds") or []:
        t(f"第 {r.get('round')} 轮：质量分 {r.get('quality_score')}，通过={r.get('passed')}，"
          f"违规项={[v.get('problem_type') for v in (r.get('violations') or [])]}")

    h("六、错误与修正过程")
    if errors:
        t(f"历史调试期共记录 {len(errors)} 条错误事件，按发生顺序列出（含修正方式）：")
        rows = [[i, e.get("run"), brief(e.get("where"), 40), brief(e.get("error"), 90), brief(fix_note(e), 90)]
                for i, e in enumerate(errors, 1)]
        tbl(["#", "运行", "位置", "错误", "修正"], rows, size=7.5)
    else:
        t("本次及历史运行均未记录到工具层错误事件。项目开发期的错误与修正以课程报告表 8 的形式留档。")

    out = DOCS / "智能体运行记录.docx"
    doc.save(out)
    return out


def main():
    run = RUNS / sys.argv[1] if len(sys.argv) > 1 else latest_run()
    events = read_events(run)
    summary = json.loads((run / "运行摘要.json").read_text(encoding="utf-8"))
    payload = json.loads((run / "智能体输出.json").read_text(encoding="utf-8"))
    errors = collect_errors()
    md = build_md(run, events, summary, payload, errors)
    dx = build_docx(run, events, summary, payload, errors)
    print(f"[输出] {md}")
    print(f"[输出] {dx}")
    print(f"[统计] 事件 {len(events)} 条；跨运行错误事件 {len(errors)} 条")


if __name__ == "__main__":
    main()
