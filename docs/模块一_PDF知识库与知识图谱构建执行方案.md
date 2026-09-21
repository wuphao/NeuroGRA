# 模块一：PDF 知识库与知识图谱构建执行方案

日期：2026-09-21  
状态：待实施；本文中的路径、类、接口和命令是实现契约，不表示已经存在。  
配套文档：[总体方案](模块一_PDF知识库与知识图谱构建方案.md)。

## 1. 执行目标与约定

交付一条可重复运行的离线流水线，以及不生成诊断结论的知识查询服务：

```text
PDF → Document → ParsedBlock → SourceSpan → Chunk
                                  ↓
                            CandidateClause
                                  ↓
                     Entity + Condition + ClauseRevision
                                  ↓
                         Review → Release
                                  ↓
                       TextIndex + GraphIndex
```

实现顺序为 S01～S12。S01～S05 建立底稿，S06 建立开发用文本检索，S07～S10 构建并审核图谱数据，S11 发布，S12 完成查询与端到端验收。开发索引和正式发布索引隔离。

通用约定：UTF-8；时间为带时区的 UTC ISO 8601；页码从 1 开始；字符区间使用 Python Unicode 字符索引和左闭右开 `[start, end)`；未知字段为 `null`，不得用空字符串伪装已知值；金额或调用量无法获得时为 `null`，不记作 0。

## 2. 模块目录与职责

```text
code/neurogra/knowledge/
  cli.py                   # 参数、子命令、退出码
  config.py                # 配置校验、配置指纹
  schemas/                 # Pydantic 模型与 JSON Schema
    source.py              # Document、ParsedBlock、SourceSpan
    text.py                # Chunk、Citation、SearchHit
    graph.py               # Entity、Clause、Condition
    lifecycle.py           # Task、ReviewDecision、Release
  ingestion/registry.py    # 文件快照、哈希、来源登记
  parsing/base.py           # PdfParser 协议
  parsing/docling.py        # Docling 输出转换
  processing/cleaner.py     # 清洗与位置映射
  processing/segmenter.py   # 原文片段与父子块
  models/base.py            # ExtractionClient、EmbeddingClient
  extraction/extractor.py  # 提示词、结构化候选抽取
  extraction/linker.py     # 术语匹配、歧义候选
  extraction/conditions.py # 条件树校验与规范化
  validation/validator.py  # 来源、类型、数值等检查
  review/service.py        # 审核导出、导入、修订
  storage/sqlite.py        # 权威记录、事务、迁移
  storage/artifacts.py     # JSONL、文件校验和
  indexing/lexical.py      # 分词与 BM25
  indexing/vector.py       # Qdrant 索引适配
  indexing/graph.py        # Neo4j 图投影
  release/service.py       # 构建、校验、激活、回滚
  retrieval/service.py     # 混合检索、实体查询、证据包
  pipeline/runner.py       # 阶段依赖、缓存、重试
  evaluation/metrics.py    # 标注集评估与统计
  prompts/                # 可版本化的抽取模板
  tests/                  # 单元、适配器、集成测试
```

模块依赖方向：CLI → runner/services → schemas 与适配器。领域模型不依赖具体模型厂商、Docling、Neo4j 或 Qdrant。适配器不得自行改变审核状态或激活发布。

数据目录：

```text
data/knowledge/
  registry.sqlite
  sources/<document_id>/source.pdf
  parses/<parse_id>/blocks.jsonl
  parses/<parse_id>/spans.jsonl
  chunks/<chunk_build_id>/chunks.jsonl
  extractions/<extraction_run_id>/candidates.jsonl
  reviews/<review_batch_id>/
  terminology/<terminology_version>/entities.jsonl
  dev_indexes/<build_id>/
  releases/<release_id>/manifest.json
  releases/<release_id>/text/          # 发布快照和 BM25 文件
  releases/<release_id>/graph/         # 节点、边导出
  releases/<release_id>/checks.json
output/knowledge/<run_id>/events.jsonl
output/knowledge/<run_id>/metrics.json
```

