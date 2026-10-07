# NeuroGRA 算法研究来源与贡献边界

本文是《NeuroGRA 多模态证据检索与缺口补偿算法设计 v1.0》的配套研究记录。算法正文使用统一方法名与编号引用；本文件保存完整来源，供正式论文引用和贡献审计。统一命名不改变基础机制的研究归属。

## 研究来源

[1] Guo, Zirui; Ren, Xubin; Xu, Lingrui; Zhang, Jiahao; Huang, Chao. RAG-Anything: All-in-One RAG Framework. arXiv:2510.12323v1, 2025. https://arxiv.org/abs/2510.12323v1 。采用多模态描述、文本与模态知识组织、结构和语义混合检索及原始内容回取思想。

[2] Guo, Zirui; Xia, Lianghao; Yu, Yanhua; Ao, Tu; Huang, Chao. LightRAG: Simple and Fast Retrieval-Augmented Generation. arXiv:2410.05779v1, 2024. https://arxiv.org/abs/2410.05779v1 。本稿对应本地预印本审阅版本，采用实体与关系描述索引、实体层与关系主题层检索思想。正式论文可核对权威发表版本后更新，避免将预印本与发表版计为两项不同工作。

[3] El Khatib, Ola; Difallah, Djellel. Explore-on-Graph: Hybrid Embedding–LLM Reasoning for Knowledge Graph Question Answering under Incompleteness. arXiv:2609.39786v1, 2026. https://arxiv.org/abs/2609.39786v1 。采用类型关系统计、ComplEx连接评分、候选剪枝、模型选择与迭代探索。基础连接模型原始研究见[5]。

[4] Yan, Shi-Qi; Gu, Jia-Chen; Zhu, Yun; Ling, Zhen-Hua. Corrective Retrieval Augmented Generation. arXiv:2401.15884v3, 2024. https://arxiv.org/abs/2401.15884v3 。提供检索质量评估与网络搜索补偿的相关研究依据，本文没有直接复现该工作的完整评估器。

[5] Trouillon, Théo; Welbl, Johannes; Riedel, Sebastian; Gaussier, Éric; Bouchard, Guillaume. Complex Embeddings for Simple Link Prediction. Proceedings of the 33rd International Conference on Machine Learning, PMLR 48:2071–2080, 2016. https://proceedings.mlr.press/v48/trouillon16.html 。公式(5)对应复数嵌入连接评分，不属于本文原创。公式(6)是本稿选择的成对排序训练目标，不能宣称为原始模型论文的原样损失。

[6] Cormack, Gordon V.; Clarke, Charles L. A.; Buettcher, Stefan. Reciprocal rank fusion outperforms condorcet and individual rank learning methods. Proceedings of the 32nd International ACM SIGIR Conference on Research and Development in Information Retrieval, 2009:758–759. https://doi.org/10.1145/1571941.1572114 。公式(8)采用其倒数排名融合思想并加入通道权重，该融合基础不属于本文原创。

## 贡献边界

| 内容 | 当前性质 | 论文写法 |
|---|---|---|
| 多模态解析和描述索引 | 基础机制与领域适配 | 引用[1,2]，说明来源与条件字段的适配 |
| 类型统计、连接评分和候选剪枝 | 借鉴已有探索机制 | 引用[3]，不将公式换名算作原创 |
| 复数连接评分模型与排序融合 | 已有基础模型及融合方法 | 引用[5,6]，说明本文的适配设置 |
| 外部搜索补偿 | 已有方向下的受控扩展 | 引用[4]，研究触发、范围核验和快照评价 |
| 需求缺口分类与动作调度 | 本文拟研究设计 | 明确操作判据、与直接多轮检索对照 |
| 候选与来源证据状态分离 | 本文拟研究设计 | 通过独立标注评价来源约束增益与成本 |
| 条件依赖及实际上下文覆盖 | 结合既有项目图谱的拟研究设计 | 展示条件完整性与版本错误的对照实验 |

表中“拟研究设计”不等于已经确认的首创贡献。正式定稿前需扩展相关工作核对，并完成实现、预算匹配对照、消融和失败分析。当前稿只完成算法设计。

## 项目设计依据

沿用《NeuroGRA 知识与来源分层图谱结构设计 v0.3》的17种核心类型、40种注册关系、条件字段和来源链；结合既有《NeuroGRA 条件依赖完整证据子图检索 算法重设计 v0.1》。新版是独立文档，不覆盖旧文档，不宣称应用代码已接入所有模块。
