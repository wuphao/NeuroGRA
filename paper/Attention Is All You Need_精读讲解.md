# 《Attention Is All You Need》中文精读：Transformer 基础与面试手册

> Ashish Vaswani et al., *Attention Is All You Need*, NeurIPS 2017，arXiv:1706.03762。  
> 论文类型：方法论文（method paper）。  
> 读者画像：默认你会 Python、知道神经网络和矩阵乘法，但第一次系统学习 Transformer。

## 核心贡献一句话

这篇论文用完全基于注意力的 encoder-decoder（编码器—解码器）架构取代了 RNN 和 CNN 中的序列计算，使模型能并行训练、用短路径建模长距离依赖，并在机器翻译上取得当时最佳或接近最佳的结果。

## 论文完整覆盖承诺

这不是摘要式短文，而是一份可以替代首轮通读原论文的单文件精读。它按原文 1–7 节讲清动机、架构、公式、训练、实验、局限与未来工作，并将原论文与后来的现代 Transformer 做明确区分。

## 怎么读这份精读

1. 第一遍读“先建立全局”和第 3 节，把数据流讲出来。
2. 第二遍手推 attention 的张量形状，重点理解 `QK^T → scale → mask → softmax → V`。
3. 第三遍读第 4–6 节，区分“为什么快”、“复杂度是什么”和“实验真正证明了什么”。
4. 最后用末尾的面试题进行闭卷复述。

## 全论文路线图

- 1 Introduction：RNN 顺序计算是瓶颈，能否完全不用 recurrence（循环）？
- 2 Background：比较 RNN、CNN 和早期 self-attention，定位论文新意。
- 3 Model Architecture：给出 Transformer 的全部组件。
- 4 Why Self-Attention：用复杂度、并行性和最大路径长度论证选型。
- 5 Training：数据、硬件、Adam、warmup、dropout 和 label smoothing。
- 6 Results：翻译主结果、消融实验与英语成分句法分析。
- 7 Conclusion：总结贡献，指出局部注意力、多模态和减少生成顺序性等未来方向。

## 逐节覆盖清单

| 原文部分 | 本文位置 | 状态 |
|---|---|---|
| Abstract | 核心贡献、第 6 节 | 已覆盖 |
| 1 Introduction | 第 1 节 | 已覆盖 |
| 2 Background | 第 2 节 | 已覆盖 |
| 3 Model Architecture | 第 3 节 | 已覆盖 |
| 3.1 Encoder and Decoder Stacks | 3.1 | 已覆盖 |
| 3.2 Attention（3.2.1–3.2.3） | 3.2 | 已覆盖 |
| 3.3 Position-wise FFN | 3.3 | 已覆盖 |
| 3.4 Embeddings and Softmax | 3.4 | 已覆盖 |
| 3.5 Positional Encoding | 3.5 | 已覆盖 |
| 4 Why Self-Attention | 第 4 节 | 已覆盖 |
| 5 Training（5.1–5.4） | 第 5 节 | 已覆盖 |
| 6 Results（6.1–6.3） | 第 6 节 | 已覆盖 |
| 7 Conclusion | 第 7 节 | 已覆盖 |
| Appendix attention visualizations | 第 4 节 | 已覆盖 |

## 图表、公式与算法清单

| 对象 | 内容 | 处理 |
|---|---|---|
| Figure 1 | Transformer 完整架构 | GPT Image 2 中文重绘，保留原图和核对规格 |
| Figure 2 | 缩放点积注意力与多头注意力 | GPT Image 2 中文重绘并核对 |
| Figures 3–5 | 长距离依赖、指代、句法结构可视化 | 保留原文页面截图，不生成数据性图像 |
| Table 1 | 层复杂度/顺序操作/路径长度 | 原表截图+逐项解释 |
| Tables 2–4 | 翻译、消融、句法分析 | 原表截图+证据边界解释 |
| Eq. 1 | scaled dot-product attention | LaTeX 转写 |
| Eq. 2 | position-wise FFN | LaTeX 转写 |
| Eq. 3 | learning-rate schedule | LaTeX 转写 |
| PE formulas | 正弦/余弦位置编码 | LaTeX 转写 |
| 算法框 | 原文无独立 algorithm box | 明确记录，未虚构 |

## 核心概念追踪

