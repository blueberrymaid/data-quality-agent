# 数据处理智能体（Data Quality Agent）

代码仓库：**https://github.com/blueberrymaid/data-quality-agent**

一个能**自主完成数据质量分析与预处理**的智能体系统，采用 **多智能体 + Plan-and-Execute** 架构，
在真实公开数据集（UCI Bank Marketing）上完成「检测 → 规划 → 执行 → 验证 → 下游建模」全闭环，
并支持一个**分类任务**（预测客户是否认购定期存款）。

- 数据集规模：主表 **47,697 行 × 22 列**（干净基准 45,211 行，人为注入缺陷后膨胀），跨表客户档案表 **46,567 行 × 6 列**
- 覆盖的四类核心问题：**缺失值 / 重复数据 / 异常值与噪声 / 数据类型与格式不一致**
- 额外覆盖的复杂问题：**字段（跨表）冲突、标签错误、类别不平衡、跨表实体匹配**
- LLM 只做**决策与解释**，所有数值计算、检测与修复由可复现的纯 Python 工具完成

---

## 1. 目录结构

```
数据处理智能体/
├─ src/
│  ├─ config.py              # 路径、随机种子、注入配置、LLM 配置、智能体配置
│  ├─ utils.py               # 运行日志器 RunLogger / 工具调用守卫 guard
│  ├─ llm/client.py          # LLM 客户端（OpenAI 兼容，含 mock 模式与 JSON 解析）
│  ├─ tools/
│  │  ├─ profiling.py        # 表格画像、缺失标记识别、数值解析、类型推断
│  │  ├─ quality.py          # 8 类质量问题的检测器 + DOMAIN_BOUNDS 业务边界
│  │  └─ repair.py           # 修复动作库 ACTION_CATALOG / ACTION_FUNCS
│  ├─ agents/
│  │  ├─ base.py             # Agent 基类、Context 上下文
│  │  ├─ orchestrator.py     # 主控：出计划、跑质量闭环、跑下游、汇总
│  │  ├─ detector.py         # 检测智能体（ReAct 循环，调用 8 个检测工具）
│  │  ├─ planner.py          # 规划智能体（把缺陷映射为修复动作序列）
│  │  ├─ executor.py         # 执行智能体（逐步修复 + 中间快照）
│  │  ├─ verifier.py         # 验证智能体（硬/软口径分离的质量分与残留判定）
│  │  └─ reporter.py         # 报告智能体（运行摘要 Markdown）
│  └─ pipeline/
│     ├─ make_dataset.py     # 拉取 UCI 数据 + 构造跨表实体（客户编号/姓名/证件）
│     ├─ inject.py           # 程序化注入 8 类缺陷并记录真值
│     ├─ classify.py         # 分类建模：特征工程、5 折 CV、阈值搜索、三档对照
│     └─ evaluate.py         # 检测指标 / 修复指标 / 实体匹配置信口径评估
├─ scripts/
│  ├─ 01_make_dataset.py     # 生成原始数据（含跨表档案）
│  ├─ 02_inject.py           # 注入缺陷，产出脏数据 + 缺陷真值
│  ├─ 03_run_agent.py        # 运行智能体（--live 切真实 LLM API）
│  ├─ 04_evaluate.py         # 对照真值计算检测/修复/下游指标
│  ├─ 05_ablation.py         # 消融实验：逐步累加修复动作的贡献
│  ├─ 06_make_docs.py        # 生成报告 docx / PPT / 个人贡献说明
│  ├─ 07_run_log_digest.py   # 把原始事件日志整理成「智能体运行记录」
│  └─ 08_package.py          # 打包成可提交的压缩包（自动排除密钥与依赖目录）
├─ data/
│  ├─ raw/                   # 原始（干净）主表与客户档案表
│  ├─ injected/              # 注入缺陷后的脏数据
│  ├─ processed/             # 智能体处理结果 + 每步中间快照
│  └─ ground_truth/          # 缺陷真值 JSON + 干净基准
├─ runs/<run_id>/            # 每次运行的完整留档（见 §5）
├─ docs/                     # 指标汇总、消融实验、生成的报告与 PPT
├─ requirements.txt
└─ README.md
```

