# Java 并发编程面试题与参考答案

> 适用对象：已掌握 Java 基本语法，正在学习线程、同步机制及并发工具的学习者。  
> 版本基准：Java SE 21；普通线程题默认使用平台线程，虚拟线程题明确标注。  
> 题量：60 题，包含 25 道单选、15 道多选、20 道问答。问答题中含代码审查与业务场景题。  
> 编写与核对日期：2026-10-03。  
> 使用方式：题目与答案分区，适合独立作答、面试复习及按知识点查询。可配合 [Java 并发编程方法学习与查询手册](Java并发编程方法学习与查询手册.md) 阅读，但题干不要求先阅读该手册。

## 阅读导航

| 部分 | 内容 | 题号 |
|---|---|---|
| [作答约定](#rules) | 版本、代码前提与自测规则 | — |
| [单选题](#single) | 每题只有一个正确选项 | S01—S25 |
| [多选题](#multiple) | 每题至少两个正确选项 | M01—M15 |
| [问答题](#written) | 原理、方法比较、代码审查、场景分析 | Q01—Q20 |
| [选择题答案速查](#choice-key) | 对照答案 | S01—S25、M01—M15 |
| [单选题解析](#single-answers) | 正确依据及错误选项说明 | S01—S25 |
| [多选题解析](#multiple-answers) | 各选项判断依据 | M01—M15 |
| [问答题参考答案](#written-answers) | 回答要点、代码修正与常见失分点 | Q01—Q20 |
| [知识点索引与资料](#references) | 按主题复习与查询官方 API | — |

<a id="rules"></a>

## 作答约定

1. 单选题只选择一个答案；多选题选择全部正确答案，未选和多选均按错误处理。
2. 除非题干明确说明，讨论方法契约，不把一次运行观察到的顺序视为所有运行的保证。
3. 代码片段默认具有必要的导入、外围类和异常声明；局部片段不一定能直接作为独立程序编译。
4. 线程池数量题默认采用题干指定的 `ThreadPoolExecutor`，并排除外部关闭、线程创建失败和任务提前结束等干扰。
5. `Thread.interrupted()` 作用于当前线程；`thread.interrupt()` 作用于目标线程。作答时应始终明确调用方与执行方。
6. 自测可采用选择题每题 2 分、问答题每题 6 分，总分 200 分。此规则仅供学习记录，问答以正确性及关键边界为评价依据。

问答题宜先给结论，再说明执行机制、必要前提和常见例外。仅背诵“线程安全”“原子性”“底层 CAS”等名词，不足以解释一个具体方法的行为。

<a id="single"></a>

## 一、单选题：S01—S25

<a id="s01"></a>

### S01. 直接调用 run()【基础】

以下代码由名为 `main` 的线程执行：

```java
Thread worker = new Thread(
    () -> System.out.println(Thread.currentThread().getName()),
    "worker"
);
worker.run();
```

哪项描述正确？

- A. 输出 `main`，并且没有启动 worker 线程。
- B. 输出 `worker`，因为任务保存在 worker 对象中。
- C. 输出取决于线程调度，可能是 `main` 或 `worker`。
- D. 必定抛出 `IllegalThreadStateException`。

<a id="s02"></a>

### S02. 同一个 Thread 对象再次启动【基础】

worker 已通过 `start()` 启动，随后正常结束。主线程成功执行 `worker.join()` 后，再调用 `worker.start()`，结果是什么？

- A. 自动创建第二条线程，再次执行任务。
- B. 抛出 `IllegalThreadStateException`。
- C. 没有任何效果，也不抛异常。
- D. 自动将对象状态恢复成 `NEW`。

<a id="s03"></a>

### S03. sleep() 的作用对象【基础】

主线程执行 `worker.sleep(100)`。虽然该写法不推荐，但 Java 允许通过对象表达式调用这一静态方法。谁被要求休眠？

- A. worker 对象代表的线程。
- B. 全部工作线程。
- C. 正在执行该调用的主线程。
- D. 由 JVM 随机选择的一条线程。

<a id="s04"></a>

### S04. join() 的等待方【基础】

main 调用已经启动的 `worker.join()`，且 worker 尚未结束。通常发生什么？

- A. worker 等待 main 结束。
- B. main 等待 worker 结束。
- C. 两条线程都被永久停止。
- D. worker 被取消，main 立即继续。

<a id="s05"></a>

### S05. wait() 的锁要求【基础】

当前线程没有持有 `monitor` 的监视器锁，`monitor` 非 `null`。执行 `monitor.wait()` 通常会怎样？

- A. 自动获取锁后开始等待。
- B. 自动释放其他线程持有的锁。
- C. 返回 `false`。
- D. 抛出 `IllegalMonitorStateException`。

<a id="s06"></a>

### S06. sleep() 与 wait() 的锁语义【基础】

哪项描述正确？

- A. `sleep()` 和 `wait()` 都释放当前线程持有的所有锁。
- B. `sleep()` 不释放已有锁；`monitor.wait()` 等待时释放 monitor 的监视器锁。
- C. `sleep()` 释放监视器，但不释放显式锁。
- D. `wait()` 只暂停执行，始终持有 monitor 的监视器锁。

<a id="s07"></a>

### S07. interrupt() 与普通计算循环【基础】

一个线程在执行无限计算循环，从不检查中断，也不调用可中断等待方法。另一线程调用它的 `interrupt()`，哪项判断正确？

- A. JVM 保证立即终止该循环。
- B. JVM 保证在下一行抛出 `InterruptedException`。
- C. 中断请求本身不保证循环结束。
- D. 中断会自动将循环条件改为 `false`。

<a id="s08"></a>

### S08. 中断标记读取与清除【基础】

当前线程执行以下代码，期间没有其他中断请求：

```java
Thread.currentThread().interrupt();
System.out.println(Thread.interrupted());
System.out.println(Thread.interrupted());
```

两次输出是什么？

- A. `true`、`true`。
- B. `true`、`false`。
- C. `false`、`true`。
- D. `false`、`false`。

<a id="s09"></a>

### S09. 可中断加锁的释放模式【基础】

为了避免获取锁被中断后仍错误地解锁，以下哪种结构合适？

- A. 先调用 `lock.lockInterruptibly()`；成功返回后进入 `try`，在 `finally` 中解锁。
- B. 在 `try` 中调用 `lock.lockInterruptibly()`，不判断是否获取成功，在 `finally` 中始终解锁。
- C. 无论是否获取成功，先调用 `unlock()` 再调用 `lockInterruptibly()`。
- D. 只调用 `lockInterruptibly()`，依靠方法返回自动释放锁。

<a id="s10"></a>

### S10. tryLock() 返回 true 的含义【基础】

`ReentrantLock.tryLock()` 返回 `true` 后，哪项判断正确？

- A. 当前线程已经获取或重入了这把锁，需要对应释放。
- B. 只说明锁曾经空闲，当前线程仍未获取锁。
- C. 其他线程仍可以同时持有同一把互斥锁。
- D. 本次获取不需要调用 `unlock()`。

<a id="s11"></a>

### S11. 公平锁与无参 tryLock()【进阶】

对于 `new ReentrantLock(true)`，哪项关于无参 `tryLock()` 的描述正确？

- A. 必须排在已有等待者之后。
- B. 无论锁是否空闲都必须先挂起。
- C. 即使存在排队线程，也可能在锁可用时立即获取，不遵守该公平排队策略。
- D. 一定返回 `false`，公平锁不支持尝试获取。

<a id="s12"></a>

### S12. 有界线程池的任务接收顺序【进阶】

线程池配置如下：

```java
ThreadPoolExecutor pool = new ThreadPoolExecutor(
    2, 4, 30, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(2),
    new ThreadPoolExecutor.AbortPolicy()
);
```

线程池最初没有工作线程。连续提交 5 个任务，所有已开始的任务都等待同一个尚未放行的闸门，因而不会结束。提交完成后，池中的工作线程数与排队任务数分别是多少？

- A. 2 条工作线程，3 个排队任务。
- B. 3 条工作线程，2 个排队任务。
- C. 4 条工作线程，1 个排队任务。
- D. 第 5 个任务必定被拒绝。

<a id="s13"></a>

### S13. 无界队列与 maximumPoolSize【进阶】

一个 `ThreadPoolExecutor` 的核心线程数为 2，最大线程数为 10，使用基本无界的任务队列。任务持续到达且队列仍能接收，为什么通常不会逐步扩容到 10 条工作线程？

- A. `maximumPoolSize` 永远没有作用。
- B. 最大线程数必须小于核心线程数才有效。
- C. 核心线程数量达到后优先排队，队列无法接收才尝试非核心扩容。
- D. Java 不允许超过两条线程并发执行。

<a id="s14"></a>

### S14. shutdownNow() 的保证【基础】

哪项描述正确？

- A. 强制杀死全部工作线程，并保证调用返回时所有任务都已停止。
- B. 只拒绝新任务，绝不影响运行中的任务。
- C. 尝试停止活动任务，取出尚未执行的任务，但不保证活动任务已经停止。
- D. 保证自动回滚所有任务的外部副作用。

<a id="s15"></a>

### S15. submit(Runnable) 的正常结果【基础】

使用没有指定 result 参数的 `submit(Runnable)`，任务正常结束。其 Future 的 `get()` 正常返回什么？

- A. 任务所在的 `Thread` 对象。
- B. `true`。
- C. `null`。
- D. 必定抛出 `ExecutionException`。

<a id="s16"></a>

### S16. Future.get() 等待超时【基础】

`future.get(1, TimeUnit.SECONDS)` 抛出 `TimeoutException` 后，以下哪个结论正确？

- A. 对应任务必定已经被取消。
- B. 任务可能仍在执行，需要另外决定是否请求取消。
- C. Future 必定永久失效，不能再获取结果。
- D. 任务的所有操作已被回滚。

<a id="s17"></a>

### S17. isDone() 的判断范围【基础】

仅凭 `future.isDone()` 返回 `true`，能够得出什么结论？

- A. 任务一定成功。
- B. 任务一定返回非 `null` 结果。
- C. Future 已处于正常完成、异常完成或取消等完成状态。
- D. 在取消场景中，实际运行代码也一定全部退出。

<a id="s18"></a>

### S18. anyOf() 与先发生的失败【进阶】

在 A、B 两个 `CompletableFuture` 都未完成时，先建立 `CompletableFuture.anyOf(A, B)`。随后 A 首先异常完成，B 稍后正常完成，组合会怎样？

- A. 忽略 A 的失败，等待 B 并正常返回 B 的结果。
- B. 由 A 的先完成失败决定结果，整体异常完成。
- C. 同时返回两个任务的结果列表。
- D. 自动取消 A 并重试它。

<a id="s19"></a>

### S19. thenCompose() 的用途【基础】

某个后续函数接收上一步结果，并返回另一个 `CompletableFuture<String>`。希望最终得到 `CompletableFuture<String>`，而不是嵌套的 Future，适合哪个方法？

- A. `thenRun()`。
- B. `thenAccept()`。
- C. `isDone()`。
- D. `thenCompose()`。

<a id="s20"></a>

### S20. 原子类的多步操作【基础】

多个线程同时执行 `counter.set(counter.get() + 1)`，counter 是 `AtomicInteger`。哪项描述正确？

- A. 读取与更新会自动合成一次原子操作。
- B. 只要 counter 是原子类，就不会丢失更新。
- C. 两个调用之间存在竞争窗口，应使用原子加法等方法。
- D. 该代码无法编译，因为原子类没有 `set()`。

<a id="s21"></a>

### S21. AtomicReference 的比较方式【进阶】

以下代码没有其他线程参与：

```java
AtomicReference<String> ref = new AtomicReference<>(new String("A"));
boolean ok = ref.compareAndSet(new String("A"), "B");
```

执行后哪个结果正确？

- A. ok 为 `true`，因为两个字符串内容相等。
- B. ok 为 `false`，ref 仍引用原来的字符串对象。
- C. ok 为 `true`，但 ref 保持原值。
- D. 必定抛 `ClassCastException`。

<a id="s22"></a>

### S22. CountDownLatch 是否识别任务身份【基础】

创建 `CountDownLatch(2)` 后，同一个线程连续调用两次 `countDown()`。其他线程随后调用 `await()`，结果是什么？

- A. 因为只来了一条线程，所以继续等待第二条线程。
- B. 必定抛出重复计数异常。
- C. 计数已经归零，可正常返回；倒计数器不验证任务身份。
- D. 自动创建第二条线程补齐参与者。

<a id="s23"></a>

### S23. 有界队列的 offer()【基础】

一个容量已满的 `ArrayBlockingQueue`，此时没有消费者取出元素。调用无超时参数的 `offer(element)`，容量条件对应的结果是什么？

- A. 返回 `false`，不等待以后出现的空间。
- B. 一直等待直到能够入队。
- C. 返回 `null`。
- D. 自动扩大数组容量。

<a id="s24"></a>

### S24. CompletableFuture.cancel(true)【进阶】

对于通过 `supplyAsync()` 启动的计算，`CompletableFuture.cancel(true)` 中参数 `true` 的意义是什么？

- A. 保证向计算线程调用 `interrupt()`。
- B. 保证立即停止计算且关闭其执行器。
- C. 保证回滚已完成的操作。
- D. 该实现不使用此参数控制中断，计算是否停止需要另外设计。

<a id="s25"></a>

### S25. 虚拟线程对象的直接 run()【Java 21】

以下代码在 Java 21 中执行：

```java
Thread virtual = Thread.ofVirtual().unstarted(
    () -> System.out.println("TASK")
);
virtual.run();
```

哪项描述正确？

- A. 创建并启动虚拟线程，输出 `TASK`。
- B. 必定由 main 执行任务并输出 `TASK`。
- C. 直接调用后变成平台线程。
- D. 直接调用不执行虚拟线程的任务，也没有通过此调用启动它。

<a id="multiple"></a>

## 二、多选题：M01—M15

<a id="m01"></a>

### M01. 内存可见性与 happens-before【基础】

哪些规则或判断正确？

- A. 对一条线程调用 `start()` 之前的操作，对该新线程的操作具有相应可见性保证。
- B. 同一把锁的释放，与随后成功获取之间建立同步关系。
- C. 对同一 volatile 字段的写，与后续读取之间建立相应同步关系。
- D. `sleep(1000)` 本身保证另一线程之前的普通字段写入可见。

<a id="m02"></a>

### M02. wait() 的使用规则【基础】

哪些描述正确？

- A. 调用 `monitor.wait()` 前，必须持有 monitor 的监视器锁。
- B. `wait()` 会释放当前线程持有的所有监视器及显式锁。
- C. 返回后通常应通过 `while` 再次检查条件。
- D. 从实际等待中恢复执行前，需要重新获取 monitor 的监视器锁。

<a id="m03"></a>

### M03. notify() 与 notifyAll()【基础】

哪些描述正确？

- A. 调用相应通知方法时，必须持有被通知对象的监视器锁。
- B. 通知方法返回前会自动释放该监视器锁。
- C. 没有等待者时，通知会累计保存为以后可消费的通知数量。
- D. `notify()` 不提供指定哪个等待线程被选中及公平唤醒的保证。

<a id="m04"></a>

### M04. 中断 API【基础】

哪些描述正确？

- A. `isInterrupted()` 不清除目标线程的中断标记。
- B. `Thread.interrupted()` 读取并清除当前线程的中断标记。
- C. `sleep()` 因中断抛 `InterruptedException` 时，中断标记被清除。
- D. 所有 Java I/O 方法都保证收到线程中断后立即停止。

<a id="m05"></a>

### M05. Lock 的获取与释放【基础】

针对 `ReentrantLock`，哪些描述正确？

- A. 每次成功获取或重入，需要对应一次释放。
- B. `lockInterruptibly()` 的获取等待可以通过中断结束。
- C. 带超时 `tryLock()` 返回 `false` 后，必须调用 `unlock()`。
- D. `newCondition()` 返回时，调用者已经获得关联锁。

<a id="m06"></a>

### M06. FutureTask 取消的边界【进阶】

针对常见 `FutureTask`，哪些描述正确？

- A. 取消成功后，Future 可以处于 `isDone()` 为 `true` 的状态。
- B. `cancel(true)` 返回 `true` 保证任务实际代码已经全部停止。
- C. `cancel(false)` 不通过该动作中断正在运行的任务，任务代码可能继续执行。
- D. 取消不会自动回滚此前产生的外部副作用。

<a id="m07"></a>

### M07. execute() 与 submit()【基础】

针对常见 `ThreadPoolExecutor` 的任务执行路径，哪些描述正确？

- A. `submit()` 返回可用于取得结果的 Future，任务提交仍可能因拒绝而抛异常。
- B. 直接 `execute()` 的普通 Runnable 若未捕获的运行时异常逃出任务，可能进入工作线程的未捕获异常处理路径。
- C. `submit()` 包装后的任务异常通常记录在 Future 中，通过 `get()` 观察。
- D. 任何拒绝策略都保证任务最终由工作线程成功执行。

<a id="m08"></a>

### M08. 执行器生命周期【基础】

哪些描述正确？

- A. Java 19 起 `ExecutorService` 支持 `AutoCloseable`，可用于资源作用域。
- B. `shutdown()` 发起有序关闭，但不等待全部任务完成。
- C. `awaitTermination()` 自身会发起关闭。
- D. 默认 `close()` 发起关闭并等待执行器终止。

<a id="m09"></a>

### M09. CompletableFuture 的回调【进阶】

哪些描述正确？

- A. 无 `Async` 后缀的方法不保证创建新线程执行回调。
- B. `thenRun()` 不论上游成功还是失败都执行，适合无条件清理。
- C. `handle()` 能处理正常和异常完成，并生成新结果。
- D. `whenComplete()` 的动作自己抛异常时，返回阶段也可能异常完成。

<a id="m10"></a>

### M10. allOf() 的行为【进阶】

哪些描述正确？

- A. 返回类型为 `CompletableFuture<Void>`。
- B. 自动把每个输入结果组成一个列表返回。
- C. 等待输入阶段全部完成；输入中存在异常完成时，整体异常完成。
- D. 某个输入失败时必定自动取消其余输入任务。

<a id="m11"></a>

### M11. 原子更新与统计【进阶】

哪些描述正确？

- A. `updateAndGet()` 的更新函数可能因竞争而被重复计算，应避免外部副作用。
- B. `AtomicReference` 会自动保护引用对象内部所有可变字段。
- C. `LongAdder.sum()` 在并发更新期间不是严格的整体原子快照。
- D. `volatile int count` 上的 `count++` 是原子操作。

<a id="m12"></a>

### M12. 协调工具选择【基础】

哪些描述正确？

- A. 同一个 `CountDownLatch` 的计数不能通过公开 API 重置到初始值。
- B. `CyclicBarrier` 适合固定参与者进行多轮阶段集合。
- C. `Semaphore.release()` 必须由此前成功获取该许可的同一个线程调用，否则总会抛监视器异常。
- D. `Phaser` 支持动态注册和注销参与者。

<a id="m13"></a>

### M13. BlockingQueue 方法语义【基础】

哪些描述正确？

- A. 阻塞队列不允许 `null` 元素。
- B. 有界队列满时，`put()` 可以等待空间。
- C. `poll()` 没有取到队首时可以返回 `null`。
- D. 无超时 `offer()` 不可能争用任何内部锁，也保证耗时为零。

<a id="m14"></a>

### M14. ConcurrentHashMap 的原子范围【进阶】

哪些描述正确？

- A. `putIfAbsent()` 将缺失检查与本次插入组成原子操作。
- B. `replace(key, oldValue, newValue)` 的值比较必定采用引用 `==`，与 AtomicReference 完全一致。
- C. `computeIfAbsent()` 的函数返回 `null` 时，不建立本次新映射。
- D. 并发更新时，`size()` 总能作为后续业务决策的稳定全局快照。

<a id="m15"></a>

### M15. ThreadLocal 与虚拟线程【基础】

哪些描述正确？

- A. `ThreadLocal.remove()` 移除的是当前线程的对应绑定。
- B. `withInitial(() -> sharedObject)` 即使返回同一个可变对象，也保证各线程对象内容独立。
- C. 在线程池任务结束时清理请求上下文，有助于防止线程复用造成的数据串用。
- D. 使用虚拟线程后，CPU 密集计算可以不受 CPU 核数限制地无限并行。

<a id="written"></a>

## 三、问答题：Q01—Q20

<a id="q01"></a>

### Q01. 线程、任务与执行方法【基础】

说明 `Thread`、`Runnable`、`Callable` 各自的角色，并比较 `start()`、直接 `run()`、直接 `call()`。需要包含执行线程、返回值及异常表达方式。

<a id="q02"></a>

### Q02. 四种等待方法【基础】

比较 `Thread.sleep()`、`Object.wait()`、`Thread.join()`、`LockSupport.park()`。需要说明等待对象、是否释放已有锁、唤醒条件及中断行为。

<a id="q03"></a>

### Q03. 协作式中断【基础】

解释中断标记与 `InterruptedException` 的关系。计算循环和阻塞等待应如何支持取消？捕获中断后，哪些处理方式合理，哪些方式容易导致问题？

<a id="q04"></a>

### Q04. 条件等待与通知【基础】

为什么 `wait()` 和 `Condition.await()` 通常要放在 `while` 中？为什么“先通知、后等待”不一定意味着正确代码会丢失进展？回答应区分业务条件状态与通知本身。

<a id="q05"></a>

### Q05. synchronized 与 ReentrantLock【基础】

比较二者的锁获取、释放、重入、中断、超时、条件队列和公平性。说明何时显式锁更合适，以及为何不能仅凭实现名就断言某一种永远更快。

<a id="q06"></a>

### Q06. AQS 的基本职责【进阶】

解释 AQS 的同步状态、获取队列、独占模式、共享模式以及 Condition 队列的关系。说明 `signal()` 之后，等待者为什么还不能立即继续受锁保护的业务。

<a id="q07"></a>

### Q07. volatile 与安全发布【基础】

某对象包含普通字段 `int data` 和 `volatile boolean ready`，初始分别为 0 和 false。写线程依次执行 `data = 42; ready = true;`，读线程读取 ready 为 true 后读取 data。假设只有这一写线程修改字段，没有之后的修改。

读线程是否有理由读取到 42？该结论能否推广为“volatile 能保证任意复合业务操作原子”？需要说明依据。

<a id="q08"></a>

### Q08. CAS、重试与 ABA【进阶】

解释 CAS 的比较与更新如何发生，为什么失败后常需重新读取条件，什么是 ABA，以及使用版本号时应保证哪些更新规则。补充说明 `AtomicReference.compareAndSet()` 比较的是什么。

<a id="q09"></a>

### Q09. AtomicLong 与 LongAdder【进阶】

比较二者的更新模型和读取语义。分别说明精确序号分配、库存条件扣减、高竞争统计计数更适合采用什么方案。

<a id="q10"></a>

### Q10. execute、submit 与异常去向【基础】

说明常见线程池中 `execute()` 和 `submit()` 的区别。为什么 `submit()` 的任务明明失败，却可能没有直接出现业务异常日志？给出观察任务失败的方式。

<a id="q11"></a>

### Q11. 线程池配置与过载处理【进阶】

解释核心线程数、最大线程数、队列、空闲回收和拒绝策略之间的关系。面对大量远程调用任务，应如何考虑工作线程数量、排队长度、超时和外部服务容量？

<a id="q12"></a>

### Q12. 有序关闭与资源生命周期【基础】

比较 `shutdown()`、`shutdownNow()`、`awaitTermination()`、`close()`。如何避免在线程池任务仍使用资源时提前关闭该资源？还需说明队列中取出的 Future 包装任务应如何处理。

<a id="q13"></a>

### Q13. Future 的超时、取消和实际退出【进阶】

解释 `get(timeout)`、`cancel(true)`、`isDone()`、任务实际退出之间的关系。若业务要求取消后确认任务的清理逻辑已经结束，需要什么额外机制？

<a id="q14"></a>

### Q14. 异步步骤的转换、串联与汇合【基础】

分别解释 `thenApply()`、`thenCompose()`、`thenCombine()`、`allOf()`、`anyOf()`。说明无 `Async` 后缀和有 `Async` 后缀对执行位置意味着什么，以及 `handle()` 与 `whenComplete()` 的用途差异。

<a id="q15"></a>

### Q15. 协调工具与线程池容量【基础】

比较 `CountDownLatch`、`CyclicBarrier`、`Semaphore`、`Phaser`。为什么将需要 5 个参与者集合的屏障任务全部提交到只有 2 个工作线程的固定线程池，可能无法推进？

<a id="q16"></a>

### Q16. 库存扣减代码审查【场景】

多个线程调用下面的方法，stock 为 `AtomicInteger`，初始库存为正数：

```java
boolean reserveOne() {
    if (stock.get() > 0) {
        stock.decrementAndGet();
        return true;
    }
    return false;
}
```

该方法是否保证库存非负？给出一组发生错误的并发交错，并提供能够保护该约束的修正方案。

<a id="q17"></a>

### Q17. 单线程池中的依赖等待【场景】

假设池没有其他任务，任务最终都能获得所需外部资源：

```java
ExecutorService pool = Executors.newSingleThreadExecutor();
Future<Integer> parent = pool.submit(() -> {
    Future<Integer> child = pool.submit(() -> 42);
    return child.get();
});
System.out.println(parent.get());
```

程序是否一定能打印 42？解释阻塞关系，并提出至少两种正确方向。说明简单增大线程数是否能普遍解决任意层级的依赖等待。

<a id="q18"></a>

### Q18. wait/notify 代码审查【场景】

```java
class Gate {
    private final Object monitor = new Object();
    private boolean ready;

    void awaitReady() throws InterruptedException {
        synchronized (monitor) {
            if (!ready) {
                monitor.wait();
            }
            System.out.println("继续处理");
        }
    }

    void open() {
        ready = true;
        monitor.notifyAll();
    }
}
```

找出至少三个需要修正的点，并提供正确的条件等待和开门方法。是否只将 ready 改成 volatile 就足够？

<a id="q19"></a>

### Q19. 原子初始化与请求上下文【场景】

服务存在两个问题。假设 loadValue 正常返回非 null 值，线程池中的每项任务代表一次独立请求：

```java
// 问题一：cache 是 ConcurrentHashMap
if (!cache.containsKey(key)) {
    cache.put(key, loadValue(key));
}

// 问题二：任务在线程池中执行
requestId.set(id); // requestId 是 ThreadLocal<String>
handleRequest();
```

分别解释重复初始化和请求上下文残留的原因，给出修正思路。使用 `computeIfAbsent()` 后，是否就可以在其回调中随意执行很慢的 I/O、递归更新同一张 map 或任意一次性副作用？

<a id="q20"></a>

### Q20. 虚拟线程与外部资源容量【场景，Java 21】

服务改用 `newVirtualThreadPerTaskExecutor()`，同时发起数万次数据库或 HTTP 请求，但数据库连接和外部服务容量有限。为什么仍可能过载？如何设计并发数量限制、截止时间及任务停止确认？CPU 密集任务是否一定因此加速？

<a id="choice-key"></a>

## 四、选择题答案速查

### 单选题

| 题号 | 答案 | 解析 |
|---|---|---|
| [S01](#s01) | A | [查看解析](#a-s01) |
| [S02](#s02) | B | [查看解析](#a-s02) |
| [S03](#s03) | C | [查看解析](#a-s03) |
| [S04](#s04) | B | [查看解析](#a-s04) |
| [S05](#s05) | D | [查看解析](#a-s05) |
| [S06](#s06) | B | [查看解析](#a-s06) |
| [S07](#s07) | C | [查看解析](#a-s07) |
| [S08](#s08) | B | [查看解析](#a-s08) |
| [S09](#s09) | A | [查看解析](#a-s09) |
| [S10](#s10) | A | [查看解析](#a-s10) |
| [S11](#s11) | C | [查看解析](#a-s11) |
| [S12](#s12) | B | [查看解析](#a-s12) |
| [S13](#s13) | C | [查看解析](#a-s13) |
| [S14](#s14) | C | [查看解析](#a-s14) |
| [S15](#s15) | C | [查看解析](#a-s15) |
| [S16](#s16) | B | [查看解析](#a-s16) |
| [S17](#s17) | C | [查看解析](#a-s17) |
| [S18](#s18) | B | [查看解析](#a-s18) |
| [S19](#s19) | D | [查看解析](#a-s19) |
| [S20](#s20) | C | [查看解析](#a-s20) |
| [S21](#s21) | B | [查看解析](#a-s21) |
| [S22](#s22) | C | [查看解析](#a-s22) |
| [S23](#s23) | A | [查看解析](#a-s23) |
| [S24](#s24) | D | [查看解析](#a-s24) |
| [S25](#s25) | D | [查看解析](#a-s25) |

### 多选题

| 题号 | 答案 | 解析 |
|---|---|---|
| [M01](#m01) | ABC | [查看解析](#a-m01) |
| [M02](#m02) | ACD | [查看解析](#a-m02) |
| [M03](#m03) | AD | [查看解析](#a-m03) |
| [M04](#m04) | ABC | [查看解析](#a-m04) |
| [M05](#m05) | AB | [查看解析](#a-m05) |
| [M06](#m06) | ACD | [查看解析](#a-m06) |
| [M07](#m07) | ABC | [查看解析](#a-m07) |
| [M08](#m08) | ABD | [查看解析](#a-m08) |
| [M09](#m09) | ACD | [查看解析](#a-m09) |
| [M10](#m10) | AC | [查看解析](#a-m10) |
| [M11](#m11) | AC | [查看解析](#a-m11) |
| [M12](#m12) | ABD | [查看解析](#a-m12) |
| [M13](#m13) | ABC | [查看解析](#a-m13) |
| [M14](#m14) | AC | [查看解析](#a-m14) |
| [M15](#m15) | AC | [查看解析](#a-m15) |

<a id="single-answers"></a>

## 五、单选题答案与解析

<a id="a-s01"></a>

### S01 解析｜答案：A

`worker.run()` 是普通平台线程上的直接方法调用，由当前 main 执行任务，不创建新执行线程。任务属于 worker 对象，不代表此次调用由 worker 执行。B、C 把对象归属混同于执行线程；D 混同于重复 `start()` 的限制。

<a id="a-s02"></a>

### S02 解析｜答案：B

一个 Thread 对象只对应一次启动生命周期，结束后也不能重启。需要再次运行任务，应创建新线程或重新提交给执行器。`join()` 只等待结束，不重置线程。

<a id="a-s03"></a>

### S03 解析｜答案：C

`sleep()` 是静态方法，暂停当前执行线程，不操作表达式左侧对象所代表的线程。正确表达方式是 `Thread.sleep(100)`，避免误读。

<a id="a-s04"></a>

### S04 解析｜答案：B

调用 `join()` 的 main 等待目标 worker 终止。它不取消 worker，也不会交换两条线程的执行任务。等待方还可能因中断退出等待。

<a id="a-s05"></a>

### S05 解析｜答案：D

`wait()` 要求调用方已经持有被操作对象的监视器，不会自动补齐该前置条件。应在 `synchronized (monitor)` 或其他已经持有该监视器的上下文中调用。

<a id="a-s06"></a>

### S06 解析｜答案：B

`sleep()` 不释放线程已有锁；`monitor.wait()` 实际等待时释放对该监视器的持有，并在恢复执行前重新获取。其他对象的监视器和显式锁不会一并释放。

<a id="a-s07"></a>

### S07 解析｜答案：C

中断是一种协作请求。普通计算代码需要检查状态，阻塞代码需要使用能够响应中断的接口。无限循环忽略这些条件时可以继续运行，不会在任意一行自动抛出受检异常。

<a id="a-s08"></a>

### S08 解析｜答案：B

首次调用读取已设置的标记，返回 true 并清除；第二次没有新中断，返回 false。若改用 `isInterrupted()`，读取本身不会清除。

<a id="a-s09"></a>

### S09 解析｜答案：A

成功获取后才进入配对释放区域：

```java
lock.lockInterruptibly();
try {
    // 受锁保护的操作
} finally {
    lock.unlock();
}
```

获取抛异常时不会进入这段 `finally`。B 在未获取情况下无条件解锁可能产生新的监视器异常，掩盖原中断；C、D 都破坏锁的使用契约。

<a id="a-s10"></a>

### S10 解析｜答案：A

`tryLock()` 是获取动作，不是单纯状态查询。成功重入也增加持有次数，因此同样需要对应释放。

<a id="a-s11"></a>

### S11 解析｜答案：C

无参 `tryLock()` 允许竞争可用锁，不遵守公平锁的排队公平策略。带超时版本遵守相应公平配置，并具有中断响应；`tryLock(0, unit)` 与无参版本也因此不完全相同。[ReentrantLock 官方说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/ReentrantLock.html#tryLock())。

<a id="a-s12"></a>

### S12 解析｜答案：B

第 1、2 个任务分别触发核心工作线程创建；第 3、4 个任务进入容量为 2 的队列；第 5 个任务无法入队，于是创建第 3 条工作线程执行它。此时没有任务结束，得到 3 条工作线程和 2 个排队任务。

接收顺序是“核心线程、排队、非核心扩容、拒绝”，不是先扩容到最大线程数。题干排除任务提前结束，才能使该数量判断稳定。

<a id="a-s13"></a>

### S13 解析｜答案：C

队列持续接受任务时，非核心扩容条件通常不成立。最大线程数仍有契约意义，但不能突破任务接收流程，在无界队列方案中不承担预想的主动扩容作用。[ThreadPoolExecutor 任务排队规则](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ThreadPoolExecutor.html)。

<a id="a-s14"></a>

### S14 解析｜答案：C

常见实现通过中断尝试停止活动任务，并返回尚未开始的排队任务。任务可能不响应中断，该方法也不等待实际停止。它没有强制终止或业务回滚保证。

<a id="a-s15"></a>

### S15 解析｜答案：C

普通 `Runnable.run()` 不返回结果，因此此重载正常完成后 `get()` 返回 null。`submit(Runnable, result)` 返回指定结果，`submit(Callable<T>)` 返回 call 的计算结果。

<a id="a-s16"></a>

### S16 解析｜答案：B

超时结束当前这次结果等待，Future 之后仍可能正常完成。是否请求取消、是否继续观察、是否释放资源，需要独立决定。

<a id="a-s17"></a>

### S17 解析｜答案：C

完成状态不等于成功状态。取消可能先使 Future 完成，而实际任务还在退出或清理，因此 D 也没有保证。

<a id="a-s18"></a>

### S18 解析｜答案：B

`anyOf()` 采用先完成的输入，包括异常完成，不会过滤失败寻找成功答案。`ExecutorService.invokeAny()` 才具有寻找一个成功任务结果的不同语义。

<a id="a-s19"></a>

### S19 解析｜答案：D

`thenCompose()` 把返回异步阶段的函数接入依赖链，并传递内层结果。`thenApply()` 用于普通结果转换，若函数返回 Future，通常得到嵌套类型。

<a id="a-s20"></a>

### S20 解析｜答案：C

两个线程可能都读到 5，之后各自写入 6，丢失一次更新。`incrementAndGet()` 或 `getAndIncrement()` 把加一作为一次原子更新，而不是分开的读写。

<a id="a-s21"></a>

### S21 解析｜答案：B

两个 `new String("A")` 创建不同对象。原子引用 CAS 使用引用身份比较，不调用 `equals()`；当前引用不等于 expected 引用，更新失败且不修改原引用。[AtomicReference.compareAndSet 契约](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicReference.html#compareAndSet(V,V))。

<a id="a-s22"></a>

### S22 解析｜答案：C

倒计数器管理数值而不验证调用线程或业务任务身份。一次任务重复调用就可能使计数过早归零，因此“每项完成恰好计一次”由业务代码保证。

<a id="a-s23"></a>

### S23 解析｜答案：A

无超时 `offer()` 对满队列返回 false，不等待未来容量。它仍可能竞争实现内部锁，不能据此宣称绝对零耗时。`put()` 才在容量不足时等待。

<a id="a-s24"></a>

### S24 解析｜答案：D

CompletableFuture 的该参数不用于中断计算线程。取消使结果以取消状态完成，原先启动的实际计算可能继续。不能直接套用 FutureTask 的执行线程中断行为。[CompletableFuture.cancel 契约](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CompletableFuture.html#cancel(boolean))。

<a id="a-s25"></a>

### S25 解析｜答案：D

Java 21 中虚拟线程对象的直接 `run()` 调用不执行其任务。应使用 `start()` 或构建器的 `start(task)` 等启动方法。该差异说明“直接 run 就由当前线程运行任务”不能不分线程类型地套用。[Thread.run 契约](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html#run())。

<a id="multiple-answers"></a>

## 六、多选题答案与解析

<a id="a-m01"></a>

### M01 解析｜答案：ABC

A、B、C 是规定的同步关系。D 中的时间等待不建立普通字段的跨线程发布保证；“等得足够久”不能替代同步。[Java 内存模型](https://docs.oracle.com/javase/specs/jls/se21/html/jls-17.html#jls-17.4.5)。

<a id="a-m02"></a>

### M02 解析｜答案：ACD

A 是前置条件；C 用于处理虚假唤醒和条件被其他线程改变；D 是恢复执行条件。B 错在扩大了释放范围，wait 只释放被操作对象的监视器。

<a id="a-m03"></a>

### M03 解析｜答案：AD

A、D 正确。通知方仍持锁，所以 B 错。通知没有计数积累，C 错；业务条件状态才是等待方是否继续的依据。[Object 通知与等待说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Object.html)。

<a id="a-m04"></a>

### M04 解析｜答案：ABC

前三项分别描述无清除查询、读取并清除、可中断休眠的异常行为。不同 I/O API 的中断响应不一致，不能对全部 I/O 作 D 中的保证。

<a id="a-m05"></a>

### M05 解析｜答案：AB

A、B 正确。带超时 tryLock 返回 false 表示本次没有获取，不应该为本次失败解锁；newCondition 只创建关联条件对象，不获取关联锁。

<a id="a-m06"></a>

### M06 解析｜答案：ACD

取消是一种 Future 完成状态，所以 A 正确。B 把状态改变等同于实际代码停止，错误。C 没有发出运行线程中断，D 说明取消不具备业务事务回滚能力。[Future 取消契约](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Future.html#cancel(boolean))。

<a id="a-m07"></a>

### M07 解析｜答案：ABC

A、B、C 区分提交阶段失败与运行阶段失败。D 不成立：拒绝策略可能抛异常、在调用者线程执行或直接丢弃。使用静默丢弃策略时，某些 Future 甚至可能长期保持未完成。

<a id="a-m08"></a>

### M08 解析｜答案：ABD

A、B、D 正确。`awaitTermination()` 仅等待，不发起关闭。默认 close 的等待期间被中断时，还会尝试停止活动任务、继续等待结束，并在返回前恢复标记。[ExecutorService 生命周期](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ExecutorService.html#close())。

<a id="a-m09"></a>

### M09 解析｜答案：ACD

thenRun 默认只在上游正常完成时执行，B 错。handle 可以转换成功和失败结果；whenComplete 常用于观察，但观察动作自己失败时仍可能影响返回阶段。

<a id="a-m10"></a>

### M10 解析｜答案：AC

allOf 是整组完成的汇合点，不负责把各个值组成列表；其他任务不会因为其中一个失败自动取消。它也不是保证首次失败就立即结束的接口。

<a id="a-m11"></a>

### M11 解析｜答案：AC

函数式原子更新可能重算，A 正确；LongAdder 的并发 sum 不是整体原子快照，C 正确。B 把引用原子性扩大到对象内部，D 把可见性扩大到复合更新原子性，均错误。

<a id="a-m12"></a>

### M12 解析｜答案：ABD

倒计数器一次性、屏障可多轮集合、Phaser 可动态参与，A、B、D 正确。信号量没有互斥锁式的获取者身份限制，多释放还可能错误地增加业务允许的并发量。

<a id="a-m13"></a>

### M13 解析｜答案：ABC

前三项是标准方法语义。D 错在把“不因队列满持续等待容量”理解成“不使用内部锁、耗时为零”。[BlockingQueue 方法对照](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/BlockingQueue.html)。

<a id="a-m14"></a>

### M14 解析｜答案：AC

A 的缺失检查和插入是同一次原子操作；C 的 null 结果不建立映射。条件 replace 使用值相等性，因此 B 错；并发 size 不能稳定支撑后续全局决策，D 错。

<a id="a-m15"></a>

### M15 解析｜答案：AC

A、C 正确。ThreadLocal 隔离的是绑定，绑定同一个对象仍然共享对象内容。虚拟线程改善大量等待型任务的线程成本，不凭空提供无限 CPU 并行能力。

<a id="written-answers"></a>

## 七、问答题参考答案

<a id="a-q01"></a>

### Q01 参考答案：线程、任务与执行方法

Thread 表示执行主体，Runnable 与 Callable 表示工作内容。Runnable 的 `run()` 返回 void，不声明一般受检异常；Callable 的 `call()` 返回泛型结果，并可以声明异常。

`new Thread(task)` 主要创建对象并保存任务。平台线程的 `start()` 安排新线程执行入口，调用者不等待任务完成；同一对象不能再次启动。直接调用普通平台线程的 `run()` 或任务的 `call()`，仍由调用者执行，不产生新线程。

任务返回值与异步结果获取是两层机制：Callable 可以返回值，但直接 call 是同步调用；交给执行器 submit 后，才通过 Future 管理完成状态与结果。Java 21 虚拟线程对象的直接 run 不执行其任务，应单独说明。

**得分要点：** 正确区分线程与任务 2 分；解释执行方和启动次数 2 分；说明结果与异常表达 2 分。

**常见失分：** 声称实现 Runnable 本身就创建线程，或声称 start 能直接返回任务结果。

<a id="a-q02"></a>

### Q02 参考答案：四种等待方法

| 方法 | 主要等待对象 | 锁语义 | 中断与其他恢复条件 |
|---|---|---|---|
| `sleep()` | 时间 | 不释放已有锁 | 时间到或中断；响应中断时抛异常并清除标记 |
| `wait()` | 监视器条件通知 | 释放调用对象监视器，恢复执行前重新获取 | 通知、中断或虚假唤醒；带超时版本另有时间条件 |
| `join()` | 目标线程终止 | 不释放调用方任意业务锁 | 目标终止或等待方被中断；带超时版本可能提前结束此次等待 |
| `park()` | 每线程单许可 | 不释放已有锁，无持监视器前提 | 许可、中断或虚假唤醒；不抛 InterruptedException、不自动清除标记 |

平台目标线程的 join 经典实现内部使用目标 Thread 的监视器等待，但这不代表 join 会释放调用方所有锁；虚拟目标线程还有不同终止等待实现。

park 有最多一个许可的模型，已启动线程先 unpark、后 park 可以消费已有许可；wait 通知不按计数保存。任何恢复后都应依据相应完成状态或条件判断是否继续。

**得分要点：** 等待对象 2 分；准确限定锁释放范围 2 分；中断和许可差异 2 分。

**常见失分：** 用“都会释放 CPU 和锁”概括四种方法，或混淆 Thread.join 与 CompletableFuture.join。

<a id="a-q03"></a>

### Q03 参考答案：协作式中断

中断状态是线程上可观察的请求标记；InterruptedException 是某些可中断 API 响应该请求的方式，不是在任意代码位置自动抛出的异常。

计算循环应定期检查 `Thread.currentThread().isInterrupted()`，将长计算拆成适当粒度，收到请求后执行必要清理并退出。阻塞逻辑应优先采用支持中断和时限的获取锁、队列或结果等待接口，并认识到普通 synchronized 获取不因中断退出。

sleep、wait、join 因中断抛异常时会清除标记。当前方法可声明异常时，应按契约向上传播；不能传播时，常见做法是恢复标记后结束当前工作：

```java
try {
    Thread.sleep(1000);
} catch (InterruptedException e) {
    Thread.currentThread().interrupt();
    return;
}
```

只打印日志后继续会丢掉取消请求；恢复标记后不断继续进入可中断等待，又可能反复立即抛异常。资源清理宜放在明确的 finally 或关闭协议中，但清理动作的中断策略也应符合业务契约。

**得分要点：** 标记与异常关系 2 分；两类任务的响应设计 2 分；传播、恢复与退出配合 2 分。

**常见失分：** 用 Thread.stop 代替协作式停止，或声称所有阻塞 I/O 都支持同样的中断行为。

<a id="a-q04"></a>

### Q04 参考答案：条件状态与通知

while 用来在持锁情况下重新检查业务条件。原因包括虚假唤醒、通知了不适合继续的等待者，以及其他线程在当前线程重新拿到锁前消耗了资源。通知只提供重检机会，不证明条件仍满足。

正确的监视器代码在同一锁下执行“检查条件、开始等待”和“修改条件、通知”。wait 在释放监视器并登记等待时与相应同步协议协调，避免另一线程插入到一个未受保护的检查与等待窗口。

```java
synchronized (monitor) {
    while (!ready) {
        monitor.wait();
    }
}
```

如果通知发生在等待方之前，且 ready 已在同一锁保护下改为 true，等待方之后检查时直接通过，不需要消费历史通知。错误的是不检查持久条件，只寄希望于某次通知被接收。

Condition 原理相同，只是可以在同一把显式锁上区分多个条件队列。signal 后等待者仍需重新获取关联锁。

**得分要点：** 给出至少两种重检原因 2 分；解释同一锁保护 2 分；区分持久条件与瞬时通知 2 分。

**常见失分：** 把 while 解释为“为了让线程不断占用 CPU 查询”，实际等待会挂起而非持续忙轮询。

<a id="a-q05"></a>

### Q05 参考答案：内置监视器与显式锁

二者都能提供互斥、可重入及相应内存可见性保证。synchronized 进入时获取监视器，离开同步范围自动释放；ReentrantLock 需要显式获取，并在成功获取后的 finally 中配对释放。

| 维度 | synchronized | ReentrantLock |
|---|---|---|
| 获取 | 进入同步块或方法 | 调用获取方法 |
| 释放 | 离开同步范围自动进行 | 显式 unlock |
| 获取等待中断 | 不以中断取消监视器获取 | 可选 lockInterruptibly |
| 获取等待超时 | 没有对应直接语法 | 可选带超时 tryLock |
| 条件等待 | 一个监视器的等待集合 | 可创建多个 Condition |
| 公平策略 | 不提供配置公平性的接口 | 构造时可配置公平性，部分方法存在例外 |

显式锁适合需要获取失败后的替代路径、超时、中断或多条件队列的场景。简单互斥使用 synchronized 通常更容易保证释放正确。

性能受竞争程度、临界区长度、线程模型、JDK 版本及具体优化影响，不能凭“底层是某种实现”推导永远更快。需要代表性测量，并先保证正确性。

**得分要点：** 共同保证与释放方式 2 分；中断、超时和条件差异 2 分；选择依据与公平例外 2 分。

**常见失分：** 声称 synchronized 不可重入，或公平锁保证所有线程在每一时刻严格按调用时间执行。

<a id="a-q06"></a>

### Q06 参考答案：AQS 的职责与两类队列

AQS 为同步工具提供状态管理、等待节点排队、挂起与恢复等基础能力，子类定义获取成功与释放成功的条件。它不是“所有 Java 锁的唯一实现”，也不是一个直接用于业务的通用锁对象。

独占模式一次由某个持有者占用，例如 ReentrantLock；共享模式允许多个获取者满足条件，例如信号量或计数归零后的等待者。state 的含义由具体同步器解释，可以是重入次数、许可数或剩余计数。

同步队列用于等待获取同步状态；Condition 队列用于等待业务条件。调用 await 会记录条件等待、释放关联锁，之后还要恢复原持有层数。signal 将等待者转向重新获取锁的流程，不直接交出通知方持有的锁。

公平与否由具体获取策略参与决定，存在先进先出的队列也不代表所有 API 都绝不插队。中断、取消、超时和虚假唤醒还会改变等待节点处理。

**得分要点：** 框架与子类职责 2 分；独占、共享及状态含义 2 分；区分条件队列与同步队列 2 分。

**常见失分：** 将 signal 解释为“直接把锁交给等待线程”，或将 AQS 的 state 在所有同步器中都理解为相同计数。

<a id="a-q07"></a>

### Q07 参考答案：volatile 发布数据

在题设前提下，读线程观察到 ready 为 true 后读取 data，应观察到 42。写线程在 volatile 写之前完成普通字段写入，读线程通过后续 volatile 读建立相应同步关系，先前写入对它可见。

```text
写线程：data = 42 → ready = true（volatile 写）
                                  ↓ 同步关系
读线程：             ready 为 true（volatile 读）→ 读取 data
```

该结论依赖发布顺序、对应读取以及没有后续竞争修改等题设条件，不能仅凭“两个字段写在一起”得出。

volatile 不让 `count++` 原子，也不自动把多个对象、多字段更新变成一个事务。复合约束应采用锁、CAS 发布整体不可变状态或其他适合的同步机制。[happens-before 规则](https://docs.oracle.com/javase/specs/jls/se21/html/jls-17.html#jls-17.4.5)。

**得分要点：** 给出 42 结论及前提 2 分；说明发布与读取同步关系 2 分；区分原子性与可见性 2 分。

**常见失分：** 只用“数据刷新到主内存”解释全部行为，或将该例推广到任意后续无同步写入。

<a id="a-q08"></a>

### Q08 参考答案：CAS 与状态历史

CAS 将“当前值是否匹配 expected”与“匹配时写 update”作为一次条件原子操作。失败表示本次条件不成立，不自动更新，也不自动重试。

重试需要重新读取旧状态、重新判断业务条件、重新计算候选值；否则可能基于过期库存或配置继续执行错误操作。高竞争下重试会消耗 CPU，函数式更新的计算还可能重复，因此计算部分不宜包含一次性外部副作用。

ABA 是状态经历 A→B→A 后，当前值比较仍认为等于 A，却无法识别中间变化。若算法要求识别变化历史，可将状态与版本号一起原子比较更新。相关更新必须遵守一致的版本推进规则，不能在部分更新路径遗漏推进；还应考虑版本溢出或重用带来的再次相等。

AtomicReference 比较引用身份，两个 equals 相等的新对象不一定能通过 CAS。引用原子发布也不保护对象内部随后的无同步修改。

**得分要点：** 条件原子更新与重试 2 分；ABA 和版本规则 2 分；引用比较及副作用边界 2 分。

**常见失分：** 将 CAS 失败视为可不重检条件直接重试，或将 CAS 循环视为天然保证多字段事务。

<a id="a-q09"></a>

### Q09 参考答案：精确状态与分散统计

AtomicLong 提供对单个 long 的原子读取、加法和条件更新，适合需要本次更新的精确旧值、新值或 CAS 的场景。高竞争时，各线程更新同一个位置会形成竞争。

LongAdder 可以把竞争更新分散到基础值及多个计数单元，适合高竞争统计。sum 会汇总这些单元，但并发更新期间不是整体原子快照，也不提供本次全局加一得到的唯一序号。

- 单 JVM 精确序号可使用 AtomicLong 的原子加法，同时考虑溢出和跨进程唯一性要求。
- 库存非负约束需要 CAS 条件循环、锁或在真实存储层的原子条件更新；单独 decrement 方法不足以检查下界。
- 允许统计读取近似反映并发进展的高竞争计数可考虑 LongAdder；更新全部停止后可以取得最终汇总。

**得分要点：** 更新结构与竞争 2 分；读取语义 2 分；三类场景选择及边界 2 分。

**常见失分：** 声称 LongAdder 在所有情况下更快，或用 sum 的并发结果直接决定余额扣款。

<a id="a-q10"></a>

### Q10 参考答案：提交与运行两阶段失败

execute 接收 Runnable，不返回任务 Future；submit 将任务包装为可保存完成状态、结果和异常的对象，返回 Future。普通 Runnable 的提交结果为 null，Callable 的结果来自 call。

直接 execute 的普通任务若运行时异常逃出 run，可能导致工作线程退出并进入其未捕获异常处理路径。submit 的包装任务通常捕获并保存失败，所以工作线程没有向外抛出同样的异常，若调用方又忽略 Future，就容易失去业务失败记录。

观察失败可通过 get 捕获 ExecutionException 并检查 cause，或建立统一的结果收集、完成回调和失败记录机制。任务提交阶段的拒绝异常应另外处理，不能只在等待结果处捕获。

```java
try {
    Integer result = future.get();
} catch (ExecutionException e) {
    Throwable cause = e.getCause();
    // 根据真实失败原因记录或处理
}
```

**得分要点：** 返回值与包装区别 2 分；异常路径 2 分；提交拒绝和结果观察 2 分。

**常见失分：** 声称 submit 吞掉异常使任务成功，或以为任何 Executor 都只能在工作线程执行。

<a id="a-q11"></a>

### Q11 参考答案：容量与过载

ThreadPoolExecutor 通常先补到核心线程，再尝试入队，队列无法接受才扩到最大线程，仍不接受则执行拒绝策略。空闲回收主要影响非核心工作线程；允许核心超时需要额外配置及合法的存活时间。

无界队列可能导致持续积压；直接移交队列会促使线程扩展；有界队列让过载时的行为更可预测，但仍需要设计拒绝后的响应。

远程调用任务应结合平均及尾部耗时、到达速率、排队时延、连接数和外部服务容量确定并发与队列上限，并进行压测验证。等待占比较大不意味着可以无限增加线程，过多并发会将压力转给外部服务。

应为请求和等待分配端到端截止时间，过期任务不应继续长时间排队执行。拒绝可以返回繁忙、降级或受控重试；CallerRuns 会让提交者直接执行并产生反压，但也可能拉长请求线程耗时，不能视为普遍安全的默认方案。

**得分要点：** 接收和扩容顺序 2 分；队列及拒绝策略 2 分；容量、截止时间和外部约束 2 分。

**常见失分：** 只背固定线程数公式，不给出任务类型、延迟预算和外部容量前提。

<a id="a-q12"></a>

### Q12 参考答案：关闭请求不等于已经关闭

shutdown 拒绝新任务并允许已接收任务执行，不等待结束。shutdownNow 更积极地尝试中断活动任务并取出排队任务，也不等待实际停止。awaitTermination 只等待关闭流程的终止，不发起关闭。Java 19 起默认 close 发起有序关闭并等待结束，可与资源作用域结合。

资源关闭顺序应符合依赖关系：先停止接收新的资源使用任务，再等待已接收任务结束或协作退出，最后释放它们仍可能访问的数据库客户端、文件或连接资源。等待超时不能证明它们已经不再使用资源。

shutdownNow 返回的对象可能是 submit 创建的 Future 包装任务。它们被移出队列不一定自动完成取消状态，应对这批未执行任务作明确的取消、失败记录或转交处理，避免其他等待者一直 get。

默认 close 等待时被中断，会按契约尝试停止活动任务并继续等待终止，再恢复标记；任务忽略停止请求时，close 也可能长期等待。

**得分要点：** 四个方法职责 2 分；任务与外部资源顺序 2 分；未执行 Future 和中断边界 2 分。

**常见失分：** 调用 shutdown 后立刻释放任务必需资源，或将 awaitTermination 超时视为池已终止。

<a id="a-q13"></a>

### Q13 参考答案：结果状态与实际退出

get(timeout) 的超时通常只结束调用方这次等待，任务继续运行。FutureTask 的 cancel(true) 尝试使 Future 进入取消状态，并中断其执行线程；任务是否停止取决于实际响应。

isDone 的 true 包括成功、失败和取消。取消成功可以先于任务退出，任务还可能正在 finally 中清理，甚至忽略中断继续外部调用。因此不能以 isDone 或 CancellationException 证明清理完成。

需要实际退出确认时，可使用在任务退出边界记录的 CountDownLatch、一个独立完成信号或由任务生命周期管理器提供的完成确认。如果任务可能在启动前取消，应由统一管理逻辑同时处理“未启动”和“已启动”两条路径，不能只依赖未必会执行的任务 finally。

若使用整个池终止作为确认，必须确保池的范围合适、全部相关任务确实受该池管理；它不能替代所有外部异步请求的完成确认。CompletableFuture.cancel 更不负责用参数中断后台计算。

**得分要点：** 超时和取消分开 2 分；Future 状态与执行状态分开 2 分；退出确认及未启动路径 2 分。

**常见失分：** 用 isDone 当作全部清理结束的证明，或只对已进入任务体的路径建立退出计数。

<a id="a-q14"></a>

### Q14 参考答案：异步依赖图

| 方法 | 作用 | 需要区分的边界 |
|---|---|---|
| `thenApply()` | 正常结果转换成普通新值 | 返回 Future 的函数会形成嵌套类型 |
| `thenCompose()` | 将返回异步阶段的函数接入依赖链 | 后一步依赖前一步，不意味着两步独立并发 |
| `thenCombine()` | 合并两份正常结果 | 并发来自前面的独立任务提交 |
| `allOf()` | 等全部输入完成 | 返回 Void，不自动收集值、不自动取消其余任务 |
| `anyOf()` | 采用一个先完成结果 | 可以由失败输入决定结果，不只选成功 |

无 Async 回调可在完成上游的线程或注册线程执行，不能保证新线程。Async 通过默认或显式执行器调度，工作线程可复用，也不是“每步骤新建专属线程”。

handle 接收成功或失败并生成新结果；whenComplete 主要观察并保留上游结果，但观察动作失败可能影响返回阶段。thenRun 默认只跟随上游正常完成，不是无条件 finally。

**得分要点：** 五个方法的依赖语义 2 分；执行位置 2 分；异常转换与观察差异 2 分。

**常见失分：** 认为 allOf 总是遇到第一次失败立即返回，或将 Async 等同于非阻塞 I/O。

<a id="a-q15"></a>

### Q15 参考答案：协调工具与可执行容量

CountDownLatch 用只减不复位的计数等待若干事件结束；CyclicBarrier 用固定参与者进行多轮集合；Semaphore 用许可数约束并发访问，不检查释放者身份；Phaser 管理可动态变化的阶段参与者。

只有 2 个工作线程时，前两个屏障任务可能都运行到 await 并占住工作线程。其余 3 个参与者尚在队列，无法执行到屏障，前两个又必须等它们到达，形成线程饥饿式的依赖循环。

需要让同一阶段所需参与者具有足够的执行机会，或重新设计协调方式，使等待不占住其余参与者必需的有限工作线程。动态使用 Phaser 时，还要在提交失败、任务退出等路径正确注销，不留下不存在的参与者。

**得分要点：** 四种工具状态模型 2 分；准确描述线程饥饿循环 2 分；容量与参与者生命周期 2 分。

**常见失分：** 把等待屏障的线程视为已经退出线程池，或把 countDown 看成框架自动验证的一次任务完成。

<a id="a-q16"></a>

### Q16 参考答案：复合库存检查不原子

原代码不能保证库存非负。库存为 1 时，线程 A 和 B 都读到大于零；A 原子减到 0，B 又原子减到 -1，二者都返回 true。单次减法原子，不代表“检查并扣减”原子。

一种修正是 CAS 条件循环：

```java
boolean reserveOne() {
    for (;;) {
        int before = stock.get();
        if (before <= 0) {
            return false;
        }
        if (stock.compareAndSet(before, before - 1)) {
            return true;
        }
        // 更新竞争失败，重新读取并重新检查条件
    }
}
```

另一种是用同一把锁保护检查和扣减，并使所有相关读写都遵守这套锁规则。如果库存实际共享于多个进程，仅在一个 JVM 的变量上修正不足以保护真实全局库存，应使用存储层的条件更新或其他跨进程一致性机制。

**得分要点：** 明确原代码错误 2 分；给出可到 -1 的交错 2 分；正确修复及保护范围 2 分。

**常见失分：** 只把 decrement 替换成 addAndGet，或者 CAS 失败后不重新检查库存。

<a id="a-q17"></a>

### Q17 参考答案：唯一工作线程等待排队子任务

题设中的正常执行流程无法打印 42：唯一工作线程被 parent 占用，parent 在 child.get 上等待；child 已入同一执行器队列，却没有空闲工作线程运行。main 又等待 parent 的结果。循环依赖来自执行容量，不要求存在 synchronized 锁。

可采用的方向包括：不必异步的子计算直接在当前任务内执行；在调用者层面提交、等待和汇合；将依赖改成完成阶段连接，使父步骤注册后续而不占住工作线程阻塞；或采用经过容量及依赖分析的独立执行器。

以下局部示例将阶段接成非阻塞依赖，等待发生在外部调用方：

```java
CompletableFuture<Integer> result =
    CompletableFuture.supplyAsync(() -> 1, pool)
        .thenCompose(seed ->
            CompletableFuture.supplyAsync(() -> seed + 41, pool));
System.out.println(result.get());
```

简单改成 2 个线程可解决题设的一层依赖，但不能普遍解决更深嵌套、多组父任务同时占满线程等情况。增加超时可以暴露问题、限制等待，不能自动使依赖关系正确。

**得分要点：** 完整阻塞链 2 分；至少两个正确方向 2 分；说明增大池与超时的边界 2 分。

**常见失分：** 认为线程池会因为父任务调用 get 自动增加工作线程，或把子任务改成 submit 就算修正。

<a id="a-q18"></a>

### Q18 参考答案：正确的监视器条件协议

至少有三个问题：open 没有持有 monitor 却调用 notifyAll，将抛监视器异常；ready 的修改没有遵守等待方使用的同一监视器规则，形成数据竞争；等待方使用 if，唤醒后没有重新检查条件。

可修正为：

```java
void awaitReady() throws InterruptedException {
    synchronized (monitor) {
        while (!ready) {
            monitor.wait();
        }
    }
    System.out.println("继续处理");
}

void open() {
    synchronized (monitor) {
        ready = true;
        monitor.notifyAll();
    }
}
```

开门状态一旦为 true 本题不再关闭，因此示例可将后续处理放在锁外。真实业务若还会关闭或消耗条件，必须重新分析处理操作与状态变化是否需要在锁内保持原子。

只添加 volatile 不能补齐通知方法的持锁要求，也不能把检查与等待按同一锁协调，更不能替代 while 重检。通知方写入与等待方读写都在同一锁规则下时，ready 不必为这个协议额外声明 volatile。

**得分要点：** 找出三个缺陷 2 分；正确修复两条路径 2 分；解释 volatile 不能包办修复 2 分。

**常见失分：** 只把 notifyAll 改成 notify，或只给 open 增加休眠等待“对方先 wait”。

<a id="a-q19"></a>

### Q19 参考答案：容器方法原子性与线程复用

ConcurrentHashMap 的 containsKey 和 put 分别安全，但组合不是一次缺失条件更新。两个线程都可能观察缺失、都执行 load，随后覆盖彼此结果。

短小且适合本地计算的初始化可采用 `computeIfAbsent(key, loader)`；已有预计算值可用 `putIfAbsent()`，但后者不会避免在调用它之前已经发生的重复加载。计算返回 null 或映射之后被移除，后续调用仍可能重新计算。

computeIfAbsent 回调会影响竞争更新，不适合长时间阻塞；不能在计算回调内修改同一张 map。昂贵远程加载可以用专门加载器或“保存共享结果占位符”的设计，再分别管理失败、超时和清理。无论哪种方式，都不能把映射原子性当成外部一次性副作用的事务保证。

线程池复用工作线程，遗漏清理的 ThreadLocal 请求值可能留给下一个任务，或长期保留关联对象。普通请求边界可使用：

```java
try {
    requestId.set(id);
    handleRequest();
} finally {
    requestId.remove();
}
```

嵌套上下文若要求恢复先前值，应遵守框架的恢复协议；任务上下文跨线程传播也需要明确机制，不能认为 ThreadLocal 自动传播。[ConcurrentHashMap 计算规则](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ConcurrentHashMap.html#computeIfAbsent(K,java.util.function.Function))、[ThreadLocal API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/ThreadLocal.html)。

**得分要点：** 解释多步竞争及正确原子入口 2 分；回调与外部副作用边界 2 分；线程复用及清理恢复 2 分。

**常见失分：** 声称 ConcurrentHashMap 能保证某个 key 永远只初始化一次，或弱引用使值不需要清理。

<a id="a-q20"></a>

### Q20 参考答案：轻量线程不等于无限资源

虚拟线程减少大量等待型任务的线程成本，每任务一条虚拟线程的执行器也不会自动限制同时发出的数据库请求、HTTP 请求或在途任务。连接池容量、外部服务能力、内存和 CPU 仍然有限。

可使用连接池、信号量、业务准入和有界任务入口限制实际并发或在途数量。仅在数万个虚拟线程内部获取许可，虽然限制了资源访问，但仍可能留下大量等待任务，所以还应关注入口积压及内存。

截止时间应贯穿准入、许可获取、网络连接、读写和结果等待，使用真正支持相应取消或超时的客户端能力。Future 等待超时或 CompletableFuture.orTimeout 不会自动停止外部操作，取消后需要明确观察实际结束和资源归还。

CPU 密集任务的吞吐主要受可用计算资源限制，虚拟线程不提供额外 CPU 核数。Java 21 下还需要按实际线程阻塞与监视器使用情况分析承载线程影响，不能无条件承诺改造后加速。

**得分要点：** 区分线程成本与资源容量 2 分；设计访问限额及入口控制 2 分；端到端时限、退出确认与 CPU 边界 2 分。

**常见失分：** 只将固定线程池替换为虚拟线程执行器，就声称并发上限和所有延迟问题已被解决。

<a id="references"></a>

## 八、知识点索引与官方资料

### 按知识点查题

| 主题 | 单选题 | 多选题 | 问答题 |
|---|---|---|---|
| 线程启动与方法调用 | [S01](#s01)、[S02](#s02)、[S03](#s03)、[S04](#s04) | — | [Q01](#q01) |
| 等待、通知、条件队列 | [S05](#s05)、[S06](#s06) | [M02](#m02)、[M03](#m03) | [Q02](#q02)、[Q04](#q04)、[Q18](#q18) |
| 中断与任务退出 | [S07](#s07)、[S08](#s08) | [M04](#m04) | [Q03](#q03)、[Q13](#q13) |
| 显式锁与 AQS | [S09](#s09)、[S10](#s10)、[S11](#s11) | [M05](#m05) | [Q05](#q05)、[Q06](#q06) |
| 内存模型与安全发布 | — | [M01](#m01) | [Q07](#q07) |
| 线程池、拒绝与关闭 | [S12](#s12)、[S13](#s13)、[S14](#s14)、[S15](#s15) | [M07](#m07)、[M08](#m08) | [Q10](#q10)、[Q11](#q11)、[Q12](#q12)、[Q17](#q17) |
| Future 超时与取消 | [S16](#s16)、[S17](#s17) | [M06](#m06) | [Q13](#q13) |
| 异步结果组合 | [S18](#s18)、[S19](#s19)、[S24](#s24) | [M09](#m09)、[M10](#m10) | [Q14](#q14) |
| CAS、原子类与统计 | [S20](#s20)、[S21](#s21) | [M11](#m11) | [Q08](#q08)、[Q09](#q09)、[Q16](#q16) |
| 线程协调 | [S22](#s22) | [M12](#m12) | [Q15](#q15) |
| 并发容器 | [S23](#s23) | [M13](#m13)、[M14](#m14) | [Q19](#q19) |
| ThreadLocal 与虚拟线程 | [S25](#s25) | [M15](#m15) | [Q19](#q19)、[Q20](#q20) |

### 官方核对入口

题目以公开方法契约为主要依据，部分内部机制采用 OpenJDK 21 的常见实现模型。以下资料供版本核对及继续阅读：

- [Thread API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html)：启动、休眠、中断、终止等待及虚拟线程。
- [Object API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Object.html)：监视器等待和通知。
- [ReentrantLock](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/ReentrantLock.html) 与 [Condition](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/Condition.html)：获取策略、条件等待及中断。
- [JLS 第 17 章](https://docs.oracle.com/javase/specs/jls/se21/html/jls-17.html) 与 [并发包说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/package-summary.html)：内存一致性关系。
- [ThreadPoolExecutor](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ThreadPoolExecutor.html) 与 [ExecutorService](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ExecutorService.html)：接收任务、提交结果及关闭。
- [Future](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Future.html)、[FutureTask](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/FutureTask.html) 与 [CompletableFuture](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CompletableFuture.html)：等待、取消、异常和组合。
- [AtomicInteger](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicInteger.html)、[AtomicReference](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicReference.html) 与 [LongAdder](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/LongAdder.html)：单变量原子更新及统计边界。
- [CountDownLatch](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CountDownLatch.html)、[CyclicBarrier](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CyclicBarrier.html)、[Semaphore](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Semaphore.html) 与 [Phaser](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Phaser.html)：协调工具。
- [LockSupport](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/LockSupport.html)、[BlockingQueue](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/BlockingQueue.html)、[ConcurrentHashMap](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ConcurrentHashMap.html) 与 [ThreadLocal](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/ThreadLocal.html)：许可、传递元素、映射更新和线程绑定。

### 文档核对记录

已核对 25 道单选、15 道多选、20 道问答的编号、选项数量和答案对应关系；单选均为一个正确选项，多选均至少两个正确选项。目录、题目、解析及知识点索引的内部链接已检查。

本机 Java / javac 21.0.7 已对 S01、S08、S12、S18、S21、S25 的关键行为执行验证，均通过。其他代码为教学局部片段，未作为全部独立程序逐项运行；题目正确性以题设前提、方法契约及解析为依据。
