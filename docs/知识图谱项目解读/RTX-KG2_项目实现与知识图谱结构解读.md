# RTX-KG2：项目实现与知识图谱结构解读

解读日期：2026-10-03。依据：本地 `RTX-KG2/RTX-KG2` 的 README、构建脚本、映射配置、归一化和融合代码。本文重点说明工程实现与数据结构；图谱规模引用仓库版本说明，尚未运行全量构建或独立验证论文实验。

## 1. 项目实现了什么

RTX-KG2 是面向转化生物医学的知识图谱构建系统，为 ARAX 等生物医学推理系统提供知识底座。它从数据库、本体和已有文献抽取结果中获取实体及关系，再进行语义标准化、标识归一化和导出。

本仓库主要实现数据工程：获取数据、转换子图、合并、标准化、归一化、融合、统计和部署辅助。ARAX 的查询与推理引擎在另一个项目中；本仓库接入的 SemMedDB 是已有的文献关系抽取结果。

主要功能包括：

1. 获取并解析数据库、XML、TSV、SQL dump 和本体。
2. 为各来源构造统一字段的节点和边。
3. 合并子图，处理同 ID 节点与缺少端点的边。
4. 将不同来源关系映射为 Biolink 谓词，必要时反转方向或过滤。
5. 使用本地 Babel SQLite 统一同义标识。
6. 按 Babel 融合簇进一步融合部分基因—蛋白和药物—化学实体。
7. 输出 JSONL、TSV、压缩包和统计报告，并提供 Neo4j、mediKanren 导出工具。

### 1.1 主要来源

当前主流程涉及 UMLS、本体集合、SemMedDB、ChEMBL、DrugBank、DrugCentral、DGIdb、NCBIGene、Ensembl、UniProtKB、Reactome、GO 注释、IntAct、HMDB、JensenLab、KEGG、UniChem、UNII、miRBase、SMPDB、ClinicalTrialsKG 和 DrugApprovalsKG 等。

不能仅凭目录中有转换脚本就认定该来源仍参与构图。例如 DisGeNET 脚本仍存在，但 `Snakefile-post-etl` 的合并输入已经将其注释，2.10.3 版本说明也列出了它的移除。

### 1.2 目录职责

| 目录或文件 | 职责 |
| --- | --- |
| `extract` | 下载和提取来源数据 |
| `convert` | 将各来源转换为 KG2 节点和边 |
| `maps` | 标识、类型、关系、知识来源及知识性质映射 |
| `process` | 合并、过滤、归一化、融合、统计、格式转换 |
| `build` | Snakemake 任务编排、打包和上传 |
| `validate` | 校验配置、CURIE、Biolink 类型与关系映射 |
| `neo4j` | Neo4j 安装、TSV 导入和索引 |
| `mediKanren` | mediKanren 格式转换和索引辅助 |
| `kg2_util.py` | 公共字段、类型、节点/边构造和 IO 工具 |
| `master-config.shinc` | 路径、版本、数据映射和 AWS 配置 |

## 2. 构建流程与产物阶段

构建使用 Snakemake、Bash 和 Python。不要把仓库中的 `build` 目录误认为已生成的图数据目录；它主要放构建源码，产物默认写入 Linux 用户目录下的 `~/kg2-build`。

```text
数据提取 → 各来源转换 → 合并子图
                         ↓
关系简化 / Biolink 映射 / 来源标准化
                         ↓
             Babel 节点和边归一化
                         ↓
       JSONL、TSV、精简文件、统计、压缩包

归一化结果 → 单独融合脚本 → 融合后的节点和边
```

### 2.1 原始转换和合并

各转换器调用 `make_node()`、`make_edge()` 构造基础记录。`merge_graphs.py` 按节点 ID 合并属性，对边按已有 ID 去重，并将缺少有效端点的边记录到孤立边输出。

此时不同数据库中描述同一实体的不同 ID 仍可能共存；它们要到后续 Babel 归一化阶段才按等价标识处理。

### 2.2 关系标准化：Simplify

`filter_kg_and_remap_predicates.py` 使用 `predicate-remap.yaml` 将来源关系映射到 Biolink。规则包括 `keep`、`invert`、`delete`，并可补充关系限定符。

同一步还将来源映射到 `infores:` 标识，按来源补充 `knowledge_level` 和 `agent_type`，过滤黑名单关系和部分自连接。当前 `run-simplify.sh` 显式使用 `--dropNegated` 删除否定边。

