# MySQL 数据库八股文详细讲解

> 来源材料：`225e2e06-5034-4691-a74e-6a125ac46794_MySQL.pdf`
>
> 目标：面向数据库基础较薄弱的同学，把文档中的 MySQL/InnoDB 高频面试知识点讲清楚，包括数据存储、SQL 执行、索引、事务、锁、日志、Buffer Pool 和 MySQL 架构。

## 一、先建立整体地图

MySQL 不是一个单纯“存表”的东西。它大体可以拆成两层：

```text
MySQL
├── Server 层
│   ├── 连接器
│   ├── 解析器
│   ├── 预处理器
│   ├── 优化器
│   ├── 执行器
│   └── binlog
│
└── 存储引擎层
    └── InnoDB
        ├── B+ 树索引
        ├── Buffer Pool
        ├── undo log
        ├── redo log
        ├── MVCC
        ├── 行锁 / 间隙锁 / next-key lock
        └── 磁盘数据文件 .ibd
```

你可以这样理解：

- `Server 层`负责“看懂 SQL，并决定怎么执行”。
- `InnoDB 层`负责“真正存数据、读数据、加锁、写日志、保证事务”。

所以一条 SQL 的执行，本质上是：

```text
用户发 SQL
-> Server 层理解 SQL、优化 SQL
-> InnoDB 根据索引和数据页读写数据
-> 通过锁和日志保证正确性
```

数据库八股文的大部分问题，其实都围绕三个核心：

1. 数据怎么存？
2. 查询怎么快？
3. 并发和宕机时怎么保证正确？

![](assets\17990622-e088-4940-9217-de5a6a46d687.png)

## 二、一行记录是怎么存的

我们平时看到一张表：

```sql
CREATE TABLE user (
  id BIGINT PRIMARY KEY,
  name VARCHAR(50),
  age INT
);
```

你可能以为磁盘里就这样存：

```text
id | name | age
```

但 InnoDB 里一行记录不是只有用户字段，它还包含很多管理信息。

以 Compact / Dynamic 行格式为例，一行大概由这些部分组成：

```text
变长字段长度列表
NULL 值列表
记录头信息
真实字段数据
隐藏字段
```

### 1. 变长字段长度列表

像 `VARCHAR`、`TEXT`、`BLOB` 这种字段长度不固定。

比如：

```sql
name VARCHAR(50)
```

存 `'Tom'` 和存 `'Christopher'` 占的字节数不同。

MySQL 读取一行时，必须知道：

```text
name 这个字段从哪里开始？
占多少字节？
下一个字段从哪里开始？
```

所以它要在记录里保存变长字段的长度信息。

这就是**变长字段长度列表**。

### 2. NULL 值列表

如果字段允许为 `NULL`，InnoDB 不会直接在数据区存一个字符串 `"NULL"`，而是用 bitmap 标记哪些字段是 NULL。

比如某行：

```text
name = NULL
age = 18
```

MySQL 会在 NULL 值列表里记录：`name` 是 NULL，`age` 不是 NULL。

如果表里所有字段都是 `NOT NULL`，这一部分就可以省掉。

所以为什么很多规范建议字段尽量 `NOT NULL`？

不是只有语义原因，也有存储和索引判断上的开销原因。

### 3. 记录头信息

记录头信息不是你业务里的字段，而是 InnoDB 管理这条记录用的。

里面有一些控制信息，比如：

- `delete_mask`：这条记录是否被标记删除。
- `next_record`：页内下一条记录的位置。
- `record_type`：记录类型。
- `heap_no`：记录在页堆里的编号。
- `n_owned`：页目录分组相关。

其中 `delete_mask` 很重要。

你执行：

```sql
DELETE FROM user WHERE id = 1;
```

InnoDB 通常不是立刻把磁盘上的这条记录物理抹掉，而是先做**逻辑删除**：

```text
delete_mask = 1
```

以后再由后台 purge 线程慢慢清理。

### 4. 真实字段数据

这部分才是你插入的数据，比如：

```text
id = 1
name = 'Tom'
age = 18
```

### 5. 隐藏字段

InnoDB 会给记录偷偷加一些隐藏字段：

- `trx_id`：最后一次修改这行记录的事务 id。

- `roll_pointer`：指向 undo log，用来找到旧版本。

- `row_id`：如果没有主键，也没有唯一非空索引，InnoDB 会生成隐藏 row_id。

  **全局共享的6字节空间（所有表），如果超过了2的48，会归零并重新开始循环，新行覆盖旧行 造成数据丢失**

这几个字段非常关键，后面 MVCC 会用到。

你现在先记一句：

> InnoDB 每行数据不仅有业务字段，还有事务相关的隐藏字段。`trx_id` 和 `roll_pointer` 是 MVCC 的基础。

## 三、为什么建议表一定要有主键

InnoDB 的表数据是按**聚簇索引**组织的。

聚簇索引是什么？

> 聚簇索引的叶子节点存完整行数据。

InnoDB 选择聚簇索引的规则是：

1. 如果有主键，就用主键。
2. 如果没有主键，就找第一个唯一非空索引。
3. 如果还没有，InnoDB 自动生成隐藏 `row_id`。

所以你不建主键，InnoDB 也得找一个东西来组织整张表。

