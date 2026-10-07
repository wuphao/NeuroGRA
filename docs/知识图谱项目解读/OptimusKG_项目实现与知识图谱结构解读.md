# OptimusKG：项目实现与知识图谱结构解读

解读日期：2026-10-03。依据：本地 `OptimusKG/OptimusKG` 的 README、Python 源码、Kedro catalog 和图谱 schema 文档。本文重点说明工程实现与数据结构；图谱规模引用仓库文档，尚未运行全量构建或独立验证论文实验。

## 1. 项目实现了什么

OptimusKG 是一套生物医学知识图谱的构建、导出、加载和查询工具。它整合基因、疾病、药物、表型、解剖结构、通路、GO 功能以及环境暴露等信息，形成具有来源和证据属性的异构图。

它的主要产物是知识图谱数据，以及使用这些数据的客户端和查询服务。构图的核心是对已有数据库和本体进行解析、标识映射、属性合并和关系整理。

项目实现的主要功能包括：

1. 按配置下载和读取不同格式的原始数据。
2. 将不同数据源转换成规范的中间表。
3. 映射实体标识，构造 10 类节点与 27 类实体配对边。
4. 保留来源、关联评分、文献、临床阶段和研究背景等属性。
5. 汇总并导出全图、分类子表和最大连通分量。
6. 提供 Python 数据访问客户端和 MCP 查询服务。
7. 提供图统计、绘图和基于文献检索的关系评估工具。

### 1.1 主要数据来源

本地流水线包含 Open Targets、DrugBank、DrugCentral、DisGeNET、Bgee、Reactome、CTD、OnSIDES、PPI 相关资源，以及 GO、MONDO、HPO、UBERON 等本体。

README 声明整合 65 个资源、18 个本体或受控词表，发布图约有 192,813 个节点和 21,834,669 条边。这些是 README 对发布数据的描述，不表示当前本地已经生成同等规模的数据；不同说明文件中的统计也有版本差异。

### 1.2 目录职责

| 目录或文件 | 职责 |
| --- | --- |
| `optimuskg/pipelines/bronze` | 解析各数据源，构造中间表 |
| `optimuskg/pipelines/silver/nodes/nodes` | 生成实体节点及其属性 |
| `optimuskg/pipelines/silver/nodes/edges` | 生成关系边及其属性 |
| `optimuskg/pipelines/gold` | 汇总并导出图谱 |
| `conf/base/catalog` | 定义数据路径、格式、schema、来源和校验元数据 |
| `optimuskg/hooks` | 下载、校验和 checksum 等构建行为 |
| `packages/optimuskg` | 独立 Python 数据访问客户端 |
| `mcpb` | 基于 DuckDB 的 MCP 查询服务 |
| `cli` | 统计、图形和文献证据评估工具 |
| `docs` | 项目网站和使用文档 |
| `assets/remotion` | 项目介绍视频的制作代码 |

## 2. 构建流程

项目使用 Kedro 编排任务，主要使用 Polars 处理表数据，配置和代码共同定义数据依赖。

```text
外部数据库、本体、受控词表
             ↓
Landing：获取和保存原始文件
             ↓
Bronze：解析来源格式，整理中间表
             ↓
Silver：映射标识、合并来源，生成节点和边
             ↓
Gold：汇总图谱、计算最大连通分量、导出
```

Landing 主要通过读取数据集前的下载 hook 实现；Bronze、Silver、Gold 是代码中的流水线。节点函数的输入和输出由 Kedro catalog ID 连接成 DAG，部分任务可以并行执行。

### 2.1 疾病—基因关系的实现例子

`silver/nodes/edges/disease_gene.py` 的实际过程为：

1. 读取 Open Targets 的疾病—基因关联。
2. 从疾病交叉引用中整理 UMLS 标识映射。
3. 使用 UMLS 标识和基因符号，将 DisGeNET 关联映射到相应实体。
4. 以 Open Targets 关联为主表，通过左连接补充匹配到的 DisGeNET 属性。
5. 合并评分、文献数量、年份及来源，输出统一边表。

因此，这段实现不是两个来源关联的完整并集：仅存在于 DisGeNET、无法匹配到主表的关联不会通过这次左连接新增进来。文件中还保留了跨标识合并的 TODO。

部分实体节点由关系端点收集得到，再关联名称和元数据。例如基因节点会从各类基因关系中汇总 ID。这意味着实体覆盖范围受到上游关系和标识映射的影响。

### 2.2 导出与质量检查

Gold 支持 Parquet 和 Neo4j 导出。当前 `parameters.yml` 默认启用带属性的 Parquet 导出，Neo4j 导出被注释，额外的 BioCypher 校验默认关闭。

质量 hook 会检查或记录：

- 节点 ID 是否全局唯一。
- 边端点是否引用存在的节点。
- 部分输出中的 ID 空值和关系枚举合法性。
- 列名规范及 `OTHER` 关系比例。
- 图的连通分量情况。

