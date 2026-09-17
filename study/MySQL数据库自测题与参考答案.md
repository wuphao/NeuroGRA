# MySQL 数据库自测题与参考答案

> 根据《MySQL数据库八股文详细讲解.md》整理。
>
> 共 30 题：15 道选择题、10 道简答题、5 道综合大题。参考答案集中在文档后半部分，建议先独立作答。
>
> 默认环境：MySQL 8.0/8.4、InnoDB。涉及锁的题目另行明确隔离级别。

**阅读纠正：**65,535 字节是 MySQL 层面的行大小限制，InnoDB 还有页内存储限制和页外存储机制，不能理解为一条记录可以完整放进一个 16KB 页。[官方说明](https://dev.mysql.com/doc/refman/8.0/en/column-count-limit.html)

## 一、选择题

前 12 题为单选，后 3 题为多选。

### 1. MySQL 架构

下面哪个组件属于 MySQL Server 层？

- A. Buffer Pool
- B. redo log
- C. binlog
- D. undo log

### 2. 聚簇索引选择

InnoDB 表没有显式主键，但存在一个所有列均为 NOT NULL 的唯一索引时，通常如何组织表数据？

- A. 使用该唯一索引作为聚簇索引
- B. 必须创建隐藏 row_id
- C. 按插入时间建立链表
- D. 建表失败

### 3. VARCHAR 的含义

在 utf8mb4 字符集下，VARCHAR(20) 中的 20 表示什么？

- A. 最多存储 20 字节
- B. 最多存储 20 个字符
- C. 固定占用 80 字节
- D. 最多存储 20 个英文字符，不能存中文

### 4. B+ 树

B+ 树适合数据库范围查询，最直接的原因是什么？

- A. 所有查询都只需要一次磁盘 I/O
- B. 叶子节点有序，并通过链表连接
- C. 非叶子节点保存全部业务数据
- D. 每个节点最多有两个孩子

### 5. 覆盖索引

有如下表结构：

```sql
CREATE TABLE student (
    id BIGINT PRIMARY KEY,
    name VARCHAR(50),
    age INT,
    address VARCHAR(200),
    INDEX idx_name_age(name, age)
);
```

仅从“所需列能否由索引提供”判断，哪条 SQL 不能被 idx_name_age 覆盖？

- A. `SELECT name FROM student WHERE name = 'Tom';`
- B. `SELECT name, age FROM student WHERE name = 'Tom';`
- C. `SELECT id, name FROM student WHERE name = 'Tom';`
- D. `SELECT address FROM student WHERE name = 'Tom';`

### 6. 联合索引

假设另有联合索引 (name, age, city)，查询如下：

```sql
SELECT *
FROM student
WHERE name = 'Tom'
  AND age > 18
  AND city = 'Beijing';
```

哪种说法最准确？

- A. 三个条件一定都能用于连续的等值定位
- B. age 使用范围条件后，整个索引失效
- C. 通常由 name、age 确定扫描范围，city 仍可能用于过滤
- D. SQL 中必须按索引顺序书写 WHERE 条件

### 7. COUNT

表中共有 5 行，name 列有 2 行为 NULL，id 是主键。以下结果正确的是？

- A. COUNT(*) = 5，COUNT(name) = 3
- B. COUNT(*) = 3，COUNT(name) = 3
- C. COUNT(id) = 3，COUNT(name) = 5
- D. COUNT(1) = 1，COUNT(*) = 5

### 8. 深分页

以下 SQL 深分页变慢的主要原因是什么？

```sql
SELECT * FROM student ORDER BY id LIMIT 1000000, 10;
```

- A. MySQL 必须返回 1000010 行给客户端
- B. MySQL 通常需要扫描或处理大量前置记录，再丢弃它们
- C. LIMIT 会让主键索引失效
- D. OFFSET 超过 100 万就会触发行锁升级

### 9. 并发异常

事务 A 读到了事务 B 尚未提交的修改，随后 B 回滚。这属于？

- A. 脏读
- B. 不可重复读
- C. 幻读
- D. 死锁

### 10. Read View

RR 隔离级别下，事务使用普通 BEGIN 开始，且没有提前创建一致性快照。Read View 通常何时建立？

- A. 数据库启动时
- B. 执行 BEGIN 时一定建立
- C. 第一次一致性快照读时
- D. 每次 UPDATE 时

### 11. 记录锁

RR 隔离级别下，在显式事务中执行：

```sql
SELECT * FROM student WHERE id = 10 FOR UPDATE;
```

id 是主键，且记录存在。针对该主键查询的核心行级锁是什么？

- A. 仅锁住 id = 10 的记录锁
- B. 必须锁住整张表
- C. 仅锁住 id = 10 前面的间隙
- D. 不加任何锁

### 12. 崩溃恢复

事务提交成功，但对应数据页尚未写回磁盘。假设必要日志已经可靠持久化，此时宕机，主要依靠什么恢复修改？

- A. undo log
- B. redo log
- C. 慢查询日志
- D. Buffer Pool 中尚未持久化的内容

### 13. 索引知识（多选）

以下关于索引的说法，哪些正确？

- A. 索引会增加部分写操作的维护成本
- B. 联合索引 (a, b) 等价于两个独立索引 (a)、(b)
- C. InnoDB 二级索引记录包含主键值
- D. 索引越多，所有查询一定越快

### 14. 日志知识（多选）

以下关于日志的说法，哪些正确？

- A. undo log 支持事务回滚和 MVCC
- B. redo log 主要用于 InnoDB 崩溃恢复
- C. binlog 可用于复制，以及结合备份进行时间点恢复
- D. 有了 binlog 就可以完全取消 redo log

### 15. 死锁预防（多选）

以下哪些措施通常有助于减少死锁？

- A. 多个事务按一致顺序访问相同资源
- B. 尽量缩短事务持续时间
- C. 建立合适索引，减少扫描和锁定范围
- D. 在事务持锁期间调用耗时的远程接口

## 二、简答题

建议每题用 3～6 句话回答，重点解释原因。

### 16. 主键设计

InnoDB 为什么通常建议使用较短、递增的主键？除了页分裂，还需要考虑主键长度对哪类索引的影响？

### 17. 回表与覆盖索引

什么是回表？什么是覆盖索引？请基于第 5 题的表结构，各举一条 SQL 说明。

### 18. 索引列函数

为什么“对索引列使用函数”可能使查询变慢？请改写下面的条件，使其更容易使用 create_time 上的普通索引：

```sql
WHERE DATE(create_time) = '2026-09-16'
```

### 19. ACID

请解释事务的 ACID，并说明 undo log、redo log、锁和 MVCC 分别发挥什么作用。

追问：数据库是否能自动保证所有业务规则正确？

### 20. MVCC

MVCC 如何找到当前事务可见的历史版本？回答需要包含 trx_id、roll_pointer、undo 版本链和 Read View。

### 21. RC 与 RR

RC 和 RR 的快照读有什么区别？

追问：RR 事务执行 BEGIN 后，其他事务提交的数据是否一定不可见？

### 22. 三种行级锁

记录锁、间隙锁和 next-key lock 分别锁什么？为什么只锁住已有记录，可能无法阻止幻读？

### 23. UPDATE 与锁范围

为什么“不走索引的 UPDATE 会自动升级为表锁”不够准确？请解释它为什么仍然可能严重影响并发。

### 24. Buffer Pool 管理

Buffer Pool 为什么不直接采用普通 LRU？请解释“预读失效”和“大扫描污染缓存”，以及 old/young 分区的作用。

### 25. 分库分表

为什么不能仅凭“单表超过 2000 万行”就决定分库分表？至少列出 5 个应该结合评估的因素。

## 三、综合大题

### 26. SQL 优化与索引设计

有订单表：

```sql
CREATE TABLE orders (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    status TINYINT NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    created_at DATETIME NOT NULL,
    remark VARCHAR(500),
    INDEX idx_user(user_id)
);
```

高频查询：

```sql
SELECT id, amount, created_at
FROM orders
WHERE user_id = 1001
  AND status = 1
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

请回答：

1. 现有 idx_user 有哪些不足？
2. 设计一个同时考虑过滤和排序的联合索引。
3. 如果希望覆盖查询，应如何调整索引？代价是什么？
4. 下一页查询如何改成游标分页？
5. 为什么只用 created_at 作为游标不够稳妥？

### 27. 事务时序与 MVCC

初始数据为 account 表中 id = 1、balance = 100。两个事务按下面顺序执行，A 中的 SELECT 都是普通快照读：

| 步骤 | 事务 A | 事务 B |
|---|---|---|
| 1 | BEGIN | |
| 2 | SELECT balance WHERE id = 1，记为 R1 | |
| 3 | | BEGIN |
| 4 | | 将 id = 1 的 balance 更新为 200 |
| 5 | | COMMIT |
| 6 | SELECT balance WHERE id = 1，记为 R2 | |
| 7 | UPDATE account SET balance = balance + 10 WHERE id = 1 | |
| 8 | SELECT balance WHERE id = 1，记为 R3 | |
| 9 | COMMIT | |

请回答：

1. A 在 RC 下，R1、R2、R3 分别是多少？
2. A 在 RR 下，R1、R2、R3 分别是多少？
3. 为什么 RR 下的 UPDATE 不一定基于此前 SELECT 读到的值计算？
4. A 为什么能看到自己的修改？

### 28. 锁范围与阻塞分析

表结构及数据：

```sql
CREATE TABLE t (
    id INT PRIMARY KEY,
    value INT
) ENGINE = InnoDB;

INSERT INTO t VALUES (10, 100), (20, 200), (30, 300);
```

事务 A 使用 RR 隔离级别：

```sql
BEGIN;
SELECT * FROM t WHERE id = 15 FOR UPDATE;
```

A 暂不提交。以下操作由其他独立事务分别执行，互不影响：

```sql
-- 操作①
INSERT INTO t VALUES (12, 120);
-- 操作②
INSERT INTO t VALUES (15, 150);
-- 操作③
UPDATE t SET value = 201 WHERE id = 20;
-- 操作④
INSERT INTO t VALUES (25, 250);
```

请回答：

1. A 没查到数据，为什么仍可能加锁？
2. A 锁住什么区间？
3. 哪些操作会因 A 的锁而等待？
4. 如果 A 改为查询已经存在的 id = 20 FOR UPDATE，上述操作会怎样变化？

### 29. 日志、提交与崩溃恢复

某事务更新一条记录。假设启用了 binlog，使用 InnoDB 与 binlog 的内部两阶段提交；必要日志按可靠持久化配置写入。

请分析：

1. 为什么不能简单地把两个日志各自独立提交？
2. redo 处于 prepare 状态，对应 binlog 事务尚未完整持久化时宕机，恢复时应如何处理？
3. redo 处于 prepare 状态，对应 binlog 事务已完整持久化，但尚未完成引擎 commit 时宕机，应如何处理？
4. 事务成功提交，但数据页还没刷盘，此时宕机会怎样？
5. 为什么“执行了 COMMIT，就绝对不会丢数据”还需要说明持久化配置？

### 30. 线上 SQL 偶发变慢

某系统中，一条查询平时耗时 10ms，偶尔升到 2 秒。同时发现：

- 夜间有大范围报表扫描。
- 部分事务持续几十秒。
- 写入高峰期间磁盘 I/O 明显上升。
- SQL 和返回行数看起来没有变化。

请设计一个排查方案：

1. 至少提出 4 类可能原因。
2. 每类原因应该收集什么证据？
3. 为什么不能直接断定是“索引失效”？
4. 如果主要原因是缓存污染和刷脏页压力，分别有什么优化方向？

---

## 四、选择题参考答案

| 题号 | 答案 | 解析 |
|---|---|---|
| 1 | C | binlog 属于 Server 层。 |
| 2 | A | 无主键时，可选择合适的唯一非空索引作为聚簇索引。 |
| 3 | B | n 表示字符数，实际字节数受字符集和内容影响。 |
| 4 | B | 有序叶子节点及链表支持连续范围扫描。 |
| 5 | D | address 不在该索引中；主键 id 包含在二级索引记录中。 |
| 6 | C | 后续列不能继续同等程度地缩小扫描范围，不代表完全没有用途。 |
| 7 | A | COUNT(name) 忽略 NULL，COUNT(*) 统计行数。 |
| 8 | B | 大 OFFSET 的主要成本是处理后丢弃大量前置记录。 |
| 9 | A | 读到其他事务未提交的数据是脏读。 |
| 10 | C | 普通 BEGIN 本身不一定建立一致性快照。 |
| 11 | A | 唯一索引精确命中已有记录时，通常只需记录锁。 |
| 12 | B | redo 支持恢复尚未写入数据文件的修改。 |
| 13 | AC | 联合索引有列顺序，索引也有空间及维护成本。 |
| 14 | ABC | binlog 与 redo 的职责不同。 |
| 15 | ABC | 持锁期间执行耗时操作会延长锁占用。 |

第 10、11 题可对照官方的[一致性读说明](https://dev.mysql.com/doc/refman/8.0/en/innodb-consistent-read.html)和[锁机制说明](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)。

## 五、简答题参考答案

### 16. 主键设计

- 递增主键通常使插入集中在 B+ 树末端，减少随机插入导致的分裂和碎片。
- 主键越短，聚簇索引键占用越小。
- 二级索引也保存主键值，因此长主键会放大各二级索引的空间成本。
- 这是常见设计取舍，不代表递增主键完全没有页分裂或并发热点。

### 17. 回表与覆盖索引

回表：先从二级索引取得主键，再访问聚簇索引获取所需列。

```sql
-- address 不在 idx_name_age 中，走该索引时需要回表
SELECT address FROM student WHERE name = 'Tom';
```

覆盖索引：查询所需列均由索引提供。

```sql
SELECT id, name, age FROM student WHERE name = 'Tom';
```

### 18. 函数与索引

普通索引按原始列值排序，对列计算函数后，优化器不一定能把条件转换为原始索引上的查找范围。可改为：

```sql
WHERE create_time >= '2026-09-16 00:00:00'
  AND create_time <  '2026-09-17 00:00:00'
```

半开区间也能正确覆盖带小数秒的时间值。函数索引等属于额外情况，不能笼统说“使用函数必然不走任何索引”。

### 19. ACID

- 原子性：整体成功或回滚，undo 支持撤销修改。
- 一致性：事务前后满足约束和业务规则。
- 隔离性：控制并发事务之间的相互影响，依靠锁和 MVCC 等机制。
- 持久性：提交结果能够在故障后保留，redo 与持久化配置发挥关键作用。

数据库不会自动理解“转账必须一扣一加”等全部业务含义，应用仍需正确组织事务。

### 20. MVCC

trx_id 标识修改版本的事务，Read View 用来判断该版本是否可见。不可见时，通过 roll_pointer 沿 undo 版本链寻找更早的版本，直到找到可见版本或确认该行不可见。事务自己的修改通常对自己可见。

### 21. RC 与 RR

RC 的每次一致性读使用新快照；RR 通常复用首次一致性读建立的快照。因此，RR 下其他事务在 BEGIN 之后、第一次快照读之前提交的数据，仍可能被看到。不能直接把 BEGIN 的时间当成快照时间。[官方说明](https://dev.mysql.com/doc/refman/8.0/en/innodb-consistent-read.html)

### 22. 三种锁

- 记录锁：锁住索引记录。
- 间隙锁：阻止向某个索引间隙插入新记录。
- next-key lock：记录锁与前方间隙锁的组合，典型区间为左开右闭。

只锁住已有记录，不能阻止其他事务在记录之间插入满足查询条件的新行。

### 23. 没有合适索引的 UPDATE

InnoDB 并非因扫描行数多就自动把行锁升级为表锁。问题在于扫描范围扩大，可能锁住大量索引记录及间隙，造成接近“全表写入受阻”的效果。具体锁定和释放行为还受隔离级别影响。[官方说明](https://dev.mysql.com/doc/refman/8.0/en/innodb-locks-set.html)

### 24. 改进 LRU

预读页可能根本不会被使用；全表扫描读入的页也可能只访问一次。若这些页直接占据 LRU 热端，就会挤掉热点页。InnoDB 将新读入页放入 old 区，并通过访问和时间条件控制晋升 young 区，减轻污染。

### 25. 是否分表

至少考虑：查询模式、索引设计、行宽、索引体积、缓存命中率、扫描与回表量、磁盘 I/O、写入压力、响应时间目标，以及归档和运维成本。行数本身不足以决定是否拆分。

## 六、综合大题参考答案

### 26. SQL 优化与索引设计

**① 现有索引不足：**idx_user 只能直接按用户筛选，之后还需要筛选状态，并可能对候选记录排序。

**② 过滤与排序索引：**

```sql
CREATE INDEX idx_user_status_time_id
ON orders(user_id, status, created_at DESC, id DESC);
```

前两列匹配等值条件，后两列对应排序。

**③ 覆盖查询方案：**

```sql
CREATE INDEX idx_user_status_time_id_amount
ON orders(user_id, status, created_at DESC, id DESC, amount);
```

加入 amount 后，从列覆盖角度满足查询。代价是索引更大、写入维护成本更高。这是两个候选方案，通常按收益选择，不必同时建立。

**④ 游标分页：**假设上一页最后一行为 created_at = :last_time、id = :last_id，以下命名参数由应用绑定：

```sql
SELECT id, amount, created_at
FROM orders
WHERE user_id = 1001
  AND status = 1
  AND (
       created_at < :last_time
       OR (created_at = :last_time AND id < :last_id)
  )
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

**⑤ 复合游标原因：**时间可能重复，加入唯一的 id 才能稳定确定同一时间下的顺序，避免简单时间游标漏行。

最终应结合真实数据的执行计划、扫描量和耗时验证索引收益。

### 27. 事务时序

| 隔离级别 | R1 | R2 | R3 |
|---|---:|---:|---:|
| RC | 100 | 200 | 210 |
| RR | 100 | 100 | 210 |

RC 第二次快照读能看到 B 已提交的 200；RR 第二次快照读仍读原快照中的 100。

但 UPDATE 是当前读，基于最新可更新版本执行 200 + 10。修改后，该版本属于 A 自己的修改，因此后续查询能看到 210。RR 并不表示事务中的写操作只能使用旧快照值。[官方说明](https://dev.mysql.com/doc/refman/8.0/en/innodb-consistent-read.html)

### 28. 锁范围

A 查询的 15 不存在，RR 下会锁住其所在间隙 (10, 20)，防止其他事务插入该范围。

| 操作 | 是否因 A 而等待 | 原因 |
|---|---|---|
| ① 插入 12 | 是 | 位于被锁间隙 |
| ② 插入 15 | 是 | 位于被锁间隙 |
| ③ 更新 20 | 否 | 间隙锁不锁住端点记录 20 |
| ④ 插入 25 | 否 | 不在被锁间隙 |

如果 A 改为主键等值查询已经存在的 20，则核心锁为 20 上的记录锁：

- ③ 更新 20 等待。
- ①、②、④不会因该记录锁而等待。

以上判断假定不存在题目之外的其他锁冲突。[官方锁机制说明](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)

### 29. 日志与恢复

1. 若两个日志独立提交，可能出现引擎恢复结果与 binlog 所描述的已提交事务不一致，进而影响复制和恢复。
2. 引擎处于 prepare，但缺少完整、持久化的对应 binlog 事务：恢复时回滚该未决事务。
3. 对应 binlog 事务已完整持久化：恢复时根据事务标识确认提交，完成引擎提交。
4. 不要求提交时就刷完数据页；可靠持久化的 redo 支持恢复未落盘修改。
5. innodb_flush_log_at_trx_commit、sync_binlog 等配置影响日志何时持久化；可靠性也依赖底层存储兑现刷盘保证。因此要区分“提交返回”和具体故障下的数据持久化保证。

### 30. 慢查询排查

| 可能原因 | 应收集的证据 |
|---|---|
| 锁等待 | 等待事件、阻塞事务、事务持续时间、锁等待关系 |
| 大扫描导致缓存污染 | 报表运行时间、Buffer Pool 物理读、缓存命中变化 |
| 刷脏页压力 | 脏页数量、刷页速率、checkpoint 压力、磁盘写延迟 |
| 执行计划变化 | 快慢时的执行计划、实际扫描量、统计信息变化 |
| CPU 或 I/O 资源争用 | CPU、磁盘延迟与队列、并发负载 |

SQL 文本与返回行数相同，并不代表扫描量、缓存状态、执行计划和等待时间相同，应先区分“执行慢”还是“等待久”。

若是缓存污染，可优化报表扫描范围、调整运行时间，并评估 old 区参数和 Buffer Pool 容量。若是刷脏页压力，可平滑批量写入、拆短事务，并结合存储能力评估刷页策略与 redo 容量。

---

自测时尤其留意第 6、10、21、27、28 题：这些题重点考查结论的适用条件，而不是单纯记忆名词。
