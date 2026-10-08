# 算法零：医学文本到 RAG 知识库与条件化知识图谱的构建

总体入口：[总体设计](总体设计.md)。首期工程按总体设计第 4.7 节及附录 B 执行：按变更文档增量处理，所有正式医学条款人工审核；本文的章节级差分与自动术语扩展为后续能力。

**2026-10-03 文献批次的实施入口：第 12 节。** 本批次采用 [知识与来源分层图谱结构设计 v0.3](知识图谱设计/NeuroGRA_知识与来源分层图谱结构设计_v0.3.md) 的 17 类核心节点及注册关系。第 2—11 节中的 Clause、七类节点、旧谓词和接口是早期方案描述，不是本批次的新数据契约；落实代码时须按第 12 节迁移。本文新增的是抽取设计、配置和待审核示例，尚未完成全量医学抽取或 v0.3 代码实现。

> 本文解决“收集到指南、共识和论文后，如何把文本转换为可检索、可计算、可追踪的知识底座”。它是 NeuroGRA 的离线知识建设算法，为[统一检索算法](知识图谱设计/NeuroGRA_多模态证据检索与缺口补偿算法设计_v1.0.md)与[临床复查流程](NeuroGRA主Agent与子Agent协作详细设计.md)提供输入。
> 该算法可作为论文系统实现中的基础方法，不宜在未进行独立评价前直接宣称为第三项创新。

## 1. 收集到的资料是否都是文本

主体确实是文本，但不全是连续自然语言。实际输入至少包括六种形态：

| 输入形态 | 例子 | 主要处理难点 |
|---|---|---|
| 正文段落 | 指南、共识、论文正文 | 条件、否定和上下文可能跨句 |
| 编号条款 | 诊断标准、纳入与排除条件 | AND、OR 和层级关系不能丢失 |
| 表格 | 诊断等级、标志物解释、鉴别表 | 行列标题决定单元格含义 |
| 流程图 | 诊断路径和分期流程 | 图形关系需要人工复核后结构化 |
| 元数据 | 标题、机构、日期、版本、DOI | 决定来源等级、有效性和引用定位 |
| 外部结构化资源 | HPO、Mondo、UMLS、药物或基因数据库 | 标识体系、映射和许可不同 |

因此，系统不能采用“PDF 转文本—固定长度切块—向量化”的单一路径。固定切块可能把“仅当”“至少满足”“除外”“不能用于”等关键限制与结论拆开，最终使 RAG 检索到一个看似正确、实际缺少前提的半条款。

## 2. 构建目标

离线构建过程需要同时生成两个相互对齐的成果。

### 2.1 RAG 文本知识库

保存适合全文检索和语义检索的证据文本，包括：

- 原文片段；
- 完整条款及必要上下文；
- 规范化摘要；
- 标题、章节、页码、表格编号；
- 来源、版本、许可和审核状态；
- 关联的实体、条件及条款 ID。

RAG 库负责回答：“原文说了什么，在哪里说的？”

### 2.2 条件化医学知识图谱

保存可用于关系扩展和条件判断的结构化知识，包括：

- 疾病、综合征、表型、检查、标志物等实体；
- 条款、条件、例外、诊断层级和证据方向；
- 条款与原文片段、来源文档和版本的连接；
- 疾病候选之间经过审核的鉴别关系。

知识图谱负责回答：“哪些概念相关、关系在什么条件下成立、依据来自哪里？”

### 2.3 双库对齐

文本库和图谱不是两套独立知识。两者以 `clause_id` 和 `span_id` 对齐：

```text
图谱条款 clause_id
   ├─ 指向原文 span_id
   ├─ 指向条件 condition_id
   ├─ 指向疾病/表现/检查 entity_id
   └─ 指向来源 document_id 和 version_id

文本证据块 chunk_id
   ├─ 包含一个或多个 span_id
   ├─ 关联 clause_id
   └─ 保留 document_id、页码和章节路径
```

检索阶段可以先用文本提高召回率，再沿图谱补齐条件和例外；也可以先从疾病或表型节点沿图召回条款，再返回原文用于引用。

## 3. 总体构建架构

```mermaid
flowchart LR
    A[指南、共识、论文、网页、本体] --> B[来源登记与版本控制]
    B --> C[版面解析与结构恢复]
    C --> D[语义单元切分]
    D --> E[候选条款抽取]
    E --> F[实体规范化与条件解析]
    F --> G[关系构建和来源绑定]
    G --> H[一致性检查与人工审核]
    H --> I[文本索引]
    H --> J[条件化知识图谱]
    I --> K[统一知识服务]
    J --> K
    K --> L[算法一：鉴别证据检索]
    L --> M[算法二：证据状态复核]
```

构建过程分为自动处理、规则校验和人工审核三层。大模型可以辅助抽取候选知识，但不能直接发布为有效医学规则。

## 4. 统一数据模型

### 4.1 文档对象

```text
Document = {
  document_id,
  title,
  document_type,
  issuing_organization,
  publication_date,
  effective_date,
  version,
  language,
  doi_or_url,
  license,
  source_level,
  supersedes,
  checksum,
  review_status
}
```

`checksum` 用于判断文件是否发生变化；`supersedes` 用于连接新旧标准；`review_status` 用于阻止未审核文档进入正式知识版本。

### 4.2 原文片段对象

```text
SourceSpan = {
  span_id,
  document_id,
  section_path,
  page_or_anchor,
  block_type,
  original_text,
  normalized_text,
  previous_span_id,
  next_span_id,
  extraction_method,
  extraction_quality
}
```

`block_type` 可以是段落、编号条款、表格行、脚注或图注。原文必须保留，规范化文本只能用于检索，不能替换引用原文。

### 4.3 条款对象

一条条款表示一个可以独立核验的医学主张。

```text
Clause = {
  clause_id,
  subject_id,
  predicate,
  object_id,
  direction,
  diagnostic_level,
  modality,
  condition_expression,
  exception_ids,
  source_span_ids,
  source_level,
  extraction_confidence,
  review_status,
  valid_from,
  valid_to
}
```

重要字段含义：

- `direction`：支持、削弱、排除限制、相关或安全警示；
- `diagnostic_level`：认知状态、临床综合征、病因、生物学状态或病理确诊；
- `modality`：必须、可以、建议、不应、证据不足等语气强度；
- `condition_expression`：条款成立所需条件；
- `source_span_ids`：支持该条款的精确原文位置。

### 4.4 条件表达式

条件采用抽象语法树，不保存为一段无法执行的文字：

```text
AND(
  age >= 60,
  duration >= 6 months,
  OR(test_method = method_A, test_method = method_B),
  NOT(acute_delirium = true)
)
```

最小条件项结构为：

```text
ConditionAtom = {
  field,
  operator,
  value,
  unit,
  method,
  time_window,
  population,
  negated,
  source_span_id
}
```

当原文条件无法可靠转换为可执行表达式时，保留 `free_text_condition` 并进入人工审核队列，不能由模型猜测阈值或运算符。

## 5. 构建算法的详细步骤

### 5.1 步骤 1：来源登记和准入判断

输入一份文档后，先计算文件哈希并登记来源。准入检查包括：

1. 是否属于预先定义的病种和知识范围；
2. 是否具有合法的保存、解析和使用权限；
3. 是否能确定标题、机构、日期和版本；
4. 是否已经存在相同文件或更高版本；
5. 是医学依据，还是只描述算法或提供背景；
6. 是否允许进入正式知识版本。

来源分数只用于安排审核优先级。可定义：

```text
S_source = w1 * authority
         + w2 * recency
         + w3 * traceability
         + w4 * scope_match
         + w5 * license_clarity
```