为什么推荐显式建主键？

因为你自己指定主键，数据组织方式可控。否则 InnoDB 用隐藏 row_id，你查数据、做复制、排查问题都不直观。

为什么主键最好递增？

假设主键递增：

```text
1, 2, 3, 4, 5 ...
```

新数据大概率追加到 B+ 树末尾，页分裂少。

如果主键是随机 UUID：

```text
a8f...
19c...
ff2...
```

新数据可能插到 B+ 树中间，导致：

```text
页分裂
数据移动
索引维护成本升高
磁盘页变碎
```

面试可以说：

> InnoDB 表数据按聚簇索引组织。主键递增时，新记录大多追加到索引末尾，页分裂少；随机主键可能插入 B+ 树中间，导致页分裂和数据移动，所以通常建议使用递增主键。

## 四、`varchar(n)` 的 n 到底是什么意思

`varchar(n)` 的 `n` 表示**字符数**，不是字节数。

比如：

```sql
name VARCHAR(10)
```

表示最多 10 个字符。

但 MySQL 行记录大小限制是按字节算的。一行最大大约是 `65535` 字节，还要扣除额外信息，比如：

```text
变长字段长度列表
NULL 值列表
真实字段数据
```

不同字符集，一个字符占的最大字节数不同：

```text
ascii    1 字节
utf8     最多 3 字节
utf8mb4  最多 4 字节
```

所以：

```text
VARCHAR(65535) 不是一定能建成功
```

如果是 `utf8mb4`，一个字符最多 4 字节，那么最大字符数大约就是：

```text
65535 / 4 ≈ 16383
```

还要扣额外开销。

面试回答：

> `varchar(n)` 的 n 是字符数，不是字节数。但 InnoDB 一行记录有 65535 字节左右的限制，所以最大 n 取决于字符集、是否允许 NULL、是否有其他字段以及行记录额外开销。

## 五、数据页：理解 InnoDB 的最小读写单位

磁盘很慢，内存很快。

如果数据库每次只从磁盘读一行，效率很低。所以 InnoDB 不是一行一行读磁盘，而是按**页**读写。

默认页大小：

```text
16KB
```

一个数据页里可以存很多行记录。

你可以想象：

```text
磁盘文件 .ibd
├── 数据页 1，16KB
├── 数据页 2，16KB
├── 数据页 3，16KB
└── ...
```

每个数据页内部大概有：

```text
文件头
页头
最小记录 / 最大记录
用户记录
空闲空间
页目录
文件尾
```

为什么页里还要有页目录？

因为一个页里有很多行，如果页内也从头扫到尾，效率低。页目录可以帮助在页内快速定位记录。

所以一次索引查询的真实过程不是：

```text
直接找到某一行
```

而是：

```text
先通过 B+ 树找到数据页
再在数据页中找到具体记录
```

## 六、索引是什么

索引的本质是：

> 用额外的数据结构，减少扫描的数据量。

没有索引时：

```sql
SELECT * FROM user WHERE id = 10000;
```

可能要从第一行扫到最后一行。

有索引时，MySQL 可以通过 B+ 树快速定位。

你可以把索引理解成书的目录。

- 没有目录：要一页一页翻。
- 有目录：先根据章节定位页码，再翻到对应页。

但索引不是免费的，它会带来成本：

1. 占磁盘空间。
2. 插入、更新、删除时要维护索引。
3. 索引太多会影响写入性能。
4. 优化器选择索引也有成本。

所以建索引的原则不是“越多越好”，而是：

> 高频查询条件、排序字段、关联字段，才适合建索引。

## 七、InnoDB 为什么用 B+ 树

数据库索引为什么不用数组、链表、二叉树、Hash，而用 B+ 树？

### 1. 不用链表

链表查找：

```text
1 -> 2 -> 3 -> 4 -> 5
```

要找某个值，只能从头扫，太慢。

### 2. 不用普通二叉树

二叉树每个节点最多两个孩子。

数据量大时树会很高。

树越高，访问磁盘页次数越多。

数据库最怕磁盘 I/O，所以树不能太高。

### 3. 不用红黑树

红黑树虽然平衡，但本质还是二叉树。

数据量千万级时，树高仍然比 B+ 树高很多。每访问一层可能就是一次磁盘 I/O，所以不适合磁盘索引。

### 4. 不只用 Hash

Hash 很适合等值查询：

```sql
WHERE id = 1
```

但不适合：

```sql
WHERE id > 100
ORDER BY id
LIKE 'abc%'
```

因为 Hash 没有顺序。

数据库查询经常要范围、排序、前缀匹配，所以不能只靠 Hash。

### 5. B+ 树的优势

B+ 树是多叉树，一个节点可以有很多孩子。

这样树高很低。

比如千万级数据，B+ 树高度可能也就 3 到 4 层。

B+ 树还有一个关键特点：

```text
非叶子节点只存索引键
叶子节点存数据或主键值
叶子节点之间有链表
```

这带来三个好处：

1. 非叶子节点不存完整数据，一个页能放更多索引项，树更矮。
2. 查询任意数据的路径长度比较稳定。
3. 叶子节点有链表，范围查询很方便。

比如：

```sql
WHERE id BETWEEN 10 AND 100
```

B+ 树先找到 `10`，然后沿着叶子节点链表往后扫到 `100`。

