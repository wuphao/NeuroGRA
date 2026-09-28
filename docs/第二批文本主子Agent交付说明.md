# 第二批文本主子 Agent 交付说明

本批覆盖 S09 → S14 → S15 → S16 的病史文本子集，保留第一批接口。认知、检验、影像子 Agent 不在本批实现范围。

## 输入与入口

输入仍是中文任意结构 JSON，唯一必填 `患者ID`。示例位于 `code/neurogra/clinical/tests/fixtures/history_only.json`；不要求用户填写任务 ID、观察 ID 或量表列表。只有病史的病例通过第一批资料盘点得到 history 任务。

`analyze_patient(raw, config) -> RunResult`：准备病例、执行子任务、复查、汇总并写报告。

`resume_run(run_id, config) -> RunResult`：按原运行配置和准备产物继续，不重置预算或截止时间。

CLI：`python -m neurogra.clinical.cli [--config 配置路径] analyze --patient 病例路径`；另有 `resume --run-id` 和 `inspect-run --run-id`。

## 模块与输入输出

| 模块 | 输入 | 输出与规则 |
|---|---|---|
| `service.prepare_patient` | 原始病例、配置 | CaseSnapshot、TaskPlan、固定知识发布版本；复用第一批 |
| `orchestration.Executor` | 任务与病例快照 | AgentResult；只提供被授权观察及其原始记录；已完成任务读取已存结果 |
| `history.HistoryAgent.analyze` | history 任务、TaskContext | 回读原文，真实检索，再调用模型；返回有患者/知识引用的病史主张与局限 |
| `history.check_claims` | 候选主张、观察、EvidenceItem | 校验引用空间、重复 ID、允许的解释强度和工具权限；失败最多请求一次修正，仍非法的主张剔除并标记 partial |
| `workflow.factual_concerns` | 当前结果、原文观察 | 有限词表的原文扩写检查，例如新增“辅助检查”；不是完整医学语义校验 |
| 主 Agent 复查 | 最新结果、观察、检索摘录、确定性检查 | ReviewPlan，转为含目标结果版本、claim_ids、具体问题、成功标准的 ReviewRequest |
| `HistoryAgent.review` | ReviewRequest、准确的旧版本、原文、已有检索证据 | ReviewResponse；可纠正、澄清、维持、未决；完整替换结果，版本加一 |
| `workflow.apply_review` | 原结果、回复、数据库 | CAS 版本更新、旧版本保留、撤回记录；拒绝过期/错对象回复；崩溃重放幂等 |
| 主 Agent 汇总 | 最新有效主张 | Synthesis：排序现有 claim_id，每项恰好一次；不允许增加无依据自由文本 |
| `workflow.render_report` | FinalReport、快照、检索与复查记录 | Markdown；列出事实、原始记录引用、有限结论、未决、复查交互、知识背景与后端局限 |
| RunResult | 报告及执行记录 | run_id、status、report_paths、report、stop_reason、usage、artifact_refs |

病例文本仅进入已配置的模型服务与本地知识查询，不自动进行互联网患者信息检索。检索摘录送模型前限制长度，完整 EvidenceBundle 保存在运行数据库；未被主张引用的检索结果在报告中标为背景，不冒充临床支持。

## 复查与停止

确定性检查和模型语义复查分开记录。初始非法引用不会成为有效主张；不能通过结构校验的修订不会覆盖有效数据库版本。复查失败后，有待核对主张从本次报告中隔离，不恢复为已验证支持。

默认最多两轮；重复问题、没有新增主张变化、无法解决或预算不足会停止。为最终汇总保留两次模型调用额度。尚缺资料记录为未决；历史诊断不当作当前确诊。返回已有主张的维持意见是合法响应。

当前子 Agent 复查动作是回读已授权原文、重核已有检索证据、修订主张和保留未决；不自动补做患者未提供的检查，也不声称已有向量或影像推理结果。

## 持久化与恢复

SQLite 保存任务、模型响应、检索请求/结果、复查计划、请求、回复、结果各版本及最终 RunResult。原预算账本跨重启保留；未明确完成的外部调用标记 indeterminate，消耗仍计入额度。操作系统文件锁防止同运行并发续跑，进程终止后自动释放。

从 `preparation.json` 开始支持续跑：完成的子任务不重跑，已保存复查回复可重放，已完成汇总不重复调用，完成的 RunResult 直接返回。源模型调用若在响应落盘前中断，不能保证外部请求恰好执行一次；重试仍受原账本约束。S01–S08 尚未保存准备产物时，不支持阶段内续跑，需新建分析运行。

报告位于 `docs/病例报告/<患者ID哈希>/<run_id>/`。患者 ID 不直接作为目录名；报告与患者中间文件已被 gitignore 排除。

## 测试与实现边界

使用 unittest。新增协议测试覆盖：虚构证据、强度升级、过期回应、结果重放、完成后续跑不调用模型、复查失败隔离、无资料、并发锁、未决退出、汇总新造主张拦截、缺失误写阴性的纠正、有依据维持、报告生成中断后的恢复。

文本流程已实现，但不能据此声称临床诊断准确率。确定性语义检查目前只覆盖有限词表，模型复查仍可能遗漏其他扩写；最终汇总采用受限排序，未实现自由生成的多病因综合推理。只有病史的本次示例统一保守返回资料不足。

真实验收记录见文末。

补充报告约束：description 类主张在最终报告中回填其引用观察的原文，不输出模型夹带的因果解释；原始模型文本仍留在专业结果版本中，转换记录为 `report_fact_canonicalized`。interpretation 类仍必须有真实知识引用、受限强度及局限。这一报告约束不等于对所有医学解释作了确定性验证。

## 真实交付演示（2026-09-24）

- 病例：`tests/fixtures/history_only.json`，仅病史的合成病例。
- 运行 ID：`8ff73b4a7af44758a6885bacd7a2b7e1`。
- 真实模型：本地 Ollama `qwen3.6:35b`；7 次模型调用完成，含盘点、规划、病史分析/格式修正、主 Agent 复查、子 Agent 回应及模型汇总。
- 真实检索：1 次检索，BM25 返回 4 条来源；graph=failed，vector=not_configured，整体检索状态 degraded。未使用替身。
- 真实交互：主 Agent 指出“未附诊断依据”被扩写成“辅助检查支持”；子 Agent 回应后该扩写仍未通过回源检查，程序隔离相关主张，保留 unresolved，结果版本从 1 更新到 2。
- 最终报告：partial + insufficient_data。保留起病时间不明确、未提供用药记录等原文事实；历史诊断仍在事实资料范围中可查，但被质疑的专业主张不进入综合结论。没有将未决复查伪装成已修正成功。
- 恢复验收：对本运行执行 resume，模型/检索调用数保持 7/1，不重复执行。
- 测试：55 项 clinical + 18 项 knowledge，共 73 项通过。

[查看真实 Markdown 报告](病例报告/patient_0eb6ed7d983936a79612c145/8ff73b4a7af44758a6885bacd7a2b7e1/report.md)。运行产物为 `output/clinical/8ff73b4a7af44758a6885bacd7a2b7e1/analysis.json`，完整事件、检索证据及各版本保存在 `data/clinical/clinical.sqlite`。

这次演示验收的是包含未决处理在内的工程闭环，不是模型全部医学表述正确，也不是一次无降级的临床诊断成功案例。此前开发运行暴露了引用编号混用、结构化修订为空和修订未真正消除扩写的问题，相关运行记录保留，未覆盖为成功记录。
