# NeuroGRA 主 Agent 实施步骤与执行计划

更新日期：2026-09-23。状态：第一批 S01～S08 基础实现已交付，验收范围与环境限制见第 23 节；S09～S17 待实施。下文各步骤保留原计划，实际文件和可执行命令以第 23 节及 README 为准。

依据：[主 Agent 与子 Agent 协作详细设计](NeuroGRA主Agent与子Agent协作详细设计.md)。本文把该方案拆成可逐步实现和验收的工程任务；架构与角色边界以设计文档为准，本文负责具体执行顺序、输入输出、失败处理及交付标准。

## 1. 本轮实施目标

实现一个 NeuroGRA 主 Agent：接收格式不固定的中文患者资料，识别实际数据，调用适合的病史、认知、检验和影像子 Agent；各子 Agent 通过统一工具查询知识；主 Agent 根据结果定向追问、要求复核和修正，最后输出有来源的报告。

外部数据只要求“患者ID”；影像记录“模态、时间、路径”，信息未知允许 null。不得要求患者提供内部 record_id、observation_id、任务状态等字段。

四项必须打通的真实链路：

1. 中文患者数据 → 资料盘点 → 主 Agent 计划 → 专业子 Agent。
2. 子 Agent → 真实可用检索后端 → 原文和引用 → 专业结果。
3. 主 Agent 发现具体问题 → 对应子 Agent 回查或检索 → 修正/保留未决 → 更新汇总。
4. 合法影像对 → DiaMond 推理工具 → 真实输出 → 影像 Agent → 主 Agent 报告。

第一条文本闭环可以先完成，第四条需要独立验证后加入。向量库暂未接通时允许明确降级，但不能把模拟结果或 BM25 命中标成向量命中。最终“影像接入完成”必须有真实工具验收，不能以接口占位代替。

## 2. 执行路线、依赖与阶段交付

### 2.1 步骤总表

| 步骤 | 工作 | 依赖 | 核心交付 |
|---|---|---|---|
| S01 | 环境与可用能力盘点 | 无 | 能力报告、实施配置初稿 |
| S02 | 统一输入输出契约 | S01 | 所有跨模块 schema、合成病例 |
| S03 | 模型网关、预算和运行存储 | S02 | 真实结构化模型调用、事件与版本记录 |
| S04 | 中文病例与附件接入 | S02、S03 的存储接口 | 原始资料快照、来源引用 |
| S05 | 文档/表格/影像元数据解析 | S04 | 带来源位置的解析资料 |
| S06 | 资料分类与患者事实标准化 | S03、S05 | CaseSnapshot、DataInventory |
| S07 | 共享检索及原始记录工具 | S02、S03；记录读取依赖 S04/S05 | EvidenceBundle、RecordReadResult |
| S08 | 主 Agent 规划与任务执行器 | S03、S06 | TaskPlan、按资料调度 |
| S09 | 子 Agent 基类及病史 Agent | S07、S08 | 第一条真实专业分析链路 |
| S10 | 认知与功能 Agent | S09 | 认知分析和量表限制 |
| S11 | 检验 Agent | S09 | 检验解释、方法和单位缺口 |
| S12 | DiaMond 工具封装与验证 | S01、S02、S03、S05 | 通过准入的真实推理工具 |
| S13 | 影像 Agent | S09、S12；报告分支可先做 | 报告分析与模型结果整合 |
| S14 | 主子 Agent 定向复查 | S08、S09；可逐步纳入 S10/S11/S13 | ReviewRequest/Response 闭环 |
| S15 | 主 Agent 汇总与报告输出 | S14 | 校验后的 JSON、Markdown |
| S16 | CLI、续跑和端到端验收 | S15；影像验收还需 S13 | 可重复运行的主流程 |
| S17 | 向量检索与检索融合优化 | S07、S16 | 独立可比较的检索升级 |

### 2.2 推荐执行顺序

第一批：S01～S08，形成可读入病例、盘点资料、真实调用模型与检索的基础。

第二批：S09 → S14 → S15 → S16 的文本子集，先让一份只有病史的病例走完主子 Agent 分析、复查与报告。此时可使用测试替身验证协议，但交付演示必须有真实模型和真实检索。

第三批：S10、S11；同时推进 S12 的环境与模型验证，再接 S13。不同模块实现可分开推进，集成仍按输入输出依赖完成。

第四批：把所有子 Agent 纳入 S14～S16 的综合验收，然后实施 S17。基础图谱查询在 S07 做，复杂重排和条件算法不阻塞文本主流程。

### 2.3 阶段里程碑

| 里程碑 | 能展示的结果 | 不满足时的处理 |
|---|---|---|
| M1 可读病例 | 自由中文输入转成有来源的事实与资料清单 | 先修接入和解析，不调试诊断表达 |
| M2 文本闭环 | 主 Agent → 病史 Agent → 检索 → 复查 → 报告 | 明确缺的是模型、检索还是契约能力 |
| M3 多领域协作 | 按资料动态启动病史/认知/检验，处理共享事实 | 不靠固定启动所有角色凑齐流程 |
| M4 影像接入 | 已验证影像对得到 DiaMond 实际输出 | 未通过验证则保留报告分析，M4 不算完成 |
| M5 可复现 V1 | 中断恢复、错误分支、报告来源及预算均验收 | 记录未达标项，不能仅以一次正常运行交付 |
| M6 检索增强 | 实际向量召回与基线对照 | 改善未证实时保留可切换基线 |

不预设日历工期。S01 完成后根据模型服务、影像环境、附件格式和真实样本可用性估算；每一步以可检查产物和验收结果完成，不以已编写文件数量完成。

## 3. 所有步骤共同遵守的接口规则

### 3.1 输入与返回约定