面试标准答法：

> InnoDB 使用 B+ 树，是因为 B+ 树是多叉平衡树，树高低，可以减少磁盘 I/O；非叶子节点只存索引键，单页能容纳更多索引项；叶子节点之间有链表，适合范围查询。相比 Hash，B+ 树还能支持范围、排序和前缀匹配。

## 八、聚簇索引和二级索引

这是 MySQL 面试最核心之一。

InnoDB 中索引分两类：

```text
聚簇索引
二级索引 / 辅助索引
```

### 1. 聚簇索引

聚簇索引的叶子节点存完整行数据。

InnoDB 必须为每张表指定一个聚簇索引来组织数据。

**通常**就是主键索引。

选择优先级：

1. **用户显式定义的主键 (PRIMARY KEY)**。
2. 如果没有主键，则选择第一个**所有列都定义为 NOT NULL 的唯一索引 (UNIQUE NOT NULL)**。
3. 如果以上都没有，InnoDB 才会自动生成一个名为 `GEN_CLUST_INDEX` 的隐藏聚簇索引，并基于一个包含**行ID (row ID)** 的合成列来组织数据。

比如：

```sql
CREATE TABLE user (
  id BIGINT PRIMARY KEY,
  name VARCHAR(50),
  age INT
);
```

主键索引大概是：

```text
id -> 完整行数据
```

查主键：

```sql
SELECT * FROM user WHERE id = 1;
```

过程：

```text
主键 B+ 树
-> 找到 id = 1 的叶子节点
-> 叶子节点里就是完整行数据
```

不用再查别的地方。

### 2. 二级索引

假设建索引：

```sql
CREATE INDEX idx_name ON user(name);
```

二级索引叶子节点不是完整行数据，而是：

```text
name + 主键 id
```

如果执行：

```sql
SELECT * FROM user WHERE name = 'Tom';
```

过程：

```text
先查 idx_name
-> 找到 name = Tom 对应的主键 id
-> 再拿 id 去主键索引查完整行
```

第二步叫**回表**。

### 3. 覆盖索引

如果查询字段都在二级索引里，就不用回表。

比如有索引：

```sql
INDEX idx_name_age(name, age)
```

执行：

```sql
SELECT name, age FROM user WHERE name = 'Tom';
```

`name` 和 `age` 都在索引里，可以直接返回。

这叫覆盖索引。

面试要能说清：

> 二级索引叶子节点存的是索引列和主键值。如果查询字段不在二级索引里，就要拿主键值回到聚簇索引查完整行，这叫回表。如果查询字段都能从索引中拿到，就叫覆盖索引，可以减少回表。

## 九、联合索引和最左前缀原则

联合索引是多个字段组成一个索引。

比如：

```sql
INDEX idx_name_age_city(name, age, city)
```

这个索引不是三个独立索引，而是一个按多列排序的 B+ 树。

排序方式类似：

```text
先按 name 排
name 相同再按 age 排
age 相同再按 city 排
```

所以它能支持从最左边开始的连续匹配。

能用索引：

```sql
WHERE name = 'Tom'

WHERE name = 'Tom' AND age = 18

WHERE name = 'Tom' AND age = 18 AND city = 'Beijing'
```

不一定能充分用索引：

```sql
WHERE age = 18
```

因为跳过了最左边的 `name`。

这就是**最左前缀原则**。

范围查询要特别注意。

比如：

```sql
WHERE name = 'Tom' AND age > 18 AND city = 'Beijing'
```

通常 `name` 可以用，`age` 可以用范围，但 `city` 可能不能继续用于索引定位。

简单记忆：

> 联合索引从左到右使用，遇到范围查询后，后面的字段通常不能继续用于精确定位。

但实际 MySQL 版本和优化器能力会有细节，所以最终看 `EXPLAIN`。

## 十、索引失效有哪些情况

索引失效不是索引没了，而是优化器没用它，或者没充分用它。

常见情况：

### 1. 左模糊 LIKE

```sql
WHERE name LIKE '%abc'
WHERE name LIKE '%abc%'
```

B+ 树按从左到右排序。左边不确定，就没法利用有序性。

可以用：

```sql
WHERE name LIKE 'abc%'
```

因为前缀确定。

### 2. 对索引列使用函数

```sql
WHERE DATE(create_time) = '2026-09-16'
```

索引列被函数包住，B+ 树里存的是原始 `create_time`，不是 `DATE(create_time)` 的结果。

更好写法：

```sql
WHERE create_time >= '2026-09-16 00:00:00'
  AND create_time <  '2026-09-17 00:00:00'
```

### 3. 对索引列计算

```sql
WHERE age + 1 = 18
```

改成：

```sql
WHERE age = 17
```

### 4. 隐式类型转换

字段是字符串：

```sql
phone VARCHAR(20)
```

但查询写：

```sql
WHERE phone = 123456
```

可能发生类型转换，导致索引失效。

应写：

```sql
WHERE phone = '123456'
```

### 5. 联合索引不满足最左前缀

索引：

```sql
INDEX(a, b, c)
```

查询：

```sql
WHERE b = 1
```

通常不能用这个联合索引。

### 6. OR 条件

```sql
WHERE name = 'Tom' OR age = 18
```