---

## 2. 环境与安装

- Python **3.12**
- 依赖见 `requirements.txt`

```bash
# 方式一：普通虚拟环境
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements.txt

# 方式二（本项目实际采用）：装到项目内的独立目录，避免污染系统环境
python -m pip install --target .pylibs -r requirements.txt
# 运行脚本前设置：
#   Windows: set PYTHONPATH=%CD%\.pylibs
#   Linux/macOS: export PYTHONPATH=$PWD/.pylibs
```

> 项目内所有脚本已在开头 `sys.path.insert(0, ROOT/".pylibs")`，因此**方式二可直接运行，无需额外设置**。

---

## 3. 运行步骤

```bash
python scripts/01_make_dataset.py    # 1. 获取 UCI 数据并构造跨表客户档案（约 1~2 分钟）
python scripts/02_inject.py          # 2. 注入 8 类缺陷，输出脏数据 + 缺陷真值（约 1 分钟）
python scripts/03_run_agent.py       # 3. 运行智能体（默认 mock 决策模式，离线可跑）
python scripts/04_evaluate.py        # 4. 对照真值评估检测/修复/下游指标
python scripts/05_ablation.py        # 5. 消融实验（约 10 分钟，5 折 CV × 8 档）
python scripts/06_make_docs.py       # 6. 生成报告 docx、PPT、个人贡献说明
python scripts/07_run_log_digest.py  # 7. 生成智能体运行记录（提示词/工具/错误与修正）
python scripts/08_package.py         # 8. 打包成可提交的压缩包（可选，约 7.6 MB）
```

### 使用真实大模型

系统默认 `LLM_MOCK=1`：LLM 决策由规则化 mock 结果替代，因此**无网络、无 API Key 也能完整跑通**，
便于课程演示与复现。要换成真实模型，在项目根目录建 `.env`（从 `.env.example` 复制）：

```dotenv
LLM_MOCK=0
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY_FILE=~/.dsh_llm_key   # 密钥放在项目之外的文件里，.env 只写路径
LLM_TEMPERATURE=0.0
LLM_MAX_TOKENS=800
LLM_PRICE_IN=0.00027          # 每 1K token 输入价格（美元）
LLM_PRICE_OUT=0.0011          # 每 1K token 输出价格（美元）
```

**密钥不落进项目**：`.env` 里不写密钥明文，只指向项目之外的文件（例如 `%USERPROFILE%\.dsh_llm_key`），
或用环境变量 `LLM_API_KEY` 传入。代码里所有展示配置的地方都走 `config.mask_secret()`，只输出
`sk-5a********b8` 这种形式；提交包脚本也会显式排除 `.env`。这样即使整个目录被截图、打包或上传，
密钥也不会跟着泄露。

然后 `python scripts/03_run_agent.py --live`。
每次调用都会写入 `runs/<run_id>/事件记录.jsonl`：模型名、完整提示词、温度、max_tokens、
输入/输出 token 数、估算成本、重试与降级情况。

> 本项目的留档运行（`runs/run_20261031_234343`）即使用 **DeepSeek `deepseek-chat`** 真实接口完成：
> 共 11 次模型调用、输入 20,635 token、输出 2,395 token，估算成本 **$0.0082**（约 ¥0.06）。
> 成本之所以极低，是因为所有数值计算都交给确定性工具，模型只做动作选择与解释。

---

## 4. 架构说明

### 4.1 多智能体分工（Plan-and-Execute，智能体内部用 ReAct）

