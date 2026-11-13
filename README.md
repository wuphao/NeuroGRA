# NeuroGRA

本地对话与病例分析系统。主 Agent 统一调度患者资料、原文 KB、条件 KG 和专科分析，报告保留原始来源及未解决问题。代码与文档入口见[项目清单](docs/项目代码与文档清单.md)。

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

Windows 下可直接双击项目根目录的 **`start.cmd`**，自动启动前端页面、后端 API 和后台任务进程，并打开浏览器。首次使用前需完成上面的依赖安装。前端由 Python 服务提供，不需要单独执行 npm。

```powershell
.\start.cmd                       # 默认端口 8767，沿用保存的配置
.\start.cmd --port 8768           # 修改端口
.\start.cmd --no-browser          # 启动服务但不自动打开浏览器
.\start.cmd --check               # 只检查，不启动服务
```

启动窗口保持打开；按 Ctrl+C 停止 Web 服务，后台任务按现有机制继续运行。需要停止后台进程时执行 `.\.venv\Scripts\neurogra.exe stop-worker`。日志位于工作区的 `data/platform/logs/worker.log`。端口占用会明确报错，不会自动结束其他服务。启动入口的后续维护要求见 [AGENTS.md](AGENTS.md)。

在项目目录启动本机工作台及独立后台执行进程：

```powershell
neurogra --profile kb-only start --port 8767
```

关闭页面服务后，后台任务继续执行。`neurogra stop-worker` 请求后台进程完成当前任务后退出。调试时仍可分别使用 `serve` 和 `worker`。

浏览器打开 http://127.0.0.1:8767，使用 RWE 账号登录。默认项目由 `configs/rwe.project8.yaml` 配置；RWE 服务地址从环境变量或本地 `.env.rwe.local` 的 `RWE_API_BASE_URL` 读取。

登录后先选择患者，再讨论患者资料或点击「智能分析」。对话可按问题读取患者资料、查询本地知识并调用相关专科；无需先生成报告。综合分析可以在对话中发起，资料变化或无法确认时需选择重新分析或查看旧报告。报告运行期间仍可提交对话，模型忙时会显示等待状态。可通过「添加对比患者」显式选择其他患者，再提出比较问题；群体统计只覆盖当前账号可见范围。

对话可在同一个问题中组合局部分析、患者比较、群体统计和完整报告；仅说“分析这个患者”且范围不清楚时会先澄清。展开「本轮协作」可以查看专科任务、定向质询、相关患者记录和本地知识摘要。系统会在权限、工具和预算范围内执行至多两轮、三项补查或交叉核查；无法完成的内容保留为缺口。出现可恢复中断时可点击「恢复分析」。这些结果供医生和课题组核查，部分完成不表示已获得诊断结论。

会话和关注点按账号、项目与患者隔离；发送「记住：关注点」「查看记忆」或「忘记：完整关注点」可管理关注点。报告卡片提供「追问此报告」。设置非密钥的 `RWE_SOURCE_ID` 可为数据源指定稳定身份；更换 RWE 地址时应继续使用同一标识。仅在核实 RWE 支持完整一致的患者快照后，才可在项目配置中设置 `consistent_patient_snapshot: true`；否则旧报告的资料一致性显示为未知。

### 配置生成模型

点击聊天输入框右下角、发送按钮左侧的当前模型名称，选择 Ollama、OpenRouter、DeepSeek 或 OpenAI。可获取模型列表，也可手填模型 ID。「高级设置」提供地址、超时、上下文容量和最大输出。点击「使用模型」后，后续对话及新分析任务使用所选生成模型。知识库的嵌入和图谱服务通过运行配置管理。

云端 Key 填到项目根目录的 `.env.llm.local`。文件不存在时可复制 `configs/llm-keys.example.env`。设置页只显示 Key 的填写状态。获取列表和连通性测试使用模型目录或固定文本。要让非本机模型接收病例提示、对话历史及工具结果，须在该文件设置 `LLM_ALLOW_EXTERNAL_PATIENT_DATA=true`。