### 2.3 标识归一化：Normalize

`kg2pre_to_kg2c_nodes.py` 和 `kg2pre_to_kg2c_edges.py` 使用 `stitch_proj.local_babel` 查询本地 Babel SQLite 数据库：

- 节点映射到首选 ID、名称、类型和同义标识，合并文献等信息。
- 边的 `subject`、`object` 改写为首选 ID，并保留原始边 ID 的追溯信息。
- 无法完成映射的节点或边会被跳过或记录，因此归一化会影响图的覆盖范围。

### 2.4 跨类型融合：Conflate

`conflate_kg2c.py` 根据 Babel 融合簇进一步合并符合条件的实体，典型情况是基因与蛋白、药物与化学实体。它合并名称、类别、同义标识和文献，改写边端点，删除融合产生的自连接。

这种跨类型融合是下游建模选择。融合后，原来分开的基因与蛋白可能不再作为两个独立实体，因此应结合主要 `category` 和 `all_categories` 理解节点含义。

**文档与实际接线差异：** 2.10.3 版本说明称归一化和融合已经统一到主构建流程。但当前本地 `Snakefile-post-etl` 和 `Snakefile-finish` 明确接入的是节点/边归一化；融合脚本由单独的 `run-conflate-kg2c.sh` 调用，未在已检查的主 Snakemake 依赖中发现自动执行融合。不能仅凭版本说明认定一次主流程运行会产生最终融合图。

### 2.5 各阶段不能共享同一个固定 schema

| 阶段 | 内容 | schema 特征 |
| --- | --- | --- |
| 原始转换图 | 来源节点和来源关系 | 原始类型通常为字符串，保留来源谓词和时间等 |
| 简化图 / KG2pre | 统一关系和来源 | 补充 Biolink 谓词、限定符、知识性质 |
| 归一化图 | Babel 首选实体 | 节点类别变为列表；重建节点和边字段 |
| 融合图 / KG2c | 进一步跨类型合并 | 重写端点、合并节点元数据、删空属性和自连接 |
| Slim | 字段精简版本 | 只保留选定的少数节点和边字段 |

仓库部分说明对 KG2pre、normalized、KG2c 的称呼混用。识别文件时应结合产物文件名、生成脚本和实际字段，而不是只看版本名称。

## 3. 知识图谱整体结构

RTX-KG2 是基于 Biolink 类型与谓词的异构、多重有向属性图：

```text
节点：ID + category + 名称、描述、来源等属性

边：subject ── predicate ──→ object
    + 限定符 + 来源 + 文献 + 知识性质
```

同一对实体可以具有不同谓词、不同来源或不同限定条件的多条边。原始边 ID 的构造包含端点、来源关系、限定符和主要来源；归一化代码为保留的每条边重新生成 UUID，并没有按所有归一化后的三元组做全局合并。

它不使用 OptimusKG 的 `GEN`、`DIS`、`DRG-GEN` 等代码。节点类型直接写为 `biolink:Gene`，边类型直接写为 `biolink:affects`。基础输出也没有 OptimusKG 那种统一的嵌套 `properties` 容器。

## 4. 节点类型

节点 `category` 来源于转换器、CURIE 类型映射以及归一化阶段的 Babel。没有固定的 10 类上限。

以下为本地代码和映射中涉及的主要类型；表中省略 `biolink:` 前缀。配置中的类型不是实际图谱节点计数，Babel 也可能引入或调整类别。

| 领域 | 类型 |
| --- | --- |
| 疾病与表现 | `Disease`、`PhenotypicFeature`、`BehavioralFeature` |
| 基因与核酸 | `Gene`、`GeneFamily`、`Transcript`、`Exon`、`MicroRNA`、`NucleicAcidEntity`、`RNAProduct`、`NoncodingRNAProduct` |
| 蛋白 | `Protein`、`ProteinFamily`、`ProteinDomain` |
| 药物与化学 | `Drug`、`SmallMolecule`、`ChemicalEntity`、`ChemicalMixture`、`MolecularEntity` |
| 生物过程与功能 | `BiologicalProcess`、`MolecularActivity`、`PhysiologicalProcess`、`PathologicalProcess`、`Pathway` |
| 解剖与细胞 | `AnatomicalEntity`、`Cell`、`CellLine`、`CellularComponent` |
| 生物与物种 | `OrganismTaxon`、`IndividualOrganism`、`OrganismalEntity`、`LifeStage` |
| 临床 | `Procedure`、`ClinicalIntervention`、`Treatment`、`Device` |
| 环境与地域 | `EnvironmentalFeature`、`EnvironmentalProcess`、`GeographicLocation` |
| 其他 | `MaterialSample`、`InformationContentEntity`、`RetrievalSource`、`Activity`、`Agent`、`BiologicalEntity`、`PhysicalEntity`、`Phenomenon`、`NamedThing` |