外部入口：`analyze_patient(patient: dict, config: ClinicalConfig) -> RunResult`。

只有顶层患者对象允许自由字段。内部对象进行严格类型验证，关键字段拼错不得静默忽略。外部字段即使无法识别，也必须保留原始键和值。

所有内部结果保留 schema_version、来源版本与稳定引用。AgentResult、工具结果、检索结果使用各自明确状态，不用一个 success 布尔值代替缺失、失败、不可用和不适配。

ErrorItem 统一字段：`code, stage, message, retryable, affected_refs, details`。原始文件内容不自动放进普通错误日志；详细输入保存到对应病例产物，日志使用引用定位。

调用预算至少含全局截止时间、模型/检索/影像调用计数和 token 记录。所有模型调用（含资料分类、格式修复和重试）经过同一网关计数。初始上限沿用设计文档，S16 用实际耗时修订，不把开发初值宣称为最优配置。

### 3.2 时间、缺失与事实约束

- 时间保留 raw、解析值、精度及来源；只有年月不补日期，相对时间缺参考点不强制换算。
- observed 数值不等于阳性；未提供不等于阴性；未做必须有原文依据。
- 患者原始事实、历史诊断、工具结果和 Agent 推断分别保存。
- 接入保留原文，规范化不覆盖原值；来源无法定位的候选事实不进入正式患者事实集。
- 一次 run 固定病例快照、知识版本、模型与提示词配置，运行期间更新生成新版本并使相关任务失效。

### 3.3 产物位置

| 类型 | 位置 |
|---|---|
| 实现代码 | code/neurogra/clinical/ |
| 运行配置模板 | configs/clinical.default.yaml |
| 可提交的合成测试病例 | code/neurogra/clinical/tests/fixtures/ |
| 患者原始资料/内部快照 | data/clinical/ |
| 运行状态库 | data/clinical/clinical.sqlite |
| 调用日志/检索中间产物/工具 CSV | output/clinical/<run_id>/ |
| 正式报告 JSON 和 Markdown | docs/病例报告/<patient目录>/<run_id>/ |

患者目录使用系统生成的安全路径标识，原始患者ID不直接拼入路径；患者报告正文保留原始患者ID。真实病例和报告不进入 Git，合成 fixture 必须明确标记 synthetic。不得将临床数据直接传给未配置的外部服务。

## 4. S01：环境与能力盘点

### 设计与具体工作

检查当前 Python、依赖、模型服务、知识 release、Neo4j、向量服务及 DiaMond 独立环境。盘点只报告实际能力，不自动下载大型模型或启动训练。模型连通性用非患者的最小结构化请求验证，凭据仅检查能否使用，不输出密钥。

DiaMond 只先核对 repo_root、推理入口、checkpoint 列表与解释器，推理一致性留到 S12。默认模型及 checkpoint 必须写进配置，不能依据“最新文件”自动选择。

### 输入

- 现有 pyproject.toml、knowledge 配置与 release manifest。
- 模型服务地址/模型名的部署配置。
- DiaMond 路径 `D:/Python Project/DiaMond/DiaMond`。

### 输出

`CapabilityReport`：组件名、configured/available/verified 状态、版本、受支持输入、失败原因。

`ImplementationProfile`：本轮启用的模型、检索后端、解析器、影像工具档案及待补配置。向量未配置可以继续；LLM 不可用则真实 Agent 演示受阻，但仍可完成非模型模块。

### 拟新增文件与接口

- clinical/config.py
- clinical/diagnostics.py
- configs/clinical.default.yaml
- `probe_capabilities(config) -> CapabilityReport`

### 验收与失败处理

每项能力都有明确状态；未验证与不可用分开。配置指向不存在的解释器/权重时返回定位信息；不替换为其他环境冒充成功。验收报告列出后续步骤哪些可做、哪些需等待外部条件。

## 5. S02：统一数据契约与合成样例

### 设计与具体工作

先固定跨模块对象，再编写 Agent 提示词。schema 至少覆盖患者输入、来源、事实、影像、任务、计划、结果、证据、复查、预算、事件和报告。

内部 ID 由系统生成。来源 ID 绑定原始输入哈希与位置；结果 ID 与版本分开，修正生成新版本。引用字段只允许指向已登记对象，不由 LLM 自创有效 ID。

### 输入

设计文档中的结构、S01 能力报告和三种输入形态（中文字段、附件、影像）。

### 输出

| 契约 | 必需内容 |
|---|---|
| PatientInput | 患者ID + 任意其他原始内容 |
| SourceRecord/SourceRef | 原始来源、内容哈希、JSON Pointer/页码/单元格/文本范围 |
| Observation | 原值、标准化值、单位、状态、时间、来源、origin |
| ImageAsset | 模态、时间、路径、序列、示踪剂、元数据及可用状态 |
| TaskPlan/AgentTask | 角色、输入引用、问题、工具权限、预算、路由理由 |
| AgentResult/Claim | 使用资料、事实整理、解释、证据引用、限制、状态 |
| RetrievalRequest/EvidenceBundle | 问题、后端状态、实际原文证据、未覆盖问题 |
| ReviewRequest/Response | 目标版本、问题、动作、成功标准、修正结果 |
| FinalReport/RunResult | 对外结论、资料范围、引用、未决项及执行状态 |

### 拟新增文件与接口

clinical/schemas/{patient,source,task,result,retrieval,review,runtime,report}.py。

`validate_patient(raw) -> PatientInput`；`validate_result(payload, expected_schema) -> TypedResult`。

### 验收与失败处理

准备仅患者ID、自由叙述、未知量表、检验、单模态影像、合法双模态元数据、冲突记录七类合成病例。验证未知中文键完整往返保存、无患者ID被拒绝、空值不转阴性、年月不补日。量表和检验值不能被宽松类型转换误改，数值转换失败保留原文并报告。