如果两边不是都有合适索引，可能导致索引效果不好。

判断索引用不用，看：

```sql
EXPLAIN SELECT ...
```

重点看：

- `type`：访问类型。
- `key`：实际使用的索引。
- `rows`：预估扫描行数。
- `Extra`：额外信息。

如果：

```text
type = ALL
key = NULL
```

基本就是全表扫描。

## 十一、count 的区别

几个常见写法：

```sql
count(*)
count(1)
count(id)
count(name)
```

区别：

- `count(*)`：统计行数，**不会真的取出所有字段。MySQL 会优化。**
- `count(1)`：每行返回常量 1，再统计。
- `count(主键)`：统计主键不为 NULL 的行。主键本来就非空。
- `count(字段)`：统计这个字段不为 NULL 的行。

如果字段可能为 NULL：

```sql
count(name)
```

不会统计 `name IS NULL` 的行。

一般建议：

```sql
count(*)
```

面试说：

> `count(*)` 是统计行数，MySQL 对它有专门优化；`count(字段)` 会统计字段非 NULL 的行，语义不同。InnoDB 没有保存精确总行数，通常需要扫描索引来统计。

## 十二、分页为什么会慢

常见分页：

```sql
SELECT * FROM user ORDER BY id LIMIT 1000000, 10;
```

它不是直接跳到第 1000000 条。

它要做的是：

```text
找到前 1000010 条
丢掉前 1000000 条
返回后 10 条
```

所以 offset 越大，越慢。

优化方式：

### 1. 游标分页

```sql
SELECT * FROM user
WHERE id > 上一页最后一个id
ORDER BY id
LIMIT 10;
```

适合下拉加载更多。

优点：快。

缺点：不能随便跳到第 1000 页。

### 2. 覆盖索引 + 子查询

先用索引找到起始 id：

```sql
SELECT id FROM user ORDER BY id LIMIT 1000000, 1;
```

再查后面的数据：

```sql
SELECT *
FROM user
WHERE id >= (
  SELECT id FROM user ORDER BY id LIMIT 1000000, 1
)
ORDER BY id
LIMIT 10;
```

核心是让前半段尽量只扫较小的索引，而不是扫完整行。

### 3. 限制深分页

很多业务直接限制最多翻 100 页，再往后要求加筛选条件。

这是产品层面的优化。

## 十三、事务是什么

事务是一组 SQL 操作，要么都成功，要么都失败。

比如转账：

```text
A 扣 100
B 加 100
```

这两步必须作为一个整体。

如果 A 扣了钱，B 没加钱，系统就错了。

事务的四大特性 ACID：

### 1. 原子性 Atomicity

事务内操作要么全成功，要么全失败。

靠 `undo log` 实现。

如果失败，就用 undo log 回滚到修改前。

### 2. 一致性 Consistency

事务执行前后，数据都要满足约束。

比如余额不能凭空消失，唯一键不能重复，外键不能乱。

一致性不是某一个机制单独保证的，而是由：

```text
undo log
redo log
锁
MVCC
约束
业务逻辑
```

共同保证。

### 3. 隔离性 Isolation

多个事务同时执行时，彼此不能乱影响。

靠：

```text
锁
MVCC
```

保证。

### 4. 持久性 Durability

事务提交后，即使数据库宕机，数据也不能丢。

靠 `redo log` 保证。

## 十四、并发事务会出现什么问题

三个经典问题：

### 1. 脏读

事务 A 修改了数据但还没提交。

事务 B 读到了 A 未提交的数据。

后来 A 回滚了。

那 B 读到的就是脏数据。

### 2. 不可重复读

事务 A 第一次读：

```text
age = 18
```

事务 B 修改并提交：

```text
age = 20
```

事务 A 第二次读：

```text
age = 20
```

同一事务内，两次读同一行结果不同。

重点是：同一行被修改。

### 3. 幻读

事务 A 第一次范围查询：

```sql
SELECT * FROM user WHERE age > 18;
```

查到 10 条。

事务 B 插入一条 `age = 20` 并提交。

事务 A 第二次查，变成 11 条。

重点是：范围内多了或少了行。

## 十五、隔离级别

隔离级别从低到高：

```text
读未提交 Read Uncommitted
读已提交 Read Committed
可重复读 Repeatable Read
串行化 Serializable
```

越高越安全，但并发性能通常越低。

### 1. 读未提交

可以读到别人未提交的数据。

问题：

```text
脏读
不可重复读
幻读
```

基本不用。

### 2. 读已提交 RC

只能读到别人已经提交的数据。

解决脏读。

但可能出现：

```text
不可重复读
幻读
```

因为每次 SELECT 都会生成新的 Read View。

### 3. 可重复读 RR

MySQL InnoDB 默认级别。

普通 SELECT 下，同一事务多次读看到的是同一个快照。

解决：

```text
脏读
不可重复读
```

很大程度避免幻读。

### 4. 串行化

事务尽量串行执行。

最安全，但并发性能差。

## 十六、MVCC 是什么

MVCC，全称 Multi-Version Concurrency Control，多版本并发控制。

它解决的问题是：

> 读写并发时，不想让读阻塞写，也不想让写阻塞普通读。

如果没有 MVCC，读写都靠锁，性能会很差。

MVCC 的核心思想：