各项归一化到 0 至 1，权重之和为 1。正式诊断标准通常具有较高 `authority`，但旧标准的 `recency` 较低；一篇新论文即使很新，也不能仅凭时间替代正式标准。

输出为已登记的 `Document`。来源不清、授权不明或无法定位版本的文档进入隔离区，不参与正式检索。

### 5.2 步骤 2：版面解析和结构恢复

按文件类型选择解析方式：

- 带文本层 PDF：抽取字符、页码、坐标和字体层级；
- 扫描 PDF：OCR 后保存置信度和页面图像坐标；
- HTML：保留标题层级、列表、表格和锚点；
- Word：保留标题、编号、批注和表格结构；
- 本体文件：直接解析 OWL、OBO、JSON 或 CSV。

版面恢复的目标不是得到一串文字，而是恢复：

```text
文档 → 章节 → 小节 → 段落/列表 → 条款 → 表格行/脚注
```

对表格，先建立带表头的完整陈述。例如表格单元格“支持性”必须与行名“某影像表现”和列名“DLB 临床诊断”组合后才有意义。OCR 低置信度的数值、单位、否定词和比较符号必须进入人工核对队列。

### 5.3 步骤 3：基于条款边界的语义切分

切分优先级如下：

1. 诊断标准编号和列表层级；
2. 表格行及其行列标题；
3. 含条件连接词的完整句群；
4. 普通段落；
5. 最后才使用长度窗口。

切分边界检测使用规则与模型联合完成。若当前句包含下列标记，则向前或向后扩展上下文：

```text
条件词：若、当、仅当、在……情况下、适用于
逻辑词：并且、至少、任一、或者、除外
限制词：不能、不足以、尚不支持、需谨慎
指代词：上述、该类、前者、后者、这些患者
```

定义候选片段 `s` 的条件完整度：

```text
C_complete(s) = 1 - unresolved_reference_count / reference_count
```

若不存在条件或指代，完整度记为 1。若完整度低于阈值，则合并相邻父级条款、表头或前后句，直到指代得到解析或达到最大上下文范围。达到上限后仍不完整的片段可以进入普通全文索引，但不能发布为可执行条款。

### 5.4 步骤 4：候选医学条款抽取

对每个语义片段提取候选条款。提示模型只输出符合固定 schema 的 JSON，并要求每个字段返回对应原文字符范围。抽取内容包括：

- 主体、关系和客体；
- 支持或削弱方向；
- 诊断层级；
- 条件逻辑和例外；
- 数值、单位、检测方法和时间窗；
- 语气强度；
- 原文依据。

一段文本可能产生多条条款。例如“存在 A 或 B，并排除 C 时支持疾病 D”应拆成一个带嵌套逻辑条件的条款，而不是生成三条无条件边。

抽取后立即执行三类约束：

1. **原文蕴含约束**：规范化主张的所有关键字段都必须在原文或明确表头中找到；
2. **范围约束**：模型不能补入原文未出现的人群、阈值或因果关系；
3. **方向约束**：“不支持排除”不能改写为“支持”，“相关”不能改写为“导致”。

若模型生成字段无法映射到原文字符范围，该字段标记为 `unsupported`，整条知识不能自动发布。

### 5.5 步骤 5：实体识别、规范化和链接

实体链接分成候选生成和候选消歧两步。

### 候选生成

依次利用：

- 精确词典和同义词；
- 中文、英文及缩写映射；
- HPO、Mondo、UMLS 等交叉映射；
- 字符相似和向量近邻；
- 上下位词扩展。

### 候选消歧

对提及 `m` 和候选概念 `e` 计算：

```text
S_link(m,e) = a1 * lexical_match
            + a2 * context_similarity
            + a3 * type_compatibility
            + a4 * neighborhood_consistency
            + a5 * source_prior
```

其中：

- `lexical_match`：名称和同义词匹配；
- `context_similarity`：当前段落与概念定义的语义相似；
- `type_compatibility`：模型预测类型与候选类型是否一致；
- `neighborhood_consistency`：候选与同句其他实体在已有图中是否合理连接；
- `source_prior`：项目首期核心词表中的优先级。

如果第一名和第二名分数过近，或最高分低于阈值，则保留多个候选并转人工审核。系统必须允许建立本地概念 ID，因为中文临床表达未必都能准确映射到外部本体。

### 5.6 步骤 6：条件逻辑解析

将自然语言条件转换为条件树，执行以下过程：

1. 识别逻辑连接词和作用范围；
2. 提取条件项的字段、运算符、值和单位；
3. 识别否定及否定范围；
4. 识别时点、持续时间和事件先后；
5. 识别人群、检查方法和样本类型；
6. 连接例外或排除条款；
7. 生成条件表达式并反向生成自然语言；
8. 将反向文本与原文并列交给审核者核对。

条件树必须保留 AND 与 OR。例如“满足 A，且 B、C 中至少一项”应表示为：

```text
AND(A, OR(B, C))
```

不能扁平化为 `A、B、C` 三条独立关系。涉及“至少两项”“持续超过某时间”“症状早于某事件”等条件时，分别使用计数、时间和顺序运算符。

### 5.7 步骤 7：关系构建与来源绑定

普通知识图谱常直接保存：

```text
某表现 --supports--> 某疾病
```

NeuroGRA 应将关系提升为条款节点：

```text
Clause_C1 --ABOUT--> Phenotype_P1
Clause_C1 --SUPPORTS--> Disease_D1
Clause_C1 --REQUIRES--> ConditionGroup_G1
Clause_C1 --HAS_EXCEPTION--> Condition_E1
Clause_C1 --AT_LEVEL--> EtiologyLevel
Clause_C1 --EVIDENCED_BY--> SourceSpan_S1
SourceSpan_S1 --PART_OF--> Document_V3
```

这样才能让关系携带条件、例外、诊断层级和出处。图中每一条临床意义边必须能够回到 `Clause`，再回到原文；只有术语层级和同义词等本体关系可以直接连接实体。

### 5.8 步骤 8：完整性、一致性和可信度检查

每条候选条款执行以下自动检查：

| 检查项 | 失败示例 | 处理 |
|---|---|---|
| 来源可定位 | 找不到页码或段落 | 不发布 |
| 字段有原文支持 | 模型生成原文没有的阈值 | 拒绝并记录 |
| 条件完整 | 只有结论，没有“仅适用于”条件 | 补取上下文或人工审核 |
| 单位完整 | 数值存在但单位丢失 | 人工核对 |
| 逻辑完整 | OR 被解析为 AND | 人工核对 |
| 类型一致 | 将量表当作疾病 | 自动阻断 |
| 层级一致 | 临床表型直接写成病理确诊 | 降级或拒绝 |
| 版本有效 | 已被新版标准替代 | 标记历史版本 |
| 关系方向一致 | “不能排除”抽成“排除” | 自动阻断 |

候选条款质量分数可定义为：

```text
Q(c) = b1 * source_quality
     + b2 * extraction_support
     + b3 * condition_completeness
     + b4 * terminology_confidence
     + b5 * provenance_completeness
     + b6 * consistency_score
```

该分数只决定审核优先级和是否允许进入候选区，不表示医学结论为真的概率。含诊断阈值、排除规则、药物禁忌和病因方向的条款，无论分数多高都应进行人工审核。

### 5.9 步骤 9：去重、冲突和版本处理

### 去重

只有在主体、关系、客体、条件、诊断层级和来源语境均相同或等价时，才视为同一主张。来源不同的相同主张可合并为一个概念条款组，但必须保留所有出处，不能把来源数量直接当作证据强度。

### 冲突

两条知识只有满足以下条件才进入冲突比较：

- 主体、客体和判断层级相同；
- 人群、检查方法和时间范围可比；
- 方向或阈值确实不一致。

