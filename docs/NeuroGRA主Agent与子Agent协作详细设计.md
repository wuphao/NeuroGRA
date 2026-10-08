# NeuroGRA 主 Agent 与子 Agent 协作主流程详细设计（V1）

运行入口见 [RWE 运行说明](RWE患者ID到可追溯报告运行说明.md)和 [Web 工作台](Web工作台运行说明.md)。本文维护在线架构、现有实现、复查约束及剩余任务，旧实施计划和分批交付记录已删除。

更新日期：2026-10-08。病史、认知、检验、影像报告 Agent 及文本复查/报告流程已有实现；DiaMond 真实分类一致性仍受阻。本文中的目标设计不自动代表所有字段、医学规则和模型能力已经实现。

## 1 目标、约定与现有代码边界

本文按当前需求确定 NeuroGRA 的在线架构：一个主 Agent 接收患者资料，根据实际内容调用不同子 Agent；子 Agent 可调用共享知识图谱、向量库及专业工具；主 Agent 检查各子 Agent 结果，对不确定、明显错误或互相矛盾的内容发起定向交互，最后由主 Agent 汇总结果。

本文作为在线工程实施口径。[《总体设计》](总体设计.md)第三部分的“综合分析智能体”由主 Agent 承担；该文第四部分的证据状态复核由主 Agent 的复查阶段和确定性校验器共同执行。首期不再另设独立的综合 Agent 和复核 Agent，以免职责重叠。未来可以将某项复核委托给独立角色，但最终状态、预算和报告仍由主 Agent 控制。

外部患者数据只要求患者ID，其余键通常为中文、内容与层级不固定。未知患者是否填写量表，也不预设可用检查。内部 ID、任务状态、版本和来源引用由系统生成，不要求患者提供。《总体设计》附录 A 的病例字段清单是研究资料收集目标，不是在线接口必填表单。

实施目标是打通真实调用链，不等待完美知识图谱、向量检索、完整条件推理和全部影像工具。模拟实现只能用于开发与测试；真实运行必须标明未实现、不可用和失败的能力，不能用模拟证据替代检索结果。

| 能力 | 本地核查现状 | V1 处理 |
|---|---|---|
| 文本查询 | `knowledge/retrieval/service.py` 提供 BM25 查询 | 复用，转换成共享检索返回结构 |
| 图谱 | 已有 Neo4j 写入和只读查询适配；当前抽取仍包含术语共现 | 图边用于定位原文，v0.3 语义结构仍待迁移 |
| 向量检索 | 已有 BGE-M3 索引、版本绑定与融合适配 | 服务、索引或发布不匹配时明确降级 |
| 在线主/子 Agent | 已实现四专业、复查、分批处理和报告 | 统一结构化模型调用，受来源、预算与版本约束 |
| DiaMond | 存在训练权重、原始影像与 HDF5 推理脚本 | 封装为影像 Agent 的受控工具，验证后启用 |

适配器、环境探测与模型权重加载已有实现；raw 预处理、RegBN 构造及训练状态恢复仍阻止真实分类一致性验收，默认 blocked_validation。下文的能力要求不能替代该验收。

## 2 总体结构与责任边界

```text
任意中文患者 JSON、附件、影像模态/时间/路径
                    |
             接入、解析、资料盘点
                    |
          NeuroGRA 主 Agent + 编排执行器
                    |
       根据资料内容生成并验证任务计划
                    |
       +------------+------------+------------+
       |            |            |            |
    病史 Agent   认知 Agent   检验 Agent   影像 Agent
       |            |            |            |
       +--------共享检索服务-------+       DiaMond 工具
       |         /     |     \                 |
       |      BM25   Neo4j   向量库            |
       +------------+------------+-------------+
                    |
               主 Agent 检查结果
                    |
       有具体问题：向对应子 Agent 发复查任务
                    |                  |
                    |              回查/检索/修正
                    +<-----------------+
                    |
          主 Agent 最终汇总 → 校验 → 报告
```

主 Agent 决定“分析什么、交给谁、追问什么、何时结束”。确定性编排执行器负责实际调用、并发、超时、预算、权限、状态保存和返回结构校验。主 Agent 不直接执行任意 Python、shell 或数据库语句；只能选择已登记的子 Agent 和工具。

子 Agent 负责本领域发现、依据检索和专业解释，不能调度其他子 Agent，也不能在内部无限复查。跨领域问题交回主 Agent。检索服务和 DiaMond 是工具，不是额外参与投票的专家。

共享事实由编排执行器统一写入。子 Agent 返回新增事实候选或解释，不能直接修改患者原始资料。跨角色分析默认基于同一病例快照，主 Agent 接收修正后增加状态版本。

## 3 外部患者数据来源与结构

### 3.1 接入约定

入口接受一个 JSON 对象。唯一必填键为 `患者ID`（非空字符串）；其他键允许任意中文名称和嵌套结构。值可以是文本、数字、布尔、对象、数组或 null。读取失败、无有效患者ID属于输入错误；只有患者ID属于有效但资料不足的病例，不启动空分析，返回资料不足结果。