连通性检查当前记录统计，不强制要求整个图只有一个连通分量。最大连通分量基于忽略边方向的 NetworkX `Graph` 计算，因此不是有向图的强连通分量。

缺少部分私有输入时，下载 hook 可以对支持的 CSV/XML 类型创建空占位文件，让后续步骤继续运行；这并不保证缺少来源时的图与完整发布图相同。输入位置以 catalog 的 `filepath` 为准。

## 3. 知识图谱的整体结构

这是具有类型和嵌套属性的异构图。可以写作：

```text
节点 = (id, label, properties)
边   = (from, to, label, relation, undirected, properties)
```

其中：

- 节点 `label` 是实体类型代码，例如 `GEN`。
- 边 `label` 是实体配对代码，例如 `DIS-GEN`。
- 边 `relation` 是具体语义，例如 `ASSOCIATED_WITH` 或 `INHIBITOR`。
- `properties` 保存该类型的名称、来源、证据等属性。

27 类边是 27 种实体配对，不等于只有 27 种关系谓词。一类药物—基因边可以表达激动、拮抗、抑制、转运等多种关系。

```text
疾病 ──关联── 基因 ──相互作用── 基因
  │             │
  └──表型── 表型 │
                ├── 解剖结构中的表达
药物 ──作用─────┤
  ├──适应证── 疾病
  └──不良反应─ 表型
                ├── 生物过程 / 分子功能 / 细胞组分
环境暴露 ───────┤
                └── 通路
```

该示意只表达实体间连接类别，实际方向和关系以每条边的字段为准。

## 4. 节点结构、类型与属性

### 4.1 节点公共字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | String | 全局唯一实体标识；沿用或整理各来源命名空间 |
| `label` | String | 3 字母实体类型代码 |
| `properties` | Struct / JSON String | 类型专有属性 |

不能假设所有 ID 都有相同格式。有的使用带冒号的标识，有的使用 Ensembl ID 或带下划线的本体标识；跨来源查询应使用实际 ID 和交叉引用。

每类节点的 `properties` 均定义来源结构：

```json
{
  "sources": {
    "direct": ["OPEN_TARGETS"],
    "indirect": []
  }
}
```

`direct` 是直接贡献该条目的来源，`indirect` 是上游资源所引用、经过映射整理的间接来源。来源列表表示来源归属，不等于每个属性均具有独立、精确到文献的溯源记录。

### 4.2 全部 10 类节点

| 代码 | 文件名 | 类型 | 主要属性内容 |
| --- | --- | --- | --- |
| `GEN` | `gene` | 基因 | 符号、名称、位置、转录本、功能、蛋白、同源信息、靶点研究 |
| `DIS` | `disease` | 疾病 | 名称、描述、编码、同义词、治疗领域、UMLS/SNOMED 映射 |
| `DRG` | `drug` | 药物 | 名称、化学结构、理化性质、审批、临床阶段、安全信息 |
| `PHE` | `phenotype` | 表型 | 名称、描述、类型、同义词、本体、UMLS/SNOMED 映射 |
| `ANA` | `anatomy` | 解剖结构 | 名称、定义、同义词、交叉引用、本体元数据 |
| `BPO` | `biological_process` | 生物过程 | 名称、定义、同义词、交叉引用、本体元数据 |
| `MFN` | `molecular_function` | 分子功能 | 名称、定义、同义词、交叉引用、本体元数据 |
| `CCO` | `cellular_component` | 细胞组分 | 名称、定义、同义词、交叉引用、本体元数据 |
| `PWY` | `pathway` | 通路 | 名称、物种 |
| `EXP` | `exposure` | 环境暴露 | 名称、来源类别、来源详情 |

### 4.3 复杂属性如何理解

基因属性最丰富：`genomic_location` 是染色体、起止坐标与链方向组成的对象；`canonical_transcript` 是主要转录本信息；`homologues`、`constraint_scores`、`tractability` 等是对象列表。

疾病和表型的 `xrefs` 是字符串列表；基因的 `xrefs` 则是含 `id`、`source` 的对象列表。相同字段名在不同实体类型中可能有不同结构。

解剖结构和三类 GO 节点的 `ontology` 包含 `title`、`description`、`license`、`version`。表型也有这个属性；当前疾病 catalog 不包含 `ontology`。

药物节点既有 SMILES、InChI、分子量等化学信息，也有审批与警告字段。`mol_file_base64` 和 `mol_image_base64` 可保存结构文件和图像编码。药物属性中的部分简称来自来源数据库；例如代码注释将 `cd_id` 解释为 calculated descriptor 标识，不能仅凭前缀将它理解为所有来源的统一药物 ID。

完整节点属性及数据类型见本文附录 A。

