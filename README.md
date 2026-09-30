# NeuroGRA

基于图谱增强 RAG 与多智能体协作的神经退行性疾病辅助诊疗系统研究项目。

本项目当前用于整理课题方案、论文材料、数据、代码和运行输出。目录保持简洁，避免把学习笔记、论文、运行结果和正式代码混在一起。

## 目录结构

```text
NeuroGRA/
├─ code/          # 项目代码
├─ data/          # 项目数据
├─ docs/          # 方案、设计文档、说明文档
│  └─ assets/     # docs 中使用的图片
├─ output/        # 程序运行产生的本地输出
├─ paper/         # 论文 PDF、阅读笔记、精读总结
├─ study/         # 个人学习资料，与本项目无关
├─ README.md
├─ pyproject.toml
├─ .gitignore
└─ .env.example
```

## 放置规则

详细规范见 [docs/项目文件放置规范.md](docs/项目文件放置规范.md)。

核心原则：

- 正式代码只放 `code/`。
- 项目数据只放 `data/`。
- 方案和说明只放 `docs/`。
- `docs` 文档引用的图片只放 `docs/assets/`。
- 运行时生成的普通结果放 `output/`。
- 项目运行时由 AI 生成的正文、诊断报告、回答内容，不放 `output/`，应按用途归入 `docs/`、`paper/精读总结/` 或单独确认位置。
- 论文资料放 `paper/`。
- 自己学习用的材料放 `study/`，不要被项目代码依赖。

## 开发环境

RWE 患者编号到智能体分析、知识检索和报告的入口见 [RWE 运行说明](docs/RWE患者ID到可追溯报告运行说明.md)。核心命令：`python -m neurogra.clinical.cli analyze-rwe --patient-id "<患者编号>"`。

建议使用 Python 3.11 或以上版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

`.env` 只保存在本地，不提交到版本库。

## 文档入口

- [总体设计与执行细则](docs/总结设计.md)
- [医学知识图谱综述](docs/医学知识图谱综述.docx)
- [神经退行性疾病研究综述](docs/神经退行性疾病人工智能辅助诊疗研究现状综述.docx)
- [知识构建详细算法](docs/算法零_医学文本到RAG知识库与条件化知识图谱构建.md)
- [鉴别证据检索详细算法](docs/算法一_鉴别证据检索详细设计.md)
- [证据状态复核详细算法](docs/算法二_证据状态引导复核详细设计.md)
- [项目文件放置规范](docs/项目文件放置规范.md)


## 在线 Agent 第一批基础能力（S01～S08）

已实现自由中文病例接入、资料盘点、结构化模型网关、共享检索接口与主 Agent 任务规划。当前入口为 `prepare`，运行到任务计划为止；专业子 Agent 分析、主子复查、最终诊断报告和 DiaMond 推理属于后续批次。

安装项目及文档解析可选依赖：`pip install -e ".[clinical]"`。影像元数据的 SimpleITK 文件读取适配为可选项 `clinical-imaging`；未安装时返回明确不可用状态。

在项目根目录执行（未做 editable install 时先设置 PYTHONPATH）：

```powershell
$env:PYTHONPATH = "$PWD/code"
python -m neurogra.clinical.cli probe --model-smoke
python -m neurogra.clinical.cli prepare --patient code/neurogra/clinical/tests/fixtures/narrative.json --query "AD 源性MCI 诊断"
python -m neurogra.clinical.cli search --query "AD 源性MCI 诊断" --term "阿尔茨海默病"
python -m neurogra.clinical.cli inspect-run --run-id <运行ID>
```

使用 `prepare --no-model` 可明确选择规则模式；不会将规则路由标成真实模型规划。输入只要求“患者ID”，其他中文字段任意；附件相对路径以 `configs/clinical.default.yaml` 的 `patient_data_root` 为根，默认 `data/clinical/patients`。需要访问其他目录时显式配置 `allowed_roots`。影像保留“模态、时间、路径”，没有适配能力不假装完成分析。

`prepare` 返回运行 ID、任务角色、模型/规则模式及 `output/clinical/<run_id>/preparation.json`。患者快照在 `data/clinical/`，SQLite 保存调用预算、事件、版本和结构化模型结果。不同 run 的快照独立保存。

当前真实验收：Ollama `qwen3.6:35b` 能执行资料识别及任务规划；BM25 能检索现有发布版。Neo4j 当前连接不可用，向量库尚未接入，结果如实标记 degraded/not_configured。Neo4j 查询适配已实现并用隔离驱动验证版本过滤，待服务可用后完成在线验证；图谱共现关系仅作为原文线索。