> 一行数据可以有多个历史版本。事务根据自己的 Read View 判断该读哪个版本。

它依赖三个东西：

### 1. 隐藏字段 trx_id

记录最后修改这行数据的事务 id。

### 2. 隐藏字段 roll_pointer

指向 undo log 里的旧版本。

### 3. undo log 版本链

每次修改前，旧值会写入 undo log。

比如原来：

```text
name = 'A'
```

事务 10 改成：

```text
name = 'B'
```

事务 20 又改成：

```text
name = 'C'
```

版本链可能类似：

```text
当前版本 C，trx_id = 20
 -> 旧版本 B，trx_id = 10
 -> 旧版本 A
```

### 4. Read View

Read View 是事务读数据时生成的“可见性视图”。

它大概记录：

```text
当前活跃事务 id 列表
最小活跃事务 id
下一个将要分配的事务 id
当前事务 id
```

判断某个版本是否可见：

1. 如果是当前事务自己改的，可见。
2. 如果修改该版本的事务在 Read View 创建前已经提交，可见。
3. 如果修改该版本的事务还活跃，不可见。
4. 当前版本不可见，就沿 undo log 找旧版本。

### RC 和 RR 的关键区别

RC：

```text
每次 SELECT 都生成新的 Read View
```

所以一次事务里两次查询可能看到不同结果。

RR：

```text
第一次快照读生成 Read View
之后复用同一个 Read View
```

所以普通 SELECT 可以做到可重复读。

## 十七、快照读和当前读

这是理解幻读和锁的关键。

### 1. 快照读

普通 SELECT：

```sql
SELECT * FROM user WHERE id = 1;
```

通常是快照读。

它读的是历史快照，不加锁，靠 MVCC。

### 2. 当前读

这些是当前读：

```sql
SELECT * FROM user WHERE id = 1 FOR UPDATE;

SELECT * FROM user WHERE id = 1 LOCK IN SHARE MODE;

UPDATE user SET name = 'Tom' WHERE id = 1;

DELETE FROM user WHERE id = 1;
```

当前读读取最新版本，并且通常要加锁。

为什么 UPDATE 是当前读？

因为你要修改数据，必须基于最新数据修改，不能拿旧快照改。

## 十八、可重复读完全解决幻读了吗

答案要分场景。

### 普通 SELECT 快照读

RR 下通过 MVCC，事务内读同一个快照。

即使别人插入了新数据，你普通 SELECT 也看不到。

所以快照读场景下，可以避免幻读。

### 当前读

当前读要读最新数据，不能只靠旧快照。

所以 InnoDB 使用：

```text
next-key lock = 记录锁 + 间隙锁
```

防止别人插入满足条件的新记录。

例如：

```sql
SELECT * FROM user WHERE id BETWEEN 10 AND 20 FOR UPDATE;
```

InnoDB 不只锁已有记录，还要锁记录之间的间隙，防止别人插入 `id = 15`。

但是有些混合场景会比较绕：

```sql
-- 事务 A
BEGIN;
SELECT * FROM user WHERE id = 5; -- 快照读，看不到

-- 事务 B
INSERT INTO user(id, name) VALUES(5, 'Tom');
COMMIT;

-- 事务 A
UPDATE user SET name = 'Jerry' WHERE id = 5; -- 当前读
SELECT * FROM user WHERE id = 5;
```

事务 A 一开始快照读看不到。

后面 UPDATE 是当前读，可以操作最新数据。

更新后这行又变成事务 A 自己修改的版本，所以后续可能看得到。

所以面试严谨说法：

> InnoDB 在 RR 下，快照读通过 MVCC 避免幻读，当前读通过 next-key lock 避免幻读。但如果混用快照读和当前读，仍可能出现看起来像幻读的现象，所以不能简单说 RR 完全解决所有幻读场景。

## 十九、锁的分类

MySQL 锁很多，但你可以按粒度理解：

```text
全局锁
表级锁
行级锁
```

### 1. 全局锁

命令：

```sql
FLUSH TABLES WITH READ LOCK;
```

让整个数据库进入只读状态。

主要用于全库逻辑备份。

### 2. 表锁

手动锁表：

```sql
LOCK TABLES t_student READ;
LOCK TABLES t_student WRITE;
UNLOCK TABLES;
```

读锁：别人可以读，不能写。

写锁：别人读写都受阻。

InnoDB 里平时更常用行锁，表锁相对少用。

### 3. MDL 元数据锁

Metadata Lock。

这个锁是 MySQL 自动加的。

你执行：

```sql
SELECT * FROM user;
```

会加 MDL 读锁。

别人此时想改表结构：

```sql
ALTER TABLE user ADD COLUMN x INT;
```

需要 MDL 写锁，会互斥。

MDL 的作用：

> 防止查询或更新过程中，表结构被别人改掉。

### 4. 意向锁

意向锁是表级锁，但它不是真的锁某一行。

它的作用是告诉别人：

> 我接下来要对这张表里的某些行加锁。

比如事务要给某些行加排他锁，会先在表上加意向排他锁。

这样如果另一个事务想直接加表锁，就能快速判断是否冲突。

### 5. AUTO-INC 锁

和自增主键有关。

多个事务并发插入时，要保证自增 id 分配不混乱。

