# 算法零：医学文本到 RAG 知识库与条件化知识图谱的构建

> 本文解决“收集到指南、共识和论文后，如何把文本转换为可检索、可计算、可追踪的知识底座”。它是 NeuroGRA 的离线知识建设算法，为算法一“条件感知的鉴别证据检索”和算法二“证据状态引导的有界复核”提供输入。  
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