## 6. S03：模型网关、统一预算与存储基础

### 设计与具体工作

实现 `generate_structured`，统一模型请求、schema 校验、超时、有限重试、格式修复和 usage 记录。V1 默认先对接当前配置的本地模型服务，其他 provider 通过适配器扩展。模型凭据、温度、上下文和输出上限在配置中固定，不由 Agent 自行修改。

输出不合法时允许一次受预算约束的格式修复；修复只处理结构，不允许补写不存在的患者事实。仍不合法返回失败，不能直接把文本当合法结果。

SQLite 建立 runs/tasks/results/reviews/events/tool_calls/budget_ledger。执行器在并发调用前原子预留预算，完成后结算；超时和失败也有记录。模型返回 token usage 缺失时写 unknown 或明确估计方法，不伪造精确值。

### 输入

ClinicalConfig、ModelRequest（角色、消息、输出 schema、工具描述、截止时间）、S02 对象。

### 输出

ModelResponse：合法结果或 ErrorItem，模型版本、调用编号、usage、耗时。

RunContext：run_id、配置哈希、版本、预算账户、事件存储与对象仓库接口。

### 拟新增文件与接口

- clinical/llm/{base,provider,structured}.py
- clinical/storage/{sqlite,repository}.py
- clinical/orchestration/budget.py
- `generate_structured(request, context) -> ModelResponse`
- `reserve_budget(run_id, operation) -> Reservation`
- `save_result(result, expected_version) -> StoredResult`

### 验收与失败处理

真实模型返回一个合成任务的结构化结果；格式错误、超时、网络失败均有可复现处理。并发请求不能超预算；恢复后预算不清零。中断时未确认是否完成的外部调用标为 indeterminate，恢复前检查产物；不能承诺外部服务严格 exactly-once，也不能默默重复昂贵影像调用。

## 7. S04：自由中文病例接入与附件登记

### 设计与具体工作

递归读取任意中文对象和数组，保留原始路径与值；不把字段名未知当错误。登记明确声明的附件、报告和影像路径，普通文本中偶然出现的路径字符串不直接当作文件读取指令。

定义路径规则：运行配置提供 patient_data_root，相对路径相对此根解析；绝对路径须在允许范围。对文件路径做解析与边界检查，防止患者ID或路径使输出落到错误位置。

### 输入

PatientInput、患者数据根目录、允许访问路径、输入文件位置。

### 输出

`IntakeResult`：raw_snapshot、SourceRecord[]、AttachmentDescriptor[]、原始 ImageAsset 候选、issues。

SourceRecord 保存字段 JSON Pointer；附件记录存在、类型、可读性、哈希和原始路径。缺少文件不删除其条目。

### 拟新增文件与接口

clinical/intake/{loader,paths,attachments}.py；`ingest_patient(raw, config) -> IntakeResult`。

### 验收与失败处理

同一输入重复接入得到相同来源指纹；未知字段不丢失；数组顺序可追踪。缺文件返回 issue，其他资料继续；患者ID有效但没有内容不报语法错误，交给主流程形成资料不足报告。

## 8. S05：附件解析与影像元数据读取

### 设计与具体工作

按实际能力注册解析器：原生文本、文本层 PDF 优先实现；表格和 DOCX 按 S01 依赖接入。扫描 PDF 未接 OCR 时标记 requires_ocr，不能用空文本表示正常。表格保留工作表、行列、表头、单位与公式缓存值是否可用，不能随意计算无依据的量表总分。

影像阶段只读取元数据并列出序列，不运行分类模型。文件夹不能仅凭名称判定模态；用户输入和文件头不一致时保留冲突。DICOM 多序列保存每个 SeriesInstanceUID 候选。

### 输入

AttachmentDescriptor[]、影像候选、解析器配置。

### 输出

`ParseResult`：document_id、parser/version、status、ParsedSegment[]、issues。

ParsedSegment 包括 text/raw_cells、page/sheet/cell/offset、section_context、source_ref。影像输出 metadata、series_candidates、可用性与待核对项。

### 拟新增文件与接口

clinical/parsing/{registry,text,pdf,table,docx,image_metadata}.py。

`parse_attachment(descriptor) -> ParseResult`；`inspect_image(asset) -> ImageInspection`。

### 验收与失败处理

PDF 引用可定位到页码；表格数值与单元格一致；未知表头不套模板。损坏文件、需密码、OCR 不可用、格式不支持各有独立 code。原始影像只读，不在该阶段改写、重采样或删除数据。

## 9. S06：资料识别、事实提取和盘点

### 设计与具体工作

规则处理明确结构（数值、时间、显式影像字段），模型处理自由叙述和含义不明确的字段。分类允许多个领域；病史和认知可共享功能描述。模型提取候选事实必须返回 SourceRef，再由程序核对原文。

标准化保存原值及单位，未知术语先保留原名；不要为统一代码而丢失内容。相对时间依赖可靠记录日期，没有锚点则保存原描述。历史诊断单独标记，实验标签不得混入患者事实。

### 输入

IntakeResult、ParseResult[]、影像检查结果、受预算约束的模型网关。

### 输出

CaseSnapshot：records、observations、images、timeline、conflict_groups、unclassified_items。

DataInventory：按领域给出 available/partial/absent/unreadable/unclassified、关联记录、可解释性限制及工具准入候选。

### 拟新增文件与接口

clinical/profiling/{classifier,extractor,normalizer,inventory}.py。

`profile_case(sources, parsed, context) -> ProfileResult`。

### 验收与失败处理

“吃药需提醒”能分到病史和认知，但引用同一事实；“未提到幻觉”不变成明确否认；同一检查不同日期不覆盖。每个原始资料片段有 processed/unclassified/failed 等处理记录。语义提取失败时保留原始资料和明确字段，不生成虚假完整盘点。

