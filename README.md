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