所有相对路径从项目根目录解析。源 PDF 以哈希去重后保存不可变副本，原始用户文件不移动、不修改。JSONL 中的大模型原始返回属于构建审计数据，存入 `data/knowledge/`；日志不包含 API 密钥。

## 3. 核心数据结构

### 3.1 ID、修订与公共字段

| 字段 | 生成规则 |
|---|---|
| `document_id` | `doc_` + PDF 字节 SHA-256；同字节同 ID |
| `parse_id` | 文档 ID + 解析器版本 + 解析配置指纹的哈希 |
| `block_id` | parse_id + 页码 + 页面阅读序号的哈希 |
| `span_id` | parse_id + 原文定位列表 + 切分版本的哈希 |
| `chunk_id` | span ID 列表 + 清洗/切分配置 + 块角色的哈希 |
| `entity_id` | 已确认规范概念使用持久 UUID；后续更名不改变 ID |
| `candidate_id` | 抽取任务缓存键 + 候选序号的哈希 |
| `clause_id` | 候选首次入库分配持久 UUID，修订不改变 |
| `revision_id` | 条款 ID + 修订序号；修订内容不可变 |
| `release_id` | 一次发布的 UUID，关联不可变 manifest |

内容哈希采用规范化 JSON：排序键、固定编码、禁止 NaN，集合字段排序而有序逻辑列表保留顺序。不同解析版本不强行复用原文片段 ID。跨来源相似主张通过 `claim_group_id` 分组，不合并来源记录。

公共字段包括 `schema_version: str`、`created_at: datetime`。可修订对象通过追加 revision 更新，不原地覆盖已发布内容。

### 3.2 Document 与 ParsedBlock

`Document`：

| 字段 | 类型 | 说明 |
|---|---|---|
| document_id / checksum | str | 文件标识和完整哈希 |
| source_path / snapshot_path | str | 导入位置与不可变副本位置 |
| title | str \| null | 已核验或待确认标题 |
| source_role | Literal | clinical_guideline / clinical_research / method_reference |
| language | str \| null | zh / en 等 |
| publication_date / source_version / doi / organization | str \| null | 来源元数据 |
| metadata_status | Literal | pending / verified |
| source_family_id / supersedes_document_id | str \| null | 人工确认的版本关联 |
| page_count | int \| null | 成功解析后写入 |

`ParsedBlock`：`block_id, parse_id, document_id, page_no, printed_page_label?, reading_order, block_type, raw_text, bbox, page_width, page_height, extraction_method, parser_quality?, table?, issues[]`。

- `block_type`：heading / paragraph / list_item / table / caption / footnote / figure。
- `bbox`：左上角原点、归一化到 `[0,1]` 的 `[x0,y0,x1,y1]`；适配器负责转换 PDF 坐标。
- `extraction_method`：native_text / ocr / manual_transcription。
- `table` 包含行列数及单元格列表，每格保存 `row, col, row_span, col_span, text, bbox, is_header`；跨页表通过 `table_group_id` 连接。
- 无可靠解析质量分数时 `parser_quality=null`，不得构造虚假概率。

### 3.3 SourceSpan 与来源定位

```python
class SourceLocator(BaseModel):
    block_id: str
    start: int                 # 对应 ParsedBlock.raw_text
    end: int

class EvidenceRef(BaseModel):
    span_id: str
    start: int                 # 对应 SourceSpan.original_text
    end: int
    quote: str                 # 必须等于 original_text[start:end]

class SourceSpan(BaseModel):
    span_id: str
    parse_id: str
    document_id: str
    section_path: list[str]
    locators: list[SourceLocator]
    original_text: str
    normalized_text: str
    normalization_map: list[dict]
    context_span_ids: list[str]
    issues: list[str]
```

以上是字段契约片段，实施时补齐 import、公共字段和 validators。`original_text` 由 locators 指向的字符片段按固定换行规则拼接；拼接区间映射到对应块、页码和 bbox。`normalization_map` 记录规范化字符区间与原文字符区间的对应关系，插入的检索标题不伪造原文位置。映射不可靠的内容不得自动生成精确引文。