本体术语、知识来源和较抽象的概念也可以是节点，因此不能将所有节点都理解为具体的疾病、药物或基因实体。

## 5. 节点字段结构

### 5.1 原始节点公共字段

`make_node()` 定义如下基础字段，来源转换器再填充值：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | String | 唯一 CURIE 标识，如 `NCBIGene:7157` |
| `iri` | String / null | 完整 URI |
| `name` | String | 显示名称 |
| `full_name` | String | 完整名称 |
| `category` | String | 主要 Biolink 类型 |
| `category_label` | String | 类型的文本标签，如 `gene` |
| `description` | String / null | 描述 |
| `synonym` | List[String] | 来源别名或同义名称；部分历史来源还混入其他文本 |
| `publications` | List[String] | 相关文献 CURIE |
| `creation_date` | String / null | 来源创建日期 |
| `update_date` | String / null | 来源更新日期，格式不完全统一 |
| `deprecated` | Boolean | 是否废弃 |
| `replaced_by` | String / null | 替代实体 ID |
| `provided_by` | List[String] | 节点来源；标准化阶段映射为 infores |
| `has_biological_sequence` | String / null | 序列或化学结构字符串 |

`has_biological_sequence` 随来源变化，可能保存蛋白或核酸序列，也可能保存 SMILES，不能统一解释成蛋白序列。

部分转换器内部临时使用 `xrefs`。例如 UniProt 转换器先把它转成关联边，再删除该字段后写出节点；不能把这种中间变量直接列为所有最终节点都有的公共属性。

### 5.2 归一化后的节点字段

归一化脚本重建节点，主要写出以下字段。有值的可选项才会出现，后续融合还会删除部分空列表字段。

| 字段 | 类型 | 输出行为 |
| --- | --- | --- |
| `id` | String | Babel 首选 ID |
| `name` | String | Babel 名称，缺失时回退到原名称或 ID |
| `category` | List[String] | Babel 类型；后续融合要求主要类型列表只有一个元素 |
| `all_categories` | List[String] | 相关类型集合 |
| `synonym` | List[String] | 同义名称字符串 |
| `same_as` | List[String] | 同义实体 CURIE |
| `description` | String / null | 有值时保留描述 |
| `iri` | String | 有值时保留 URI |
| `publications` | List[String] | 合并支持文献 |
| `in_taxon` | Babel 返回值 | 针对基因/蛋白补充物种标识；本地脚本未显式转换其类型 |

关键区别是 `synonym` 保存名称，`same_as` 保存标识符。`all_categories` 用于补充类型信息，并不意味着主要 `category` 一定是多种独立实体类型。

当前归一化代码没有完整复制原始的 `provided_by`、日期、`deprecated`、`replaced_by`、`full_name` 和 `has_biological_sequence` 等字段。它们在原始图中存在，不代表在 normalized/conflated 图中也存在。

以下为使用占位 ID 的结构示意：

```json
{
  "id": "NCBIGene:EXAMPLE",
  "name": "示例基因",
  "category": ["biolink:Gene"],
  "all_categories": ["biolink:Gene"],
  "synonym": ["另一名称"],
  "same_as": ["ENSEMBL:EXAMPLE", "HGNC:EXAMPLE"],
  "description": "示例描述"
}
```

## 6. 边类型与限定符

### 6.1 关系类型由 predicate 表达

当前 `predicate-remap.yaml` 中有 72 个不同的 `core_predicate` 值。它们是本地映射配置的候选值，不等于实际图中出现的关系总数，也不是对 Biolink 全部谓词的枚举。

主要关系如下，完整配置值见附录 A：