| 术语 | 中文与本文语境 | 首次详解 | 状态 | 远距复现提醒 |
|---|---|---|---|---|
| Transformer | 变换器；本文指完全用注意力搭建的序列转换模型 | 3.1 | 已解释 | 面试节重申架构 |
| sequence transduction | 序列转换；把一个序列映射成另一个序列 | 1 | 已解释 | 无需重复 |
| self-attention | 自注意力；Q/K/V 来自同一序列 | 2、3.2 | 已解释 | 4 和面试节提醒 |
| query/key/value | 查询/键/值；决定“我想找什么”、“我能匹配什么”、“真正取走什么” | 3.2 | 已解释 | 面试节重申 |
| causal mask | 因果遮罩；禁止解码器看到未来 token | 3.1、3.2 | 已解释 | 面试题 6 提醒 |
| multi-head attention | 多头注意力；并行在多个子空间建模关系 | 3.2 | 已解释 | 消融与面试节提醒 |
| positional encoding | 位置编码；给无序的 attention 注入顺序 | 3.5 | 已解释 | 消融和面试节提醒 |
| autoregressive | 自回归；生成第 i 个 token 依赖前 i-1 个 token | 3.1 | 已解释 | 面试题 8 提醒 |
| residual connection | 残差连接；子层学增量，主干直接传递 | 3.1 | 已解释 | Pre/Post-LN 题提醒 |
| layer normalization | 层归一化；对单个 token 的特征维归一化 | 3.1 | 已解释 | 面试题 9–10 提醒 |
| BLEU | 机器翻译自动评估指标；越高通常越好，但不等于人类偏好 | 6.1 | 已解释 | 无需重复 |
| ablation | 消融实验；改掉一个因素，观察它是否重要 | 6.2 | 已解释 | 无需重复 |

## 经典原文观点

- “Attention Is All You Need”：标题的含义不是模型只有一个 attention 算子，而是序列主干不再需要 RNN/CNN；整个模型仍包含嵌入、FFN、残差、LayerNorm 和输出层。
- “first transduction model relying entirely on self-attention”：作者对创新边界的核心表述，重点是完整 transduction 模型的主干全部由 self-attention 支撑。
- “Multi-head attention allows the model to jointly attend”：多头的目的是并行建模多个表示子空间，而不是单纯增加重复计算。
- 论文宣称 Transformer 是第一个完全依赖 self-attention 、不用 sequence-aligned RNN/CNN 来构建输入输出表示的 transduction 模型。
- 论文认为正弦位置编码“可能”外推到训练时未见过的更长序列；这是动机而非论文已严格证明的结论。

## 读者可能卡住的补课地图

- 矩阵形状：在 3.2 用一个具体 shape 示例跟完 attention。
- Softmax 与梯度饱和：在 3.2.1 解释为何除以 `sqrt(d_k)`。
- 训练并行与生成并行：在 3.1 区分，避免把 Transformer 说成“生成时也全并行”。
- 复杂度：在第 4 节讲清 `O(n^2d)` 不等于“一定比 RNN 快”。
- 原始 Transformer 与现代 LLM：在面试节区分 encoder-decoder、encoder-only 和 decoder-only。

---

# 1. Introduction：为什么需要 Transformer？

## 本节解决什么

原文首先攻击的不是 RNN 的表达能力，而是它的计算依赖。RNN 必须按位置更新：

$$
h_t = f(h_{t-1}, x_t)
$$

因为 $h_t$ 依赖 $h_{t-1}$，一条序列内的各个位置不能同时计算。长序列不仅计算时间长，反向传播也要穿过很长的路径。LSTM/GRU 缓解了长程梯度问题，却没有消除这个顺序瓶颈。

注意力当时已经用在 encoder-decoder 中，但通常是附加在 RNN 旁边。这篇论文的关键跳跃是：如果 attention 可以直接连接任意两个位置，为什么不让它成为主干？

**常见误解。** “Transformer 没有顺序性”是错的。它在输入表示中显式注入位置；自回归解码时仍然逐 token 生成。它消除的是训练时网络主干中按位置循环的必要性。

**本节记忆点。** Transformer 的原始出发点是并行计算和长距离依赖，不是“把所有旧网络都换个名字”。

# 2. Background：论文新在哪里？

CNN 可以并行计算，但局部卷积核不能一层连接任意两个位置。连接距离为 $n$ 的两个 token，普通卷积需要线性增长的层数，空洞卷积也需要对数层数。自注意力在一层内就能把任意两个位置直接连起来。

原文不是泛泛地说“CNN 不好”，而是指出三条具体技术路线：Extended Neural GPU、ByteNet 和 ConvS2S 都用卷积让各位置并行计算；但 ConvS2S 中两位置交互的操作数随距离线性增长，ByteNet 则是对数增长。Transformer 将这条路径压到常数，但代价是 attention 把多个位置加权平均后，有效分辨率可能下降；作者用多头在不同子空间同时读取来抵消这个问题。

Self-attention（自注意力；本文语境：同一序列内不同位置相互读取）并不是这篇论文凭空发明的；它已经用在阅读理解、摘要、文本蕴含和句子表示等任务。本文的新意是将它变成完整序列转换模型的唯一主要信息混合机制，完全拿掉对齐序列位置的 RNN/CNN 层。

End-to-End Memory Networks 是另一条相关线：它用循环注意力而不是与序列位置对齐的 recurrence，在简单问答和语言建模上有效。这些前工作说明 attention 有潜力，但尚未形成这篇论文的完整方案。

**代价。** 全局 self-attention 需要构造 $n\times n$ 分数矩阵，所以长序列时显存和计算量可成为新瓶颈。论文自己已提出 restricted attention（受限注意力）作为未来方向。

# 3. Model Architecture：架构逐块拆解

![Figure 1 Transformer 中文重绘](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/fig01_redraw_zh.png)

### 先沿着 Figure 1 走完一次翻译数据流