支持三种数据来源：直接中文字段、病历/检验/量表等附件、影像文件或目录。附件支持范围由实际安装的解析器声明，不能假设所有 PDF、扫描件和电子表格都已可解析。字段内容可能混合多个领域，一份病历可拆给多个子 Agent。

推荐但不强制的样例（虚构输入，只说明接口）：

```json
{
  "患者ID": "P0001",
  "基本情况": {"年龄": 68, "性别": "女", "受教育年限": 9},
  "家属说的情况": "近两年记忆下降，最近半年吃药需要提醒。",
  "既往情况": "高血压10年。",
  "一次评估": {"时间": "2026-09-10", "表格名称": "MoCA", "总分": 20},
  "检查资料": [
    {"名称": "门诊记录", "路径": "data/patients/P0001/门诊记录.pdf"},
    {"名称": "检验结果", "时间": "2026-09-12", "路径": "data/patients/P0001/检验.xlsx"}
  ],
  "影像数据": [
    {"模态": "MRI", "时间": "2026-09-15", "路径": "data/patients/P0001/MRI/", "序列": "T1", "报告路径": "data/patients/P0001/MRI报告.pdf"},
    {"模态": "PET", "时间": "2026-09-16", "路径": "data/patients/P0001/PET/", "示踪剂": "FDG"}
  ]
}
```

`一次评估` 可以改为其他字段，也可以完全没有量表。`基本情况` 中的字段均非必填。系统保留原始键名，不强迫输入端提前按专业分栏。

影像条目标准键为 `模态、时间、路径`，模态或时间未知时允许为 null；有影像报告时可增加 `报告路径` 或 `报告文本`。可选键包括部位、序列、示踪剂、格式和备注。路径是文件或目录，优先使用相对病例数据根目录的路径，也允许配置范围内的绝对路径。

时间接受日期、年月和原始描述。内部保存原始值、解析值与精度；不能将“2026-09”虚构为“2026-09-01”。附件元数据补出的时间和模态需记录来源，与用户字段冲突时保留两份记录。

### 3.2 缺失信息与未知内容

- 没有某个键：只表示未提供，不能说明患者没有该症状或没做检查。
- null、空字符串或“未知”：保存为缺失/未知，不作为阴性事实。
- 原文明示“未做”：标记未检查；原文明示“否认”：标记明确否认，并保留否认的作用范围。
- 不认识的量表：保留名称、项目和原分数，禁止自动套用 MoCA/MMSE 的解释。
- 不认识的字段：归入待分类资料；一次受控语义分类仍不能识别则列入未利用资料。
- 不同时间结果：保留纵向记录；不同来源同一时间矛盾：建立冲突组，不能覆盖。
- 历史诊断属于“病历记载的历史判断”，不是系统本次结论；实验时目标标签和未来信息不得进入输入。

## 4 接入、盘点与内部数据结构

### 4.1 数据处理顺序

1. 保存原始 JSON 快照与内容哈希，解析患者ID。
2. 枚举字段和明确的附件路径，不扫描患者未指定的其他目录。检查路径、文件类型和可访问性。
3. 按解析器能力读取文本、表格、报告和影像元数据。扫描件需要 OCR 时记录能力与结果；解析失败不是正常结果。
4. 以规则初分和受控语义识别生成资料清单。字段名只提供线索，实际内容决定分配。
5. 提取原子事实，保留数值、单位、时间、提供者、原文位置；区分观察事实、历史诊断、工具结果和分析推断。
6. 生成病例快照和可用能力清单交给主 Agent。没有后续数据更新时不反复解析相同文件。

### 4.2 内部对象

内部采用英文键和 Pydantic 等类型校验；对用户呈现使用中文。每个引用都能回到原始输入，内部 ID 不要求用户填写。

| 对象 | 核心字段 | 作用 |
|---|---|---|
| CaseSnapshot | case_id, patient_id, version, raw_input_hash, records, observations, images, inventory, issues | 主 Agent 使用的病例快照 |
| SourceRecord | record_id, source_kind, original_key, path, text, locator, time, parse_status | 保存患者原始来源 |
| Observation | observation_id, domain, name, value, unit, status, time, context, source_refs, origin | 可追踪患者事实 |
| ImageAsset | asset_id, modality, sequence, tracer, time, path, series_id, report_refs, usability | 影像工具输入候选 |
| DataInventory | domain, availability, record_ids, observation_ids, unresolved_items, tool_eligibility | 按领域盘点有什么和缺什么 |
| AgentTask | task_id, agent_type, case_version, input_refs, questions, permitted_tools, budget | 主 Agent 的委派 |
| AgentResult | result_id, task_id, version, status, findings, claims, evidence_refs, uncertainties, tool_results | 子 Agent 的结构化结果 |
| ReviewRequest/Response | request_id, target_result_version, issues, actions, resolution, revised_result | 主子交互协议 |
| CaseState | run_id, snapshot, plan, results, claims, issues, events, budget, knowledge_release_id | 共享运行状态 |
| FinalReport | patient_id, run_id, status, data_used, findings, assessment, differential, unresolved, citations | 最终输出 |

Observation.status 使用 `observed / present / absent / not_recorded / not_performed / uncertain / conflicting`。量化值标 observed，并不自动表示正常或异常。时间和测量背景保存在 context，支持量表版本、教育校正、方法、样本、参考范围等。

