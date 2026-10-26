# NeuroGRA

本地病例分析系统。主 Agent 统一调度患者资料、原文 KB、条件 KG 和专科分析，报告保留原始来源及未解决问题。当前正在按[工程执行计划](docs/工程优化与分析平台执行计划.md)建设分析平台。

## 安装

使用 Python 3.11，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements/windows-py311.lock
python -m pip install -e ".[clinical,kb,kg,rwe,dev]"
```

锁文件为 Windows/Python 3.11 的已捕获运行依赖。其他平台需要重新解析并验证依赖。原始影像适配另选 `clinical-imaging`。Ollama 模型和 Neo4j 服务由本机环境提供，安装 Python 包不会下载模型。

## 启动与分析

下面的工作台与后台进程应在同一个项目目录启动。当前开发版本使用两个终端：

```powershell
# 终端一：本机工作台
neurogra --profile kb-only serve --port 8767
# 终端二：独立后台任务执行进程
neurogra worker
```

浏览器打开 http://127.0.0.1:8767，上传或粘贴患者 JSON，预览后提交分析。`kb-only` 使用原文 KB；需要完整图谱时显式选择 `standard`。关闭知识来源不会初始化该来源，也不会将未验证条件当作已满足。

```powershell
# 检查环境，不执行生成推理
neurogra --profile kb-only doctor
# 离线检查输入，不创建分析任务
neurogra --profile patient-only analyze --patient code/neurogra/clinical/tests/fixtures/multispecialty.json --check
# 提交持久任务，由独立 worker 执行
neurogra --profile kb-only analyze --patient path/to/patient.json
# 提交后由当前进程执行队列并等待结果
neurogra --profile kb-only analyze --patient path/to/patient.json --wait
# 查看任务/历史产物完整性
neurogra jobs
neurogra integrity
```

可使用 `--root` 或 `NEUROGRA_HOME` 指定工作空间，`--config` 指定 YAML。覆盖规则为内置默认值、配置方案、配置文件、显式参数；查看 `neurogra config` 获得脱敏后的有效配置。旧 `python code/main.py` 入口仍可使用，但必须显式指定 `--patient`，不再默认读取本地患者。

## 知识与数据

```powershell
python code/build_knowledge.py --pdf-folder docs/references --kb-only --check
python code/build_knowledge.py --pdf-folder path/to/pdfs --kb-only
```

原文与知识发布保持现有位置。平台输入版本、队列及新报告保存在 `data/platform/`；SDK 独立运行报告在 `data/clinical/reports/`，不再写入文档目录。任务排队和医学判断分别显示，部分完成不等同于确诊。

凭据使用环境变量或被忽略的本地 `.env.rwe.local`，参考 `.env.example`。不要提交真实患者、真实编号清单、Token、密码或运行日志。已完成的清理不会自动恢复旧病例和报告，历史缺失产物会明确显示不可用。

## 开发验证

```powershell
python -m unittest discover -s code/neurogra/platform/tests -v
python -m unittest discover -s code/neurogra/clinical/tests -v
python -m unittest discover -s code/neurogra_kg/tests -v
python -m unittest discover -s code/neurogra_kb/tests -v
python -m build --wheel
```

平台建设进度及尚未完成的知识/空间/备份等能力统一维护在工程执行计划中。浏览器局部验收记录位于被忽略的 `testing/runs/`，不作为医学验证或整个版本已完成的证明。
