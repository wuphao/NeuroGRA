# RWE 患者 ID 到可追溯报告

## 入口

在项目根目录执行：

```powershell
python -m pip install -e ".[clinical,rwe]"
$env:PYTHONPATH = "$PWD/code"
python -m neurogra.clinical.cli analyze-rwe --patient-id "<RWE患者编号>"
```

这里的 `--patient-id` 是 RWE 的 `patient_number`，不是数据库内部数字 `patient_id`。程序在指定项目中按患者编号精确匹配，拒绝不存在或重复匹配的编号。

默认读取 `configs/clinical.rwe.yaml` 与 `configs/rwe.project8.yaml`。切换数据源表单时修改配置，无需修改患者分析代码：

```powershell
python -m neurogra.clinical.cli --config configs/clinical.rwe.yaml analyze-rwe --patient-id "<患者编号>" --rwe-config configs/rwe.project8.yaml
```

## 数据源与模型配置

连接器参考 Multi-agent 的只读导出方式：PostgreSQL 只用于解析患者编号，实际表单通过 RWE `/form/queryData` 获取。表单 ID、名称和日期字段是数据源 schema 配置，不包含患者预期答案、分数阈值或诊断映射。原始表单 JSON 单独保存，不自动计分、补单位、选取第一条记录或补造影像路径。

在被 Git 忽略的 `.env.rwe.local` 中填写：

```dotenv
RWE_API_BASE_URL=http://localhost:8081
RWE_DB_HOST=localhost
RWE_DB_PORT=5432
RWE_DB_NAME=rwe_nexus_develop
RWE_DB_USER=postgres
RWE_DB_PASSWORD=<本地数据库密码>
RWE_API_TOKEN=<有效Token>
```

同名环境变量优先。项目 ID 从 RWE YAML 的 `project_id` 读取。不要将真实凭据放入 YAML 或报告。

Token 过期时可交互登录，密码不会写入文件：

```powershell
python -m neurogra.clinical.rwe_login --account "<RWE登录账号>"
```

本地模型与检索配置在 `configs/clinical.rwe.yaml`：Ollama 文本模型、嵌入模型、知识库配置、向量索引清单、运行预算与任务预算。当前配置使用已有本地知识发布；不会在分析时自动重建或改写知识库。

RWE 入口强制启用报告依据核对，即使显式传入旧版临床配置，也不会跳过该步骤。

## 执行流程

1. 精确解析患者编号，查询配置的表单，核对每行患者归属并检查返回数量。认证失败、表单读取失败、空表单分别记录。
2. 保存本次独立原始导出及临床输入。每个观察关联表单 ID、记录 ID、原始 JSON Pointer、原值和来源校验和。
3. 对结构化字段作确定性资料盘点；主智能体根据实际可用资料规划专业任务。无原始影像时不制造影像分类任务。
4. 专业智能体调用真实 BM25、向量或图谱检索。大病例按输入规模分批，背景资料随批提供，所有源字段保留；模型只引用本批有效观察及真实知识证据。
5. 校验主张的引用和强度，主智能体复查并综合；未通过依据校验的主张不作为报告支持。缺少量表版本、方法、单位等解释条件时保留原始事实与限制。
6. 模型生成摘要并进行独立依据核对，必要时修订一次。如自由改写仍不可靠，模型选择已有事实与解释的重点和顺序，程序按原始字段直接渲染。报告明确标注 `model_verified` 或 `source_rendered` 对应方式，不将被拒绝的段落作为合格报告输出。

`completed` 仅表示所需执行步骤完成，诊断判断状态独立保存。资料不充分可以得到 `insufficient_data`；这不应通过补造结论来消除。服务失败或处理未完成时报告为 `partial` 或入口失败。

## 产物与追溯

命令输出 `run_id`、执行状态、停止原因及文件路径：

- `docs/病例报告/<患者哈希>/<run_id>/report.md`：中文摘要、事实与时间线、分析、依据、限制及知识来源。
- 同目录 `report.json`：结构化主张、专业结果、依赖版本、摘要来源与生成方式。
- 同目录 `provenance.json`：观察、原始记录、知识证据、主张依赖与任务计划。
- `data/clinical/rwe/<export_id>/source.json`：原始 API 表单返回值；`patient.json` 为保留来源信息的临床输入。
- `data/clinical/clinical.sqlite`：运行状态、模型调用、预算、检索、复查、摘要验证及批次记录。
- `output/clinical/<run_id>/`：准备结果、运行结果与验收记录。

上述患者数据、报告和 Token 均由 `.gitignore` 排除。每次导出与运行独立保存。

追溯路径：**报告主张 → observation_id → record_id → rwe.value_pointer → 原始 source.json**；医学解释还需关联 **evidence_id → 发布版本与原文 span**。

## 检查与恢复

```powershell
python -m neurogra.clinical.cli inspect-run --run-id <run_id>
python -m neurogra.clinical.cli audit-run --run-id <run_id>
python -m neurogra.clinical.cli resume --run-id <run_id>
```

恢复沿用原运行配置、知识绑定和剩余预算，不重新读取 RWE。需要分析更新后的 RWE 资料时新建 `analyze-rwe` 运行。

审计检查患者归属、原始文件校验和、字段定位与原值、观察原文、引用与版本、预算、报告文件和摘要验证记录。它不能代替临床正确性评估；模型语义核对也不等同于医学证明。图谱算法深化及 DiaMond 一致性验证不属于本次端到端接入的替代验收项目。