| 用途 | 典型谓词，省略 `biolink:` |
| --- | --- |
| 治疗与用药 | `treats`、`applied_to_treat`、`treats_or_applied_or_studied_to_treat`、`contraindicated_in`、`has_side_effect` |
| 疾病关联 | `gene_associated_with_condition`、`associated_with`、`biomarker_for`、`predisposes_to_condition` |
| 因果与调控 | `affects`、`causes`、`regulates`、`contributes_to`、`disrupts` |
| 相互作用 | `interacts_with`、`physically_interacts_with`、`directly_physically_interacts_with`、`indirectly_physically_interacts_with` |
| 表型与组织 | `has_phenotype`、`expressed_in`、`located_in`、`disease_has_location`、`occurs_in` |
| 基因与蛋白 | `gene_product_of`、`transcribed_from`、`translates_to`、`homologous_to` |
| 功能与通路 | `enables`、`catalyzes`、`capable_of`、`has_participant`、`actively_involved_in` |
| 组成与代谢 | `has_part`、`has_member`、`has_input`、`has_output`、`has_metabolite`、`produces` |
| 层级与对应 | `subclass_of`、`same_as`、`exact_match`、`close_match`、`broad_match` |
| 其他 | `related_to`、`coexists_with`、`correlated_with`、`chemically_similar_to`、`precedes` |

### 6.2 限定符表达更精确的关系

例如本地 ChEMBL 激动/拮抗关系映射，可表示为：

```text
药物 ── biolink:affects ──→ 靶点
qualified_predicate = biolink:causes
object_aspect_qualifier = activity
object_direction_qualifier = decreased
```

这表达药物导致靶点活动降低。起点终点定义图的箭头；`decreased` 定义生物学变化方向，两者含义不同。

本地限定符配置值包括：

- 影响方面：`abundance`、`activity`、`activity_or_abundance`、`degradation`、`expression`、`localization`、`molecular_modification`、`stability`、`synthesis`。
- 变化方向：`decreased`、`increased`、`downregulated`、`upregulated`。

RTX-KG2 的公共边结构没有 `undirected` 布尔字段；对称关系是否可双向遍历，应依据谓词语义及导出形式处理。

## 7. 边字段结构

### 7.1 原始与简化图字段

| 字段 | 类型 | 含义与阶段 |
| --- | --- | --- |
| `id` | String | 原始由端点、来源谓词、限定符和来源拼接生成 |
| `subject` | String | 起点实体 ID |
| `object` | String | 终点实体 ID |
| `source_predicate` | String | 来源数据库的原始关系 CURIE |
| `relation_label` | String | 原始关系文本；反转时可加 `INVERTED:` |
| `predicate` | String / null | 标准 Biolink 谓词；转换初期为空，Simplify 填充 |
| `predicate_label` | String | Simplify 写入的关系文本标签，不应替代标准谓词进行判断 |
| `qualified_predicate` | String / null | 更具体的限定关系谓词 |
| `object_aspect_qualifier` | String / null | 对象受影响的方面 |
| `object_direction_qualifier` | String / null | 对象变化方向 |
| `primary_knowledge_source` | String | 主要来源；标准化后通常为 `infores:...` |
| `knowledge_level` | String | Simplify 按来源配置补充的知识性质 |
| `agent_type` | String | Simplify 按来源配置补充的生成方式 |
| `publications` | List[String] | 支持关系的文献标识 |
| `publications_info` | Object | 按文献 ID 组织的详细证据信息 |
| `negated` | Boolean | 是否是否定断言；主流程在 Simplify 删除否定边 |
| `domain_range_exclusion` | Boolean | 来源转换中的语义排除标记 |
| `update_date` | String / null | 来源更新时间 |

当前代码中 `primary_knowledge_source` 为字符串，README 的列表描述不能直接套用。限定符的实际字段名是 `object_aspect_qualifier` 和 `object_direction_qualifier`，不是部分旧文档中的 `qualified_object_aspect` / `qualified_object_direction`。

### 7.2 publications_info 的嵌套结构

SemMedDB 转换器保留文献抽取证据。以下是结构示意，不是真实记录：

```json
{
  "publications": ["PMID:EXAMPLE"],
  "publications_info": {
    "PMID:EXAMPLE": {
      "publication date": "日期字符串",
      "sentence": "支持该关系的原文句子",
      "subject score": "评分字符串",
      "object score": "评分字符串"
    }
  }
}
```

内层键名确实包含空格。`sentence` 在 SemMedDB 边中可以有值；其他来源不保证提供。不同来源评分的含义和尺度不统一，不能直接把它们当成统一边权重。

### 7.3 knowledge_level 和 agent_type