SourceRef 支持原始 JSON Pointer、原文字符区间、PDF 页码、表格工作表/单元格和影像工具输入。JSON 引用必须指向实际存在的字段；文本引用须与对应解析版本一致。主 Agent 和子 Agent 的解释保存为 Claim，不回写为患者事实。

资料 availability 使用 `available / partial / absent / unreadable / unclassified`。影像另分文件存在、报告可读、工具可用三个状态，防止把“有文件”当成“已分析”。资料缺口由本次分析需求产生，不枚举所有可能检查并暗示必须补做。

## 5 主 Agent 的设计

### 5.1 输入与输出

输入：CaseSnapshot、DataInventory、能力注册表、配置预算、固定知识版本和用户分析目标。主 Agent 首轮只需资料摘要及定位引用，必要时调用 read_case_record 读取原文，避免将全部附件无差别塞入上下文。

输出分两层：运行中的结构化动作和最终报告。允许动作：`dispatch_agent`、`request_review`、`read_case_record`、`search_knowledge`、`synthesize`、`finalize`。每个动作必须包含对象、具体问题和停止条件，执行器校验后执行。

主 Agent 不由一个自由文本提示词全权执行系统流程。模型负责提出计划、识别语义问题、综合分析；程序负责注册表匹配、状态转移、预算、引用校验和调用控制。

### 5.2 主 Agent 的七项职责

1. 根据盘点结果判断应启动哪些子 Agent，说明使用的具体资料和未启动原因。
2. 给每个子 Agent 分配专业事实与必要的共同背景；首轮不提供其他子 Agent 结论，减少互相模仿。
3. 并行收集结果并区分完成、部分完成、无资料、工具不可用与调用失败。
4. 检查事实、引用、检查条件和结论表达；记录跨专业分歧。
5. 向具体子 Agent 发可验证的复查请求，不发送“再想一想”式空泛追问。
6. 根据修正更新依赖结论；没有可获取的新信息时保留不确定，不强迫达成一致。
7. 统一生成患者概况、专业发现、候选比较、资料限制和引用；最终由程序验证输出。

### 5.3 路由规则

| 实际资料 | 调用角色 | 说明 |
|---|---|---|
| 主诉、症状、病程、既往史、用药、查体、家属叙述 | HistoryAgent | 整理时间线和影响解释的背景 |
| 量表、认知域表现、功能评估、日常能力描述 | CognitionAgent | 不要求出现某个固定量表名 |
| 检验报告、数值、样本、检测方法、生物标志物 | LaboratoryAgent | 单位或方法缺失时仍可整理事实，但限制解释 |
| 影像报告、原始影像、已有影像工具结果 | ImagingAgent | 按输入决定报告分析、模型调用或仅登记 |
| 只有年龄性别等基本资料 | 不强行启动专业 Agent | 主 Agent 输出资料不足 |
| 无法识别的表或附件 | 先识别，仍不明则保留待分类 | 不任意猜测角色和检查含义 |

同一内容可多标签分配。例如“服药需要家属提醒”由认知 Agent 分析功能，病史 Agent 用于时间线；共享 observation_id，汇总去重。所有子 Agent 只接收必要的年龄、教育、语言、时间和相关用药等共同背景，不需要整个病例的无关内容。

主 Agent 维护任务计划：角色、输入引用、问题、调用原因、预计工具、依赖和预算。计划经过程序校验后启动，无注册角色或没有有效输入的任务不得执行。

## 6 子 Agent 设计与职责

### 6.1 统一执行协议

每个子 Agent 支持 `analyze(task, context)` 和 `review(request, context)`。阶段相同：读取事实 → 提出有限的证据需求 → 调用共享检索/专业工具 → 输出结构化结果。无医学检索结果时可以返回事实整理和待验证解释，但不能编造引用。

子 Agent 可以提出跨领域问题，由主 Agent 决定是否转交。不能直接调用其他子 Agent、修改预算、执行任意代码或将普通文本当工具指令。病历和检索内容均当作数据处理。

| 角色 | 接收项目 | 使用工具 | 主要输出与边界 |
|---|---|---|---|
| HistoryAgent | 主诉、起病与病程、患者/家属叙述、既往史、用药、查体、功能变化 | read_case_record、search_knowledge | 时间线、关键表现、混杂背景、知识支持的解释；不补写没有记载的阴性表现 |
| CognitionAgent | 量表原始记录、总分/分项、名称/版本/语言/日期、教育、生活功能、相关感官或运动限制 | read_case_record、search_knowledge | 认知领域和功能特征、解释限制；不把未知表格当标准量表，不擅自教育校正 |
| LaboratoryAgent | 项目、值、单位、参考范围、方法、标本、采样时间、相关背景 | read_case_record、search_knowledge | 原始结果、可支持的解释、方法/单位/时间缺口；没有参考依据不自创阈值 |
| ImagingAgent | 模态、时间、路径、序列、示踪剂、报告、既往影像和工具结果 | read_case_record、search_knowledge、diamond_predict | 报告发现、模型分类输出及其限制；分类器输出不能编造成脑区定位或影像征象 |