## 10. S07：共享检索与患者原文读取工具

### 设计与具体工作

提供两个完全不同的工具：`read_case_record` 读取当前患者资料，`search_knowledge` 查询一般医学依据。所有子 Agent 都能使用，执行器记录访问和消耗。

S07 首先复用现有 `knowledge.retrieval.service.search(repository, query, top_k, release_id)`，把 SearchHit 转为 EvidenceItem。知识版本在运行开始固定，不能每次查询动态跟随 active release。

Neo4j 适配器使用固定参数化查询模板，基于现有 schema 检索实体、条款与原文，限制返回条数和路径范围；LLM 不直接生成任意 Cypher。按 release 隔离，找不到来源的图节点不能当证据返回。向量后端本步完成接口和 not_configured 分支，真实实现留到 S17。

### 输入

RetrievalRequest：question、patient_fact_refs、candidate_terms、known/unknown_conditions、requested_backends、top_k、knowledge_release_id。

RecordReadRequest：record_id、合法定位范围、case_version。

### 输出

EvidenceBundle：status、各后端状态、原文/上下文/页码/版本/引用、matched_by、适用限制、未覆盖问题。

RecordReadResult：实际原文与 SourceRef，或 record_not_found/stale_version 等错误。

### 拟新增文件与接口

clinical/retrieval/{service,bm25,neo4j,vector,merge}.py；clinical/tools/records.py。

`search_knowledge(request, context) -> EvidenceBundle`；`read_case_record(request, context) -> RecordReadResult`。

### 验收与失败处理

至少一条真实知识查询可回到已有原文；同一 span 多后端命中合并，保留 matched_by；Neo4j 不可用但 BM25 正常返回 degraded。未配置向量不能写 matched_by=vector。associated_with 共现只提供背景或定位用途；condition=null 不表示自动适用。检索为空返回 empty，而非模型编写的替代文献。

## 11. S08：主 Agent 任务规划与编排器

### 设计与具体工作

主 Agent 根据 DataInventory、任务目标和能力生成 TaskPlan；执行器验证计划是否合法。可用角色固定 history/cognition/laboratory/imaging，按资料启用，不强制四个全部调用。

每项任务包含 why、input_refs、background_refs、questions、permitted_tools 和 budget。程序核对引用存在、角色匹配、预算足够和资料覆盖。遗漏重要领域可以要求主 Agent 一次修订；仍不合法时输出明确计划失败或采用记录了原因的保守路由。

并发执行用独立存储事务或连接，避免跨异步任务共享不安全的 SQLite 会话。子 Agent 通过受控工具代理发请求，不能直接拿到数据库连接或调用其他 Agent。

### 输入

CaseSnapshot、DataInventory、CapabilityReport、RunContext、分析目标。

### 输出

TaskPlan、AgentTask[]、未调用角色及原因、任务状态、结果接收事件。

### 拟新增文件与接口

clinical/agents/main.py 的 plan；clinical/orchestration/{registry,planner,executor,state}.py。

`MainAgent.plan(context) -> TaskPlan`；`validate_plan(plan, context) -> ValidatedPlan`；`execute_tasks(tasks, context) -> AgentResult[]`。

### 验收与失败处理

只有病史不会启动检验；有未识别量表也允许认知角色整理原文；只有影像文件但无可用工具时计划应明确分析边界。非法角色、未知引用、超预算任务被拒绝。某子任务超时不取消其他成功结果。未实现角色只能在测试模式使用 fixture，真实模式返回 unavailable。

## 12. S09：子 Agent 基类与病史 Agent

### 设计与具体工作

统一子 Agent 流程：读取任务 → 判断需要查什么 → 受控调用工具 → 产生结构化结果。工具请求由标准 Action 对象表示，即使 provider 不支持原生 function calling，也由执行器执行经过验证的动作，不能让模型输出任意命令。

病史 Agent 先整理起病、时间线、既往史、用药、症状及功能变化，再对确有解释需求的项目检索。角色提示词声明事实和解释分开、阴性要有来源、不能填补患者缺失。

### 输入

AgentTask、相关患者事实、必要背景、原文读取与检索工具代理。

### 输出

AgentResult：used_refs、findings、timeline、claims、uncertainties、information_requests、tool_results、usage。

review 接口先实现“读取指定记录、逐项回应、输出新版本”，复查策略由 S14 主 Agent 实现。

### 拟新增文件与接口

clinical/agents/{base,history}.py；clinical/prompts/{agent_base,history}.md。

`HistoryAgent.analyze(task, context) -> AgentResult`；`HistoryAgent.review(request, context) -> ReviewResponse`。

### 验收与失败处理

一份合成叙述经真实模型和检索得到结构化结果；每条事实可回原文；真实检索失败仍可返回事实整理和限制。完成后优先实现 S14/S15 的最小版本，尽早展示第一条可执行闭环。

## 13. S10：认知与功能 Agent

### 设计与具体工作

接收量表和叙述，不依赖固定表名。已知量表只有明确提供的名称/版本/项目才能使用；总分和分项不完整时保留缺失。教育校正、语言和功能背景必须来自患者记录或明确的工具运算契约，不能由模型默认补齐。

对认知领域和生活功能作结构化整理。分数意义需要检索时提出相应条件需求；未知量表仅描述事实，不套用已知阈值。V1 不新增未经定义的自动计分器。

### 输入

认知/功能 observations、量表原文、年龄/教育/语言等已提供背景、相关病史引用。

### 输出

AgentResult：scale_summaries、cognitive_findings、functional_findings、interpretable_scope、claims、missing_context、引用。专业扩展字段纳入 schema，不放任自由额外键。

### 拟新增文件与接口