## 5. 边结构、类型与属性

### 5.1 边公共字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `from` | String | 起点节点 ID |
| `to` | String | 终点节点 ID |
| `label` | String | 实体配对代码，如 `DRG-GEN` |
| `relation` | String | 具体关系枚举 |
| `undirected` | Boolean | 是否被代码标为语义无方向 |
| `properties` | Struct / JSON String | 来源、证据及关系专有属性 |

边基础 schema 没有单独的公共 `id` 字段，也没有统一 `weight` 字段。关联评分、表达排名和证据数量属于各类边自己的属性，不能直接放到同一评分尺度比较。

### 5.2 全部 27 类实体配对边

下表方向标记来自本地边构造代码。`True` 表示 `undirected=True`；它是实现中的标记，不代表可以忽略所有生物学语义。

| 代码 | 实体配对 | 关系 | undirected | 属性组 |
| --- | --- | --- | --- | --- |
| `ANA-ANA` | 解剖结构—解剖结构 | `PARENT` | False | 仅来源 |
| `ANA-GEN` | 解剖结构—基因 | `EXPRESSION_PRESENT` / `EXPRESSION_ABSENT` | True | 表达 |
| `BPO-BPO` | 生物过程—生物过程 | `IS_A` | False | 仅来源 |
| `BPO-GEN` | 生物过程—基因 | `INTERACTS_WITH` | True | GO 注释 |
| `CCO-CCO` | 细胞组分—细胞组分 | `IS_A` | False | 仅来源 |
| `CCO-GEN` | 细胞组分—基因 | `INTERACTS_WITH` | True | GO 注释 |
| `DIS-DIS` | 疾病—疾病 | `PARENT` | False | 仅来源 |
| `DIS-GEN` | 疾病—基因 | `ASSOCIATED_WITH` | True | 关联证据 |
| `DIS-PHE` | 疾病—表型 | `PHENOTYPE_PRESENT` / `PHENOTYPE_ABSENT` | True | 临床表型 |
| `DRG-BPO` | 药物—生物过程 | `INDICATION` | True | 临床阶段、参考文献 |
| `DRG-DIS` | 药物—疾病 | `INDICATION` / `CONTRAINDICATION` / `OFF_LABEL_USE`；未映射项可为 `OTHER` | True | 临床用药、冲突 |
| `DRG-DRG` | 药物—药物 | `SYNERGISTIC_INTERACTION` / `PARENT` | False | 相互作用、冲突 |
| `DRG-GEN` | 药物—基因 | 药理作用或靶点角色，见下文 | False | 作用机制、冲突 |
| `DRG-PHE` | 药物—表型 | 不良反应、关联、适应证、禁忌证、超说明书使用；未映射项可为 `OTHER` | True | 临床用药、冲突 |
| `EXP-BPO` | 暴露—生物过程 | `INTERACTS_WITH` | True | 暴露研究 |
| `EXP-CCO` | 暴露—细胞组分 | `INTERACTS_WITH` | True | 暴露研究 |
| `EXP-DIS` | 暴露—疾病 | `LINKED_TO` | False | 暴露研究 |
| `EXP-EXP` | 暴露—暴露 | `PARENT` | False | 暴露研究 |
| `EXP-GEN` | 暴露—基因 | `INTERACTS_WITH` | False | 暴露研究 |
| `EXP-MFN` | 暴露—分子功能 | `INTERACTS_WITH` | True | 暴露研究 |
| `GEN-GEN` | 基因—基因 | `INTERACTS_WITH` | True | 仅来源 |
| `MFN-GEN` | 分子功能—基因 | `INTERACTS_WITH` | True | GO 注释 |
| `MFN-MFN` | 分子功能—分子功能 | `IS_A` | False | 仅来源 |
| `PWY-GEN` | 通路—基因 | `INTERACTS_WITH` | True | 仅来源 |
| `PWY-PWY` | 通路—通路 | `PARENT` | False | 仅来源 |
| `PHE-GEN` | 表型—基因 | `ASSOCIATED_WITH` | False | 关联证据 |
| `PHE-PHE` | 表型—表型 | `PARENT` | False | 仅来源 |

`DRG-GEN` 的本地映射包含：

```text
ACTIVATOR, AGONIST, ALLOSTERIC_ANTAGONIST, ANTAGONIST,
ANTISENSE_INHIBITOR, BINDING_AGENT, BLOCKER, CARRIER,
CROSS_LINKING_AGENT, DEGRADER, DISRUPTING_AGENT, ENZYME,
EXOGENOUS_GENE, EXOGENOUS_PROTEIN, HYDROLYTIC_ENZYME,
INHIBITOR, INVERSE_AGONIST, MODULATOR,
NEGATIVE_ALLOSTERIC_MODULATOR, NEGATIVE_MODULATOR,
OPENER, OTHER, PARTIAL_AGONIST,
POSITIVE_ALLOSTERIC_MODULATOR, POSITIVE_MODULATOR,
PROTEOLYTIC_ENZYME, RELEASING_AGENT, RNAI_INHIBITOR,
STABILISER, SUBSTRATE, TARGET, TRANSPORTER, VACCINE_ANTIGEN
```