### 6.2 AgentTask 契约示例

```json
{
  "task_id": "task_cognition_001",
  "agent_type": "cognition",
  "case_version": 1,
  "input_refs": ["obs_001", "obs_002"],
  "background_refs": ["obs_age", "obs_education"],
  "questions": ["整理实际提供的认知与功能资料", "哪些解释需要补核对量表版本或背景？"],
  "permitted_tools": ["read_case_record", "search_knowledge"],
  "budget": {"max_llm_calls": 3, "max_retrieval_calls": 2, "timeout_seconds": 120}
}
```

### 6.3 AgentResult 契约

| 字段 | 说明 |
|---|---|
| result_id, task_id, version, case_version | 结果标识和输入版本 |
| status | completed / partial / skipped_no_data / failed |
| used_observation_ids, used_record_ids | 实际使用的资料 |
| findings | 有患者来源的事实整理 |
| claims | 专业解释、诊断层级、患者事实引用、知识或模型结果引用 |
| uncertainties | 不确定的具体原因、影响结论、可执行的解决动作 |
| information_requests | 缺患者事实还是缺医学知识，预期从哪里查 |
| tool_results | 工具成功、不可用、失败及原始结果引用 |
| limitations, errors, usage | 局限、结构化错误和资源消耗 |

Claim 包含 `claim_id, text, kind, level, observation_ids, evidence_ids, tool_result_ids, strength, limitations`。kind 区分事实描述、医学解释和模型分类。医学解释需要患者事实和适用知识；模型分类需要真实工具结果及输入引用；不能将模型分数当作适用知识。

strength 使用 descriptive / tentative / conditional / supported，不输出未经校准的疾病概率。子 Agent 不需要填写主观“置信度百分比”；不确定性要写成可核查的原因。

## 7 共享知识检索接口

所有子 Agent 都具备 search_knowledge 能力，主 Agent 也可直接调用。同一接口封装 BM25、Neo4j 和向量库；后端配置属于系统配置，不混入患者 JSON。

```text
search_knowledge(RetrievalRequest) -> EvidenceBundle

RetrievalRequest:
  request_id, task_id, question, patient_fact_refs,
  candidate_terms, known_conditions, unknown_conditions,
  requested_backends, top_k, knowledge_release_id

EvidenceBundle:
  bundle_id, request_id, status,
  backend_status, items, uncovered_questions, warnings, usage

EvidenceItem:
  evidence_id, document_id, span_id, clause_id?,
  title, quote, context, page, version, matched_by,
  review_provenance, applicability, allowed_use, limitations
```

后端状态逐一返回 `ok / empty / unavailable / timeout / failed / not_configured`。整体状态区分 ok、empty、degraded、failed。未配置向量库不能静默宣称已使用向量检索；BM25 有结果但图谱不可用时返回 degraded 并继续可用分析。

首期流程：词项/别名构造查询 → 各可用后端召回 → 按来源片段去重 → 补原文上下文 → 返回证据与缺口。排序先采用简单可复现方式，后续再优化融合、重排、候选对检索和条件求值。

现有知识图谱主要为 associated_with 共现关系，只允许 background 或原文定位用途，不能自动升级为直接诊断支持。condition 为空可能是未抽取，不能视为已知无条件。审核来源区分人工、自动通过和未知，不能仅看 approved 状态。

只有当关键条件已核对且引用真正支持主张时才允许 direct_support；条件未知用 conditional_support 或 background。适用性可先由保守结构校验与 Agent 解释共同产生，记录判定来源，后续用条件树求值替换。

知识缺口走此接口；患者检测方法等资料缺口走 read_case_record。检索分数只用于排序，不代表疾病概率或结论可信度。知识版本在一次运行内固定；临时证据不自动写回正式知识图谱。

## 8 主 Agent 与子 Agent 的复查交互

### 8.1 什么情况触发复查

| 触发问题 | 主 Agent 动作 | 成功标准 |
|---|---|---|
| 数值、单位、时间与原文不符 | 请求对应 Agent 回读具体记录 | 修正值能定位到原始来源 |
| 出现没有来源的患者事实 | 要求提供来源或撤回 | 找到有效来源，或删除该事实及依赖结论 |
| 阴性、未检查和未提供混淆 | 指定原文要求重判状态 | 状态与原文含义一致 |
| 医学引用不支持主张或遗漏条件 | 要求定向检索/补上下文 | 新证据支持相应强度，或降低/撤回主张 |
| 模型标签被写成确诊 | 请求影像 Agent 解释工具输出范围 | 恢复为模型分类结果，保留限制 |
| 结果不确定且可从已有资料解决 | 指定缺失字段和读取范围 | 从实际资料获得字段，或明确原资料也没有 |
| 跨 Agent 分歧 | 给各相关 Agent 发送争议事实与对方结构化依据 | 区分错误、不同时间/层级或仍无法解决的分歧 |
| 工具失败或输入不适配 | 校验输入；只对可恢复错误重试 | 有合法工具结果，或明确降级并停止重试 |