若适用人群或检测平台不同，应表示为条件不同，而非冲突。

### 版本

新版指南替代旧版时：

```text
Document_new --SUPERSEDES--> Document_old
Clause_new --REVISES--> Clause_old
Clause_old.status = superseded
```

旧知识不删除，用于复现实验和解释历史病例；在线系统默认检索有效版本，除非查询明确指定历史时点。

### 5.10 步骤 10：人工审核和主动抽样

人工审核界面应同时显示：

- 原文页面或网页上下文；
- 模型抽取的结构化条款；
- 条件树和反向生成文本；
- 实体链接候选；
- 新旧版本或潜在冲突；
- 通过、修改、拒绝及原因。

审核优先级不是随机排列，而是：

```text
Priority(c) = risk(c) * uncertainty(c) * expected_use(c)
```

`risk` 对排除规则、诊断阈值和安全知识赋高值；`uncertainty` 来自低抽取置信度、链接歧义和规则失败；`expected_use` 来自该条款在四组重点鉴别中的预计使用频率。

系统可定期抽查已经自动通过的低风险条款。如果抽查错误率超过预设阈值，应暂停该文档批次发布并重新审核，而不是只修正抽到的个别条款。

### 5.11 步骤 11：生成 RAG 文本索引

审核后的条款产生三种文本视图：

1. **原文视图**：保留完整原文，用于引用和核查；
2. **检索视图**：加入规范化术语、缩写展开和章节标题，提高召回；
3. **证据包视图**：条款、条件、例外和来源组合后的完整上下文。

建立两类索引：

- BM25 倒排索引，适合疾病名、量表名、基因名和精确术语；
- 向量索引，适合临床同义表达和自然语言问题。

向量嵌入使用“检索视图”，回答引用必须返回“原文视图”。不能把模型生成的规范化摘要伪装成原文。

### 5.12 步骤 12：发布知识图谱和一致版本

发布时生成一个不可变的 `knowledge_release_id`，其中记录：

```text
knowledge_release_id,
document_versions,
ontology_versions,
clause_count,
reviewed_clause_count,
rejected_clause_count,
text_index_version,
graph_version,
embedding_model_version,
build_time,
reviewer_set
```

文本索引和图谱必须来自同一次发布。如果更新了图谱但没有更新文本索引，或者重新计算了向量却没有记录模型版本，实验结果将无法复现。

## 6. 构建算法伪代码

```text
输入：文档集合 D，术语集合 O，项目 schema S
输出：文本索引 I，条件化知识图谱 G，构建报告 R

初始化 I、G、审核队列 H

for each document d in D:
    meta = register_source(d)
    if not admissible(meta):
        quarantine(d)
        continue

    blocks = parse_layout(d)
    spans = semantic_segment(blocks)

    for each span s in spans:
        candidates = extract_clause_candidates(s, S)

        for each candidate c in candidates:
            c.entities = link_entities(c.mentions, O, G)
            c.condition = parse_condition_tree(c, s)
            c.provenance = bind_source_spans(c, s)

            checks = validate(c, s, meta, S)
            c.quality = score_quality(c, checks)

            if checks.has_hard_error:
                reject_or_review(c, H)
            else if requires_clinical_review(c):
                enqueue_by_priority(c, H)
            else:
                stage(c)

review(H)
resolve_duplicates_conflicts_versions()

for each approved clause c:
    write_clause_graph(c, G)
    write_text_views(c, I)

validate_cross_store_alignment(I, G)
freeze_release(I, G)
generate_build_report(R)
```

## 7. 增量更新算法

知识库建成后不能每次全部重建。新文档或新版指南进入时执行：

1. 根据 URL、DOI、标题和文件哈希判断新增、重复或修订；
2. 只解析发生变化的章节；
3. 将新候选条款与旧条款按实体、关系、条件和原文相似度匹配；
4. 分类为新增、等价、修订、删除或无法判断；
5. 对修订和删除项进行人工审核；
6. 建立 `REVISES`、`SUPERSEDES` 或 `RETRACTS` 关系；
7. 重建受影响的文本块、向量和图邻域；
8. 生成新的不可变发布版本。

增量匹配分数可写为：

```text
S_update(c_new,c_old) = g1 * entity_overlap
                      + g2 * relation_match
                      + g3 * condition_similarity
                      + g4 * text_similarity
                      + g5 * source_lineage
```

高相似但条件或阈值变化的条款必须标记为“可能修订”，不能作为普通重复被删除。

## 8. 如何评价这套构建算法

构建算法需要单独评价，否则后续 RAG 效果不好时无法判断是检索算法错误还是知识底座错误。

### 8.1 建议评价集

从核心诊断标准中分层抽取 200 至 500 个条款，人工标注：

- 原文边界；
- 主体、关系、客体；
- 条件和逻辑结构；
- 例外、否定、阈值、单位及方法；
- 诊断层级和证据方向；
- 术语标准 ID；
- 来源位置和版本关系。

训练/开发和测试应按文档或条款组隔离。不能把同一指南中高度相似的上下条款分别放入开发集和测试集。

### 8.2 指标

| 评价环节 | 指标 |
|---|---|
| 文档解析 | 字符准确率、标题层级准确率、表格结构恢复率 |
| 条款边界 | Boundary Precision、Recall、F1 |
| 实体抽取 | 严格匹配和宽松匹配 F1 |
| 实体链接 | Accuracy、Top-k Recall、拒识准确率 |
| 关系抽取 | 按关系类型的 Precision、Recall、F1 |
| 条件解析 | 条件项 F1、AND/OR/NOT 结构完全匹配率 |
| 来源绑定 | 可定位引用率、错误页码率 |
| 完整性 | 条件完整条款率、例外保留率、单位保留率 |
| 图谱质量 | 类型约束违规率、重复率、伪冲突率 |
| 人工成本 | 每百条审核时间、自动通过率、返工率 |

其中“条件树完全匹配率”和“可定位引用率”应作为核心指标，因为它们直接决定算法一能否判断条款适用性，以及算法二能否核查引用。

### 8.3 必要对照

至少比较：

1. 固定长度切块与条款感知切分；
2. 只抽主体—关系—客体与加入条件树；
3. 纯 LLM 抽取与规则校验加人工审核；
4. 仅文本库与文本—图谱双库对齐。

如果双库方案效果提高，应进一步确认收益来自结构关系，而不是因为双库方案人工提供了更多信息。对照组需要使用相同原文和相同审核条款。

## 9. 与两个核心算法的接口

### 向算法一提供

```text
approved Clause
ConditionTree
Entity IDs and aliases
SourceSpan and document version
candidate differential links
BM25 index and vector index
knowledge_release_id
```

算法一据此生成候选病因对查询，联合召回文本和图谱条款，并构造条件完整的证据包。

### 向算法二提供

```text
条款的有效版本和审核状态
条件字段及可满足状态
例外和限制
原文定位
新旧版本及冲突关系
```

算法二据此判断问题属于知识缺口、患者资料缺失、证据冲突还是版本错误，并选择相应复核动作。

## 10. 在论文中的定位建议

这套算法建议写入第五章“系统设计”的离线知识建设部分，并在第三章方法开始前说明知识输入的形成过程。论文的两项主要创新仍保持为：

1. 面向候选病因对的条件感知鉴别证据检索；
2. 证据状态引导的有界复核。

离线构建算法为两者提供可靠的数据基础。若后续完成了独立标注集、系统对照实验，并证明条件化抽取显著改善了条款完整性或下游检索，才适合将其提升为独立研究贡献。

## 11. 最小可实现版本

硕士论文首期可以按以下规模实施：