clinical/agents/cognition.py；clinical/prompts/cognition.md；`CognitionAgent.analyze/review`。

### 验收与失败处理

未知表格不会自动改名；未注明教育校正不会写成已校正；同量表不同日期独立保留；只提供家属功能描述也能返回有限分析。认知状态表述不能直接转换为特定病因确诊。

## 14. S11：检验与生物标志物 Agent

### 设计与具体工作

按项目拆分结果，保留原单位、参考范围、检测方法、样本和时间；换算必须由已登记的确定性工具执行并保存原值，V1 可先不提供换算工具。

区分“报告明确标记异常”与“Agent 根据知识解释异常”。没有参考范围或方法时不自造界限；相同名称但不同平台/样本/单位不能直接比较趋势。

### 输入

检验 observations、表格/报告原文、检测背景、相关用药与时间信息。

### 输出

AgentResult：test_findings、report_flags、method_constraints、comparable_series、claims、unresolved_fields。

### 拟新增文件与接口

clinical/agents/laboratory.py；clinical/prompts/laboratory.md；`LaboratoryAgent.analyze/review`。

### 验收与失败处理

缺单位/方法产生限制而不是猜值；“未做”不成为阴性；跨日期不同方法不直接认定数值变化代表病程变化。原文解析不完整时标记 partial 并指出具体项目。

## 15. S12：DiaMond 工具封装与接入验证

### 设计与具体工作

分成五个子步骤，每个子步骤产物明确：

| 子步骤 | 输入 | 工作 | 输出 |
|---|---|---|---|
| S12.1 模型档案 | repo、解释器、指定 checkpoint | 固定权重/超参数/代码指纹及标签来源 | DiamondModelProfile |
| S12.2 输入准入 | ImageAsset[]、时间范围、模型档案 | MRI T1/FDG PET、同患者、时间配对、序列核对 | EligibilityResult、SelectedImagePair |
| S12.3 推理适配 | 合法影像对、工具预算 | 独立进程调用 raw 脚本、超时和输出隔离 | 原始 CSV、ToolExecution |
| S12.4 输出规范化 | CSV、输入/模型指纹 | 校验行数、类别、分数、有限值、输入对应 | DiamondResult |
| S12.5 一致性验证 | 已知验证样本、参考输出 | 对比原推理与封装，重复性和训练约定核对 | ModelValidationReport、准入结论 |

入口为现有 `D:/Python Project/DiaMond/DiaMond/tools/predict_diamond_raw.py`，使用参数列表传入 --mri、--pet、--checkpoint、--output-csv、--device，shell=False。不在本步骤重新训练模型。

当前脚本必须同时有 MRI/PET；多序列目录不能直接依赖文件数最多的默认选择。明确选中 SeriesInstanceUID 后提供隔离序列目录或小范围扩展显式序列参数，记录实际使用文件。

影像时间未知、不同访视配对规则未配置、未知示踪剂或不支持模态时不执行分类。不得零填充缺失模态。报告分支仍可继续。

### 输入

DiamondRequest：patient_id、case_version、mri_asset_id、pet_asset_id、配对理由、评估时间范围、model_profile、timeout。

工具配置：固定解释器、repo_root、checkpoint、device、preprocessing_version、配对参数、缓存目录。

### 输出

DiamondResult：status、输入资产及时间、输入/权重哈希、label_map、prediction 或 null、score_type、validation_status、warnings、原始输出引用、耗时/错误。

三分类分数标 softmax_uncalibrated；不是患者真实患病概率。失败输出不能含伪造预测。

### 拟新增文件与接口

clinical/tools/{diamond,diamond_profile,image_pairing}.py。

`check_diamond_eligibility(assets, profile) -> EligibilityResult`；`diamond_predict(request, context) -> DiamondResult`。

### 验收与失败处理

合法已验证样本能得到真实 CSV 和结构化结果；单模态被拒绝；超时关闭工具进程树；相同输入缓存复用；不同权重/预处理缓存失效。

重点核对训练和 raw 入口的空间预处理、标签语义、独立 RegBN 状态恢复。现有源码疑点只记为待验证，不能直接宣称模型错误。若权重无法重现训练时推理，状态为 blocked_validation，仍交付适配代码与定位报告，M4 不标完成。真实影像或参考结果不可获得时可验收工具协议，但实际模型一致性仍是待完成项。

## 16. S13：影像 Agent

### 设计与具体工作

先盘点“报告可读、影像可访问、模型是否适配”，再决定工具。影像 Agent 自己可查询知识，但不能通过检索编写患者影像所见。

报告和模型结果分别建立来源。模型输出是类别分数，不是脑区定位；报告与模型不同需说明时间、输入和判断层级，而非自动认定谁对谁错。

### 输入

影像任务、ImageAsset[]、报告事实、必要背景、search_knowledge/read_case_record/diamond_predict 工具代理。

### 输出

AgentResult：report_findings、model_results、longitudinal_comparison、claims、discrepancies、limitations。model_results 必须引用已成功执行的 DiamondResult，未执行时为空并说明原因。

### 拟新增文件与接口

clinical/agents/imaging.py；clinical/prompts/imaging.md；`ImagingAgent.analyze/review`。

### 验收与失败处理

仅报告可分析；仅 MRI 不假装运行双模态；合法 MRI/PET 可真实调用；CT 无适配工具仍保留资料。主 Agent 质疑分类解释时，影像 Agent 应回查原输出而非为得到不同类别重复推理。

## 17. S14：主子 Agent 复查闭环

### 设计与具体工作

先由确定性校验器检查引用存在、值和单位、使用工具真实性、结果版本和对象关系；再由主 Agent 判断语义问题、知识适用性及跨专业分歧。两类检查结果分别记录，不能把 LLM 判断伪装成确定性验证。