“主 Agent 觉得不对”只是复查线索，不能直接否定子 Agent。子 Agent 可以维持原判断，但必须提供原始证据和回应。没有新增事实的不确定性不反复询问模型；不能要求子 Agent 为达成一致而提高结论强度。

### 8.2 ReviewRequest 示例

```json
{
  "request_id": "review_001",
  "target_agent": "cognition",
  "target_result_id": "result_cognition_001",
  "target_result_version": 1,
  "claim_ids": ["claim_003"],
  "issue_type": "unsupported_patient_fact",
  "question": "你写了该量表分数已经教育校正，请核对这一信息是否出现在原始记录中。",
  "source_refs": ["record_scale_001"],
  "allowed_actions": ["read_case_record", "revise_claim"],
  "success_criterion": "找到明确校正记载，或删除已校正表述并说明该字段未知。",
  "if_unresolved": "保留原分数，限制依赖教育校正的解释。"
}
```

子 Agent 返回 `ReviewResponse`：request_id、resolution（corrected / clarified / maintained_with_evidence / unresolved / failed）、读取和检索记录、新增证据、逐问题回答、revised_result_version、superseded_claim_ids、remaining_limitations。

旧结果保留但标记 superseded；主 Agent 更新依赖图并使受影响结论失效，重新汇总。不能把新旧结果同时计入支持证据。响应针对过期病例或结果版本时拒绝应用，重新给出最新上下文。

### 8.3 有界循环与预算

V1 默认最多 2 轮复查，每个子 Agent 初次分析最多 3 次模型调用、2 次检索；复查任务各自最多 3 次模型调用、2 次检索，但仍扣除全局预算。建议全局起始上限：48 次模型调用、24 次检索、2 次 DiaMond 调用、总时限 900 秒。均为可调工程初值，不代表效果最优。

模型超时默认 120 秒、影像工具超时默认 600 秒，实际截止时间取任务时限与全局剩余时间的较小值。GPU 工具默认并发 1；专业 LLM 任务默认并发最多 4。为最终汇总保留模型调用预算，保留不足时使用已校验事实模板输出 partial 结果。

停止条件：没有可执行问题、问题全部解决、前后证据与结论无有效变化、重复相同请求、达到轮数/调用/时间预算。网络瞬时失败可有限重试，重试计入总预算；结构错误、模态缺失和不支持格式不盲目重试。缓存工具结果相同输入不重跑，模型分类与主 Agent 意见不一致不是重跑模型的理由。

## 9 DiaMond 影像模型的工具封装

### 9.1 已核查的实际入口

外部项目位置由 `configs/clinical.default.yaml` 的 `diamond.repo_root` 指定；它是本地独立依赖，不要求随主仓库提交。

- 原始影像入口：`tools/predict_diamond_raw.py`。接受 --mri、--pet、--checkpoint、--output-csv、--device；支持 SimpleITK 可读影像文件及 DICOM 序列目录。
- HDF5 入口：`tools/predict_diamond.py`。读取 MRI/T1/data 和 PET/FDG/data；原始病例路径优先走 raw 入口。
- 已存在 split0～split4 的 bestval/latest 权重。首期配置明确固定一个 checkpoint，记录哈希，不按文件时间自动挑选，不默认进行五折集成。
- 同目录 _hyperparams.yaml 当前 class_num=3、modality=multi、img_size=128，with_mri/with_pet 均为 true。
- raw 脚本实际要求 MRI 和 PET 两种输入，非 multi 会报错。命令行列出 mono 选项不等于已支持单模态推理。
- raw 脚本做强度缩放到 0～1、CropOrPad 到 128×128×128，输出 CSV 包含 pred_idx、pred_label、prob_0 等。三分类标签为 CN/MCI/AD。

### 9.2 能力声明与调用条件

工具名称：`diamond_predict`。用户不直接提供 checkpoint 和 Python 环境，由工具配置确定。

首期能力声明为“已验证预处理域内的 MRI T1 + FDG PET 双模态分类”。源码数据加载路径反映 T1/FDG，具体训练输入分布仍需核对。CT、其他 PET 示踪剂、只有 MRI 或只有 PET 均不能直接送当前双模态脚本，也不能用零矩阵或另一模态复制值代替缺失模态。

| 当前影像资料 | ImagingAgent 行为 |
|---|---|
| 可读报告，无适配影像对 | 分析报告，不调用 DiaMond |
| 仅 MRI / 仅 PET / 仅 CT | 登记原始数据，报告有则分析；返回 unsupported_input 或 missing_modality |
| MRI T1 + FDG PET，时间及来源可核对 | 完成配对、预处理准入后调用 DiaMond |
| 有多次 MRI/PET | 构造明确影像对，记录配对依据；不简单取两个最新文件 |
| 模态、示踪剂、时间或序列不清 | 优先读取元数据；仍不清则保留 pending，不默认为符合模型 |
| 路径不可读或文件损坏 | 返回结构化错误，主流程继续处理其他资料 |

影像配对必须属于同一患者。默认按同次明确访视/检查组合配对；跨日期配对需设置经过项目确认的最大间隔参数 max_pair_interval_days，未配置时不自动推断跨访视兼容。未知时间不能当作时间一致。所有模型输入不得晚于评估截止时点；该截止时点由运行参数或病例分析目标明确，缺失时说明时间范围限制。

