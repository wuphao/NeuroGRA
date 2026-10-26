# 独立知识图谱模块

实现进度和未完成项见 [实施记录](../../../docs/知识图谱设计/知识图谱独立模块实施记录.md)。这是持续开发中的模块，不能将合成样例通过解释为临床正确性验证。

## 安装和模型

在项目根目录安装 `pip install -e ".[kg]"`。无需临床应用即可导入 `neurogra_kg`。

- 生成任务：本地 `qwen3.8:27b`。
- 嵌入任务：本地 `qwen3-embedding:0.6b`，本次真实探测为 1024 维。
- 配置：`configs/kg.local.yaml`；Neo4j 密码从 `NEUROGRA_KG_NEO4J_PASSWORD` 环境变量读取。
- 默认输出在 `output/kg`，数据和发布资源在 `data/kg`。

输入支持 PDF、结构化 JSON 和 DOCX。DOCX 直接读取 OOXML，保留正文顺序、标题层级、表格单元格和脚注；出处使用 `source_locator` 定位 XML 段落或表格，`page_kind=unavailable` 表示没有真实排版页码。嵌入对象、修订和暂不支持的内容会触发解析复核，不能默认为完整解析成功。

源码环境未安装项目时，在 PowerShell 中临时设置 `$env:PYTHONPATH = (Join-Path (Get-Location) 'code')`。

## 当前可运行命令

```powershell
python -m neurogra_kg.cli probe --config configs/kg.local.yaml
python -m neurogra_kg.cli build --config configs/kg.local.yaml --manifest code/neurogra_kg/examples/manifest.json
python -m neurogra_kg.cli inspect-build --build-id <构建ID>
python -m neurogra_kg.cli publish --build-id <构建ID>
python -m neurogra_kg.cli retrieve --request <查询JSON>
python -m neurogra_kg.cli inspect-evidence --evidence-id <证据ID>
python -m neurogra_kg.cli inspect-query --run-id <查询ID>
python -m neurogra_kg.cli resume-query --run-id <查询ID>
python -m neurogra_kg.cli train-complex --release-id <版本ID>
```

正式发布默认要求 Neo4j 可连接。开发样例可以在 SDK 配置中显式设置 `config.graph.provider = "memory"`；该模式不验证 Neo4j。

## SDK

```python
from neurogra_kg import KnowledgeGraphService
from neurogra_kg.contracts import BuildRequest, RetrievalRequest

with KnowledgeGraphService("configs/kg.local.yaml") as kg:
    result = kg.build(BuildRequest.model_validate(document_manifest))
    release = kg.publish(result.build_id)
    evidence = kg.retrieve(RetrievalRequest.model_validate(patient_query))
```

`RetrievalRequest` 必须给出患者分析时间 `reference_time`。缺失事实返回 UNKNOWN 与 DataRequest；不同发布版本、模型或模板的向量不能混用。

## 提示词与输出

`prompts/common.system.txt` 与 L0—L8 的 system/user 模板组成任务输入，每项有可检查的 JSON Schema。

L3 的模型输出采用扁平节点数组；代码校验引用、循环、未覆盖条件和否定后转换为内部条件树。L4 的模型核验和代码来源核验分开记录，未决条件不作为适用依据。

原文源记录只接受逐字引用。生成日志、缓存和患者查询结果只写入配置的本地运行目录，不作为仓库文件提交。

## 中英双语适配

`DocumentInput.language` 支持 `zh/en/mixed/und`，默认 `und`；可在构建 manifest 显式指定。已有文档和声明 JSON 可继续读取，旧发布和旧向量不被就地改写。

实体新增 `label_zh/label_en`，仍以已有 `name` 和代码生成 ID 管理身份；中英名称及缩写通过受控别名映射共享实体。声明和 TEXT 条件新增 `zh_annotation`，独立记录中文表达、验证状态及生成绑定。`SourceRef.quote` 始终保留原语言逐字引文。

`construction.localize_zh` 默认开启。L1—L4 先完成原文抽取与核验，再由 L7 生成中文表达、L8 核对一致性；已有纯中文表达可复用。中英混合表达也核验。ATOM 条件说明从标准字段、运算符、值与限制生成，条件树不由译文回写。每条需要转换的声明通常增加两次模型调用，格式修复及重试仍计入统一预算。

翻译须保留数字、否定、条件、方法、人群和证据强度；失败表达不进入中文索引，原文检索仍可用，构建 issues 和检索 warnings 保留失败信息。`verified` 仅表示中文表达通过代码约束及模型一致性核对，不代表人工审定或临床正确性。

声明索引采用 `bilingual_statement_v1`，将已验证中文表达、原表述及中英标签/别名合并到同一声明资源。新发布绑定模板与术语指纹；旧发布读取原有资源，不强制重建向量。患者字段、方法及受控字符串值共享映射；未知方法或人群仍返回 UNKNOWN。

八篇核心英文文献的构建输入为 `configs/kg.bilingual_core.manifest.json`。实际全文构建和正式发布需要运行本地模型及 Neo4j；本次代码改造没有自动重建或替换当前正式图谱。

```powershell
python -m neurogra_kg.cli build --config configs/kg.local.yaml --manifest configs/kg.bilingual_core.manifest.json
```

完整修改及验收记录见 `docs/知识图谱设计/知识图谱中英双语适配实施记录.md`。

## 工程检查

```powershell
python -m unittest discover -s code/neurogra_kg/tests -v
```

`examples/smoke.py` 需要先使用真实本地模型构建并发布合成样例，验证满足/未知/违反三种结果；它显式使用内存图，不能替代数据库集成验证。


查询运行写入 `output/kg/query_runs/<run_id>/checkpoint.json`，保存阶段、证据、已补查查询/缺口/上下文和调用消耗。`inspect_query` 可读进度，`resume_query` 从已完成阶段继续；原截止时间与调用预算不重置。相同 ID 的输入、发布、提示词、算法代码、模型或 ComplEx 产物变更时拒绝恢复，须创建新运行。操作系统文件锁阻止同一运行并发执行，并在进程退出后自动释放。

补查重新判定全部候选后才截取 top-k；证据排名下降不会消除缺口。新增证据按相关性评分，明确患者资料缺口不触发文献补查。重新抽取的声明仅在核心字段及原文位置相符时更新查询内条件，正式发布保持不可变。

L3/L4 显式保留人口/人群与方法限定；Schema 合法而来源、作用范围或逻辑不合法的输出移入 `llm_rejected/`，从成功缓存移除。L0 提取结果另按字段目录验证值类型。

生成 Schema 在请求时将来源 block_id 限定为输入中真实存在的 ID，减少模型抄写长标识错误；逐字引用、字符区间、条件归属和逻辑仍由代码核验。纯用途描述保存在 U，不重复充当 C。

模型不可用时，查询 SDK 仍运行可用检索通道并返回 degraded 与 warnings。自动 run_id 包含运行绑定；模型、代码或提示词变更会生成新运行。明确指定的运行 ID 仍要求绑定一致才能恢复。

旧 `neurogra.knowledge` 源码与失效配置已移至 `output/kg/legacy_archive`，归档附文件校验和；原始文档和旧数据未搬迁。当前安装包只包含新知识图谱算法实现。

最终实现与逐阶段工程证据见 [实现与验收说明](../../../docs/知识图谱设计/知识图谱模块实现与验收说明.md)。模型版本检查计入查询总时间预算。表格分片不跨越合并单元格的行范围；同一合并单元格的条件覆盖其全部行。