| 字段 | 当前配置值 | 含义 |
| --- | --- | --- |
| `knowledge_level` | `knowledge_assertion` | 知识断言 |
| `knowledge_level` | `logical_entailment` | 逻辑蕴含 |
| `knowledge_level` | `prediction` | 预测或抽取性质的知识 |
| `agent_type` | `manual_agent` | 人工整理 |
| `agent_type` | `automated_agent` | 自动处理 |
| `agent_type` | `text_mining_agent` | 文本挖掘 |
| `agent_type` | `manual_validation_of_automated_agent` | 自动生成后人工验证 |

这些值主要按数据源赋予，不等于每条边都进行了独立评估。公共 schema 没有统一 `weight` 或 `confidence` 字段。

### 7.4 归一化后实际保留的边字段

`kg2pre_to_kg2c_edges.py` 明确复制或构造：

```text
id: String，重新生成 UUID
kg2_ids: List[String]，原始边 ID
subject: String，Babel 首选起点 ID
object: String，Babel 首选终点 ID
predicate: String
primary_knowledge_source: String
knowledge_level: String
agent_type: String
domain_range_exclusion: Boolean
qualified_predicate: String，可选
object_aspect_qualifier: String，可选
object_direction_qualifier: String，可选
publications: List[String]，可选
publications_info: Object，可选
```

它不在保留列表中复制 `source_predicate`、`relation_label`、`predicate_label`、`update_date` 和 `negated`。其中原始关系只能通过 `kg2_ids` 回到对应的 KG2pre 数据查找，不能指望 normalized 文件自己携带所有原始信息。

以下为结构示意：

```json
{
  "id": "示例UUID",
  "kg2_ids": ["示例原始边ID"],
  "subject": "CHEBI:EXAMPLE",
  "object": "UniProtKB:EXAMPLE",
  "predicate": "biolink:affects",
  "qualified_predicate": "biolink:causes",
  "object_aspect_qualifier": "activity",
  "object_direction_qualifier": "decreased",
  "primary_knowledge_source": "infores:chembl",
  "knowledge_level": "knowledge_assertion",
  "agent_type": "manual_agent",
  "domain_range_exclusion": false
}
```

## 8. 存储、精简版本与使用方式

当前主要使用节点和边分别存放的 JSON Lines：每行一个 JSON 对象，常压缩为 `.jsonl.gz`。旧文档中的单个 `{build, nodes, edges}` JSON 大对象不能直接代表所有当前产物的布局。

同时支持 TSV 节点表、边表和各自的 header 文件，可导入 Neo4j；mediKanren 目录提供相应格式转换。

`slim_kg2.py` 仅保留输入中存在的下列字段：

| 对象 | 字段 |
| --- | --- |
| 节点 | `id`、`name`、`category`、`synonym`、`same_as` |
| 边 | `subject`、`object`、`predicate`、`primary_knowledge_source`、`qualified_predicate`、`object_aspect_qualifier`、`object_direction_qualifier` |

Slim 会丢失文献、详细证据、时间和知识性质字段，也不保留独立边 ID，不适合直接承担完整证据追溯。主流程的 Slim 输入为简化图，不能假定它自动包含只有归一化后才新增的字段。

## 9. 当前本地状态与实现限制

本地版本说明记录的 2.10.3 normalized 图约有 596 万节点、3676 万边，conflated 图约有 584 万节点、3628 万边。这是仓库版本文档中的发布统计，不能作为当前目录已经构建成功的证据。

本次检查未发现完整 JSONL/TSV 图数据或 Babel SQLite 数据库。在 NeuroGRA 的 `code`、`configs`、`scripts`、`frontend` 中也没有搜到对 RTX-KG2 的直接调用。

构建脚本面向 Ubuntu/Linux，路径、AWS 下载和数据库工具依赖较强。README 记录了约 128 GiB 内存、1 TiB 磁盘的构建环境要求；当前 Windows 源码目录并不等于已经具备运行环境。

其他与数据解释有关的限制包括：

- 类型和谓词配置不等于实际图中出现的类型统计。
- 部分来源受访问条件限制，缺少来源会改变图覆盖。
- Babel 无法映射的节点或边可能被排除。
- 归一化会舍弃一部分原始属性；跨类型融合还会改变实体边界。
- 当前边归一化使用 `uuid.uuid4()`，边 ID 不能保证重复运行时保持不变。
- 当前归一化与融合代码不提供同端点、同谓词的全局边折叠保证。