多序列 DICOM 目录先由适配层识别并选定 SeriesInstanceUID。现有脚本按文件数最多选择序列，不能直接视为正确 T1/FDG 序列；V1 应传入隔离的已选序列目录，或对该入口做小范围扩展支持显式 series_id。原始影像保持只读。

### 9.3 工具输入输出契约

```text
diamond_predict(DiamondRequest) -> DiamondResult

DiamondRequest:
  tool_call_id, patient_id, case_version,
  mri_asset_id, pet_asset_id,
  pair_selection_reason, assessment_cutoff,
  model_profile, timeout_seconds

DiamondResult:
  tool_call_id, status,
  input_assets, acquisition_times, input_hashes,
  model_name, checkpoint_path, checkpoint_hash,
  hyperparams_hash, preprocessing_version, label_map,
  prediction: {label, index, class_scores, score_type},
  validation_status, limitations, warnings,
  raw_output_ref, duration_ms, error
```

状态为 ok / not_applicable / input_invalid / unavailable / timeout / failed。失败和不适用时 prediction 为 null，不生成示例概率填充字段。score_type 对当前三分类为 softmax_uncalibrated，数值保持原始模型输出。类名、个数、范围、有限值和概率和校验通过才能标记工具成功。

模型结果表述为“DiaMond 对该影像对的分类输出”，不能写为患者真实患病概率或确诊结论。CN/MCI/AD 这一标签空间不能直接覆盖本项目所有候选病因，也不能通过 MCI 标签推出具体病因。没有定位输出就不能生成“模型发现某脑区异常”等解释。

### 9.4 封装方式

V1 采用独立 Python 环境的 subprocess 适配器，避免 NeuroGRA 与 DiaMond 的 torch、MONAI、SimpleITK 等依赖相互影响。使用固定可执行文件和参数列表，shell=False，不拼接患者提供的命令文本。

适配器流程：校验影像与能力 → 选择明确影像对/序列 → 计算输入与模型指纹 → 命中缓存则复用 → 在独立临时目录调用 raw 脚本 → 检查退出码和 CSV → 转换 DiamondResult → 保存工具产物。

输出目录由程序生成，不能允许模型随意覆盖文件。缓存键包含输入文件内容/目录清单指纹、checkpoint、超参数、预处理和脚本版本。超时终止该工具进程树，释放占用并记录状态。后续需要减少模型重复加载时再升级常驻 worker，接口不变。

### 9.5 接入前必须验证的事项

这些是工具启用前的工程验证项，不要求本次文档任务训练或修改模型。

1. 固定目标 checkpoint，核对其结构、对应训练参数与标签映射，不能因为共享 yaml 写 split=1 就认为所有权重来自同一 split。
2. 核对训练数据预处理与 raw 入口是否一致，包括方向、体素间距、轴顺序、配准、裁剪和 PET 条件。CropOrPad 只改变数组尺寸，不等于完成空间标准化。
3. 检查训练标签语义。src/adni.py 的映射含 FTD→1、Dementia→2；是否存在于实际训练样本未核实，需查看训练数据统计，不能将标签名当作纯病因训练的证明。
4. 检查 RegBN 状态恢复。当前训练保存代码可见 model/head/optimizer 状态，独立 RegBN 未见保存；raw 脚本重新创建 RegBN。应核对实际 checkpoint 及推理一致性，不能把源码疑点直接断言为已证实模型错误。
5. 用已知验证样本比较既有推理与工具封装输出；重复调用稳定、输入顺序正确、类名映射正确。若发现不可恢复的不一致，保持工具为未验证并降级报告分析，不自行补造状态或声称等价。

## 10 主流程状态机与伪代码

运行状态：received → profiling → planning → analyzing → reviewing → synthesizing → finalizing → completed / partial / failed。reviewing 可回到 analyzing，受统一轮次约束。任务另有 pending/running/completed/partial/skipped/failed 状态。

```python
async def analyze_patient(patient_input, config):
    state = intake_and_profile(patient_input, config)
    if state.invalid_input:
        return failure_report(state)
    if not state.has_usable_clinical_data:
        return insufficient_data_report(state)

    plan = await main_agent.plan(state.inventory, state.background, capabilities)
    tasks = executor.validate_plan(plan, state)
    results = await executor.run_subagents(tasks, state.snapshot)
    state = apply_validated_results(state, results)

    for round_index in range(config.max_review_rounds):
        hard_issues = validators.check(state)
        review_plan = await main_agent.review(state, hard_issues)
        requests = executor.select_actionable_requests(review_plan, state.budget)
        if not requests or no_new_information_possible(state, requests):
            break
        responses = await executor.review_subagents(requests, state.snapshot)
        updated_state = apply_revisions_and_invalidate_dependents(state, responses)
        if not has_material_change(state, updated_state):
            state = updated_state
            break
        state = updated_state

    draft = await main_agent.synthesize(state)
    checked = validators.check_final_report(draft, state)
    # 最终汇总新引入的错误也必须处理；预算不足时删改违规主张并保留局限。
    final = await bounded_final_repair_or_template(checked, state)
    return persist_and_render(final, state)
```