## 二十、InnoDB 行锁

InnoDB 行锁非常关键。

先记一个重点：

> InnoDB 的行锁是加在索引上的，不是直接加在表里的物理行上。

行级锁主要有三种：

### 1. Record Lock 记录锁

锁住已经存在的索引记录。

比如：

```sql
SELECT * FROM user WHERE id = 10 FOR UPDATE;
```

如果 `id = 10` 存在，会锁住这条索引记录。

### 2. Gap Lock 间隙锁

锁住两个索引记录之间的间隙。

比如索引里有：

```text
10, 20
```

间隙有：

```text
(10, 20)
```

如果锁住这个间隙，别人不能插入：

```text
11, 12, 15, 19
```

间隙锁主要用于防止幻读。

### 3. Next-Key Lock 临键锁

```text
next-key lock = record lock + gap lock
```

它锁的是：

```text
左开右闭区间
```

比如：

```text
(10, 20]
```

既锁住记录 `20`，又锁住 `10` 到 `20` 之间的间隙。

## 二十一、不同查询怎么加锁

这是高级一点的面试点。

**InnoDB 的锁加在索引上，不是直接加在行上。**

加锁的基本单位是 **next-key lock**，它是一个左开右闭区间：`(前一条记录, 当前记录]`。在 RR 下，很多加锁行为都可以用 next-key lock 的退化来解释。

### 1. 唯一索引等值查询，记录存在

```sql
SELECT * FROM user WHERE id = 10 FOR UPDATE;
```

`id` 是唯一索引，且记录存在。

InnoDB 可以精确定位一条记录，所以 next-key lock 退化成记录锁。

锁住：

```text
id = 10
```

### 2. 唯一索引等值查询，记录不存在

```sql
SELECT * FROM user WHERE id = 15 FOR UPDATE;
```

假设索引中有：

```text
10, 20
```

`15` 不存在。

为了防止别人插入 `15` 造成幻读，会锁住间隙：

```text
(10, 20)
```

### 3. 非唯一索引等值查询

比如：

```sql
WHERE age = 18 FOR UPDATE
```

`age` 不是唯一索引，可能有多条 `age = 18`。

InnoDB 要锁住所有匹配的二级索引记录，还要锁住相关间隙，防止别人再插入新的 `age = 18`。

如果查询需要回表，还会对对应主键索引记录加锁。

### 4. 范围查询

```sql
WHERE age BETWEEN 10 AND 20 FOR UPDATE
```

范围当前读通常会锁：

```text
已有记录
记录之间的间隙
```

防止范围内插入新数据。

## 二十二、update 没走索引为什么危险

比如：

```sql
UPDATE user SET name = 'Tom' WHERE phone = '123';
```

如果 `phone` 没有索引，InnoDB 只能全表扫描。

全表扫描过程中，它可能对扫描到的记录加锁，范围条件下还可能加 next-key lock。

效果接近：

```text
锁住整张表
```

所以线上更新一定要注意：

1. WHERE 条件尽量走索引。
2. 先 EXPLAIN。
3. 大批量更新分批做。
4. 必要时开启 `sql_safe_updates`。
5. 避免长事务。

## 二十三、死锁

死锁就是两个事务互相等对方释放锁。

例子：

```text
事务 A 锁住 id = 1
事务 B 锁住 id = 2

事务 A 想锁 id = 2，等待 B
事务 B 想锁 id = 1，等待 A
```

谁也走不下去。

InnoDB 有死锁检测：

```sql
SHOW VARIABLES LIKE 'innodb_deadlock_detect';
```

检测到死锁后，会回滚其中一个事务，让另一个继续。

减少死锁：

1. **事务尽量短。**
2. **多个事务按相同顺序访问资源。**
3. **where 条件走索引，减少锁范围。**
4. **避免大事务。**
5. **唯一性判断交给唯一索引。**
6. **不要先查再插做复杂幂等，容易扩大锁冲突**。

## 二十四、三大日志：undo、redo、binlog

这部分非常重要。

### 1. undo log

undo log 是回滚日志。

它记录修改前的数据。

比如：

```sql
UPDATE user SET name = 'Tom' WHERE id = 1;
```

原来：

```text
name = 'Jerry'
```

undo log 会记录：

```text
把 name 恢复成 Jerry
```

作用：

```text
事务回滚
MVCC 版本链
```

所以 undo log 保证事务的**原子性**，也支持 MVCC。

### 2. redo log

redo log 是重做日志。

它解决的问题是：

> 事务提交了，但数据页还没刷盘，MySQL 突然宕机怎么办？

InnoDB 更新数据时，不会每次都立刻把数据页写回磁盘。

因为随机写磁盘太慢。

它会先改 Buffer Pool 里的页，让它变成脏页。

只要 redo log 落盘，事务就可以认为安全提交。

宕机后，InnoDB 根据 redo log 重做修改。

这叫 WAL：

```text
Write-Ahead Logging
先写日志，再写数据页
```

redo log 保证事务的**持久性**。

参数：

```sql
innodb_flush_log_at_trx_commit
```

常见取值：

- `1`：每次提交都刷盘，最安全。
- `2`：每次提交写到操作系统缓存，每秒刷盘。
- `0`：每秒写入并刷盘，性能好但可能丢数据。

### 3. binlog