`Citation`：`document_id, title, source_role, parse_id, span_id, page_no, printed_page_label?, bbox, quote, extraction_method`。跨页引文返回 Citation 列表，不以单页坐标覆盖全部文字。

### 3.4 Chunk

字段：`chunk_id, chunk_build_id, document_id, span_ids[], parent_chunk_id?, role, original_text, retrieval_text, section_path[], token_count, tokenizer_version, text_review_status, linked_clause_ids[]`。

- `role`：parent / child；仅 child 向量化和参与 BM25，parent 用于上下文扩展。
- `text_review_status`：pending / approved / rejected。
- 父块文本由已知来源构成；返回父块上下文前检查其审核资格。
- `retrieval_text` 可包含标题与别名；引用只能取 `original_text` 对应来源。
- 超长条款允许拆子块，但全部指向完整父块；不能完整容纳于模型上下文的条款进入问题队列。

### 3.5 Entity 与实体提及

`Entity`：`entity_id, canonical_name, entity_type, aliases[], external_ids: dict[str,str], terminology_version, status`。

`entity_type`：disease / syndrome / phenotype / examination / biomarker。

`EntityMention`：`mention_id, text, predicted_type, evidence: EvidenceRef, selected_entity_id?, candidate_entity_ids[], link_method, link_status`。

`link_status`：resolved / ambiguous / local_pending。精确匹配和人工确认可完成链接；模糊匹配只提供候选。外部标准 ID 必须来自词表或已核实映射，不能由模型编造。待确认本地概念可建立 UUID，但关联条款在链接确认前不可发布。

### 3.6 条件树

使用 Pydantic 可辨识联合类型，按 `kind` 区分以下节点；递归类型通过 `model_rebuild()` 解析：

| kind | 必需字段 | 校验 |
|---|---|---|
| ATOM | field, operator, value, evidence_refs | operator 来自固定枚举 |
| AND / OR | children | 子节点至少 2 个 |
| NOT | child | 恰好 1 个子节点 |
| TEXT | text, evidence_refs, reason | 无法可靠结构化的条件 |

ATOM 可选字段为 `unit, method, population, time_window`。`operator` 为 `eq/ne/gt/gte/lt/lte/in/exists`；`value` 支持 str / bool / Decimal / 同类型列表 / null，其中 exists 的 value 为 bool。数值必须使用严格校验，避免字符串和布尔值被隐式转换。

```json
{
  "kind": "AND",
  "children": [
    {"kind": "ATOM", "field": "finding_a", "operator": "eq", "value": true,
     "evidence_refs": [{"span_id": "s_demo", "start": 0, "end": 1, "quote": "A"}]},
    {"kind": "NOT", "child": {"kind": "ATOM", "field": "finding_b",
     "operator": "eq", "value": true,
     "evidence_refs": [{"span_id": "s_demo", "start": 2, "end": 3, "quote": "B"}]}}
  ]
}
```

示例是结构演示，实际引用必须通过来源校验。没有明确条件使用 `condition=null`，与“条件尚未解析”区分。任何 TEXT 节点使 `condition_executable=false`。首期只保存和验证结构，不执行患者适用性判断；未来求值需要 true / false / unknown 三值语义，缺失事实不能等价于否定。

### 3.7 CandidateClause 与 ClauseRevision

抽取输出 `CandidateClause` 保存原始实体提及、predicate、条件、例外、字段级 EvidenceRef 和抽取任务信息，不直接写正式图谱。

规范化后 `ClauseRevision`：

| 字段 | 类型 / 约束 |
|---|---|
| clause_id / revision_id / candidate_id | str |
| subject_entity_id / object_entity_id | str；关系需要的实体均已解析 |
| predicate | supports / weakens / associated_with / distinguishes / limits |
| direction | supporting / weakening / neutral / limiting；按 predicate 校验 |
| diagnostic_level | cognitive_state / syndrome / etiology / biological / pathological / unspecified |
| modality | required / recommended / permitted / prohibited / uncertain / unspecified |
| condition | ConditionNode \| null |
| exceptions | list[ConditionNode] |
| condition_executable | bool；仅表示是否已完整结构化，不表示已完成医学验证 |
| evidence_refs | 非空 list[EvidenceRef] |
| field_evidence | dict[str, list[EvidenceRef]]；覆盖关系、条件、阈值等关键字段 |
| assertion_text | str；规范化表述，不冒充原文 |
| review_status | pending / approved / rejected |
| validation_issues | list[ValidationIssue] |
| claim_group_id | str \| null |
| extraction_run_id | str |