主 Agent 输出 ReviewRequest，必须指定目标 Agent、目标结果版本、claim_ids、具体问题、可用动作、成功标准和未解决时处理。禁止只有“再检查一下”的空请求。

子 Agent 回应 corrected/clarified/maintained_with_evidence/unresolved/failed。维持原判断有充分依据也是合法结果，不以服从主 Agent 作为成功。

### 输入

当前 CaseState、AgentResult[]、EvidenceBundle[]、工具结果、硬校验问题、剩余预算。

### 输出

ReviewRequest[]、ReviewResponse[]、结果新版本、失效/撤回 claim 列表、未决项、stop_reason。

### 拟新增文件与接口

clinical/validation/{references,facts,claims}.py；clinical/orchestration/{review,dependencies}.py；MainAgent.review。

`detect_issues(state) -> Issue[]`；`MainAgent.review(state, issues) -> ReviewPlan`；`apply_review(response, expected_version) -> StateUpdate`。

### 版本与停止设计

先构建事实→工具/证据→专业 claim→汇总 claim 依赖。修正只使受影响下游失效，不重跑所有 Agent。旧结果保留为 superseded，不能继续计为有效支持。过期版本回应拒绝应用。

默认最多两轮；无可执行动作、已重复同一问题、没有新增有效信息或预算不足则停止。不确定但无法从现有资料解决时直接保留。为最终汇总预留预算。

### 验收与失败处理

至少覆盖：虚构教育校正被撤回；未提供被误写阴性后修正；主 Agent 错误质疑被子 Agent 用原文澄清；跨日期结果并非冲突；真正分歧保留；重复问题退出。复查失败不恢复已撤回结论，也不无限重试。

## 18. S15：主 Agent 综合结果与报告

### 设计与具体工作

主 Agent 只汇总最新有效结果，按事实去重，分别表达患者发现、认知/功能判断、候选病因和模型分类。不得将多个角色重复引用同一事实当作独立证据。

汇总输出 FinalReport schema，渲染器生成 Markdown。主 Agent 汇总阶段新增的结论也需要来源检查；无依据内容在预算内修正，仍不合法则删除对应主张并列出局限，不直接输出未校验文本。

### 输入

最新 CaseState、专业结果、复查记录、有效证据、工具结果和未决项。

### 输出

FinalReport：患者ID、资料范围、执行状态、assessment_status、事实摘要、专业分析、候选比较、结论限制、待核对信息、患者/知识引用。

RunResult：run_id、status、report_paths、errors、usage、artifact_refs。

### 拟新增文件与接口

MainAgent.synthesize；clinical/reporting/{builder,renderer}.py；clinical/validation/report.py。

`MainAgent.synthesize(state) -> FinalReportDraft`；`validate_report(draft, state) -> ReportCheck`；`render_report(report) -> str`。

### 验收与失败处理

completed 只表示流程完成；判断确定性由 assessment_status 表示。缺资料病例可 completed + insufficient_data，预期任务因工具失败未完成用 partial。整份报告无法形成时才用 failed。主模型不可用但已有合法事实时用模板返回 partial，不把模板输出伪装成模型综合意见。

## 19. S16：命令入口、恢复与全流程验收

### 设计与具体工作

提供 analyze、inspect-run、resume 三个 CLI 子命令，统一调用服务层。运行目录、报告目录和配置指纹返回给用户；调试事件可以查看主 Agent 调用了谁、提出什么问题、是否解决。

拟定命令（实现后才可运行）：

```text
python -m neurogra.clinical.cli analyze --patient <病例.json> --config configs/clinical.default.yaml
python -m neurogra.clinical.cli inspect-run --run-id <运行ID>
python -m neurogra.clinical.cli resume --run-id <运行ID>
```

### 输入

患者 JSON、运行配置、固定模型/知识版本；续跑输入 run_id 及已保存状态。

### 输出

最终报告、运行概况、任务及复查记录、工具产物、验收报告。

### 拟新增文件与接口

clinical/{service,cli}.py；clinical/orchestration/resume.py；clinical/tests/。

`analyze_patient(patient, config) -> RunResult`；`resume_run(run_id, config) -> RunResult`。

### 验收矩阵

| 场景 | 预期行为 |
|---|---|
| 只有患者ID | 不启动无资料的子 Agent，返回资料不足 |
| 只有描述 | 正确路由，能查知识并输出有限结果 |
| 未知中文字段/未知量表 | 原文保留，不猜表名和阈值 |
| 缺失与明确阴性混合 | 状态和引用正确区分 |
| 同项目不同日期 | 时间线保留，不覆盖或误判冲突 |
| PDF 解析失败 | 标记未利用资料，其他分支继续 |
| 检索为空/向量未配置 | 无伪造引用，明确后端状态 |
| 只有影像报告/只有 MRI | 有限分析，不假装 DiaMond 已运行 |
| 合法双模态 | 真实推理、正确引用工具输出 |
| 错配时间/示踪剂/序列 | 准入失败，不强制执行 |
| 子 Agent 错误与主 Agent 误判 | 定向修正或有证据维持，记录交互 |
| 无法解决的分歧 | 保留未决，不循环追问 |
| 子 Agent/检索/模型超时 | 预算内处理，已成功结果保留 |
| 中断恢复 | 不丢历史预算，不重复完成任务，版本一致 |
| 汇总新增无依据内容 | 最终校验拦截并修正/删除 |

测试分层：契约和确定性行为用小型 fixture；模型适配用真实非患者请求；主流程用合成病例加真实模型与知识库；影像真实调用用适配且允许使用的验证样本。mock 测试通过不替代真实工具验收。

工程完成门槛：所有约定场景有预期结果、关键引用均可定位、缺失不被伪造成事实、预算和停止条件可触发、至少一次可展示的主子复查闭环。医学效果另设独立病例标签与人工评阅，不把流程通过率报告为诊断准确率。

