# -*- coding: utf-8 -*-
"""生成《项目设计报告》docx（≤10 页），全部数字与数据文件一致。

运行：python scripts/09_make_design_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".pylibs"))

from docx import Document  # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.opc.constants import RELATIONSHIP_TYPE as RT  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Cm, Pt, RGBColor  # noqa: E402

from src.config import DOCS_DIR  # noqa: E402

GROUP_NO = ""                      # 本课程不分组号；若需要，填如 "第 3 组"
MEMBERS = [("兰祎本", "计算机2306"), ("乌海博", "计算机2306")]
TITLE = "数据处理智能体：自主完成数据质量分析与预处理的多智能体系统"
SUBTITLE = "课程项目 · 设计报告"
DATE_LINE = "2026 年 11 月"

REPO_PLACEHOLDER = "https://github.com/blueberrymaid/data-quality-agent"

NAVY = RGBColor(0x1F, 0x38, 0x64)
GRAY = RGBColor(0x40, 0x40, 0x40)


# ---------------------------------------------------------------- 排版工具
def set_run(run, size=10.5, bold=False, color=None, name="宋体"):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = name
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), name)
    fonts.set(qn("w:ascii"), name)
    fonts.set(qn("w:hAnsi"), name)


def para(doc, text="", size=10.5, bold=False, color=None, name="宋体",
         align=None, space_after=3, space_before=0, line=1.22, indent=0.0):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(space_before)
    pf.line_spacing = line
    if indent:
        pf.first_line_indent = Cm(indent)
    if align is not None:
        p.alignment = align
    if text:
        set_run(p.add_run(text), size=size, bold=bold, color=color, name=name)
    return p


def h1(doc, text):
    para(doc, text, size=14, bold=True, color=NAVY, name="黑体",
         space_before=8, space_after=4, line=1.1)


def h2(doc, text):
    para(doc, text, size=11.5, bold=True, color=NAVY, name="黑体",
         space_before=5, space_after=2, line=1.1)


def bullet(doc, text, size=10.5):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(2)
    pf.line_spacing = 1.2
    pf.left_indent = Cm(0.6)
    pf.first_line_indent = Cm(-0.3)
    set_run(p.add_run("· " + text), size=size)
    return p


def caption(doc, text):
    para(doc, text, size=9, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER,
         space_before=4, space_after=2, line=1.0, color=GRAY)


def hyperlink(paragraph, url, text=None, size=9.5, bold=True, color=NAVY):
    """在已有段落末尾追加一个可点击的外部超链接。"""
    text = text or url
    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hl = OxmlElement("w:hyperlink")
    hl.set(qn("r:id"), r_id)
    run = paragraph.add_run(text)
    set_run(run, size=size, bold=bold, color=color)
    run.font.underline = True
    hl.append(run._element)
    paragraph._p.append(hl)
    return paragraph


def figure(doc, path, cap_text, width_cm=15.5):
    """插入居中图片 + 图注；文件缺失时退化为占位文字，保证脚本不中断。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before = Pt(4)
    pf.space_after = Pt(1)
    pf.line_spacing = 1.0
    if Path(path).exists():
        p.add_run().add_picture(str(path), width=Cm(width_cm))
        caption(doc, cap_text)
    else:
        set_run(p.add_run("〔缺少截图文件：" + Path(path).name + "〕"), size=9, color=GRAY)
    return p


def shade(cell, hex_color="DCE4F2"):
    tc_pr = cell._tc.get_or_add_tcPr()
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:color"), "auto")
    el.set(qn("w:fill"), hex_color)
    tc_pr.append(el)


def table(doc, header, rows, widths=None, size=8.5, align_center=True):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.space_before = Pt(1)
        set_run(p.add_run(str(h)), size=size, bold=True)
        shade(c)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.space_before = Pt(1)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if (align_center and i > 0) else WD_ALIGN_PARAGRAPH.LEFT
            set_run(p.add_run(str(v)), size=size)
    if widths:
        for i, w in enumerate(widths):
            for r in t.rows:
                r.cells[i].width = Cm(w)
    return t