将相关性映射为 `associated_with`，不得改写为因果。`distinguishes` 首期仅用于明确疾病对主张，类型矩阵要求两端均为 disease；其他语境不硬套该关系。没有合适关系类型的候选进入 `unsupported_relation` 队列。

`ValidationIssue`：`code, severity, object_id, field_path?, message, evidence_refs[], resolver?`；severity 为 error / warning / info。缺来源、引用不匹配、非法类型、未解析实体为发布阻断项；语义不确定转人工审核，不用模型自报置信度自动放行。

### 3.8 ReviewDecision、Task 与 Release

`ReviewDecision`：`decision_id, object_type, object_id, expected_revision_id, action, revised_payload?, reviewer_id, reason, decided_at`。action 为 approve / amend / reject。修订必须重跑校验；存在阻断错误时不接受 approve。审核并发时通过 expected_revision_id 阻止覆盖他人修订。

`Task`：`task_id, run_id, stage, input_ids[], cache_key, status, attempt, output_artifacts[], error_code?, error_message?, started_at?, finished_at?`。status 为 pending / running / succeeded / failed / blocked / skipped。人工待审核不是模型失败。

`ReleaseManifest`：`release_id, schema_version, document_ids[], parse_ids[], clause_revision_ids[], chunk_ids[], terminology_version, parser_versions, prompt_hash, extraction_model, embedding_model, embedding_dimension, tokenizer_version, config_hash, artifact_checksums, bm25_path, qdrant_collection, neo4j_release_id, counts, checks, created_at`。

发布状态单独存储为 building / validated / active / retired / failed；不可变 manifest 不随激活状态修改。

## 4. 存储与图谱映射

### 4.1 SQLite 权威记录

开启外键，写入使用事务，保存 schema migration 版本。首期表：

| 表 | 主键与主要关联 |
|---|---|
| documents | document_id，checksum 唯一 |
| parses / blocks / spans | 各自 ID；parse→document，block/span→parse |
| span_locators | span_id + ordinal；关联 block |
| chunks / chunk_spans | chunk_id；中间表关联 span |
| entities / mentions | entity_id / mention_id |
| candidates / clauses / clause_revisions | candidate_id / clause_id / revision_id |
| clause_evidence / clause_entities | revision_id + ordinal；关联 span/entity |
| reviews | decision_id；保留对象修订与操作者 |
| tasks / artifacts | task_id / artifact_id；cache_key 成功结果唯一 |
| releases / release_members | release_id；冻结对象修订清单 |
| active_release | 单行活动版本指针 |

嵌套条件和模型返回可存为经过 schema 验证的 JSON；可查询的 ID、状态和来源关系使用独立列或关联表，避免所有内容只塞入 JSON。JSONL 从权威数据导出并记录校验和；禁止直接修改 JSONL 后绕过导入校验。

### 4.2 Neo4j 投影

节点：Document、SourceSpan、Entity、Clause、Condition。Clause 对应一个 revision。每个发布节点使用 `uid = release_id + ':' + object_id`，设置唯一约束；所有节点和边带 release_id。

| 关系 | 起点 → 终点 |
|---|---|
| PART_OF | SourceSpan → Document |
| SUBJECT / OBJECT | Clause → Entity |
| EVIDENCED_BY | Clause → SourceSpan |
| REQUIRES / HAS_EXCEPTION | Clause → Condition |
| HAS_CHILD | Condition → Condition；保存 ordinal |