- 20 至 40 份核心指南、标准和共识；
- 500 至 1500 个经过审核的条款；
- 100 至 300 个重点鉴别证据包；
- 疾病、综合征、表型、检查、标志物、条款和来源七类核心节点；
- `SUPPORTS`、`WEAKENS`、`REQUIRES`、`HAS_EXCEPTION`、`EVIDENCED_BY`、`DISTINGUISHES` 六类核心临床关系；
- BM25、向量索引和一个属性图数据库；
- 文本与图谱共用 `clause_id`、`span_id` 和 `knowledge_release_id`；
- 所有核心诊断条款经过人工审核。

这一规模足以验证你的关键研究问题，也能控制知识审核工作量。第一版不需要把所有论文都抽成图谱，更不需要把整个生物医学领域复制进本地图数据库。

## 12. 本批神经退行性疾病文献的抽取实施设计

### 12.1 输入范围、核查结果和目标

输入是 `docs/references/neurodegenerative_diagnosis_20261003/` 中的 14 份 PDF、来源清单及 `docs/reviews/` 中的中文入门综述 Markdown/Word。14 份 PDF 共 **270 页**；本次逐份核对实际页数、SHA-256 与清单，均一致；逐页文本预检没有空文本页。没有空页只说明存在文本层，不证明双栏顺序、表格和符号被正确解析。本次阅读了综述并核对各框架的关键条款；对诊断表、脚注和框注进行了选页渲染检查，未把这项工作称为全部 270 页的医学审核。

目标是从这些资料建立“诊断框架、条款定义、组合规则、检查用途、分期和可追溯原文”的图谱，为后续鉴别证据检索提供完整约束。先回答这些能力问题，再决定抽取范围：

1. 某版标准的某诊断类别需要哪些前提、核心项、支持项、排除项，如何组合？
2. 一个临床表现在哪些框架中承担什么角色？条目的时间、部位、计数与操作定义是什么？
3. 某检查测什么、可用于哪个目的、适用于什么人群和场景、有什么限制？
4. 疾病身份、症候群、临床严重程度和生物学分类分别由哪些来源定义？
5. 某个节点字段或关系的依据位于哪个冻结文档、哪页、哪个表格/脚注、哪段原文？

本批次先集中抽取诊断内容、诊断相关鉴别与方法限制。治疗管理、患病率、历史背景和机制讨论可以保留在原文索引中；只有属于上述问题且有明确来源时才进入本期正式图谱。原文流程图也要列入扫描范围；不能仅因文字抽取困难就当作无内容。图形语义无法可靠恢复时登记人工转录任务。

配套文件：

- [诊断文献抽取配置](知识图谱设计/诊断文献抽取配置_2026-10-03.json)：逐文献路由、页码锚点、重点依赖、检查要求；它是设计配置，尚未接入 CLI。
- [待审核抽取样例](../data/knowledge/examples/诊断文献抽取样例_2026-10-03.json)：用本地 PDF 真实引文示范字段、条件和偏移；是中间记录示意，不能直接导入正式图谱。
- `output/knowledge_design/diagnostic_extraction_20261003/`：本次页级预检、选页文本和版面核查输出；不是已发布知识库。

### 12.2 每篇文献采用什么抽取模板

下表页码均为 **PDF 阅读器从 1 开始的页序**，不是期刊印刷页码。它们是优先抽取锚点；每篇仍需扫描全文，补齐锚点引用的定义、例外、图注、脚注和附录。表格范围只表示已核对或应优先核对的起点，不表示其余页可直接跳过。

| 清单 ID / 文献 | 抽取模板与主要输出 | 优先锚点 | 必须处理的差异 |
|---|---|---|---|
| 01 AD 2024 诊断与分期（27 页） | 框架、病理/标志物分类、检查用途、多个分期轴 | p3 Box 1；p5 Table 2；p6 Box 2；p15 Table 6 | AD 生物学定义、临床表现和临床阶段分别建模；保留检测性能及适用限制 |
| 02 AD 2025 血液标志物指南（17 页） | 推荐与 DiagnosticUse；性能准入；限制；原文 GRADE 等级 | p7 Table 1；**p8 Table 2**；后续检查性能表 | 分流与确认是两个用途；性能要求不是通用浓度 cutoff；表下注释与 remarks 一并抽取 |
| 03 DETeCD 专科指南（29 页） | 编号推荐、评估步骤、分层检查用途、转诊/升级条件 | p4—5 Box 1；p12 相关分层评估正文 | 专科场景独立；检查的病因、功能与症候群用途不能混为确诊 AD |
| 04 DETeCD 基层指南（32 页） | 基层评估与检查推荐，先后条件、资源/场景限制 | p9 Recommendations 8、9；前文病史和测评推荐 | 与 03 共享指南背景但推荐场景不同；不能当两项独立验证研究 |
| 05 AD 2011 NIA-AA（12 页） | 痴呆前提、probable/possible 类别、条目及限制 | p4—6 诊断核心段落 | 含资料库封面；作者稿页码单独保存；不与 2024 生物学类别直接合并 |
| 06 PD 2015 MDS（10 页） | parkinsonism 前提、支持/红旗/绝对排除、两类组合规则 | p5 操作说明；**p6 Table 1**；后续解释 | 红旗数与支持项数的比较、最多两项红旗；子项不重复计数；正常已做检查与未做检查不同 |
| 07 DLB 2017 共识（15 页） | 痴呆前提、四类核心特征、指示性/支持性标志物、probable/possible | **p3 Table 1**；后文各特征和标志物解释 | 临床 RBD 与 PSG 证据分开；“less likely”不自动变成绝对排除；一年规则单独保留用途 |
| 08 bvFTD 2011 FTDC（22 页） | 表内诊断标准与附录操作定义；研究结果另路由 | **p5 Table 3**；p18—22 附录 | ≥3 个 A—F 域；域内逻辑不同，F 是全部子项；“early”的时间定义及类别专属排除 |
| 09 ALS 2020 Gold Coast（4 页） | 原始提议框架、完整主规则及定义 | **p3 Table 1** | 原始标准来源；不因 opinion paper 类型而丢弃条款，也不伪造推荐证据等级 |
| 10 ALS 2022 EMG 说明（3 页） | 原标准转述、操作定义、EMG 用途与灵敏度局限 | **p2 Box 1**；p1—2 正文 | 条款出处与 09 核对；不是 2022 新框架或独立验证研究；区域、肌肉、根/神经条件分层 |
| 11 MSA 2022 MDS（18 页） | established/probable/研究前驱类别；分类表、操作定义、MRI 及排除 | **p4—5 Table 1 及续表**；后续操作定义表 | 同一表的列决定类别；3/10 分钟等阈值不同；支持项脚注、MRI 区域去重、续表排除不能漏 |
| 12 PSP 2017 MDS（12 页） | O/P/A/C 编码定义、基本项、组合规则、表型类别 | p5 Table 2/3；p7 操作定义；**p8 Table 5**；前文 Table 1 | 组合表需回连定义表与通用前提；PDF 文本把组合符号抽成 `1`，必须按版面核实 |
| 13 HD GeneReviews（55 页） | 疾病/表型、CAG 区间、检测用途和方法局限；遗传咨询上下文 | p1 版本；p2—3 诊断与区间；后续检测方法表 | 冻结更新日期 **2026-02-12**，下载日期另存；边界、外显率与测量方法限制保留；预测检测与已有症状诊断分开 |
| 14 PD 2024 SynNeurGe（14 页） | S/N/G/C 分类、研究用途、检测定义与局限 | p1 用途声明；p4 Table 2；后续分类/限制段落 | FrameworkVersion.purpose=research；不得覆盖 PD 2015 临床标准；endorsed 与 investigational 分开 |

