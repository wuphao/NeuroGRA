# 独立原文知识库

`TextKnowledgeService` 提供原文解析、不可变发布、BM25/向量召回与来源扩展。无需 Neo4j、KG 发布或任何声明抽取；索引不依赖 `statement_id`。

生成新对象 ID、校验和、版本与引用均由代码负责。嵌入使用 `qwen3-embedding:0.6b`，实际维度从返回向量确定，模型摘要和模板随索引绑定。模型身份变化时返回明确的向量不可用状态，原文词法查询仍可使用。

从项目根目录运行：

```powershell
$env:PYTHONPATH='code'
python -m neurogra_kb.cli --config configs/kb.local.yaml publish 'docs/综述/神经退行性疾病诊断入门综述.docx'
python -m neurogra_kb.cli --config configs/kb.local.yaml query '认知评估 MMSE'
```

`publish --lexical-only` 明确发布无向量版本。SDK 查询时传入固定 `release_id`，避免运行中跟随新版本。`expand_source(snippet_id, release_id=...)` 只接受该版本中真实存在的代码引用。

原始文件快照位于 `data/kb/`，发布位于 `output/kb/releases/`。DOCX 使用原段落定位，`page=None`，不将段落序号当作物理页码。来源、资源或索引文件被修改时检查失败。

原文证据默认 `allowed_use=background`、`applicability=unknown`；原文召回不等于条件已适用于患者。条件判断由独立 KG 工具提供，后续临床整合保留两者性质。