## 10. 关键源码索引

- [项目说明](../../RTX-KG2/RTX-KG2/README.md)
- [字段与基础构造函数](../../RTX-KG2/RTX-KG2/kg2_util.py)
- [构建入口](../../RTX-KG2/RTX-KG2/build/build-kg2-snakemake.sh)
- [后处理任务规则](../../RTX-KG2/RTX-KG2/build/Snakefile-post-etl)
- [结束规则](../../RTX-KG2/RTX-KG2/build/Snakefile-finish)
- [结束打包与上传](../../RTX-KG2/RTX-KG2/build/finish-snakemake.sh)
- [类型映射](../../RTX-KG2/RTX-KG2/maps/curies-to-categories.yaml)
- [关系映射](../../RTX-KG2/RTX-KG2/maps/predicate-remap.yaml)
- [知识性质映射](../../RTX-KG2/RTX-KG2/maps/knowledge-level-agent-type-map.yaml)
- [图合并](../../RTX-KG2/RTX-KG2/process/merge_graphs.py)
- [关系与来源标准化](../../RTX-KG2/RTX-KG2/process/filter_kg_and_remap_predicates.py)
- [节点归一化](../../RTX-KG2/RTX-KG2/process/kg2pre_to_kg2c_nodes.py)
- [边归一化](../../RTX-KG2/RTX-KG2/process/kg2pre_to_kg2c_edges.py)
- [跨类型融合](../../RTX-KG2/RTX-KG2/process/conflate_kg2c.py)
- [单独融合入口](../../RTX-KG2/RTX-KG2/process/run-conflate-kg2c.sh)
- [精简输出](../../RTX-KG2/RTX-KG2/process/slim_kg2.py)
- [SemMedDB 证据转换](../../RTX-KG2/RTX-KG2/convert/semmeddb_tuplelist_json_to_kg_jsonl.py)
- [版本与发布统计](../../RTX-KG2/RTX-KG2/docs/kg2-versions.md)

## 附录 A：本地关系映射中的 72 个核心谓词

从 `predicate-remap.yaml` 的 `core_predicate` 字段去重提取；这是配置候选集，不能据此推断实际发布图的边分布。每个值均含 `biolink:` 前缀。

| 序号 | 核心谓词 |
| --- | --- |
| 1 | `biolink:actively_involved_in` |
| 2 | `biolink:affects` |
| 3 | `biolink:ameliorates_condition` |
| 4 | `biolink:applied_to_treat` |
| 5 | `biolink:associated_with` |
| 6 | `biolink:beneficial_in_models_for` |
| 7 | `biolink:biomarker_for` |
| 8 | `biolink:broad_match` |
| 9 | `biolink:capable_of` |
| 10 | `biolink:catalyzes` |
| 11 | `biolink:causes` |
| 12 | `biolink:chemically_similar_to` |
| 13 | `biolink:close_match` |
| 14 | `biolink:coexists_with` |
| 15 | `biolink:colocalizes_with` |
| 16 | `biolink:composed_primarily_of` |
| 17 | `biolink:contraindicated_in` |
| 18 | `biolink:contributes_to` |
| 19 | `biolink:correlated_with` |
| 20 | `biolink:derives_from` |
| 21 | `biolink:develops_from` |
| 22 | `biolink:diagnoses` |
| 23 | `biolink:directly_physically_interacts_with` |
| 24 | `biolink:disease_has_basis_in` |
| 25 | `biolink:disease_has_location` |
| 26 | `biolink:disrupts` |
| 27 | `biolink:drug_regulatory_status_world_wide` |
| 28 | `biolink:enables` |
| 29 | `biolink:exacerbates_condition` |
| 30 | `biolink:exact_match` |
| 31 | `biolink:expressed_in` |
| 32 | `biolink:gene_associated_with_condition` |
| 33 | `biolink:gene_product_of` |
| 34 | `biolink:has_completed` |
| 35 | `biolink:has_decreased_amount` |
| 36 | `biolink:has_increased_amount` |
| 37 | `biolink:has_input` |
| 38 | `biolink:has_member` |
| 39 | `biolink:has_metabolite` |
| 40 | `biolink:has_molecular_consequence` |
| 41 | `biolink:has_not_completed` |
| 42 | `biolink:has_output` |
| 43 | `biolink:has_part` |
| 44 | `biolink:has_participant` |
| 45 | `biolink:has_phenotype` |
| 46 | `biolink:has_plasma_membrane_part` |
| 47 | `biolink:has_side_effect` |
| 48 | `biolink:homologous_to` |
| 49 | `biolink:in_taxon` |
| 50 | `biolink:indirectly_physically_interacts_with` |
| 51 | `biolink:interacts_with` |
| 52 | `biolink:is_sequence_variant_of` |
| 53 | `biolink:lacks_part` |
| 54 | `biolink:located_in` |
| 55 | `biolink:manifestation_of` |
| 56 | `biolink:mentions` |
| 57 | `biolink:model_of` |
| 58 | `biolink:occurs_in` |
| 59 | `biolink:opposite_of` |
| 60 | `biolink:overlaps` |
| 61 | `biolink:physically_interacts_with` |
| 62 | `biolink:precedes` |
| 63 | `biolink:predisposes_to_condition` |
| 64 | `biolink:preventative_for_condition` |
| 65 | `biolink:produces` |
| 66 | `biolink:regulates` |
| 67 | `biolink:related_to` |
| 68 | `biolink:same_as` |
| 69 | `biolink:subclass_of` |
| 70 | `biolink:temporally_related_to` |
| 71 | `biolink:treats` |
| 72 | `biolink:treats_or_applied_or_studied_to_treat` |