binlog 是 Server 层日志。

它记录数据库的逻辑变更。

主要作用：

```text
主从复制
数据恢复
审计
```

常见格式：

- `statement`：记录 SQL 原文。
- `row`：记录每一行怎么变。
- `mixed`：混合模式。

生产中常用 `row`，因为主从一致性更好。

### 4. 三者对比

```text
undo log：回滚、MVCC，InnoDB 层
redo log：崩溃恢复，InnoDB 层
binlog：主从复制、数据恢复，Server 层
```

记忆：

```text
undo log：后悔药
redo log：保险单
binlog：操作档案
```

## 二十五、一条 UPDATE 的完整流程

比如：

```sql
UPDATE user SET name = 'Tom' WHERE id = 1;
```

大致流程：

1. 客户端发送 SQL。
2. 连接器校验连接和权限。
3. 解析器分析 SQL。
4. 预处理器检查表和字段。
5. 优化器选择执行计划。
6. 执行器调用 InnoDB。
7. InnoDB 通过主键索引找到 `id = 1`。
8. 如果数据页不在 Buffer Pool，从磁盘加载。
9. 写 undo log，保存旧值。
10. 修改 Buffer Pool 中的数据页，变成脏页。
11. 写 redo log 到 redo log buffer。
12. Server 层写 binlog 到 binlog cache。
13. 提交时 redo log 和 binlog 做两阶段提交。
14. 事务提交成功。
15. 后台线程未来某个时机把脏页刷回磁盘。

核心思想：

> 更新不是直接改磁盘文件，而是先改内存页，再靠 redo log 保证崩溃恢复，靠后台刷脏页最终落盘。

## 二十六、为什么 redo log 和 binlog 需要两阶段提交

redo log 和 binlog 都记录更新，但属于不同层。

- redo log：InnoDB 层，用于崩溃恢复。
- binlog：Server 层，用于主从复制和数据恢复。

如果它们不一致，会出大问题。

### 情况 1：先写 redo log，再写 binlog

redo log 写成功。

binlog 还没写。

MySQL 崩溃。

重启后，主库根据 redo log 恢复了这次更新。

但 binlog 没有这次更新。

从库同步不到。

结果：

```text
主库有
从库没有
```

### 情况 2：先写 binlog，再写 redo log

binlog 写成功。

redo log 还没写。

MySQL 崩溃。

重启后，主库没有 redo log，恢复不出这次更新。

但 binlog 有这次更新。

从库或用 binlog 恢复的数据会多一次更新。

结果：

```text
主库没有
从库可能有
```

所以 MySQL 用两阶段提交：

```text
1. redo log 写入 prepare 状态
2. 写 binlog
3. redo log 写入 commit 状态
```

崩溃恢复时：

- 如果 redo log 是 prepare，并且 binlog 完整，就提交。
- 如果 redo log 是 prepare，但 binlog 不完整，就回滚。

面试表达：

> 两阶段提交是为了保证 redo log 和 binlog 的一致性。redo log 先 prepare，binlog 写成功后 redo log 再 commit。这样崩溃恢复时可以根据 redo log 状态和 binlog 是否完整判断事务该提交还是回滚。

## 二十七、Buffer Pool 是什么

Buffer Pool 是 InnoDB 的内存缓冲区。

磁盘慢，内存快，所以 InnoDB 会把数据页、索引页缓存到 Buffer Pool。

查询时：

```text
先查 Buffer Pool
有就直接读
没有再从磁盘加载
```

更新时：

```text
先改 Buffer Pool 中的数据页
标记为脏页
未来再刷回磁盘
```

Buffer Pool 缓存的内容包括：

```text
数据页
索引页
undo 页
插入缓存
自适应哈希索引
锁信息
数据字典
```

它不是缓存 SQL 结果，而是缓存磁盘页。

## 二十八、Buffer Pool 怎么管理页

Buffer Pool 里有很多 16KB 的缓存页。

InnoDB 用链表管理它们：

### 1. Free List

空闲页链表。

表示还没被使用的缓存页。

### 2. Flush List

脏页链表。

表示这些页被修改过，需要未来刷盘。

### 3. LRU List

管理已使用页。

最近使用的在头部，最久没使用的在尾部。

空间不够时，淘汰尾部。

注意：

> 脏页既在 LRU List，也在 Flush List。

因为它既是已使用页，又是需要刷盘的页。

## 二十九、为什么不用普通 LRU

普通 LRU 有两个问题。

### 1. 预读失效

InnoDB 可能提前把相邻页读进 Buffer Pool。

但预读进来的页不一定真的会被访问。

普通 LRU 会把新读入的页放到头部，可能把真正热点页挤走。

### 2. Buffer Pool 污染

一个大查询：

```sql
SELECT * FROM big_table;
```

如果全表扫描，会把大量冷数据页读入 Buffer Pool。

普通 LRU 会把这些只用一次的页放到头部，导致热点数据被淘汰。

InnoDB 的优化：

把 LRU 分成：

```text
young 区：热数据
old 区：冷数据
```

新读入的页先进入 old 区。

只有满足条件后再次访问，才进入 young 区。

还有参数：

```text
innodb_old_blocks_time
```

默认大约 1 秒。