predicate、direction、diagnostic_level、modality 放在 Clause 属性中。此图表达与“条款 SUPPORTS 疾病”的语义等价，但使用固定 SUBJECT/OBJECT 结构避免动态关系名扩散。首期不另建无条件 Entity→Entity 临床边。

Neo4j 不支持的嵌套对象不直接写属性：条件递归为节点，完整条件 JSON 可作为辅助字符串保存。使用带 uid 的 MERGE 保证幂等；查询必须携带 release_id。相似主张和冲突候选首期保存在权威记录中，不自动宣布矛盾成立。

## 5. 分步执行与验收

### S01：工程骨架、配置与契约

- 输入：现有 pyproject、本文 schema、项目目录规范。
- 实现：创建模块骨架；配置模型；日志；SQLite migration；CLI；模型与存储适配协议。
- 输出：可导入包、默认配置示例、数据库初始结构、schema 导出。
- 接口：`load_config(path) -> BuildConfig`；`init_store(config) -> Repository`。
- 验收：配置错误明确报错；密钥从环境变量读取；空库可初始化；schema 拒绝非法枚举、非法条件树及多余字段。

### S02：文档导入与快照

- 输入：显式 PDF 路径列表、来源元数据。
- 实现：检查文件可读性、PDF 格式、哈希、快照写入和登记；记录密码保护或损坏错误；不自动扫描全部 paper。
- 输出：Document、source.pdf、登记结果。
- 接口：`register_pdf(path, metadata) -> RegistrationResult`，含 document_id、is_duplicate、issues。
- 验收：重复文件仅一份权威文档；相同名字但不同内容分别登记；原文件未改变；元数据修改有历史。

### S03：版面解析与 OCR 路由

- 输入：Document 快照、ParseConfig。
- 实现：Docling adapter；正文/表格/页码/坐标转换；按可用文本和质量检查决定 OCR；保存解析器及模型版本。
- 输出：ParsedBlock 列表、blocks.jsonl、页面级 issues。
- 接口：`PdfParser.parse(snapshot_path, config) -> ParsedDocument`。
- 验收：中文、英文、双栏、扫描和表格样本逐类核查；bbox 不越界；阅读顺序可检查；失败页面被明确记录。整文档存在未处理页时不可默认标记完整成功。

### S04：清洗与原文片段

- 输入：ParsedBlock 列表。
- 实现：识别重复页眉页脚；规范化空白和断行；恢复章节路径；构建 SourceSpan 与位置映射。清洗不覆盖 raw_text。
- 输出：spans.jsonl、清洗统计和问题列表。
- 接口：`build_spans(parsed_document, config) -> SpanBuildResult`。
- 验收：抽样片段可回到原块；跨页片段返回多个位置；页眉删除不误删正文；OCR 引用标注转录来源。

### S05：父子文本块与抽取上下文

- 输入：SourceSpan、切分规则、tokenizer。
- 实现：条款与表格优先切分；子块关联父块；补充必要表头、脚注和相邻指代上下文；生成独立 retrieval_text。
- 输出：Chunk 列表、chunks.jsonl、待核查上下文问题。
- 接口：`build_chunks(spans, config) -> ChunkBuildResult`；`get_extraction_context(chunk_id) -> ExtractionInput`。
- 验收：每个子块来源非空；条件连接词样本不被无上下文截断；表格行包含表头；超过上限的问题显式入队。

### S06：开发文本索引与基线检索

- 输入：通过解析质量检查的 child chunks；配置允许 pending 的开发模式。
- 实现：中英文分词、医学别名展开、BM25；EmbeddingClient；Qdrant 开发 collection；RRF 融合。
- 输出：开发索引、检索结果、调用统计。
- 接口：`build_text_index(chunks, index_spec) -> TextIndexManifest`；`search_text(request) -> list[SearchHit]`。
- RRF：`score(d)=Σ 1/(k0+rank_i(d))`，排名从 1 开始，默认 k0=60；两路各取 top 20，按 chunk_id 去重，最终默认 top_k=5。参数写入配置。
- 验收：两路使用同一过滤条件；索引文本数和向量数一致；同模型维度正确；已知证据可用于计算 Recall@k；开发结果明确标记非正式发布。