`GET /api/llm` 查看供应商和当前配置，`POST /api/llm` 保存生成参数，`POST /api/llm/models` 获取模型列表，`POST /api/llm/test` 测试连接。云端供应商使用固定官方地址。Ollama 的 `base_url` 可编辑。病例问答选用支持工具调用的模型，整病例分析选用支持 JSON 输出的模型。

患者对话使用项目内的 `data/platform/chat.sqlite` 保存会话。长期记忆由 Hermes `MemoryStore` 保存于 `data/platform/evochat/`，默认从 `~/Desktop/EvoChat` 加载模块，可通过 `NEUROGRA_EVOCHAT_ROOT` 指定位置。新会话绑定稳定数据源、账号、项目和患者；旧会话的原有作用域仍依赖工作空间路径，来源不能确认时不会自动合并。备份应同时包含对话数据库和记忆目录。

旧版 PostgreSQL 会话可以一次性导入空的聊天 SQLite。安装 `pip install -e ".[chat-migration]"` 后，停止旧版对话服务并运行 `neurogra migrate-chat`。导入默认从 EvoChat 的 `.env` 读取旧库连接，也可通过 `NEUROGRA_EVOCHAT_DATABASE_URL` 指定。数据库读取使用只读事务。

登录密码不保存，RWE Token 和患者列表缓存只驻留服务端内存，浏览器使用 HttpOnly、SameSite=Strict 会话 Cookie；退出登录或重启服务后需要重新登录。患者列表缓存 60 秒。RWE 接口用于读取资料。

对话页顶部的「智能分析」读取所选患者的完整表单并保存本地输入版本；本地知识图谱已发布且预检通过时可与原文知识库共同使用，否则使用符合预检条件的知识库配置。任务卡显示执行进度、摘要和报告入口，打开原会话时可恢复。相同输入和配置下排队或执行中的任务会复用。模型或必要资料未就绪时会显示对应错误。旧页面地址 `/patients` 和 `/workbench` 会转到对话页。

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

## 空间与备份

```powershell
# 统计受管理目录，不扫描 study 等个人文件
neurogra storage
# 先生成清理预览，仅涉及超过保留期的已结束任务轮转日志
neurogra cleanup --retention-days 30 --save-plan tmp/cleanup-plan.json
# 审阅预览后显式执行；文件发生变化时必须重新生成预览
neurogra cleanup --apply-plan tmp/cleanup-plan.json
# 在队列为空、任务结束后创建私有备份，目标目录必须尚不存在
neurogra backup --destination backups/local-20261027
neurogra verify-backup --source backups/local-20261027
# 恢复到全新的目录，不覆盖当前工作空间
neurogra restore --source backups/local-20261027 --destination tmp/restored-workspace
neurogra --root tmp/restored-workspace doctor --offline
```

备份包含患者资料，应按本地私有数据保管。备份会校验输入、SQLite、文件哈希及 KB/KG 发布；凭据与外部模型、Neo4j 服务不打包，恢复后需重新配置和检查。原先缺失的报告仍显示不可用。失败操作可能保留带 `.partial-` 的目录用于排查，这些目录不算有效备份或已恢复工作空间。

平台模型日志默认仅记录调用关联和内容摘要，每份日志 2 MiB，最多保留三份轮转副本。需要完整调试内容时，在配置中显式设置 `llm_debug_logging: true`；正式报告、来源和任务记录独立保存，不随日志轮转删除。

## 开发验证

```powershell
python -m unittest discover -s code/neurogra/platform/tests -v
python -m unittest discover -s code/neurogra/clinical/tests -v
python -m unittest discover -s code/neurogra_kg/tests -v
python -m unittest discover -s code/neurogra_kb/tests -v
python -m build --wheel
```

浏览器局部验收记录保存在本地 `testing/runs/`，各报告标明本轮的验证环境和范围。
