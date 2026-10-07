可以，压缩为 **“建图—检索与识别缺口—补偿探索—核验与补全—终止与回答”五步**。中间三步组成循环，统一表述为 NeuroGRA 的算法。

**第一步：建图——构建多模态知识图谱与来源索引**

解析文献正文、图像、表格和公式，抽取实体及关系，并保留定义、条件、例外、适用版本和原文位置。将文本知识与模态知识进行领域对齐：

$$
G=\operatorname{Align}_{\mathrm{domain}}
\left(G_{\mathrm{text}}\cup G_{\mathrm{modal}}\right).
$$

对齐依据实体类型、定义及适用范围，避免合并同名但含义不同的对象。同时建立语义索引，使尚未形成图谱连接的原文也能被检索。

该步骤输出：**观测知识图谱、语义索引和可定位的原始来源。**

**第二步：联合检索与缺口识别——确定问题还缺哪些证据**

将问题 $q$ 分解为必要证据需求，包括目标对象、待回答关系、限定条件及适用范围。随后联合进行图谱检索与语义检索：

$$
\mathcal C_0(q)
=
\mathcal C_{\mathrm{graph}}(q)
\cup
\mathcal C_{\mathrm{semantic}}(q).
$$

检索内容经来源核验后形成回答证据集合 $\mathcal P_i$。根据当前必要需求集合 $\mathcal N_i^{\mathrm{req}}$，计算缺口：

$$
\mathcal U_i
=
\left\{
n\in\mathcal N_i^{\mathrm{req}}:
\operatorname{Cover}(\mathcal P_i,n)=0
\right\}.
$$

只有证据能够支持所需内容、保留必要条件且符合适用范围时，才认为该需求被覆盖。

缺口主要区分为：**已有实体之间的关系缺失、相关实体未入图、条件或例外不完整，以及本地来源不足。** 若证据已经存在但未装入回答上下文，则优先重新组织证据。

**第三步：缺口驱动的补偿探索——寻找潜在连接与新增对象**

围绕未满足需求选择补偿路径：

| 缺口 | 处理方式 |
|---|---|
| 已有实体之间缺少关系 | 利用类型—关系统计确定探索方向，结合结构评分和语义检索生成候选连接 |
| 相关实体未入图 | 定向检索原文，抽取实体并核对定义、类型和身份 |
| 条件或例外不完整 | 补检相邻段落、表头、脚注及相关定义 |
| 本地来源仍不足 | 在问题范围与预算允许时，检索外部原始来源 |

对于已登记实体之间的候选连接 $(h,r,t)$，采用图谱嵌入评分：

$$
\phi(h,r,t)
=
\operatorname{Re}
\left(
\sum_{k=1}^{d}
z_{h,k}z_{r,k}\overline{z_{t,k}}
\right).
$$

其中，$z_h,z_r,z_t\in\mathbb{C}^d$ 为离线训练得到的实体和关系嵌入。大语言模型结合当前需求，从通过类型、方向及范围检查的候选标识中选择探索对象。

**候选连接用于指导原文检索。** 结构分数只是排序信号；尚未入图的实体需要通过来源发现与抽取，不能直接依靠已有实体的嵌入恢复。

**第四步：来源核验与条件补全——将候选转化为回答证据**

对候选连接及新增对象进行来源核验。支持性证据的接受条件为：

$$
\operatorname{Accept}
=
\operatorname{LocatorOK}
\land
\operatorname{Entail}
\land
\operatorname{ScopeOK}
\land
\operatorname{ContextOK}.
$$

分别检查：**原文能否定位、是否支持该关系、适用范围是否一致，以及必要上下文是否完整。** 核验结果记录为支持、反驳或未确定；支持性内容进入证据池，反证和冲突用于界定结论。

随后展开来源明确声明的相关条件与依赖：

$$
K_{j+1}
=
K_j
\cup
\bigcup_{u\in K_j}\operatorname{Dep}_q(u).
$$

其中，$K_j$ 为当前涉及的知识对象，$\operatorname{Dep}_q(u)$ 为与问题相关的定义、前提及例外依赖。

新增依赖转化为新的证据需求，再组织回答上下文，返回第二步重新检查覆盖。这样形成：

$$
\boxed{
\text{缺口识别}
\rightarrow
\text{补偿探索}
\rightarrow
\text{来源核验与条件补全}
\rightarrow
\text{重新检查缺口}
}
$$

**第五步：依据证据完整性与预算终止，生成可追溯回答**

算法在以下任一条件满足时终止：

$$
\operatorname{Stop}_i
=
\left(
\mathcal U_i=\varnothing
\land
\operatorname{Closed}(\mathcal P_i,q)
\right)
\lor
\operatorname{BudgetExhausted}_i
\lor
\operatorname{NoAction}_i.
$$

其中，$\operatorname{Closed}$ 表示必要条件和已声明依赖已经得到处理；预算约束探索轮数、检索次数及模型调用；$\operatorname{NoAction}$ 表示合法、未尝试且预算允许的补偿动作已经耗尽。一次检索失败后，仍可继续尝试其他有效路径。

最终依据实际回答证据和剩余缺口生成答案：

$$
A=\operatorname{Generate}(q,\mathcal P_i,\mathcal U_i).
$$

证据完整时，输出带来源引用的回答；仍有缺口时，呈现已支持内容，并明确未解决字段或冲突。每项结论均关联实际使用的原始证据。