| 智能体 | 职责 | 关键产物 |
| --- | --- | --- |
| **Orchestrator** | 判断处理激进度（保守/均衡/激进），调度质量闭环与下游任务 | `plan`、`quality_rounds` |
| **Detector** | ReAct 循环调用 8 个检测工具，从数据本身推断该查什么 | `findings`、`match_map`、`react_trace` |
| **Planner** | 把缺陷映射为有依赖顺序的修复动作，缺项自动补齐 | `plan.actions`、`plan_risk` |
| **Executor** | 逐步执行修复并落中间快照，自动补依赖参数 | `execution_trace`、逐步快照 CSV |
| **Verifier** | 重跑检测器，按**硬/软口径**判定是否收敛，输出质量分 | `verification`、`quality_score` |
| **Reporter** | 汇总全过程与指标，生成 Markdown 运行摘要 | `智能体运行摘要.md` |

修复动作的依赖顺序（Planner 的 `ORDER`）：
`enrich_from_ref → standardize_format → drop_duplicates → impute_missing → fix_outliers → resolve_conflicts → fix_label_errors → rebalance_training`
—— 格式统一必须在数值解析之前，去重必须在填补之前（否则填补值会把重复行变得不一致而漏去重）。

### 4.2 硬口径 / 软口径分离（本项目的核心工程决策）

验证器若对所有检测结果都用零容差，闭环**永远不可能收敛**：

- `balance` 等重尾字段，IQR×3 统计口径会持续标出真实存在的长尾值；
- 跨表 `教育水平` 冲突按业务策略「以主表为准」，只记录不修改，检测器每轮都会重新命中。

因此每个 `Finding` 都带 `evidence["kind"]`，验证器只对**硬口径**（域边界越界、自洽冲突、重复、
格式错误、缺失标记）设零容差，软口径（IQR 离群、跨表策略性差异）只计入 `soft_residual_rate` 供参考。
改造前验证 3 轮不收敛（conflict 残留恒为 0.0611），改造后**第 1 轮即通过**（质量分 95.7）。

### 4.3 为什么 LLM 不碰数值

检测与修复全部由纯函数实现（可单测、可复现、零成本），LLM 只负责「下一步查什么」「如何排修复顺序」
「怎么写结论」。这样既避免了模型幻觉改错数据，也让整个流程在无 Key 时可离线复现。

---

## 5. 运行留档（对应交付项 6）

每次运行在 `runs/<run_id>/` 下产生：

| 文件 | 内容 |
| --- | --- |
| `事件记录.jsonl` | 逐条事件：`stage`/`llm`/`tool`/`error`。含模型名、完整提示词、参数、token、成本、工具入参与耗时、错误与修正方式 |
| `智能体输出.json` | 检测结果、计划、执行轨迹、验证过程、下游指标、实体匹配映射 |
| `运行摘要.json` | 汇总统计：LLM 调用次数、工具调用次数、错误次数、总 token、总成本、耗时 |
| `智能体运行摘要.md` | 人类可读的运行摘要（Report Agent 生成） |

整理后的可读版本见 `docs/智能体运行记录.docx` / `.md`（由 `scripts/07_run_log_digest.py`
从原始 JSONL 自动生成，含 LLM 调用明细与完整提示词、ReAct 决策轨迹、工具调用、错误与修正表）。

---

## 6. 数据说明

- **原始数据**：UCI Machine Learning Repository — *Bank Marketing*（`bank-full.csv`，45,211 行）。
  `scripts/01_make_dataset.py` 会自动尝试 UCI 官方地址与多个镜像，全部失败时回退到**同分布合成数据**，
  因此离线环境同样可复现。
- **跨表构造**：由数据自身用 md5 派生出「客户编号 / 中文姓名 / 证件后四位 / 城市 / 开户年份」生成客户档案表，
  并在档案表中制造 ID 书写变体（`C043689` / ` 043689 ` / `cus-043689`）、打错一位、教育水平与主表冲突，
  用于考察**跨表实体匹配**能力。
