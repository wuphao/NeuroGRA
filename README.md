# NeuroGRA

面向神经退行性疾病的多源患者资料分析项目。新的系统设计以提高辅助诊断和患者分析准确性为目标，由多智能体分析框架与共享医学知识服务组成，重点研究知识图谱与知识库构建，以及面向不同分析任务的知识检索。系统设计与当前代码实现状态分别说明。

## 当前入口

| 用途 | 文档 |
| --- | --- |
| 研究范围与系统方案 | [总体设计](docs/总体设计.md) |
| 图谱结构契约 | [知识与来源分层图谱 v0.3](docs/知识图谱设计/NeuroGRA_知识与来源分层图谱结构设计_v0.3.md) |
| 文献抽取与知识构建 | [算法零](docs/算法零_医学文本到RAG知识库与条件化知识图谱构建.md#12-本批神经退行性疾病文献的抽取实施设计) |
| 统一研究算法 | [多模态证据检索与缺口补偿 v1.0](docs/知识图谱设计/NeuroGRA_多模态证据检索与缺口补偿算法设计_v1.0.md) |
| 算法概览与研究归属 | [五步说明](docs/知识图谱设计/NeuroGRA_五步算法说明.md)、[来源与贡献边界](docs/知识图谱设计/NeuroGRA_算法研究来源与贡献边界_v1.0.md) |
| 临床工程与待完成事项 | [主子 Agent 协作设计](docs/NeuroGRA主Agent与子Agent协作详细设计.md) |
| RWE 患者分析 | [RWE 运行说明](docs/RWE患者ID到可追溯报告运行说明.md) |
| 本地 Web 工作台 | [Web 运行说明](docs/Web工作台运行说明.md) |
| 目录与版本管理 | [文件放置规范](docs/项目文件放置规范.md) |

开题材料在 `docs/开题报告/`，背景综述在 `docs/` 和 `docs/reviews/`，诊断文献与来源目录在 `docs/references/`。旧方案和分批交付说明已按清理记录删除，不再作为当前入口。

## 安装和运行

使用 Python 3.11 或以上版本，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[clinical,rwe]"
Copy-Item .env.example .env
```

只需 JSON 病例分析时可安装 `.[clinical]`；SimpleITK 影像元数据适配另用 `.[clinical-imaging]`。凭据写入被忽略的本地环境文件，配置模板保存在 `configs/`。

```powershell
python -m neurogra.clinical.cli probe --model-smoke
python -m neurogra.clinical.cli analyze --patient code/neurogra/clinical/tests/fixtures/multispecialty.json
python -m neurogra.clinical.cli analyze-rwe --patient-id "<RWE患者编号>"
python -m neurogra.clinical.web --port 8767
```

Web 地址为 <http://127.0.0.1:8767/>。病例分析需要按运行说明启动 Ollama、知识检索和相应的数据服务。自定义配置须放在子命令之前：`python -m neurogra.clinical.cli --config configs/clinical.default.yaml analyze --patient <病例.json>`。

```powershell
python -m neurogra.clinical.cli inspect-run --run-id <运行ID>
python -m neurogra.clinical.cli audit-run --run-id <运行ID>
python -m neurogra.clinical.cli resume --run-id <运行ID>
python -m neurogra.clinical.cli build-vector-index
python -m neurogra.clinical.cli evaluate-retrieval --queries code/neurogra/clinical/tests/fixtures/retrieval_queries.json --top-k 5
```

恢复保留原运行预算、截止时间、模型和知识绑定；已有成功结果不重复执行。报告写入被忽略的 `docs/病例报告/`，原始资料在 `data/clinical/`，中间产物在 `output/clinical/`。自然语言摘要补写入口为 `write-narrative --run-id <运行ID>`，会创建独立写作运行。

## 能力与限制

- 已有病史、认知、检验和影像报告四类 Agent、主子复查、分批处理、报告依据核对、RWE 与本地 Web 入口。
- 检索已包含 BM25、BGE-M3 向量与基础图谱查询的融合和明确降级；实际可用性由本地服务、发布版本及索引决定。
- DiaMond 仍需预处理、RegBN 和训练参考一致性验证，默认保持 `blocked_validation`，不能把工具接入当作真实分类验收完成。
- 三层图谱 v0.3 和统一算法 v1.0 是待实现、待实验的研究设计；现有临床工作流不代表已实现完整多模态候选探索、图嵌入训练和条件完整证据选择。
- 流程 `completed` 表示执行完成，医学判断状态独立保存。引用及协议检查通过不等同于临床准确率验证。

## 目录和本地参考源码

`code/` 放项目代码，`configs/` 放配置，`frontend/` 放工作台，`docs/` 放当前文档，`data/` 和 `output/` 放本地数据与产物，`paper/` 放研究资料，`study/` 放个人学习资料。

以下 GitHub 源码单独保留在本地并由 `.gitignore` 排除，主仓库不再跟踪嵌套仓库引用：

- [OptimusKG](https://github.com/mims-harvard/OptimusKG)：`OptimusKG/OptimusKG/`。
- [RAG-Anything](https://github.com/HKUDS/RAG-Anything)：`RAG-Anything/RAG-Anything/`。
- [RTX-KG2](https://github.com/RTXteam/RTX-KG2)：`RTX-KG2/RTX-KG2/`。

需要查阅源码时按上述路径单独克隆；研究引用继续保留论文来源和所用版本。项目代码不得依赖 `study/`。

## 回归检查

```powershell
python -m unittest discover -s code/neurogra/clinical/tests -v
python -m unittest discover -s code/neurogra/knowledge/tests -v
```