### S07：候选条款抽取

- 输入：ExtractionInput，包含原文片段 ID、文本、必要上下文、允许实体类型、关系和输出 schema。
- 实现：可版本化提示词；结构化模型返回；JSON 与 EvidenceRef 校验；缓存；记录原始响应和用量。PDF 内文字作为数据，不执行其中的指令。
- 输出：CandidateClause 列表，允许合法空列表；每个候选附模型/提示词/输入指纹。
- 接口：`extract(context, schema, model_config) -> ExtractionResult`。
- 重试：网络或限流最多 3 次并退避；结构错误最多一次格式修复；不得通过重复调用掩盖长期失败。每次尝试单独记账。
- 验收：空结果与失败可区分；原文没有的字段被标记；引用偏移可检查；断点续跑不重复调用已成功任务。

### S08：实体归一与条件规范化

- 输入：候选条款、本地术语表。
- 实现：规范名/别名精确匹配；类型约束；歧义候选；新本地实体；条件树结构、数值单位与否定范围检查。
- 输出：Entity、EntityMention、pending ClauseRevision、链接问题。
- 接口：`normalize_candidates(candidates, terminology) -> NormalizationResult`。
- 验收：确认的中英文别名映射到同一 entity_id；相似但不同概念不强制合并；未解析条件为 TEXT；不凭空添加外部本体 ID。

### S09：一致性校验与相似主张分组

- 输入：pending ClauseRevision 及来源记录。
- 实现：来源与字段证据校验；主体/客体类型矩阵；数值、单位、语气和否定检查；同任务精确去重；跨来源相似主张分组。
- 输出：ValidationReport、审核队列、claim_group_id 与潜在冲突标记。
- 接口：`validate_clause(revision, repository) -> ValidationReport`。
- 验收：引用不匹配、缺实体、错误单位的构造样例被拦截；相同实体不同条件不被删除；不将检查方法不同直接标成医学冲突。

### S10：文本与条款审核

- 输入：审核队列、带页面定位的证据、当前修订。
- 实现：导出 JSONL/Markdown 审核包；导入结构化决定；校验 reviewer_id；修改生成新 revision；同时提供文本块或文档范围的明确文本批准操作。
- 输出：ReviewDecision、已批准条款修订、文本审核记录、未决队列。
- 接口：`export_review_batch(ids) -> ReviewBatch`；`apply_decisions(decisions) -> ReviewApplyResult`。
- 文本批量批准只表示该范围可进入正式文本检索，不自动批准任何图谱条款；父块上下文也必须处于批准范围。
- 验收：pending/rejected 不进入正式图谱；无来源错误不能被 approve 绕过；旧修订审核冲突报错；修改后的证据重验。

### S11：双库构建与统一发布

- 输入：经过验证的来源集合、approved 文本块、approved 条款修订、术语版本、模型配置。
- 实现：冻结 release_members；生成发布快照；构建 BM25、独立 Qdrant collection、独立 release_id 图投影；交叉一致性校验；原子切换 SQLite active_release。
- 输出：ReleaseManifest、发布图与索引、checks.json。
- 接口：`build_release(selection) -> ReleaseBuildResult`；`activate_release(release_id) -> ActivationResult`。
- 必须校验：条款引用的来源存在；每条已发布条款至少关联一个已发布文本块且证据片段可读取；所有实体可解析；索引数量/模型维度一致；图与文本 release_id 相同；快照校验和有效。
- 失败策略：任何检查失败均不激活；候选外部数据保留为 failed 供排障。服务每次请求只解析一次活动版本，整次请求使用该版本；旧版本保留到显式回收。
- 验收：注入 Qdrant/Neo4j 写失败时旧版本不变；重复构建无重复节点；可切回上一有效版本；不会读到半发布数据。

### S12：统一查询、评估与运行说明