假设源句是“I love NLP”，目标句是“我喜欢自然语言处理”。`token`（词元；模型处理的离散符号单位）先变成 `embedding`（嵌入；可学习的稠密向量），再加上位置编码。这个矩阵通过 6 个 encoder 层，每层先让源句内所有位置相互读取，再用 FFN 逐位置加工。Encoder 顶层的每个向量都是带全句上下文的源语言表示。

训练 decoder 时，正确目标句右移，例如输入 `<BOS>, 我, 喜欢, ...`，去预测 `我, 喜欢, 自然, ...`。它先经 masked self-attention，确保每个目标位置只看见已知前缀。随后进入 cross-attention：`Q`（查询）来自 decoder 当前表示，`K/V`（键/值）来自 encoder 顶层，因此 decoder 可以在每个目标位置查询源句。经 FFN 和 6 层堆叠后，`hidden state`（隐状态；每个位置的上下文向量）经 Linear 映射为整个词表的 `logits`（未归一化分数），Softmax 才将它变成下一 token 的概率。

## 3.1 Encoder 和 Decoder 堆叠

Transformer 仍保留经典 encoder-decoder 任务边界：

- Encoder 读取整个输入 $(x_1,\dots,x_n)$，产生上下文表示 $(z_1,\dots,z_n)$。
- Decoder 使用 encoder 的输出和已知目标前缀，自回归地预测下一个 token。

原始 base Transformer 的 encoder 和 decoder 各堆叠 $N=6$ 层，$d_{model}=512$。

Encoder 每层有两个子层：多头 self-attention 和 position-wise FFN。Decoder 比 encoder 多一个 encoder-decoder attention（交叉注意力），并将第一个 self-attention 改成 masked self-attention。

原论文每个子层都使用：

$$
\mathrm{LayerNorm}(x+\mathrm{Sublayer}(x))
$$

这是 Post-LN（后归一化；本文语境：先残差相加，再归一化）。现代大模型常用 Pre-LN/RMSNorm，不能反过来归到 2017 原文。

对形状 `[B,L,D]` 的表示，LayerNorm 对每个 batch 样本、每个 token 自己的 `[D]` 特征求均值和方差。它不把不同长度句子或 padding token 的统计混在一起。

### 为什么要残差与 LayerNorm？

残差连接让信息和梯度有一条直通通道，子层只需学“在现有表示上增加什么”。LayerNorm 稳定每个 token 的特征分布。注意 LayerNorm 与 BatchNorm 的统计维度不同：它不依赖 batch 内其他样本，因而更适合可变长序列和自回归推理。

### 为什么 decoder 输入要右移？

训练时已经知道整个目标序列，可以一次并行预测各个位置，但不能让第 $i$ 个位置直接看到它要预测的 token。因此目标序列右移一位，再用上三角因果遮罩把未来位置的 attention logit 加上 $-\infty$。Softmax 后这些位置的权重就是 0。

**训练 vs. 推理。** 训练可以在目标位置维并行，因为正确前缀已知；自回归推理仍要一步一步生成，通常用 KV cache 避免重复计算历史 K/V。KV cache 是后来工程实践中的重要概念，不是原论文的主题。

## 3.2 Attention：整篇论文的数学核心

![Figure 2 注意力中文重绘](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/fig02_redraw_zh.png)

### 3.2.1 Scaled Dot-Product Attention