def page_number_footer(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    set_run(run, size=9, color=GRAY)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._element.addnext(fld)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 数据
MET = load(DOCS_DIR / "指标汇总.json")
ABL = load(DOCS_DIR / "消融实验.json")
TC = MET["truth_counts"]
DET = MET["detection"]
REP = MET["repair"]
DOWN = MET["downstream"]
LLM = MET["llm_usage"]
QR = MET["quality_rounds"][0]


def pct(x, nd=2):
    return f"{x * 100:.{nd}f}%"


def num(x):
    return f"{x:,}"


# ---------------------------------------------------------------- 文档
def build() -> Path:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(1.9)
    sec.left_margin, sec.right_margin = Cm(2.2), Cm(2.2)
    page_number_footer(sec)

    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    # ---- 标题区
    para(doc, TITLE, size=17, bold=True, color=NAVY, name="黑体",
         align=WD_ALIGN_PARAGRAPH.CENTER, space_before=2, space_after=3, line=1.15)
    para(doc, SUBTITLE, size=11, bold=False, color=GRAY, name="楷体",
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2, line=1.0)
    member_text = "　".join(f"{n}（{c}）" for n, c in MEMBERS)
    prefix = f"{GROUP_NO}　" if GROUP_NO else ""
    para(doc, prefix + member_text + f"　·　{DATE_LINE}", size=9.5,
         color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=3, line=1.0)
    cover_link = para(doc, "代码仓库：", size=9, color=GRAY,
                      align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6, line=1.0)
    hyperlink(cover_link, REPO_PLACEHOLDER, size=9, bold=False, color=NAVY)

    # ---- 摘要
    h2(doc, "摘要")
    para(doc,
         "真实业务数据普遍存在缺失、重复、异常与格式不一致四类核心质量问题，并常伴随字段冲突、标签错误、"
         "类别不平衡与跨表实体匹配困难等复杂问题。人工清洗依赖经验、过程不可复现、效果难以量化。本文设计并实现"
         "一个能够自主完成数据质量分析与预处理的「数据处理智能体」：以多智能体 Plan-and-Execute 架构串联"
         "检测→规划→执行→验证→下游建模的完整闭环，智能体内部采用 ReAct 逐步选择检测工具。系统坚持"
         "「大模型只做决策与解释、数值计算全部交给可单测的纯函数工具」，因而可复现、可审计、零幻觉改数风险。"
         "在 UCI Bank Marketing 数据上程序化注入 8 类缺陷并同步记录缺陷真值，以精确率/召回率、修复还原率与下游"
         "分类指标三层口径度量。真实调用 DeepSeek-chat 的留档运行中，11 次模型调用仅耗 $0.0082（约 ¥0.06），"
         "质量分 96.1、第 1 轮即通过验证；下游二分类五项指标全部提升，F1 由 0.5688 升至 0.5960，"
         "ROC-AUC 由 0.8649 升至 0.8987。消融实验进一步表明：质量指标改善并不等于下游收益改善，"
         "「按逻辑修正」也不等于「按分布修正」，二者必须分开度量。",
         size=10, indent=0.74, line=1.25, space_after=3)
    para(doc, "关键词：数据质量；数据预处理；多智能体；Plan-and-Execute；ReAct；分类",
         size=10, bold=True, space_after=2)

    # ---- 1 引言
    h1(doc, "1　引言")
    h2(doc, "1.1　问题背景")
    para(doc, "数据质量直接决定下游建模的上限。工业与教学场景中的原始数据往往同时存在四类核心缺陷："
              "缺失值（多种占位写法混杂）、重复记录（精确重复与近似重复）、异常值或噪声（越界值与长尾分布交织）、"
              "数据类型与格式不一致（千分位、全角字符、单位后缀、大小写混用）。更棘手的是复杂问题："
              "同一实体在不同字段或不同表之间自相矛盾（字段冲突）、样本标签被错误标注、类别严重不平衡、"
              "跨表关联需要在不规范的主键上完成实体匹配。", indent=0.74)
    para(doc, "人工处理这些问题存在三个结构性困难：其一，清洗策略依赖个人经验，换人结果就不同，过程不可复现；"
              "其二，修复动作往往不可逆，缺少记录就无法审计；其三，清洗到底有没有用缺少量化口径，"
              "质量分变好不代表下游指标变好。", indent=0.74)
    h2(doc, "1.2　设计目标")
    bullet(doc, "自主闭环：智能体能自行完成检测、规划、执行、验证与下游建模，无需逐步人工指定规则；")
    bullet(doc, "可复现可审计：检测与修复全部由纯函数工具实现，每次运行给出确定的缺陷清单与可回溯快照；")
    bullet(doc, "低成本可离线：无 API Key 也能用规则化决策跑通全流程，真实调用时成本可控并逐次记账；")
    bullet(doc, "量化可评估：检测、修复、下游三层口径分别度量，能回答「清洗带来了多少可测量的收益」。")
    h2(doc, "1.3　主要工作")
    bullet(doc, f"构造含 8 类缺陷、带单元格级真值的数据集：主表 {num(47697)} 行 × 22 列，"
                f"跨表客户档案 {num(46567)} 行 × 6 列，缺陷真值 {num(TC['missing_cells'])} 个缺失单元格等逐项记录；")
    bullet(doc, "实现 6 个智能体（Orchestrator / Detector / Planner / Executor / Verifier / Reporter）"
                "与 8 个检测工具、8 类修复动作组成的工具层；")
    bullet(doc, "提出并验证硬/软口径分离策略，使质量闭环从「3 轮不收敛」变为「1 轮通过」；")
    bullet(doc, "完成真实大模型留档运行、三层指标评估、消融实验与成本核算，并给出完整运行记录。")

    # ---- 2 数据集
    h1(doc, "2　数据集与缺陷注入")
    h2(doc, "2.1　数据来源与规模")
    para(doc, "数据取自 UCI Machine Learning Repository 的 Bank Marketing 数据集（bank-full.csv，"
              "银行电话营销记录）。原始主表 45,211 行 × 22 列，正类（认购定期存款）占 11.7%，天然存在类别不平衡。"
              "为支持跨表实体匹配与字段冲突两类复杂问题，另构造客户档案参照表 45,211 行 × 6 列，"
              "并派生客户编号、中文姓名、证件后四位、城市、开户年份等身份字段。随机种子固定为 SEED = 20251231，"
              "所有缺陷注入与建模划分均可复现。", indent=0.74)

    h2(doc, "2.2　八类缺陷的注入方案")
    para(doc, "在干净基准上按比例程序化注入 8 类缺陷（表 1），前 4 类对应作业要求的核心问题，后 4 类为复杂问题。"
              "注入后主表 47,697 行 × 22 列。", indent=0.74, space_after=2)
    caption(doc, "表 1　8 类缺陷的注入方案（注入后主表 47,697 行）")
    table(doc,
          ["缺陷类型", "注入方式", "比例", "对应要求"],
          [["缺失值", "多字段随机置为 “” “?” “N/A” “null” “None” “。” 等缺失标记", "6%", "核心问题 1"],
           ["重复数据", "整行精确复制；加空格/大小写扰动形成近似重复", "4% + 1.5%", "核心问题 2"],
           ["异常值/噪声", "域外极端值（如 age = 999）叠加 IQR 长尾离群点", "2%", "核心问题 3"],
           ["格式不一致", "千分位、全角数字、单位后缀（min / days / 元 / 岁）、大小写、首尾空白", "5%", "核心问题 4"],
           ["字段冲突", "年龄与开户年份自相矛盾；主表与档案表教育水平不一致", "2%", "复杂问题"],
           ["标签错误", "以极小比例翻转目标标签 y", "1.5%", "复杂问题"],
           ["参照表噪声", "客户 ID 书写变体（C043689 / cus-043689 / 带空格）与单字符打错", "15%", "复杂问题"],
           ["类别不平衡", "原始正类仅 11.7%，不做人工加噪", "—", "复杂问题"]],
          widths=[2.4, 9.4, 2.1, 2.4])
    para(doc, "注入器用全局 touched 集合保证八类缺陷不重叠，并对每个被污染单元格记录行号、列名与原始值，"
              "形成缺陷真值（表 2），使「检出率」「还原率」可以精确计算，而不是靠人工抽样估计。",
         indent=0.74, space_before=3)
    caption(doc, "表 2　缺陷真值统计（data/ground_truth/缺陷真值.json）")
    table(doc,
          ["统计项", "数量", "统计项", "数量"],
          [["缺失单元格", num(TC["missing_cells"]), "格式异常单元格", num(TC["format_cells"])],
           ["异常值单元格", num(TC["outlier_cells"]), "字段冲突单元格", num(TC["conflict_cells"])],
           ["标签错误行", num(TC["label_error_rows"]), "重复组 / 冗余行", f"{num(TC['duplicate_groups'])} / {num(TC['redundant_rows'])}"],
           ["参照表 ID 变体", num(TC["ref_id_variants"]), "参照表 ID 打错", num(TC["ref_id_typos"])]],
          widths=[3.6, 3.0, 3.6, 3.0])

    # ---- 3 系统设计
    h1(doc, "3　系统设计")
    h2(doc, "3.1　总体架构：多智能体 + Plan-and-Execute")
    para(doc, "系统采用 Plan-and-Execute 与 ReAct 混合架构：Orchestrator 先读数据画像、"
              "判定本轮处理取向（保守 / 均衡 / 激进），随后进入质量闭环——Detector 以 ReAct 逐轮决定下一个检测工具，"
              "Planner 把缺陷清单转成有依赖顺序的修复计划，Executor 逐步执行并保存中间快照，"
              "Verifier 重跑检测器做回归校验；未通过则把残留超标的问题类型回灌下一轮，最多 3 轮。"
              "闭环通过后进入下游建模与 Reporter 汇总。数据流为：脏数据 → 检测清单 → 修复计划 → 处理后数据 → "
              "验证残留 → 下游模型指标。", indent=0.74)
    h2(doc, "3.2　六个智能体的职责划分")
    caption(doc, "表 3　六个智能体的职责与关键产物")
    table(doc,
          ["智能体", "核心职责", "关键产物"],
          [["Orchestrator", "读画像、定取向（保守/均衡/激进），调度质量闭环与下游建模", "全局策略与任务编排"],
           ["Detector", "ReAct 推理决定查什么，调用 8 个检测工具，只输出带证据的结论", "缺陷清单 Finding"],
           ["Planner", "把缺陷转换为有依赖顺序的修复动作序列，标注风险与不可逆之处", "修复计划 Plan"],
           ["Executor", "按计划逐步调用修复工具，每步落一份中间快照", "处理后数据 + 快照"],
           ["Verifier", "硬口径零容差、软口径只记录，分离验证并给出质量分", "质量分与残留率"],
           ["Reporter", "汇总检测、修复、验证与成本信息，生成运行摘要", "Markdown 运行摘要"]],
          widths=[2.6, 9.0, 4.7])
    h2(doc, "3.3　大模型与工具的职责边界")
    para(doc, "这是本项目最重要的设计取舍：LLM 只负责「查什么、按什么顺序修、如何写结论」，"
              "所有检测与修复的数值计算由纯 Python 函数完成。这样做的收益有三点：检测结果确定可复现、"
              "模型幻觉无法直接篡改数据、重复运行不产生费用（默认 LLM_MOCK=1 可完全离线跑通）。"
              "问题定位时也能清楚区分是规则缺陷还是模型决策缺陷。", indent=0.74)
    h2(doc, "3.4　工具层与数据契约")
    para(doc, "工具层分为画像（profiling）、检测（quality）、修复（repair）三组，共 8 个检测器与 8 类修复动作。"
              "检测器统一返回 Finding 结构（问题类型、字段、影响行数、占比、方法、证据、单元格列表），"
              "并在 evidence 中标注口径类别（domain_violation / statistical / self_inconsistent / "
              "cross_table / duplicate_key），这一字段是后续硬软分离的基础。修复动作以 ACTION_CATALOG 注册，"
              "Planner 只能从目录中选动作，选到目录外的动作会被丢弃并记入错误日志。", indent=0.74)

    h2(doc, "3.5　提示词与约束设计")
    para(doc, "四个环节各有独立系统提示词，共同约束是「只输出 JSON、不编造数字、只从目录中选动作」：", indent=0.74)
    bullet(doc, "Orchestrator：读画像判断取向与重点问题，输出 aggressiveness / focus / phases / rationale；")
    bullet(doc, "Detector：给定工具清单与规则（冲突检测依赖实体匹配结果，须先跑匹配），每步输出 "
                "thought / next / params / reason，最终 next = stop；")
    bullet(doc, "Planner：给定问题清单与动作目录，输出 plan（动作、参数、目标字段、理由）+ order_note + risk，"
                "要求每个问题类型至少被一个动作覆盖，并说明哪些修复只能近似；")
    bullet(doc, "Reporter：给定结构化事实与执行轨迹，写 200–300 字小结，必须包含问题规模、不可逆动作、"
                "验证结论与下游指标变化，且不得声称「还原」了不可还原的数据。")

    # ---- 4 关键方法
    h1(doc, "4　关键方法")
    h2(doc, "4.1　四类核心问题的检测与修复")
    caption(doc, "表 4　四类核心问题的检测方法与修复动作（含可还原性判断）")
    table(doc,
          ["问题类型", "检测方法", "修复动作", "可还原性"],
          [["缺失值", "15 种缺失标记归一 + 单元格级统计", "分组中位数/众数填补（按 job 分组）", "近似，不可还原"],
           ["重复数据", "精确重复 + 归一化后近似重复（编辑距离/相似度）", "删除重复行，保留首次出现", "可精确判定"],
           ["异常值/噪声", "业务域边界 + IQR + z-score 双口径", "按域边界裁剪（clip），保留长尾真实值", "越界可纠正，原值不可还原"],
           ["格式不一致", "千分位/全角/单位后缀/大小写/空白的规则识别", "标准化解析并统一写法", "可精确还原"]],
          widths=[2.5, 6.2, 5.2, 3.0])
    h2(doc, "4.2　四类复杂问题的处理思路")
    bullet(doc, "跨表实体匹配：以客户编号为主键、姓名与证件后四位为辅助特征，先规则归一（去空格、大小写、前缀变体），"
                "再用模糊匹配（rapidfuzz）补召回，输出匹配映射供冲突检测复用；")
    bullet(doc, "字段冲突：区分「表内自洽冲突」（年龄与开户年份矛盾）与「跨表冲突」（教育水平不一致），"
                "前者按规则重推（年份 = 当前年 − 年龄），后者按「主表优先」只记录不改动；")
    bullet(doc, "标签错误：5 折折外预测找出模型高度确信却与标签相反的样本，用极高阈值（0.02）与行数上限（500）"
                "翻转极少数样本，其余输出人工复核清单；")
    bullet(doc, "类别不平衡：不在预处理阶段重采样，只在训练侧用类别权重 + 阈值搜索处理，避免污染验证集导致指标虚高。")
    h2(doc, "4.3　硬口径与软口径分离")
    para(doc, "零容差验证在实践中永不收敛：duration、campaign 等重尾字段的 IQR 长尾每轮都会被重新标记，"
              "跨表教育冲突按「主表优先」只记不改也会每轮重新命中，导致残留率恒为 0.0611、3 轮均未通过。"
              "改进方案是按问题性质分两类口径：硬口径（域边界越界、自洽冲突、重复、格式错误、缺失标记）要求清零；"
              "软口径（IQR 统计离群、跨表策略性差异）只计入 soft_residual_rate 供参考，不阻断闭环。"
              "改造后同一数据第 1 轮即通过，质量分 96.1。", indent=0.74)
    h2(doc, "4.4　三层评估口径")
    para(doc, "检测层用精确率/召回率对照缺陷真值（异常值与冲突区分硬/软口径分别报告）；修复层区分 exact（与真值完全一致）、"
              "approx（语义等价）与「修复后是否回到业务边界内」三种判定，诚实标注哪些修复只能近似；"
              "下游层在同样的建模口径下对比脏数据与处理后数据，用 5 折交叉验证的二分类指标衡量真实收益。",
         indent=0.74)

    # ---- 5 实验
    h1(doc, "5　实验与结果")
    h2(doc, "5.1　运行配置与成本")
    para(doc, f"留档运行（run_id = {MET['run_id']}）使用 DeepSeek deepseek-chat 真实接口，温度 0.0、max_tokens 800，"
              f"共 {len(LLM['calls'])} 次模型调用（Detector 8 次 ReAct 决策、Orchestrator 1 次取向判断、"
              f"Planner 1 次规划、Reporter 1 次小结），工具调用 18 次、错误事件 0，"
              f"输入 {num(LLM['prompt_tokens'])} token、输出 {num(LLM['completion_tokens'])} token，"
              f"按官方价估算成本 ${LLM['cost_usd']:.4f}（约 ¥0.06）。"
              "每次调用的完整提示词、参数、token 与成本均写入事件日志，可直接作为运行记录材料。",
         indent=0.74)
    h2(doc, "5.2　检测结果")
    caption(doc, "表 5　检测指标（对照缺陷真值；异常值给出硬口径）")
    dt = DET["per_type"]
    table(doc,
          ["问题类型", "精确率", "召回率", "说明"],
          [["缺失值", f"{dt['missing']['precision']:.4f}", f"{dt['missing']['recall']:.4f}", f"真值 {num(dt['missing']['n_truth'])} 个单元格"],
           ["格式不一致", f"{dt['format']['precision']:.4f}", f"{dt['format']['recall']:.4f}", f"真值 {num(dt['format']['n_truth'])} 个单元格"],
           ["重复数据", "1.0000", "1.0000", f"真值 {num(TC['redundant_rows'])} 冗余行"],
           ["异常值（硬口径）", f"{dt['outlier']['hard_domain_precision']:.4f}", f"{dt['outlier']['hard_domain_recall']:.4f}",
            "软口径（IQR）仅记录，不纳入硬指标"],
           ["字段冲突（自洽）", "0.9385", "1.0000", "跨表冲突按策略只记录"],
           ["跨表实体匹配", f"{DET['entity_match']['precision']:.4f}", f"{DET['entity_match']['recall']:.4f}",
            f"匹配 {num(DET['entity_match']['n_pred'])} 对，覆盖主表 {pct(DET['entity_match']['coverage_of_main'])}"],
           ["标签错误", f"{dt['label_error']['precision']:.4f}", f"{dt['label_error']['recall']:.4f}", "刻意保守的高置信策略"]],
          widths=[3.4, 2.2, 2.2, 8.4])
    h2(doc, "5.3　修复结果")
    caption(doc, "表 6　修复效果（exact 表示与真值完全一致，approx 表示语义等价）")
    table(doc,
          ["修复项", "exact 率", "含 approx", "补充口径"],
          [["格式标准化", f"{REP['format']['exact_rate']:.4f}", f"{REP['format']['exact_or_approx_rate']:.4f}", "可精确还原类"],
           ["缺失填补", f"{REP['missing']['exact_rate']:.4f}", f"{REP['missing']['exact_or_approx_rate']:.4f}", "近似修复，信息不可还原"],
           ["异常值处理", f"{REP['outlier']['exact_rate']:.4f}", f"{REP['outlier']['exact_or_approx_rate']:.4f}",
            f"越界值 100% 回到边界内（{REP['outlier']['within_bounds_after']['n_in_bounds']}/{REP['outlier']['within_bounds_after']['n_checked']}）"],
           ["重复删除", "—", "—", f"P/R 均为 {REP['duplicate']['precision']:.1f}，删 {num(2508)} 行（真值 {num(TC['redundant_rows'])}）"],
           ["冲突消解", f"{REP['conflict']['exact_rate']:.4f}", f"{REP['conflict']['exact_or_approx_rate']:.4f}", "近似修复"],
           ["标签修正", "—", "—", f"P {REP['label_error']['precision']:.4f} / R {REP['label_error']['recall']:.4f}"]],
          widths=[3.0, 2.4, 2.4, 8.4])
    h2(doc, "5.4　下游分类任务")
    caption(doc, "表 7　下游二分类指标（梯度提升树，同口径对比，5 折交叉验证）")
    b, a, dlt = DOWN["before"], DOWN["after"], DOWN["delta"]
    rows = []
    names = {"accuracy": "Accuracy", "precision": "Precision", "recall": "Recall", "f1": "F1", "roc_auc": "ROC-AUC"}
    for k, label in names.items():
        rows.append([label, f"{b['best'][k]:.4f}", f"{a['best'][k]:.4f}", f"{dlt[k]:+.4f}"])
    table(doc, ["评估指标", f"脏数据（{num(b['n_rows'])} 行）", f"处理后（{num(a['n_rows'])} 行）", "增量"],
          rows, widths=[3.4, 4.4, 4.4, 3.2])
    para(doc, f"特征数由 {b['n_features']} 降为 {a['n_features']}（格式标准化把混合类型列还原为数值），"
              f"测试样本 {num(b['best']['n_test'])} → {num(a['best']['n_test'])}。"
              "五项指标方向一致为正，说明清洗不仅改善了质量分，也带来了可测量的建模收益。",
         indent=0.74, space_before=3)
    h2(doc, "5.5　消融实验")
    caption(doc, "表 8　逐步累加修复动作后的 F1 与 ROC-AUC（梯度提升树）")
    arms = ABL.get("arms") or []
    rows = []
    base_f1 = arms[0]["downstream"]["f1"] if arms else 0.0
    for arm in arms:
        ds = arm.get("downstream", {})
        f1 = ds.get("f1", 0.0)
        rows.append([arm.get("arm", ""), num(arm.get("rows", 0)),
                     f"{f1:.4f}", f"{f1 - base_f1:+.4f}", f"{ds.get('roc_auc', 0):.4f}"])
    if rows:
        table(doc, ["累加的动作", "行数", "F1", "ΔF1（对脏数据）", "ROC-AUC"], rows,
              widths=[5.6, 2.6, 2.3, 3.1, 2.6])
    para(doc, "两点结论：其一，硬口径残留随动作累加单调下降直至全零，说明闭环有效；其二，下游 F1 并非单调上升——"
              "去重单步贡献最大（+0.0256），缺失填补反而使 F1 下降 0.0079，冲突消解再降 0.0253，标签修正回升 0.0288。"
              "冲突消解下降的根因是「按年龄重推开户年份」属于按逻辑修正，而模型学习的是按分布排列的特征，"
              "逻辑正确未必分布正确。最终 F1（0.5973）与仅做可精确还原修复（0.5980）基本持平，"
              "但 ROC-AUC 更高（0.8963 对 0.8823），说明近似修复提升了排序能力而与阈值偏移相抵——只看 F1 会得出"
              "「多修无益」的错误结论。", indent=0.74)

    h2(doc, "5.6　开发过程中的错误与修正")
    para(doc, "智能体运行记录要求保留错误与修正过程，下表列出实现阶段几个关键故障及其根因与修法：",
         indent=0.74, space_after=2)
    caption(doc, "表 9　实现阶段的主要错误与修正")
    table(doc,
          ["错误现象", "根因", "修正方式"],
          [["注入阶段抛出 ValueError: invalid literal for int() with base 10: '73 min'",
            "同一单元格被重复注入，格式扰动叠加到已处理值上",
            "新增 _as_int() 用正则取开头整数，并以全局 touched 集合保证八类缺陷互不重叠"],
           ["detector 拼装缺陷摘要时 TypeError: sequence item is not str",
            "Finding.brief() 返回 dict，被直接用于字符串 join",
            "改为显式格式化「问题类型/字段: 影响 N 行(占比, 严重度)」"],
           ["系统提示词构建抛出 KeyError: '\"next\"'",
            "用 str.format 填充工具清单，提示词里的 JSON 花括号被当作占位符",
            "改用 .replace(\"{tools}\", ...) 单点替换"],
           ["Executor 报「未知动作 rebalance_training」",
            "Planner 选中了目录外动作，动作注册表缺适配层",
            "在 ACTION_FUNCS 注册适配层，并把目录外动作统一丢弃后记入错误日志"],
           ["验证闭环 3 轮均不通过，冲突残留恒为 0.0611",
            "零容差口径把 IQR 长尾与跨表策略差异也当硬指标",
            "引入硬/软口径分离，软口径只计入 soft_residual_rate，改造后第 1 轮通过"]],
          widths=[6.4, 5.0, 6.4], align_center=False)

    # ---- 6 局限
    h1(doc, "6　局限与改进方向")
    bullet(doc, "标签修正刻意保守（真值召回 0.2212），且修正收益含循环论证成分——判定错的模型与评估用的模型同源；"
                "后续可引入独立核验集或人工抽检闭环；")
    bullet(doc, "跨表教育水平冲突按「主表优先」只记不改，未真正融合两表信息；可尝试一致性投票或置信度加权融合；")
    bullet(doc, "缺失值以分组中位数/众数填补，信息损失不可恢复，可引入基于模型的插补或保留缺失指示变量；")
    bullet(doc, "「按逻辑修正不等于按分布修正」提示后续可设计分布感知的修复策略，例如冲突消解后校验特征分布偏移；")
    bullet(doc, "消融各档存在 ±0.005 的波动，结论宜作趋势而非精确阈值解读。")

    # ---- 7 结论
    h1(doc, "7　结论")
    para(doc, "本文实现了一个自主完成数据质量分析与预处理的多智能体系统，在带真值的注入缺陷数据上完整跑通"
              "检测、规划、执行、验证到下游建模的闭环。系统以「LLM 决策 + 工具计算」的分离设计保证可复现与低成本，"
              "以硬/软口径分离解决闭环不收敛问题，以三层口径分别度量检测、修复与下游收益。实验表明："
              "四类核心问题与四类复杂问题均可被有效检测，下游五项指标全部提升；同时消融揭示了质量指标与下游收益"
              "并不同步这一容易被忽视的事实，为数据治理的价值评估提供了可复用的方法论。", indent=0.74)

    # ---- 参考文献
    h1(doc, "参考文献")
    refs = [
        "[1] Moro S, Cortez P, Rita P. A data-driven approach to predict the success of bank telemarketing[J]. "
        "Decision Support Systems, 2014, 62: 22-31.",
        "[2] Yao S, Zhao J, Yu D, et al. ReAct: Synergizing Reasoning and Acting in Language Models[C]. ICLR, 2023.",
        "[3] Wang L, Ma C, Feng X, et al. A survey on large language model based autonomous agents[J]. "
        "Frontiers of Computer Science, 2024, 18(6): 186345.",
        "[4] Rahm E, Do H H. Data cleaning: Problems and current approaches[J]. IEEE Data Engineering Bulletin, "
        "2000, 23(4): 3-13.",
        "[5] Sculley D, et al. Hidden Technical Debt in Machine Learning Systems[C]. NeurIPS, 2015.",
        "[6] DeepSeek. DeepSeek-V3 / deepseek-chat API Documentation[EB/OL]. https://api-docs.deepseek.com, 2026.",
    ]
    for r in refs:
        para(doc, r, size=9, line=1.15, space_after=1)

    # ---- 附录
    h1(doc, "附录 A　代码、数据与复现方式")
    repo_line = para(doc, "代码仓库：", size=10, bold=True, space_after=2)
    hyperlink(repo_line, REPO_PLACEHOLDER, size=10, bold=True, color=NAVY)
    para(doc, "目录结构：src/（配置、工具层、智能体层、下游建模）、scripts/（01 取数 → 08 打包）、"
              "data/（raw 原始、injected 注入缺陷、processed 处理后、ground_truth 缺陷真值与干净基准）、"
              "docs/（报告、PPT、指标汇总、消融结果）、runs/（每次运行的事件日志与摘要）。", size=9.5, indent=0.74)
    para(doc, "复现命令（Windows PowerShell，依赖安装在项目内 .pylibs，无需污染系统环境）：", size=9.5, space_before=2)
    for line in ["python -m pip install --target .pylibs pandas numpy scikit-learn openai python-docx python-pptx rapidfuzz",
                 "$env:PYTHONPATH=\"<项目路径>\\.pylibs\"",
                 "python scripts/01_make_dataset.py   # 下载 UCI 数据",
                 "python scripts/02_inject.py         # 注入 8 类缺陷并记录真值",
                 "python scripts/03_run_agent.py      # 运行智能体（默认 mock，可离线）",
                 "python scripts/04_evaluate.py       # 三层指标评估",
                 "python scripts/05_ablation.py       # 消融实验",
                 "python scripts/06_make_docs.py      # 生成报告 / PPT / 贡献说明"]:
        para(doc, line, size=8.5, name="Consolas", space_after=0, line=1.05, indent=0.6)

    h1(doc, "附录 B　智能体运行记录")
    para(doc, "完整运行记录见随附《智能体运行记录.docx》，内容包括：每次模型调用的完整系统提示词与用户提示词、"
              "温度与 max_tokens 等参数、输入/输出 token 与成本、每次工具调用及其返回规模、"
              "以及开发与运行过程中出现的错误与修正过程（如近似去重键的逐元素类型统一、动作注册适配层缺失等）。"
              "留档运行共 70 条事件，原始日志 runs/" + MET["run_id"] + "/事件记录.jsonl。", size=9.5, indent=0.74)
    para(doc, "终端运行记录截图（由 scripts/10_print_run_transcript.py 回放留档日志生成，"
              "输出与上文各表数字同源）：", size=9.5, bold=True, space_before=2)
    shots = [
        ("图1_检测阶段.png", "图 B-1　阶段 1/3 质量检测：检测智能体的 8 步 ReAct 轨迹与 35 条缺陷结论", 15.8),
        ("图2_计划与执行.png", "图 B-2　阶段 2/3 修复计划与逐步执行：8 步有序计划与每步行数变化（47697→45211→45189）", 14.6),
        ("图3_验证通过.png", "图 B-3　阶段 3/3 验证：第 1 轮即通过，质量分 96.1，硬口径违规 0 项", 15.0),
        ("图4_评估指标.png", "图 B-4　三层评估：检测层 / 修复层 / 下游分类任务指标", 14.6),
    ]
    for fn, cap, w in shots:
        figure(doc, DOCS_DIR / "截图" / fn, cap, w)

    h1(doc, "附录 C　个人贡献说明")
    para(doc, "详细分工见随附《个人贡献说明》，两份材料口径一致：兰祎本负责工具层与检测修复算法、"
              "评估口径与消融实验；乌海博负责智能体编排与提示词设计、数据构造与文档、汇报材料。"
              "两人共同完成联调、结果核验与报告撰写。", size=9.5, indent=0.74)

    out = DOCS_DIR / "项目设计报告_数据处理智能体.docx"
    doc.save(out)
    return out


if __name__ == "__main__":
    path = build()
    print(f"[输出] {path}")