测试：

```powershell
python -m unittest discover -s code/neurogra/clinical/tests -v
python -m unittest discover -s code/neurogra/knowledge/tests -v
```

详细交付记录见[执行计划第 23 节](docs/NeuroGRA主Agent实施步骤与执行计划.md#23-第一批实施记录s01s08)，架构见[主子 Agent 设计](docs/NeuroGRA主Agent与子Agent协作详细设计.md)。

## 在线 Agent 第二批：病史分析、复查与报告

```powershell
$env:PYTHONPATH = "$PWD/code"
python -m neurogra.clinical.cli analyze --patient code/neurogra/clinical/tests/fixtures/history_only.json
python -m neurogra.clinical.cli inspect-run --run-id <运行ID>
python -m neurogra.clinical.cli resume --run-id <运行ID>
```

指定配置时将 `--config configs/clinical.default.yaml` 放在子命令之前。`analyze` 实际调用配置中的 Ollama 模型和本地发布知识库；没有伪造模型输出或检索结果的演示模式。当前实现病史子 Agent，其他专业任务明确标为未实现。报告写入 `docs/病例报告/<患者ID哈希>/<run_id>/report.{json,md}`，中间产物写入 `output/clinical/<run_id>/`。

复查最多两轮，允许子 Agent 纠正、澄清、有依据维持或保留未决；无可执行问题时不强行交互。最终汇总采用模型排序已验证主张的方式，避免自由生成新的诊断。`completed` 表示流程完成，不表示确诊；仅病史演示的判断状态为 `insufficient_data`。

`resume` 从已落盘的准备结果继续，保留原预算、截止时间、模型配置和知识发布版本；已完成任务不会重复运行。准备阶段尚未生成 `preparation.json` 的中断目前需要重新 `analyze`。详细接口与验收见 [第二批交付说明](docs/第二批文本主子Agent交付说明.md)。

## 在线 Agent 第三批：认知、检验和影像

认知、检验和影像报告 Agent 已接入同一规划、检索、复查和报告流程。可运行：

```powershell
python -m neurogra.clinical.cli analyze --patient code/neurogra/clinical/tests/fixtures/multispecialty.json
python -m neurogra.clinical.cli validate-diamond
```

DiaMond 的环境与模型权重已真实验证加载；raw 入口的预处理、RegBN 构造及训练状态恢复仍有阻断，分类工具默认返回 `blocked_validation`，不会生成未经验证的预测。详见 [第三批交付与验证记录](docs/第三批多专业Agent与DiaMond验证交付说明.md)。

## 第四批：全 Agent 验收与向量检索

四类子 Agent 已纳入统一复查、版本依赖和续跑验收。默认配置接入本地 BGE-M3 1024 维索引，与 BM25、S07 基础图谱查询按 RRF 融合。向量失败会降级，不要求复杂重排或条件算法先完成。

```powershell
python -m neurogra.clinical.cli build-vector-index
python -m neurogra.clinical.cli audit-run --run-id <运行ID> --require-all-agents
python -m neurogra.clinical.cli evaluate-retrieval --queries code/neurogra/clinical/tests/fixtures/retrieval_queries.json --top-k 5
```

索引构建后按输出设置 `vector.index_manifest`。`resume` 现已支持从已保存输入恢复准备阶段，保持原预算和截止时间。DiaMond 的 S12 一致性阻断仍保留。见 [第四批交付与真实验收](docs/第四批综合验收与向量检索交付说明.md)。

四轮代码审查后的修复已加入：影像复用与恢复检查当前输入/模型绑定，检索按任务截止时间终止独立工作进程，资料盘点按原始记录去重提交。影像绑定变化时保留历史并要求新建运行。实现细节、132 项测试及真实调用结果见 [审查修复与验收](docs/四轮代码审查修复与验收.md)。

新生成报告末尾增加“自然语言综合报告”，以一段中文概述患者情况、各项资料和未决问题，技术引用另存 JSON。复查未完成时仅概述事实和限制，不把未经复查的解释润色为确诊结论。为已有报告补写这一段可运行 `python -m neurogra.clinical.cli write-narrative --run-id <原运行ID>`；补写会新建有独立调用预算的写作运行，输出到原报告目录的 `narrative-<新运行ID>/`，不覆盖原始分析记录。此功能不解除此前复查输入超限或 DiaMond 验证阻断。
