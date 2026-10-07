# MySQL 中高级面试深入解读：从原理推演到线上决策

> 整理日期：2026-10-03。面向中高级后端／数据库方向。
>
> 已阅读本目录的《MySQL数据库八股文详细讲解》和《MySQL数据库自测题与参考答案》。本文保留它们的基础地图，重点补充推导过程、边界条件、反例、工程取舍与排障证据。
>
> 技术基线：MySQL 8.0 / 8.4、InnoDB；有版本差异的地方单独说明。实验在独立的 MySQL 8.0.41 实例验证，不能据此声称所有 8.4 行为已经实测。

## 怎么使用这份笔记

中高级面试的核心能力是：**给定 SQL、数据分布、事务时序和故障条件，能够推演结果，并说明用什么证据验证。**

阅读时采用四层回答：先给结论，再解释机制，接着说明条件，最后落到项目或实验。

例如问“为什么这条查询慢”，完整回答应该包含：它扫描了多少候选记录，是否回表，是否额外排序，是否等待锁，数据分布是否导致计划变化，以及改动后的指标。仅回答“加联合索引”还不足以证明你能处理线上问题。

本文中的优先级来自当前公开面试准备资料与工程重要性的综合判断，**不是企业面试题频次统计**。截至整理日，[JavaGuide 的后端面试准备路线](https://javaguide.cn/interview-preparation/backend-interview-plan.html)明确要求索引题结合项目 SQL、数据分布、执行计划和扫描量；[小林的 MySQL 面试题目录](https://xiaolincoding.com/interview/mysql.html)仍集中覆盖索引、事务、锁、日志和复制。它们用于判断复习覆盖面；技术结论以文中链接的 MySQL 官方资料及实验为依据。

| 优先级 | 主题 | 中高级需要达到的程度 |
|---|---|---|
| P0 | 索引与执行计划 | 能为真实 SQL 比较候选索引，解释扫描、回表、排序和写入代价 |
| P0 | MVCC 与隔离级别 | 能推演两个事务的读写结果，识别丢失更新、写偏差及读写混用 |
| P0 | 锁与死锁 | 能沿执行路径分析锁范围，读取等待关系，设计事务重试 |
| P0 | 日志、提交与恢复 | 能区分 WAL、持久化、内部两阶段提交、双写和备份恢复 |
| P1 | 复制与一致性 | 能解释复制延迟、写后读、半同步边界和切换风险 |
| P1 | 线上排障与 DDL | 能根据等待事件、计划、I/O、长事务和 MDL 定位问题 |
| P1 | 业务并发设计 | 能设计扣库存、幂等、任务领取及数据库与消息的协作 |
| P2 | 容量与扩展 | 能判断归档、分区、分片的收益与维护成本 |

建议阅读顺序：先读第 1～5 节，再读第 6～9 节，最后读第 10～14 节。第 13 节可以直接作为实验手册，第 14 节用于口述自测。

## 1. 现有笔记的具体问题与修正

讲解稿适合第一次建立概念，但它以“是什么”和“标准回答”为主，缺少“为什么这个例子成立、换一个条件会怎样”。自测题已经补上不少条件，例如首次快照读、当前读以及不走索引并不自动升级为表锁，这些内容应该保留。

以下区分原稿需要修正的表述，以及值得继续深入的简化：

| 原位置／表述 | 问题 | 更准确的理解 |
|---|---|---|
| 隐藏字段：row_id 超过 2^48 后，新行覆盖旧行造成数据丢失 | 没有给出具体版本、实现与触发条件，不能当成一般使用结论 | 隐藏 row_id 为 6 字节；主键建议首先从可寻址性、二级索引成本和复制运维解释。极端计数器问题必须另找实现证据 |
| VARCHAR：InnoDB 一行约 65535 字节 | 混合了 Server 的行大小约束和 InnoDB 页内记录约束 | MySQL 的 65535 字节行定义限制与 InnoDB 页内本地记录限制是两层；默认 16KB 页下，本地记录上限略小于半页，大字段可有页外存储 |
| 串行化：事务尽量串行执行 | 容易误解成整个数据库一次只运行一个事务 | 目标是效果可串行化；InnoDB 对相关读使用更严格的锁，不相交事务仍可能并发 |
| Buffer Pool 的内容列表包含锁信息、数据字典 | 容易把所有 InnoDB 内存结构都归入页缓存 | Buffer Pool 的核心是缓存数据、索引等页；锁系统、数据字典缓存等应作为独立内存结构理解 |
| 当前读读取最新版本 | 缺少锁冲突条件 | 当前读也要等待冲突事务结束，随后读取可锁定、可更新的最新状态；不会随意读走别人未提交的值 |
| 覆盖索引不用回表 | 作为列覆盖定义正确，作为绝对运行行为过强 | 二级索引可提供所需列，但某些 MVCC 可见性检查仍需访问聚簇记录 |
| 范围后面的字段不能继续定位 | 适合入门，容易被扩大成“后面的列没有用” | 区分范围构造、索引内过滤、覆盖和排序；后续列仍可能发挥作用 |
| redo 刷盘后就能安全提交 | 少了 binlog 及故障边界 | 启用 binlog 时还要讨论日志协调和 sync_binlog；持久化也依赖底层存储兑现刷盘保证 |
| redo prepare → binlog → redo commit | 是逻辑模型，不是每个事务固定进行三次独立刷盘 | prepare、协调记录、引擎 commit 描述事务状态；实际存在组提交，写入与同步需要区分 |
| 偶发变慢主要举刷脏页 | 示例合理，但容易形成单因判断 | 同时检查执行计划、数据参数、锁等待、MDL、缓存、I/O、提交等待及应用连接池 |

行大小条件见[官方 InnoDB Limits](https://dev.mysql.com/doc/refman/8.4/en/innodb-limits.html)；聚簇与二级索引选择见[官方索引组织说明](https://dev.mysql.com/doc/refman/8.4/en/innodb-index-types.html)。下面围绕这些边界展开，不要求你背内部字段大小来代替工程判断。

## 2. 一套能够贯穿查询、并发和恢复的模型

### 2.1 查询：减少要处理的数据，而非单纯“走索引”

```text
SQL 与参数
  → 优化器估算候选路径成本
  → 按索引定位／扫描候选记录
  → 在索引层过滤，必要时访问聚簇记录
  → 关联、排序、聚合、返回结果

贯穿全过程的影响因素：缓存命中、锁／MDL 等待、CPU、存储和网络。
```

一个有用的思考式是：

```text
总延迟 ≈ 等待时间 + 定位成本 + 扫描成本 + 回表成本
        + 关联／排序／聚合成本 + 结果传输成本
```

它不是优化器的精确公式，作用是提醒你：`key` 不为 NULL，只证明选用了某条索引路径。扫描几百万条二级索引再大量回表，仍然可能比顺序扫描慢。

索引至少有四种用途：确定查找范围、在索引中过滤、提供结果列、提供顺序。面试讨论“是否使用索引”时，先明确讨论的是哪一种。

### 2.2 写入：页面、锁与日志一起变化

```text
找到记录并获得必要锁
  → 生成 undo 信息
  → 修改内存中的数据／索引页，产生相应 redo
  → 为 binlog 收集变更事件
  → 提交时协调引擎与 binlog，并按配置持久化
  → 后台在合适时机写出脏页，推进 checkpoint
```

这些步骤描述职责，并非每个底层函数的严格调用顺序。undo 页和二级索引页本身也是数据库页，它们的变化同样需要恢复保护。

由此可以推导：增加一个索引不只增加磁盘占用，还增加页修改、日志生成与维护工作；长事务不仅占连接和锁，还可能延缓旧版本清理；扩大 redo 容量只能缓冲一部分刷页压力，不能无限提高磁盘吞吐。

### 2.3 B+ 树的真正优势：页内扇出与顺序性

先明确一个假设算例：若内部页可用空间约 14KB，一个内部条目约 24 字节，则扇出约为 600；若一个叶子页能放 100 行，三层树的粗略容量约为：

```text
600 × 600 × 100 = 3600 万行
```

这是用于理解数量级的假设，实际值会受键长、行宽、填充率、删除与行格式影响。不能从这个算例推出“超过 2000 万就必须分表”。

还有三个容易漏掉的点：

1. 根页和内部页经常被缓存，树高不等于每次查询的物理 I/O 次数。
2. 叶子页按键有序，不意味着整个 `.ibd` 文件在磁盘上按主键连续排列。
3. 递增主键通常减少随机插入，但仍会发生页分裂；极高写入并发下，末端页还可能成为争用热点。

**中高级回答重点：**B+ 树适合面向页的存储和有序访问，性能还取决于候选扫描、回表、缓存和访问分布。把树高背成固定的“3 次磁盘 I/O”会丢失这些条件。

## 3. 索引设计：从查询反推，而不是背建索引口诀

### 3.1 全文统一的订单查询

实验表有主键 `id`、用户 `user_id`、状态 `status`、金额 `amount`、时间 `created_at` 和较宽的 `remark`。最初只有 `idx_user(user_id)`。

```sql
SELECT id, amount, created_at
FROM orders
WHERE user_id = 42 AND status = 1
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

仅用 `idx_user`，数据库先定位该用户的订单，再读取状态和金额，过滤后获取最新 20 条。这可能涉及大量回表和额外排序。

候选 A：

```sql
CREATE INDEX idx_feed
ON orders(user_id, status, created_at DESC, id DESC);
```

前两列固定后，索引中的后两列恰好按查询要求排序。数据库可以沿顺序找到 20 条，而不必把所有符合条件的订单先排序。

候选 B：

```sql
CREATE INDEX idx_feed_cover
ON orders(user_id, status, created_at DESC, id DESC, amount);
```

B 进一步覆盖金额列，减少为了取金额而进行的聚簇记录访问。**A、B 是需要比较的候选方案，通常择一，不能默认同时上线。**

加入 `amount` 的代价：每条索引记录更宽，缓存能容纳的条目更少，更新金额也要维护该索引。若分页每次只回表 20 次，A 可能已经足够；若查询量非常高、回表 I/O 明显，B 的收益可能更大。这里的决策需要测量。

### 3.2 “区分度最高的列放最前”为什么不完整

对以上查询，`user_id` 与 `status` 都是等值条件，两列都固定后，交换它们仍可能得到相同的候选集合。

真正决定顺序的还包括：是否存在只按用户查询的其他 SQL、是否存在跨用户按状态查询、能否支持排序，以及如何复用已有索引。

低区分度的 `status` 也可能有价值：全表中完成态占 99.9%、待处理态只占 0.1%，查询少量待处理记录时，状态条件就很有筛选作用。不能仅凭“只有两个值”否定索引。

可以按这套过程设计：

1. 收集高频 SQL 与实际参数分布，特别是普通用户与大客户。
2. 先识别等值条件，再识别范围条件和顺序要求。
3. 比较扫描范围、回表次数及能否提前结束。
4. 评估索引复用、空间与写入成本。
5. 用实际计划与负载验证，不仅比较单次耗时。

### 3.3 最左前缀的本质是元组排序

索引 `(a,b,c)` 可以想成按这些元组排序：

```text
(1,10,1)
(1,10,9)
(1,20,2)
(2,10,1)
(2,20,5)
```

`a=1 AND b=10` 可以定位连续片段；只给 `b=10`，这些记录分散在不同 `a` 组内。

| 条件 | 典型推演，最终以计划为准 |
|---|---|
| `a=1 AND b=10 AND c=9` | 可使用完整等值前缀定位 |
| `c=9 AND b=10 AND a=1` | 条件书写顺序通常不改变等价访问机会 |
| `a=1 AND c=9` | 先定位 a 的片段，c 仍可参与过滤；b 的缺失影响更精确定位 |
| `a=1 AND b>10 AND c=9` | 通常主要按 a、b 构造范围，c 仍可索引内过滤或提供覆盖 |
| `a IN (1,2) AND b=10` | 可能拆成多个等值区间，不能把 IN 一律当成截断后续列的范围 |
| `b=10` | 传统前缀查找受限；特定条件下可能全索引扫描或使用 skip scan |

不要把 `>`、`>=`、`BETWEEN`、`LIKE`、`IN` 全部简化成同一种行为。范围端点如何构造、哪些条件需要重检，有具体的优化规则。[官方 Range Optimization](https://dev.mysql.com/doc/refman/8.4/en/range-optimization.html)

skip scan 的思路是枚举缺失的前导列值，再分别进行范围访问。它有适用条件和成本约束；前导列不同值很多时可能不划算。知道这个例外之后，仍然应该优先设计符合主要访问模式的索引。

### 3.4 ICP：减少回表，不等于减少最初扫描范围

假设索引为 `(user_id, amount, status)`：

```sql
SELECT remark FROM orders
WHERE user_id=42 AND amount>500 AND status=1;
```

通常由 `user_id` 和 `amount` 构造扫描范围。`status` 虽然未必能继续精确缩小整个范围，但它已在索引记录中，存储引擎可以先检查状态，只有符合条件时才为了读取 `remark` 去访问聚簇记录。

假设需要扫描 10000 个索引条目，其中 200 个满足状态条件：ICP 的主要收益是减少不必要的聚簇记录访问；它不意味着最初只扫描了 200 条。

因此看到 `Using index condition`，应解释“引擎利用索引列提前过滤”，不能解释为“所有列已经覆盖，不会回表”。[官方 ICP 说明](https://dev.mysql.com/doc/refman/8.4/en/index-condition-pushdown-optimization.html)

### 3.5 覆盖索引有一个 MVCC 边界

二级索引包含索引列及主键值，但不像聚簇记录一样保存完整的隐藏事务信息。当二级索引记录被标记删除，或索引页受较新事务修改而无法直接确认版本可见性时，InnoDB 可能访问聚簇记录并通过 undo 获取正确版本。

所以要分两层说：**列需求上被覆盖，通常能减少取业务列所需的回表；运行中是否完全避免聚簇访问，还受可见性判断影响。**[官方 MVCC 与二级索引说明](https://dev.mysql.com/doc/refman/8.0/en/innodb-multi-versioning.html)

### 3.6 “索引失效”要拆成不同原因

| 场景 | 本质 | 合理处理 |
|---|---|---|
| `DATE(created_at)=...` | 普通索引按原始时间排序，表达式条件未必可转成范围 | 改半开时间区间；特定场景可评估函数索引 |
| 字符串字段与数字参数比较 | 隐式转换可能发生在列上 | 参数类型与字段语义一致，尤其注意前导零 |
| `LIKE '%abc%'` | 普通 B+ 树通常无法通过固定前缀缩小范围 | 根据搜索语义评估全文索引或专门检索方案 |
| OR 中部分分支缺少访问路径 | 整体候选获取成本高 | 评估各分支索引、Index Merge 或语义正确的改写 |
| 索引存在但优化器选全表扫描 | 估算认为扫描大量行并回表更贵 | 先核对命中比例与统计信息，不能直接强制索引 |
| 选择了索引但仍慢 | 候选太多、回表多、排序或等待 | 比较实际行数和等待，而非只看 key |

MySQL 8.0 支持函数索引，但它并非给所有 SQL 套一个函数就能自动受益：表达式匹配、类型与相关限制需要核对。[官方 CREATE INDEX](https://dev.mysql.com/doc/refman/8.0/en/create-index.html)

**面试口述：**“我先分清索引是不可用于构造范围，还是可以使用但成本不合适。再分别检查表达式和参数类型、前缀、命中比例及实际计划。索引列变换、全索引扫描和优化器主动放弃索引，是不同情况。”

## 4. 执行计划：把“会看 EXPLAIN”提升为“能证明优化有效”

### 4.1 传统 EXPLAIN 只回答一部分问题

| 字段／现象 | 正确用途 | 常见误读 |
|---|---|---|
| `type=ref/range/index/ALL` | 描述访问方式 | 固定排名能替代成本判断 |
| `key` | 选择的索引 | key 不为空，所以查询高效 |
| `key_len` | 使用键部分的长度信息 | 越长越好，或覆盖列都一定体现在这里 |
| `rows` | 估算要检查的行数 | 已经实测扫描了这些行 |
| `filtered` | 估算剩余条件的过滤比例 | 是整个 SQL 的最终返回比例 |
| `Using index` | 列覆盖层面的索引访问提示 | 在任何 MVCC 情况下都绝不会访问聚簇记录 |
| `Using index condition` | 索引条件下推 | 等同于覆盖索引 |
| `Using filesort` | 有额外排序步骤 | 一定用了磁盘，一定非常慢 |
| `Using temporary` | 使用内部临时表处理 | 一定已经落盘 |

`index` 可能是完整扫描较小的二级索引；`ALL` 在小表或高命中率查询里可能很合理。`filesort` 也可能完全在内存完成。应评估数据量、资源和目标延迟。[官方 EXPLAIN 输出](https://dev.mysql.com/doc/refman/8.4/en/explain-output.html)、[ORDER BY 优化](https://dev.mysql.com/doc/refman/8.4/en/order-by-optimization.html)

### 4.2 EXPLAIN ANALYZE：看实际执行和估算偏差

```sql
EXPLAIN FORMAT=TREE
SELECT id,amount,created_at FROM orders
WHERE user_id=42 AND status=1
ORDER BY created_at DESC,id DESC LIMIT 20;

EXPLAIN ANALYZE
SELECT id,amount,created_at FROM orders
WHERE user_id=42 AND status=1
ORDER BY created_at DESC,id DESC LIMIT 20;
```

`EXPLAIN ANALYZE` **会真实执行语句**。在学习库使用很方便，在线上要考虑语句本身的工作量，不能把它当成零成本检查。MySQL 8.0.18 起提供此能力。

例如一个演示节点：

```text
(cost=... rows=10)
(actual time=0.050..0.080 rows=100 loops=1000)
```

`actual time` 单位为毫秒，表示首行和完成该迭代器的时间；多次循环时按循环平均，节点时间包含子节点。`rows` 与 `loops` 要一起看：平均每次 100 行，循环 1000 次，意味着大量重复处理。

不能把父子节点时间简单相加，否则会重复统计；也不能把所有节点的 `rows × loops` 都当成物理存储扫描次数。[官方 EXPLAIN ANALYZE](https://dev.mysql.com/doc/refman/8.4/en/explain.html)

### 4.3 本次实验的真实结果

独立 8.0.41 实例、10 万行人工订单、同一查询参数：

| 计划指标 | 仅有 idx_user | 增加 idx_feed_cover 后 |
|---|---|---|
| key | idx_user | idx_feed_cover |
| type | ref | ref |
| rows，估算 | 1000 | 250 |
| filtered，估算 | 10% | 100% |
| Extra | Using where; Using filesort | Using index |

优化后 `EXPLAIN ANALYZE` 的实际索引迭代器输出 20 行、loops=1，并由 LIMIT 提前结束。这个例子说明：访问类型同为 ref，执行工作却不同；估算候选 250 行，也不表示 LIMIT 实际必须遍历完 250 行。

这里只证明这组数据的计划和结果，不把学习库的亚毫秒时间当成线上性能收益。原始结果保存在 [实验验证记录](assets/mysql_interview_validation_20261003.json)。

### 4.4 优化器为什么可能选错

优化器依赖估算。统计过旧、数据倾斜、列之间存在相关性、参数差异以及成本模型与实际存储状况不一致，都可能让估算与真实执行偏离。

典型问题：普通用户只有 100 条订单，大客户有几百万条；同一 SQL 模板，不同 `user_id` 的合适访问路径和耗时不一定相同。

排查顺序：比较估算与实际行数，核对参数分布，再评估更新统计信息、调整 SQL 或索引。需要时使用 optimizer trace 查看成本选择。

```sql
ANALYZE TABLE orders;
-- 针对具体估算问题再评估直方图，而非全表盲目添加。
ANALYZE TABLE orders UPDATE HISTOGRAM ON status WITH 16 BUCKETS;
```

直方图改善部分列值分布的估算，不能代替索引，也不能完整描述多列相关性。8.4 增加直方图自动更新相关能力，不能把 8.0 的维护规则无条件套到 8.4。[官方 Optimizer Statistics](https://dev.mysql.com/doc/refman/8.4/en/optimizer-statistics.html)、[ANALYZE TABLE](https://dev.mysql.com/doc/refman/8.4/en/analyze-table.html)

`FORCE INDEX` 可以作为对照实验或经过证据支持的干预。上线前要覆盖不同参数、负载与数据规模，并准备撤回方式；一次更快不能保证以后始终更快。

### 4.5 JOIN：关注过滤后的驱动结果和重复次数

“小表驱动大表”需要改成“比较过滤后的候选结果以及内表访问成本”。物理上很大的表，经过高选择性条件过滤后也可能很适合成为驱动输入。

对索引嵌套循环：

```text
外表候选 10 万行 × 内表每次索引查找 0.05ms ≈ 5 秒
```

这是解释累积成本的假设算例。即使内表单次查询很快，过多 loops 仍会放大总成本。常见处理是尽早减少驱动行、为关联键建合适索引，或改变查询结构。

MySQL 8 的部分连接可以使用 hash join。它先构造哈希结构再探测，但构建成本、内存和可能的磁盘溢出仍需评估，不能概括成“所有 JOIN 都变成 Hash”。[官方 Hash Join](https://dev.mysql.com/doc/refman/8.4/en/hash-joins.html)

语义也要检查：在 `LEFT JOIN` 后的 WHERE 中加 `right_table.status=1` 会排除右侧为 NULL 的未匹配行；放在 ON 中则保留左侧记录。这种差别首先影响正确性。

## 5. 分页与统计：性能改善也必须保持结果语义

### 5.1 深分页真正浪费在哪里

```sql
SELECT id,amount,created_at FROM orders
WHERE user_id=42 AND status=1
ORDER BY created_at DESC,id DESC
LIMIT 100000,20;
```

OFFSET 要处理并丢弃大量前置结果。若访问路径还需要回表和排序，前置工作会进一步放大。B+ 树不是按“第几条匹配结果”随机定位的数组。

延迟关联可以先只取主键，最后再补业务列：

```sql
SELECT o.id,o.amount,o.created_at
FROM orders AS o
JOIN (
  SELECT id,created_at FROM orders
  WHERE user_id=42 AND status=1
  ORDER BY created_at DESC,id DESC
  LIMIT 100000,20
) AS page ON page.id=o.id
ORDER BY page.created_at DESC,page.id DESC;
```

如果内层使用较窄的覆盖访问，可以减少被丢弃行的取列代价，但**OFFSET 的线性前置工作仍在**。是否 materialize、是否已覆盖及实际收益，要看计划。原稿“按主键分页时先查 id”的例子不保证获得独立窄二级索引的优势，因为 InnoDB 主键叶子本来就保存行数据。

### 5.2 游标分页必须带上完整顺序

命名参数由应用绑定，不是直接可以粘贴到 MySQL 客户端的语法：

```sql
SELECT id,amount,created_at FROM orders
WHERE user_id=:user_id AND status=1
  AND (created_at<:last_time
       OR (created_at=:last_time AND id<:last_id))
ORDER BY created_at DESC,id DESC
LIMIT 20;
```

时间可能重复，`id` 用来给同一时间下的记录确定顺序。只写 `created_at<last_time` 会漏掉相同时间的其他行。

工程上还要检查：游标时间的微秒精度是否丢失、时区是否一致、过滤条件是否改变、排序字段是否会更新、是否允许翻页期间的新记录影响结果。若要求一次导出形成严格一致的集合，需要单独设计快照、截止边界或离线任务，并评估长事务代价。

游标分页方便顺序翻页，任意跳到“第 1000 页”则需要额外的定位设计。不能把它包装成对所有分页产品都无代价的替换。

### 5.3 COUNT 为什么仍需要工作

`COUNT(*)` 统计行；`COUNT(col)` 只统计该表达式非 NULL 的行。不要背固定性能排序来替代语义判断。

InnoDB 的事务可能看到不同版本，因此不能拿一个全局固定行数同时回答所有事务的一致性计数。无过滤条件时，也通常需要扫描合适索引，遇到过滤和 JOIN 还要按计划完成相应工作。[官方 COUNT 行为](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

可选设计取决于准确性需求：

- 一般列表用 `LIMIT page_size+1` 判断还有没有下一页，避免每页精确总数。
- 展示近似规模可使用统计或缓存，但必须标注业务可接受的精度与更新周期。
- 必须精确且查询频繁时，可设计计数汇总；同时处理事务更新、并发热点、补偿和对账。

**追问：索引让 COUNT 变成 O(1) 吗？**普通 B+ 树叶子并没有为任意事务、任意谓词保存可直接读取的精确结果。索引往往缩小扫描或降低扫描宽度，不能自动消除计数工作。

## 6. MVCC 与隔离级别：先推演可见性，再谈异常

### 6.1 Read View 保存的是可见性规则

Read View 不是把整张表复制一份到内存。读记录时，根据版本的事务 ID 判断可见性，必要时沿 undo 重建历史版本。

为避免源码中 up/low 命名的混淆，用三个教学符号：

- `A`：视图建立时仍活跃的读写事务 ID 集合。
- `L`：该集合的最小事务 ID；没有活跃事务时按视图边界处理。
- `U`：视图建立时的高水位，理解为尚未分配的下一个事务 ID 边界。

对某版本的修改事务 ID `t`，简化判断过程：

```text
如果是本事务自己的修改 → 可见
否则 t < L             → 可见
否则 t >= U            → 不可见
否则 L <= t < U：
    t 在 A 中          → 不可见
    t 不在 A 中        → 可见
不可见时，继续找旧版本；没有可见版本则该行对本次读不存在。
```

假设 `A={100,103}`、`L=100`、`U=105`：99 可见，100 不可见，102 可见，103 不可见，105 不可见。

**为什么不能仅判断 t 是否比当前事务 ID 小？**因为事务 ID 的先后不等于提交顺序。100 可能还没提交，102 已经提交；需要活跃事务集合区分。

即使事务 100 后来提交，它对已经建立的 RR 视图也不会突然变得可见。读的是该视图定义的边界。实际实现还有只读事务优化和 purge 相关编号，不必把这个教学模型当成所有字段的完整定义。[官方 ReadView 源码文档](https://dev.mysql.com/doc/dev/mysql-server/8.0.45/classReadView.html)

### 6.2 快照何时建立：BEGIN 不是快照时间

在常见 RC 一致性读中，每条读语句建立新的视图；RR 通常复用第一次一致性读建立的视图。

```text
A：BEGIN
B：更新并 COMMIT
A：第一次普通 SELECT
```

A 的 RR 首次读可以看到 B 的提交，因为 B 的提交发生在视图建立之前。原自测题在这里已经写对。

需要在事务开始时建立一致性快照，可在合适的 RR 场景使用：

```sql
START TRANSACTION WITH CONSISTENT SNAPSHOT;
```

语句有隔离级别条件，不能把它当成所有级别下都固定整份快照的开关。[官方一致性非锁定读](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)

### 6.3 RR 的 UPDATE 为什么能基于 200，而 SELECT 仍看到 100

初始余额 100，按下列顺序：

| 步骤 | 事务 A | 事务 B |
|---|---|---|
| 1 | BEGIN；普通 SELECT，R1=100 | |
| 2 | | 更新余额为 200，COMMIT |
| 3 | 普通 SELECT，记 R2 | |
| 4 | `UPDATE ... SET balance=balance+10` | |
| 5 | 普通 SELECT，记 R3 | |

本次 8.0.41 实验：

| A 的隔离级别 | R1 | R2 | R3 |
|---|---:|---:|---:|
| RC | 100 | 200 | 210 |
| RR | 100 | 100 | 210 |

RR 的 R2 使用旧视图；UPDATE 是加锁的当前读，基于最新可更新的 200 计算；R3 能看到本事务自己的修改。

所以“RR 下同一事务所有 SQL 都看到 BEGIN 那一刻的数据”是错误理解。普通一致性读、加锁读和本事务修改的可见性必须分别推演。

若 B 尚未提交并持有冲突锁，A 的 UPDATE 会等待；B 提交或回滚后，再继续处理。当前读不是脏读。

### 6.4 把幻读问题说清楚

先问清面试官说的是哪一种读：

1. **重复普通一致性读：**RR 复用视图，其他事务后来插入的记录通常不可见。
2. **重复加锁范围读：**RR 通过相关索引范围的记录／间隙锁限制其他事务插入，从而保护范围。
3. **先普通 SELECT，再 UPDATE 或 FOR UPDATE：**两种读取机制可以观察不同的状态，不能要求当前读遵循旧快照。

“RR 完全等同于 SERIALIZABLE”与“RR 对幻读毫无保护”都过度概括。官方也提醒，在同一 RR 事务混用加锁和非加锁语句时，需要认真处理两种状态的差异。[官方隔离级别](https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-isolation-levels.html)

### 6.5 中高级加分点：丢失更新与写偏差

**丢失更新：应用拿旧值覆盖当前值。**

```text
A 普通读 balance=100
B 普通读 balance=100
A 在应用算出 110，UPDATE SET balance=110，提交
B 在应用也算出 110，UPDATE SET balance=110，提交
最终 110，而不是预期 120
```

两个 UPDATE 本身都获得了锁，RR 也没有阻止 B 使用旧业务计算结果执行无条件覆盖。本次已复现。

可以根据业务选择：`balance=balance+10` 的数据库原子更新；带条件或版本号的 CAS；先 FOR UPDATE 再基于当前值决策。不同方案适合不同业务。

**写偏差：两个事务修改不同行，却共同破坏约束。**

```text
规则：两名值班人员中，至少一名在岗。
A 和 B 都在 RR 快照中看到两人均在岗。
A 将人员 1 改为离岗；B 将人员 2 改为离岗。
两者更新不同主键，都提交，最终无人值班。
```

本次实验的最终在岗数确为 0。它证明：稳定的快照与单行写锁，不会自动保护所有跨行业务约束。

处理方向：把共享规则映射到共同的可锁定资源，统一串行检查与修改；或对相关集合使用合适的加锁读；或评估 SERIALIZABLE 并处理事务失败。若使用共同“守卫行”，要在读取业务判断所依赖的状态之前取得锁，并保证检查使用正确的当前状态，不能拿早已建立的旧快照继续判断。

### 6.6 RC 与 RR 的选择应围绕业务访问方式

| 需求／现象 | 决策问题 |
|---|---|
| 多次报表读取需要稳定结果 | RR 一致性视图有帮助，但长快照影响版本清理 |
| 高并发短事务、范围写冲突多 | RC 的间隙锁策略可能减轻部分冲突，但结果会随语句变化 |
| 余额、库存、状态转换 | 先设计原子条件更新或正确锁定；隔离级别不能代替业务检查 |
| 跨行约束 | 需要统一资源协调或更严格隔离，并处理重试 |

SERIALIZABLE 追求可串行化效果，不是把整个数据库变成单线程。InnoDB 在多语句事务中的普通读可转为共享加锁读；autocommit 下独立只读语句又有例外。应描述访问冲突与实现条件，而非一句“所有事务排队”。

### 6.7 长事务为什么拖累查询和空间回收

一个事务长期持有旧 Read View，purge 就可能无法移除其他事务仍需保留的历史版本。业务上的 DELETE 已经提交，不等于旧版本立即物理移除，也不等于文件立即缩小。

结果可能包括 undo 历史积累、更多旧版本重建、删除记录滞留、额外扫描和磁盘压力。**只读但长期保持的快照也值得检查，不能只盯住 UPDATE 很多的事务。**

观察长事务、history list length、purge 进度与业务时间线；不能把 history list length 直接解释为“undo 字节数”或“删除行数”。[官方 Purge Configuration](https://dev.mysql.com/doc/refman/8.4/en/innodb-purge-configuration.html)

## 7. 锁：沿执行路径判断，而不是只看 WHERE

### 7.1 两个必须先说清的事实

第一，InnoDB 的记录锁落在索引记录上；没有业务索引时，也会通过聚簇索引扫描并锁定。第二，锁范围取决于访问路径、隔离级别、索引唯一性、是否命中、SQL 类型和边界。

因此，一条 SQL 更换索引或计划，可能同时改变性能与并发冲突。评审索引变更时也要评估事务行为。

分析题按以下步骤：

1. 写出版本、隔离级别、事务范围及 autocommit 条件。
2. 确认实际使用哪个索引，有必要时在实验中固定路径。
3. 画出按索引键与主键组成的有序记录。
4. 确认等值／范围、唯一／非唯一、命中／未命中。
5. 分析记录、间隙与边界，再检查聚簇记录锁。
6. 列出另一事务请求的锁，判断兼容或等待。
7. 用 data_locks 与 data_lock_waits 验证。

### 7.2 锁的职责不要混淆

| 类型 | 保护对象／用途 | 需要掌握的边界 |
|---|---|---|
| 记录 S/X 锁 | 索引记录 | X 锁会阻止冲突修改，但普通一致性读通常仍可读取可见版本 |
| gap lock | 索引间隙中的插入 | 纯间隙锁彼此可共存，不锁现存端点记录 |
| next-key lock | 记录和前方间隙 | 典型表示 `(前一键,当前键]` |
| 插入意向锁 | 声明准备插入某间隙位置 | 不同位置插入未必互斥，仍可能被间隙锁阻塞 |
| IS/IX 意向锁 | 表中将有行级锁 | IX 与 IX 可兼容，不表示整表写入被互斥封锁 |
| MDL | 表等对象的结构定义 | 与数据记录锁不同，长事务也可能阻塞 DDL |

`FOR UPDATE` 要持续保护后续 SQL，必须放在明确的事务范围中。若每条语句执行完就提交，先查后改之间没有持续的事务保护。[官方 Locking Reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)

### 7.3 唯一索引等值命中与未命中，结果完全不同

实验主键为 `10,20,30,40`。

```sql
-- RR，显式事务 A
SELECT * FROM lock_demo WHERE id=20 FOR UPDATE;
```

核心是 id=20 的记录 X 锁。B 更新 20 等待；B 插入 12 不因该记录锁等待。

换成不存在的 15：

```sql
SELECT * FROM lock_demo WHERE id=15 FOR UPDATE;
```

没有查到行，仍会保护所在的 `(10,20)` 间隙。本次实测：

| B 的独立操作 | 结果 |
|---|---|
| 插入 id=12 | 等待，实验中触发 1205 超时 |
| 插入 id=15 | 位于同一间隙，会受阻 |
| 更新 id=20 的 payload | 不因这个纯间隙锁受阻 |
| 插入 id=25 | 不因这个间隙锁受阻 |

这里默认没有其他锁、外键或唯一键冲突。**锁住 15 的缺失位置，会连带阻止同一物理索引间隙中的 12，锁保护范围可能大于业务谓词本身。**

### 7.4 非唯一二级索引必须按完整元组画图

实验数据：

```text
主键 id：  10       20       30       40
索引 k：    1        2        2        3
二级键： (1,10)  (2,20)  (2,30)  (3,40)
```

```sql
SELECT * FROM lock_demo FORCE INDEX(idx_k)
WHERE k=2 FOR UPDATE;
```

本次 8.0.41 实测的相关锁：

```text
idx_k：next-key ((1,10),(2,20)]
idx_k：next-key ((2,20),(2,30)]
idx_k：gap      ((2,30),(3,40))
PRIMARY：id=20、id=30 的记录锁
表：IX
```

这比“锁住 k=2”多了一层物理解释：重复键靠主键细分位置；最后的 gap 用来保护继续出现符合条件的新键。插入 `(k=2,id=25)` 因而等待。

上述区间是**已给定数据和固定访问路径的一次实际结果**。范围查询、LIMIT、不同索引与版本的边界处理不能都套用这张图。遇到最大键之后，还要考虑 supremum 所代表的尾部间隙。

### 7.5 两个常见误区

**误区一：间隙锁都是 X 锁，所以相互排斥。**纯 gap lock 主要抑制插入，两个事务可以同时持有同一间隙锁。本次两个事务都查询不存在的 id=15 FOR UPDATE，二者都成功获得保护。之后如果都想插入该间隙，还可能出现等待与死锁。

**误区二：不走索引，行锁自动升级成表锁。**InnoDB 并不因为扫描条数多就执行这种自动升级。RR 中全聚簇扫描可能锁住大量记录与间隙，造成类似全表写入受阻的效果，但锁结构与机制不同。[官方 InnoDB Locking](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)

### 7.6 RC 能减轻哪些锁，又不能保证什么

RC 的普通搜索和索引扫描通常不采用 RR 那样的间隙保护；外键和重复键检查仍有例外。UPDATE 还可能用半一致性读先判断已锁行是否符合条件。

不匹配记录的锁通常会按相关规则释放，但不要扩大为“RC 永远只锁最终修改行”：二级索引访问、剩余条件检查以及索引维护都需要考虑。官方列出的不同索引条件示例就说明，两个最终修改不同行的语句仍可能发生冲突。[官方不同 SQL 的加锁行为](https://dev.mysql.com/doc/refman/8.4/en/innodb-locks-set.html)

**面试口述：**“我不会只从 WHERE 推锁范围。先看隔离级别和实际索引，再按索引元组画命中记录及间隙，检查聚簇记录锁和等待方请求，最后用 Performance Schema 确认。”

## 8. 死锁与业务并发：让失败成为可处理的流程

### 8.1 死锁是等待图出现环

```text
A 持有 id=1，等待 B 的 id=2
B 持有 id=2，等待 A 的 id=1
```

如果开启检测，InnoDB 会选择事务回滚以打破环；它倾向选择修改量较小的事务，而不能概括成“一定杀后来的事务”。热点大量等待时，检测本身也有成本。[官方 Deadlock Detection](https://dev.mysql.com/doc/refman/8.4/en/innodb-deadlock-detection.html)

“统一加锁顺序、短事务、合适索引”能降低概率，不代表数据库再也不会死锁。唯一键检查、外键、二级索引维护和锁升级请求也可能参与冲突。

### 8.2 1213 与 1205 的回滚范围不同

| 情况 | InnoDB 通常的行为 | 应用设计 |
|---|---|---|
| 1213：检测到死锁 | 回滚整个受害事务 | 重新执行完整事务，并重新读取决策所需状态 |
| 1205：锁等待超时 | 默认只回滚当前语句，相关配置可改变行为 | 明确回滚策略；业务上常选择主动回滚完整事务再有限重试 |
| 唯一键冲突 | 通常回滚失败语句 | 区分重复请求与真正的业务冲突 |

不能捕获一个异常后，默认此前 SQL 全部撤销，继续执行下一步。也不能无条件重试已经发生的外部支付或消息发送。[官方 InnoDB Error Handling](https://dev.mysql.com/doc/refman/8.4/en/innodb-error-handling.html)

适合事务重试的结构：

```text
有限次数循环
  → 开始一个新事务
  → 重新读取／锁定业务状态
  → 执行全部数据库操作
  → 提交
如果是可重试并发失败：明确回滚，短暂退避并加入随机抖动
超过上限：返回可识别错误，记录证据
```

Java 中，重试控制要在创建事务的边界之外，每次重试得到新事务；同时核对 Spring 代理调用、回滚规则与异常传播。不能在同一个已回滚或 rollback-only 的事务里反复重试 SQL。

### 8.3 扣库存：将判断和修改放进同一条语句

以下是业务设计示例，`:qty` 必须是经过校验的正数，`product` 不属于实验准备表：

```sql
UPDATE product
SET stock=stock-:qty
WHERE id=:product_id AND stock>=:qty;
```

检查影响行数是否为 1。检查与修改在当前读和锁保护下完成，可以避免两个请求都根据普通 SELECT 的旧库存扣减。

但它只解决这一行的条件扣减：订单写入、优惠额度、请求幂等和消息通知仍需设计。如果同一次请求因网络超时再次执行，又没有幂等约束，库存仍可能被重复扣减。

### 8.4 乐观锁是冲突检测，仍然会使用数据库锁

```sql
UPDATE accounts
SET balance=:new_balance, version=version+1
WHERE id=:id AND version=:old_version;
```

影响 0 行意味着条件没有成功匹配，可能是版本已变化或目标不存在。重新读取并按业务策略决定是否重试，不能只替换新版本号后盲目沿用旧计算结果。

CAS 的 UPDATE 内部仍需要数据库锁。“乐观”指应用先计算，提交时检查冲突；它不表示 InnoDB 不加锁。

version 比“旧业务值相等”更容易表达状态变更历史，但依赖所有相关写入都正确更新版本号。

### 8.5 幂等：唯一键保护请求身份，事务保护业务效果

“先查询 request_id 不存在，再插入”有并发窗口。更可靠的基础是非空、语义明确的唯一键，由数据库裁决并发请求。

实验中的 `biz_request.request_id` 使用 `NOT NULL` 和区分大小写的排序规则。因为 MySQL 的可空唯一列允许多个 NULL，大小写比较规则也会影响哪些字符串被判为同一个身份。

完整幂等设计还要说明：

- 请求记录与扣库存／写订单是否在同一事务提交。
- 同一 request_id 携带不同参数时如何拒绝，而非默默复用旧结果。
- 请求处理中、已成功、已失败时如何向重试方返回结果。
- 数据库已提交但客户端没收到响应时，怎样查回原来的结果。

不要把 `INSERT IGNORE` 当成完整幂等方案，它可能弱化错误反馈。捕获预期的唯一键冲突，再读取并核验原请求，业务语义更清楚。

### 8.6 数据库事务与消息：内部 2PC 不会自动覆盖 MQ

```text
数据库提交后发消息：提交成功，发送前进程崩溃 → 消息缺失
先发消息再提交：消息已发，数据库回滚 → 消费者收到无效事件
```

常见设计是事务内同时写业务表和 outbox，提交后由独立过程可靠投递。投递可能重复，消费者仍需去重或幂等；还要有重试、积压监控和对账。

这与 InnoDB 和 binlog 的内部协调是不同边界。面试时应先说明你要保证的是单库事务、数据库与消息的最终一致，还是跨服务强一致，再比较方案。

### 8.7 SKIP LOCKED 适合队列表，不适合普通余额判断

```sql
START TRANSACTION;
SELECT id FROM job_queue
WHERE state=0 ORDER BY id LIMIT 2
FOR UPDATE SKIP LOCKED;
-- 在同一短事务中把选出的任务标记为已领取，再提交。
COMMIT;
```

多个消费者可以跳过已被其他消费者锁住的任务。实验中 A 锁住任务 1，B 返回任务 2、3。

它返回的集合会跳过锁住的行，不是一般业务所需的完整一致视图。还需设计领取超时、工作进程崩溃后的重新领取、幂等执行和可能的饥饿；不要在持锁事务里做长时间的远程工作。[官方 NOWAIT 与 SKIP LOCKED](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)

## 9. 日志与恢复：区分原子性、持久化与事务协调

### 9.1 三种日志分别解决什么缺口

| 日志 | 主要职责 | 无法单独替代的能力 |
|---|---|---|
| undo | 撤销修改、重建部分历史版本 | 不负责把已提交修改可靠地重做出来 |
| redo | 为页修改提供崩溃恢复能力 | 不承担长期完整业务变更历史与任意时间点恢复 |
| binlog | Server 层变更事件、复制、结合备份恢复 | 不替代 InnoDB 的页面级恢复与运行中回滚机制 |

“undo 记录旧值、redo 记录新值”只适合作为记忆线索。undo 记录能撤销相关操作的信息；redo 是面向 InnoDB 页变化的重做记录；ROW binlog 描述行事件。它们不是三份一模一样的 SQL 文本。

redo 的容量受到管理，旧空间需要随着 checkpoint 推进而复用。binlog 按文件积累，但也有保留周期与清理策略，因此“binlog 永久保存所有历史”同样不成立。

### 9.2 WAL 真正要求的顺序

WAL 的核心是：**把数据页写到持久存储之前，保护该页修改所需的日志必须先达到必要的持久化位置。**

可以先改内存页、生成日志，再同步日志；它不是“先把所有 redo 刷盘，才能修改内存”。事务提交时通常也不需要把所有业务脏页都刷完。

反过来，未提交事务修改过的页也可能被刷出。数据库通过日志和恢复逻辑处理这种情况，并非只允许已提交内容进入磁盘。

这解释了为什么恢复既需要 redo，也需要 undo：先使页面恢复到合理的日志状态，再处理未完成事务的撤销与未决事务的提交决策。不能把恢复过程概括为“只重放提交事务的 SQL”。[官方 InnoDB Recovery](https://dev.mysql.com/doc/refman/8.4/en/innodb-recovery.html)

### 9.3 内部两阶段提交：持久化提交决策

假设启用了 binlog、使用 InnoDB，且必要日志按可靠配置持久化：

```mermaid
flowchart LR
    A[执行修改并生成日志] --> B[InnoDB prepare]
    B --> C[binlog 完整事务及提交标识持久化]
    C --> D[InnoDB commit]
    D --> E[向客户端返回成功]
```

以上是逻辑状态模型，不表示各阶段各自独立刷盘一次。binlog 与引擎依靠事务标识关联，恢复时处理 prepare 状态的未决事务。

| 崩溃时已可靠保存的状态 | 逻辑恢复判断 |
|---|---|
| 尚未形成提交决策 | 按未完成事务处理并撤销必要修改 |
| 引擎 prepare，找不到完整对应的 binlog 提交记录 | 不能当成已提交，恢复时回滚该未决事务 |
| 引擎 prepare，对应 binlog 事务和提交标识已经持久化 | 根据匹配事务标识完成引擎提交 |
| 提交已完成，业务数据页还没写出 | 利用日志恢复相应页修改 |

所以“先写 redo，再写 binlog 为什么不行”的准确含义是：**不能让两个系统各自独立决定完成提交，缺少恢复时统一的决策证据。**正常实现中先 prepare 并不意味着事务已最终提交。[官方 Binary Log 与崩溃恢复](https://dev.mysql.com/doc/refman/8.4/en/binary-log.html)

内部 2PC 与外部 XA 共享部分事务协调思想，但普通业务事务不需要你手工发 XA PREPARE。它也不等于分布式数据库或 MQ 已加入同一个事务。

### 9.4 组提交：逻辑提交顺序与物理刷盘次数不同

多个并发事务可以把日志写入与同步成本合并分摊。即使可靠配置要求日志在成功返回前持久化，也不意味着每个事务独享一轮 fsync。

`sync_binlog=1` 的同步单位可以是提交组。讨论提交延迟应同时观察日志同步耗时、组提交、存储能力以及可能的半同步 ACK 等待，不能把固定的“三阶段”直接乘成三次磁盘延迟。

### 9.5 innodb_flush_log_at_trx_commit 与 sync_binlog

先单独理解 redo 参数，在默认日志周期配置下：

| 值 | 基本行为 | 故障边界 |
|---|---|---|
| 1 | 提交时要求写日志并同步到持久存储 | 依赖存储兑现同步保证，组提交可分摊操作 |
| 2 | 提交写到 OS 缓存，通常由周期任务同步 | mysqld 进程故障与 OS／断电故障不能等同分析 |
| 0 | 通常由周期任务写入并同步 | 未写出的日志可能在进程故障中丢失 |

“0 和 2 最多丢 1 秒”不是严格保证：周期配置、调度延迟与内部刷盘都会改变窗口。[官方 InnoDB 日志参数](https://dev.mysql.com/doc/refman/8.4/en/innodb-parameters.html#sysvar_innodb_flush_log_at_trx_commit)

启用 binlog 时，再看 `sync_binlog`。常见可靠配置组合是：

```text
innodb_flush_log_at_trx_commit = 1
sync_binlog = 1
```

这是对已确认提交的持久性与两个日志恢复一致性的常见基础，不替代备份，也不保证被任意选择的新主库已经拥有事务。

**追问：COMMIT 网络超时，事务到底成功了吗？**可能数据库已提交，只是响应没有送达；也可能未提交。客户端的异常不等于数据库回滚。应用应使用幂等请求标识查询状态和安全重试，不能直接再扣一次库存。

### 9.6 doublewrite 保护的是页面完整性

默认页通常为 16KB，底层写入不一定具备整页原子性。故障可能导致只写了半页。redo 能描述修改，并不意味着任意损坏页都能作为可靠恢复基底。

doublewrite 在页写入最终数据位置前，先保存可用于恢复的页副本。发生不完整页写入时，从完整副本恢复页，再进行必要的日志恢复。

因此：redo 负责重做，doublewrite 处理部分页写入，两者职责不同。批量顺序写与同步合并也使其代价不能简单理解成“每次 I/O 严格翻倍”。8 系列还存在独立 doublewrite 文件等实现变化。[官方 Doublewrite Buffer](https://dev.mysql.com/doc/refman/8.4/en/innodb-doublewrite-buffer.html)

### 9.7 checkpoint 与 redo 容量

LSN 可以理解为日志进展的位置。checkpoint 描述已满足相应落盘条件、恢复时可以从其后继续工作的进度；刷新采用渐进方式，不要求一次刷空整个 Buffer Pool。

当日志产生速度长期大于存储刷新能力，checkpoint 无法足够快推进，可复用的 redo 空间不足，前台写入可能受到压力。

扩大 redo 容量能增加缓冲窗口，平滑一部分突发负载；若持续写入远超设备能力，最终仍会积压。还需评估故障恢复工作量，不能单凭容量越大越好。[官方 Checkpoints](https://dev.mysql.com/doc/refman/8.4/en/innodb-checkpoints.html)

版本提醒：8.0.30 起支持动态 redo 容量配置，8.4 应围绕 `innodb_redo_log_capacity` 理解；不要始终按 5.7 的 `ib_logfile0/1` 固定文件模型回答。[官方 Redo Log](https://dev.mysql.com/doc/refman/8.0/en/innodb-redo-log.html)

### 9.8 宕机恢复与误删恢复是两类问题

宕机恢复解决“不完整执行和未落盘页修改”；误删之后如果 DELETE 已正确提交，数据库不会判断它是人为错误并自动撤销。

时间点恢复通常需要可用的一致性基础备份，以及之后连续保留的 binlog：在隔离恢复环境中恢复基线，重放到错误事务之前的明确边界，再验证数据并决定回迁方式。

必须说明事务边界、binlog 保留是否完整、备份是否真实可恢复、RPO 与 RTO。仅有 binlog 而没有对应基线，不能保证重建全部历史数据；undo 也不是无限保留的误删保险。[官方时间点恢复](https://dev.mysql.com/doc/refman/8.4/en/point-in-time-recovery.html)

## 10. Buffer Pool 与线上排障：区分工作多和等待久

### 10.1 缓存页不等于缓存 SQL 结果

Buffer Pool 的核心是缓存 InnoDB 页面。同一页中的不同记录可以被不同查询复用；它不以 SQL 字符串为键直接返回结果集。

内存容量还要给连接、排序、关联、临时表和其他系统结构留空间。特别是高并发下，不能把所有内存都分给 Buffer Pool，然后忽略每连接或每操作的内存开销。

改进 LRU 使用 old/young 分区和晋升控制，减少一次性大扫描对热点的挤压。长报表、导出和预读仍需要结合访问模式评估，不能把这个机制理解为“全表扫描完全不影响缓存”。[官方 Buffer Pool](https://dev.mysql.com/doc/refman/8.4/en/innodb-buffer-pool.html)

### 10.2 命中率 99.9% 为什么仍可能有很高 I/O

一个假设：每秒逻辑页读取 100 万次，即使 99.9% 命中，仍约有每秒 1000 次物理页读取需求。较慢存储或高延迟场景下，这仍然可能影响响应。

还要看绝对读取速率、磁盘服务时间、缓存工作集及尾延迟。命中率是比例，不能单独证明 I/O 足够轻。

用同一采样窗口的计数器增量估算：

```text
近似命中率 = 1 - ΔInnodb_buffer_pool_reads
                  / ΔInnodb_buffer_pool_read_requests
```

这是便于监控的近似表达，需要确认指标含义、采样和重启／重置情况。不要把服务器启动以来的累计比率当成当前故障窗口的比率。

### 10.3 先按证据分类

```text
接口变慢
  ├─ 尚未发到 MySQL：连接池排队、应用线程、网络
  └─ MySQL 已收到
      ├─ 等待：记录锁、MDL、日志同步、资源争用
      └─ 执行工作：扫描、回表、JOIN、排序、聚合、传输
```

| 现象 | 优先证据 | 可能的处理方向 |
|---|---|---|
| 同模板某些参数很慢 | 参数分布、估算／实际行数、计划 | 处理倾斜、重设访问路径 |
| 写 SQL 停住，CPU 不高 | data_lock_waits、阻塞事务、事务年龄 | 缩短持锁时间、减少范围，处理阻塞方 |
| 普通 SELECT 集体卡住，刚上线 DDL | metadata_locks、Waiting for table metadata lock | 检查 MDL 等待链与长事务 |
| 夜间报表后查询变慢 | 物理读速率、报表时间、热点变化 | 调整报表、隔离负载、评估缓存容量 |
| 写高峰提交延迟增大 | redo／binlog 同步等待、存储延迟、半同步 ACK | 找具体等待环节，平滑负载 |
| 大写入后抖动 | dirty pages、刷页、checkpoint、存储队列 | 拆批次、减少写放大、评估存储与容量 |
| 扫描量不大但返回很慢 | 返回数据体积、客户端消费、网络 | 减少宽列和结果数量，优化消费路径 |

同一 SQL 文本、相同返回行数，不代表参数、计划、缓存和等待相同。线上偶发慢不能只归因于索引失效或刷脏页。

### 10.4 一套能够使用的观察语句

下面是 MySQL 8 常用的只读诊断入口，权限和采集开关会影响能看到的内容。实验中已验证 data_locks 的实际锁输出，未对真实业务库运行这些排障步骤。

```sql
SHOW FULL PROCESSLIST;

SELECT trx_id,trx_state,trx_started,trx_mysql_thread_id,trx_query
FROM information_schema.innodb_trx
ORDER BY trx_started;

SELECT object_schema,object_name,index_name,lock_type,
       lock_mode,lock_status,lock_data,engine_transaction_id
FROM performance_schema.data_locks;

SELECT requesting_engine_transaction_id,
       blocking_engine_transaction_id,
       requesting_engine_lock_id,blocking_engine_lock_id
FROM performance_schema.data_lock_waits;

SELECT object_type,object_schema,object_name,
       lock_type,lock_duration,lock_status,owner_thread_id
FROM performance_schema.metadata_locks
WHERE object_schema='mysql_interview_lab_20261003';

SELECT digest_text,count_star,
       sum_timer_wait/1000000000000 AS total_seconds,
       sum_rows_examined,sum_rows_sent
FROM performance_schema.events_statements_summary_by_digest
WHERE schema_name='mysql_interview_lab_20261003'
ORDER BY sum_timer_wait DESC LIMIT 10;

SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool%';
SHOW GLOBAL STATUS LIKE 'Innodb_log_waits';
SHOW ENGINE INNODB STATUS;
```

在 mysql 命令行里，可使用 `SHOW ENGINE INNODB STATUS\G` 竖向显示。要把事务 ID、线程 ID、连接 ID 的关系分清，不能直接把 engine_transaction_id 当成可 KILL 的连接 ID。

`Innodb_log_waits` 与日志缓冲等待有关，不是所有 redo 容量与提交同步压力的万能指标。应配合具体等待事件、LOG 部分和存储监控判断。[官方 InnoDB 与 Performance Schema](https://dev.mysql.com/doc/refman/8.4/en/innodb-performance-schema.html)

### 10.5 优化验证要覆盖整个工作负载

一次修改后至少比较：

- 相同数据与参数下，结果是否一致，扫描和关联工作是否减少。
- 普通参数、极端参数，缓存较冷与较热时，延迟是否稳定。
- 合理并发下的吞吐、p95/p99、I/O、锁等待与提交延迟。
- INSERT／UPDATE 的维护成本、索引体积及空间增长。

“第二次查询快了”可能只是缓存暖了；“返回 20 行”不能说明只处理了 20 行。实验记录中的速度仅用于观察，本文没有进行生产负载基准测试。

## 11. 复制与一致性：日志收到、事务应用和读可见是不同状态

### 11.1 复制链路

```text
主库完成业务变更并写 binlog
  → 副本 receiver 接收事件，写 relay log
  → applier／worker 应用事务
  → 副本完成提交
  → 具体读语句按自己的隔离规则读取
```

每个箭头都可能引入延迟：网络、日志接收、事务执行、锁、磁盘、大事务，以及并行应用的依赖。

GTID 为事务提供身份与已执行集合，便于定位和切换。它不会凭空消除传输和应用延迟，也不直接实现业务请求幂等。[官方 GTID 说明](https://dev.mysql.com/doc/refman/8.4/en/replication-gtids.html)

### 11.2 半同步复制为什么不能保证任意副本写后读

启用并正常运行的半同步流程会等待配置数量的副本确认。副本确认是在事务事件写入并刷入 relay log 之后；**确认并不要求事务已经应用并在副本提交。**

还要注意：确认的是配置中的部分副本；等待超时可能降为异步；等待点配置影响主库提交和可见性时序。因此“半同步开了，随便读一个副本都能读到刚写的数据”不成立。[官方 Semisynchronous Replication](https://dev.mysql.com/doc/refman/8.4/en/replication-semisync.html)

### 11.3 写后读的三种常见选择

| 方案 | 优点 | 边界 |
|---|---|---|
| 必须新鲜的请求读主库 | 实现直观 | 给主库增加读负载，跨区域有延迟 |
| 写后短时间固定读主库 | 常见工程折中 | 固定时间窗不构成严格一致保证，延迟可能超过窗口 |
| 按本次提交 GTID 等待指定副本应用 | 提供明确的应用进度证据 | 需要捕获本次事务 GTID、等待超时处理，以及正确的读取快照 |

第三种的读取逻辑示意：

```sql
-- 应用已经获得必须可见的事务 GTID，且在目标副本上执行。
SELECT WAIT_FOR_EXECUTED_GTID_SET(:required_gtid,1);
-- 返回 0 才表示成功；返回 1 为超时，需要按策略读主库或报错。
-- 然后开始合适的新读取／新事务，再查业务数据。
```

这段命名参数由应用绑定。不要把主库整个全局 `gtid_executed` 随手当成本次事务的 GTID，它可能包含无关并发提交。

还有一个高级边界：如果副本上的 RR 事务已经建立旧快照，即使等待函数确认 GTID 已应用，旧快照也不会自动刷新。因此要设计等待与建立新读视图的顺序。[官方 GTID Functions](https://dev.mysql.com/doc/refman/8.4/en/gtid-functions.html)

### 11.4 主从延迟要先区分接收慢还是应用慢

```sql
SHOW REPLICA STATUS;
SELECT * FROM performance_schema.replication_applier_status_by_worker;
```

先看线程是否运行、是否报错，再看接收与已执行进度、事务执行与等待。时间型延迟指标有场景限制，不能仅凭 `Seconds_Behind_Source=0` 就证明当前每个写请求都已可在副本读到。

若 receiver 跟不上：检查网络、主库发送和副本日志 I/O。若已接收但应用积压：检查大事务、热点锁、DDL、缺失主键导致的行定位成本、磁盘及依赖关系。

并行复制受事务依赖、热点键和提交顺序约束；提高 worker 数量不意味着某一个巨大事务会自动等比例加速。拆批次、减少大事务和热点往往比盲目加线程更直接。

### 11.5 故障切换不只是“把从库设成主库”

需要回答：新主库已应用了哪些事务，哪些事务仍在 relay log，旧主库是否可能继续接受写入，客户端如何重连，未收到确认的请求如何查状态。

隔离旧主写入权限和路由，即 fencing，是避免双主分叉的重要环节。异步复制可能存在已确认但尚未传到候选副本的事务；半同步也必须结合当前是否降级、确认副本与切换规则评估。

“数据库单机日志已可靠落盘”和“故障切换后业务不丢写入”不是同一保证。中高级回答应分别说明本地持久性、复制进度与选主规则。

## 12. DDL、容量与分片：先说明约束，再选方案

### 12.1 Online DDL 仍可能等待 MDL

一个常见等待链：

```text
A：BEGIN；读过 orders，长时间没有结束事务
B：ALTER TABLE orders ...，等待所需 MDL
C、D：后续业务访问，可能因 MDL 调度而继续排队
```

SQL 执行完而事务未结束，仍可能保留事务级 MDL。于是一个空闲长事务也能影响 DDL 和后续流量。[官方 Metadata Locking](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html)

INSTANT、INPLACE、COPY 描述算法差异；`LOCK=NONE` 等描述并发要求。INPLACE 不保证绝不重建或不消耗大量 I/O；在线操作在开始或结束阶段仍可能需要更严格的 MDL。[官方 Online DDL 限制](https://dev.mysql.com/doc/refman/8.4/en/innodb-online-ddl-limitations.html)

### 12.2 上线结构变更时要验证什么

先查对应版本和具体操作是否支持目标算法，再用接近真实数据量的环境验证时间、空间和并发影响。需要时显式要求算法与锁级别，让不支持的操作报错，避免默默选择代价很高的方案。

评估内容包括：长事务、临时空间、并发写入引起的 online log 压力、复制延迟、失败与回滚时间，以及发布后能否撤回。

不是所有 ADD COLUMN 都 INSTANT，也不是普通加二级索引就没有扫描成本。[官方 Online DDL Operations](https://dev.mysql.com/doc/refman/8.4/en/innodb-online-ddl-operations.html)

### 12.3 行数不是容量决策的单一尺度

先把容量转换成真实负载：行宽与索引体积、热工作集、查询候选量、写入速度、I/O、锁热点、归档需求、备份窗口和恢复目标。

主键加宽会使多个二级索引一起变宽。数据量增长之后，缓存命中下降、大客户占比变化或维护窗口超标，可能比 B+ 树从三层变四层更早成为问题。

可先比较：SQL／索引改善、减少宽列访问、冷热归档、历史查询迁移、读负载隔离，再评估分区或分片。这里是决策顺序，具体项目仍要用瓶颈证据决定。

### 12.4 分区表不是分库分表

分区通常仍在同一 MySQL 实例内，适合按时间管理数据，并在条件满足时通过分区裁剪减少访问。它不自动提供跨机器的写入容量。

MySQL 分区有主键与唯一键的规则：分区表达式涉及的列需要满足所有唯一键的相应要求。例如原本只按 `id` 唯一，按时间分区时可能需要重新考虑唯一性设计，不能随便改成 `(id,created_at)` 就声称仍由数据库单独保证 id 全局唯一。[官方分区键与唯一键限制](https://dev.mysql.com/doc/refman/8.4/en/partitioning-limitations-partitioning-keys-unique-keys.html)

### 12.5 真正分片之前要回答六个问题

1. 分片键是否出现在主要查询中？否则需要广播查询和汇总。
2. 数据与热点是否均匀？按 user_id 分片也可能遇到巨大租户。
3. 全局唯一 ID 如何分配？业务唯一键跨分片如何保护？
4. 跨片事务、JOIN、分页、聚合怎样实现，语义是否改变？
5. 扩容迁移时如何校验、切换、回滚，怎样处理双写期间的不一致？
6. 运维如何做备份、恢复、升级、监控和故障定位？

中高级面试真正关心的是：你能说明被解决的瓶颈，也能说明新引入的复杂度。仅报一个“单表 2000 万”阈值，还没有形成容量方案。

## 13. 配套实验：用结果检验你的推演

### 13.1 准备与执行约定

配套文件：[MySQL中高级面试实验准备.sql](MySQL中高级面试实验准备.sql)。它创建全新的 `mysql_interview_lab_20261003` 学习库，生成 10 万行订单，以及 accounts、lock_demo、on_call、job_queue、biz_request 表。

在有创建学习库权限的 MySQL 8 客户端执行一次准备文件。先确认库名不存在；如已存在，换一个学习库名，不重复初始化。准备脚本不会删除已有库，批处理应在错误时停止。

Windows PowerShell 可用以下命令，先将 `your_mysql_user` 换成自己的学习库账号，密码由客户端提示输入：

```powershell
& 'D:\mysql\mysql server\bin\mysql.exe' --no-defaults --default-character-set=utf8mb4 --host=127.0.0.1 --user=your_mysql_user --password --execute="SOURCE D:/PYTHON/NeuroGRA/NeuroGRA/study/MySQL中高级面试实验准备.sql"
```

这里使用已在本地找到的客户端路径；实际连接端口不为默认端口时另加 `--port`。`--execute` 采用批处理方式；客户端的选项与 source 行为见[官方 mysql Client Options](https://dev.mysql.com/doc/refman/8.0/en/mysql-command-options.html)。

之后打开 A、B 两个独立连接；需要观察锁时，再打开只读观察连接 C。每个连接先执行：

```sql
USE mysql_interview_lab_20261003;
SET SESSION autocommit=1;
SELECT CONNECTION_ID(),VERSION(),@@transaction_isolation;
```

按标注时序分别执行，**不要把 A、B 代码合并到同一连接**。每组实验结束先在 A、B 执行 ROLLBACK 或 COMMIT，确认旧事务已结束，再重置学习数据。

本次验证使用独立目录、独立端口的临时 8.0.41 实例。14 个检查项通过；实验实例已经正常关闭。崩溃恢复、主从复制、DDL 高负载和性能基准没有做故障注入或压测，这些部分按官方文档讲解。

### 实验 1：同为 ref，工作量为什么不同

先在刚初始化的学习库运行：

```sql
EXPLAIN
SELECT id,amount,created_at FROM orders
WHERE user_id=42 AND status=1
ORDER BY created_at DESC,id DESC LIMIT 20;
```

然后选择本实验的覆盖候选；如果已经创建同名索引则跳过 CREATE：

```sql
CREATE INDEX idx_feed_cover
ON orders(user_id,status,created_at DESC,id DESC,amount);
ANALYZE TABLE orders;

EXPLAIN ANALYZE
SELECT id,amount,created_at FROM orders
WHERE user_id=42 AND status=1
ORDER BY created_at DESC,id DESC LIMIT 20;
```

观察：访问类型、候选估算、实际输出、filesort 是否消失。再把 `SELECT` 列换成包含 `remark`，解释覆盖关系怎样改变。

将第一页最后一行的时间与 id 保存，按第 5 节的复合游标查第二页；在数据没有变化的条件下，与 `LIMIT 20,20` 的结果比较。本次这组比较通过。

**应该讲出的因果链：**等值定位用户与状态 → 索引提供顺序 → 覆盖结果列 → LIMIT 提前停止。不能仅回答“因为建了索引”。

### 实验 2：RR 中的三个余额

先确保 A、B 都无未结束事务，用 B 重置：

```sql
UPDATE accounts SET balance=100,version=0 WHERE id=1;
```

A：

```sql
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION;
SELECT balance FROM accounts WHERE id=1; -- 100
```

B：

```sql
UPDATE accounts SET balance=200 WHERE id=1; -- autocommit
```

A：

```sql
SELECT balance FROM accounts WHERE id=1; -- 100
UPDATE accounts SET balance=balance+10 WHERE id=1;
SELECT balance FROM accounts WHERE id=1; -- 210
ROLLBACK;
```

重置余额后，将 A 的隔离级别改成 READ COMMITTED 重做，第二次 SELECT 应为 200，最后仍为 210。

再做一个变体：A 仅执行 BEGIN，不先 SELECT；B 改为 300 后提交；A 首次 SELECT 会看到 300。解释“事务开始”和“Read View 建立”的区别。

### 实验 3：查不到记录，却能阻止插入

A：

```sql
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION;
SELECT * FROM lock_demo WHERE id=15 FOR UPDATE;
```

C：

```sql
SELECT index_name,lock_type,lock_mode,lock_status,lock_data
FROM performance_schema.data_locks
WHERE object_schema='mysql_interview_lab_20261003'
  AND object_name='lock_demo';
```

本次看到 PRIMARY 的 `X,GAP`，lock_data=20，以及表级 IX。**lock_data=20 是用于表示前方间隙的后继记录，不能据此说记录 20 被 X 记录锁封锁。**

B 按以下顺序分别执行：

```sql
SET SESSION innodb_lock_wait_timeout=2;
START TRANSACTION;
INSERT INTO lock_demo VALUES(12,1,120); -- 等待，预期 1205
ROLLBACK;

START TRANSACTION;
UPDATE lock_demo SET payload=201 WHERE id=20; -- 可以完成
ROLLBACK;

START TRANSACTION;
INSERT INTO lock_demo VALUES(25,2,250); -- 可以完成
ROLLBACK;
```

最后 A ROLLBACK。然后将 A 的查询改为 id=20，重新执行：B 插入 12 可完成，更新 20 等待。对照说明记录锁与纯间隙锁的差异。

### 实验 4：二级索引的锁为何要带主键

A：

```sql
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION;
SELECT * FROM lock_demo FORCE INDEX(idx_k)
WHERE k=2 FOR UPDATE;
```

C 运行实验 3 的锁查询。应结合完整元组 `(k,id)` 解读 idx_k 上的锁，并检查 PRIMARY 的记录锁。B 尝试插入 `(id=25,k=2)`，会因为相关间隙保护等待；实验后 B、A 都回滚。

一个额外变体：A、B 都在 RR 事务中查询不存在的 id=15 FOR UPDATE。在没有其他冲突时，两者的间隙锁可以共存。先完成这个实验，再讨论二者同时想插入时为何可能形成死锁。

### 实验 5：RR 不能自动防止旧值覆盖

先重置 accounts.id=1 的余额为 100，确保 A、B 均已结束上一实验。

1. A BEGIN 并普通 SELECT，读到 100。
2. B BEGIN 并普通 SELECT，也读到 100。
3. A 执行 `UPDATE accounts SET balance=110 WHERE id=1`，COMMIT。
4. B 执行同一条无条件赋值 UPDATE，COMMIT。
5. 新事务读取结果为 110。

重置为 100，改成两个连接各执行一次 `balance=balance+10`，最终为 120。本次两种结果均验证通过。

**追问：**如果加 version 条件会发生什么？第二个请求应该重新读取并重新计算，还是直接返回冲突？这需要业务规则决定。

### 实验 6：RR 的跨行写偏差

先在无未结束事务时重置：

```sql
UPDATE on_call SET is_on_call=1;
```

1. A、B 都使用 RR，分别 BEGIN。
2. A、B 都执行 `SELECT COUNT(*) FROM on_call WHERE is_on_call=1`，都看到 2。
3. A 更新 id=1 为 0，B 更新 id=2 为 0。
4. A、B 提交。新的查询会看到在岗数为 0。

解释：决策依赖两行，但写锁只直接协调各自修改的行。然后设计一个所有离岗事务都会先锁定的共同资源，并解释如何保证检查读取当前状态。

### 实验 7：观察一个真实死锁

A、B 先各 BEGIN；为了观察等待，实验中可给 B 设置合适的 `innodb_lock_wait_timeout`，默认检测应保持开启。

```sql
-- A 先执行
UPDATE accounts SET balance=balance+1 WHERE id=1;

-- B 再执行
UPDATE accounts SET balance=balance+1 WHERE id=2;

-- B 再执行，此时等待 A
UPDATE accounts SET balance=balance+1 WHERE id=1;

-- B 仍等待时，A 执行，构成环
UPDATE accounts SET balance=balance+1 WHERE id=2;
```

一个连接收到 1213 后，另一个通常得以继续。查看 `SHOW ENGINE INNODB STATUS` 的最近死锁部分，说明每个事务持有什么、请求什么，而非只记录报错。

本次受害者为 A，但受害者选择不是业务接口保证。最后 A、B 都回滚；应用应把完整事务放进有限重试边界。

### 实验 8：任务领取与请求去重

A：

```sql
START TRANSACTION;
SELECT * FROM job_queue WHERE id=1 FOR UPDATE;
```

B：

```sql
START TRANSACTION;
SELECT id FROM job_queue WHERE state=0
ORDER BY id LIMIT 2 FOR UPDATE SKIP LOCKED; -- 2、3
ROLLBACK;
```

A 回滚后，再单独验证请求唯一约束：

```sql
INSERT INTO biz_request(request_id,result_text) VALUES('req-1','ok');
INSERT INTO biz_request(request_id,result_text) VALUES('req-1','again');
-- 第二条预期唯一键冲突 1062。
```

解释：SKIP LOCKED 解决领取时的行锁等待策略；唯一键解决请求身份重复；业务效果与领取／去重记录的事务边界还需要应用组织。

### 13.2 本次验证范围

| 检查项 | 结果 |
|---|---|
| 准备脚本与 10 万行订单数据 | 通过 |
| 订单覆盖索引、LIMIT 实际计划与第二页游标结果 | 通过 |
| RC 的 100 → 200 → 210 | 通过 |
| RR 的 100 → 100 → 210 | 通过 |
| BEGIN 未提前建立视图 | 通过 |
| 未命中唯一键的 gap lock | 通过 |
| 命中唯一键的 record lock | 通过 |
| 非唯一二级索引与聚簇记录锁 | 通过 |
| 纯间隙锁可共存 | 通过 |
| 旧值覆盖与原子增量对比 | 通过 |
| RR 写偏差 | 通过 |
| SKIP LOCKED | 通过 |
| 唯一键重复请求冲突 | 通过 |
| 两行循环等待触发死锁 | 通过 |

原始版本、计划、锁模式和断言结果见[验证记录](assets/mysql_interview_validation_20261003.json)。这组验证用于检查教材示例，不覆盖其他版本的所有锁边界，也不构成生产性能与灾难恢复证明。

## 14. 面试追问、自评与复习顺序

### 14.1 十五组递进追问

每组先口述一分钟，再回答第二层。能把最后一层说清楚，比背更多题目更有价值。

| 第一问 | 继续追问 | 深一层的答案要点 |
|---|---|---|
| 为什么用 B+ 树？ | 三层就等于三次磁盘 I/O 吗？ | 页扇出、缓存、扫描及回表一起分析 |
| 联合索引最左前缀是什么？ | 范围后的列还有用吗？ | 范围定位、ICP、覆盖、排序分开回答 |
| 覆盖索引不用回表吗？ | MVCC 会改变实际行为吗？ | 列覆盖定义与可见性检查区别 |
| 一条 SQL 走索引却很慢？ | 看什么证明原因？ | 估算与实际、loops、等待、I/O 和结果体积 |
| 加什么索引优化订单列表？ | 为什么不把所有列放进去？ | 过滤排序覆盖收益与写入／缓存代价 |
| 深分页怎样优化？ | 游标能保证翻页期间绝对一致吗？ | 唯一顺序、游标精度、并发更新与快照策略 |
| RC 与 RR 有什么区别？ | BEGIN 后别人提交的记录能看到吗？ | 首次一致性读建立视图的时机 |
| RR 的 SELECT 读 100？ | UPDATE 为什么加成 210？ | 快照读、当前读、本事务修改分别推演 |
| RR 能保证业务正确吗？ | 不同行修改会出问题吗？ | 旧值覆盖、写偏差、共享规则的协调资源 |
| 查不到记录有锁吗？ | 插入 12 和更新 20 谁等待？ | 具体数据下的 gap 与记录边界 |
| UPDATE 无索引为什么危险？ | 是自动升级表锁吗？ | 扫描路径与大量记录／间隙锁，区分 RC/RR |
| 死锁怎么处理？ | 1205 和 1213 是否都回滚整个事务？ | 明确错误语义，完整事务有限重试 |
| redo 和 binlog 为什么要协调？ | prepare 后崩溃怎么判断？ | 持久化提交决策、事务标识、完整日志 |
| 开半同步就能读副本吗？ | 等 GTID 后仍读到旧值怎么办？ | relay log 确认、应用进度、目标副本与旧快照 |
| 为什么要分库分表？ | 迁移和故障恢复怎么做？ | 具体瓶颈、路由、跨片语义、校验和切换 |

### 14.2 用四个问题给自己的回答评分

| 层次 | 自问 | 达标标志 |
|---|---|---|
| 结论 | 我能先用两句话回答吗？ | 不兜圈子，定义准确 |
| 推导 | 我能画出索引顺序／事务时序／日志状态吗？ | 有因果链，能够预测结果 |
| 边界 | 版本、隔离、索引和故障条件是什么？ | 能提出反例，避免绝对化 |
| 证据 | 我用什么计划、锁或指标验证？ | 能展示实验或真实项目记录 |

如果只会前两层，先做第 13 节；如果能推演却不会线上诊断，重点补第 10～12 节。

### 14.3 三轮复习安排

**第一轮：把核心 SQL 讲透。**用订单查询完成索引比较、EXPLAIN ANALYZE、分页和列覆盖解释。选一条你项目中真实存在的 SQL，记录数据规模、参数分布、改动前后计划与结果正确性。

**第二轮：把并发时序讲透。**完成余额、gap lock、写偏差、死锁四类实验；每次先预测再执行，错误预测必须回到机制解释。

**第三轮：把工程决策讲透。**准备一份慢查询排查过程、一份写后读方案、一份 DDL 或容量扩展方案，说明证据、取舍、失败处理与验证范围。

可以按下面模板组织项目案例：

```text
业务目标与响应时间要求：
数据规模、行宽、索引与参数分布：
原 SQL 和执行计划：
确认的瓶颈及证据：
比较过的候选方案与代价：
最终改动：
结果正确性、p95/p99、扫描／I/O、写入成本：
上线验证、监控与撤回方式：
```

没有真实压测或生产经验时，明确说“这是学习实验”并展示验证范围。可信的条件与证据比虚构一个优化数字更有说服力。

### 14.4 版本知识的核对清单

本地客户端为 8.0.41，本文实验也为 8.0.41。面试中先说明版本与 InnoDB，再套用结论。

- 不把旧版本的 SQL 查询缓存与 InnoDB Buffer Pool 混为一谈。
- descending index、函数索引、EXPLAIN ANALYZE、hash join 等按实际 8 系列能力核对。
- redo 容量在 8.0.30 前后有明显管理差异。
- INSTANT／INPLACE 是否支持取决于具体操作、版本和表条件。
- 8.4 的直方图自动更新能力与 8.0 维护规则有差异。
- 8 系列锁观测优先了解 Performance Schema 的 data_locks／data_lock_waits。
- 复制命令优先熟悉 SOURCE／REPLICA 命名，并核对当前环境支持的语法。

正文中的官方链接按知识点就近放置。复习时优先回看产生误判的具体章节；不用把整本手册从头读完，也不必把每个内部结构字段都当成面试必背项。