语义错误由主 Agent 在预算内修正；引用不存在、数值错误、未实际执行工具却声称执行等确定性错误必须阻断相应主张进入报告。事实整理可以正常返回，不因一个专业失败而丢弃所有结果。

## 11 输出报告与运行记录

主 Agent 是唯一最终汇总责任者。报告渲染器只将已校验结构转为中文 Markdown，不再独立推断。

对外报告结构：患者ID、分析状态、已使用资料及时间范围、关键事实、各专业结果、综合分析、候选比较、未决问题、后续核对方向、患者来源与医学引用。对内保存 run_id、模型/提示词/知识版本、工具指纹和完整结构化结果。

每条关键判断能追踪到患者事实和医学依据或真实工具输出。跨专业分歧可以保留；不能用投票、平均 LLM 自评分或检索分数产生诊断概率。病因倾向、认知状态和影像模型类别分层展示。

状态含义：completed 表示本次流程完成，不代表已确诊或资料齐全；partial 表示某项已计划分析因能力/服务/预算问题未完成，或仅能给出有限结果；failed 表示无可交付结果或输入根本无效。报告另设 assessment_status 表示 supported_tendency / conditional / unresolved / insufficient_data，避免把执行状态当判断确定性。

存放：患者输入及事实快照放 data/clinical/；运行事件、检索中间结果和工具 CSV 放 output/clinical/<run_id>/；正式中文报告与配套结构化报告放 docs/病例报告/<patient_id>/<run_id>/，遵守本项目 AI 正文不放 output 的约定，真实病例内容不纳入版本库。

## 12 代码模块与接口

```text
code/neurogra/clinical/
├─ schemas.py / intake.py / parsing.py / profiling.py
├─ orchestration.py / workflow.py / batching.py
├─ history.py / specialists.py / imaging.py
├─ retrieval.py / retrieval_worker.py / vector.py
├─ diamond.py / diamond_probe_worker.py / diamond_input_worker.py
├─ llm.py / storage.py / narrative.py / acceptance.py
└─ cli.py / rwe.py / rwe_login.py / web.py
```

现有实现采用平铺模块，职责表中的名称表示逻辑职责，不要求存在同名子包。

| 模块 | 输入 | 输出 |
|---|---|---|
| intake/parsing/profiling | 中文患者对象与附件 | CaseSnapshot、DataInventory |
| MainAgent.plan | 资料盘点、背景、能力 | TaskPlan |
| Executor | 合法任务/动作、剩余预算 | 子 Agent 结果或工具结果 |
| SubAgent.analyze | AgentTask、病例快照、工具代理 | AgentResult |
| MainAgent.review | 全部结果、确定性检查 | ReviewRequest[] |
| SubAgent.review | 复查请求、目标结果、相关证据 | ReviewResponse |
| MainAgent.synthesize | 已处理问题的 CaseState | FinalReport 草案 |
| validation/reporting | 报告草案与真实引用对象 | 校验后的 JSON 与 Markdown |

现有 CLI：`python -m neurogra.clinical.cli --config configs/clinical.default.yaml analyze --patient <病例.json>`。Python 入口位于 `workflow.py` 的 `analyze_patient(raw, config)`；RWE 和 Web 使用同一临床流程。

配置放 configs/clinical.default.yaml，包含 model_provider/model、enabled_agents、knowledge_release_id、backend 开关、预算、路径根目录、DiaMond 的 repo_root/python_executable/checkpoint/device 和配对策略。模型凭据从环境读取，不出现在患者数据或报告中。模型推理走本地服务还是外部服务由部署配置明确。

持久化可使用现有 SQLite 技术栈另建 clinical.sqlite；表按 runs/tasks/results/reviews/events/tools 管理。每次状态提交具备输入版本、幂等键和结果版本；中断恢复重用已成功任务，不重复扣除历史消耗，也不遗忘已消耗预算。患者路径可访问范围由配置限定，模型不能扩大读取范围。

## 13 四个端到端交互场景

#### 场景 A：只有描述，没有填过量表

输入只有患者ID与家属描述。盘点识别症状及功能变化；主 Agent 启动病史 Agent，若存在明确认知/功能内容则同时启动认知 Agent。认知 Agent 可以分析叙述，但必须说明无标准化量表，不能补出分数。无影像/检验不启动对应角色。主 Agent 最终汇总已有事实和可支持的有限解释。

#### 场景 B：量表名称或解释背景不清

患者提供“评估表.xlsx”及总分，无法确定表名。资料识别读取表头，仍无法识别则认知 Agent 只整理项目。若子 Agent 错误声称是 MoCA，主 Agent 通过来源校验发起 ReviewRequest。子 Agent 回查后撤回表名或提供实际表头来源；无法确定则报告保留未识别量表，不强行解释分数。

#### 场景 C：存在符合工具要求的 MRI 与 FDG PET

影像 Agent 接收明确时间、路径与序列；适配器核对配对、环境、权重和预处理准入后调用 DiaMond。返回分类分数及工具指纹。主 Agent 若发现影像 Agent 将模型分类写成确诊，要求其修订并引用适用范围。最后保留模型输出，与其他资料共同讨论，不让模型类别覆盖整个候选病因空间。