如果页刚进入 old 区马上被访问，不一定立刻进入 young 区。这样可以防止大范围扫描污染缓存。

## 三十、脏页什么时候刷盘

脏页：内存中被修改过，但还没写回磁盘的数据页。

刷盘时机：

1. redo log 快满了。
2. Buffer Pool 空间不足，需要淘汰脏页。
3. MySQL 空闲时后台线程刷盘。
4. MySQL 正常关闭前刷盘。

为什么事务提交时不立刻刷脏页？

因为随机刷数据页太慢。

只要 redo log 已经持久化，即使宕机，重启后也能恢复。

所以：

```text
事务提交
≠ 数据页已经写回磁盘
```

而是：

```text
事务提交
= redo log 已经保证可恢复
```

## 三十一、为什么 SQL 偶尔突然变慢

如果某条 SQL 平时很快，偶尔突然慢，可能是遇到了刷脏页。

比如 Buffer Pool 空间不够，要淘汰一个页。

如果淘汰的是干净页：

```text
直接丢掉
```

如果淘汰的是脏页：

```text
必须先写回磁盘
再释放空间
```

这个刷盘会带来额外 I/O，导致 SQL 抖动。

优化方向：

1. 增大 Buffer Pool。
2. 增大 redo log。
3. 避免大事务。
4. 避免瞬间大量写入。
5. 监控脏页比例和 I/O。

## 三十二、单表 2000 万行靠谱吗

“单表不要超过 2000 万”不是 MySQL 硬限制。

真正决定性能的是：

```text
B+ 树高度
索引设计
SQL 是否走索引
Buffer Pool 命中率
数据页数量
行记录大小
是否大量回表
磁盘 I/O
冷热数据分布
写入压力
```

如果表有 3000 万行，但查询都走高效索引，Buffer Pool 足够，性能可能很好。

如果表只有 500 万行，但经常全表扫描，也会很慢。

是否分库分表，要看：

1. 查询是否明显变慢。
2. 索引是否还能支撑。
3. 写入压力是否过高。
4. 单表文件是否过大。
5. 是否有冷热数据归档需求。
6. 是否有业务维度天然可拆分。

面试回答：

> 2000 万只是经验建议，不是硬性限制。是否分表取决于索引设计、查询模式、Buffer Pool 命中率、B+ 树高度、数据冷热分布和写入压力。

## 三十三、把所有知识串成一条主线

你学 MySQL 八股文，不要孤立背。

可以按这条主线理解：

1. 数据存在哪里？
   存在 InnoDB 的数据页里，磁盘文件是 `.ibd`。
2. 数据怎么组织？
   通过 B+ 树组织。主键索引叶子节点存整行数据。
3. 查询为什么快？
   因为索引减少扫描量，B+ 树高度低，Buffer Pool 缓存数据页。
4. 为什么会回表？
   二级索引叶子节点只存索引列和主键值，查完整行要回主键索引。
5. 事务怎么回滚？
   靠 undo log。
6. 宕机怎么恢复？
   靠 redo log。
7. 主从怎么同步？
   靠 binlog。
8. redo log 和 binlog 怎么保持一致？
   靠两阶段提交。
9. 并发读写怎么保证隔离？
   普通读靠 MVCC，当前读靠锁。
10. 幻读怎么处理？
    快照读靠 MVCC，当前读靠 next-key lock。
11. 锁为什么会扩大？
    InnoDB 锁加在索引上，没走索引可能全表扫描并锁很多范围。
12. SQL 为什么偶尔抖动？
    可能遇到脏页刷盘、锁等待、I/O 压力、执行计划变化。

## 三十四、面试背诵版总答案

如果面试官让你整体讲 MySQL/InnoDB，你可以这样说：

> MySQL 整体分为 Server 层和存储引擎层。Server 层负责连接管理、权限校验、SQL 解析、预处理、优化和执行，binlog 也在 Server 层；InnoDB 负责真正的数据存储和事务能力，包括 B+ 树索引、Buffer Pool、undo log、redo log、MVCC 和行锁。
>
> InnoDB 的数据按页读写，默认页大小 16KB，表数据通过聚簇索引组织，聚簇索引叶子节点存完整行数据，二级索引叶子节点存索引列和主键值，所以通过二级索引查完整行时可能需要回表。
>
> 查询时，优化器选择执行计划，执行器调用 InnoDB，通过 B+ 树定位数据页，优先从 Buffer Pool 读取；更新时，InnoDB 会先写 undo log 以支持回滚和 MVCC，再修改 Buffer Pool 中的数据页并标记为脏页，然后写 redo log 保证崩溃恢复，同时 Server 层写 binlog 支持主从复制。redo log 和 binlog 通过两阶段提交保证一致。
>
> 并发控制方面，普通 SELECT 通常是快照读，通过 MVCC 和 Read View 读取历史版本；UPDATE、DELETE、SELECT FOR UPDATE 是当前读，会读取最新数据并加锁。InnoDB 在 RR 隔离级别下，快照读通过 MVCC 避免幻读，当前读通过 next-key lock，也就是记录锁加间隙锁，防止其他事务插入满足条件的新记录。

这段能顺畅讲出来，MySQL 八股文骨架就稳了。后面再补细节，比如索引失效、深分页、count、死锁、Buffer Pool LRU 优化，就不容易散。