中文综述采用“导航/覆盖检查”模板：提取章节主题、别名、拟回答问题和引文编号，形成待核对主题清单。其 Markdown 与 Word 是同一篇综述的两种载体，先比对正文再登记载体关系，不按两份独立医学证据计数。表格重排或文字不一致处登记差异。综述的四个教学病例标记为 synthetic，不生成疾病事实、诊断充分条件或患者节点。

综述中的 `[3,4]`、`[3–5]` 等需逐项解析并映射来源清单。引文指向参考文献只证明引用关系，不能证明原文完整支持综述中的整个句子。来源已核实后可用 `DOCUMENT_RELATION(dependency_type=cites)`；二次陈述用 `secondary_report_of` 且保留依据，未知则 candidate。综述可提供中文显示名称和抽取问题，但正式医学定义、组合规则和边的依据优先回到其所引原始资料逐字段确认。

### 12.3 固定目标结构，抽取中间记录再映射

遵循 v0.3 的三层结构，不另造一套“疾病—症状—检查”节点契约：

| 层 | 对象 | 本批次怎样得到 |
|---|---|---|
| 知识层：概念与定义 | Concept、ClinicalPattern、TemporalConstraint、DistributionConstraint、MeasurementDefinition、DiagnosticUse | 概念释义、条目操作定义、时间/区域规则、检查的测量对象与用途 |
| 知识层：框架与分类 | FrameworkVersion、Criterion、CriterionBinding、DiagnosticRule、DiagnosticCategory、StageScheme、StageDefinition | 诊断表、编号条款、分期表及它们引用的定义 |
| 知识层：关系记录 | KnowledgeStatement | 已核对的来源主张映射到注册谓词，保存范围、条件、语气、例外 |
| 来源主张层 | SourceAssertion | **每篇文献分别抽**“该文献具体说了什么”，保留逐字段原文证据 |
| 文档层 | DocumentVersion、SourceSpan | 冻结文档版本、解析版本、真实原文、页码与版面位置 |

关键链路为：

```mermaid
flowchart LR
    D[冻结 PDF / DocumentVersion] --> P[版面块 / SourceSpan]
    P --> U[抽取单元与依赖上下文]
    U --> A[逐文献 SourceAssertion 候选]
    A --> V[引文与医学逻辑核查]
    V --> K[规范化知识节点和 KnowledgeStatement]
    K --> H[人工审核与冻结发布]
    H --> G[Neo4j 投影]
    H --> R[BM25 / 向量证据索引]
```

`ExtractionUnit`、`DocumentMap`、`ExtractionRun`、覆盖清单和审核任务是工程记录，不是新增核心图节点。IntermediateAssertion 是模型输出 DTO；完整映射并验证后才成为 N15。每个模型任务只处理一个 DocumentVersion。另篇文献可以生成新的 SourceAssertion；不能把两篇措辞拼接后说成其中一篇的原话。

正式知识关系使用 v0.3 注册集，如 `USES_CRITERION`、`FOR_CATEGORY`、`YIELDS_CATEGORY`、`USES_METHOD`、`TARGETS`、`SUPPORTS_DIAGNOSIS`；不能继续把旧 `associated_with` 共现结果当医学事实。具有领域含义的结构边也要生成 KnowledgeStatement，并保留说明该关系的来源。仅仅能找到两端的名称不够支持它们之间的边。

### 12.4 先恢复版面和引用关系，再构造抽取单元

**第一步：冻结来源。** 沿用清单 ID，校验 SHA-256；DOI/章节身份用于 logical document_id，内容 hash 用于不可变版本。源文件版本、解析版本、抽取 run、模型及 prompt 版本分别记录。在线发表年、期刊年、章节更新日、下载日分别保存，不猜测缺失日期。原文件留在用户指定目录，本次不移动。

**第二步：扫描全文并生成 DocumentMap。** 每页登记标题、表/框/图、条目编号、范围/用途声明、脚注、附录、参考文献、版面质量和路由。资料库封面、版权水印、重复页眉页脚留在解析记录中，不送作医学语义；参考文献用来解析依赖，不能把文献标题抽成研究结论。图表、缩写定义、跨页续表须登记，不能只扫描预设页码。