#### 场景 D：影像结果与专业意见不同

主 Agent 先检查是否来自同一时间、同一对象、同一判断层级，再向相关子 Agent 发送具体争议和证据引用。若只是历史结果与当前变化，不认定逻辑冲突；若输入错配，撤回工具结果并修复受影响结论；若依据真实且仍不一致，保留分歧及其影响，不重复调用相同模型直到得到想要的类别。

## 14 分阶段实现和验收

| 阶段 | 工作 | 完成条件 |
|---|---|---|
| P0 契约与接入 | 任意中文字段、来源定位、资料清单、影像三要素、状态机 | 任意未知键不丢失；患者ID之外非必填；文件问题可追踪 |
| P1 主子调用 | 主 Agent 计划、四子 Agent、统一模型网关、并发与存储 | 按真实输入动态调用，真实模型返回结构化结果 |
| P2 检索接入 | BM25 复用、图查询基础、向量适配接口 | 每个子 Agent 可调用统一接口，后端未接入明确降级 |
| P3 DiaMond | 固定模型档案、输入准入、独立环境、CSV 转换与一致性验证 | 合法影像对得到真实结果，单模态不会被伪装执行 |
| P4 复查汇总 | 问题分类、主子追问、版本修正、停止和报告 | 至少展示一次真实定向修正及一个未解决问题的合理保留 |
| P5 优化 | 向量检索、图文融合、条件求值、缓存与质量评估 | 在固定病例与知识版本上量化改进 |

验收场景必须包括：只有患者ID、只有描述、未知中文键、未知量表、同名字段多次随访、显式阴性与缺失、解析失败、单模态影像、错配时间/序列、不支持示踪剂、DiaMond 超时、检索空结果、向量库未配置、子 Agent 编造事实、主 Agent 无依据质疑但子 Agent 正确、跨层级假冲突、真正未决冲突、预算耗尽及中断恢复。

工程指标：正确路由率、原始资料覆盖率、引用可定位率、结构合法率、复查解决率、错误保留率、无效重复调用率、耗时和资源消耗。医学质量另以独立人工标注病例评价；流程成功率和工具可运行不等于诊断准确率。

主流程完成的最小定义：输入一份实际中文病例 → 主 Agent 根据真实资料调度 → 子 Agent 调用可用检索/工具 → 主 Agent 定向复查 → 修正或明确保留未决 → 输出双重来源可追踪的报告。知识检索暂不完美可以如实降级，但不能用未实现能力冒充真实执行。


## 15 现行复查约束与剩余任务

本节汇入旧证据状态复核设计和实施计划中仍有效的约束。以下为设计与验收要求，现有确定性校验和模型复查并不保证完整医学语义判定。

对每项候选结论分别记录支持、削弱/限制、知识缺口、患者资料缺口、可比冲突、引用/层级/版本问题和未覆盖需求。条款条件的满足、不满足、未知，与证据允许用途分开保存；未知条件不能直接支持肯定结论，没有支持也不等于存在反证。

| 问题 | 允许动作与结束条件 |
| --- | --- |
| 医学知识或条款定义缺失 | 在原预算内检索及补读来源，条件和例外完整且可定位才算覆盖。 |
| 患者条件未知 | 只查已有真实记录；记录不存在则保留待补项，不能用文献补造患者结果。 |
| 证据似乎冲突 | 先核对主体、字段、层级、时间、方法、单位和范围；不同时间及不同维度不自动互相否定。 |
| 引用不支持具体表述 | 缩小表述、替换依据或撤回主张；不能只凭引用对象存在判为支持。 |
| 版本或结论层级错误 | 核对有效版本、用途和允许强度，避免从症候群跨到病理、从未记录跨到排除。 |

复查请求应绑定目标结果版本、主张和触发依据，明确问题、允许动作、禁止推断和成功条件。优先处理患者事实错误、条件误用和来源不支持，再处理可比冲突、关键知识缺口及一般补充。无真实资料源可查时登记未决，不反复搜索文献。

新增知识需要与原候选去重并重新组织上下文，必要条件和已知可比分歧两侧均计入预算；不能无限追加内容。所有角色共享累计调用、时间和上下文预算，重复请求和无有效变化停止。主张按患者原始资料和医学来源分别追溯；无法核实的解释保留条件或撤回。

当前代码已包含四类专业 Agent、分批分析及复查、向量融合、准备阶段恢复、报告依赖和依据核对。历史测试数量不再作为当前验收数量，重新验收时记录命令、代码版本和实际结果。

仍需完成：

1. DiaMond raw 输入维度与预处理核验、RegBN 构造和训练状态恢复、标签映射及重复性对照；通过前保持 blocked_validation，不标影像分类已完成。
2. DICOM 的显式序列选择与转换，不能默认拿切片最多序列当作已验证输入。
3. v0.3 图谱迁移、条件适用性求值及 v1.0 候选探索/完整证据选择。它们是待实现研究内容，不能由基础图查询或临床工作流验收替代。
4. 固定知识、病例和预算下的独立医学标注及效果评价；流程完成、协议检查和模型自评均不能替代临床质量评估。