枚举或映射中允许某个关系，并不表示当前发布数据中必然出现该值。

### 5.3 不同边的属性含义

| 属性组 | 主要内容 |
| --- | --- |
| 关联证据 | `evidence_score`、`evidence_count`、`disgenet_score`、DSI、DPI、年份、PMID 数量、SNP 数量 |
| 表达 | `expression_rank`、`call_quality`；表达存在/缺失放在 `relation` |
| GO 注释 | `evidence`、`gene_product`、`eco_ids` |
| 临床表型 | `frequency`、`onset`、`sexes`、`modifiers`、`evidence_type`、`references`、`qualifier_not` |
| 临床用药 | `highest_clinical_trial_phase`、`reference_ids`，以及部分来源的结构和关联 ID |
| 作用机制 | `mechanisms_of_action`、`source_ids`、`source_urls` |
| 相互作用 | `interaction_description` |
| 暴露研究 | 对象、人群、年龄、检测方法、介质、地域、研究时间、结果描述和文献 |

所有边类型都定义 `sources`。某些关系只保留来源，不带统一评分。例如基因—基因和通路—基因边不能从公共 schema 中直接取得置信度。

### 5.4 关系合并与冲突

药物—疾病、药物—药物、药物—基因、药物—表型以及疾病—表型等多源合并边定义了：

- `relation_assertions: List[Struct{source: String, relation: String}]`
- `relation_conflict: Boolean`

合并时按 `RELATION_PRIORITY` 选出代表关系，优先级数值越小越优先，相同优先级按名称排序。原始来源断言保存在属性中，预定义的互斥关系组合会设置冲突标记。

以下是结构示例，不是当前本地数据中的真实记录：

```json
{
  "from": "DrugBank:EXAMPLE",
  "to": "MONDO:EXAMPLE",
  "label": "DRG-DIS",
  "relation": "INDICATION",
  "undirected": true,
  "properties": {
    "sources": {
      "direct": ["OPEN_TARGETS", "DRUG_CENTRAL"],
      "indirect": []
    },
    "relation_assertions": [
      {"source": "OPEN_TARGETS", "relation": "INDICATION"},
      {"source": "DRUG_CENTRAL", "relation": "CONTRAINDICATION"}
    ],
    "relation_conflict": true
  }
}
```

该示例展示了为什么不能只读取顶层 `relation`。这些冲突字段只在相应类型的 schema 中存在；不能假设每条边都携带它们。冲突检测也只覆盖代码预定义的关系组。

## 6. 存储格式与访问方式

默认 Gold 输出路径由 catalog 定义在 `data/gold/kg/parquet/`，主要布局为：

```text
nodes.parquet
edges.parquet
largest_connected_component_nodes.parquet
largest_connected_component_edges.parquet
nodes/gene.parquet
nodes/disease.parquet
nodes/drug.parquet
...其他节点分类文件
edges/disease_gene.parquet
edges/drug_gene.parquet
...其他边分类文件
```

分类文件中 `properties` 是原生 Struct，保留每类属性的具体数据类型。汇总文件将属性编码为 JSON String，以容纳不同类型的不同属性结构。它仍然是一个字段，并非全部展开为独立顶层列。

Python 客户端提供 `get_file`、`load_parquet`、`load_graph`、`load_networkx`，从 Dataverse 下载并缓存发布数据。客户端与根目录的构图工程是不同的包布局，部署时应区分。

`load_networkx()` 返回 `MultiDiGraph`，默认将 `properties` 解码到节点和边的属性字典。实际构造代码对每行边仅执行一次 `add_edge(from, to)`，保留 `undirected` 标记，但不会自动插入反向边；遍历无向关系时应自行处理。

MCP Bundle 将 Parquet 数据构建为本地 DuckDB，提供 schema、实体搜索、实体详情、邻居、邻居计数、连接路径和受限制的只读 SQL 查询。自然语言理解由调用 MCP 的模型完成。

## 7. 当前本地状态与使用边界

本次检查中，OptimusKG 自己的 `data` 目录只有 `.gitkeep`，未发现完整构建产物。在 NeuroGRA 的 `code`、`configs`、`scripts`、`frontend` 中未搜到直接调用 OptimusKG 的代码。

schema 定义某个字段，不代表每条记录都填有值。缺少输入资源、ID 映射失败、来源覆盖差异和部分节点由关系端点生成，都可能影响结果。

代码按 Biolink/BioCypher 配置组织语义，但 Silver/Parquet 中仍使用自身的 3 字母类型代码和大写关系枚举；不能直接将其当成无需转换的 Biolink CURIE 表。