$$
\mathrm{Attention}(Q,K,V)=\mathrm{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V
$$

一句话：用 query 与每个 key 的匹配分数产生权重，然后对 value 做加权求和。

假设 batch 先省略，序列长度 $n=4$，$d_{model}=512$，单头 $d_k=d_v=64$：

- $Q\in\mathbb{R}^{4\times64}$：每个位置有一个“我正在找什么”向量。
- $K\in\mathbb{R}^{4\times64}$：每个位置有一个“我具有什么特征供匹配”向量。
- $V\in\mathbb{R}^{4\times64}$：每个位置有一个“如果你注意我，我把什么信息传给你”向量。
- $QK^T\in\mathbb{R}^{4\times4}$：第 $i,j$ 项是位置 $i$ 对位置 $j$ 的原始匹配分。
- Softmax 在每一行上做，每行和为 1。
- 权重矩阵 $(4\times4)$ 乘 $V(4\times64)$，输出仍是 $4\times64$。

#### 为什么要除以 $\sqrt{d_k}$？

如果 $q_i,k_i$ 独立、均值 0、方差 1，则

$$
q\cdot k=\sum_{i=1}^{d_k}q_i k_i,
\qquad \mathrm{Var}(q\cdot k)=d_k
$$

点积的标准差是 $\sqrt{d_k}$。除以它后，分数的尺度不会随维度增大。否则 softmax 输入过大，分布很尖，大多数位置接近 0/1，梯度变小。

**面试陷阱。** 这不是 L2 归一化，也不是把点积变成 cosine similarity。它只是按维度设置一个固定 temperature。

这里的 `temperature`（温度；本文语境：控制 Softmax 分布尖锐程度的缩放量）不是另外一个学习参数。

**一个三 token 数值例子。** 某个 query 与三个 key 缩放后分数是 `[2, 1, 0]`，Softmax 约为 `[0.665, 0.245, 0.090]`。若三个 value 的某一维是 `[10, 0, -10]`，输出该维约为 $0.665\times10+0.245\times0+0.09\times(-10)=5.75$。若第三个 token 是未来位置，先把其分数设为 $-\infty$，Softmax 重新归一化前两个权重，而不是在加权后才删除它。

原文还比较了 additive attention（加性注意力；用单隐层前馈网络算兼容性）。两者理论复杂度相近，但点积 attention 可使用高度优化的矩阵乘法，实际更快、更省空间；大 $d_k$ 下若不缩放，它的效果反而可能劣于 additive attention。

### 3.2.2 Multi-Head Attention

$$
\mathrm{head}_i=\mathrm{Attention}(QW_i^Q,KW_i^K,VW_i^V)
$$

$$
\mathrm{MultiHead}(Q,K,V)=\mathrm{Concat}(\mathrm{head}_1,\ldots,\mathrm{head}_h)W^O
$$

原论文取 $h=8$，$d_k=d_v=d_{model}/h=64$。每个头学不同的线性投影，可以在不同的表示子空间、不同的位置关系上并行读取。

多头不是简单重复计算 $h$ 次同一个 attention，因为每头的 $W_i^Q,W_i^K,W_i^V$ 都不同。在 $d_k=d_{model}/h$ 时，忽略投影常数，所有头合计的主要注意力计算量与一个全维单头同阶。

### 3.2.3 三种 attention 不要混

| 位置 | Q 来自 | K/V 来自 | 遮罩 | 作用 |
|---|---|---|---|---|
| Encoder self-attention | encoder 上一层 | 同一处 | padding mask | 输入内部全局交互 |
| Decoder masked self-attention | decoder 上一层 | 同一处 | causal + padding mask | 只读已生成前缀 |
| Encoder-decoder attention | decoder 当前表示 | encoder 最终输出 | 输入 padding mask | 目标位置查询源句子 |

Q/K/V “来自同一处”不等于数值完全相同。Self-attention 中的原始输入 $X$ 相同，但 $Q=XW^Q,K=XW^K,V=XW^V$，三个投影矩阵不同。

`padding mask`（补齐遮罩；本文语境：一个 batch 内短句子为了和长句子对齐而填入 PAD token，这些人工位置不应被读取）可以同时出现在 encoder 和 decoder。它按样本的真实长度遮住无效位置；`causal mask`（因果遮罩）则按时间顺序遮住未来位置。前者解决批处理的人工填充，后者维持自回归因果性，两者可以叠加。

## 3.3 Position-wise Feed-Forward Network

$$
\mathrm{FFN}(x)=\max(0,xW_1+b_1)W_2+b_2
$$

原论文 $512\rightarrow2048\rightarrow512$，中间用 ReLU。这个网络对每个位置独立使用，但所有位置共享参数；不同 Transformer 层之间不共享。它等价于两个 kernel size 1 的卷积。

直觉上，attention 负责 token 之间的信息交换，FFN 负责每个 token 内部的非线性特征变换。如果只有 attention 而没有 FFN，模型缺少足够的逐位置非线性容量。

## 3.4 Embedding 与 Softmax

输入 token 先映射到 $d_{model}$ 维嵌入。Decoder 末端的线性层再将 hidden state 映射到词表 logits，softmax 生成下一 token 概率。论文在输入嵌入、输出嵌入和 pre-softmax 线性变换之间共享权重，并将嵌入乘以 $\sqrt{d_{model}}$。

权重共享（weight tying）减少参数，也让“如何表示一个词”与“如何给这个词打分”使用同一几何空间。

## 3.5 Positional Encoding

Self-attention 如果不加位置信息，对 token 排列是 permutation equivariant（置换等变；调换输入顺序，输出只会跟着调换），它不知道谁在前谁在后。

$$
PE_{(pos,2i)}=\sin\left(pos/10000^{2i/d_{model}}\right)
$$

$$
PE_{(pos,2i+1)}=\cos\left(pos/10000^{2i/d_{model}}\right)
$$

- $pos$：token 位置。
- $i$：特征维索引。
- 偶数维用 sin，奇数维用 cos，不同维度的波长按几何级数变化。

位置编码与 token embedding 同维，所以直接相加。对固定偏移 $k$，$PE_{pos+k}$ 可以由 $PE_{pos}$ 线性表示，这是作者认为模型容易学相对位置的原因。

论文消融显示，学习式位置嵌入与正弦编码结果几乎相同。作者选正弦版主要因为它“可能”外推到更长序列，不应在面试中说成已被这篇论文强证明。

# 4. Why Self-Attention：为什么是它？

![Table 1 复杂度比较](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/table01_complexity.png)

论文用三个标准比较自注意力、RNN 和 CNN：

1. 每层计算复杂度。
2. 最少顺序操作数，衡量并行性。
3. 任意两个位置之间的最大信息路径长度。

| 层类型 | 每层复杂度 | 顺序操作 | 最大路径 |
|---|---:|---:|---:|
| Self-attention | $O(n^2d)$ | $O(1)$ | $O(1)$ |
| Recurrent | $O(nd^2)$ | $O(n)$ | $O(n)$ |
| Convolutional | $O(knd^2)$ | $O(1)$ | $O(\log_k n)$ |
| Restricted self-attention | $O(rnd)$ | $O(1)$ | $O(n/r)$ |

当 $n<d$ 时，$O(n^2d)<O(nd^2)$，这在当时机器翻译的句子长度与特征维度下很常见。但当 $n$ 远大于 $d$ 时，二次项会主导，所以“attention 永远比 RNN 复杂度低”是错的。

路径长度 $O(1)$ 表示任意两个 token 在一层中就能直接交互，这使长距离依赖的前向信号和反向梯度不必经过 $n$ 步。但路径短不等于一定能学好所有长程规律；它只是优化上更有利的结构条件。

表中“顺序操作”是理论上最少必须串行的步数，不是真实墙钟时间；硬件利用率、内存访问和 kernel 实现同样会影响速度。对卷积，普通连续 kernel 需要 $O(n/k)$ 层才能连通远端位置，表中 $O(\log_k n)$ 对应空洞卷积。原文还指出，普通卷积的计算成本通常比 RNN 多一个 kernel 宽度 $k$ 的因子。

可分离卷积将复杂度降为 $O(knd+nd^2)$。当 $k=n$ 时，它的计算量大致等于一个 self-attention 层加一个 position-wise FFN，正好对应 Transformer 的主体组合。对非常长的序列，原文建议将 attention 限制在宽度 $r$ 的邻域：复杂度降为 $O(rnd)$，但最大路径增为 $O(n/r)$。这是效率与全局信息传播之间的明确交换。

![Figure 3 长距离依赖](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/fig03_attention.png)

**Figure 3 怎么看。** 上下都是同一个句子，线条表示 encoder 第 5/6 层中、当 query 是 `making` 时它对其他词的注意力，不同颜色是不同头。多个头连到距离很远的 `more difficult`，完成“making ... more difficult”的搭配。它直观展示了一层 attention 跨距离连接的能力，但不证明模型所有时候都能抓对长距离依赖。

![Figure 4 指代关系](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/fig04_coreference.png)

**Figure 4 怎么看。** 句子中 `its application` 的 `its` 需要找到前面的 `The Law`。上半部给出第 5 个头的完整 attention，下半部只隔离 `its` 在第 5、6 个头的连线。这些分布对 `its` 很尖锐，作者将它解读为与 anaphora resolution（回指/指代消解）有关。两个头图案不同，也支持“多头可学不同关系”的观察。

![Figure 5 句法结构](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/fig05_structure.png)

**Figure 5 怎么看。** 它给出同一句话“The Law will never be perfect, but its application should be just ...”在 encoder 第 5/6 层两个不同头上的完整 attention 模式。阅读时沿某个词出发的线查看它将权重分给哪些词：上下两组线并不相同，说明两个头在同一句子上学到了不同的结构连接模式。原图和 caption 没有为每条连线标注一个确定语法名称，因此严谨结论只是“行为似乎与句子结构有关”，不应凭图自行命名具体依存类型。它支持“头之间可能有分工”，但不能将某个头等同于一个稳定可靠的语法解析器。

三张附录图共同表明，某些头会聚焦长距离搭配、指代和句法结构。这是解释性案例，不是“每个头都有唯一人类可命名职责”的证明。架构为什么合理已经讲完，下面转向它如何被训练出来。

# 5. Training：训练细节也是贡献的一部分

## 5.1 数据与分词

- WMT 2014 English-German：约 450 万对句子，共享约 37K BPE（byte-pair encoding；字节对编码，本文中是子词分词）词表。
- WMT 2014 English-French：约 3600 万对句子，使用 32K `word-piece`（子词片段；将低频词分成可复用子单位）词表。
- batch 按近似序列长度分组，每批约 25,000 个源 token 和 25,000 个目标 token。

## 5.2 硬件与训练时间

- 8 张 NVIDIA P100 GPU。
- base：100K steps，每步约 0.4 s，约 12 小时。
- big：300K steps，每步约 1.0 s，约 3.5 天。

这些数字只能在 2017 年的硬件、实现和任务背景下理解，不能直接拿来预估现代 LLM 的训练成本。

## 5.3 Adam 与 warmup

Adam 超参数：$\beta_1=0.9,\beta_2=0.98,\epsilon=10^{-9}$。学习率：

$$
lrate=d_{model}^{-0.5}\min(step^{-0.5},\;step\cdot warmup^{-1.5})
$$

$warmup=4000$。前 4000 步线性增加学习率，之后按步数平方根的倒数衰减。Warmup 的直觉是避免训练初期表示与优化器统计量尚不稳定时迈出过大步长。

## 5.4 正则化

- Residual dropout：子层输出在加入残差之前 dropout，嵌入+位置编码后也 dropout。Base $P_{drop}=0.1$。
- Label smoothing：$\epsilon_{ls}=0.1$。它使目标分布不再是绝对 one-hot，降低过度自信。论文明说它会损害 perplexity（困惑度），却提高 accuracy 和 BLEU。这不矛盾：不同指标衡量的性质不同。

# 6. Results：实验真正证明了什么？

## 6.1 机器翻译

![Table 2 机器翻译结果](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/table02_translation.png)

**先读表。** 每行是一种模型，中间两列分别是英德/英法 BLEU，右侧是论文估算的训练 FLOPs（浮点运算次数；用训练时间×GPU 数×单卡持续算力估算）。`single model`（单模型）与 `ensemble`（集成；合并多个模型预测）要分开比，后者通常更强也更贵。

- WMT14 English→German：Transformer big 取得 28.4 BLEU，比当时已报告的最佳结果（包括 ensemble）高超过 2 BLEU。
- WMT14 English→French：摘要和 Table 2 报告 41.8 BLEU；正文 6.1 中出现 41.0，这是原文内部的数字不一致，本文不替作者消除它。
- Big 模型约 2.13 亿参数，base 约 6500 万。
- Base 英德训练成本约 $3.3\times10^{18}$ FLOPs，big 英德约 $2.3\times10^{19}$ FLOPs；同表的部分旧系统训练成本更高。这是论文所说“质量更高且训练成本更低”的证据，不是现代推理吞吐的直接结论。
- 推理使用 `beam search`（束搜索；每步保留若干个最优候选），beam size 4，`length penalty`（长度惩罚；避免搜索过度偏好短句）$\alpha=0.6$。最长输出是输入长度 + 50，可提前结束。
- `checkpoint averaging`（检查点平均；对多个相邻训练时刻的参数求平均，减小单点波动）：base 平均最后 5 个每隔 10 分钟保存的 checkpoint，big 平均最后 20 个。
- 英法 big 实验用 dropout 0.1，而不是 Table 3 中 big 英德设置的 0.3。

证据边界：这证明了 Transformer 在当时两个机器翻译基准上同时具有强质量和训练效率，不是对所有任务、所有长度和所有硬件的普遍优越证明。

## 6.2 模型变体与消融

![Table 3 消融实验](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/table03_ablations.png)

**表的比较口径。** 所有数字都在 WMT 英德 newstest2013 开发集上，使用前文的 beam search，但不做 checkpoint averaging。PPL 是按 BPE wordpiece 计算，不能与按完整单词计算的 PPL 直接比。因此这里 big 的 26.4 是开发集 BLEU，不是 Table 2 测试集的 28.4。

主要结论：

- 单头比最佳设置低约 0.9 BLEU，但头太多也会下降；头数不是越多越好。
- 降低 $d_k$ 会损害质量，说明 query-key 兼容性不是一个过于简单的任务。
- 在 C 组宽度实验中，增大 $d_{model}$ 或 $d_{ff}$ 通常更好，但参数和成本更高；不能推成所有“变大”都单调提升。例如层数 $N=8$ 时为 25.5 BLEU，低于 base 25.8。
- Dropout 明显抑制过拟合：去掉 dropout 时 BLEU 从 25.8 降到 24.6。
- 去掉 label smoothing 后 PPL 从 4.92 改善到 4.67，但 BLEU 下降到 25.3，是“PPL 与 BLEU 可能反向”的直接数据。
- 学习式位置嵌入和正弦编码几乎持平（25.7 vs. 25.8 BLEU）。

注意：这些消融是在当时的数据、base 设置和开发集上做的，不能直接外推为现代超大规模模型的最优设置。

## 6.3 英语成分句法分析

![Table 4 句法分析](Attention%20Is%20All%20You%20Need_%E7%B2%BE%E8%AF%BB%E8%AE%B2%E8%A7%A3_assets/table04_parsing.png)

论文用 4 层、$d_{model}=1024$ 的 Transformer 做 constituency parsing（成分句法分析）：

**实验设置。** `WSJ`（Wall Street Journal；Penn Treebank 中的华尔街日报句法标注语料）纯监督设置约 40K 句子、词表 16K；半监督加入约 17M 高置信句子、词表 32K。作者只在 WSJ Section 22（开发集，用于选超参数）少量选择 dropout、学习率和 beam size，其他参数基本沿用英德 base 模型。最终在 Section 23（测试集，用于报告最终结果）评估，beam size 21，$\alpha=0.3$，最长输出为输入长度 + 300。`labeled-bracketing F1`（带类别成分括号 F1）综合比较预测句法成分与金标树的精确率和召回率，越高越好，不是分类任务中的普通样本 F1。

- 仅用约 40K WSJ 训练句子时，Section 23 成分括号 F1=91.3，超过对比中 Berkeley Parser 的 90.4。
- 半监督约 17M 句子时，同一测试的 F1=92.7。
- 它仍低于表中某些 multi-task/generative 方法，所以不是“在所有句法分析设置上 SOTA”。

这个实验的主要价值是说明 Transformer 不只是专门为翻译巧调出来的，它可以迁移到输出长、结构约束强的任务。

# 7. Conclusion：真正的贡献、局限与未来

## 真正贡献

1. 证明序列转换主干可以完全去掉 RNN/CNN。
2. 给出 scaled dot-product attention、multi-head attention、位置编码、残差与 FFN 的一套可训练组合。
3. 将架构设计与并行性、最大路径长度联系起来，并用强实验结果支撑。

## 局限和边界（本文分析）

- 全局 attention 的时间/显存对序列长度是二次的。
- 原始 decoder 的生成仍然是自回归顺序的。
- 实验主要是机器翻译，句法分析是一个迁移例子；尚不是后来语言模型那种广泛任务证据。
- 注意力可视化提供案例性解释，不等于完整因果解释。
- 原文没有系统讨论公平、安全、幻觉或大规模预训练，这些不能追加为论文原结论。

## 作者明确的未来方向

- 将 attention-based 模型用到文本以外的图像、音频和视频。
- 研究 local/restricted attention，更高效地处理大输入输出。
- 让生成过程减少顺序性。

---

# 面试高频题：从及格回答到追问

> 本节参考了公开 Transformer 面试题库、技术文章和社区讨论中反复出现的题型。它们只能说明备考高频主题，不能证明某家公司必然会问。

## A 档：必会

### 1. 请用 1 分钟介绍 Transformer

**参考回答：** Transformer 是一种最初用于序列转换的 encoder-decoder 架构，它用多头 self-attention 建模 token 之间的全局依赖，用位置编码补充顺序，用 FFN 做逐 token 非线性变换，并通过残差和 LayerNorm 稳定深层训练。它相比 RNN 的核心优势是训练可并行且任意两位置的信息路径是常数；代价是全局 attention 对序列长度为二次复杂度。

### 2. Q、K、V 分别是什么？

Q 表示当前位置想查询的特征，K 表示每个位置用于被匹配的特征，V 是真正被加权汇总的内容。$QK^T$ 只决定读取权重，最后权重乘 $V$ 才产生输出。

**追问：为什么 Q/K 不共享投影？** 一个关系中“我要找什么”与“我能被怎样找到”未必是同一特征。不共享使匹配函数更灵活；它不是数学上不能共享，而是容量与归纳偏置选择。

### 3. 为什么除以 $\sqrt{d_k}$？

标准回答要包含“点积方差随 $d_k$ 增大”、“softmax 饱和”和“梯度”。如果 q/k 分量独立且方差为 1，点积方差是 $d_k$，除以标准差 $\sqrt{d_k}$ 使 logit 尺度稳定。

### 4. Multi-head 为什么比 single-head 好？

它让模型并行学习多个不同投影子空间和关系模式，减轻单头把多种关系平均在一个分布里的限制。但在总维度固定时，头越多，每头维度越小；原论文的消融也显示头数过多会变差。

### 5. 为什么需要位置编码？

因为没有位置信息的 self-attention 对排列缺少语序辨识能力。正弦编码使不同位置具有不同向量，且相对偏移具有线性关系。

**追问：学习式 vs. 正弦？** 原文中效果几乎相同。学习式灵活，但受训练最大位置约束；正弦形式可以在新位置上直接计算，但“可计算”不保证模型真正长度外推良好。

### 6. Mask 有哪些？放在哪？

- Padding mask：遮住补齐 token。
- Causal/look-ahead mask：遮住 decoder 的未来位置。

通常在 softmax 之前对 attention logits 加 0 或 $-\infty$ 的遮罩。放在 softmax 后才直接置零会破坏权重和为 1，除非再次归一化。

### 7. Self-attention 的复杂度是多少？

attention score 与加权汇总主要是 $O(n^2d)$，attention 矩阵显存 $O(n^2)$。线性投影和 FFN 还带来 $O(nd^2)$。因此完整 Transformer 层的成本不应只背一个 $O(n^2)$；要说明对 $n$ 和 $d$ 的依赖。

### 8. Transformer 为什么比 RNN 易于并行？

训练时 self-attention 可一次对所有位置做矩阵运算，没有 $h_t\leftarrow h_{t-1}$ 的位置级串行依赖。但 decoder-only 自回归生成仍串行，不能把训练并行性与推理生成并行性混为一谈。

## B 档：常见深挖

### 9. 为什么是 LayerNorm，不是 BatchNorm？

LayerNorm 对单样本、单 token 的特征维做统计，不依赖 batch 大小、填充比例和其他句子，训练/推理行为一致。BatchNorm 对可变长、大量 padding 和小 batch 的序列建模较不自然。

### 10. Pre-LN 和 Post-LN 有什么区别？

- 原论文 Post-LN：$\mathrm{LN}(x+F(x))$。
- 常见 Pre-LN：$x+F(\mathrm{LN}(x))$。

Pre-LN 的残差主干更直接，通常训练更深网络更稳定；Post-LN 原始设计常更依赖 warmup。不要把现代 Pre-LN 说成原论文设计。

### 11. Encoder-only、Decoder-only、Encoder-decoder 有什么区别？

- Encoder-only（如 BERT）：双向 self-attention，擅长理解/表示。
- Decoder-only（如 GPT 系）：全层因果 self-attention，擅长自回归生成。
- Encoder-decoder（原始 Transformer、T5）：encoder 双向理解输入，decoder 因果生成，通过 cross-attention 连接，适合条件序列到序列任务。

### 12. 为什么 FFN 的中间维度更大？

先升维再降维提供更大的逐 token 非线性容量。原文是 $512\rightarrow2048\rightarrow512$，即 4 倍。现代模型可用 GELU/SwiGLU 和不同展开比，那是后续演进。

### 13. Attention 权重等于解释吗？

不完全等于。权重表示某层某头的信息混合系数，可提供线索，但最终预测还受 V、输出投影、残差、FFN 和后续层影响。原文 Figures 3–5 是有趣案例，不是完整因果归因方法。

### 14. 为什么 label smoothing 可能让 perplexity 变差、BLEU 变好？

Label smoothing 故意不让模型对正确类分配 1 的目标概率，会惩罚过度尖锐的概率，因而按 one-hot 对数似然计算的 perplexity 可能变差；但它改善泛化和校准，序列选择反而可能得到更好 BLEU。

## C 档：手写和张量题

### 15. 写一个最小 scaled dot-product attention

```python
import math
import torch

def attention(q, k, v, mask=None):
    # q: [B, H, Lq, Dk]
    # k: [B, H, Lk, Dk]
    # v: [B, H, Lk, Dv]
    scores = q @ k.transpose(-2, -1) / math.sqrt(q.size(-1))
    if mask is not None:
        scores = scores.masked_fill(~mask, float("-inf"))
    weights = torch.softmax(scores, dim=-1)
    return weights @ v, weights
```

必须说清 softmax 的维度是 key 序列维 `-1`，mask 需能 broadcast 到 `[B,H,Lq,Lk]`。工程上还要处理“一整行都被 mask”可能导致 NaN 的问题。

### 16. 给定 $B=2,H=8,L=128,D=64$，score 形状？

$QK^T$ 的形状为 `[2,8,128,128]`；与 $V[2,8,128,64]$ 相乘后为 `[2,8,128,64]`；合并头后为 `[2,128,512]`。

## 面试回答自查清单

你应该能不看笔记回答：

- 用 60 秒说完 Transformer 数据流。
- 写出 attention 公式，解释 Q/K/V 以及每个矩阵形状。
- 推导为什么除以 $\sqrt{d_k}$。
- 区分三种 attention 以及两种 mask。
- 区分训练并行与自回归推理。
- 给出 self-attention 的时间/显存复杂度，并说出长序列瓶颈。
- 区分原始 Post-LN Transformer 和现代 Pre-LN/RMSNorm 变体。
- 准确说出原文实验证明了什么，没证明什么。

---

# 建议学习路线

1. 用纸笔画出 Figure 1，标注三种 attention 的 Q/K/V 来源。
2. 用 NumPy/PyTorch 手写单头 attention，打印每步 shape。
3. 加 causal mask，检查 attention 矩阵上三角是否全 0。
4. 扩展为 multi-head，确认 split/transpose/concat 次序。
5. 写一个 encoder block，再比较 Post-LN 与 Pre-LN。
6. 用本文面试题模拟问答，每题要能接一层追问。

# 公开面试题库与技术讨论交叉参考

- [Learnixo: Transformer Architecture Interview](https://learnixo.io/blog/tx-architecture-interview)：覆盖 $\sqrt{d_k}$、归一化、位置编码等架构追问。
- [AI Engineer Interview Questions: LLM & Transformer Fundamentals](https://github.com/ombharatiya/AI-Engineer-Interview-Questions/blob/main/02-llm-fundamentals/questions.md)：强调 attention 实现、位置信息和现代 attention 变体。
- [DSPrep Transformer Questions](https://dsprep.com/Interview-Questions/Transformers/)：将残差、LayerNorm、Pre/Post-Norm 等组织为追问。
- [DeepLearning.AI 社区：为什么是平方根缩放](https://community.deeplearning.ai/t/what-is-the-rationale-behind-square-root-scaling-in-attention/441193)：聚焦方差与标准差推导。

这些网站是备考材料，不是 Transformer 技术事实的一手权威来源。技术结论以原论文及后续一手论文为准。

# 最后总结

初学者最应记住的不是 28.4 BLEU，而是三件事：

1. Attention 是“用 Q/K 决定读谁，再从 V 读内容”的可学习加权汇总。
2. Transformer 把位置级串行依赖移出了网络主干，换来训练并行和短信息路径，代价是全局 attention 的二次长度成本。
3. 真正的 Transformer block 不只是 attention，而是 attention + FFN + 位置 + 残差 + 归一化 + 遮罩 + 合理训练策略的整体。

## 视觉/公式与自检记录

- Figure 1–2：由 GPT Image 2 基于原图生成中文重绘，已对照节点、顺序、连线、Mask 位置与 Q/K/V 流向。
- Tables 1–4 和 Figures 3–5：保留原文截图，避免对数字证据做生成式重画。
- 公式：已与原文 Eq. 1–3 和位置编码对照。
- 已覆盖原文所有编号节、子节、表格、图、实验、局限和未来方向。原文没有独立算法框。
- 为正式引用或完全复现实验时，仍应回到原论文和官方代码核对。
