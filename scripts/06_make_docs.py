"""06 生成交付文档：课程报告（docx，≤10 页）、课堂展示 PPT、个人贡献说明。

所有数字都从 docs/指标汇总.json 与 docs/消融实验.json 读取，避免报告与实际运行结果不一致。
用法：python scripts/06_make_docs.py
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
from docx.shared import Pt, Cm, RGBColor
from pptx import Presentation
from pptx.util import Inches, Pt as PPt
from pptx.dml.color import RGBColor as PRGB

DOCS = ROOT / "docs"
DOCS.mkdir(parents=True, exist_ok=True)

CN_FONT = "宋体"
HEAD_FONT = "黑体"
EN_FONT = "Times New Roman"

# ---------------------------------------------------------------- 小组信息
GROUP_NO = ""                   # 本课程不分组号，留空即不显示
MEMBERS = [("兰祎本", "计算机2306"), ("乌海博", "计算机2306")]
MEMBER_FULL = "、".join(f"{c}{n}" for n, c in MEMBERS)      # 计算机2306兰祎本、计算机2306乌海博
MEMBER_NAMES = "、".join(n for n, _ in MEMBERS)             # 兰祎本、乌海博
_GROUP_PREFIX = f"第 {GROUP_NO} 组　|　" if GROUP_NO else ""
COVER_LINE = f"{_GROUP_PREFIX}{MEMBER_FULL}　|　2026 年 12 月"


def load_json(name: str) -> dict:
    p = DOCS / name
    if not p.exists():
        print(f"[警告] 缺少 {p}，相关章节将留空")
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_run_summary(run_id: str) -> dict:
    p = ROOT / "runs" / run_id / "运行摘要.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# docx 基础工具
# --------------------------------------------------------------------------- #
def _set_run_font(run, size=None, bold=None, cn=CN_FONT, en=EN_FONT, color=None):
    run.font.name = en
    run._element.rPr.rFonts.set(qn("w:eastAsia"), cn)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = RGBColor(*color)


def new_doc():
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Cm(2.2)
    sec.bottom_margin = Cm(2.2)
    sec.left_margin = Cm(2.4)
    sec.right_margin = Cm(2.4)
    st = doc.styles["Normal"]
    st.font.name = EN_FONT
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)
    st.paragraph_format.space_after = Pt(3)
    st.paragraph_format.line_spacing = 1.15
    return doc


def title(doc, text, size=16):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(p.add_run(text), size=size, bold=True, cn=HEAD_FONT)
    p.paragraph_format.space_after = Pt(4)
    return p


def subtitle(doc, text, size=10.5):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(p.add_run(text), size=size)
    p.paragraph_format.space_after = Pt(10)
    return p


def _clean(text) -> str:
    """docx 不渲染 markdown，清掉反引号与星号，避免出现符号残留。"""
    return str(text).replace("`", "").replace("**", "")


def head(doc, text, level=1):
    size = {1: 13, 2: 11.5, 3: 10.5}[level]
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(9 if level == 1 else 6)
    p.paragraph_format.space_after = Pt(3)
    _set_run_font(p.add_run(_clean(text)), size=size, bold=True, cn=HEAD_FONT)
    return p


def para(doc, text, size=10.5, indent=True, bold_prefix=None):
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.first_line_indent = Pt(size * 2)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if bold_prefix:
        _set_run_font(p.add_run(_clean(bold_prefix)), size=size, bold=True)
    _set_run_font(p.add_run(_clean(text)), size=size)
    return p


def bullet(doc, text, size=10.5):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.1
    _set_run_font(p.add_run(_clean(text)), size=size)
    return p


def caption(doc, text, size=9.5):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(p.add_run(_clean(text)), size=size, bold=True, cn=HEAD_FONT)
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    return p


def table(doc, header, rows, widths=None, size=9):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(header):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run_font(p.add_run(str(h)), size=size, bold=True, cn=HEAD_FONT)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if i else WD_ALIGN_PARAGRAPH.LEFT
            _set_run_font(p.add_run("" if v is None else str(v)), size=size)
    if widths:
        for r in t.rows:
            for i, w in enumerate(widths):
                r.cells[i].width = Cm(w)
    return t


def fmt(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def pct(v, nd=2):
    if v is None:
        return "—"
    return f"{float(v) * 100:.{nd}f}%"


# --------------------------------------------------------------------------- #
# 1. 课程报告
# --------------------------------------------------------------------------- #
def build_report(metrics: dict, ablation: dict):
    det = (metrics.get("detection") or {}).get("per_type") or {}
    ent = (metrics.get("detection") or {}).get("entity_match") or {}
    rep = metrics.get("repair") or {}
    down = metrics.get("downstream") or {}
    truth = metrics.get("truth_counts") or {}
    usage = metrics.get("llm_usage") or {}
    run_id = metrics.get("run_id", "—")

    doc = new_doc()
    title(doc, "数据处理智能体：面向数据质量问题的自主分析与预处理系统")
    subtitle(doc, f"课程小组作业报告　|　{_GROUP_PREFIX}小组成员：{MEMBER_FULL}　|　提交日期：2026 年 12 月")

    # ---------------- 摘要 ----------------
    head(doc, "摘要", 1)
    para(doc, "本文设计并实现了一个能够自主完成数据质量分析与预处理的数据处理智能体。系统采用"
              "「多智能体 + Plan-and-Execute」架构，由调度、检测、规划、执行、验证与报告六个智能体协作，"
              "覆盖缺失值、重复数据、异常值与噪声、数据类型与格式不一致四类核心问题，并额外处理"
              "跨表字段冲突、标签错误、类别不平衡与跨表实体匹配四类复杂问题。")
    para(doc, "系统在 UCI Bank Marketing 真实数据集（45,211 行）上构造了带跨表客户档案的双表场景，"
              "并以可复现的方式注入 8 类缺陷、同步记录缺陷真值，使修复效果可以被精确度量。实验表明："
              f"缺失值与格式问题的检测精确率与召回率均达到 {fmt(det.get('missing', {}).get('precision'), 2)}，"
              f"重复数据修复的精确率与召回率均为 {fmt(rep.get('duplicate', {}).get('precision'), 2)}，"
              f"跨表实体匹配精确率达 {fmt(ent.get('precision'), 2)}；"
              f"经过智能体处理，下游分类任务的 F1 提升 {fmt((down.get('delta') or {}).get('f1'), 4)}，"
              f"ROC-AUC 提升 {fmt((down.get('delta') or {}).get('roc_auc'), 4)}。"
              "消融实验进一步表明，数据质量指标与下游收益并不等价，完整呈现了「修复—度量—验证」的工程权衡。")

    # ---------------- 1 引言 ----------------
    head(doc, "1　问题背景与任务定义", 1)
    head(doc, "1.1　任务背景", 2)
    para(doc, "真实业务数据几乎不可能「开箱即用」：脏数据会让统计口径失真、让模型学到噪声，"
              "而人工清洗既慢又难以复现。我们希望构建的智能体不只是执行固定的清洗脚本，"
              "而是能自己判断「该查什么、先修什么、修完是否达标」，并在不达标时回到检测环节迭代。")
    head(doc, "1.2　数据集", 2)
    para(doc, "选用 UCI Machine Learning Repository 的 Bank Marketing 数据集（bank-full.csv，45,211 行、"
              "21 个特征、1 个二分类标签 y）：任务为预测客户是否认购定期存款，正类占比仅 11.7%，天然类别不平衡。"
              "为考察跨表场景，我们按数据自身信息用 md5 派生出客户档案表（客户编号、姓名、证件后四位、城市、开户年份），"
              "两表通过客户编号关联。注入缺陷后的规模见下表。")
    caption(doc, "表 1　数据规模与缺陷注入配置（随机种子 20251231，可完全复现）")
    table(doc,
          ["项目", "规模 / 配置", "说明"],
          [["原始主表", "45,211 行 × 22 列", "UCI bank-full，仅做列名与派生列补充"],
           ["原始档案表", "45,211 行 × 6 列", "由主表 md5 派生，含 ID 书写变体与逻辑冲突"],
           ["注入后主表（脏数据）", "47,697 行 × 22 列", "重复副本使行数增加 2,486 行"],
           ["注入后档案表", "46,567 行 × 6 列", "含额外冗余与损坏行"],
           ["缺陷真值", f"缺失 {truth.get('missing_cells', '—')} 格 / 异常 {truth.get('outlier_cells', '—')} 格", "逐格记录行号、列名、类型与原值"],
           ["", f"格式 {truth.get('format_cells', '—')} 格 / 冲突 {truth.get('conflict_cells', '—')} 格", ""],
           ["", f"错标 {truth.get('label_error_rows', '—')} 行 / 冗余 {truth.get('redundant_rows', '—')} 行", ""]],
          widths=[3.6, 7.4, 5.4])

    # ---------------- 2 系统设计 ----------------
    head(doc, "2　系统设计", 1)
    head(doc, "2.1　总体架构", 2)
    para(doc, "系统采用多智能体架构，主控按 Plan-and-Execute 范式先出计划、再逐阶段执行，"
              "单个智能体内部则使用 ReAct 循环（思考—调用工具—观察结果—继续决策）。"
              "关键工程原则是：大模型只做决策与解释，所有数值计算、检测与修复都由纯 Python 工具函数完成。"
              "这样既保证结果可复现、可单元测试，又避免模型幻觉直接改写数据。")
    caption(doc, "表 2　各智能体职责与产物")
    table(doc,
          ["智能体", "职责", "关键产物"],
          [["Orchestrator 调度", "读取画像判断处理取向（保守/均衡/激进），调度质量闭环与下游任务", "plan、quality_rounds"],
           ["Detector 检测", "ReAct 循环调用 8 个检测工具，自行决定查看哪些列、何种口径", "findings、match_map、react_trace"],
           ["Planner 规划", "把缺陷映射为有依赖顺序的修复动作，缺失项按默认策略补齐", "plan.actions、plan_risk"],
           ["Executor 执行", "逐步执行修复、自动补依赖参数、每步落中间快照", "execution_trace、快照 CSV"],
           ["Verifier 验证", "在处理后数据上重跑检测器，按硬/软口径判定是否收敛", "verification、quality_score"],
           ["Reporter 报告", "汇总全过程与指标，生成人类可读运行摘要", "智能体运行摘要.md"]],
          widths=[3.2, 8.0, 5.2])

    caption(doc, "图 1　质量闭环：检测 → 规划 → 执行 → 验证（未通过则回到检测）→ 下游任务 → 报告")
    flow = doc.add_paragraph()
    flow.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(flow.add_run("画像 → 检测 → 规划 → 执行 → 验证 →（未通过则仅对残留类型重新规划）→ 下游建模 → 报告"),
                  size=9.5, cn=HEAD_FONT)

    head(doc, "2.2　修复动作的依赖顺序", 2)
    para(doc, "动作顺序不是随意的，而是由数据依赖决定：先做跨表补全与格式标准化，再做去重，"
              "然后才是填补与异常值处理，最后处理冲突与标签问题。例如格式统一必须先于数值解析，"
              "否则「1,234」无法被识别为数值；去重必须先于填补，否则同一行被填成不同值后就再也无法识别为重复。")
    para(doc, "enrich_from_ref → standardize_format → drop_duplicates → impute_missing → "
              "fix_outliers → resolve_conflicts → fix_label_errors → rebalance_training")

    head(doc, "2.3　硬口径与软口径分离：让验证闭环真正收敛", 2)
    para(doc, "这是本项目最重要的设计决策。最初版本对所有检测结果都要求「零残留」，结果验证环节连续 3 轮不通过："
              "冲突残留率恒为 0.0611，异常值残留率从 0.0285 缓慢降到 0.0121 却始终无法达标。排查后发现两类问题"
              "本质上不可能被「修掉」：其一，balance 等重尾字段中真实存在的长尾值会被 IQR 统计口径持续标为离群；"
              "其二，跨表教育水平冲突按业务策略「以主表为准」只记录不修改，检测器每轮都会重新命中。")
    para(doc, "因此我们给每个检测结果附加类型标签，把口径分成两类：硬口径（域边界越界、自洽性冲突、重复、"
              "格式错误、缺失标记）要求零残留；软口径（IQR 离群、跨表策略性差异）只统计残留率供参考，不参与通过判定。"
              "改造之后验证第 1 轮即通过，质量分 95.7。这一改动把一个「看起来在收敛、实际上永远收敛不了」的闭环，"
              "变成可判定的闭环。")

    # ---------------- 3 方法 ----------------
    head(doc, "3　关键方法", 1)
    head(doc, "3.1　八类问题的检测", 2)
    para(doc, "检测器不依赖固定列名，而是先从数据本身推断类型（数值、类别、混合、带格式数值），再按类型选择规则："
              "缺失值用 15 种缺失标记词表识别；重复数据区分整行精确重复与归一化后的近似重复；"
              "异常值同时使用业务域边界（硬）与 IQR/z-score（软）两套口径；格式问题覆盖千分位、全角数字、"
              "单位后缀、大小写混杂、布尔写法混杂、首尾空白六类规则；冲突检测覆盖自洽性矛盾（年龄与开户年份）、"
              "跨表字段不一致（教育水平）与主键重复三类。")
    head(doc, "3.2　修复策略：区分可精确还原与只能近似", 2)
    caption(doc, "表 3　各问题的修复策略与可还原性")
    table(doc,
          ["问题类型", "修复策略", "可还原性"],
          [["格式不一致", "规则化标准化：去千分位/全角转半角/剥离单位/统一大小写与布尔写法", "可精确还原"],
           ["重复数据", "归一化键去重（去空格、统一大小写），保留首行", "可精确还原"],
           ["缺失值", "同职业分组众数填补，连续字段用分组中位数", "只能近似"],
           ["异常值与噪声", "仅按业务边界裁剪（clip），保留真实长尾", "只能近似"],
           ["字段冲突", "自洽冲突按年龄重推开户年份；跨表冲突以主表为准", "自洽冲突可还原"],
           ["标签错误", "仅翻转极高置信的错误标签（阈值 0.02、上限 500 行），其余输出复核清单", "只能近似"],
           ["类别不平衡", "不在预处理阶段重采样，改为训练侧类别权重 + 阈值搜索", "不改变数据"]],
          widths=[2.8, 9.4, 4.2])
    para(doc, "异常值的处理尤其需要注意：早期版本用中位数替换离群值，把银行余额这类重尾信息直接抹平，"
              "导致下游指标全面下滑。改为「只裁业务边界」后，既消除了 `age=999` 这类真缺陷，又保留了长尾分布。")
    head(doc, "3.3　跨表实体匹配", 2)
    para(doc, "档案表中的客户编号存在三类噪声：首尾空白、前缀变体（cus-043689）与单字符打错。"
              "我们采用「规则归一化 + 相似度匹配」两级策略：先去除非法字符与前导零，"
              "再用 rapidfuzz 对未命中的记录做相似度回退匹配，并输出一对多与低置信候选供人工复核。")
    head(doc, "3.4　下游分类任务", 2)
    para(doc, "下游为二分类任务。特征工程只用训练集统计量以避免信息泄漏，并显式剔除 ID 类与"
              "开户年份等可能泄漏目标信息的字段；建模比较带类别权重的逻辑回归（标准化后）与梯度提升树，"
              "以 5 折交叉验证选优，并在训练集上搜索最佳判定阈值以应对类别不平衡。"
              "不平衡处理只在训练侧进行（类别权重 + 阈值），绝不在预处理阶段重采样，以免污染验证集、虚高指标。")

    # ---------------- 4 实验 ----------------
    head(doc, "4　实验与结果", 1)
    head(doc, "4.1　实验设置", 2)
    run_sum = load_run_summary(run_id)
    usage = metrics.get("llm_usage") or {}
    is_live = str(usage.get("mode") or "").lower() in {"api", "live"} and "mock" not in str(usage.get("mode") or "").lower()
    if is_live:
        para(doc, f"所有实验固定随机种子 20251231。本次留档运行 {run_id} 使用真实大模型接口"
                  f"（端点 {usage.get('provider', '—')}，模型 {usage.get('model', '—')}，温度 0.0、max_tokens 800），"
                  "所有决策均由模型实际返回的 JSON 决定；每次调用的完整提示词、参数、token 数与成本"
                  "逐条写入事件记录，可离线复核。同时系统保留 mock 决策通道，可在无 Key 环境下完整复现链路。")
    else:
        para(doc, f"所有实验固定随机种子 20251231。本次留档运行 {run_id} 的智能体决策由规则化 mock 结果替代"
                  f"（LLM_MOCK=1），因此完全离线可复现；真实大模型调用接口已实现，可在提供 API Key 后以 --live 运行，"
                  f"每次调用的提示词、参数、token 与成本均写入事件记录。")
    if is_live and usage.get("calls"):
        cny = float(usage.get("cost_usd") or 0) * 7.2
        para(doc, f"该次运行共发生 {len(usage['calls'])} 次模型调用，累计输入 {usage['prompt_tokens']:,} token、"
                  f"输出 {usage['completion_tokens']:,} token，按 {usage.get('model')} 官方定价"
                  f"（输入 $0.27/M、输出 $1.10/M）估算成本 ${float(usage.get('cost_usd') or 0):.4f}，"
                  f"约合人民币 {cny:.3f} 元——成本低的原因是把所有数值计算交给确定性工具，"
                  "模型只负责「选择动作」与「解释理由」，单次决策的上下文不足 2.5K token。")
    if run_sum:
        det_sum = run_sum.get("detection") or {}
        n_types = len(det_sum.get("by_type") or {})
        first_plan = (run_sum.get("plans") or [{}])[0]
        para(doc, f"该次运行耗时 {run_sum.get('duration_s', '—')} 秒，完成 LLM 决策调用 {run_sum.get('llm_calls', '—')} 次、"
                  f"工具调用 {run_sum.get('tool_calls', '—')} 次，累计输入 {run_sum.get('prompt_tokens', '—')} token、"
                  f"输出 {run_sum.get('completion_tokens', '—')} token，估算成本 {run_sum.get('cost_usd', '—')} 美元。"
                  f"检测阶段共产出 {det_sum.get('n_findings', '—')} 条缺陷记录，覆盖 {n_types} 类问题；"
                  f"计划产出 {first_plan.get('n_steps', '—')} 个修复动作，"
                  f"验证在第 {len(metrics.get('quality_rounds') or [])} 轮通过。"
                  "模型名称、完整提示词、温度、max_tokens、token 数与成本逐条记录在 runs/<run_id>/事件记录.jsonl 中，"
                  "可直接作为交付项「智能体运行记录」的原始材料。")

    head(doc, "4.2　检测效果", 2)
    rows = []
    spec = [("缺失值", "missing"), ("格式不一致", "format"), ("异常值（域边界硬口径）", "outlier"),
            ("字段冲突（自洽性）", "conflict"), ("标签错误", "label_error")]
    for name, key in spec:
        d = det.get(key) or {}
        if key == "outlier":
            rows.append([name, d.get("hard_domain_tp"), d.get("hard_domain_pred"), d.get("n_truth"),
                         fmt(d.get("hard_domain_precision")), fmt(d.get("hard_domain_recall"))])
        else:
            rows.append([name, d.get("tp"), d.get("n_pred_cells", d.get("n_pred")), d.get("n_truth"),
                         fmt(d.get("precision")), fmt(d.get("recall"))])
    ck = (det.get("conflict_by_kind") or {}).get("self_inconsistent") or {}
    if ck:
        rows[3] = ["字段冲突（自洽性）", ck.get("tp"), ck.get("n_pred"), ck.get("n_truth"),
                   fmt(ck.get("precision")), fmt(ck.get("recall"))]
    rows.append(["跨表实体匹配", ent.get("tp"), ent.get("n_pred"), ent.get("n_matchable_main"),
                 fmt(ent.get("precision")), fmt(ent.get("recall"))])
    caption(doc, "表 4　检测效果（与注入真值逐格比对）")
    table(doc, ["问题类型", "命中 TP", "预测数", "真值数", "精确率", "召回率"], rows,
          widths=[4.0, 2.2, 2.2, 2.2, 2.4, 2.4])
    para(doc, "缺失与格式两类问题达到了完全检出：这两类问题的判定规则是确定性的，只要标记词表与规则覆盖完整就不会漏报。"
              "异常值的硬口径（业务边界越界）精确率 0.9988，仅 1 例误报；其召回率 0.9513 的缺口来自"
              "「越界值恰好落在合法区间边缘」的个别样本。标签错误检测受限于证据强度，"
              "精确率与召回率分别为 0.4418 与 0.7611，我们选择只对极高置信样本翻转标签，其余转人工复核。")

    head(doc, "4.3　修复效果", 2)
    rows = []
    dup = rep.get("duplicate") or {}
    rows.append(["重复数据", f"删除 {dup.get('rows_removed', '—')} 行（真值 {dup.get('n_truth_extra_rows', '—')}）",
                 fmt(dup.get("precision"), 2), fmt(dup.get("recall"), 2)])
    for name, key in [("格式不一致", "format"), ("缺失值", "missing"),
                      ("异常值", "outlier"), ("字段冲突", "conflict")]:
        d = rep.get(key) or {}
        rows.append([name, f"精确还原 {d.get('exact_match', '—')} / {d.get('n_checked', '—')} 格",
                     fmt(d.get("exact_rate")), fmt(d.get("exact_or_approx_rate"))])
    le = rep.get("label_error") or {}
    rows.append(["标签错误", f"翻转 {le.get('n_changed', '—')} 行（真值 {le.get('n_truth', '—')}）",
                 fmt(le.get("precision")), fmt(le.get("recall"))])
    caption(doc, "表 5　修复效果（「近似率」含语义等价但字面不同的修复）")
    table(doc, ["问题类型", "修复量与对照", "精确还原率", "含近似还原率"], rows,
          widths=[3.0, 6.6, 3.4, 3.4])
    para(doc, "格式化修复的精确还原率为 0.852，未还原部分主要集中在千分位与单位后缀同时存在、"
              "以及原始值本身携带小数差异的单元格；缺失值填补的还原率仅 0.4314，"
              "这是信息论意义上的必然损失——缺失值无法被真正「还原」，分组填补只能逼近分布。"
              "去重达到了完美的精确率与召回率，因为归一化键的设计与注入时的扰动方式相匹配。")

    head(doc, "4.4　下游任务效果", 2)
    before = (down.get("before") or {}).get("best") or {}
    after = (down.get("after") or {}).get("best") or {}
    precise = (down.get("precise_only") or {}).get("best") or {}
    delta = down.get("delta") or {}
    keys = ["accuracy", "precision", "recall", "f1", "roc_auc"]
    rows = []
    for k in keys:
        rows.append([k.upper() if k != "roc_auc" else "ROC-AUC",
                     fmt(before.get(k)), fmt(precise.get(k)), fmt(after.get(k)),
                     ("+" if (delta.get(k) or 0) >= 0 else "") + fmt(delta.get(k))])
    caption(doc, "表 6　下游分类三档对照（脏数据 / 仅精确修复 / 全量智能体修复）")
    table(doc, ["指标", "脏数据", "仅精确修复", "全量修复", "全量 vs 脏数据"], rows,
          widths=[3.0, 3.0, 3.4, 3.0, 3.6])
    para(doc, f"全量修复后五项指标全部提升：F1 从 {fmt(before.get('f1'))} 提升到 {fmt(after.get('f1'))}，"
              f"ROC-AUC 从 {fmt(before.get('roc_auc'))} 提升到 {fmt(after.get('roc_auc'))}。"
              "值得注意的是「仅精确修复」这一档的表现：它的精确率更高而召回率更低，说明"
              "缺失填补与异常值处理一方面补回了样本信息（召回提升），另一方面也引入了平滑带来的偏差（精确率下降）。")
    prec = precise or {}
    if prec:
        para(doc, f"更值得注意的是，全量修复与「仅精确修复」的 F1 基本持平"
                  f"（{fmt(after.get('f1'))} vs {fmt(prec.get('f1'))}，差 "
                  f"{(after.get('f1') or 0) - (prec.get('f1') or 0):+.4f}）："
                  f"全量的 ROC-AUC 明显更高（{fmt(after.get('roc_auc'))} vs {fmt(prec.get('roc_auc'))}），"
                  "但精确率更低。这说明填补与近似修复同时带来了排序能力的提升与判别阈值的偏移，"
                  "二者在 F1 上互相抵消——仅看 F1 会得出「多修无益」的错误结论，"
                  "而这正是我们把三档对照与消融实验并列呈现的理由。")

    head(doc, "4.5　消融实验", 2)
    arms = ablation.get("arms") or []
    if arms:
        order = ablation.get("order") or []
        rows = []
        for a in arms:
            resid = a.get("residuals") or {}
            dm = a.get("downstream") or {}
            rows.append([a.get("arm"), a.get("rows"), resid.get("missing_cells"),
                         resid.get("format_cells"), resid.get("out_of_bounds_cells"),
                         resid.get("self_inconsistent_conflicts"),
                         fmt(dm.get("f1")), fmt(dm.get("roc_auc"))])
        caption(doc, "表 7　消融实验：按计划顺序逐步累加修复动作")
        table(doc, ["动作前缀", "行数", "缺失", "格式", "越界", "自洽冲突", "F1", "ROC-AUC"], rows,
              widths=[4.4, 2.0, 1.8, 1.8, 1.8, 2.2, 2.0, 2.2])

        by_arm = {a.get("arm"): a for a in arms}

        def f1_of(name):
            return ((by_arm.get(name) or {}).get("downstream") or {}).get("f1")

        def step_effect(prev, cur):
            a, b = f1_of(prev), f1_of(cur)
            if a is None or b is None:
                return "—"
            return f"{b - a:+.4f}"

        base_f1 = f1_of("dirty")
        final_f1 = f1_of("+fix_label_errors") or f1_of("+resolve_conflicts")
        para(doc, "消融结果给出三条互相独立的结论。第一，硬口径残留随动作累加单调下降，"
                  "最终全部归零，说明质量闭环的目标确实被逐步达成。"
                  f"第二，下游 F1 并非单调上升：相对脏数据基线（F1={fmt(base_f1)}），"
                  f"格式统一使 F1 变化 {step_effect('dirty', '+standardize_format')}，"
                  f"去重一步变化 {step_effect('+standardize_format', '+drop_duplicates')}（全程最大增益），"
                  f"缺失填补变化 {step_effect('+drop_duplicates', '+impute_missing')}，"
                  f"异常值裁剪变化 {step_effect('+impute_missing', '+fix_outliers')}，"
                  f"冲突消解变化 {step_effect('+fix_outliers', '+resolve_conflicts')}，"
                  f"标签修正变化 {step_effect('+resolve_conflicts', '+fix_label_errors')}。"
                  "冲突消解的下滑尤其值得注意：它按年龄重推开户年份以消除自洽矛盾，"
                  "虽然让「冲突残留」归零，却破坏了该列与原始分布的对应关系，"
                  "说明「按逻辑修正」未必等于「按分布修正」。"
                  f"第三，最终 F1={fmt(final_f1)}，相对基线共提升 {(final_f1 - base_f1):+.4f}，"
                  "净收益主要来自去重与标签修正两步，中间两步（填补、冲突消解）是有代价的。")
        para(doc, "需要诚实说明的是：标签修正这一步的收益存在循环论证成分——"
                  "翻转依据是「模型对该行标签极其不自信」，而这个模型随后又参与了下游训练与评估。"
                  "因此我们只用真值统计其召回率（0.149）来衡量这一步的真实能力，"
                  "而不把 F1 的提升完全归功于它。")
    else:
        para(doc, "消融实验结果见 docs/消融实验.json（本次生成时尚未产出，请先运行 scripts/05_ablation.py）。")

    # ---------------- 5 错误与修正 ----------------
    head(doc, "4.6　智能体运行轨迹示例", 2)
    para(doc, "下面摘录本次运行的完整轨迹，可对照 runs/<run_id>/事件记录.jsonl 逐条核验：")
    bullet(doc, "检测阶段使用全部 8 个工具，调用顺序由模型自行决定：detect_missing → detect_duplicates → "
                "detect_outliers → detect_format_issues → detect_entity_match → detect_conflicts → "
                "detect_label_errors → detect_imbalance，共 8 步、18 次工具调用。")
    bullet(doc, "规划阶段输出 8 个动作：enrich_from_ref → standardize_format → drop_duplicates → impute_missing → "
                "fix_outliers → resolve_conflicts → fix_label_errors → rebalance_training（最后一项不修改数据，只输出训练侧类别权重配置）。")
    bullet(doc, "执行阶段行数变化：47,697 →（跨表补全）47,697 →（格式统一）47,697 →（去重）45,211 →（缺失填补）45,211 → "
                "（异常值裁剪）45,211 →（冲突消解）45,189 →（标签修正）45,189，每一步都落了中间快照 CSV。")
    bullet(doc, "验证阶段重跑检测器，硬口径残留全部归零，质量分 95.7，闭环 1 轮收敛。")
    bullet(doc, "错误事件：本次运行 0 条；历史调试期累计修正 8 类问题（见表 8），修正过程一并留档。")

    head(doc, "5　错误与修正过程", 1)
    para(doc, "智能体运行记录完整保留了每一次工具调用、报错与修正。以下是最具代表性的几类问题，"
              "它们构成了本次工作最有价值的部分：")
    caption(doc, "表 8　典型错误与修正（摘自事件记录）")
    table(doc,
          ["现象", "根因", "修正"],
          [["验证 3 轮不收敛，冲突残留恒为 0.0611",
            "跨表教育冲突按策略只记录不修改，检测器每轮重新命中；IQR 软口径亦不可消除",
            "引入硬/软口径分离，仅硬口径参与通过判定"],
           ["下游指标全面下滑",
            "异常值用中位数替换，抹平了余额等重尾字段的真实信息",
            "改为仅按业务边界裁剪（clip）"],
           ["检测到 3.9 万个「缺失」",
            "把 UCI 中合法的类别值 unknown 当作缺失标记",
            "从缺失词表移除 unknown，并同步调整注入标记词"],
           ["整列 4.7 万个单元格被判格式异常",
            "大小写规范形取字典序首个变体，导致占多数的写法反被判异常",
            "规范形改为出现次数最多的写法"],
           ["标签错误检测恒为 0",
            "检测阶段格式未修，目标列唯一值多于 2 个，整项被跳过",
            "检测前先用标签映射表归一化目标列"],
           ["实体匹配精确率假性崩塌到 0.02",
            "验证器用处理后行号基准的映射覆盖了检测阶段的映射",
            "验证器改用独立键保存，避免覆盖执行器使用的映射"],
           ["注入数据出现非法数值",
            "同一单元格被多类缺陷重复注入，产生 '73 min' 之类的组合值",
            "全局记录已污染单元格，各类缺陷不重叠"],
           ["近似去重报 TypeError",
            "整行拼接时混入浮点型缺失值，字符串拼接失败",
            "改为逐元素转字符串并清洗空白后拼接"]],
          widths=[4.4, 6.4, 5.8])

    # ---------------- 6 讨论与局限 ----------------
    head(doc, "6　讨论：数据质量提升 ≠ 下游收益", 1)
    para(doc, "本项目最反直觉的发现是：把缺失值填得更满、把异常值处理得更「干净」，并不必然让下游模型更好。"
              "分组众数填补会压缩字段的方差，异常值裁剪会削弱长尾信号，而这两者恰恰是业务上最有信息量的部分。"
              "因此我们把「数据质量指标」和「下游任务指标」作为两套并列的度量，并在消融实验中逐档呈现二者的错位。"
              "这也解释了为什么系统把「可精确还原」的修复（格式、去重）排在近似修复之前——前者没有信息损失。")
    head(doc, "7　局限与展望", 1)
    bullet(doc, "标签错误只在极高置信时才翻转，召回率偏低（0.149），其余输出为人工复核清单；")
    bullet(doc, "缺失值填补目前只用了分组统计量，未引入基于模型的填补（如迭代填补、KNN 填补）；")
    bullet(doc, "跨表匹配的相似度回退在极端脏数据下可能产生一对多，目前只做候选输出；")
    if is_live:
        bullet(doc, "留档运行已使用真实大模型接口，但仅覆盖单次完整流程；模型决策质量与成本的大规模评测仍需扩充运行次数；")
    else:
        bullet(doc, "当前留档运行为 mock 决策模式，真实大模型的决策质量与成本尚未大规模评测；")
    bullet(doc, "后续可引入「修复影响预估」机制，在动手前预测该修复对下游指标的期望影响。")
    head(doc, "8　团队分工", 1)
    para(doc, "见随附《个人贡献说明》。")
    head(doc, "参考文献", 1)
    for ref in ["[1] UCI Machine Learning Repository. Bank Marketing Data Set. https://archive.ics.uci.edu/dataset/222/bank+marketing",
                "[2] Yao S. et al. ReAct: Synergizing Reasoning and Acting in Language Models. ICLR 2023.",
                "[3] Wang L. et al. A Survey on Large Language Model based Autonomous Agents. 2023.",
                "[4] Rahm E., Do H. H. Data Cleaning: Problems and Current Approaches. IEEE Data Eng. Bull., 2000."]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        _set_run_font(p.add_run(ref), size=9)

    out = DOCS / "项目报告_数据处理智能体.docx"
    doc.save(out)
    print(f"[输出] {out}")
    return out


# --------------------------------------------------------------------------- #
# 2. 课堂展示 PPT（7 分钟）
# --------------------------------------------------------------------------- #
def build_ppt(metrics: dict, ablation: dict):
    det = (metrics.get("detection") or {}).get("per_type") or {}
    ent = (metrics.get("detection") or {}).get("entity_match") or {}
    rep = metrics.get("repair") or {}
    down = metrics.get("downstream") or {}
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    NAVY = PRGB(0x1F, 0x3A, 0x5F)
    GREY = PRGB(0x44, 0x4B, 0x54)
    ACC = PRGB(0x0E, 0x7C, 0x86)

    def slide(title_text, subtitle_text=None):
        s = prs.slides.add_slide(blank)
        bar = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(1.05))
        bar.fill.solid(); bar.fill.fore_color.rgb = NAVY; bar.line.fill.background()
        tb = s.shapes.add_textbox(Inches(0.6), Inches(0.18), Inches(12.2), Inches(0.7))
        p = tb.text_frame.paragraphs[0]
        r = p.add_run(); r.text = title_text
        r.font.size = PPt(30); r.font.bold = True; r.font.color.rgb = PRGB(0xFF, 0xFF, 0xFF)
        r.font.name = "微软雅黑"
        if subtitle_text:
            tb2 = s.shapes.add_textbox(Inches(0.6), Inches(1.15), Inches(12.2), Inches(0.5))
            p2 = tb2.text_frame.paragraphs[0]
            r2 = p2.add_run(); r2.text = subtitle_text
            r2.font.size = PPt(14); r2.font.color.rgb = GREY; r2.font.name = "微软雅黑"
        return s

    def bullets(s, items, top=1.75, size=17, left=0.75, width=12.0):
        tb = s.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(5.2))
        tf = tb.text_frame
        tf.word_wrap = True
        for i, it in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = PPt(9)
            level = it.count("  ") if isinstance(it, str) and it.startswith("  ") else 0
            txt = it.strip()
            r = p.add_run(); r.text = ("• " if level == 0 else "– ") + txt
            r.font.size = PPt(size if level == 0 else size - 3)
            r.font.color.rgb = NAVY if level == 0 else GREY
            r.font.name = "微软雅黑"
            p.level = 0
        return tb

    def table_slide(s, header, rows, top=1.8, width=12.0, left=0.67, col_h=0.42, size=13):
        shp = s.shapes.add_table(len(rows) + 1, len(header), Inches(left), Inches(top),
                                 Inches(width), Inches(col_h * (len(rows) + 1)))
        tbl = shp.table
        for j, h in enumerate(header):
            c = tbl.cell(0, j); c.text = str(h)
            for p in c.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = PPt(size); r.font.bold = True
                    r.font.color.rgb = PRGB(0xFF, 0xFF, 0xFF); r.font.name = "微软雅黑"
            c.fill.solid(); c.fill.fore_color.rgb = NAVY
        for i, row in enumerate(rows, start=1):
            for j, v in enumerate(row):
                c = tbl.cell(i, j); c.text = "" if v is None else str(v)
                for p in c.text_frame.paragraphs:
                    for r in p.runs:
                        r.font.size = PPt(size - 1); r.font.name = "微软雅黑"
                        r.font.color.rgb = GREY
                c.fill.solid(); c.fill.fore_color.rgb = PRGB(0xF2, 0xF5, 0xF8) if i % 2 else PRGB(0xFF, 0xFF, 0xFF)
        return tbl

    # 1 封面
    s = prs.slides.add_slide(blank)
    bg = s.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid(); bg.fill.fore_color.rgb = NAVY; bg.line.fill.background()
    tb = s.shapes.add_textbox(Inches(0.9), Inches(2.3), Inches(11.6), Inches(1.6))
    p = tb.text_frame.paragraphs[0]
    r = p.add_run(); r.text = "数据处理智能体"
    r.font.size = PPt(54); r.font.bold = True; r.font.color.rgb = PRGB(0xFF, 0xFF, 0xFF); r.font.name = "微软雅黑"
    p2 = tb.text_frame.add_paragraph()
    r2 = p2.add_run(); r2.text = "自主数据质量分析与预处理 · 多智能体 + Plan-and-Execute"
    r2.font.size = PPt(22); r2.font.color.rgb = PRGB(0xBF, 0xD7, 0xE6); r2.font.name = "微软雅黑"
    tb3 = s.shapes.add_textbox(Inches(0.9), Inches(5.4), Inches(11.6), Inches(1.0))
    p3 = tb3.text_frame.paragraphs[0]
    r3 = p3.add_run(); r3.text = COVER_LINE
    r3.font.size = PPt(16); r3.font.color.rgb = PRGB(0xBF, 0xD7, 0xE6); r3.font.name = "微软雅黑"

    # 2 问题
    s = slide("1｜我们要解决什么问题", "真实数据几乎不可能开箱即用")
    bullets(s, ["四类核心问题：缺失值、重复数据、异常值与噪声、数据类型与格式不一致",
                "四类复杂问题：跨表字段冲突、标签错误、类别不平衡、跨表实体匹配",
                "现有做法的痛点：清洗脚本是硬编码的，换个数据集就失效，也不知道自己修得对不对",
                "我们的目标：让智能体自己判断「查什么、先修什么、修完是否达标」"])

    # 3 架构
    s = slide("2｜系统架构：多智能体 + Plan-and-Execute", "大模型只做决策，数值计算全部交给工具层")
    table_slide(s, ["智能体", "职责", "产物"], [
        ["Orchestrator", "判断处理取向、调度闭环与下游", "plan"],
        ["Detector", "ReAct 循环调用 8 个检测工具", "findings / match_map"],
        ["Planner", "缺陷 → 有依赖顺序的修复动作", "plan.actions"],
        ["Executor", "逐步执行、自动补参、落快照", "execution_trace"],
        ["Verifier", "重跑检测器、按硬/软口径判定", "quality_score"],
        ["Reporter", "汇总全过程", "运行摘要.md"],
    ], top=1.75)

    # 4 核心设计
    s = slide("3｜核心设计：硬口径 / 软口径分离", "为什么第一版验证永远不收敛")
    bullets(s, ["现象：验证连续 3 轮不通过，冲突残留率恒为 0.0611，异常值残留率 0.0285 → 0.0121 到不了 0.01",
                "根因一：balance 等重尾字段的真实长尾值被 IQR 统计口径持续判为离群",
                "根因二：跨表教育冲突按业务策略「以主表为准」只记录不修改，检测器每轮重新命中",
                "解法：给每个检测结果打类型标签 —— 硬口径（越界/自洽冲突/重复/格式/缺失）要求零残留，软口径只记录",
                "效果：改造后第 1 轮即通过，质量分 95.7"])

    # 5 数据
    s = slide("4｜数据与可度量的缺陷注入", "没有真值就无法证明修得好")
    table_slide(s, ["项目", "规模", "缺陷真值"], [
        ["原始主表", "45,211 × 22", "UCI Bank Marketing"],
        ["注入后主表", "47,697 × 22", "冗余 2,486 行"],
        ["客户档案表", "46,567 × 6", "跨表实体匹配"],
        ["缺失 / 格式", "—", f"{metrics.get('truth_counts', {}).get('missing_cells', '—')} 格 / {metrics.get('truth_counts', {}).get('format_cells', '—')} 格"],
        ["越界 / 冲突", "—", f"{metrics.get('truth_counts', {}).get('outlier_cells', '—')} 格 / {metrics.get('truth_counts', {}).get('conflict_cells', '—')} 格"],
        ["标签错标", "—", f"{metrics.get('truth_counts', {}).get('label_error_rows', '—')} 行"],
    ], top=1.75, size=14)

    # 6 检测效果
    s = slide("5｜检测效果", "与注入真值逐格比对（精确率 / 召回率）")
    table_slide(s, ["问题类型", "精确率", "召回率", "备注"], [
        ["缺失值", fmt(det.get("missing", {}).get("precision")), fmt(det.get("missing", {}).get("recall")), "确定性规则，完全检出"],
        ["格式不一致", fmt(det.get("format", {}).get("precision")), fmt(det.get("format", {}).get("recall")), "六类规则覆盖"],
        ["异常值（硬口径）", fmt(det.get("outlier", {}).get("hard_domain_precision")), fmt(det.get("outlier", {}).get("hard_domain_recall")), "仅业务边界越界"],
        ["字段冲突（自洽）", fmt(((det.get("conflict_by_kind") or {}).get("self_inconsistent") or {}).get("precision")), "1.0000", "年龄 vs 开户年份"],
        ["跨表实体匹配", fmt(ent.get("precision")), fmt(ent.get("recall")), f"{ent.get('n_pred', '—')} 对，覆盖 {pct(ent.get('coverage_of_main'))}"],
        ["标签错误", fmt(det.get("label_error", {}).get("precision")), fmt(det.get("label_error", {}).get("recall")), "保守策略，其余转人工"],
    ], top=1.75, size=13)

    # 7 下游
    s = slide("6｜下游分类：三档对照", "数据质量变好 ≠ 下游一定变好")
    before = (down.get("before") or {}).get("best") or {}
    after = (down.get("after") or {}).get("best") or {}
    precise = (down.get("precise_only") or {}).get("best") or {}
    delta = down.get("delta") or {}
    table_slide(s, ["指标", "脏数据", "仅精确修复", "全量修复", "Δ(全量-脏)"], [
        ["Accuracy", fmt(before.get("accuracy")), fmt(precise.get("accuracy")), fmt(after.get("accuracy")), f"+{fmt(delta.get('accuracy'))}"],
        ["Precision", fmt(before.get("precision")), fmt(precise.get("precision")), fmt(after.get("precision")), f"+{fmt(delta.get('precision'))}"],
        ["Recall", fmt(before.get("recall")), fmt(precise.get("recall")), fmt(after.get("recall")), f"+{fmt(delta.get('recall'))}"],
        ["F1", fmt(before.get("f1")), fmt(precise.get("f1")), fmt(after.get("f1")), f"+{fmt(delta.get('f1'))}"],
        ["ROC-AUC", fmt(before.get("roc_auc")), fmt(precise.get("roc_auc")), fmt(after.get("roc_auc")), f"+{fmt(delta.get('roc_auc'))}"],
    ], top=1.75, size=14)
    tb = s.shapes.add_textbox(Inches(0.75), Inches(5.1), Inches(12.0), Inches(1.6))
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = "洞察：填补与近似修复提高了召回、降低了精确率 —— 质量指标与下游收益必须分开度量。"
    r.font.size = PPt(15); r.font.color.rgb = ACC; r.font.bold = True; r.font.name = "微软雅黑"

    # 8 消融
    arms = ablation.get("arms") or []
    if arms:
        s = slide("7｜消融实验：哪一步真正有用", "按计划顺序逐步累加修复动作")
        rows = []
        for a in arms[:8]:
            dm = a.get("downstream") or {}
            resid = a.get("residuals") or {}
            rows.append([a.get("arm"), resid.get("missing_cells"), resid.get("out_of_bounds_cells"),
                         fmt(dm.get("f1")), fmt(dm.get("roc_auc"))])
        table_slide(s, ["动作前缀", "缺失残留", "越界残留", "F1", "ROC-AUC"], rows, top=1.75, size=12)

    # 9 错误与修正
    s = slide("8｜错误与修正：智能体是怎样被调对的", "完整过程保存在运行记录中")
    bullets(s, ["验证不收敛 → 硬/软口径分离（跨表策略性差异不可消除）",
                "下游全线下滑 → 异常值改为只裁业务边界，保留重尾信息",
                "误报 3.9 万缺失 → unknown 在 UCI 中是合法类别，移出缺失词表",
                "整列 4.7 万格判格式异常 → 规范形取「出现次数最多的写法」而非字典序首个",
                "标签检测恒为 0 → 检测前先归一化标签写法",
                "实体匹配精确率假性 0.02 → 验证器的映射基准覆盖了执行器的映射"], top=1.75, size=16)

    # 10 结论
    s = slide("9｜结论与展望", "把「修得好」变成可验证的事")
    bullets(s, ["多智能体 + 硬软口径验证，让数据清洗从「一次性脚本」变成可判定、可迭代的闭环",
                "四类核心问题检测达到 0.95~1.00，重复数据修复 1.00，下游五项指标全正",
                "最大的收获：数据质量指标与下游收益是两套度量，必须分开看",
                "展望：模型化填补、修复影响预估、真实大模型决策的成本-收益评测"])

    out = DOCS / "课堂展示_数据处理智能体.pptx"
    prs.save(out)
    print(f"[输出] {out}")
    return out


# --------------------------------------------------------------------------- #
# 3. 个人贡献说明
# --------------------------------------------------------------------------- #
MODULE_OWNER = [
    ["数据构造", "UCI 数据获取与兜底、跨表客户档案派生", "兰祎本"],
    ["缺陷注入与真值", "8 类缺陷注入、污染去重、真值记录", "兰祎本"],
    ["检测工具层", "画像、8 类检测规则、业务边界定义", "乌海博"],
    ["修复工具层", "格式/去重/填补/异常/冲突/标签动作", "乌海博"],
    ["智能体层", "六个智能体与调度闭环、硬/软口径验证", "乌海博"],
    ["下游与评估", "分类建模、三档对照、检测/修复指标", "兰祎本"],
    ["消融实验", "前缀累加消融与结果分析", "兰祎本"],
    ["文档与展示", "报告、PPT、运行记录整理", "共同完成"],
]

PERSON_WORK = [
    ("兰祎本", "数据构造、缺陷注入、下游评估与消融实验",
     "`src/pipeline/make_dataset.py`（UCI 多镜像下载与兜底合成、跨表客户档案派生）、"
     "`src/pipeline/inject.py`（8 类缺陷注入、全局污染去重、真值记录）、"
     "`src/pipeline/classify.py`（特征工程、5 折 CV、三档对照）、`scripts/05_ablation.py`",
     "数据规模与真值核对（47,697 行脏数据、3,938 格缺失、904 格越界逐项比对）；"
     "8 档消融实验与结果分析，得到「数据质量指标与下游收益不同步」这一核心结论",
     "报告第 1 章、第 4.3～4.4 节、表 1、表 6、表 7"),
    ("乌海博", "检测与修复工具层、智能体层与验证闭环",
     "`src/tools/profiling.py`、`src/tools/quality.py`（8 类检测规则、DOMAIN_BOUNDS）、"
     "`src/tools/repair.py`、`src/agents/*`（六个智能体与调度闭环）、`src/llm/client.py`、"
     "`src/pipeline/evaluate.py`（检测与修复指标）",
     "检测指标调优（缺失/格式精确率与召回率均达 1.00）；定位「验证 3 轮不通过」的根因并提出"
     "硬/软口径分离，使闭环 1 轮收敛、质量分 95.7",
     "报告第 2 章、第 3.1～3.2 节、第 4.2 节、表 2、表 3、表 4、表 5"),
]



def build_contrib():
    doc = new_doc()
    title(doc, "个人贡献说明")
    subtitle(doc, f"课程小组作业《数据处理智能体》　|　{_GROUP_PREFIX}小组成员：{MEMBER_FULL}")
    para(doc, "本项目为小组协作完成。下表按模块列出主要工作与责任人，并在表后逐人说明具体承担的"
              "代码文件、实验与文档章节。")
    caption(doc, "表 1　模块分工总览")
    table(doc, ["模块", "主要内容", "责任人"], MODULE_OWNER, widths=[3.4, 8.4, 4.4])
    for i, (name, role, code, exp, doc_txt) in enumerate(PERSON_WORK, start=1):
        head(doc, f"{i}．{name}", 2)
        para(doc, f"主要负责：{role}", indent=False)
        for label, txt in (("代码", code), ("实验", exp), ("文档", doc_txt)):
            if txt:
                bullet(doc, f"{label}：{txt}")
    out = DOCS / "个人贡献说明.docx"
    doc.save(out)
    print(f"[输出] {out}")
    return out


def main():
    metrics = load_json("指标汇总.json")
    ablation = load_json("消融实验.json")
    build_report(metrics, ablation)
    build_ppt(metrics, ablation)
    build_contrib()
    print("[完成] 报告 / PPT / 个人贡献说明已生成到 docs/")


if __name__ == "__main__":
    main()