## 8. 关键源码索引

- [项目说明](../../OptimusKG/OptimusKG/README.md)
- [流水线注册](../../OptimusKG/OptimusKG/optimuskg/pipeline_registry.py)
- [实体、关系、来源和冲突枚举](../../OptimusKG/OptimusKG/optimuskg/pipelines/silver/nodes/constants.py)
- [节点 catalog](../../OptimusKG/OptimusKG/conf/base/catalog/silver/nodes)
- [边 catalog](../../OptimusKG/OptimusKG/conf/base/catalog/silver/edges)
- [疾病—基因构造](../../OptimusKG/OptimusKG/optimuskg/pipelines/silver/nodes/edges/disease_gene.py)
- [Gold 导出](../../OptimusKG/OptimusKG/optimuskg/pipelines/gold/nodes/export_kg.py)
- [Parquet 属性编码](../../OptimusKG/OptimusKG/optimuskg/pipelines/gold/export_formats/parquet.py)
- [导出配置](../../OptimusKG/OptimusKG/conf/base/parameters.yml)
- [质量检查](../../OptimusKG/OptimusKG/optimuskg/hooks/quality_checks_hooks.py)
- [下载与空输入占位](../../OptimusKG/OptimusKG/optimuskg/hooks/origin/origin_hooks.py)
- [Python 客户端 API](../../OptimusKG/OptimusKG/packages/optimuskg/src/optimuskg/api.py)
- [NetworkX 构造](../../OptimusKG/OptimusKG/packages/optimuskg/src/optimuskg/_graph.py)
- [MCP 服务](../../OptimusKG/OptimusKG/mcpb/src/optimuskg_mcp/server.py)

## 附录 A：全部节点属性字段字典

以下从本地 Silver catalog 的 `properties` schema 提取。`Struct` 表示嵌套对象，`List[Struct]` 表示对象列表，`[]` 表示列表元素；`Int16/Int32/Int64/UInt32` 和 `Float32/Float64` 为 Polars 数据类型。字段的 null 值和实际填充率需通过构建后的数据确认。共享 schema 的类型合并展示。

### A.1 ANA (anatomy)、BPO (biological_process)、CCO (cellular_component)、MFN (molecular_function)

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.name` | `String` |
| `properties.definition` | `String` |
| `properties.xrefs` | `List[String]` |
| `properties.synonyms` | `List[String]` |
| `properties.ontology` | `Struct` |
| `properties.ontology.description` | `String` |
| `properties.ontology.title` | `String` |
| `properties.ontology.license` | `String` |
| `properties.ontology.version` | `String` |

### A.2 DIS (disease)

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.name` | `String` |
| `properties.description` | `String` |
| `properties.code` | `String` |
| `properties.xrefs` | `List[String]` |
| `properties.exact_synonyms` | `List[String]` |
| `properties.related_synonyms` | `List[String]` |
| `properties.narrow_synonyms` | `List[String]` |
| `properties.broad_synonyms` | `List[String]` |
| `properties.obsolete_terms` | `List[String]` |
| `properties.obsolete_xrefs` | `List[String]` |
| `properties.therapeutic_areas` | `List[String]` |
| `properties.concept_ids` | `List[String]` |
| `properties.concept_names` | `List[String]` |
| `properties.umls_cui` | `String` |
| `properties.snomed_full_names` | `List[String]` |
| `properties.cui_semantic_type` | `String` |
| `properties.snomed_concept_ids` | `List[String]` |

### A.3 DRG (drug)

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.name` | `String` |
| `properties.inchi_key` | `String` |
| `properties.type` | `String` |
| `properties.synonyms` | `List[String]` |
| `properties.description` | `String` |
| `properties.accession_numbers` | `List[String]` |
| `properties.canonical_smiles` | `String` |
| `properties.chemical_abstracts_service_number` | `String` |
| `properties.unique_ingredient_identifier` | `String` |
| `properties.black_box_warning` | `Boolean` |
| `properties.year_of_first_approval` | `Int64` |
| `properties.maximum_clinical_trial_phase` | `Float64` |
| `properties.has_been_withdrawn` | `Boolean` |
| `properties.is_approved` | `Boolean` |
| `properties.trade_names` | `List[String]` |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.source_ids` | `List[String]` |
| `properties.cd_id` | `String` |
| `properties.cd_formula` | `String` |
| `properties.cd_mol_weight` | `Float64` |
| `properties.calculated_log_p` | `Float64` |
| `properties.alogs` | `Float64` |
| `properties.tpsa` | `Float64` |
| `properties.lipinski` | `Float64` |
| `properties.number_of_formulations` | `Int32` |
| `properties.mol_file_base64` | `String` |
| `properties.mol_image_base64` | `String` |
| `properties.mrdef` | `String` |
| `properties.enhanced_stereo` | `Boolean` |
| `properties.aromatic_carbons` | `Int32` |
| `properties.sp3_count` | `Int32` |
| `properties.sp2_count` | `Int32` |
| `properties.sp_count` | `Int32` |
| `properties.halogen_count` | `Int32` |
| `properties.hetero_sp2_count` | `Int32` |
| `properties.rotatable_bonds` | `Int32` |
| `properties.o_n` | `Int32` |
| `properties.oh_nh` | `Int32` |
| `properties.inchi` | `String` |
| `properties.rgb` | `Float64` |
| `properties.fda_labels` | `Int32` |
| `properties.status` | `String` |
| `properties.struct_id` | `String` |