**第三步：带坐标的结构解析。** 优先评估 Docling 的 JSON/HTML 表格输出，映射到项目已有 ParsedBlock/ParsedTable/SourceSpan；pypdf 保留用于文本预检和正文回退。Docling 的文档模型提供页码、bbox 和 charspan；其 JSON/HTML 能保留合并单元格信息，而 Markdown 导出会压平跨度，所以不能仅凭 Markdown 恢复 MSA/PSP 表格。[Docling 文档模型](https://docling-project.github.io/docling/reference/docling_document/)、[表格序列化说明](https://docling-project.github.io/docling/concepts/serialization/)。工具能力不等于本批文献解析质量，先用试点页比较和核查后再确定运行版本。

表格单元格需要 `table_id、table_group_id、row/col、rowspan/colspan、header_path、footnote_refs、page_index、bbox`。续表通过标题、列标题和人工核对归为同一个 table_group；不能把第二页的排除项认成新类别。列内换行、双栏读序与 ≥/≤/+/α 等符号做定向检查。无法恢复的关键表进入 `parse_failed` 审核队列，候选规则不进入正式可执行集合。视觉转录仍须绑定页面区域，并保留原版面与转录审核，不用模型生成的文字覆盖原始文本。

**第四步：组装 ExtractionUnit。** 不按固定 500 字等长度直接切诊断标准。使用以下边界：

| 单元类型 | 本体内容 | 必须附带的上下文 |
|---|---|---|
| definition | 一项术语/模式/操作定义 | 上级定义、缩写与适用范围 |
| criterion_item | 一个编号条目 | 父列表说明、该类别、脚注、时间/检查定义 |
| rule_bundle | 某诊断类别的组合条款 | 前提、角色组、排除与定义引用；超长则分任务，但共享依赖清单 |
| recommendation | 一条编号推荐或用途 | 人群、场景、推荐强度/证据等级、remarks 与例外 |
| table_record | 一个完整逻辑行/列组 | 表标题、所有继承表头、跨页延续和关联脚注 |
| research_result | 一个明确报告结果 | 方法、样本、人群、参考标准、时点、估计/区间含义 |
| stage_record | 一个阶段 | 整个分期轴、阶段前提、表头和检查定义 |

每个单元记录 `primary_span_refs、context_span_refs、dependency_codes、unit_kind、framework_candidate、route、parse_quality`。标题/列表引导语产生的“至少三项”“全部必须”“仅研究用途”必须显式传入任务；附带上下文仍需证据定位，不能把继承当无来源默认值。

正文可在完整单元内设置模型 token 预算，超限时拆成条目定义任务与规则组装任务。重叠窗口只提高召回，同一原文的重复输出按文档/片段/命题去重，不增加证据票数。

### 12.5 模型分两阶段抽取，程序负责定位与校验

阶段 A 抽取来源主张；阶段 B 根据已定位主张规范化对象、闭合依赖、组装规则。整个过程保留模型候选与人工修订记录。

**阶段 A：单来源、单任务类型、受约束 JSON。** 最少输出：

```text
document_version_ref / extraction_unit_id
assertion_kind / source_statement_text / source_participants
polarity / modality / scope / condition / exceptions
source_item_code / parent_item_code / category / role_candidate
reported_results / reported_evidence_grade
evidence_by_field[{field_path, span_ref, exact_quote, context_hint}]
unresolved_dependencies / ambiguity_notes
```

`source_statement_text` 可以忠实释义；`exact_quote` 必须是任务输入原文中的连续字串。值与状态分开：known / unknown / not_reported / not_applicable；未知不填“所有人”“所有平台”或 false。参与者名称先保留原始英文，不要求模型猜 HPO/MONDO/UMLS ID。每个模型输出先做 schema 验证，不符合枚举或出现未注册字段则隔离。

抽取任务的提示词固定为以下约束，并配本批次人工审核的正反例：

```text
仅依据给定冻结文档的原文和上下文抽取，不使用医学常识补全。
区分条目定义、角色绑定、完整规则、推荐、限制和研究结果。
保留 AND/OR、至少/至多、计数单位、否定、时间起点、方法与例外。
每个有医学含义的字段给出来自 span_ref 的逐字引文。
没有原文依据的字段使用状态值；规则依赖不全则列出缺口。
不要把研究用途改成临床用途，不把建议改成必需。
不要把多个条目的共同出现解释为关联、充分条件或因果关系。
```

**程序定位引文，模型不决定偏移。** 根据 span_ref、exact_quote、上下文/出现序号找到唯一原文范围，以 Python Unicode code point 计数，保存左闭右开 `[start,end)`。跨句、表头和脚注的支撑用多个 EvidenceLocator，不拼一个不存在的连续引文。浏览器若使用 UTF-16 索引须转换；不能与 Python 偏移混用。检索清洗后的文字通过 normalization_map 回映原文；映射不确定则回到原文重抽或人工核对。

```python
# 正式引文的必要校验；不是医学正确性的充分条件
assert 0 <= start < end <= len(span.original_text)
assert span.original_text[start:end] == quote
assert span.document_ref == assertion.document_ref
```

如果相同引文在一个片段中出现多次，需用邻近上下文确定唯一 occurrence；不能默取第一处。匹配不到、只能模糊匹配、表格符号异常、依赖缺失分别产生明确任务，不用“差不多匹配”放行。LangExtract 的逐字提取与定位机制可作适配层参考；官方文档也指出无法定位的提取会有空 char_interval，需要过滤。使用本地 Ollama 时，其自定义 output_schema 支持有约束，不能据此省去本项目 Pydantic 校验。[LangExtract 官方项目](https://github.com/google/langextract)、[输出 schema 说明](https://github.com/google/langextract/blob/main/docs/examples/output_schema.md)。本期无需为了建图强制引入 LangExtract 或 GraphRAG 整套框架。

**阶段 B：规范化与规则组装。** 输入已定位的 A 阶段主张、框架类别清单、条目字典和依赖索引；输出完整 v0.3 对象候选。先生成概念、模式及测量定义，再生成 Criterion 和类别角色绑定，最后组装 DiagnosticRule/StageDefinition/DiagnosticUse 及 KnowledgeStatement。Verifier 任务逐字段检查条件遗漏、符号/范围/角色错误，可以提出修订；程序校验和人工审核仍然是发布依据，模型间一致不能替代来源。

### 12.6 六种抽取模板的具体字段和发布边界

| 模板 | 核心抽取字段 | 映射及边界 |
|---|---|---|
| 框架与规则 | 正式名称/版本、purpose、疾病、类别、前提、条目编号、角色、计数、组合、例外 | FrameworkVersion / Criterion / CriterionBinding / DiagnosticRule / DiagnosticCategory；同编号不同框架不合并 |
| 模式与操作定义 | 组成项、预期值、ANY/ALL、起点/时间窗、重复/持续、区域、阈值、方法 | ClinicalPattern / TemporalConstraint / DistributionConstraint / MeasurementDefinition；“早期”必须带本框架定义 |
| 检查用途与推荐 | 测量对象、方法/样本/平台、target、use_type、适用场景、准入性能、阈值、参考标准、语气、例外 | MeasurementDefinition / DiagnosticUse；原文推荐强度保存在来源字段，normalized modality 不能抹掉 conditional |
| 分期 | scheme/axis/version、阶段、成员条件、功能/生物学依赖、是否有序 | StageScheme / StageDefinition；probable/possible 不当作严重程度阶段 |
| 比较/限制 | 比较两端、比较维度、判别模式或用途、条件、否定与局限 | 使用注册谓词；来源只说“缺乏特异性”时不能反推所有鉴别边 |
| 研究结果 | 人群/样本、index test、reference standard、估计、CI 类型、单位、时点、设计和作者结论 | 优先 SourceAssertion.reported_results；明确结果不能自动变为临床规则或新增“研究结果节点” |

全文中同一条款可同时出现定义、解释和验证结果，按原意分别建来源主张并引用同一框架。结果没有样本或 CI 时保留状态，不借用另一篇结果。指南原文报告 GRADE 时保存 reported_evidence_grade；年份、文献类型、模型 confidence 或引文数都不能生成证据等级。

概念规范化采用“项目已有词表候选→定义/语境核对→标准 ID 映射”的顺序。疾病与综合征（PD/parkinsonism）、病理过程与临床痴呆、风险变异与明确致病变异不合并。中文译名是 display label/alias，英文原文永远保留。临床 RBD 模式与 PSG REM sleep without atonia 属于不同定义，不能仅因综述用“睡眠障碍”概括就合并。若标准词表无合适 ID，保留可审核的 local concept；拒绝错误映射优于强行对齐。

同名检查按实际测量定义建模；检查在 DLB/PD/SynNeurGe 中的角色由各自 CriterionBinding 或 DiagnosticUse 表达。跨框架条目只有定义、条件和范围一致且经核对才复用。别名相同不能证明条目等价。

### 12.7 本批次的条件逻辑必须这样抽

下列是从本地原文归纳的**设计级逻辑例子**，用于说明表达方式和测试关注点，不是可以直接用于患者判断的完整规则对象。正式对象必须补齐各条目操作定义、全部适用范围/例外和人工审核；本次均不标 executable=true。

**DLB：按核心特征身份计数。** p3 Table 1 的 probable 分支为“至少两个核心特征”或“一个核心特征加至少一个指示性生物标志物”，并有痴呆前提及其他解释限制。结构应是：

```text
ALL(
  prerequisite:dementia,
  ANY(
    AT_LEAST_K(2, DISTINCT(core_feature_group)),
    ALL(AT_LEAST_K(1, DISTINCT(core_feature_group)),
        AT_LEAST_K(1, DISTINCT(indicative_biomarker_group)))
  )
)
```

四个核心 feature 各有 group_key；同一种幻视来自两份描述仍计一项。只有指示性标志物而无核心特征不能得到 probable，possible 类别有自己的规则。支持性标志物不放进 indicative 计数。表内“less likely”保存限制主张，不默改为 NOT 排除；DLB/PDD 时间边界的场景和作者解释另建 TemporalConstraint/比较主张。

**PD：红旗的作用取决于类别。** p6 Table 1 包含前提 `bradykinesia AND (rest_tremor OR rigidity)`。clinically established 与 clinically probable 分别建规则；前者要求无绝对排除、至少两项支持且无红旗；后者要求无绝对排除、红旗不超过两项且被支持项抵消。组装 `COMPARE_COUNTS(red_flag_count <= supportive_count)` 并保留上限。临床药物反应条目的多个描述不计为多个支持项；正常突触前多巴胺成像作为“已执行并有正常结果”的排除核对，未做该检查不当成正常或强制检查。

**bvFTD：≥3 项指 A—F 六个域，不是任意三个子症状。** p5 Table 3 中 A—E 域内有 OR；F 的执行、记忆相对保留和视空间相对保留三项为 ALL。possible 规则计域；probable 还要求 possible、显著功能下降和合适影像。神经退行性进展、持续/反复及 early 的定义一同闭合。排除 C 在 possible/probable 中作用不同，必须按 category 建 CriterionBinding；不能把 C 永久写进 Criterion.role。

**ALS：先定义肌肉/区域受累，再计区域。** p3（09）和 p2 Box 1（10）分别保存来源主张并核对一致性。主结构：

```text
ALL(
  progressive_motor_impairment_preceded_by_normal_function,
  ANY(
    EXISTS_REGION(r, ALL(UMN(r), LMN(r))),
    AT_LEAST_K(2, DISTINCT_REGION(r, LMN(r)))
  ),
  investigations_excluding_other_disease_processes
)
```

上述 `EXISTS_REGION` 是说明性记法，落地前须映射/注册到 v0.3 ConditionExpression 求值契约，不假装已被当前求值器支持。LMN 定义还含“临床无力且萎缩”或“EMG 慢性神经源改变且进行性失神经”；肢体区域需要不同根和神经支配的两块肌肉，球部/胸部规则另列。`counting_unit=muscle` 与 `region` 分开，并保存同一区域约束。EMG 中 supportive but not obligatory 的成分不升级为必需。仅有 LMN 主条款而缺肌肉/区域定义，rule_completeness=partial。

**MSA/PSP：二维表与定义依赖不能扁平化。** MSA p4—5 同表两列的自主神经/小脑阈值和 MRI 要求不同。支持项脚注“排除孤立的勃起功能障碍”要落到适用表达式；MRI 计数按受累脑区，不能把同脑区的萎缩和弥散变化算两项。正文 p5 对 PVR 写 `>100 mL`，而 Table 1 写 `≥100 mL`，本地可见不一致；分别保存来源字段并标记 boundary_conflict，待审核记录说明规范化依据，不能静默挑选或平均。PSP 的 O/P/A/C 编码须回连 Table 4 操作定义、Table 1 通用条件和排除；文本把 Table 5 的组合加号抽成 `1`，必须看原版面，不能解释成数值或擅自按字符串替换。某表型组合不能仅抽出眼球运动与疾病的支持边。

**血液标志物/HD：三类数值分开。** 02 p8 的分流条件是敏感度 ≥90%、特异度 ≥75%；确认用途是两者均 ≥90%，适用于原文规定的客观认知损害、专科记忆诊疗场景。保存到 DiagnosticUse.qualification_expression，并连同参考标准、conditional recommendation / low certainty、临床评估前提、阳性分流后的确认和备注限制。它们不是 MeasurementDefinition 的个人血液浓度阈值，也不是患 AD 的概率。若来源未给具体 assay cutoff，threshold_definition 不补造。13 p3 的 CAG 区间则属于遗传测量结果解释，27—35、36—39、≥40 等边界与外显率说明保留；重复中断及片段分析可能低估重复数的限制与方法绑定，不把例子变成新的通用诊断界值。预测性检测的扩增结果与已有症状的 HD 诊断不同。

**所有可执行表达式采用三值逻辑。** ALL 遇 false 为 false、全部 true 才 true；ANY 遇 true 为 true、全部 false 才 false；NOT unknown 仍 unknown。至少 k 项计数：已确定 true 的 distinct 项达到 k 才 true；true 加 unknown 的最大可能仍低于 k 才 false，其余 unknown。计数比较、时间窗口和区域量词也需要明确定义上下界/未知状态。未知值不得被转换为阴性；可选检查的 applicability 必须区分“来源未要求所有人做检查”与“当前缺少已做检查的结果”。

DSL 操作 ALL/ANY/NOT/AT_LEAST_K/COUNT_DISTINCT/COMPARE_COUNTS/BINDING_REF/PATTERN_REF 按 v0.3 落地；时间/区域等尚未实现的运算列入 contract registry。含 TEXT、未注册运算或未闭合依赖的对象可以解释与检索，但 executable=false。不能把可读的自然语言摘要标为结构完整且可执行。

### 12.8 来源覆盖、归并和版本冲突

每个重要字段独立挂载证据。例如某 DiagnosticRule 的 `expression` 来自组合表，Criterion 的 `operational_definition` 来自后文，TemporalConstraint 的 anchor/window 来自脚注；保存各自 SourceAssertion 与 SourceSpan。规则表达式引用的 binding/pattern、排除及其定义形成 dependency closure，检索时一并返回。`DEPENDS_ON` 必须有确定的依赖语义与来源，不扩成任意医学相关边。

来源链使用 v0.3：

```text
知识节点 / KnowledgeStatement
  --HAS_SOURCE(字段路径、supports/qualifies、coverage)--> SourceAssertion
SourceAssertion --FROM_DOCUMENT--> DocumentVersion
SourceAssertion --EVIDENCED_BY(start,end,quote,字段路径,角色)--> SourceSpan
SourceSpan --PART_OF_DOCUMENT--> DocumentVersion
```

KnowledgeStatement 对应的正式直连边带 statement_ref；subject/object/predicate/qualifiers 必须一致。原文覆盖只能对声明的字段路径生效；提到某名称属于 mentions，不能充当定义、role、阈值或整个节点的 complete 支持。源主张与知识命题有争议时标 disputed/partial，不发布肯定直连投影。

知识归并键包括端点修订、谓词、polarity、modality、scope、condition、exceptions 和必要 qualifiers。SourceAssertion 始终保留每篇来源身份；同义改写/重复窗口去重不增加独立证据。ALS 2022 转述原标准、DETeCD 两种场景摘要、综述多次引用的依赖关系均保留可查依据，未知研究身份不强并。09 与 10 关系的具体类型和定位、03 与 04 的共享背景要核实后再登记，不凭文件名设置 verified_study_id。

AD 2011/2024 与 PD 2015/SynNeurGe 2024 分别存在；purpose 和 diagnostic_axis 控制查询。只有来源明确给出替代关系并核实依据才建立 supersedes，不按年份推断。跨来源推导出的新综合结论保持 draft，保存 normalization_record，不能借几条部分证据伪装成一篇原文的完整规则。

### 12.9 完整性检查和正式发布条件

维护**来源驱动的覆盖清单**：每份文献的类别、编号推荐、条目组、表行、脚注与定义依赖，按 DocumentMap/人工核对生成 expected 清单。不能只统计模型已经抽出的对象来宣称“100% 覆盖”。每项标 expected → extracted → validated → reviewed 或明确 excluded(reason)；缺项进入 gap queue。

| 检查 | 自动检查或审核内容 | 失败处理 |
|---|---|---|
| 来源完整性 | hash、文档/parse 版本、页序、span 归属、偏移逐字相等 | quote_unresolved / version_mismatch；阻止发布 |
| 版面忠实度 | 表头/跨页/合并单元格、符号、脚注、读序 | parse_failed；回到版面核对或人工转录 |
| 类型和关系 | 17 类节点、注册谓词、domain/range、引用修订、字段状态 | schema_invalid；禁止未注册谓词写图 |
| 字段覆盖 | 定义、范围、角色、逻辑、阈值、单位、语气/否定、例外逐字段可追踪 | evidence_gap；保留候选及缺口 |
| 规则闭包 | 条目代码全部解析、通用前提/排除、group_key、依赖无环 | dependency_missing；partial，executable=false |
| 语义核查 | AND/OR、边界、时间起点、临床/研究、表列、计数单位、可选门控 | semantic_ambiguity / boundary_conflict；人工决定并记录理由 |
| 来源去重 | 转述/同研究/载体关系和重复输出 | 不确定依赖保留 candidate，不增加独立支持票数 |
| 发布一致性 | 同 release 的对象/边、statement_ref 一致、source coverage 与审核状态 | projection_mismatch；不切换线上 release |

第一批所有正式医学内容人工审核，尤其每个完整诊断规则、操作定义、用途阈值和例外必须核对原文。审核视图同时显示 PDF 位置、原文引文、完整条件树、定义闭包、源字段与归一化字段的差异，不能只看一句摘要或模型 confidence。approved 表示审核通过；executable 还需要求值器契约与规则测试通过。抽取模型自评高分不能自动发布。

工作流状态建议为 `queued → parsed → candidate → validated → pending_review → approved/rejected → released`；这些是工程任务状态，不强行扩展核心图的枚举。修订不能覆盖旧证据或旧规则。审核修改作用到新 revision，并保留原候选、修改字段、审核依据和时间。

### 12.10 先做有代表性的试点，再扩展本批资料

**第一组试点：** 07 DLB（p3 及定义）、02 血液指南（p8 及备注）、09+10 ALS（主表/框注及方法限制）。这组覆盖条目计数与类别、推荐用途/性能/例外、区域肌肉逻辑与二次转述。先人工制作源条款和依赖清单，再执行相同的解析/模型/验证/审核流程。

**第二组压力样例：** 06 PD、08 bvFTD、11 MSA、12 PSP 的关键表及操作定义。检验计数比较、域内外嵌套、跨页表、多列阈值、OCR/字符映射、冲突边界和 code→definition 回连。

**随后扩展：** 01/05 的框架及分期、03/04 场景化推荐、13 遗传检测及版本、14 研究分类。最后用中文综述的章节和问题逐项回查遗漏。覆盖全部 14 份诊断内容并不要求把 13 的全部治疗段落写入图谱。

初始评测集建议人工标注 120—200 个来源单元，实际数量以覆盖上述错误类型为准；这是计划规模，不是已存在的 gold set。标注条目/依赖/字段、exact quotes、完整逻辑树、角色与适用范围、正确拒识或 partial 情况。由第二轮复核消解歧义，记录标注理由。开发/测试按框架或条款家族分组隔离；ALS 转述、同条款改写、综述转述及高度近似推荐不得分到两侧造成泄漏。最终某个 holdout 框架应整组留出，不仅留出几页。

发布的结构门槛为 100% 合法引用、100% 逐字引文验证、100% 正式医学字段来源覆盖及人工审核、0 个未注册/不支持运算标为 executable。它们是逐记录硬门槛，不能用平均准确率豁免。候选抽取精确率/召回率、条件树完全匹配率、范围/例外保留率、每百条审核时间另按试点评估；试点前不承诺某模型达到 95% 等指标。

必须做有意义的条件测试：DLB 只有标志物、同核心重复记录；PD 两项红旗与一项支持、未做成像；bvFTD 同域三个子项、F 缺子项；ALS 跨区域 UMN/LMN、同根/神经两块肌肉；MSA 续表漏排除、同脑区 MRI 重复；PSP 代码依赖缺失；血液检测的适用场景/确认性能缺项；HD 区间边界和平台解释。这些是将来求值器的合成测试输入，不冒充真实病例或独立医学证据。边界与 unknown 的期望结果须由审核后的规则定义，不从同一实现机械生成。

### 12.11 输出和增量处理

建议后续实现每个 extraction_run 的中间产物：

```text
output/knowledge_extraction/<run_id>/
  document_map.jsonl / parse_quality.jsonl
  extraction_units.jsonl / assertion_candidates.jsonl
  coverage_report.json / gaps.jsonl / review_queue.jsonl
  model_io/  （请求、响应、schema/prompt/model 版本；不记录密钥）

data/knowledge/<corpus_id>/<release_id>/
  document_versions.jsonl / source_spans.jsonl
  source_assertions.jsonl / knowledge_nodes.jsonl
  knowledge_statements.jsonl / edges.jsonl
  manifest.json / approved_review_records.jsonl
```

路径是本批次建议，具体与现有存储组件适配；不是声称上述发布文件已生成。knowledge_nodes 保存 N01—N13，KnowledgeStatement 与来源单独分文件，但仍属相同 17 类契约。发布 manifest 冻结 source hash、parse/prompt/model/schema/evaluator/release 版本和记录计数；SQLite 保存权威修订，Neo4j 和文本索引是可重建投影。索引命中至少返回 source_assertion_ref、knowledge refs、span refs、release_id，并能补回条目与规则的依赖闭包。

增量键至少包括 `document_hash + parse_contract + extraction_template_version + prompt_hash + model_identity + schema_version`。任一语义因素变化需新 run；原文变化产生新 DocumentVersion，解析变化产生新 parse_id，偏移不覆盖。复用已审核对象仍需说明与新版本的对齐。运行按单元持久化、断点续跑和有界重试；无效 JSON、解析失败、模型不可用分别记账，不静默转成空结果。

### 12.12 与现有代码的对应及实施顺序

当前代码可以复用来源登记、存储、审核/发布骨架和基础索引，但尚不是本设计的语义抽取器：

| 当前模块 | 已有能力 / 本次检查到的限制 | 下一步实现 |
|---|---|---|
| `code/neurogra/knowledge/parsing/simple_pdf.py` | pypdf 页文本、按段落输出；没有可靠标题/表格与 bbox | 增加布局解析适配器、表格网格/脚注/续表和质量闸门，保留原解析版本 |
| `schemas/source.py` | 已有块/表格、SourceSpan、EvidenceRef 结构 | 扩展或映射布局 locator/normalization_map；统一 Unicode 偏移契约 |
| `processing/segmenter.py` | 句段切分与长度控制 | 增加 criterion/rule/table/recommendation 单元和依赖上下文组装 |
| `models/base.py` | 受 schema 约束的模型接口边界 | 实现任务路由、结构化适配器、输入输出留档、重试与拒识；先验证本地模型质量 |
| `extraction/extractor.py` | RuleBasedExtractor 是少量词表共现的流程验证实现 | 加 SourceAssertion 抽取和规则组装；共现仅作候选定位，不能发布关联关系 |
| `schemas/graph.py` | 旧 Entity/ClauseRevision，条件主要 ATOM/AND/OR/NOT/TEXT | 实现 17 类型 v0.3 契约、角色绑定、注册关系和计数/时间/分布求值契约 |
| `validation/validator.py` | quote 在原文/规范化文本出现的宽松匹配 | 改为 exact offset + 同文档 + 字段覆盖 + 规则闭包；兼容迁移记录不能冒充新门槛 |
| `graph_store/neo4j.py` | 旧条款图投影 | 根据 v0.3 节点/关系注册表投影、statement_ref 一致性检查、同 release 原子切换 |

实施顺序是：**目标 schema 与严格证据契约 → 布局/语义单元 → 试点人工清单和样例 → 来源主张抽取 → 规则/用途映射与闭包 → 审核发布 → 求值器及下游检索接口**。解析、抽取和规则逻辑测试随对应实现加入；本次只有文档与待审核示例，不扩展现有临床算法或运行全项目测试。

旧 ClauseRevision 可通过 migration_record 与新 SourceAssertion/KnowledgeStatement 建映射，审核明确后迁移；不能重命名旧 entity/condition 字段便声称支持 v0.3。第 9 节接口最终需转为 approved knowledge refs、SourceAssertion/SourceSpan refs、完整条件/定义闭包、固定 framework/purpose/release；下游患者信息与 Observation 仍由病例层管理，不塞进本批知识层。

### 12.13 本设计的完成状态与剩余工作

已完成：本地语料盘点与 hash/page 核验、中文综述及关键条款核读、代表性版面检查、逐篇路由设计、抽取/来源/逻辑/审核契约、真实原文待审核样例。样例偏移可以程序逐字核验；这不等于医学字段已获正式审核。

尚待实施：结构解析器的本批质量评测、v0.3 schema/求值器、模型抽取适配器、人工 gold set、全批候选抽取和医学审核、正式 release/Neo4j 投影。本次没有将 14 份文献自动入图，也没有把任何样例设为 approved 或 executable。先完成三类试点，才能用实际候选准确率和审核成本确定下一批工作量。
