# Figure 2 中文重绘规格

- 左图：Scaled Dot-Product Attention。Q 与 K 相乘（K 转置隐含于矩阵乘）→ 按 sqrt(d_k) 缩放 → 可选 Mask → Softmax → 与 V 相乘 → 输出。
- 右图：Multi-Head Attention。Q/K/V 各自经过 h 组学习到的线性投影，并行进入 h 个缩放点积注意力，输出拼接 Concat，再经输出线性投影。
- 中文标签：缩放点积注意力、矩阵乘、缩放、遮罩（可选）、归一化指数函数 Softmax、线性投影、拼接。
- 保留 Q/K/V 英文字母，并在图中表明 Q=查询、K=键、V=值。
- 不得改变：Mask 在 Softmax 之前；V 不参与 QK^T 相似度计算；多头输出先拼接再线性投影。

## 验证记录

- 通过：左图次序为 QKᵀ 矩阵乘 → 除以 √d_k → Mask → Softmax → 与 V 矩阵乘。
- 通过：V 没有进入 QKᵀ 分数计算。
- 通过：右图 Q/K/V 各经 h 组线性投影，h 个头并行，先 Concat 再输出线性变换。
- 通过：Q/K/V 中英文含义可读，未增加不支持的数据或关系。