- **缺陷注入**（`src/config.py: InjectConfig`，随机种子 `SEED=20251231`，完全可复现）：

| 缺陷 | 注入方式 | 比例 |
| --- | --- | --- |
| 缺失值 | 多字段随机置为 `""`/`?`/`N/A`/`null`/`None`/`。` | 6% |
| 重复数据 | 精确复制 + 近似重复（空格/大小写扰动） | 4% + 1.5% |
| 异常值 | 域外极端值（如 `age=999`）+ IQR 离群 | 2% |
| 格式不一致 | 千分位、全角数字、单位后缀、大小写混杂、首尾空白 | 5% |
| 字段冲突 | 年龄与开户年份自相矛盾、跨表教育水平不一致 | 2% |
| 标签错误 | 极小比例翻转 `y` | 1.5% |
| 参照表噪声 | ID 书写变体与单字符打错 | 15% |
| 类别不平衡 | 原始数据本身正类仅 11.7% | — |

注入过程被完整记录到 `data/ground_truth/缺陷真值.json`（含每个被污染单元格的行号、列名、缺陷类型、
原始值，以及 `_origin` 标记哪些行是重复副本），因此**修复效果可以被精确度量**，而不是靠肉眼看。

---

## 7. 主要结果（真实大模型留档运行 `run_20261031_234343`，deepseek-chat，随机种子固定）

**检测**（`docs/指标汇总.json`）

| 问题类型 | Precision | Recall |
| --- | --- | --- |
| 缺失值 | 1.0000 | 1.0000 |
| 格式不一致 | 1.0000 | 1.0000 |
| 异常值（域边界硬口径） | 0.9988 | 0.9513 |
| 字段冲突（自洽性） | 0.9385 | 1.0000 |
| 跨表实体匹配 | 1.0000 | 1.0000 |
| 标签错误 | 0.4418 | 0.7611 |

**修复**：重复数据 1.0000 / 1.0000（删 2,508 行，真值 2,486 行）；格式精确匹配率 0.8544；
缺失填补 0.4137（越界值 100% 回到业务边界内）；冲突按业务规则重算，近似命中率 1.0；
标签修正 precision 0.519 / recall 0.2212（只翻极高置信项）。

**下游分类（脏数据 → 处理后）**：accuracy **+0.0127**、precision **+0.0258**、recall **+0.0284**、
F1 **+0.0272**、ROC-AUC **+0.0338**（五项全正）。

**消融发现（报告核心分析点）**：8 档前缀累加实验显示，质量指标的改善与下游收益并不同步 ——
去重一步贡献最大（F1 +0.0256），缺失填补反而使 F1 下降 0.0079，冲突消解（按年龄重推开户年份）
进一步下降 0.0253，而最后一步标签修正回升 0.0288；最终 F1 0.5973 与「仅做可精确还原修复」的
0.5980 基本持平。结论：**数据质量指标变好不等于下游任务变好，必须分开度量**；
「按逻辑修正」也未必等于「按分布修正」。

---

## 8. 已知限制

1. **标签错误只在极高置信时才翻转**（阈值 0.02，上限 500 行），因此 recall 偏低（0.149）——
   这是刻意的保守策略，其余可疑标签输出为人工复核清单。
2. **跨表教育水平冲突按「以主表为准」记录不修改**，属于业务策略而非能力缺陷。
3. 缺失值填补采用「同职业分组众数」，连续字段的信息损失无法完全恢复，故精确率不高（0.43）。
4. 消融实验中每档独立建模，受随机性影响指标存在 ±0.005 量级波动。
5. **标签修正的收益含循环论证成分**：翻转依据来自「模型对该行标签极不自信」，而同一族模型又参与
   下游训练与评估，因此该步的 F1 提升不宜全部归功于修正本身，真实能力以对真值的召回 0.149 为准。

---

## 9. 团队分工

见 `docs/个人贡献说明.docx`；六项交付物的文件对应关系见 `docs/交付物清单.md`。