- 输入：有效 release_id、查询文本/实体 ID、测试标注集。
- 实现：混合检索、图谱扩展、来源查询、证据包；端到端评估；记录运行命令和环境锁定信息。
- 输出：SearchResponse、EvidencePackage、metrics.json、运行说明文档。
- 接口见第 6 节。
- 验收：单文档全链路完成，再通过 3～5 份中英文 PDF 回归；统计正确引用率、抽取质量和检索召回；无标注项写 not_evaluated，不伪造精度。

## 6. 外部接口契约

### 6.1 CLI 设计

下列是计划实现的命令，当前尚不可执行：

```powershell
python -m neurogra.knowledge.cli ingest --pdf "paper/示例.pdf" --metadata "data/knowledge/import.json"
python -m neurogra.knowledge.cli build --document-id <ID> --through chunks
python -m neurogra.knowledge.cli build --document-id <ID> --through candidates
python -m neurogra.knowledge.cli review-export --document-id <ID>
python -m neurogra.knowledge.cli review-import --file <decisions.jsonl>
python -m neurogra.knowledge.cli release-build --selection <selection.json>
python -m neurogra.knowledge.cli release-activate --release-id <ID>
python -m neurogra.knowledge.cli search --query "查询问题" --top-k 5
python -m neurogra.knowledge.cli graph --entity-id <ID> --limit 20
python -m neurogra.knowledge.cli resume --run-id <ID>
python -m neurogra.knowledge.cli evaluate --dataset <annotations.jsonl> --release-id <ID>
```

`import.json` 包含 source_role、title、language 和可选来源字段；`selection.json` 显式指定 document_ids、批准修订集合或审核快照、术语版本和构建配置。参数解析后再次校验审核资格，不能通过手写 selection 绕过审核。

退出码：0 成功；2 参数/配置错误；3 依赖或外部服务失败；4 数据校验失败；5 等待审核或未满足发布条件。输出 JSON 包含 run_id、status、artifacts、issues；人类摘要写 stderr 或单独展示，便于脚本处理。

### 6.2 Python 服务

```python
search(request: SearchRequest) -> SearchResponse
get_entity_clauses(entity_id: str, release_id: str, limit: int = 20) -> list[ClauseView]
get_evidence(clause_id: str, release_id: str) -> EvidencePackage
get_source(span_id: str, release_id: str) -> SourceView
```

`SearchRequest`：`query, top_k=5, release_id?, source_roles?, document_ids?, mode='hybrid'`。top_k 限制 1～100；默认 source_roles 为临床指南和临床研究；默认只读正式活动版本。开发检索使用单独 CLI/API 入口，不以布尔参数悄悄放宽正式审核过滤。

`SearchResponse`：`query, release_id, hits[], degraded, warnings[], timing_ms`。

`SearchHit`：`chunk_id, score, matched_by[], original_text, retrieval_text, context_text, citations[], clause_ids[], source_role`。融合分数是排序依据，不表示医学可信度。

`EvidencePackage`：`release_id, clause_id, revision_id, assertion_text, subject, predicate, object, condition, exceptions, modality, diagnostic_level, citations[], original_context, review_status`。

图扩展限定为“命中的条款及其实体关联的一跳条款”，默认最多 20 条，并保留已审核、同发布版本过滤。第一版不进行无限遍历或自动诊断推理。

可选单路故障降级必须配置启用：若向量服务失效而 BM25 可用，响应设置 degraded=true 并说明失效通道；不能伪装成完整混合检索。证据或版本完整性失败时禁止降级返回不完整图谱知识。

## 7. 配置、缓存与失败恢复

配置至少包含：

```yaml
schema_version: "1"
paths:
  data_root: data/knowledge
  output_root: output/knowledge
parsing:
  adapter: docling
  ocr_policy: auto
chunking:
  target_tokens: 450
  max_tokens: 800
  preserve_clause_context: true
extraction:
  provider: null         # 实施时填写，不填则禁止抽取阶段
  model: null
  prompt_version: "v1"
  max_transport_attempts: 3
embedding:
  provider: null
  model: null
  dimension: null
retrieval:
  lexical_top_k: 20
  vector_top_k: 20
  rrf_k: 60
  allow_degraded: false
review:
  require_clause_approval: true
```