### A.4 EXP (exposure)

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.name` | `String` |
| `properties.source_categories` | `List[String]` |
| `properties.source_details` | `String` |

### A.5 GEN (gene)

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.symbol` | `String` |
| `properties.biotype` | `String` |
| `properties.transcript_ids` | `List[String]` |
| `properties.canonical_transcript` | `Struct` |
| `properties.canonical_transcript.id` | `String` |
| `properties.canonical_transcript.chromosome` | `String` |
| `properties.canonical_transcript.start` | `Int64` |
| `properties.canonical_transcript.end` | `Int64` |
| `properties.canonical_transcript.strand` | `String` |
| `properties.canonical_exons` | `List[String]` |
| `properties.genomic_location` | `Struct` |
| `properties.genomic_location.chromosome` | `String` |
| `properties.genomic_location.start` | `Int64` |
| `properties.genomic_location.end` | `Int64` |
| `properties.genomic_location.strand` | `Int32` |
| `properties.alternative_genes` | `List[String]` |
| `properties.name` | `String` |
| `properties.hallmarks_attributes` | `List[Struct]` |
| `properties.hallmarks_attributes[].pmid` | `Int64` |
| `properties.hallmarks_attributes[].description` | `String` |
| `properties.hallmarks_attributes[].attribute_name` | `String` |
| `properties.cancer_hallmarks` | `List[Struct]` |
| `properties.cancer_hallmarks[].pmid` | `Int64` |
| `properties.cancer_hallmarks[].description` | `String` |
| `properties.cancer_hallmarks[].impact` | `String` |
| `properties.cancer_hallmarks[].label` | `String` |
| `properties.synonyms` | `List[Struct]` |
| `properties.synonyms[].label` | `String` |
| `properties.synonyms[].source` | `String` |
| `properties.symbol_synonyms` | `List[Struct]` |
| `properties.symbol_synonyms[].label` | `String` |
| `properties.symbol_synonyms[].source` | `String` |
| `properties.name_synonyms` | `List[Struct]` |
| `properties.name_synonyms[].label` | `String` |
| `properties.name_synonyms[].source` | `String` |
| `properties.function_descriptions` | `List[String]` |
| `properties.subcellular_locations` | `List[Struct]` |
| `properties.subcellular_locations[].location` | `String` |
| `properties.subcellular_locations[].source` | `String` |
| `properties.subcellular_locations[].term_sl` | `String` |
| `properties.subcellular_locations[].label_sl` | `String` |
| `properties.target_class` | `List[Struct]` |
| `properties.target_class[].id` | `Int64` |
| `properties.target_class[].label` | `String` |
| `properties.target_class[].level` | `String` |
| `properties.obsolete_symbols` | `List[Struct]` |
| `properties.obsolete_symbols[].label` | `String` |
| `properties.obsolete_symbols[].source` | `String` |
| `properties.obsolete_names` | `List[Struct]` |
| `properties.obsolete_names[].label` | `String` |
| `properties.obsolete_names[].source` | `String` |
| `properties.constraint_scores` | `List[Struct]` |
| `properties.constraint_scores[].constraint_type` | `String` |
| `properties.constraint_scores[].score` | `Float32` |
| `properties.constraint_scores[].exp` | `Float32` |
| `properties.constraint_scores[].obs` | `Int32` |
| `properties.constraint_scores[].oe` | `Float32` |
| `properties.constraint_scores[].oe_lower` | `Float32` |
| `properties.constraint_scores[].oe_upper` | `Float32` |
| `properties.constraint_scores[].upper_rank` | `Int32` |
| `properties.constraint_scores[].upper_bin` | `Int32` |
| `properties.constraint_scores[].upper_bin6` | `Int32` |
| `properties.target_enabling_package` | `Struct` |
| `properties.target_enabling_package.target_from_source_id` | `String` |
| `properties.target_enabling_package.description` | `String` |
| `properties.target_enabling_package.therapeutic_area` | `String` |
| `properties.target_enabling_package.url` | `String` |
| `properties.associated_proteins` | `List[Struct]` |
| `properties.associated_proteins[].id` | `String` |
| `properties.associated_proteins[].source` | `String` |
| `properties.xrefs` | `List[Struct]` |
| `properties.xrefs[].id` | `String` |
| `properties.xrefs[].source` | `String` |
| `properties.chemical_probes` | `List[Struct]` |
| `properties.chemical_probes[].control` | `String` |
| `properties.chemical_probes[].drug_id` | `String` |
| `properties.chemical_probes[].id` | `String` |
| `properties.chemical_probes[].is_high_quality` | `Boolean` |
| `properties.chemical_probes[].mechanism_of_action` | `List[String]` |
| `properties.chemical_probes[].origin` | `List[String]` |
| `properties.chemical_probes[].probe_miner_score` | `Int64` |
| `properties.chemical_probes[].probes_drugs_score` | `Int64` |
| `properties.chemical_probes[].score_in_cells` | `Int64` |
| `properties.chemical_probes[].score_in_organisms` | `Int64` |
| `properties.chemical_probes[].target_from_source_id` | `String` |
| `properties.chemical_probes[].urls` | `List[Struct]` |
| `properties.chemical_probes[].urls[].nice_name` | `String` |
| `properties.chemical_probes[].urls[].url` | `String` |
| `properties.homologues` | `List[Struct]` |
| `properties.homologues[].species_id` | `String` |
| `properties.homologues[].species_name` | `String` |
| `properties.homologues[].homology_type` | `String` |
| `properties.homologues[].target_gene_id` | `String` |
| `properties.homologues[].is_high_confidence` | `String` |
| `properties.homologues[].target_gene_symbol` | `String` |
| `properties.homologues[].query_percentage_identity` | `Float64` |
| `properties.homologues[].target_percentage_identity` | `Float64` |
| `properties.homologues[].priority` | `Int32` |
| `properties.tractability` | `List[Struct]` |
| `properties.tractability[].modality` | `String` |
| `properties.tractability[].id` | `String` |
| `properties.tractability[].value` | `Boolean` |
| `properties.safety_liabilities` | `List[Struct]` |
| `properties.safety_liabilities[].event` | `String` |
| `properties.safety_liabilities[].event_id` | `String` |
| `properties.safety_liabilities[].effects` | `List[Struct]` |
| `properties.safety_liabilities[].effects[].direction` | `String` |
| `properties.safety_liabilities[].effects[].dosing` | `String` |
| `properties.safety_liabilities[].biosamples` | `List[Struct]` |
| `properties.safety_liabilities[].biosamples[].cell_format` | `String` |
| `properties.safety_liabilities[].biosamples[].cell_label` | `String` |
| `properties.safety_liabilities[].biosamples[].tissue_id` | `String` |
| `properties.safety_liabilities[].biosamples[].tissue_label` | `String` |
| `properties.safety_liabilities[].datasource` | `String` |
| `properties.safety_liabilities[].literature` | `String` |
| `properties.safety_liabilities[].url` | `String` |
| `properties.safety_liabilities[].studies` | `List[Struct]` |
| `properties.safety_liabilities[].studies[].description` | `String` |
| `properties.safety_liabilities[].studies[].name` | `String` |
| `properties.safety_liabilities[].studies[].type` | `String` |
| `properties.transcription_start_site` | `Int64` |