## 20. S17：向量检索和图文融合迭代

### 设计与具体工作

在主流程稳定后接入真实 embedding 和向量索引。先固定 embedding 模型、维度、文本视图、归一化方式及 release；索引和查询必须一致。向量结果使用既有 span/document 引用，不能成为来源孤立的新文本库。

实现 BM25、向量、图谱的简单融合与去重，保持相同请求返回契约。后续再做候选对重排、条件树适用性、图路径筛选和更细的证据包。不要同时改 Agent 提示词和检索策略后把收益全部归因于向量库。

### 输入

已发布文本与来源、embedding 配置、向量存储配置、固定开发/留出查询集。

### 输出

VectorIndexManifest、实际 vector backend、融合 EvidenceBundle、与 BM25 基线的对照结果。

### 拟新增文件与接口

完善 clinical/retrieval/vector.py；索引构建放 knowledge/indexing 下，通过版本化 manifest 对接。

`build_vector_index(release, config) -> VectorIndexManifest`；`VectorRetriever.search(request) -> BackendResult`。

### 验收与失败处理

维度/模型/知识版本不一致阻断该后端；数据未索引返回明确状态；服务失败降级有记录。评价区分 Hit@K 与 Recall@K：Hit@K 是每个查询是否至少命中一条，Recall@K 是命中的相关证据数占全部相关证据数，再按约定汇总。现有开发单条查询不能证明质量提升，需要独立查询集及人工相关性标注。

## 21. 贯穿案例：每一步的数据怎样传递

下面是虚构数据，用于说明交接，不是医学规则或真实结果。

输入：患者ID、家属叙述、一个未注明版本的认知量表总分、MRI 路径；没有 PET 和检验。

| 环节 | 接收到什么 | 交出什么 |
|---|---|---|
| S04 | 任意中文 JSON | 原始记录和 MRI 资产，保留未知键 |
| S05 | 附件和影像路径 | 可读文本、来源位置、MRI 元数据或解析问题 |
| S06 | 全部解析资料 | 病史/认知 available，影像 partial，检验 absent；量表版本未知 |
| S08 | 资料盘点和能力表 | 病史、认知、影像任务；检验不调用并写明原因 |
| S09/S10 | 各自事实与原文 | 时间线、认知事实、检索依据及版本限制 |
| S13 | MRI 资产与可能的报告 | 无 PET 不运行双模态；有报告分析，无报告则说明未分析 |
| S14 | 假设认知 Agent 错写“已教育校正” | 主 Agent 指定原记录追问；子 Agent 查无记载后撤回并更新版本 |
| S15 | 最新有效结果 | 报告保留原分数、未知背景、影像未推理、检验未提供 |
| S16 | 完整运行记录 | 可查看调度理由、实际检索、复查前后变化及最终文件 |

错误注入仅用于测试，真实模式不主动制造错误来展示复查。真实主 Agent 没发现问题时允许零次复查，不为了形式固定循环两轮。

## 22. 每一步的交付记录模板

实施时为每一步填写以下记录，任务完成后再进入依赖它的步骤：

```text
步骤编号与名称：
状态：not_started / in_progress / completed / blocked_external
输入版本及依赖：
新增/修改文件：
实际输入样例：
实际输出样例与路径：
已验证行为：
失败分支与处理：
真实调用还是模拟测试：
剩余问题及影响：
允许开始的下一步：
```

步骤实际状态见第 23 节；尚未进入的 S09～S17 保持 not_started。遇到外部阻塞（例如缺少可验证影像样本）时记录具体阻塞与可继续的工作；可以完成文本主流程，但不能把被阻塞的影像验收标成完成。

执行起点是 S01 能力盘点与 S02 契约，首个可用成果是 M2 文本闭环，完整 V1 在 M5 验收后交付。后续优化遵循同一接口，不反复推倒患者数据结构和主子 Agent 协作协议。


## 23. 第一批实施记录（S01～S08）

### 23.1 交付范围

2026-09-23 已实现病例接入、解析、事实盘点、结构化模型调用、共享检索及主 Agent 规划/调度基础。为避免大量空壳文件，首期将每个模块集中为一个 Python 文件，后续复杂度增加再拆包。代码位于 `code/neurogra/clinical/`，没有改动现有 knowledge 算法和 DiaMond 模型。

| 步骤 | 实际代码 | 验收与状态 |
|---|---|---|
| S01 | config.py、diagnostics.py、configs/clinical.default.yaml | 完成能力盘点；真实模型连通通过，能力可用与已验证分开记录 |
| S02 | schemas.py、tests/fixtures/ | 完成严格内部契约、自由中文输入、七类病例和独立叙述样例 |
| S03 | llm.py、storage.py | Ollama JSON schema 输出，有限格式修复；SQLite 原子预算、任务范围计数、结果版本和事件 |
| S04 | intake.py | 任意中文键/嵌套值保留，JSON Pointer、路径边界及影像三要素 |
| S05 | parsing.py | TXT/MD、文本 PDF、CSV/TSV、XLSX、DOCX；DICOM 元数据；OCR/缺失依赖明确返回状态 |
| S06 | profiling.py | 规则与真实模型结合分类提取，精确引用校验、缺失状态、时间线、冲突组；模型漏提原文保留 |
| S07 | retrieval.py | 真实 BM25 已验证；Neo4j 只读适配及版本过滤测试通过，服务当前不可达，在线验证待环境恢复；vector 明确未接入 |
| S08 | orchestration.py | 真实主 Agent 生成任务提案、程序生成系统字段并验证；并发执行器具备隔离输入、权限、预算、超时与结果引用校验 |