分词器、模型上下文上限、批大小、数据库连接、向量距离函数和术语版本在实际 BuildConfig 中补齐必需项。密钥只使用环境变量引用，不进入配置指纹或日志。指纹保存影响结果的非敏感配置。

缓存键包含阶段名称、输入内容哈希、schema、实现版本、提示词、模型、相关参数与术语版本。LLM 请求设置固定参数仍不保证完全确定性，因此保存实际成功响应；显式重新抽取创建新 extraction_run，不覆盖旧结果。

阶段输出先写临时文件，校验后改名并提交 artifacts 记录。启动恢复时核对 running 任务的产物与事务状态，不根据文件存在就认定成功。模型并发和速率限制可配置；首期采用单构建写入者，查询可读已发布快照。

| 变更 | 重跑范围 |
|---|---|
| 新增 PDF | 该文档全部必要阶段 |
| 同 PDF 同配置 | 复用成功产物 |
| 解析/OCR 配置变化 | parse 及所有下游 |
| 切分变化 | spans/chunks 及受影响抽取和索引 |
| 提示词或抽取模型变化 | 抽取及其下游；旧审核结果不自动迁移 |
| 术语表变化 | 实体归一、校验、必要审核和图/文本检索视图 |
| 仅嵌入模型变化 | 向量重建、新发布；无需重做 PDF 与条款抽取 |
| 人工修订 | 新 revision、校验、审核、新发布 |

## 8. 测试与验收清单

| 测试层 | 必测行为 |
|---|---|
| schema | 条件树、严格值类型、非法关系、空证据 |
| 来源 | Unicode 偏移、跨页、规范化映射、OCR 标记 |
| 切分 | 否定/条件不断义、表头脚注保留、超长上下文 |
| 抽取 | 合法空结果、畸形 JSON、错误引用、超时重试 |
| 归一 | 别名复用、歧义拒识、同名不同类型 |
| 审核 | 未审阻断、修订校验、乐观锁、记录可追踪 |
| 检索 | 中英文分词、过滤一致、RRF 去重、来源返回 |
| 图谱 | 幂等写入、跨版本隔离、条件树节点完整 |
| 发布 | 部分失败不激活、原子切换、旧版本回滚 |
| 续跑 | 重复导入、缓存失效、中断恢复 |

使用固定小型合成 PDF 和模型响应 fixture 验证工程行为，真实样本用于解析与医学抽取质量评价。模型 mock 测试通过不能替代真实抽取评价。

标注集记录 `document_id, span_ids, expected_entities, expected_relations, expected_condition, expected_citations`；检索标注记录 `query_id, query, relevant_span_ids`。开发与独立评估按文档隔离；跨块重复命中的相同证据按 span 去重后计算召回。

指标输出同时给出样本数、分母、错误例子和未评估项。工程门槛见总体方案；医学语义准确率和召回目标在首批基线之后写入验收配置，并注明尚未达标项。

## 9. 里程碑交付与完成定义

| 里程碑 | 对应步骤 | 必须可展示的内容 |
|---|---|---|
| M1 结构化底稿 | S01～S05 | 一份 PDF、原文块、位置映射、父子块和解析问题 |
| M2 文本检索基线 | S06 | 查询命中原文、页码与上下文；开发标识清晰 |
| M3 候选知识与审核 | S07～S10 | 抽取 JSON、实体归一、条件树、审核前后差异 |
| M4 可发布双库 | S11 | 同版文本索引、图谱、manifest 与通过的完整性检查 |
| M5 模块验收 | S12 | 统一查询、真实样本指标、失败恢复、运行说明 |

模块完成意味着：代码与依赖可安装；输入一份已登记 PDF 可以得到可审核底稿；审核后可以发布并查询双库；每条正式条款能回到原文；重复执行和失败恢复经过验证；有真实样本评价和明确局限。仅生成图谱图片或向数据库写入若干节点不视为完成。

实施启动时先确认可用模型服务与本地资源，使用试点 PDF 测量解析结果，再锁定依赖和模型配置。完整网页界面、外部本体扩展和复杂条件求值在此模块验收后另立迭代。