### A.6 PWY (pathway)

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.name` | `String` |
| `properties.species` | `String` |

### A.7 PHE (phenotype)

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.name` | `String` |
| `properties.description` | `String` |
| `properties.code` | `String` |
| `properties.xrefs` | `List[String]` |
| `properties.exact_synonyms` | `List[String]` |
| `properties.related_synonyms` | `List[String]` |
| `properties.narrow_synonyms` | `List[String]` |
| `properties.broad_synonyms` | `List[String]` |
| `properties.obsolete_terms` | `List[String]` |
| `properties.obsolete_xrefs` | `List[String]` |
| `properties.type` | `String` |
| `properties.ontology` | `Struct` |
| `properties.ontology.description` | `String` |
| `properties.ontology.title` | `String` |
| `properties.ontology.license` | `String` |
| `properties.ontology.version` | `String` |
| `properties.concept_ids` | `List[String]` |
| `properties.concept_names` | `List[String]` |
| `properties.umls_cui` | `String` |
| `properties.snomed_full_names` | `List[String]` |
| `properties.cui_semantic_type` | `String` |
| `properties.snomed_concept_ids` | `List[String]` |

## 附录 B：全部边属性字段字典

这里列出各类边的 `properties`。所有边的顶层公共字段已在第 5.1 节说明。相同 schema 的边合并展示；各组仍列出全部对应文件名，覆盖 27 类边。

### B.1 `anatomy_anatomy`、`biological_process_biological_process`、`cellular_component_cellular_component`、`disease_disease`、`gene_gene`、`molecular_function_molecular_function`、`pathway_gene`、`pathway_pathway`、`phenotype_phenotype`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |

### B.2 `anatomy_gene`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.expression_rank` | `Int32` |
| `properties.call_quality` | `String` |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |

### B.3 `biological_process_gene`、`cellular_component_gene`、`molecular_function_gene`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.evidence` | `List[String]` |
| `properties.gene_product` | `List[String]` |
| `properties.eco_ids` | `List[String]` |

### B.4 `disease_gene`、`phenotype_gene`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.evidence_score` | `Float64` |
| `properties.evidence_count` | `Int64` |
| `properties.disease_specificity_index` | `Float64` |
| `properties.disease_pleiotropy_index` | `Float64` |
| `properties.evidence_index` | `Float64` |
| `properties.disgenet_score` | `Float64` |
| `properties.year_initial` | `String` |
| `properties.year_final` | `String` |
| `properties.number_of_pmids` | `Int16` |
| `properties.number_of_snps` | `Int16` |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |

### B.5 `disease_phenotype`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.aspect` | `List[String]` |
| `properties.bio_curation` | `List[String]` |
| `properties.evidence_type` | `List[String]` |
| `properties.frequency` | `List[String]` |
| `properties.modifiers` | `List[String]` |
| `properties.onset` | `List[String]` |
| `properties.qualifier_not` | `Boolean` |
| `properties.references` | `List[String]` |
| `properties.sexes` | `List[String]` |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.relation_assertions` | `List[Struct]` |
| `properties.relation_assertions[].source` | `String` |
| `properties.relation_assertions[].relation` | `String` |
| `properties.relation_conflict` | `Boolean` |

### B.6 `drug_biological_process`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.reference_ids` | `List[String]` |
| `properties.highest_clinical_trial_phase` | `Float64` |

### B.7 `drug_disease`、`drug_phenotype`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.structure_id` | `String` |
| `properties.drug_disease_id` | `String` |
| `properties.reference_ids` | `List[String]` |
| `properties.highest_clinical_trial_phase` | `Float64` |
| `properties.relation_assertions` | `List[Struct]` |
| `properties.relation_assertions[].source` | `String` |
| `properties.relation_assertions[].relation` | `String` |
| `properties.relation_conflict` | `Boolean` |

### B.8 `drug_drug`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.interaction_description` | `String` |
| `properties.relation_assertions` | `List[Struct]` |
| `properties.relation_assertions[].source` | `String` |
| `properties.relation_assertions[].relation` | `String` |
| `properties.relation_conflict` | `Boolean` |

### B.9 `drug_gene`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.source_ids` | `List[String]` |
| `properties.source_urls` | `List[String]` |
| `properties.mechanisms_of_action` | `List[String]` |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.relation_assertions` | `List[Struct]` |
| `properties.relation_assertions[].source` | `String` |
| `properties.relation_assertions[].relation` | `String` |
| `properties.relation_conflict` | `Boolean` |

### B.10 `exposure_biological_process`、`exposure_cellular_component`、`exposure_disease`、`exposure_exposure`、`exposure_gene`、`exposure_molecular_function`

| 属性路径 | 数据类型 |
| --- | --- |
| `properties.sources` | `Struct` |
| `properties.sources.direct` | `List[String]` |
| `properties.sources.indirect` | `List[String]` |
| `properties.evidence_count` | `UInt32` |
| `properties.number_of_receptors` | `Int64` |
| `properties.receptors` | `List[String]` |
| `properties.receptor_notes` | `List[String]` |
| `properties.smoking_statuses` | `List[String]` |
| `properties.age_entries` | `UInt32` |
| `properties.age_range_values` | `List[String]` |
| `properties.age_mean_values` | `List[String]` |
| `properties.age_median_values` | `List[String]` |
| `properties.age_point_values` | `List[String]` |
| `properties.age_open_range_values` | `List[String]` |
| `properties.sexes` | `List[String]` |
| `properties.races` | `List[String]` |
| `properties.methods` | `List[String]` |
| `properties.detection_limit` | `List[String]` |
| `properties.detection_limit_uom` | `List[String]` |
| `properties.detection_frequency` | `List[String]` |
| `properties.mediums` | `List[String]` |
| `properties.assay_notes` | `List[String]` |
| `properties.study_countries` | `List[String]` |
| `properties.states_or_provinces` | `List[String]` |
| `properties.city_town_region_areas` | `List[String]` |
| `properties.exposure_event_notes` | `List[String]` |
| `properties.outcome_relationships` | `List[String]` |
| `properties.exposure_outcome_notes` | `List[String]` |
| `properties.references` | `List[String]` |
| `properties.associated_study_titles` | `List[String]` |
| `properties.enrollment_start_years` | `List[String]` |
| `properties.enrollment_end_years` | `List[String]` |
| `properties.study_factors` | `List[String]` |