S08 的实际专业子 Agent 尚未注册，执行器返回 agent_unavailable；测试中的注册 handler 只验证调度协议，不能当作 S09 的真实病史分析。prepare 命令到任务计划即结束，不输出诊断报告，不调用 DiaMond 推理。

### 23.2 真实运行证据

- 非患者模型连通测试：`run_id=bb97a84bc7934e28bffc4d106710464f`，Ollama `qwen3.6:35b` 返回合法结构化响应。
- 最终合成病例试跑：`run_id=c3afcb2c39224293bf7d41989e58829e`，`plan_mode=model`，输出 history/cognition 两类任务，共 8 条内部观测；没有启动缺少资料的检验或影像角色。
- [合成病例试跑产物](../output/clinical/c3afcb2c39224293bf7d41989e58829e/preparation.json)包含患者快照、模型计划、引用证据和限制。
- 固定知识版本：`release_89168ff3faf8e2baf5b278772b25b0e398f04d2b423274e582fb8f326213e69a`。
- 检索状态：bm25=ok、graph=failed、vector=not_configured；整体运行 prepared_with_limitations，不把降级结果冒充完整图文检索。

首次试跑的模型任务格式曾未通过校验，系统按约定显式回退；随后将任务 ID、版本及预算转为程序生成，仅让模型生成角色、资料选择和分析问题，重新真实验证通过。这项修正已保留在当前实现中。

### 23.3 自动化验收

临床基础模块 41 项测试覆盖：自由键保留、年月精度、路径范围、缺文件、PDF OCR 分流、表格/Word 位置、公式缓存缺失、附件时间继承、合成 DICOM 多序列元数据、缺失与阴性区分、原文引用校验、模型漏提保留、共享事实路由、版本冲突、并发预算、格式修复计费、知识版本过滤、检索降级去重、任务超时、输入隔离、伪造引用拦截以及 run 级快照隔离。另有现有知识库 18 项回归测试通过。

图谱版本隔离测试使用隔离驱动，不是在线 Neo4j 连接证明。专业子 Agent 测试使用注册测试 handler，不是专业诊断质量证明。真实资料识别、主 Agent 规划和 BM25 查询使用了实际服务。

### 23.4 使用方式与剩余项

入口：`python -m neurogra.clinical.cli`，支持 probe、prepare、search、inspect-run；全局 `--config` 放在子命令之前。完整命令见 README 的“在线 Agent 第一批基础能力”。

下一步可开始 S09，实现病史 Agent 并注册到 Executor；随后做 S14/S15 的最小复查与汇总闭环。外部剩余条件为 Neo4j 服务在线验证，以及后续 DiaMond 一致性验证所需样本。当前 NeuroGRA Python 环境没有 SimpleITK，NIfTI 等元数据读取会标记不可用；DICOM 元数据适配已通过合成数据测试。

范围说明：没有接入向量检索、未实施医学条件树求值、未执行影像分类、未验证诊断准确率。以上属于后续步骤，不能从第一批工程验收推导其效果。

## 24. 第二批交付：S09 → S14 → S15 → S16 文本子集

已实现病史子 Agent、真实检索与模型分析、主 Agent 定向复查、版本化回复、受限模型汇总、Markdown/JSON 报告，以及 `analyze` / `resume` 命令。实现集中在 `clinical/history.py` 和 `clinical/workflow.py`，沿用第一批平铺模块结构，未迁移为同名子包。

输入输出、停止规则、恢复范围和真实运行记录见 [第二批文本主子Agent交付说明](第二批文本主子Agent交付说明.md)。本批复查可回读病例及已有知识证据；向量、影像及跨专业综合仍留待后续批次。S16 当前从准备产物保存后支持续跑，尚不支持准备阶段内部的断点恢复。

本批自动化验证：55 项 clinical 测试、18 项 knowledge 回归测试通过。交付演示必须查看真实运行记录，不以协议测试替身代替模型与检索验收。

## 25. 第三批交付：S10、S11、S12 验证及 S13 接入

S10/S11 已实现专业结构字段、缺失条件约束、真实模型与检索分析、目标专业复查；S13 已实现影像报告分析和受控工具调用。实现沿用平铺模块：`specialists.py`、`imaging.py`、`diamond.py`，并扩展现有 Executor、workflow、schema 和 CLI。

S12 环境及三路模型权重已真实加载，发现 raw 入口预处理维度、RegBN 构造和独立训练状态恢复问题。适配器与协议测试已交付，S12.5 模型一致性验证及 DICOM 显式序列转换尚未完成；按原验收规则标记 blocked_validation，M4 不标完成。

详细模块输入输出、实际证据、两个集成演示和解阻顺序见 [第三批多专业Agent与DiaMond验证交付说明](第三批多专业Agent与DiaMond验证交付说明.md)。本批累计 75 项 clinical + 18 项 knowledge 测试通过。

## 26. 第四批交付：全 Agent S14–S16 验收与 S17

四类 Agent 已完成统一复查协议测试和真实综合运行；新增报告主张到专业结果版本的依赖、跨专业有界上下文、准备阶段恢复及 audit-run。S17 已构建真实 BGE-M3 1024 维、46 文本块索引，固定模型摘要/语料/发布版本，接入 BM25/向量/基础图谱的 RRF 融合与明确降级。

新增 build-vector-index、evaluate-retrieval 和 audit-run 命令。8 条固定 dev/heldout 工程查询完成真实对照，小样本未证明融合优于 BM25，不宣称医学效果提升。复杂重排、条件树与图路径算法未成为文本流程前置要求。

真实四专业运行通过 66 项确定性验收，影像阻断分支通过 10 项；111 项自动化测试通过。S12 真实推理一致性仍为 blocked_validation，M4 不因此被标记完成。详细记录见 [第四批综合验收与向量检索交付说明](第四批综合验收与向量检索交付说明.md)。