## 附录 B：本地类型映射与公共常量的候选类别

以下是 `curies-to-categories.yaml` 的映射值与 `kg2_util.py` 的 `BIOLINK_CATEGORY_*` 常量的并集。实际图的类别还受来源覆盖、Babel 和融合结果影响；此表不是发布图的实际 metagraph。

| 本地类型名称 | 对应 Biolink 类型形式 |
| --- | --- |
| `RNA product` | `biolink:RNAProduct` |
| `activity` | `biolink:Activity` |
| `agent` | `biolink:Agent` |
| `anatomical entity` | `biolink:AnatomicalEntity` |
| `behavioral feature` | `biolink:BehavioralFeature` |
| `biological entity` | `biolink:BiologicalEntity` |
| `biological process` | `biolink:BiologicalProcess` |
| `cell` | `biolink:Cell` |
| `cell line` | `biolink:CellLine` |
| `cellular component` | `biolink:CellularComponent` |
| `chemical entity` | `biolink:ChemicalEntity` |
| `chemical mixture` | `biolink:ChemicalMixture` |
| `clinical intervention` | `biolink:ClinicalIntervention` |
| `device` | `biolink:Device` |
| `disease` | `biolink:Disease` |
| `drug` | `biolink:Drug` |
| `environmental feature` | `biolink:EnvironmentalFeature` |
| `environmental process` | `biolink:EnvironmentalProcess` |
| `exon` | `biolink:Exon` |
| `gene` | `biolink:Gene` |
| `gene family` | `biolink:GeneFamily` |
| `geographic location` | `biolink:GeographicLocation` |
| `individual organism` | `biolink:IndividualOrganism` |
| `information content entity` | `biolink:InformationContentEntity` |
| `life stage` | `biolink:LifeStage` |
| `material sample` | `biolink:MaterialSample` |
| `microRNA` | `biolink:MicroRNA` |
| `molecular activity` | `biolink:MolecularActivity` |
| `molecular entity` | `biolink:MolecularEntity` |
| `named thing` | `biolink:NamedThing` |
| `noncoding RNA product` | `biolink:NoncodingRNAProduct` |
| `nucleic acid entity` | `biolink:NucleicAcidEntity` |
| `organism taxon` | `biolink:OrganismTaxon` |
| `organismal entity` | `biolink:OrganismalEntity` |
| `pathological process` | `biolink:PathologicalProcess` |
| `pathway` | `biolink:Pathway` |
| `phenomenon` | `biolink:Phenomenon` |
| `phenotypic feature` | `biolink:PhenotypicFeature` |
| `physical entity` | `biolink:PhysicalEntity` |
| `physiological process` | `biolink:PhysiologicalProcess` |
| `procedure` | `biolink:Procedure` |
| `protein` | `biolink:Protein` |
| `protein domain` | `biolink:ProteinDomain` |
| `protein family` | `biolink:ProteinFamily` |
| `retrieval source` | `biolink:RetrievalSource` |
| `small molecule` | `biolink:SmallMolecule` |
| `transcript` | `biolink:Transcript` |
| `treatment` | `biolink:Treatment` |
