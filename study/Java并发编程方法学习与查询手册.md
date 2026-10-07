# Java 并发编程方法学习与查询手册

> 适用对象：掌握 Java 基本语法、刚开始学习并发编程的学习者。  
> 版本基准：Java SE 21；线程基础默认讨论普通平台线程，涉及虚拟线程时明确限定。  
> 更新与核对日期：2026-10-08。  
> 内容结构：41 个重点方法采用详细教程，其余 92 个方法条目集中在一张速查表中。  
> 源码说明：执行流程和伪代码是帮助理解的概念模型，不是逐字复制的 JDK 源码。公开 API 契约与具体实现细节分别说明。

## 阅读导航

| 章节 | 学习目标 |
|---|---|
| [1. 基础概念：先理解代码由谁执行、数据由谁保护](#sec-1) | 按场景、机制与代码逐步理解 |
| [2. Thread：从执行任务到等待结束](#sec-2) | 按场景、机制与代码逐步理解 |
| [3. Object：等待业务条件与通知条件变化](#sec-3) | 按场景、机制与代码逐步理解 |
| [4. Lock 与 Condition：显式控制获取锁与条件等待](#sec-4) | 按场景、机制与代码逐步理解 |
| [5. 执行器与线程池：从提交任务到结束服务](#sec-6) | 按场景、机制与代码逐步理解 |
| [6. Future：等待任务结果与理解取消](#sec-7) | 按场景、机制与代码逐步理解 |
| [7. CompletableFuture：用结果连接后续任务](#sec-8) | 按场景、机制与代码逐步理解 |
| [8. 原子类：把读取、判断和更新连成一次操作](#sec-9) | 按场景、机制与代码逐步理解 |
| [9. 线程协调工具：区分结束、集合和进入名额](#sec-10) | 按场景、机制与代码逐步理解 |
| [10. LockSupport：让线程暂停，把条件判断留给同步协议](#sec-11) | 按场景、机制与代码逐步理解 |
| [11. BlockingQueue：让生产者和消费者通过队列交接数据](#sec-12) | 按场景、机制与代码逐步理解 |
| [12. ConcurrentHashMap：让“是否存在”和更新成为一次映射操作](#sec-13) | 按场景、机制与代码逐步理解 |
| [13. ThreadLocal：绑定在线程上，而不是绑定在某次请求上](#sec-14) | 按场景、机制与代码逐步理解 |
| [14. 非重点方法统一速查表](#other-methods) | 集中查询用途与关键限制 |
| [15. 完整实验：从代码观察方法行为](#sec-16) | 运行实验、对照排错与复习 |
| [16. 方法对照、常见误区与排错](#sec-17) | 运行实验、对照排错与复习 |
| [17. 学习检查与参考答案](#sec-18) | 运行实验、对照排错与复习 |
| [18. 官方资料与源码入口](#sec-19) | 运行实验、对照排错与复习 |

## 学习路线与阅读方式

第一次学习适合依次阅读基础概念、Thread、Object 和 Lock。理解线程怎样启动、谁在等待、等待时持有什么锁之后，再学习线程池、Future 和异步结果组合。

原子类、协调工具、队列和映射适合结合具体业务问题阅读：计数是否会丢失，库存能否扣成负数，如何等三个任务结束，如何最多允许三个人同时访问资源，生产者如何把工作交给消费者。

重点方法的正文按照“为什么需要 → 必要概念 → 内部逐步做什么 → 代码如何运行 → 返回说明什么 → 易错点”展开。完整程序集中在后面的实验章节，适合在阅读对应方法后执行。速查表用于确认非重点方法的用途及关键限制，无需在第一轮逐项记忆。

### 示例约定

标注“局部示例”的代码需放入合适的类或方法，并添加必要导入和异常声明。`Runnable`、`Thread` 等 java.lang 类型不需要手动导入，`Future`、`ReentrantLock` 等需要导入对应包。完整实验包含这些外围结构。

伪代码或 `text` 代码块用于说明执行关系，不能直接编译。代码输出顺序只有在明确同步关系下才作保证；一次运行得到的顺序不代表所有运行都如此。

## 重点方法入口

| 方法族 | 详细讲解入口 |
|---|---|
| Thread | [Thread.start()](#method-2-1)、[Thread.run()](#method-2-2)、[Thread.sleep(...)](#method-2-4)、[Thread.join()](#method-2-5)、[Thread.interrupt()](#method-2-7)、[Thread.isInterrupted()](#method-2-8)、[Thread.interrupted()](#method-2-9) |
| Object | [Object.wait()](#method-3-1)、[Object.notifyAll()](#method-3-4) |
| Lock / Condition | [ReentrantLock.lock()](#method-4-1)、[ReentrantLock.unlock()](#method-4-2)、[ReentrantLock.tryLock(timeout, unit)](#method-4-4)、[ReentrantLock.lockInterruptibly()](#method-4-5)、[Condition.await()](#method-4-7) |
| 执行器 | [Executor.execute(Runnable)](#method-6-5)、[ExecutorService.submit(...)](#method-6-6)、[ExecutorService.shutdown()](#method-6-7)、[ExecutorService.shutdownNow()](#method-6-8)、[ScheduledExecutorService.scheduleAtFixedRate(...)](#method-6-16) |
| Future | [Future.get()](#method-7-1)、[Future.cancel(mayInterruptIfRunning)](#method-7-3) |
| CompletableFuture | [CompletableFuture.supplyAsync(...)](#method-8-2)、[CompletableFuture.thenApply(fn)](#method-8-3)、[CompletableFuture.thenCompose(fn)](#method-8-7)、[CompletableFuture.thenCombine(other, fn)](#method-8-8)、[CompletableFuture.join()](#method-8-14) |
| 原子类 | [AtomicInteger.compareAndSet(expected, update)](#method-9-3)、[AtomicInteger.updateAndGet(fn)](#method-9-10)、[AtomicReference.compareAndSet(expected, update)](#method-9-13) |
| 协调工具 | [CountDownLatch.countDown()](#method-10-1)、[CountDownLatch.await()](#method-10-2)、[CyclicBarrier.await()](#method-10-4)、[Semaphore.acquire()](#method-10-7)、[Semaphore.release()](#method-10-10) |
| LockSupport | [LockSupport.park()](#method-11-1)、[LockSupport.unpark(thread)](#method-11-4) |
| BlockingQueue | [BlockingQueue.put(element)](#method-12-1)、[BlockingQueue.take()](#method-12-2) |
| ConcurrentHashMap | [ConcurrentHashMap.putIfAbsent(key, value)](#method-13-2)、[ConcurrentHashMap.computeIfAbsent(key, fn)](#method-13-3) |
| ThreadLocal | [ThreadLocal.remove()](#method-14-4) |

<a id="sec-1"></a>

## 1. 基础概念：先理解代码由谁执行、数据由谁保护

### 线程与任务：执行者和工作内容

任务是需要做的工作，线程是执行工作的一条控制流程。二者分开后，才容易理解 Thread 与线程池。

```java
Runnable task = () -> {
    System.out.println("处理一张订单");
};
Thread worker = new Thread(task, "order-worker");
worker.start();
```

第一行创建任务对象，花括号里的任务代码尚未运行。第二步创建一个 Thread 对象，将任务交给它保存，也尚未运行任务。第三步才安排新线程执行任务。

同一个任务可以被不同执行机制调用。`task.run()` 是普通调用；`new Thread(task).start()` 使用新线程；`pool.execute(task)` 交给执行器。任务类型本身不决定它在哪条线程上运行。

Runnable 的 `run()` 不返回结果。Callable 的 `call()` 可以返回结果并声明异常，但直接调用 call 仍是同步方法调用，只有交给执行器安排后才具有相应异步执行关系。

### 当前线程与目标线程：看清方法作用对象

“当前线程”是正在执行某行代码的线程。“目标线程”是某个 Thread 变量所引用的线程。main 中执行下列代码时，三行分别具有不同含义：

```java
worker.interrupt(); // main 向 worker 发出中断请求
worker.join();      // main 等待 worker 结束
Thread.sleep(100);  // main 自己暂停一段时间
```

不能从方法写在 `worker.` 后面，就推断“worker 自己被等待”或者“worker 被暂停”。尤其 `sleep()` 是静态方法，应使用类名调用；通过对象表达式调用也仍作用于当前线程。

### 并发与并行：能交错，不代表同时使用 CPU

并发强调多项任务的执行在时间上交错、重叠推进。并行强调某一时刻多项任务确实同时执行。只有一份 CPU 执行资源时，多个线程也能通过切换形成并发；更多线程不会凭空增加计算资源。

一个线程可能运行、等待 CPU、等待资源或已经结束。线程暂停时可以让其他线程获得机会，但“让出执行机会”与“释放某把业务锁”是不同动作。

### 共享数据：问题经常藏在多步操作里

多个线程访问同一份可变数据时，需要考虑操作会怎样交错。例如：

```java
count++;
```

这行不是单一步骤，可以理解为“读取 count、计算加一、写回 count”。初始 count 为 0 时，一种可能的交错如下：

| 顺序 | 线程 A | 线程 B | 共享 count |
|---|---|---|---|
| 1 | 读到 0 | — | 0 |
| 2 | — | 读到 0 | 0 |
| 3 | 根据旧值算出 1 并写回 | — | 1 |
| 4 | — | 根据旧值算出 1 并写回 | 1 |

两次加一只留下结果 1，这叫丢失更新。代码只有一行、字段类型为 int、单独的读取或写入具有某些保证，都不等于这个读改写整体具有原子性。

### 原子性、可见性与有序性：三种不同要求

原子性要求某项更新作为不可分割的整体发生。可见性要求跨线程写入能够按同步规则被另一线程观察。有序性要求需要保留的操作顺序得到相应保证。

原子计数能避免单次加法丢失，但“先检查余额，再调用一个原子扣减方法”仍可能不是整体原子操作。volatile 状态标记能提供相应可见性，但不会自动把多步更新合成一项事务。

判断并发代码时应指出需要保护的业务约束，例如“库存不能小于零”，再选择能覆盖整项约束的机制，不能只看使用了哪个带有并发字样的类。

### synchronized 与监视器：锁到底锁住什么

`synchronized` 是关键字，不是方法。下面的同步块表示线程要先获取 `monitor` 对象的监视器锁，才能执行其中的代码：

```java
synchronized (monitor) {
    count++;
}
```

同一时刻，只有一个线程持有这一个监视器锁。另一个使用同一 monitor 的线程，需要等持有者释放后才能进入。退出同步范围时，Java 自动释放相应持有，即使代码因异常离开也如此。

锁保护数据的前提是相关访问路径都遵守同一规则。若一个线程使用 monitor，另一个线程完全不加锁就修改 count，前者的锁无法阻止后者。若每次访问都新建一个 Object 作为锁，各次调用也不会相互排斥。

实例同步方法通常使用该实例的监视器，静态同步方法使用对应 Class 对象的监视器。两个不同实例不是同一把实例锁。

### 可重入：再次获取不等于再次建立一个独立锁

同一个线程已经持有某把可重入锁时，再次获取它可以成功，不必等待自己释放。内部会记录持有层数：获取两次，就需要释放两次，最终归零才让其他线程有机会获取。

因此，一个加锁方法调用同一对象的另一个加锁方法可以成立。可重入解决“同一线程再次获取”的问题，不代表不同线程能够同时持有互斥锁，也不代表无需配对释放。

### 等待、释放锁和重新获取锁是三个动作

下面两种等待虽然都可能暂停线程，但对锁的影响不同：

```java
synchronized (monitor) {
    Thread.sleep(100); // 当前线程暂停，仍持有 monitor
}
```

```java
synchronized (monitor) {
    while (!ready) {
        monitor.wait(); // 等待期间释放 monitor，恢复前重新获取
    }
}
```

第一段的其他竞争线程仍无法拿到 monitor。第二段允许准备方拿到 monitor，修改 ready 并通知等待方。wait 不会释放当前线程还持有的所有其他锁。

“被唤醒”也不是立即执行业务：如果需要重新获取的锁仍被别人持有，就要继续等待这把锁。

### 中断：请求停止与实际停止分开

线程的中断状态可以理解成一个请求标记。另一个线程调用 interrupt 提出请求，目标线程通过检查标记或某些可中断等待接口响应。普通计算不会在任意一行突然抛出 InterruptedException。

sleep 等接口可能通过抛 InterruptedException 响应该请求，并清除标记；park 等接口可能返回但保留标记；等待 synchronized 获取不因为中断就自动退出。这些差异在具体方法中逐项说明。

取消状态、线程退出和清理完成也需要分别确认。结果对象已经显示取消，不能单凭这一点推断外部请求已停止或资源已经归还。

### happens-before：为什么另一线程能够正确读取结果

happens-before 是 Java 内存模型规定的关系，用来判断跨线程操作的可见性与必要顺序。它不是“日志上较早发生”或“等待了一秒”。

几个具体入口可以先建立直觉：

- main 先设置任务参数，再调用 worker.start，新线程能按启动规则观察启动前的设置。
- worker 写出结果并结束，main 通过 join 检测其结束后，可以观察相应结果。
- 一个线程在同一把锁下修改数据并释放，另一线程随后成功获取该锁，可以按锁规则观察此前修改。
- 一个线程先准备数据再写 volatile 标记，另一线程通过相应标记读取建立同步关系后，可读取此前发布的数据。

这些规则的用途是消除“可能看到旧数据”的不确定性，但不自动保证业务所有步骤的原子性。详见 [Java 语言规范的 happens-before 规则](https://docs.oracle.com/javase/specs/jls/se21/html/jls-17.html#jls-17.4.5)。

### 线程状态：诊断名称不等于业务结论

| 状态 | 大致含义 | 常见场景 |
|---|---|---|
| `NEW` | 尚未启动 | 创建 Thread 后尚未 start |
| `RUNNABLE` | JVM 视角下可以或正在运行 | 可能正在计算，也可能等待 CPU 等 |
| `BLOCKED` | 等待对象监视器锁 | 进入别的线程持有的 synchronized |
| `WAITING` | 没有指定等待时限 | wait、join、park 的相应等待路径 |
| `TIMED_WAITING` | 有时间限制的等待 | 正时长 sleep、一些限时等待 |
| `TERMINATED` | 执行结束 | 线程入口任务结束 |

日常说“阻塞了”不一定对应 Java 枚举 BLOCKED，例如 ReentrantLock 等待常通过挂起机制表现为 WAITING。getState 只是状态快照，不能代替正确的同步协议。

### 阅读每个方法时的检查清单

重点不是一次记住所有函数名，而是能回答：谁调用，谁执行，谁等待；需要先持有什么锁；等待时释放的是哪一把锁；中断和超时改变什么；正常返回能够证明什么。

例如 join 的核心结论是“调用者等待目标结束”，而不是笼统的“阻塞线程”；tryLock 返回 true 的核心结论是“已实际获得锁”，而不是“检查到锁空闲”。后续详细章节都会落到这些具体关系。


<a id="sec-2"></a>

## 2. Thread：从执行任务到等待结束

本章以 Java 21 平台线程为基准。`new Thread(task)` 创建的是执行任务的线程对象，`task` 是任务本身；对象存在不代表任务已经开始。阅读每个方法时，应先判断它作用于“当前正在执行代码的线程”，还是变量引用的“目标线程”。

```java
worker.start();       // 启动 worker，任务由 worker 执行
worker.join();        // 当前线程等待 worker 结束
worker.interrupt();   // 向 worker 发出中断请求
Thread.sleep(100);    // 当前线程暂停 100 毫秒左右
```

`sleep()`、`interrupted()` 都是静态方法，应使用 `Thread.方法名()` 调用。线程对象写在调用表达式左侧，并不能把这两个方法的作用对象改为那个线程。

<a id="method-2-1"></a>

### 2.1 Thread.start()：让任务获得一条新的执行路径

#### 解决的问题：对象创建后，谁来执行任务

```java
Runnable task = () -> System.out.println("生成报表");
Thread worker = new Thread(task, "report-worker");
```

执行以上两行时，创建任务和线程对象的仍是当前线程。Lambda 里的打印语句并没有因此执行。若程序希望报表任务与当前线程的后续工作同时推进，就需要调用 `worker.start()`。

**`start()` 安排目标线程启动，随后由目标线程执行任务。** 它的签名是 `public void start()`，没有任务结果作为返回值，也不等待任务完成。

#### 内部执行流程：检查、启动、调度、进入任务

以下流程是平台线程的概念模型，不是 JDK 源码的逐行翻译：

```text
调用者线程执行 worker.start()
    ↓
检查 worker 是否尚未启动
    ↓
通过 JVM 完成底层线程启动管理
    ↓
worker 可以参与操作系统的线程调度
    ├─ 调用者继续执行 start() 后面的代码
    └─ worker 获得执行机会后，执行自身的 run()
```

第一步的检查防止同一个线程对象被重复启动。平台线程的启动还涉及 JVM 与操作系统的执行资源，不能把整个方法理解成简单的 `run()` 调用。新线程何时获得 CPU 时间，由调度决定；启动动作完成与任务完成是两件事情。

同一个线程对象的典型生命周期是 `NEW → 已启动并执行 → TERMINATED`。中间可能进入各种等待状态，但结束后不会回到 `NEW`。

#### 具体示例：任务与 main 的后续工作分开执行

```java
public class StartDemo {
    public static void main(String[] args) throws InterruptedException {
        Runnable reportTask = () -> {                     // ① 描述任务
            System.out.println("任务线程："
                    + Thread.currentThread().getName());
        };

        Thread worker = new Thread(reportTask, "report-worker"); // ② 创建对象
        worker.start();                                  // ③ 启动新线程
        System.out.println("main 继续处理其他工作");        // ④ main 的后续工作
        worker.join();                                   // ⑤ 等待任务线程结束
    }
}
```

① 只定义任务；② 把任务与线程对象关联起来；③ 使任务可以在 `report-worker` 中执行；④ 仍由 main 执行；⑤ 让 main 等待 worker 结束，`join()` 的机制在后文单独解释。

输出中的“任务线程：report-worker”和“main 继续处理其他工作”可以交换顺序。main 在源码中先调用 `start()`，不代表 worker 一定先完成打印。

#### 启动前的写入：为什么新线程能读取初始化数据

```java
class ReportTask implements Runnable {
    int rowCount;

    @Override
    public void run() {
        System.out.println(rowCount);
    }
}

// 以下为调用方代码。
ReportTask task = new ReportTask();
task.rowCount = 100;
Thread worker = new Thread(task);
worker.start();
```

调用方先写入 `100`，再启动 worker。在没有其他写入的前提下，worker 能够读取这个初始化值。这是线程启动建立的 happens-before 关系：启动前的操作对被启动线程可见。

若调用方改成先 `start()`，再写 `task.rowCount = 200`，就不能仅依赖启动规则确保任务读取到 `200`。启动后的共享数据读写需要自己的同步规则。这个保证不是“从此所有字段都自动线程安全”。

#### 返回与常见误区

| 情况 | 实际含义 |
|---|---|
| `start()` 正常返回 | 启动操作已完成，任务可能尚未执行，也可能已经完成 |
| 对同一对象调用第二次 `start()` | 抛出 `IllegalThreadStateException` |
| 线程结束后再次 `start()` | 仍是重复启动，不允许 |
| 任务代码抛出未处理异常 | 异常发生在任务线程中，不会作为任务异常直接从调用方的 `start()` 抛出 |

需要再次执行相同任务时，应创建新线程对象或重新向线程池提交任务。线程对象代表一次线程生命周期。有关“至多启动一次”的方法契约见 [Java 21 Thread.start()](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html#start())；启动的可见性规则见 [Java 语言规范 17.4.5](https://docs.oracle.com/javase/specs/jls/se21/html/jls-17.html#jls-17.4.5)。

<a id="method-2-2"></a>

### 2.2 Thread.run()：定义执行内容，区分普通调用与线程入口

#### 前置概念：两个 run() 分别属于谁

`Thread` 实现了 `Runnable`，因此存在两个相关角色：

| 方法 | 角色 |
|---|---|
| 线程对象的 `Thread.run()` | 平台线程执行时的入口方法，可以由子类重写 |
| 任务对象的 `Runnable.run()` | 保存实际业务代码的方法 |

例如 `new Thread(task)` 把任务交给线程对象。对于普通平台线程，默认的 `Thread.run()` 再委托给任务的 `run()`。

其概念模型如下，省略了 JDK 的辅助实现：

```text
Thread.run():
    找到创建线程时关联的 Runnable
    如果存在 Runnable:
        调用 Runnable.run()
    否则:
        默认实现不做任务工作
```

**`run()` 决定做什么；是否由新线程执行，取决于是否通过线程启动机制进入。**

#### 具体示例：相同任务，执行者不同

```java
public class RunDemo {
    public static void main(String[] args) throws InterruptedException {
        Thread worker = new Thread(() -> {
            System.out.println("执行者："
                    + Thread.currentThread().getName());
        }, "worker");

        worker.run();   // ① 普通调用，main 执行任务并等待本次调用返回
        worker.start(); // ② 启动 worker，worker 执行同一份任务
        worker.join();  // ③ main 等待 worker 结束
    }
}
```

第一条输出是 `执行者：main`，第二条是 `执行者：worker`。第一步全部执行完之后，main 才会进入第二步，因此这里两条输出的先后是确定的。

直接调用平台线程对象的 `run()` 没有启动线程，不会把对象从 `NEW` 变成“已启动”，所以之后的第一次 `start()` 仍可执行。任务却被执行了两次；这可能导致重复扣款或重复写入。业务代码通常不应把直接调用 `Thread.run()` 当作任务调度方式。

#### 重写 run() 与传入 Runnable 的关系

```java
class CustomThread extends Thread {
    CustomThread(Runnable task) {
        super(task);
    }

    @Override
    public void run() {
        System.out.println("先执行额外逻辑");
        super.run(); // 显式委托，继续执行构造时传入的 Runnable
    }
}
```

启动 `CustomThread` 后，实际进入的是重写后的 `run()`。只有重写实现调用了 `super.run()`，父类默认逻辑才会执行传入任务。若删除这一行，不能期待传入的任务自动再执行一次。

初学阶段更适合使用 `Runnable` 描述任务，使用 `Thread` 或线程池安排执行，避免把业务内容和执行设施混在一个子类中。

#### 返回值与异常：执行路径决定异常传播路径

`run()` 的返回类型是 `void`，不能直接返回计算结果，也不能在重写签名中额外声明一般受检异常。需要结果时，可以通过后文的 `Callable`、`Future` 等工具表达任务结果。

对上述平台线程直接调用 `worker.run()`，任务中的运行时异常会沿当前线程的普通调用栈传播，调用方的 `try-catch` 可以捕获。

通过 `worker.start()` 执行任务时，任务异常发生在 worker 的调用栈中。未捕获的异常进入 worker 的未捕获异常处理机制，任务线程最终结束。把 `try-catch` 写在 `worker.start()` 外层，不能捕获 worker 随后执行任务时的异常。

#### Java 21 的虚拟线程边界

“直接调用 `Thread.run()` 会由调用者执行任务”的说明限定于本节的平台线程。Java 21 对虚拟线程规定：**直接调用虚拟线程对象的 `run()` 不执行任务。** 虚拟线程应通过对应的启动机制安排任务。[Java 21 Thread.run()](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html#run())。

<a id="method-2-4"></a>

### 2.3 Thread.sleep(...)：暂停当前线程，但保留已经持有的锁

#### 解决的问题：当前执行路径需要暂时停顿

`Thread.sleep(500)` 的主要签名为：

```java
public static void sleep(long millis) throws InterruptedException
```

它让**执行这行代码的当前线程**暂时停止向下执行。若 main 调用，就暂停 main；若 worker 在任务内调用，就暂停 worker。

它常用于教学中的延迟模拟、简单重试间隔等。它没有表达“某个任务已经结束”或“某份数据已经准备好”，所以不能靠多睡一会儿建立线程间的正确执行关系。

#### 内部执行流程：定时等待与重新调度

```text
当前线程调用 sleep(500)
    ↓
检查时间参数与中断状态
    ↓
通过底层定时等待机制暂停当前线程
    ↓
等待时间到达，或等待响应中断
    ↓
线程重新获得执行机会
    ↓
正常继续下一行，或抛出 InterruptedException
```

对正时长的平台线程休眠，Java 线程状态通常为 `TIMED_WAITING`。暂停期间线程不需要靠不停执行循环来消耗 CPU。到达期望时长后，还要等待调度机会；`500` 毫秒不是保证在第 `500` 毫秒准时执行下一行的预约。

#### 具体示例：谁调用，谁暂停

```java
public class SleepDemo {
    public static void main(String[] args) throws InterruptedException {
        Thread worker = new Thread(() -> {
            try {
                System.out.println("worker 开始休眠");
                Thread.sleep(300); // worker 暂停，main 不会因此暂停
                System.out.println("worker 恢复执行");
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                System.out.println("worker 提前结束等待");
            }
        }, "worker");

        worker.start();
        System.out.println("main 可以继续执行");
        worker.join();
    }
}
```

worker 开始休眠以后，main 仍然可以执行。main 最后停在 `join()`，原因是主动等待 worker 结束，不能把这次等待归因于 worker 的 `sleep()`。

Java 语法允许通过对象表达式调用静态方法，例如 `worker.sleep(300)`，但它仍暂停执行调用的当前线程。这种写法容易误导阅读者，应始终写成 `Thread.sleep(300)`。

#### 为什么不释放锁：暂停不是退出同步块

```java
// 以下两段分别由线程 A 和线程 B 执行，monitor 是同一个对象。
// 线程 A
synchronized (monitor) {
    System.out.println("A 获得锁");
    Thread.sleep(1000);
    System.out.println("A 即将退出同步块");
}

// 线程 B
synchronized (monitor) {
    System.out.println("B 获得锁");
}
```

在 A 先获得锁的情况下，执行时序为：

1. A 获取 `monitor` 的监视器锁，进入同步块。
2. A 休眠一秒左右，但还没有退出同步块，仍是锁的持有者。
3. B 尝试进入同步块，因为锁仍由 A 持有，只能等待获取锁。
4. A 恢复并退出同步块，锁才释放。
5. B 随后才有机会获取锁并打印。

`sleep()` 同样不会释放已经获取的 `ReentrantLock`。需要“等待条件时让其他线程进入临界区修改条件”，应使用后文的 `wait()` 或 `Condition.await()`，它们具有对应锁的释放机制。

#### 中断：从休眠转为异常路径

若线程进入休眠时已带有中断标记，或者休眠期间被中断，`sleep()` 会响应中断，抛出 `InterruptedException` 并清除当前线程的中断标记。

```java
try {
    Thread.sleep(1000);
} catch (InterruptedException e) {
    // 抛出该异常时，中断标记已经清除。
    Thread.currentThread().interrupt(); // 如需向上保留取消信号，则恢复标记
    return;                            // 同时结束当前任务或方法
}
```

若异常从同步块中向外传播，锁的释放发生在离开同步块时；不是 `sleep()` 主动释放了锁。若在同步块内部捕获异常并继续执行，线程仍持有监视器锁。

#### 返回、时间参数与边界

正常返回表示本次休眠已结束，不表示其他线程做完任何工作。负毫秒值抛出 `IllegalArgumentException`；`sleep(0)` 也不能作为可靠的让步或同步协议。

延长休眠没有内存可见性保证：一个普通共享字段不会因为读取方“睡得足够久”就变成安全发布。应通过锁、`volatile`、任务等待工具等建立规定的同步关系。

<a id="method-2-5"></a>

### 2.4 Thread.join()：当前线程等待目标线程结束

#### 解决的问题：后续工作需要前一个线程的最终结果

若 worker 计算总数，main 需要等结果完成后才能打印，可以让 main 调用 `worker.join()`。

**方法左侧是被等待的目标线程，发生等待的是调用者线程。** worker 不会因为 main 调用 `join()` 而暂停，它应继续执行，最终结束，以便 main 的等待完成。

```java
public final void join() throws InterruptedException
```

#### 具体示例：计算完成后才读取结果

```java
public class JoinDemo {
    static class Result {
        int total;
    }

    public static void main(String[] args) throws InterruptedException {
        Result result = new Result();
        Thread worker = new Thread(() -> {
            int sum = 0;
            for (int n = 1; n <= 100; n++) {
                sum += n;
            }
            result.total = sum; // ① worker 写入最终结果
        }, "calculator");

        worker.start();
        worker.join();         // ② main 等待 worker 终止
        System.out.println(result.total); // ③ 输出 5050
    }
}
```

在该例中，worker 结束后不再修改 `total`，main 在成功检测其终止后才读取。线程终止与 `join()` 的成功检测建立可见性关系，所以这次结果读取不需要额外把 `total` 声明为 `volatile`。

删除 `join()` 后，main 可能在 worker 写入之前读到默认值 `0`，而且代码失去了该等待建立的可见性保证。增加 `Thread.sleep(1000)` 不能正确替代 `join()`。

#### 内部执行流程：循环确认目标是否仍在运行

```text
main 调用 worker.join()
    ↓
worker 若尚在运行，main 等待其终止
    ↓
worker 执行任务并结束
    ↓
main 被安排继续执行，确认目标已终止
    ↓
join() 正常返回，main 执行下一行
```

OpenJDK 21 的平台目标线程等待路径使用 `Thread` 对象自身的监视器。概念模型可以写成：

```text
取得 worker 对象的内部监视器
while worker 仍存活:
    在 worker 上等待，等待期间释放该监视器
确认目标已结束后返回
```

目标线程终止时，底层机制会通知等待者。循环检查用于防止一次唤醒被误认为目标已结束。虚拟目标线程使用不同的终止等待机制，不能把“内部一定是 `wait()`”推广到所有线程。

该监视器属于线程等待的内部实现。业务代码应使用独立对象作为业务锁，不应在 `Thread` 实例上自行建立 `wait/notify` 协议。[Java 21 join 的实现说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html#join(long))。

#### 关键边界：join() 不释放调用方的业务锁

```java
// 错误示例：不要运行这个无超时等待结构。
synchronized (businessLock) {
    worker.start();
    worker.join();
}
```

若 worker 的任务也必须获取 `businessLock` 才能完成，就形成循环等待：

```text
main 持有 businessLock → 等待 worker 结束
worker 等待 businessLock → 无法完成任务
```

平台线程 `join()` 的内部等待可能释放的是 `worker` 对象监视器，不是 `businessLock`。原则上，应在不持有 worker 所需业务锁的位置等待线程结束。

#### 中断与返回：等待结束不等于任务成功

main 等待时，若其他线程中断 main，main 的 `join()` 可抛出 `InterruptedException`，并清除 main 的中断标记。worker 不会因此自动被中断或停止。若需要取消 worker，调用方必须另外采取取消动作。

正常结束后 `join()` 返回 `void`。它不返回业务结果，也不会把 worker 的未捕获异常重新抛到 main。异常失败的线程同样会结束，因此“线程结束”不能直接等同于“计算成功”。

#### 与带超时版本的配套使用

```java
worker.join(1000);
if (worker.isAlive()) {
    System.out.println("等待已结束，worker 仍在执行");
} else {
    System.out.println("已检测到 worker 结束");
}
```

`join(1000)` 结束等待后不会停止 worker，也不直接返回是否终止。`join(0)` 表示无超时等待。无参或毫秒版本对尚未启动的线程会立即返回，不能等待未来的一次启动；对当前线程自身执行无超时 `join()`，则会陷入等待自身结束的错误逻辑。

Java 21 的 `join(Duration)` 使用不同的返回契约：返回是否终止，并对尚未启动的目标抛出 `IllegalThreadStateException`。查询时应明确所用重载。

<a id="method-2-7"></a>

### 2.5 Thread.interrupt()：发出取消信号，由任务配合处理

#### 前置概念：中断标记是一种状态，不是强制终止命令

每个线程具有中断状态，可先把它理解成与线程关联的布尔标记：未请求中断时为 `false`，收到请求后通常为 `true`。

调用 `worker.interrupt()` 的是请求方，收到请求的是 worker。该方法通常做两类工作：设置目标线程的中断状态；若目标正处在支持中断的等待中，触发该等待机制的响应。

**普通计算不会被自动截断，任务必须在合适位置检查信号，或通过可中断等待响应信号。**

#### 内部流程：先看目标当前在做什么

```text
调用方执行 worker.interrupt()
    ↓
向 worker 提交中断请求
    ├─ worker 在普通计算：记录标记，计算代码需要主动检查
    ├─ worker 在 sleep/wait/join：相关等待响应并走异常路径
    ├─ worker 在可中断加锁：允许放弃这次等待
    └─ worker 在其他阻塞：按具体阻塞工具的契约处理
```

方法没有“立即结束任意代码”的能力，也没有直接释放 worker 所持业务锁的能力。可控退出依赖任务自己的代码结构：检查取消、停止后续工作、执行清理、从任务入口返回。

#### 示例一：计算循环主动检查中断

```java
public class InterruptComputeDemo {
    public static void main(String[] args) throws InterruptedException {
        Thread worker = new Thread(() -> {
            long batches = 0;
            try {
                while (!Thread.currentThread().isInterrupted()) {
                    // 模拟一批有边界的小任务，每批结束后重新检查取消信号。
                    for (int n = 0; n < 10_000; n++) {
                        batches += n;
                    }
                }
            } finally {
                System.out.println("任务退出并执行清理，累计值：" + batches);
            }
        }, "compute-worker");

        worker.start();
        worker.interrupt(); // 请求取消；worker 可能还没有执行第一批
        worker.join();      // 确认 worker 最终结束
    }
}
```

worker 在每轮循环开头检查自己的标记。请求到达后，至多还会执行当前尚未结束的一批，然后在下一次检查时退出循环。实际响应延迟取决于每批工作的长度。

`interrupt()` 与 `join()` 的作用不同：前者发请求，后者等待退出结果。代码不检查中断且没有可中断等待时，只有前者不能保证任务结束。

#### 示例二：阻塞任务通过异常响应中断

```java
public class InterruptSleepDemo {
    public static void main(String[] args) throws InterruptedException {
        Thread worker = new Thread(() -> {
            try {
                while (!Thread.currentThread().isInterrupted()) {
                    System.out.println("执行一次检查");
                    Thread.sleep(1000); // 取消请求可以结束这次等待
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt(); // 保留取消状态
            } finally {
                System.out.println("关闭任务资源");
            }
        }, "checking-worker");

        worker.start();
        worker.interrupt();
        worker.join();
    }
}
```

存在两种正常时序。请求先于循环检查到达时，worker 直接跳过循环并清理；请求在 `sleep()` 等待期间到达时，休眠抛出异常，进入 `catch`，再执行清理。若请求恰好在循环检查之后、休眠之前到达，`sleep()` 会检查到已有标记并进入同样的异常处理路径。

该示例不依赖“先睡几十毫秒，确保 worker 进入休眠”的猜测，因此能够覆盖启动与取消之间的时序变化。

#### 为什么异常处理里经常恢复中断标记

`sleep()`、`wait()` 等在以 `InterruptedException` 报告中断时，会清除标记。异常已经把取消信息交给当前这一层代码，但外层若只查询标记，就无法再看到原来的信号。

处理策略应与方法职责匹配：

| 当前方法的职责 | 常见处理方式 |
|---|---|
| 可以声明并传播 `InterruptedException` | 向调用方传播，让上层决定取消策略 |
| 无法传播受检异常，例如 `Runnable.run()` | 恢复当前线程标记，并结束或转交后续处理 |
| 当前层明确负责完成取消 | 执行清理和退出，按契约决定是否保留标记 |

`Thread.currentThread().interrupt()` 恢复的是当前执行线程的标记，并不是再次强制停止任务。若恢复后又无条件调用 `sleep()`，下一次等待会立即响应中断；恢复信号必须和明确的控制流配合。

#### 不同等待方式的响应不相同

| 目标线程当前行为 | 中断的典型响应 |
|---|---|
| 普通计算 | 设置标记，任务自行检查 |
| `sleep()`、`Object.wait()`、等待中的 `join()` | 抛 `InterruptedException`，报告该异常时清除标记 |
| 等待进入 `synchronized` | 不因此放弃获取监视器，标记可以保留 |
| 等待 `ReentrantLock.lock()` | 不因中断退出加锁，继续等待获取锁 |
| `lockInterruptibly()`、带时间的 `tryLock()` | 可以放弃等待，以 `InterruptedException` 报告 |
| `LockSupport.park()` | 可以返回，不抛上述异常，也不自动清除标记 |
| 具体 I/O 操作 | 必须查看该 API 的取消契约，不能统一理解成抛 `InterruptedException` |

例如可中断通道与普通文件或网络 API 可能具有不同响应。对尚未存活或已经结束的线程中断，也不能建立通用的任务取消协议。[Java 21 Thread.interrupt()](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html#interrupt())。

<a id="method-2-8"></a>

### 2.6 Thread.isInterrupted()：观察目标线程的中断标记

#### 与 interrupt() 的分工

`interrupt()` 发请求，`isInterrupted()` 查询请求状态。后者是实例方法：

```java
public boolean isInterrupted()
```

`worker.isInterrupted()` 查询 worker；`Thread.currentThread().isInterrupted()` 查询当前执行者。方法立即返回布尔值，不等待，不清除标记。

它的概念模型只有一个核心动作：读取目标线程当前的中断状态。它不会自动退出循环、停止任务或关闭资源；这些动作需要调用方根据返回值决定。

#### 具体示例：重复读取不会消耗信号

```java
public class IsInterruptedDemo {
    public static void main(String[] args) throws InterruptedException {
        Thread worker = new Thread(() -> {
            Thread.currentThread().interrupt(); // worker 给自身设置标记

            boolean first = Thread.currentThread().isInterrupted();
            boolean second = Thread.currentThread().isInterrupted();

            System.out.println(first);  // true
            System.out.println(second); // true
            // run() 接下来正常结束，标记本身没有强制终止代码。
        }, "worker");

        worker.start();
        worker.join();
    }
}
```

两次查询之间没有清除操作，所以都返回 `true`。例子把查询放在 worker 的任务里，不依赖在线程结束后查询其状态的实现细节。

#### 为什么适合计算循环

```java
while (!Thread.currentThread().isInterrupted()) {
    processOneBatch(); // 假设每批能在合理时间内结束
}
```

循环只观察信号，不把它清掉。内层代码和外层清理逻辑仍有机会识别相同请求。若 `processOneBatch()` 无限制地阻塞，又没有中断支持，循环条件再正确也无法及时取消；检查点的间隔与每批工作的行为同样重要。

#### 不能从标记推导出的结论

| 返回值 | 能确认什么 | 不能确认什么 |
|---|---|---|
| `true` | 此次查询观察到目标中断标记已设置 | 任务已停止、资源已关闭、线程已结束 |
| `false` | 此次查询未观察到已设置标记 | 从未有人请求中断；之前的请求可能已被等待方法或其他代码消费 |

main 查询 worker 的标记只是状态快照。需要确认退出，应等待终止，例如 `join()`；需要了解任务成功、失败、取消的结果，应使用明确的任务结果协议。

<a id="method-2-9"></a>

### 2.7 Thread.interrupted()：读取并清除当前线程的中断标记

#### 解决的问题：显式消费当前线程的中断状态

```java
public static boolean interrupted()
```

该方法把“读取当前线程中断状态”和“清除这个状态”结合起来。它返回清除前的值；无论当前线程原来是否中断，调用后本次已观察到的标记被清除。新的中断请求仍可在之后到达。

概念模型为：

```text
读取当前线程的中断标记
清除已读取的标记
返回原来读取到的值
```

这里的“当前线程”由实际执行者决定。即使写成 `worker.interrupted()`，它仍是静态方法，读取和清除的仍是调用者线程。正确写法是 `Thread.interrupted()`。

#### 具体示例：为什么连续查询的结果不同

```java
public class InterruptedDemo {
    public static void main(String[] args) {
        Thread.currentThread().interrupt(); // 给 main 设置标记

        System.out.println(Thread.currentThread().isInterrupted()); // true，保留
        System.out.println(Thread.interrupted());                   // true，清除
        System.out.println(Thread.currentThread().isInterrupted()); // false
        System.out.println(Thread.interrupted());                   // false
    }
}
```

状态变化为：`false → interrupt() 设置 true → isInterrupted() 保持 true → interrupted() 清为 false`。若两次查询之间另有一次中断，第二次 `interrupted()` 也可能返回 `true`；该示例没有额外请求，所以输出确定。

#### 为什么不能随意用来打印日志

```java
// 有问题：记录状态的同时把取消信号清除了。
System.out.println("是否中断：" + Thread.interrupted());
continueWork();
```

若 `continueWork()` 依赖 `isInterrupted()` 决定退出，日志查询已经清除信号，后续代码可能继续工作。只观察状态，应使用 `Thread.currentThread().isInterrupted()`。

底层实现可以在明确负责处理中断时消费这个信号。例如可中断等待的入口，需要发现并报告已有中断；概念逻辑可能是：

```java
if (Thread.interrupted()) {
    throw new InterruptedException();
}
```

这是教学模型，不表示所有 JDK 方法都通过这段 Java 代码实现。调用方看到异常时应处理该中断信息；不能只因标记现在是 `false` 就判断从未中断。

#### 三个方法的查询口诀与使用选择

| 方法 | 作用对象 | 是否修改标记 | 常见用途 |
|---|---|---|---|
| `worker.interrupt()` | worker | 发出中断请求 | 请求任务取消 |
| `worker.isInterrupted()` | worker | 不清除 | 观察目标状态 |
| `Thread.interrupted()` | 当前线程 | 读取并清除 | 明确消费中断状态 |

业务任务的循环通常使用当前线程的 `isInterrupted()`。`interrupted()` 适合明确需要消费状态的控制逻辑；选择它时，代码必须说明消费后如何报告、退出或继续。

<a id="sec-3"></a>

## 3. Object：等待业务条件与通知条件变化

线程结束可以通过 `join()` 等待，但更多业务问题需要等待一个条件，例如“数据已准备”“队列已经有元素”。这时生产数据的线程可能还要继续工作，并不会结束，因此需要条件等待。

本章的方法属于 `Object`，与对象的监视器配合使用。对象的 Java 字段与它的监视器是不同概念：字段保存业务数据，监视器管理互斥进入和等待通知。

```text
同一个对象的监视器，可先按三个概念理解：
    当前持有锁的线程：能进入受保护的临界区
    等待获得锁的线程：想进入临界区，但锁暂时被占用
    等待条件的线程：调用 wait()，释放锁并等待通知等事件
```

“等锁”和“等条件”不能混为一件事情。等条件的线程必须先取得锁才能检查条件，发现条件不满足后，再释放锁去等；条件改变后，仍需重新取得锁才能安全读取数据。

<a id="method-3-1"></a>

### 3.1 Object.wait()：条件不满足时，释放对应监视器并等待

#### 为什么需要 wait()：持锁睡眠会阻碍条件改变

假设读取方在等待数据：

```java
// 错误思路，monitor 和 ready 由双方共享。
synchronized (monitor) {
    while (!ready) {
        Thread.sleep(100);
    }
}
```

若准备方也必须取得 `monitor` 才能把 `ready` 改成 `true`，读取方始终持有锁，就阻止了准备方进入。即使每次只睡 100 毫秒，循环还在同步块中，锁不会释放。

`wait()` 用于解决这种等待：**当前线程暂时放下被调用对象的监视器锁，让其他线程能够进入并改变条件，随后等待重新检查条件的机会。**

#### 调用前必须明确三件事

1. 锁对象是谁，例如 `monitor`。
2. 等待的业务条件是什么，例如 `ready == true`。
3. 哪条路径负责改变条件并通知，例如 `publish()`。

主要签名为：

```java
public final void wait() throws InterruptedException
```

当前线程必须已经持有被调用对象的监视器，否则抛出 `IllegalMonitorStateException`。`synchronized (a)` 中调用 `b.wait()`，若没有同时持有 b 的监视器，同样不合法。持有一把任意锁，不代表满足这个前置条件。

#### 具体示例：读取方等待一个计算结果

```java
class OneTimeResult {
    private final Object monitor = new Object();
    private boolean ready;
    private int value;

    int awaitValue() throws InterruptedException {
        synchronized (monitor) {           // ① 先获得锁
            while (!ready) {               // ② 持锁检查条件
                monitor.wait();            // ③ 条件不满足，释放这把锁并等待
            }
            return value;                  // ④ 条件满足时，仍持锁读取
        }                                  // ⑤ 离开同步块，释放锁
    }

    void publish(int calculatedValue) {
        synchronized (monitor) {           // ⑥ 修改方也使用同一把锁
            if (ready) {
                throw new IllegalStateException("结果只能发布一次");
            }
            value = calculatedValue;
            ready = true;                  // ⑦ 先保存数据并改变业务条件
            monitor.notifyAll();           // ⑧ 再通知等待方重新检查
        }
    }
}
```

两个线程可以分别执行 `result.awaitValue()` 与 `result.publish(42)`。`ready` 和 `value` 的相关读写都发生在同一个监视器保护下，因此本例不需要给它们额外加 `volatile`。

`wait()` 返回 `void`，不会直接返回 `42`。它只管理等待；结果来自等待结束后对业务字段 `value` 的读取。

#### 内部执行流程：释放锁和重获锁是成对的

读取方先到达且结果尚未准备时，可能发生以下时序：

| 步骤 | 读取线程 A | 准备线程 B | monitor 的持有者 |
|---|---|---|---|
| 1 | 获取锁，发现 `ready == false` | 尚未进入 | A |
| 2 | 调用 `wait()`，进入等待集合并释放锁 | 可以尝试进入 | 无 |
| 3 | 等待条件 | 获取锁，写入 `value` 和 `ready` | B |
| 4 | 收到通知，等待重新获得锁 | 调用 `notifyAll()`，仍在同步块内 | B |
| 5 | 继续竞争锁 | 退出同步块，释放锁 | 暂时无 |
| 6 | 重获锁，再次检查 `ready`，读取结果 | 可以继续其他工作 | A |

正常进入等待时，等待登记与对应锁的释放形成协调步骤，不会让准备方在“读取方检查完条件，但还没有登记等待”的缝隙中任意穿入。正确代码必须把条件检查和 `wait()` 都放在同一个同步块里。

若准备方先完成，读取方后来取得锁，直接看到 `ready == true`，就不会调用 `wait()`。程序依赖持久保存的条件，而不是要求每个线程必须收一次通知。

#### 为什么必须用 while，不能只用 if

```java
// 标准模式。
synchronized (monitor) {
    while (!ready) {
        monitor.wait();
    }
    // 此处 ready 已在持锁状态下重新检查。
}
```

唤醒只说明“可以重新尝试”，不保证条件成立。至少有三类原因需要重查：

- 存在虚假唤醒：没有所期待的通知，等待也可能返回。
- 一次通知可能对应多个等待者，各自等待的条件未必相同。
- 可消耗资源可能已经被先获得锁的线程取走。例如队列中刚放入一个元素，两名消费者都被唤醒，第一名取走后第二名仍需继续等待。

`if` 只检查一次，等待返回后直接执行后续逻辑；`while` 在每次返回后重查条件，才能确保继续执行的前提仍然成立。

#### 释放的是哪把锁：包括重入层数，但不包括其他锁

同一线程可以多次进入同一对象的 `synchronized`。`wait()` 真正进入等待时，会释放该对象监视器的全部重入持有；重新获得它后，再恢复原来的持有层数。

它不会释放线程持有的其他对象监视器，也不会释放其他 `ReentrantLock`。若 A 在持有 `outerLock` 时等待 `monitor`，而 B 需要 `outerLock` 才能改变条件，仍然可能死锁。需要审查完整的持锁关系，不能只看调用 `wait()` 的那一行。

#### 中断、超时与返回的含义

无参 `wait()` 不设置时间上限。它可以因通知、虚假唤醒或中断结束等待。正常返回前必须重新获得对应监视器；等待期间被中断时，也需要重新获得监视器，再抛出 `InterruptedException` 并清除标记。因此，别的线程仍占着锁时，中断请求不会保证等待方立即进入异常处理代码。

若在进入实际等待前已经存在中断状态，可以直接通过异常报告，不必先完成一次释放与等待过程。

带时间版本增加了超时返回条件，但超时后仍必须重新获得监视器。`wait(0)` 表示没有超时；每次唤醒后重新等待完整时长，可能使整体等待不断延长，整体时限应单独计算剩余预算。

有关监视器所有权、虚假唤醒和重获锁的规定见 [Java 21 Object.wait()](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Object.html#wait())。

<a id="method-3-4"></a>

### 3.2 Object.notifyAll()：通知同一对象上的所有等待线程重新检查条件

#### 解决的问题：条件改变后，等待方需要继续尝试

准备方修改了业务数据后，可以在持有同一监视器时调用：

```java
monitor.notifyAll();
```

主要签名是 `public final void notifyAll()`。它通知的是在 **monitor 对象上调用 `wait()`** 的等待者，不是程序中的所有线程，也不是所有正在等锁的线程。

调用方必须持有 monitor 的监视器，否则抛出 `IllegalMonitorStateException`。通知方法返回 `void`，不报告某个等待者是否已经运行或处理完数据。

#### 内部流程：从等通知变成等锁

```text
修改方持有 monitor，更新业务状态
    ↓
调用 notifyAll()
    ↓
该对象上的等待者获得重新争取 monitor 的机会
    ↓
修改方仍持有 monitor，继续执行同步块内剩余代码
    ↓
修改方退出同步块并释放 monitor
    ↓
等待者逐个有机会重获锁，重新检查各自条件
```

**通知不会立即释放锁，也不会让全部等待者同时拥有锁。** 若修改方调用后还在同步块内执行耗时计算，被通知的线程只能等待它退出。

#### 具体示例：一个格子的消息箱

下面的消息箱最多保存一条消息。发送方等“格子为空”，接收方等“格子有消息”，两种条件共用同一个监视器。

```java
class OneSlotMailbox {
    private final Object monitor = new Object();
    private String message; // null 表示空格子

    void put(String newMessage) throws InterruptedException {
        if (newMessage == null) {
            throw new IllegalArgumentException("消息不能为 null");
        }
        synchronized (monitor) {
            while (message != null) { // ① 格子有消息，发送方不能覆盖
                monitor.wait();
            }
            message = newMessage;     // ② 格子从空变为非空
            monitor.notifyAll();      // ③ 通知所有等待者重新检查
        }
    }

    String take() throws InterruptedException {
        synchronized (monitor) {
            while (message == null) { // ④ 空格子，接收方需要等待
                monitor.wait();
            }
            String result = message;
            message = null;           // ⑤ 消费后，格子重新为空
            monitor.notifyAll();      // ⑥ 通知等待中的发送方等线程
            return result;
        }
    }
}
```

发送方成功写入一条消息后，接收方的条件可能满足；接收方取走消息后，发送方的条件可能满足。每条改变条件的路径都通知，且接收和发送都在循环里检查自己的条件。

若两个接收方同时被通知，它们仍需逐个取得锁。第一个取走消息后，第二个发现格子再次为空，会继续等待，不会错误地返回 `null`。

#### 为什么多个条件混用时常选择 notifyAll()

`notify()` 只通知一个等待者，但不能指定它是发送方还是接收方。多个条件共用一个等待集合时，可能选到仍不能继续的一方，让真正可以继续的一方继续留在等待集合中。如果后续没有新的条件变化和通知，程序就可能停住。

`notifyAll()` 使所有等待者都有机会重新判断，通常更容易保证需要继续的一方获得机会。代价是更多线程竞争同一把锁，部分线程发现条件不满足后又回到等待。

当不同条件需要大量协作时，后文的 `Condition` 可以把等待集合拆分为“非空”和“未满”等不同队列，进行更有针对性的通知。

#### 通知不是可以保存的消息

没有等待者时，一次 `notifyAll()` 不会存成“下一位调用 `wait()` 的线程可用的通知”。例如：

```java
// 错误思路：只发通知，却没有保存可检查的业务状态。
synchronized (monitor) {
    monitor.notifyAll();
}
```

之后另一个线程无条件调用 `wait()`，仍可能一直等待。正确程序把“已准备好”保存在 `ready`、消息槽、队列等状态里，再通知线程重新检查它。

可见性也依赖同步规则：修改方在锁内写入，再释放锁；读取方重新获得同一把锁，然后读取。不能把 `notifyAll()` 理解成独立的“刷新所有变量”操作。

#### 返回后的边界

方法正常返回，只能确认本次通知动作已经完成，不能确认等待者已经执行。没有公平顺序保证：等待集合里的顺序、被调度的顺序和最终获取锁的顺序不是同一个概念。

通知不会自动清除等待者的中断状态，也不会取代等待方自己的中断处理。等待者被中断和被通知的时机可能接近，业务代码必须允许正常重查条件或通过异常退出两条路径。

<a id="sec-4"></a>

## 4. Lock 与 Condition：显式控制获取锁与条件等待

`synchronized` 进入和退出时由语言机制管理锁。`ReentrantLock` 则把这些动作变成明确的方法调用，允许程序选择普通获取、限时获取或可中断获取。

本章讨论 `ReentrantLock` 及其创建的 `Condition`，不把某一种实现的全部行为推广到所有 `Lock` 或 `Condition` 实现。

```text
ReentrantLock：控制谁能够进入临界区
    ├─ lock()：等待直到获得锁
    ├─ tryLock(时间, 单位)：限制本次获取锁的等待预算
    └─ lockInterruptibly()：允许取消本次获取锁的等待

Condition：在同一把锁下等待某个业务条件
    ├─ await()：释放关联锁，等待后重新获取
    └─ signal()/signalAll()：通知条件等待者重新争取锁
```

对同一个 `ReentrantLock` 对象执行 `synchronized (lock)`，获取的是对象监视器；调用 `lock.lock()`，获取的是这个显式锁所管理的锁。**两者不是同一套互斥机制**，不能混合使用来保护同一份数据。

<a id="method-4-1"></a>

### 4.1 ReentrantLock.lock()：获得互斥访问资格

#### 解决的问题：检查与修改必须作为一个整体

库存还剩一件，两个线程同时执行：

```java
if (remaining > 0) {
    remaining--;
    return true;
}
```

没有同步时，两个线程可能都先读到 `remaining == 1`，然后都认为自己预订成功。即使字段有可见性，也不能保证“检查并扣减”这个组合只有一名线程执行。

`lock()` 获取互斥锁，正常返回后，当前线程成为持有者，才能进入保护的临界区。其他遵循同一把锁规则的线程需要等待。

#### 前置概念：持有者、重入次数、等待者

初学阶段可以把 `ReentrantLock` 的状态理解成三部分：

| 状态信息 | 用途 |
|---|---|
| 持有者 | 记录当前哪条线程拥有锁 |
| 重入次数 | 记录该持有者成功获取了几次、尚未释放几次 |
| 等待获取锁的线程 | 记录竞争失败后仍希望获得锁的线程 |

“可重入”表示同一线程已经持有这把锁时，可以再次获取它，不会把自己当成另一名竞争者而卡住。但是，每次成功获取都要有一次对应的释放。

#### 内部执行流程：获取失败后排队，不是一直忙循环

```text
当前线程调用 lock()
    ├─ 锁空闲：尝试以原子方式占有，成功后成为持有者
    ├─ 锁由当前线程持有：增加重入次数，立即返回
    └─ 锁由其他线程持有：登记等待，必要时挂起
                              ↓
                       收到继续竞争的机会
                              ↓
                       再次检查并尝试获得锁
```

“原子方式占有”用于防止两个线程同时认领一把空闲锁。竞争失败以后，内部还要处理等待登记、线程挂起、唤醒和重试，所以整个 `lock()` 不能概括为“一条 CAS 指令”。

OpenJDK 使用 AQS 组织这类同步状态和等待管理。理解 AQS 前，应先掌握这里的持有者和等待关系，API 使用并不需要先记住底层节点字段。

#### 具体示例：安全地预订最后一件库存

```java
import java.util.concurrent.locks.ReentrantLock;

class LockedInventory {
    private final ReentrantLock lock = new ReentrantLock(); // ① 所有访问共用
    private int remaining = 1;

    boolean reserveOne() {
        lock.lock();                          // ② 获得互斥访问资格
        try {
            if (remaining == 0) {             // ③ 检查与扣减在同一临界区
                return false;
            }
            remaining--;
            return true;
        } finally {
            lock.unlock();                    // ④ 无论哪条返回路径都释放
        }
    }

    int remaining() {
        lock.lock();                          // ⑤ 读取也遵守同一套锁规则
        try {
            return remaining;
        } finally {
            lock.unlock();
        }
    }
}
```

若 A 先获得锁，A 看到库存为一，扣减为零，返回 `true`。B 等到 A 释放后获得锁，看到库存为零，返回 `false`。任意哪个线程先获得都可以，但不会两名线程都成功预订唯一的一件库存。

① 必须是同一个共享锁对象。若在 `reserveOne()` 方法里每次 `new ReentrantLock()`，不同调用得到不同锁，互斥就失效。④ 放在 `finally` 中，即使 `try` 中执行 `return` 或抛出异常，释放也会发生。

#### 返回、锁与中断

`lock()` 返回 `void`。正常返回本身表示此次获取成功；获取不到时，它可以一直等待，没有内置超时。

该方法不是可中断获取方法。等待期间收到中断，不会以 `InterruptedException` 结束此次获取；线程仍继续获取锁。若任务要求等待期间可取消，应选择 `lockInterruptibly()` 或带时间的 `tryLock()`。

获得锁后中断状态仍应由任务按自身契约处理。某个获取锁方法支持中断，也不意味着获得锁后的每一行业务代码都能自动响应中断。

#### 公平与非公平：谁有机会先获得锁

`new ReentrantLock()` 默认采用非公平策略，新到达的线程可能先于某些已等待线程获得锁。`new ReentrantLock(true)` 使用公平策略，更重视较早等待者。

公平锁不保证操作系统公平调度，不保证每条线程严格轮流执行，也不一定性能更好。初学阶段应先写对锁的范围、共享对象和释放路径，再决定是否需要公平策略。[Java 21 ReentrantLock](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/ReentrantLock.html)。

<a id="method-4-2"></a>

### 4.2 ReentrantLock.unlock()：减少持有次数，最后一次才真正释放

#### 为什么需要显式释放

`ReentrantLock` 不会因为方法返回就自动释放，也不会仅因线程入口结束而替业务代码归还锁。获取之后忘记 `unlock()`，其他线程可能一直无法进入。

与 `synchronized` 的语言级自动退出不同，显式锁需要程序把释放安排到每条正常和异常路径。最常见的正确结构是：

```java
lock.lock();
try {
    // 仅在成功获取锁后进入业务代码。
} finally {
    lock.unlock();
}
```

#### 内部执行流程：先验证所有权，再更新计数

```text
当前线程调用 unlock()
    ↓
确认当前线程是这把锁的持有者
    ↓
把重入次数减一
    ├─ 仍大于零：当前线程继续持有锁
    └─ 减为零：完全释放，允许等待者重新竞争
```

其他线程不能替持有者释放 `ReentrantLock`。未持有就调用，或多释放一次，会抛出 `IllegalMonitorStateException`。这个限制也是它与“任何线程都可以归还许可”的 `Semaphore` 的重要区别。

#### 具体示例：获取两次与释放两次

```java
import java.util.concurrent.locks.ReentrantLock;

public class ReentrantUnlockDemo {
    public static void main(String[] args) {
        ReentrantLock lock = new ReentrantLock();
        lock.lock();                               // ① 持有次数：1
        try {
            lock.lock();                           // ② 同一线程重入，次数：2
            try {
                System.out.println(lock.getHoldCount()); // 2
            } finally {
                lock.unlock();                     // ③ 次数降为 1，尚未完全释放
            }
            System.out.println(lock.getHoldCount());     // 1
        } finally {
            lock.unlock();                         // ④ 次数降为 0，完全释放
        }
        System.out.println(lock.getHoldCount());           // 0
    }
}
```

实际程序常发生在“外层方法获取锁，调用的内层方法又获取同一锁”的结构中。重入使调用能够继续，但内层的一次释放只对应内层的一次获取，不替外层释放。

#### 释放后，等待线程是否马上执行

不保证。完全释放使锁可以被获取，并让等待者获得继续竞争的机会；等待者仍需获得调度、检查状态、成功认领锁。

方法返回时，其他线程可能已经获得锁，也可能还没有运行。不能根据 `unlock()` 返回推断某个指定线程已经完成下一步工作。

#### 可见性与常见错误

对同一把锁的释放，以及其他线程之后的成功获取，建立相应的内存可见性关系。持锁写入的结果，能够由后续持有同一锁的线程可靠读取。若读取方不获取这把锁，就不能借用这个保证。

最常见的错误是把释放写在正常业务代码最后一行：前面一旦异常，释放就被跳过。另一个错误是 `tryLock()` 返回 `false` 后仍执行 `unlock()`；这次没有成功获取，就不能归还一层不存在的持有。

<a id="method-4-4"></a>

### 4.3 ReentrantLock.tryLock(timeout, unit)：为获取锁设置等待预算

#### 解决的问题：锁忙时不能无限等待

后台更新可以长期排队，但请求处理可能只能容忍等待 200 毫秒。带时间的 `tryLock()` 允许设置本次获取锁的最大等待预算。

```java
public boolean tryLock(long timeout, TimeUnit unit)
        throws InterruptedException
```

它与无参 `tryLock()` 不同：无参版本只立即尝试；带时间版本可以进入等待，即使方法名含有“try”。

#### 内部执行流程：成功、超时或中断

```text
检查已有中断状态
    ↓
尝试获取锁（包括当前持有者的重入情况）
    ├─ 成功：返回 true
    └─ 暂时失败：按剩余时间等待、被唤醒后重试
                       ├─ 成功：返回 true
                       ├─ 预算耗尽：返回 false
                       └─ 响应中断：抛 InterruptedException
```

时间预算限制的是**等待获取锁**，不限制获得锁后业务代码的运行时间。`tryLock(200, MILLISECONDS)` 成功后，业务代码若执行两秒，方法不会在第 200 毫秒自动把锁收回。

线程调度和方法返回也有成本，超时参数不是精确的整体响应时刻。

#### 具体示例：锁忙则不更新配置

```java
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;

class TimedConfig {
    private final ReentrantLock lock = new ReentrantLock();
    private String displayName = "default";

    boolean changeName(String newName) throws InterruptedException {
        boolean acquired = lock.tryLock(200, TimeUnit.MILLISECONDS); // ①
        if (!acquired) {                                            // ②
            return false; // 本次锁获取超时，displayName 没有被本方法修改
        }

        try {
            displayName = newName;                                  // ③
            return true;
        } finally {
            lock.unlock();                                          // ④
        }
    }
}
```

① 三种结果都可能出现：成功返回 `true`；等待耗尽返回 `false`；被中断抛出异常。② 没获得锁时，既不能修改受保护的数据，也不能调用 `unlock()`。③ 和④ 只在获取成功后执行，异常释放路径由 `finally` 兜底。

调用方可以对 `false` 返回“配置忙，请稍后重试”，而把 `InterruptedException` 向更上层传播。这两个结果含义不同：一个表示等待预算耗尽，一个表示当前任务收到取消请求。

#### 为什么加锁放在 try/finally 之前

以下结构有问题：

```java
// 错误示例。
try {
    lock.tryLock(200, TimeUnit.MILLISECONDS); // 忽略返回值，也可能抛中断异常
    changeSharedData();
} finally {
    lock.unlock(); // 未成功获取时也执行，可能产生另一异常
}
```

它既可能在没有锁的情况下修改数据，也可能在中断后错误释放。应该先确认本次成功获取，再进入负责释放的 `try/finally`。

若调用方进入方法前就已持有同一锁，重入成功会增加一次持有；释放应只对应这次成功增加的持有，不能顺便释放外层的锁。

#### 中断、零时长与公平策略

带时间的 `tryLock()` 是中断响应点。已有标记或等待期间被中断时，可以抛出 `InterruptedException` 并清除标记。对 `ReentrantLock` 而言，中断检查优先于正常或重入获取，因此“锁现在空闲”也不保证一个已中断调用成功返回。

零或负时长不进行正时长等待，但仍需按该重载的中断与获取规则处理。公平锁的带时间版本遵守其公平获取策略；无参 `tryLock()` 即使面对公平锁，也允许立即竞争而不按等待顺序。这两个重载不能仅按是否带时间互相替换。

<a id="method-4-5"></a>

### 4.4 ReentrantLock.lockInterruptibly()：允许取消正在排队的加锁请求

#### 解决的问题：任务可能卡在锁外，尚未进入业务代码

如果任务在 `lock()` 处等待，循环里的中断检查还没有机会执行。取消请求到来后，`lock()` 本身不会因此放弃等待；原持有者若迟迟不释放，任务就仍然停在锁外。

`lockInterruptibly()` 把获取锁这一步设计为中断响应点：

```java
public void lockInterruptibly() throws InterruptedException
```

它没有固定超时，但等待期间可以因中断退出。这适合“可以等，只要任务仍未被取消”的场景。

#### 内部执行流程：中断成为另一条退出路径

```text
进入方法时检查已有中断
    ├─ 已中断：报告 InterruptedException
    └─ 未中断：尝试获取锁
                    ├─ 成功：返回，当前线程持有锁
                    └─ 失败：排队等待并重试
                                  ├─ 获得锁：返回
                                  └─ 响应中断：放弃本次等待，报告异常
```

竞争与中断可能同时发生，不宜根据外部日志猜测结果。调用方应正确处理“成功获取”与“响应中断而没有新增持有”这两条路径。

#### 具体示例：已收到取消请求时不继续等待锁

```java
import java.util.concurrent.locks.ReentrantLock;

public class InterruptibleLockDemo {
    public static void main(String[] args) throws InterruptedException {
        ReentrantLock lock = new ReentrantLock();

        Thread worker = new Thread(() -> {
            try {
                lock.lockInterruptibly(); // ① 可中断获取；成功后才进入内层 try
                try {
                    System.out.println("worker 已获得锁");
                } finally {
                    lock.unlock();        // ② 只释放本次成功获取
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                System.out.println("worker 放弃等待锁");
            }
        }, "worker");

        lock.lock();                     // ③ main 先占用锁
        try {
            worker.start();
            worker.interrupt();          // ④ 取消 worker 的加锁请求
            worker.join();               // ⑤ worker 能响应中断而结束
        } finally {
            lock.unlock();               // ⑥ main 归还自己的锁
        }
    }
}
```

此例输出“worker 放弃等待锁”。main 在 worker 整个存活期间持有锁，worker 没有成功获取机会。中断若在 worker 进入①之前到达，①检查已有标记；若在①的等待期间到达，等待响应中断。两种时序都能走取消路径。

这里 main 持锁等待 worker 的结构，仅用于演示**已明确取消的可中断获取**，不应当作一般的业务等待模板。若把①换成普通 `lock()`，worker 无法靠中断放弃获取，main 和 worker 就会彼此等待。

#### 与 tryLock(timeout) 的选择

| 方法 | 获取不到时如何退出 |
|---|---|
| `lock()` | 等待获得锁，不因中断放弃这次获取 |
| `lockInterruptibly()` | 获得锁或响应中断，没有固定等待预算 |
| `tryLock(timeout, unit)` | 获得锁、预算耗尽或响应中断 |

需要同时支持取消和超时，应使用带时间的 `tryLock()`。不应该用循环无休止地立即 `tryLock()` 模拟等待，那样可能浪费 CPU，并使取消与公平性处理变复杂。

#### 中断发生在获得锁之后怎么办

成功返回后，线程已经获得锁。随后收到中断，不会让此前已经返回的方法“补抛一个异常”，也不会自动释放锁。业务代码仍需检查取消，或通过后续的可中断等待响应，并确保 `finally` 释放。

该方法报告 `InterruptedException` 时清除当前线程标记。外层若不能传播异常，通常应恢复标记并退出。调用方若在进入前已经持有这把锁，中断失败不会替它释放原来的一层持有。

<a id="method-4-7"></a>

### 4.5 Condition.await()：释放关联的显式锁，等待业务条件

#### 解决的问题：既要保护队列，又不能拿着锁等数据

假设一个消费者获取锁后发现队列为空，需要等待生产者加入元素。若消费者持锁 `sleep()`，生产者无法进入修改队列，条件就不能改变。

`Condition.await()` 与 `Object.wait()` 的思想相同，但关联的是 `ReentrantLock`。它让消费者释放这把锁，等待被通知等事件，随后重新获取锁再检查队列。

主要签名为：

```java
void await() throws InterruptedException
```

#### 前置概念：一把锁可以拥有多个条件队列

```java
ReentrantLock lock = new ReentrantLock();
Condition notEmpty = lock.newCondition(); // 等待“队列非空”的线程
Condition notFull = lock.newCondition();  // 等待“队列未满”的线程
```

两个 `Condition` 共用同一把锁来保护数据，但把等待者按条件分开。`Condition` 对象不会自己检查队列，也不保存条件是否为真。真正的条件仍然由 `queue.isEmpty()`、`queue.size()` 等业务状态表达。

调用 `notEmpty.await()` 前，当前线程必须持有关联的 `lock`。`synchronized (notEmpty)` 只获取对象监视器，不能替代 `lock.lock()`。对 `ReentrantLock` 创建的条件，未持有该锁调用等待或通知，会抛出 `IllegalMonitorStateException`。

#### 具体示例：容量为一的队列

```java
import java.util.ArrayDeque;
import java.util.concurrent.locks.Condition;
import java.util.concurrent.locks.ReentrantLock;

class ConditionMailbox {
    private final ReentrantLock lock = new ReentrantLock();
    private final Condition notEmpty = lock.newCondition();
    private final Condition notFull = lock.newCondition();
    private final ArrayDeque<String> queue = new ArrayDeque<>();

    void put(String message) throws InterruptedException {
        if (message == null) {
            throw new IllegalArgumentException("消息不能为 null");
        }
        lock.lockInterruptibly();
        try {
            while (queue.size() == 1) { // ① 容量已满，等“未满”条件
                notFull.await();       // ② 释放 lock，进入 notFull 等待
            }
            queue.addLast(message);    // ③ 获得空间后写入
            notEmpty.signal();         // ④ 通知一个等“非空”的消费者
        } finally {
            lock.unlock();
        }
    }

    String take() throws InterruptedException {
        lock.lockInterruptibly();      // ⑤ 先取得保护队列的锁
        try {
            while (queue.isEmpty()) {  // ⑥ 条件必须在锁内检查
                notEmpty.await();     // ⑦ 释放 lock，等数据到达
            }
            String message = queue.removeFirst(); // ⑧ 条件满足后消费
            notFull.signal();          // ⑨ 通知一个等“未满”的生产者
            return message;
        } finally {
            lock.unlock();
        }
    }
}
```

ArrayDeque 本身没有被当作并发容器使用；它的所有相关访问由同一把锁保护。`notEmpty` 与 `notFull` 只是把等待者分组，数据的互斥规则仍由 `lock` 提供。

生产者和消费者通过 `signal()` 通知不同组的等待者，避免了一个对象监视器上混合多种条件时，无法指定通知组的问题。教学示例体现底层协作；实际应用可以直接使用后文的 `BlockingQueue`。

#### 内部流程：条件队列与获取锁的等待是两个阶段

消费者先发现空队列时，可能发生以下流程：

```text
消费者获取 lock，确认队列为空
    ↓
notEmpty.await()
    ↓
登记为 notEmpty 的等待者，释放 lock
    ↓
生产者获取 lock，加入消息，调用 notEmpty.signal()
    ↓
消费者获得重新获取 lock 的机会，但生产者此时仍持锁
    ↓
生产者退出临界区，释放 lock
    ↓
消费者成功重获 lock，再次检查队列，取出消息
```

OpenJDK 的实现把条件等待与获取锁的等待分开管理。发信号后，等待者需要转向锁的获取过程；不是生产者直接把锁交给消费者，也不是一通知消费者就立即执行下一行。

对于 `ReentrantLock` 创建的条件，真正进入等待时会释放该锁的全部重入持有；恢复时再还原原先的持有次数。它不释放线程持有的其他业务锁。

#### await() 返回后为什么还是要 while

`await()` 可以虚假唤醒；即使发生真实通知，资源也可能被其他先获得锁的消费者取走。消费者获得锁时，队列是否仍非空，必须重新检查。

`signal()` 没有把一条消息保留给某个指定消费者，`await()` 的正常返回也不带“条件一定成立”的证明。`while` 检查是整个协议的一部分，不能因条件队列分得更细就改成 `if`。

若生产者先写入，消费者后到，消费者通过持锁检查发现队列已有数据，会直接消费，不要求再补收一次历史信号。

#### 中断与锁恢复：异常处理开始时持锁关系是什么

`await()` 支持中断。进入方法时已有标记，可以直接抛出 `InterruptedException`；已经进入条件等待后响应中断，需要在重新获取关联锁之后报告异常，并清除标记。

本例的 `take()` 向上声明中断异常，同时通过 `finally` 释放锁。如果中断发生在⑤等待获取锁时，获取失败，尚未进入负责释放的 `try`；如果发生在⑦条件等待中，异常报告前锁已经重获，所以内层 `finally` 负责释放这次仍持有的锁。

等待者重获锁可能还需时间，因此中断不保证其异常处理立即执行。通知与中断接近时也存在竞态，业务逻辑不能依赖“收到 signal 后一定只会正常返回”的假设。

#### 返回与使用边界

无参 `await()` 返回 `void`，没有超时，不直接返回消息。正常返回时当前线程重新持有关联锁，需要重查条件；业务方法在临界区内取出消息，再返回自己的结果。

`signal()` 或 `signalAll()` 同样需要持有关联锁，它们不自动释放锁。没有等待者时信号不会存为以后可用的消息，持久业务状态仍必须保存。

如果等待需要整体时间上限，可以选用带时间的条件等待，并在循环中计算剩余预算。每次唤醒后重新给足完整时间，不能实现固定的整体超时。重入恢复、中断和通知顺序的具体规则见 [Java 21 ReentrantLock.newCondition()](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/ReentrantLock.html#newCondition())；接口范围见 [Java 21 Condition](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/Condition.html)。


<a id="sec-6"></a>

## 5. 执行器与线程池：从提交任务到结束服务

手工启动线程时，一条 `Thread` 对象通常对应一次执行。线程池采用另一种组织方式：程序把许多任务提交给池中的少量工作线程，每条工作线程执行完一个任务，再取下一个任务。这就是“复用线程”。

理解本章先分清三个角色：

| 角色 | 具体含义 | 是否就是一条线程 |
|---|---|---|
| 任务 | 一段需要执行的代码，常用 `Runnable` 或 `Callable` 表示 | 否 |
| 执行器 | 接收任务、决定如何执行的对象 | 否 |
| 工作线程 | 实际执行任务代码的线程 | 是 |

`Runnable.run()` 不直接返回结果；`Callable<T>.call()` 可以返回类型为 `T` 的结果，并可声明受检异常。两者都只是任务描述，创建任务对象不会自动执行其中的代码。

`Executor` 是最基础的执行接口；`ExecutorService` 增加任务结果和关闭管理；`ThreadPoolExecutor` 是常见的平台线程池实现。下文解释池的接收、排队和关闭过程时，以 Java 21 的普通 `ThreadPoolExecutor` 为准。接口允许其他实现采用不同机制。

<a id="method-6-5"></a>

### 5.1 execute(Runnable)：把任务交给执行器【重点】

#### 用途与调用者的变化

假设程序需要处理一批日志。逐条 `new Thread(...)` 会不断创建平台线程；使用线程池，可以让已有线程接着处理日志。`execute(task)` 负责把一项任务交给执行器，返回类型是 `void`，不提供读取任务结果的凭证。

最常见的调用方式如下。示例中的语句可以放在 `main` 中，并导入 `java.util.concurrent.*`。

```java
ExecutorService pool = Executors.newFixedThreadPool(2); // ①

pool.execute(() -> {                                  // ②
    System.out.println("处理日志："
        + Thread.currentThread().getName());          // ③
});

System.out.println("提交后继续处理其他工作");           // ④
pool.shutdown();                                     // ⑤
```

① 创建一个最多有两条工作线程同时执行任务的池，线程通常在需要时创建。② 构造 `Runnable` 并提交；此时不要求任务已经运行完。③ 这是任务内容，在池的工作线程中执行。④ 是提交者后续的代码。⑤ 发起关闭，已经接收的日志任务仍可继续执行。

这段示例中，③ 与④ 谁先打印没有保证。`execute()` 的调用发生在提交者线程中，任务代码的执行通常发生在工作线程中，两段代码可以并发运行。

#### 内部如何决定接收任务

理解线程池的接收规则，需要四项配置：`corePoolSize` 是核心线程数量，`maximumPoolSize` 是允许的最大线程数量，工作队列存放暂未执行的任务，拒绝策略决定接收失败后怎么办。

`ThreadPoolExecutor.execute()` 的概念流程如下。流程图是机制概括，不是可以编译的真实源码。

```text
收到任务
  │
  ├─ 工作线程数少于核心数量？
  │     └─ 尝试新增工作线程，把任务作为它的首个任务
  │
  ├─ 没有通过上一步接收，且池仍允许接收？
  │     └─ 尝试把任务放入工作队列
  │          └─ 入队后重查关闭状态、确认存在消费队列的线程
  │
  ├─ 无法入队？
  │     └─ 尝试新增工作线程，但不能超过最大数量
  │
  └─ 仍不能接收，或池已经关闭 → 执行拒绝策略
```

入队后需要重新检查，是因为其他线程可能恰好同时关闭线程池。不能仅凭“入队前还没关闭”就认定任务一定应继续留在队列里。

接收顺序通常是 **核心线程 → 队列 → 扩容到最大线程数 → 拒绝**。以下配置有助于观察：

```java
ThreadPoolExecutor pool = new ThreadPoolExecutor(
    1, 2,                                      // 核心 1 条，最多 2 条
    30, TimeUnit.SECONDS,                     // 多出的线程空闲后可回收
    new ArrayBlockingQueue<>(1),              // 队列最多存 1 个任务
    new ThreadPoolExecutor.AbortPolicy()      // 接收失败时抛异常
);
```

假设前面的任务一直没结束，快速提交任务 A、B、C、D：A 可由新增的核心线程执行，B 排队，C 因队列已满而触发第二条工作线程，D 因线程与队列都满而被拒绝。这个推演依赖“前面的任务仍占据资源”这一前提；如果 A 很快结束，队列空间就可能被释放，D 未必被拒绝。

这也解释了常见误区：最大线程数设为 100，不代表池会先创建 100 条线程。工作队列如果几乎总能接收，池通常会一直排队，而不因任务积压自动扩容到 100。接收规则见 [ThreadPoolExecutor 官方说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ThreadPoolExecutor.html)。

#### execute 一定异步执行吗

`Executor` 接口不保证使用另一条线程。下面是一个合法的同步执行器：

```java
Executor direct = Runnable::run;
direct.execute(() -> System.out.println("由调用者直接执行"));
```

普通线程池也可能由调用者执行任务。配置 `CallerRunsPolicy` 后，如果池因饱和拒绝接收、但尚未关闭，拒绝策略会让提交者直接运行这个任务。此时 `execute()` 会花费任务执行时间，任务异常也可能在调用者的这次调用中抛出。该策略在池已关闭时会丢弃任务，而不会继续让调用者运行。

因此，“交给执行器”与“必然切换线程”不是同一件事。接口契约见 [Executor 官方说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Executor.html)。

#### 任务出错时，异常在哪里

普通 `ThreadPoolExecutor` 的工作线程直接运行 `execute()` 提交的普通 `Runnable`，如果未处理异常逃出任务，会走工作线程的未捕获异常路径，并可能使该工作线程退出，由池按需要补充工作线程。提交者外层的 `try-catch` 通常捕获不到后来在工作线程中产生的异常。

```java
try {
    pool.execute(() -> {
        throw new IllegalStateException("日志处理失败");
    });
} catch (RejectedExecutionException e) {
    // 捕获提交阶段的拒绝，并不等于捕获了任务执行阶段的异常。
}
```

需要观察结果与失败时，`submit()` 更方便。直接使用 `execute()` 时，应在任务边界设计明确的错误记录或报告方式。不能把“提交成功”作为“业务处理成功”。

工作线程的寿命也不等于某个任务的寿命。普通任务结束后，工作线程通常继续从队列取任务；只有池关闭、空闲回收或异常等条件才使它退出。排队任务没有各自独占的线程，队列只存放任务对象。分析任务积压时，必须同时看正在执行的数量与排队数量，不能把 `Thread` 的数量当作所有未完成任务的数量。

<a id="method-6-6"></a>

### 5.2 submit(...)：提交任务，并取得 Future【重点】

#### 为什么还需要 submit

某项任务可能要计算订单总价，调用者随后需要取得价格；也可能没有返回值，但仍需要知道是否执行失败。`submit()` 返回的 `Future` 解决了这两件事：它记录任务的完成状态，并提供等待结果、读取异常和取消的入口。

“返回 Future”不表示结果已经出现。Future 可以先存在，任务随后才开始执行。

三个常见重载的区别是结果来源：

| 提交形式 | 任务正常完成后 `get()` 返回什么 |
|---|---|
| `submit(Callable<T>)` | `call()` 的返回值 |
| `submit(Runnable)` | `null` |
| `submit(Runnable, T result)` | 提交时指定的 `result` |

例如 `() -> 6 * 7` 返回整数，是 `Callable<Integer>`；`() -> System.out.println("完成")` 不返回值，是 `Runnable`。第三个重载不是读取 `Runnable.run()` 的返回值，因为这个方法本来就是 `void`。

#### 内部如何把“能运行”与“能查询”放在一起

普通 `ThreadPoolExecutor` 继承的 `submit()` 实现来自 `AbstractExecutorService`。其默认路径先创建 `FutureTask`，再把这个包装对象交给 `execute()`。实现允许子类替换包装方式，因此该路径不应当作所有执行器的固定源码。

```text
原始 Callable 或 Runnable
        ↓ 包装
FutureTask 对象
  ├─ 实现 Runnable：可以放入线程池执行
  └─ 实现 Future：保存状态、结果、异常和等待者
        ↓ execute(包装对象)
工作线程运行 FutureTask.run()
        ↓
调用原任务 → 保存返回值或异常 → 发布完成状态 → 唤醒等待者
```

任务的结果和状态就在这个包装对象中。它不是单独创建的一条“Future 线程”。这一包装关系可以对照 [OpenJDK 21 AbstractExecutorService 实现](https://github.com/openjdk/jdk/blob/jdk-21%2B35/src/java.base/share/classes/java/util/concurrent/AbstractExecutorService.java)。

#### 顺着代码理解提交和读取

```java
ExecutorService pool = Executors.newFixedThreadPool(2);
try {
    Future<Integer> future = pool.submit(() -> { // ①
        int unitPrice = 20;
        int quantity = 3;
        return unitPrice * quantity;            // ②
    });

    System.out.println("任务已经提交");           // ③
    int total = future.get();                   // ④
    System.out.println("总价：" + total);         // ⑤
} finally {
    pool.shutdown();
}
```

① 得到一个结果类型为 `Integer` 的 Future。② 在任务的执行线程中计算并返回 60。③ 由提交者打印，任务此时可能未开始、正在执行或已经完成。④ 由当前调用者读取 Future；未完成就等待，完成就取得结果。⑤ 在④成功返回之后打印，因此总价一定是 60。

示例所在的方法需要声明 `throws InterruptedException, ExecutionException`，或者分别处理这两类异常。它们不应为了解决编译问题而无条件吞掉。两类异常的含义在第 6 章详解。

#### submit 如何保存任务异常

```java
Future<Integer> future = pool.submit(() -> {
    throw new IllegalArgumentException("订单数据不合法");
});

try {
    future.get();
} catch (ExecutionException e) {
    System.err.println(e.getCause());
}
```

包装任务会捕获业务任务抛出的异常，并把 Future 设为异常完成。调用 `get()` 时才用 `ExecutionException` 报告失败，`getCause()` 是原始原因。因此，使用 `submit()` 却从不读取或观察 Future，可能导致业务异常没有被报告。工作线程没有打印堆栈，并不能证明任务成功。

提交阶段的拒绝与执行阶段的失败也要分开：默认拒绝策略可直接让 `submit()` 抛 `RejectedExecutionException`，此时连正常返回 Future 这一步都没完成；任务已接收而后来失败，则通过返回的 Future 表达。

如果选择静默丢弃的拒绝策略，`execute()` 可能返回而包装任务根本没有运行，`submit()` 却仍返回未完成的 Future；之后 `get()` 可能一直等待。需要结果的任务，拒绝逻辑必须给调用者一个明确结论。接口结果契约见 [ExecutorService 官方说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ExecutorService.html)。

带指定结果的重载也容易误读。例如 `pool.submit(() -> saveRecord(), "保存完成")` 中，字符串只是正常结束后要返回的固定标记，并不会检查数据库是否达到某种业务状态。`saveRecord()` 必须自己正确表达成功与失败：异常被吞掉，包装任务仍可能认为它正常结束，并返回“保存完成”。并发 API 的正常完成与业务成功，需要任务代码共同建立对应关系。

<a id="method-6-7"></a>

### 5.3 shutdown()：停止接收新任务，完成已接收任务【重点】

#### 它关闭的是接收入口

程序完成最后一次提交后，通常希望池把已有工作做完，再释放工作线程。`shutdown()` 发起这个关闭流程。它不接收新任务，通常保留已接收任务的执行机会，并且自身不等待这些任务执行完。

对于普通 `ThreadPoolExecutor`，可以把相关状态理解成：

```text
RUNNING：可接收新任务，也处理队列任务
    ↓ shutdown()
SHUTDOWN：停止接收新任务，继续处理已接收任务
    ↓ 队列已空，并且工作线程全部退出
TIDYING：进行终止收尾
    ↓
TERMINATED：池已真正终止
```

状态名称不是 `Thread.State`，而是线程池自身的运行状态。一条任务还在执行时，池完全可以已经是 `SHUTDOWN`。

#### 内部做了哪些工作

调用方法的线程更新池的关闭状态。池会使空闲工作线程有机会从等待中醒来、检查关闭条件；对于普通池，这常通过中断空闲工作线程实现。运行中的业务任务不会仅因 `shutdown()` 就被主动中断。工作线程完成任务后继续取队列里的任务；队列耗尽后，线程按关闭规则退出，最后推进到终止状态。

因此，中断空闲线程是池内部的退出管理，不能误读为“shutdown 会取消每个业务任务”。关闭流程可以对照 [OpenJDK 21 ThreadPoolExecutor 实现](https://github.com/openjdk/jdk/blob/jdk-21%2B35/src/java.base/share/classes/java/util/concurrent/ThreadPoolExecutor.java)。

#### 正确地等待关闭完成

```java
pool.submit(() -> System.out.println("处理最后一项任务"));

pool.shutdown();                              // ①
boolean ended = pool.awaitTermination(        // ②
    5, TimeUnit.SECONDS
);

if (ended) {
    System.out.println("线程池已终止");           // ③
} else {
    System.out.println("本次等待超时");           // ④
}
```

① 发出关闭请求，很快返回；② 是当前调用者等待整个池终止，最长等待给定时间；③ 表示池确实终止；④ 表示本次等待没有等到终止，任务可能仍在运行。④不会自动停止任务，也不会把池恢复成可提交状态。

`isShutdown()` 回答“是否已经发起关闭”，`isTerminated()` 回答“是否已经终止”。关闭刚开始时，前者可以是 `true`，后者仍是 `false`。是否能关闭任务仍需使用的数据库连接、文件等资源，应依据任务确已结束的证据，而不是仅凭 `isShutdown()`。

重复调用 `shutdown()` 不会重新执行一轮任务，也不会让池恢复运行。关闭以后使用默认拒绝策略再次提交，会被拒绝。需要新一轮池服务时应重新创建或重新组织生命周期，不能“重新 start”旧线程池。

本节针对普通线程池的已接收任务。定时池对周期任务、延迟任务的关闭处理还受专门策略控制，不能将普通队列的描述直接套到所有定时任务上。

还有一种边界：已接收任务执行期间再提交一个新任务，新任务属于关闭后的新提交，同样会被拒绝，并不会因为提交者恰好是池内线程而自动放行。存在分阶段提交时，应用应确认所有必要阶段都已经完成提交，再关闭相应执行器。尤其是 CompletableFuture 后续函数还要使用同一池时，不能在第一步刚提交后就认定整条业务链已经全部提交。

<a id="method-6-8"></a>

### 5.4 shutdownNow()：停止取新任务，并尝试中断正在执行的任务【重点】

#### 为什么名称中的 Now 不等于立刻停止

有序关闭可能等待很久。例如，一个计算任务始终不退出，`shutdown()` 就无法让整个池终止。`shutdownNow()` 的请求更积极：停止继续处理队列任务，并尝试中断工作线程。Java 中断是协作信号，不是强制杀死线程，所以它仍不能保证任务代码立刻结束。

在普通 `ThreadPoolExecutor` 中，概念流程是：

```text
把运行状态推进到 STOP
        ↓
尝试中断工作线程，包括正在执行任务的线程
        ↓
从队列取走等待执行的任务
        ↓
返回这些尚未执行的任务对象
        ↓
工作线程真正退出后，池才能最终进入 TERMINATED
```

它不等待最后这一步。提交或取任务与关闭存在竞争，不能根据返回列表推断程序历史上“所有尚未完成任务”的全量清单；正在执行或已经被工作线程取得的任务不在这个队列返回列表里。

#### 如何理解返回列表

```java
List<Runnable> pending = pool.shutdownNow();   // ①

for (Runnable task : pending) {                // ②
    if (task instanceof Future<?> future) {
        future.cancel(false);                 // ③
    }
}

boolean ended = pool.awaitTermination(         // ④
    5, TimeUnit.SECONDS
);
```

① 得到被移出队列、不会再由这个池执行的任务。② 明确处置这些任务。对于普通池的 `submit()` 任务，列表里的对象常是 `FutureTask` 包装对象；③ 将包装结果标为取消，使持有 Future 的其他调用者得到取消结论。④ 再等待池实际终止，返回 `false` 仍表示没有等到结束。

普通 `ThreadPoolExecutor.shutdownNow()` 取走队列任务，并不保证自动对每个包装 Future 调用 `cancel()`。如果未处理，某个队列 Future 既没有运行，也没有完成，其他代码可能一直等待它的 `get()`。定时池等实现的具体返回与取消方式可能不同，必须区分实现。

#### 运行中的任务如何配合

任务如果处于 `sleep()`、可中断队列等待等位置，中断可能使等待抛 `InterruptedException`。任务应在中断路径执行清理并退出，而不是无条件继续下一轮。纯计算任务则需要在合理位置检查中断标记，例如：

```java
while (!Thread.currentThread().isInterrupted()) {
    // 执行一小批计算，再回到循环条件检查是否应停止。
}
// 离开循环后执行必要的资源清理。
```

循环检查不能替代对所有阻塞资源的处理。某些 I/O 操作有自己的取消、关闭或超时契约，需要结合具体资源设计。

“Future 被取消”“池收到了停止请求”“任务线程退出”“外部副作用被撤回”是不同事件。`shutdownNow()` 不会把已经写入文件、已提交事务或已发送的请求自动回滚。方法名称所表达的是请求力度，真正结束仍要等待和确认。

判断一个停止方案是否正确，至少需要追踪三个环节：队列中取出的任务有没有得到处置，运行中的任务是否存在能观察到停止请求的路径，资源是否在实际退出后释放。仅调用该方法再打印“全部停止”，缺少最后两个环节的证据。另一次 `awaitTermination()` 超时也不是停止失败任务的回执，而是等待者观察到“池还没有终止”。

<a id="method-6-16"></a>

### 5.5 scheduleAtFixedRate(...)：按固定节奏安排重复执行【重点】

#### 先理解四个参数

统计采样、状态检查等工作需要重复执行。该方法接收任务、首次延迟、周期和时间单位：

```java
ScheduledExecutorService scheduler =
    Executors.newScheduledThreadPool(1);

ScheduledFuture<?> handle = scheduler.scheduleAtFixedRate(
    () -> System.out.println("采样一次"), // ① 重复执行的任务
    1,                                   // ② 首次延迟
    2,                                   // ③ 计划周期
    TimeUnit.SECONDS                     // ④ 两个时间参数的单位
);
```

这表示：首次约在提交后第 1 秒具备执行条件，以后按第 3、5、7 秒等目标节奏安排。时间到了意味着任务可以执行，不意味着精确到该时刻就一定获得 CPU。周期必须大于零。

返回的 `ScheduledFuture` 代表整项周期安排，可以用于取消或观察异常，不代表“第一次采样结果”。周期任务持续正常运行时，这个 Future 不会因每一轮结束就正常完成，因而不宜用它的 `get()` 来取得每轮结果。

#### 内部如何再次安排同一个任务

定时池把任务和下一次应执行的时刻放入延迟队列。工作线程等到任务到期后执行；本轮正常结束后，再计算下一次目标时刻，并重新安排。对固定频率，下一次目标沿原来的节奏推进：

```text
首次目标时刻 + 第 n 个周期 × 周期长度
```

例如目标为 0、1、2 秒，本轮在 0 秒开始却耗时 1.5 秒，那么目标为 1 秒的下一轮已经迟到。它可以在资源允许时紧接着执行，随后继续沿原计划追赶。它并不会改成“每次执行完，再休息一秒”。

同一周期安排中的前后两轮不会并发重叠；即使定时池有多条线程，也不会因一轮超时而把下一轮放到另一条线程上并发执行。不同任务、或对同一段代码提交的两份独立周期安排，则可能同时执行。[ScheduledExecutorService 周期契约](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ScheduledExecutorService.html)。

#### 和固定延迟对照

假设每轮执行耗时 300 毫秒，周期参数为 1 秒：

```text
固定频率：目标开始于 0、1、2、3 秒……
固定延迟：0 秒开始 → 0.3 秒结束 → 再等 1 秒 → 1.3 秒开始……
```

前者强调原定节奏，后者强调两轮之间的休息间隔。需要执行完成后等待一段时间再轮询的场景，通常应考虑表格中的 `scheduleWithFixedDelay()`。

#### 一次异常为什么会导致以后不再执行

```java
ScheduledFuture<?> handle = scheduler.scheduleAtFixedRate(
    () -> {
        throw new IllegalStateException("采样失败");
    },
    0, 1, TimeUnit.SECONDS
);
```

这项周期任务第一轮失败后，后续轮次会被抑制，Future 异常完成。调用该 Future 的 `get()` 会通过 `ExecutionException` 报告失败。不会因为设置了“每秒一次”就自动忽略异常重新运行；异常也不一定出现在调用调度方法的线程中。

被抑制的是这项周期安排，不等于整个调度器已经关闭。其他已安排的任务仍可能继续执行。排查“定时任务突然不再打印”时，应先观察该任务句柄的状态和异常，而不是只检查调度器的线程是否还活着。线程仍存活，恰好可以与某个周期任务已经失败同时成立。

希望某种可恢复错误不终止周期安排时，应在任务内部捕获这类具体错误、记录并完成本轮：

```java
Runnable sample = () -> {
    try {
        // 执行采样；以下方法代表具体业务调用。
        sampleOnce();
    } catch (RecoverableSamplingException e) {
        // 记录本轮失败，使该类错误不会逃出周期任务。
        recordSamplingFailure(e);
    }
};
```

最后这段是结构示例，业务方法与异常类型需要由应用定义。不能为“保证一直运行”而无条件吞掉所有 `Throwable`，那会掩盖严重错误和停止请求。结束使用时应按需要取消周期句柄并关闭调度器，例如 `handle.cancel(false); scheduler.shutdown();`。前者通常不打断正在执行的本轮，后者发起调度器关闭。

<a id="sec-7"></a>

## 6. Future：等待任务结果与理解取消

`Future<T>` 可以理解为一份任务结果凭证。`T` 指结果类型，例如 `Future<Integer>` 对应整数结果。Future 对象可以先返回给提交者，任务再在后台执行。访问这个对象并不等于访问执行任务的线程。

本章将 Future 的可观察状态分为四类：尚未完成、正常完成、异常完成、取消。后三类都属于“已完成”，所以 `isDone()` 返回 `true` 只能证明进入了某种完成状态，不能证明业务成功。具体内部实现以普通线程池默认使用的 `FutureTask` 为例。

<a id="method-7-1"></a>

### 6.1 get()：等待 Future，并读取任务结果【重点】

#### 等待的是调用者，计算的是工作线程

一个后台任务正在计算，main 需要使用计算结果。main 调用 `future.get()` 后，如果还没有结果，暂停等待的是 main；工作线程继续计算，并不会因为被调用 `get()` 而暂停。

```text
main：submit() → 得到 Future → get() 等待 ─────→ 取得结果继续执行
                             ↑                ↑
worker：              开始计算 → 保存结果 → 通知等待者
```

如果任务在调用前就已完成，`get()` 可以直接读取已有结果，不必为了方法名字中的 Future 而额外等待。多次调用 `get()` 不会重新执行任务；正常完成后，读取的是同一次任务保存的结果。

#### 内部怎样等到结果

对 OpenJDK 21 `FutureTask`，可以将内部过程理解为：

1. 查询完成状态；正常完成就返回保存的值，失败或取消则报告对应异常。
2. 尚未完成时，把当前线程登记为等待者。需要多个等待者时，每个调用者都有自己的等待记录。
3. 使用 `LockSupport` 等机制挂起等待线程，避免不停循环消耗 CPU。
4. 任务完成或取消后发布结果状态，并唤醒等待者。
5. 被唤醒的线程再次检查状态，再决定返回、抛异常或继续等待。

唤醒只是给线程再次检查的机会，不意味着可以跳过状态判断。结果写入和状态发布还需要确保可见性，使正常取得结果的调用者能观察任务中的相关写入；不能把这种保证理解为后续对任意共享变量的访问都线程安全。[FutureTask 官方说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/FutureTask.html)。

#### 四种返回路径必须分开

```java
ExecutorService pool = Executors.newFixedThreadPool(1);
Future<Integer> future = pool.submit(() -> 42);

try {
    int result = future.get();              // ① 正常完成，得到 42
    System.out.println(result);
} catch (InterruptedException e) {          // ② 等待结果的线程被中断
    Thread.currentThread().interrupt();
    System.err.println("调用者停止等待结果");
} catch (ExecutionException e) {            // ③ 任务执行失败
    System.err.println("任务失败：" + e.getCause());
} catch (CancellationException e) {         // ④ Future 已被取消
    System.err.println("任务结果已取消");
} finally {
    pool.shutdown();
}
```

① 表示 Future 正常完成，直接返回值。② 发生在当前线程需要等待期间，表示调用者收到了中断请求；捕获后恢复标记，便于上层识别。若方法允许传播 `InterruptedException`，也可以直接交给上层处理。③ 表示任务抛出的失败已被保存，原始异常用 `getCause()` 取得。④ 是非受检异常，即便方法签名没写它，也可能出现。

本例任务只返回 42，正常情况下走①，其余分支说明一般任务的异常处理结构。`ExecutionException` 与 `InterruptedException` 不能混为“后台任务失败”：前者谈任务，后者谈正在等待的调用者。

尤其需要注意：**调用者停止等待，不会由 get 自动取消后台任务。** main 等待被中断后退出，worker 仍可能继续运行。是否调用 `future.cancel(true)`，取决于业务是否还需要这个任务，不能无条件对共享任务取消。

#### 两种典型的无限等待

第一种是在锁内等待：main 持有某把业务锁调用 `get()`，但任务必须取得同一把锁才能产生结果。`get()` 不会释放 main 持有的这把业务锁，两边互相等待。修正方式是尽量在持锁区外等待结果，再进入短小的同步区域使用结果。

第二种是线程池容量依赖。池里只有一条工作线程，外层任务占着它，又提交一个内层任务并等待：

```java
ExecutorService pool = Executors.newSingleThreadExecutor();

Future<Integer> outer = pool.submit(() -> {
    Future<Integer> inner = pool.submit(() -> 10);
    return inner.get(); // 错误：唯一工作线程等待自己队列里的任务。
});
```

内层任务排队等待工作线程，工作线程却要先等内层任务完成。这个示例用于分析问题，不应直接运行后依赖正常退出。修正思路是避免同一受限池中这种阻塞依赖，能够直接计算的步骤直接调用，需要异步链的步骤用清楚的阶段组合设计。仅把池大小随意增加，可能在更高并发时再次出现相同问题。

不确定等待时长时，表格中的 `get(timeout, unit)` 可限制本次等待；超时只表示调用者本次没有等到结果，后台任务不会因此自动取消。

正常结果还可以是 null。例如 `submit(Runnable)` 对应的 `get()` 正常返回 null，这表示无返回值任务已正常结束；不能仅凭 null 就认为尚未完成。读取完成状态和取得结果是两个问题。正常结束但返回空业务数据，也不同于异常结束；失败应通过明确异常或业务结果类型表达，避免用 null 同时代表多种含义。

<a id="method-7-3"></a>

### 6.2 cancel(boolean)：尝试确定取消状态【重点】

#### 必须分开“结果不再使用”与“代码已经停止”

调用者放弃计算时，通常想取消任务。但取消包含两个不同目标：Future 不再提供原计算结果，以及任务代码尽快停止占用资源。`cancel()` 能确定取消状态，是否停止代码还取决于任务是否开始、实现是否发出中断、任务如何响应。

方法的参数名为 `mayInterruptIfRunning`，意为“如果正在运行，是否允许尝试中断”。它不是“强制结束开关”。

| 参数 | 普通 FutureTask 的关键行为 |
|---|---|
| `false` | 不通过这次取消中断运行线程；未开始的任务不再执行原任务，已开始的代码可能继续 |
| `true` | 未开始时阻止原任务开始；已经执行时尝试向记录的执行线程发出中断 |

方法返回 `true` 表示这次取消成功确定了 Future 的取消状态；返回 `false` 表示本次没能取消，例如任务已正常完成、已失败或已取消。判断成功停止代码，不能只检查这个返回值。

#### 内部状态如何与完成竞争

`FutureTask` 记录任务状态和当前执行线程。任务结束时要尝试保存结果，取消者也要尝试转换状态，两者通过原子操作竞争，避免同时把同一个 Future 宣布为“返回了正常结果”和“已经取消”。

```text
尚未确定完成结果
    ├─ 执行者先确定正常结果 → get 返回结果，后来的 cancel 失败
    ├─ 执行者先确定失败     → get 报告失败，后来的 cancel 失败
    └─ cancel 先确定取消    → get 抛 CancellationException
                               └─ true 参数时，再尝试中断执行线程
```

这是概念状态图。真实 `FutureTask` 还有发布结果、发送中断所用的过渡状态。一个容易误读的实现细节是：原始 `NEW` 状态并不只对应“尚未运行”，已经进入执行而结果还没确定的任务也可能保持这个状态；执行线程另由字段记录。状态查询不能替代任务执行进度报告。[OpenJDK 21 FutureTask 实现](https://github.com/openjdk/jdk/blob/jdk-21%2B35/src/java.base/share/classes/java/util/concurrent/FutureTask.java)。

#### 可配合取消的完整任务

以下代码可放在声明 `throws InterruptedException` 的方法中。`CountDownLatch` 在这里只用于确保任务已经开始；`started.await()` 等待它第一次调用 `countDown()`。

```java
ExecutorService pool = Executors.newSingleThreadExecutor();
CountDownLatch started = new CountDownLatch(1);

Future<?> future = pool.submit(() -> {
    started.countDown();                        // ① 报告已进入任务
    try {
        while (!Thread.currentThread().isInterrupted()) {
            Thread.sleep(100);                 // ② 模拟可中断等待
        }
    } catch (InterruptedException e) {          // ③ 响应中断并退出
        Thread.currentThread().interrupt();
    } finally {
        System.out.println("任务执行清理");       // ④ 在实际任务中清理
    }
});

started.await();                               // ⑤ 等任务开始
boolean cancelled = future.cancel(true);       // ⑥ 请求取消和中断
System.out.println("取消成功：" + cancelled);

pool.shutdown();
boolean stopped = pool.awaitTermination(2, TimeUnit.SECONDS);
System.out.println("线程池实际终止：" + stopped); // ⑦ 另外确认执行结束
```

①和⑤建立“开始之后再取消”的顺序，使示例不靠猜测休眠时间来判断任务是否启动。⑥成功后，Future 的读取结果已变成取消。任务如果在②收到中断，会走③与④；如果在检查循环条件时观察到标记，也会退出循环后执行④。⑦等的是整个池确已结束。

取消成功的打印与任务清理的打印先后不固定。Future 可以先对外宣布取消，任务之后才完成清理。在生产代码中，需要某个任务自己的退出证据时，可以使用独立的清理完成信号；不能因为 Future 已取消就立即拆掉它尚需使用的资源。

#### cancel(false) 也可能在任务运行期间成功

例如长计算已经开始，但尚未发布结果，`cancel(false)` 在普通 `FutureTask` 中仍可能返回 `true`。此后调用者的 `get()` 抛取消异常，原任务却没有因为这次取消收到中断，仍可能计算到结束；后来得到的正常结果不会替换已经确定的取消结论。

取消不能回滚已经发生的业务写入。正在发送的外部请求、已经执行的扣款等，需要业务级协议处理。此外，`CompletableFuture.cancel(true)` 不采用这里“记录执行线程并中断”的机制，不能仅因它也实现 `Future` 就套用相同内部解释。

取消与完成的竞争还说明了为什么先查询再取消不能提供保证：即使 `isDone()` 刚返回 false，执行者也可能在下一瞬间完成，使随后 `cancel()` 返回 false。调用者应以取消调用的实际结果和后续完成状态处理业务，而不能依据前一次状态快照承诺取消一定成功。状态查询不是锁住任务进度的操作。

<a id="sec-8"></a>

## 7. CompletableFuture：用结果连接后续任务

普通 Future 主要提供“提交后等待结果”的能力。假如业务要求“查询用户编号，然后查询用户详情，再转换成展示文本”，每一步都立即 `get()` 会让调用者反复等待，而且难以表达依赖关系。`CompletableFuture` 可以把关系先注册下来，等结果出现时再触发后续处理。

理解它时，应把两个问题分开：**数据从哪个阶段传给哪个阶段，以及阶段中的代码由哪个线程执行。** `thenApply()`、`thenCompose()`、`thenCombine()` 描述前一个问题；使用哪个执行器、是否带 `Async` 后缀，影响后一个问题。阶段数量不等于线程数量。

一个 `CompletableFuture<T>` 包含一次完成结果，以及依赖这个结果的后续动作。结果可以是正常值，也可以是异常。后续方法通常返回新的 CompletableFuture，代表新的阶段；不会因为变量名相似就把原来的结果对象改成新类型。

```text
阶段 A 保存结果
    ↓ 满足注册的触发条件
执行阶段 B 的函数
    ↓
阶段 B 保存自己的结果
    ↓
供下一阶段使用
```

无 `Async` 后缀的方法可能由完成上游的线程执行，也可能在上游已完成时由注册阶段的当前线程执行；不能假定它总在 main 或总在某条工作线程中。带 `Async` 后缀的方法经执行器调度，但执行器本身也可能采用调用者执行策略，因此也不等于每个阶段新建线程。[CompletableFuture 执行策略说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CompletableFuture.html)。

<a id="method-8-2"></a>

### 7.1 supplyAsync(...)：启动一个产生结果的阶段【重点】

#### 方法接收的是 Supplier

`supplyAsync()` 适合开启整条阶段链的第一步，例如计算价格、查询数据。其主要形式是：

```java
static <U> CompletableFuture<U> supplyAsync(
    Supplier<U> supplier, Executor executor
)
```

`Supplier<U>` 表示“不需要输入参数、可以提供一个 U 类型结果”的函数对象，执行入口是 `get()`。这里的 `Supplier.get()` 是业务计算函数，与 `Future.get()` 等待结果的方法含义不同。

例如 `() -> 100` 是一个 Supplier，执行它会得到整数 100。传入 `pool` 决定这份计算如何调度。

#### 内部从空结果到完成结果

概念上，`supplyAsync()` 先创建一个尚未完成的 CompletableFuture，再把运行 Supplier 的包装任务交给执行器：

```text
创建未完成结果对象
        ↓
把包装任务交给 executor.execute()
        ↓
执行器安排运行 Supplier.get()
    ├─ 返回值 → 正常完成结果对象
    └─ 抛异常 → 异常完成结果对象
        ↓
触发已注册、满足条件的后续阶段
```

调用方法自身不是在等待 Supplier 返回结果。普通池成功接收时，调用者拿到结果对象后可继续处理其他事情。任务很快时，也可能在方法返回前已经完成；方法返回的先后不能用来判断任务一定处于哪一状态。

#### 基础示例逐行解释

```java
ExecutorService pool = Executors.newFixedThreadPool(2);
try {
    CompletableFuture<Integer> price =
        CompletableFuture.supplyAsync(() -> {    // ①
            System.out.println("计算价格："
                + Thread.currentThread().getName());
            return 100;                         // ②
        }, pool);                               // ③

    System.out.println("可以处理其他工作");       // ④
    int value = price.join();                   // ⑤
    System.out.println("价格：" + value);         // ⑥
} finally {
    pool.shutdown();
}
```

① 定义提供整数的函数；②把 100 保存为 price 的正常结果；③明确使用该线程池调度第一步。④由提交者执行，与计算线程的输出顺序不固定。⑤在调用者需要结果的位置等待。⑥成功取得结果后，确定打印价格 100。

示例使用 `join()` 便于聚焦执行顺序；它的异常、中断与阻塞边界在 [CompletableFuture.join()](#method-8-14) 中单独解释。省去受检异常声明不代表可以忽略失败。

#### 不传入执行器会怎样

`supplyAsync(supplier)` 没有指定业务池。普通 CompletableFuture 默认使用公共 `ForkJoinPool.commonPool()`；如果公共池不支持至少为 2 的并行度，则使用备用的新线程机制。公共池是共享执行资源，不是每条业务链专属的线程池。

方法的 Async 说明任务通过异步执行机制安排，不保证每次新建线程。明确传入同步执行器 `Runnable::run` 时，Supplier 会在调用者中执行；传入饱和池并使用调用者执行的拒绝策略时，也可能产生类似行为。

阻塞任务如果长期占用共享执行资源，其他工作可能得不到及时执行。应用可以显式传入与工作性质相匹配的执行器，并对任务时长与积压作实际控制，而不是只凭 Async 后缀认定不存在阻塞。

Future 的泛型也不意味着把结果对象深复制给各个阶段。假如 Supplier 返回一个可变列表，后续得到的可能仍是同一个列表引用。不同线程随后同时修改它，仍需要合适的共享数据设计。阶段机制负责确定一次完成结果和传递依赖，不负责把任意业务对象转成并发容器。

#### 异常与取消边界

任务中的异常使 price 异常完成，后续正常转换步骤通常不会执行。执行器在提交阶段拒绝任务，也可能直接从 `supplyAsync()` 抛出异常，应与“后台计算已经启动后失败”区分。

`Supplier.get()` 不声明受检异常。包含文件等操作时，需要在函数内部按业务处理或用 `CompletionException` 包装后抛出，不能通过编译后就认为错误处理完成。

另外，对 price 调用 `cancel(true)` 会改变 CompletableFuture 的取消状态，但这里的 true 不表示向 Supplier 所在线程发中断。需要可停止的实际工作时，应另外设计任务取消机制，不能照搬普通 FutureTask 的解释。

<a id="method-8-3"></a>

### 7.2 thenApply(fn)：把上一步的值转换成新的值【重点】

#### 它适合“一份输入，普通结果输出”

第一步得到整数价格，下一步需要生成展示文字，转换关系就是 `Integer → String`。`thenApply()` 注册这种转换：上游正常完成后，把结果传入函数，函数的返回值成为新阶段的结果。

```java
CompletableFuture<Integer> price =
    CompletableFuture.supplyAsync(() -> 100, pool);

CompletableFuture<String> text = price.thenApply(value -> { // ①
    return "价格：" + value + " 元";                        // ②
});

System.out.println(text.join());                           // ③
```

①不是提前取得一个值再调用普通方法，而是把函数登记为 price 的依赖动作。②中的 value 是 price 正常完成的整数 100，返回字符串。③取得 text 的结果，因此得到“价格：100 元”。price 仍保存整数 100，没有被改成字符串。

#### 方法内部怎样等上游完成

内部首先准备新的结果对象，并记录“哪一个上游、哪一个转换函数、哪一个下游结果”的关系。如果上游尚未完成，就登记依赖动作并返回新阶段；上游完成时触发动作。如果上游已经正常完成，可能直接执行转换，无需建立长期等待。

```text
price 尚未完成 → 登记转换关系 → 返回未完成的 text
                         ↓ price 正常完成
                 fn(100) → 保存 "价格：100 元" → text 正常完成

price 异常完成 → 不调用 fn → text 异常完成
fn 自己抛异常 → price 仍然正常 → text 异常完成
```

因此，在未完成上游上注册关系，通常不需要调用者阻塞等结果；如果上游已完成，转换可能在本次 `thenApply()` 调用里立即执行，调用者会花费函数运行时间。

#### 转换在哪条线程上运行

以下代码可以用于观察，但不能把某一次输出当成全部场景的规律：

```java
CompletableFuture<Integer> price =
    CompletableFuture.supplyAsync(() -> 100, pool);

CompletableFuture<String> text = price.thenApply(value -> {
    return Thread.currentThread().getName() + "：" + value;
});
```

如果工作线程完成 price 时，转换已经注册，它可能接着执行转换。如果 price 在注册前已完成，注册动作的线程可能立即执行转换。注册与完成还可以并发竞争，因此应把 `thenApply()` 看成没有固定线程归属的步骤。

希望通过指定池调度转换时，可以使用表格中的 `thenApplyAsync(fn, executor)`。不过函数若执行很久或阻塞 I/O，仍占用执行它的线程；Async 不会把函数内部的阻塞代码变成非阻塞代码。

#### 常见错误：把失败当成普通输入

```java
CompletableFuture<Integer> failed =
    CompletableFuture.supplyAsync(() -> {
        throw new IllegalStateException("查价失败");
    }, pool);

CompletableFuture<String> text = failed.thenApply(
    value -> "价格：" + value
);
```

失败时并不是把 null 传给转换函数；正常转换函数不执行，text 随上游异常完成。需要失败时生成备用值、状态对象或记录错误，应使用统一表格中的异常处理方法，并定义清楚“备用值”的业务含义。

另一个错误是修改共享对象后，认为“链式方法保证所有访问安全”。阶段链只为这些阶段间的完成与数据传递建立关系，不能替任意外部线程的并发读写提供互斥保护。原 Future 的完成状态不变，也不代表其结果对象内容不会被函数中的代码修改。

<a id="method-8-7"></a>

### 7.3 thenCompose(fn)：把依赖前一步的异步任务接到链上【重点】

#### 为什么 thenApply 有时不够

查询用户详情需要先知道用户编号，而“查询详情”自身也返回 CompletableFuture。这时转换关系不是 `编号 → 详情`，而是 `编号 → 另一个待完成的详情任务`。

如果使用 `thenApply()`，函数的普通返回值恰好是一个 Future，所以会得到两层嵌套：

```java
CompletableFuture<String> userId =
    CompletableFuture.supplyAsync(() -> "U100", pool);

CompletableFuture<CompletableFuture<String>> nested =
    userId.thenApply(id ->
        CompletableFuture.supplyAsync(() -> "用户详情：" + id, pool)
    );
```

外层 Future 完成只证明“已拿到内层 Future 对象”，内层代表的详情计算仍可能没有结束。此时 `nested.join()` 返回的是另一份 Future，调用者还要再处理一层。

`thenCompose()` 把内层阶段连接进当前链，让返回的阶段直接代表内层最终结果。

#### 用同一个业务流程对照

```java
CompletableFuture<String> userId =
    CompletableFuture.supplyAsync(() -> "U100", pool);       // ①

CompletableFuture<String> detail = userId.thenCompose(id -> { // ②
    return CompletableFuture.supplyAsync(                    // ③
        () -> "用户详情：" + id,
        pool
    );
});

System.out.println(detail.join());                          // ④
```

①启动查询编号的阶段。②注册依赖关系，必须有正常编号后才调用函数。③利用实际编号启动查询详情，并返回内层 CompletableFuture。④等待的是内层详情也完成后的最终结果，打印“用户详情：U100”。返回类型只有一层 `CompletableFuture<String>`。

示例用立即返回的字符串模拟查询，以便观察关系。实际应用可以把①、③替换为耗时查询，依赖顺序不变。

#### 内部连接的是什么

`thenCompose()` 创建新的结果对象。上游正常完成时，它执行传入函数，取得内层阶段，再把“内层完成后如何完成新结果”连接起来。内层如果尚未完成，就登记结果传递动作；如果已经完成，就可以传递现有结果。

```text
查询编号 A
   ↓ 正常得到 U100
调用函数，创建详情任务 B
   ↓ B 仍在执行时，最终阶段 C 仍未完成
B 正常结束 → C 正常得到详情
B 异常结束 → C 异常完成
```

机制通常不需要在函数里调用 B 的 `get()` 或 `join()`。代码先返回内层阶段，完成通知负责把它连到下游；这样表达依赖比占着工作线程等待内层结果更清楚。

上游 A 失败时，函数通常不执行，最终阶段也失败；函数本身抛异常，最终阶段失败；函数返回 null 不是有效的内层阶段，也会使依赖阶段失败。函数应该返回非 null 的 `CompletionStage`。

#### 必须避免的两种误解

第一，`thenCompose()` 不表示两步同时执行。B 需要 A 的编号，A 没得到结果就不能正确查询 B。这是有顺序的数据依赖。

第二，它不保证函数在新线程中执行。②中的函数属于非 Async 的依赖动作，可能在完成 A 的线程中执行。③之所以经池调度，是因为函数自己调用了 `supplyAsync(..., pool)`；如果函数在返回 Future 前先做一次长时间同步查询，那段查询仍会阻塞执行函数的线程。

`thenComposeAsync()` 可以经执行器调度这个函数，但也不改变“A 的结果出现后才能开始 B”的数据依赖。[CompletionStage 的 thenCompose 契约](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CompletionStage.html#thenCompose(java.util.function.Function))。

类型可以帮助判断选哪一个方法：函数返回的是普通详情对象，使用 `thenApply()`；函数返回的是“未来才能得到详情”的阶段对象，使用 `thenCompose()`。不能把所有返回 Future 的方法统一接成 `thenApply()`，再在每一层手工等待；那会把明确的数据依赖变成嵌套阻塞，也增加追踪异常和关闭时机的难度。

<a id="method-8-8"></a>

### 7.4 thenCombine(other, fn)：等待两份结果，再合并【重点】

#### 它适合两个来源共同决定一个结果

计算订单总价，需要商品金额和配送费。这两份数据可以独立查询，最终再相加。`thenCombine()` 接收另一份阶段和一个双参数函数，等两份结果都正常得到后，再调用合并函数。

它与 `thenCompose()` 的区别首先是依赖形状：

```text
thenCompose：A 的结果 → 用结果启动 B → 取得 B 的结果

thenCombine：A ─┐
              ├─ 两者正常完成 → 合并
             B ─┘
```

#### 逐行理解两份任务的提交

```java
CompletableFuture<Integer> goods =
    CompletableFuture.supplyAsync(() -> 100, pool);          // ①

CompletableFuture<Integer> shipping =
    CompletableFuture.supplyAsync(() -> 8, pool);            // ②

CompletableFuture<Integer> total = goods.thenCombine(        // ③
    shipping,
    (goodsValue, shippingValue) -> goodsValue + shippingValue // ④
);

System.out.println(total.join());                           // ⑤
```

①和②独立提交任务。②没有调用①的 `join()`，所以不存在“先等商品查询完成，再提交配送查询”的等待限制。③登记汇合关系。④只有两个阶段都正常完成后才运行，参数分别来自 goods 和 shipping。⑤取得 108。

goods 与 shipping 谁先完成不影响最终值。如果池有足够工作线程，它们可以同时执行；如果池只有一条线程，它们可能依次运行。并发能力来自提交和执行资源，`thenCombine()` 本身不增加线程，也不保证两个任务同时开始。

#### 内部怎样判断结果齐了

新阶段记录两个上游和合并函数。完成通知到达时，检查两个上游是否都有完成结果。少一份时，就不能正常合并；两份都正常时，取出值并调用函数，然后完成新阶段。

这不是要求工作线程调用两次阻塞的 `get()` 来凑齐结果。依赖完成机制可以在条件满足时触发动作。一个上游已经完成，也不需要把它重新执行。

如果合并函数抛异常，total 异常完成，已经成功的 goods、shipping 不会自动被改成失败。非 Async 的合并函数没有固定线程归属，可能在使条件最后满足的线程中运行，也可能在注册时条件已满足而立即运行。

#### 一个查询失败后，另一个会取消吗

不会自动取消。任何一个上游失败，正常合并函数都无法按约定运行，total 最终异常完成，但另一任务的执行生命周期并不由这个事实自动结束。

还不能把 `thenCombine()` 当作“任一失败立刻返回”的接口。普通 CompletableFuture 的二元合并机制需要处理两份完成状态，不能依赖它在另一份始终未完成时立即把失败传给调用者。两个任务都失败时，也不应依赖一个固定的异常选择顺序。失败、超时和取消的业务规则应另行定义。

此外，在①后立刻 `goods.join()`，再提交②，会把本可独立开展的查询变成串行。需要等两份结果才能用时，应先把独立任务提交出去，再注册汇合，在最终需要值的位置等待。

可以用耗时推演理解价值：商品查询约 200 毫秒、配送查询约 300 毫秒，具备足够执行资源且没有其他瓶颈时，独立开展再汇合的等待主要受较慢的查询影响；先等商品再提交配送，则两次等待依次发生。这只是理想推演，实际还包括排队、调度和服务争用，不能把估算写成精确时限保证。

<a id="method-8-14"></a>

### 7.5 join()：等待完成阶段，并取得最终值【重点】

#### 同名方法等待的对象不同

`Thread.join()` 等待一条线程结束，没有任务返回值。`CompletableFuture.join()` 等待一个结果对象完成，并返回该对象的结果。线程池中一条工作线程可以继续执行其他任务，因此阶段已完成不意味着工作线程应结束。

`join()` 也不负责启动阶段。它只观察并等待结果；如果程序创建了一个空 CompletableFuture，却没有任何执行路径会完成它，`join()` 就可能一直等下去。

```java
CompletableFuture<Integer> result = new CompletableFuture<>();
// 若没有其他路径调用 complete、completeExceptionally 等使其完成，
// result.join() 不会凭空生成结果。
```

#### 返回与异常如何判断

```text
阶段正常完成 → 返回保存的结果
阶段异常完成 → 通常抛 CompletionException，原因中包含实际失败
当前阶段被直接取消 → 抛 CancellationException
阶段尚未完成 → 当前调用线程等待
```

“当前阶段直接取消”与“上游取消导致下游异常完成”应区分：下游可能以 `CompletionException` 包含取消原因报告失败，不能把所有与取消有关的异常都认定为最外层 `CancellationException`。

```java
CompletableFuture<Integer> good =
    CompletableFuture.completedFuture(42);
System.out.println(good.join());               // ① 得到 42

CompletableFuture<Integer> bad = new CompletableFuture<>();
bad.completeExceptionally(new IllegalStateException("查询失败"));

try {
    bad.join();                               // ② 读取异常完成结果
} catch (CompletionException e) {
    System.err.println(e.getCause());          // ③ 原始失败原因
}
```

①使用已完成阶段，无需再等待。②没有启动新任务，只读取已确定的失败。③得到 `IllegalStateException`。`CompletionException` 是非受检异常，所以编译器不强制声明或捕获，但调用方仍必须有合适的失败处理边界。

#### join 与 get 的真正区别

| 对比项 | CompletableFuture.join() | CompletableFuture.get() |
|---|---|---|
| 正常结果 | 返回该阶段的结果 | 返回该阶段的结果 |
| 是否可能阻塞 | 是 | 是 |
| 任务失败 | 通常用非受检 `CompletionException` | 用受检 `ExecutionException` |
| 等待中的中断 | 不仅因为中断就用 `InterruptedException` 退出等待 | 可用 `InterruptedException` 退出等待 |
| 超时参数 | 无 | 有 `get(timeout, unit)` 重载 |

普通 OpenJDK CompletableFuture 在 `join()` 内部等待时会记录中断，并在等待完成后恢复中断标记。它不因为线程收到了中断就立刻放弃结果等待。因此，需要“调用者被中断后停止等待”的方法契约时，应考虑 `get()`，不能为少写 `throws` 而随意换成 `join()`。

这不意味着调用 `get()` 后任务就被取消：两种方法都在等待结果，是否取消实际计算仍需要另外处理。

#### 应该在哪里等待

阶段链适合先描述处理关系，最后在真正需要最终值的位置等待一次：

```java
CompletableFuture<String> display =
    CompletableFuture.supplyAsync(() -> 100, pool)
        .thenApply(value -> value + 8)
        .thenApply(total -> "应付：" + total);

String text = display.join();
```

这段代码把计算关系连接起来，最终等待得到“应付：108”。如果每注册一段就马上 `join()`，调用者反复阻塞，阶段链的组织价值会减弱。

更危险的是在锁内，或在只能提供少量线程的执行器任务中，阻塞等待另一个必须占用同样资源才能完成的阶段。`join()` 不会自动释放业务锁，也不能凭空增加执行资源；第 6 章的依赖等待问题仍然可能发生。超时完成方法也只改变结果状态，不自动撤销正在运行的外部操作。

学习时可将三个方法连起来复述：`supplyAsync()` 启动第一份计算，`thenApply/thenCompose/thenCombine` 表达结果关系，`join()` 在调用者需要值的位置取得最终结果。每一步再单独判断执行线程、异常传播和停止需求，才不会把“链写得短”误认为“并发行为已经清楚”。


<a id="sec-9"></a>

## 8. 原子类：把读取、判断和更新连成一次操作

共享变量最常见的错误，是把“单次读取没有问题”和“整个计算没有问题”混为一谈。假设库存只有一件，两个线程都先读取到 `1`，都认为可以购买，然后分别扣减，最终可能卖出两件。问题不在读取或写入的某一行，而在两行之间允许另一个线程插入执行。

**原子操作**是对其他线程而言不能被拆成半完成状态的一次操作。`AtomicInteger` 保护一个整数值，`AtomicReference` 保护一个对象引用。它们能协调一次值更新，但不会自动把扣库存、收款、发货组成一个事务。内部常借助 JVM 支持的原子指令及相应内存访问规则；不能把所有原子方法都理解成“偷偷加了 synchronized”。

阅读本章代码时，`get()` 表示读取当前值，`set()` 表示覆盖当前值。两者单独有相应的原子与可见性保证，但 `set(get() + 1)` 仍然是分开的两个调用。标准原子方法的访问语义可查阅 [Java 21 原子类说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/package-summary.html)。

<a id="method-9-3"></a>

### 8.1 AtomicInteger.compareAndSet(expected, update)：确认旧值仍有效，才写入新值【重点】

#### 解决什么问题

`compareAndSet` 常缩写为 **CAS，Compare-And-Set**。调用方先基于某个旧值计算候选新值，提交时再检查：共享变量现在是否仍等于那个旧值？如果一致，更新成功；如果已经变了，本次更新失败，旧计算结果不被强行写入。

```java
boolean changed = stock.compareAndSet(10, 9);
```

参数 `10` 不是“强制把库存先设为 10”，而是“本次操作要求当前库存等于 10”；参数 `9` 是满足要求后要保存的新值。方法比较的是整数数值。成功返回 `true`；不匹配返回 `false`，这次调用不修改库存。

#### 方法内部的概念流程

下面是帮助理解的流程，不是 JDK 的逐字源码：

```text
调用 compareAndSet(预期旧值, 候选新值)
    ↓
JVM 执行一次原子的“比较并条件写入”
    ├─ 当前值 == 预期旧值 → 写入候选新值 → 返回 true
    └─ 当前值 != 预期旧值 → 保持当前值   → 返回 false
```

读取比较和条件写入被绑定成一次原子操作。另一个线程不能在“比较通过”和“写入新值”之间修改这个变量，再被本次写入悄悄覆盖。`compareAndSet()` 本身不会在失败后自动重新读取并重试，也不会等待变量以后变成预期值；重试策略属于调用方。

#### 示例：多个线程抢最后一件库存

以下方法片段需要导入 `java.util.concurrent.atomic.AtomicInteger`：

```java
static boolean reserveOne(AtomicInteger stock) {
    for (;;) {
        int before = stock.get();                     // ①
        if (before <= 0) {                            // ②
            return false;
        }
        int after = before - 1;                       // ③
        if (stock.compareAndSet(before, after)) {     // ④
            return true;                              // ⑤
        }
        // ⑥ CAS 失败，回到循环顶部读取最新库存。
    }
}
```

① 读取一份当前库存的快照。它只是这次读取时的值，不保证下一行仍未变化。② 根据这份快照检查业务条件；没有库存便结束。③ 计算候选库存，尚未修改共享变量。④ 把“快照仍有效”作为写入条件。⑤ 只有成功提交更新的线程才能宣告预留成功。⑥ 失败后必须重新执行条件检查，不能只反复使用旧的 `before`。

库存为 `1` 时，一种执行交错如下：

| 顺序 | 线程 A | 线程 B | 实际库存 |
|---|---|---|---|
| 1 | 读取 `before = 1` | — | 1 |
| 2 | — | 读取 `before = 1` | 1 |
| 3 | CAS `(1, 0)` 成功 | — | 0 |
| 4 | — | CAS `(1, 0)` 失败 | 0 |
| 5 | 返回预留成功 | 重新读取 0，返回失败 | 0 |

两者曾经同时看到库存，但只有一个线程能成功把状态从 `1` 改为 `0`。这就是 CAS 循环的作用：**失败的不是整个线程，而是基于过期状态的一次提交。**

#### 错误写法及原因

```java
// 错误：判断与扣减分开，二者之间可以插入其他线程。
if (stock.get() > 0) {
    stock.decrementAndGet();
    return true;
}
```

`decrementAndGet()` 确实能原子减一，但没有把前面的“库存大于零”也纳入同一次更新。如果 A、B 都通过判断，再各自减一，库存会变成 `-1`。方法级别的线程安全无法自动拼成业务级别的线程安全。

另一个常见错误是把付款操作放进重试循环，在每次尝试前扣款。CAS 可能失败多次，付款便可能重复。即使把付款移到 CAS 成功之后，扣库存与付款仍不是跨系统事务，失败回滚和幂等控制需要业务设计。

#### 返回值和使用边界

`true` 只表示这次条件更新成功，不表示返回时变量仍保持新值；其他线程可以立即继续修改它。`false` 也不是异常，它是竞争中的正常结果。大量线程争抢同一个变量时，反复失败会消耗 CPU，某个线程也可能长时间无法成功；不能仅凭使用 CAS 就宣称每个调用都有固定完成时限。

CAS 只比较当前值，不能识别 `1 → 2 → 1` 的历史变化。如果业务需要判断“期间是否发生过修改”，需增加版本信息，详见下一节的引用 CAS 和 ABA。整数溢出也不由原子类解决。契约见 [AtomicInteger.compareAndSet](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicInteger.html#compareAndSet(int,int))。

<a id="method-9-10"></a>

### 8.2 AtomicInteger.updateAndGet(fn)：根据旧值计算新值，竞争时重新计算【重点】

#### 解决什么问题

只有“加一”时可以直接使用 `incrementAndGet()`，但实际状态转换可能是“增加 10，最多到 100”。普通的读取再设置会丢失更新。`updateAndGet()` 接收一个函数，把读取、计算和原子提交组织起来，并返回本次成功提交的新值。

```java
int result = progress.updateAndGet(old -> Math.min(100, old + 10));
```

这里的函数相当于一条规则：给定旧值 `old`，计算候选新值。它不是任意业务事务的回调，也不保证只执行一次。

#### 方法内部的概念流程

```text
读取旧值 old
    ↓
执行函数 fn(old)，得到候选新值 next
    ↓
尝试原子提交 old → next
    ├─ 成功 → 返回 next
    └─ 竞争导致失败 → 取得新状态，必要时重新计算并重试
```

不同 JDK 实现可能复用某些已计算结果或使用不同的原子访问方式，但调用契约明确允许由于竞争重新应用函数。因而代码必须按“函数可能执行多次”设计。[AtomicInteger.updateAndGet](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicInteger.html#updateAndGet(java.util.function.IntUnaryOperator))。

#### 示例：两次增加进度，不丢失其中一次

```java
AtomicInteger progress = new AtomicInteger(80);

// 两个线程都调用下面这条语句。
int committed = progress.updateAndGet(
    old -> Math.min(100, old + 10)
);
```

可能的交错是：A 读到 `80` 并算出 `90`；B 也读到 `80` 并算出 `90`；A 先提交成功；B 的提交失败，于是基于 `90` 重新计算 `100`，再提交成功。A 得到返回值 `90`，B 得到 `100`，最终进度是 `100`。B 的计算函数可能执行了两次，但 B 这次调用成功发布的状态更新只有一次。

这一返回值是本次提交时的新值，不能当成永远稳定的当前值。例如 A 拿到 `90` 后打印时，共享变量可能已经变成 `100`。`getAndUpdate()` 与此方法采用相同的重复计算约束，只是返回成功更新之前的值。

#### 为什么函数不能有副作用

副作用是指除了计算返回值，还改变外部状态，例如写数据库、扣款、发送消息、增加另一个计数器。

```java
// 错误：候选计算可能重复，通知也会跟着重复。
counter.updateAndGet(old -> {
    sendNotification();
    return old + 1;
});
```

如果一次竞争导致函数执行三次，即使 counter 只成功增加一次，也可能发送三条通知。函数抛异常时，本次原子更新尚未成功，不会发布这个候选值，但抛异常之前已经发生的通知或扣款不会自动撤销。

函数应当短小、可重复计算，尽量只依赖参数与稳定的规则。它也不应在内部修改同一个原子变量，使“输入旧值 → 输出新值”的关系难以推理。函数若耗时很长，竞争期间浪费的计算工作也会增多。

#### 本方法不能替代的业务约束

下面看似扣减库存的写法会把“没有库存”也当作一次正常返回：

```java
int remaining = stock.updateAndGet(old -> old > 0 ? old - 1 : old);
```

返回 `0` 可能表示刚刚成功扣掉最后一件，也可能表示库存原本就已经是零。需要明确返回“是否预留成功”时，上一节的 CAS 循环更直接。函数型原子更新适合计算新状态；业务成功与失败的含义必须由接口返回值完整表达。

<a id="method-9-13"></a>

### 8.3 AtomicReference.compareAndSet(expected, update)：一次替换整份对象引用【重点】

#### 解决什么问题

配置可能包含多个相关字段，例如最大连接数和超时时间。若逐字段修改，读取线程可能看到“新连接数配旧超时”的中间状态。可以把配置做成不可变对象，创建一份完整的新配置，再原子替换保存配置的引用。

`AtomicReference<T>` 保存的是指向对象的引用。CAS 比较的是**引用身份，相当于 `==`，不是 `equals()`**。内容一样的两个对象也可能是不同实例。[AtomicReference.compareAndSet](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicReference.html#compareAndSet(V,V))。

#### 示例：替换一份一致的配置

```java
import java.util.concurrent.atomic.AtomicReference;

public class ConfigCasDemo {
    record Config(int limit, int timeoutMillis) {}

    public static void main(String[] args) {
        Config original = new Config(10, 1000);
        AtomicReference<Config> holder = new AtomicReference<>(original);

        Config equalContent = new Config(10, 1000);
        boolean first = holder.compareAndSet(
            equalContent, new Config(20, 2000)
        );
        System.out.println(first); // false：内容相等，引用不同。

        Config observed = holder.get();
        boolean second = holder.compareAndSet(
            observed, new Config(20, 2000)
        );
        System.out.println(second); // 本例没有竞争，输出 true。
        System.out.println(holder.get());
    }
}
```

`original` 和 `equalContent` 的 record 内容相等，但 `original == equalContent` 为 `false`，第一次提交失败。第二次先通过 `get()` 取得实际保存的引用，提交时检查这个引用是否仍为当前配置；没有竞争，所以成功。

内部概念流程是：“读取当前引用身份 → 与 expected 比较 → 一致时原子改成 update → 返回成功或失败”。新对象应在发布前完成构造。读取方只读取一次 `holder.get()` 并使用同一个配置对象，可以避免两次读取之间配置更换导致字段来自不同版本。

#### 为什么原子引用不等于对象内部线程安全

```java
// 错误理解：引用被 AtomicReference 保存，不会让字段更新变成原子操作。
holder.get().someMutableField++;
```

如果配置是可变对象，这一修改并没有替换引用，甚至没有调用 CAS。多个线程仍然会同时访问该对象内部字段。稳妥的发布方式是用不可变对象表示一份状态，所有更新都构造新对象；record 的组件若是可变集合，也需要额外复制或限制修改，不能只看到 record 就认定深层不可变。

#### ABA 为什么会让检查失去历史信息

假设保存引用 `A`：线程 T1 读到 A，暂时停止；T2 把引用改成 B，之后又改回**同一个 A 实例**；T1 继续执行 `compareAndSet(A, C)`，比较仍通过。

```text
T1 观察到 A ───────────────────→ CAS(A, C) 成功
             T2：A → B → A
```

CAS 证明的是“此刻仍然是 A”，没有证明“这段时间从未变化”。配置直接替换时，这段历史可能不影响正确性；在无锁链表、资源回收等依赖历史的算法中，可能导致错误。需要检测历史时，可以把版本号和状态打包进不可变对象一起替换，或者使用 `AtomicStampedReference` 同时比较引用与版本戳。版本更新规则仍需设计，不能随意复用版本。

<a id="sec-10"></a>

## 9. 线程协调工具：区分结束、集合和进入名额

这些工具并不主要保护某个字段的读写，而是描述线程之间的执行关系。

| 工具 | 状态代表什么 | 典型关系 |
|---|---|---|
| `CountDownLatch` | 还有多少次完成信号没有收到 | 汇总线程等待若干任务结束 |
| `CyclicBarrier` | 本轮还有多少个参与者未到达 | 多个参与者互相等待，再进入下一阶段 |
| `Semaphore` | 现在还有多少个进入名额 | 最多 N 个操作同时访问受限资源 |

本章提到的 **AQS** 是 JDK 内部的同步器基础框架，可以先理解为“用一个状态值表达能否继续，并维护等待线程队列”。初学阶段需要掌握状态含义和方法行为，不必先记忆 AQS 的每个节点字段。

<a id="method-10-1"></a>

### 9.1 CountDownLatch.countDown()：登记一次完成信号【重点】

#### 场景与调用者

三个任务分别加载三份资料，汇总线程必须等三份任务都结束。`new CountDownLatch(3)` 保存剩余计数 `3`。每个任务结束时调用一次 `countDown()`，计数依次变成 `2、1、0`。达到零后，等待这个对象的线程才有条件继续。

`countDown()` 的调用者通常是工作线程；它不等待其他任务，也不返回处理结果，返回类型为 `void`。计数不是自动根据线程数得出的，而是构造时由程序指定。

#### 内部概念流程

```text
读取剩余计数
    ├─ 已经为 0 → 返回，不减成负数
    └─ 大于 0 → 原子尝试减 1
                   ├─ 竞争失败 → 重试状态更新
                   └─ 成功
                       ├─ 结果仍大于 0 → 返回
                       └─ 结果为 0 → 使等待者有机会继续
```

OpenJDK 21 以 AQS 状态保存计数，使用原子更新避免两个工作线程同时减一时丢掉其中一次。归零之后不再关闭这道门，之后的 `await()` 也可以直接通过。该计数器不能重置为新一轮的 `3`。[CountDownLatch API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CountDownLatch.html)。

#### 示例：任务结束与任务成功分别记录

以下代码片段中的 `done` 由所有工作任务与汇总线程共享；`successes` 为共享的 `AtomicInteger`：

```java
CountDownLatch done = new CountDownLatch(3);
AtomicInteger successes = new AtomicInteger();

Runnable worker = () -> {
    try {
        loadPart();                 // ① 可能成功，也可能抛异常。
        successes.incrementAndGet();// ② 只有成功后增加成功数量。
    } catch (RuntimeException e) {
        recordFailure(e);           // ③ 按业务要求保存失败原因。
    } finally {
        done.countDown();           // ④ 无论成功失败，登记本任务结束。
    }
};
```

`loadPart()` 和 `recordFailure()` 代表业务方法。将 `countDown()` 放在 `finally` 中，表示“这个任务的执行流程已经到达结束位置”。放在正常路径末尾，异常就可能跳过减一，使汇总线程永远等不到零。

归零后仍需要读取成功数量和失败记录。计数器只记录信号数量，没有结果对象，也不知道异常情况，所以 **计数归零不能证明三个任务全部成功**。

#### 错误写法及原因

同一个工作任务调用两次 `countDown()`，会提前消耗其他任务应该提供的信号。这个工具不记录“谁已经上报过”，无法防止重复报告。需要由任务结构保证一项任务恰好报告一次。

```java
// 错误：正常完成时一共减了两次。
try {
    loadPart();
    done.countDown();
} finally {
    done.countDown();
}
```

漏报也可能发生在任务尚未开始时，例如线程池拒绝提交。任务根本没有运行，就不会进入任务的 `finally`。调用方需要在提交失败时把这一项标为失败并完成对应计数，或者改用能直接收集提交结果的工具；不能依靠任务内部清理处理“任务未启动”。

<a id="method-10-2"></a>

### 9.2 CountDownLatch.await()：让当前线程等到计数归零【重点】

#### 谁被阻塞

`done.await()` 阻塞调用它的线程，通常是汇总线程。工作线程不因汇总线程的等待而停止，它们继续工作并调用 `countDown()`。`await()` 不会创建任务，不会主动减少计数，也不会替工作线程执行未完成任务。

签名为 `void await() throws InterruptedException`。计数为零便返回；计数不为零时等待；调用者被中断时可以抛出 `InterruptedException`，而不是继续等到全部任务完成。

#### 内部概念流程

```text
检查当前调用者的中断状态
    ↓
检查剩余计数
    ├─ 为 0 → 正常返回
    └─ 不为 0 → 登记为等待者，挂起调用者
                    ↓
            得到继续机会后重新检查
                    ├─ 计数为 0 → 正常返回
                    └─ 被中断   → 退出等待，抛中断异常
```

多个线程可以同时等待同一个 latch，计数归零后都会获得继续机会，而不是只放行一个。调用方无须自己编写 `while (getCount() != 0)` 的忙等待；同步器负责正确检查与等待。

#### 完整示例：等待后再读取各任务的结果

```java
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;

public class LatchResultDemo {
    public static void main(String[] args) throws InterruptedException {
        int[] results = new int[3];
        CountDownLatch done = new CountDownLatch(3);

        for (int i = 0; i < 3; i++) {
            final int index = i;
            new Thread(() -> {
                try {
                    results[index] = (index + 1) * 10; // ①
                } finally {
                    done.countDown();                 // ②
                }
            }, "loader-" + i).start();
        }

        done.await();                                 // ③
        System.out.println(Arrays.toString(results)); // ④ [10, 20, 30]
    }
}
```

① 每个任务只写自己负责的数组位置，没有两个任务争抢同一个元素。② 先写结果，再报告完成。③ main 等到三次报告。④ 正常通过等待后，main 可以观察到各工作线程在相应 `countDown()` 前的结果写入。

这是 latch 的内存可见性保证：工作线程先写数据再减计数，汇总线程正常通过对应等待后再读数据，写入不会仅因跨线程而不可见。若工作线程把写结果放在 `countDown()` 后，就不能套用这条保证；若限时等待返回 `false`，也不能假装所有结果已写完。

#### 中断、超时与业务锁

`await(timeout, unit)` 返回 `true` 表示已等到零，返回 `false` 表示此次等待超时。超时和调用者被中断，都不会自动取消工作任务。任务和它们使用的文件、连接等资源是否还在使用，需要另行管理。

`await()` 不会释放调用方外部持有的锁。若汇总线程拿着某把业务锁等待，而工作线程必须获取这把锁才能完成并调用 `countDown()`，双方就相互等待。等待应安排在没有这种依赖的位置。

还应避免把“等待任务结束的父任务”和它的子任务提交到只有一个工作线程的同一线程池：父任务占住唯一线程后等待，子任务都在队列中无法开始，计数永远不归零。这是执行资源不足导致的等待死锁，不是 latch 计数失效。

<a id="method-10-4"></a>

### 9.3 CyclicBarrier.await()：登记本轮到达，等所有参与者集合【重点】

#### 与 CountDownLatch 的关系区别

三个计算线程都必须先完成第一轮，再统一开始第二轮。这里需要每个计算线程都等另外两个，而不是单独一个汇总线程等任务结束。`CyclicBarrier(3)` 表示每轮需要三次到达；每个参与者完成当前阶段后调用 `await()`。

名称中的 Cyclic 表示正常通过一轮后可以再次使用。下一轮仍要三次到达，计数会在轮次推进时恢复。构造参数表示参与数量，不绑定特定线程身份；同一协议仍需保证每个逻辑参与者在一轮只报告一次。

#### 内部概念流程

OpenJDK 21 用锁保护当前轮次、剩余到达数等状态，并用条件等待集合结果。可按下面的流程理解：

```text
进入屏障内部的同步区域
    ↓
检查本轮是否已破坏、当前线程是否被中断
    ↓
剩余未到达数减 1，得到到达序号
    ├─ 尚有人未到达 → 等待本轮推进，等待期间释放内部锁
    └─ 自己是最后到达者
          ↓
        执行可选的汇合动作
          ↓
        唤醒本轮等待者，建立下一轮，正常返回
```

正常返回的 `int` 是本轮的**到达序号**：最后到达者为 `0`，第一个到达者为 `parties - 1`。它不是固定线程编号，同一个线程不同轮的序号可能不同。

#### 完整示例：两轮集合

```java
import java.util.concurrent.BrokenBarrierException;
import java.util.concurrent.CyclicBarrier;

public class BarrierRoundsDemo {
    public static void main(String[] args) throws InterruptedException {
        CyclicBarrier barrier = new CyclicBarrier(3,
            () -> System.out.println("本轮三个线程已集合"));
        Thread[] workers = new Thread[3];

        for (int i = 0; i < workers.length; i++) {
            final int id = i;
            workers[i] = new Thread(() -> {
                try {
                    for (int round = 1; round <= 2; round++) {
                        System.out.println(id + " 完成第 " + round + " 轮计算");
                        int index = barrier.await();
                        System.out.println(id + " 通过屏障，到达序号 " + index);
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                } catch (BrokenBarrierException e) {
                    System.err.println("屏障已破坏，本任务停止后续轮次");
                }
            }, "participant-" + i);
            workers[i].start();
        }
        for (Thread worker : workers) {
            worker.join();
        }
    }
}
```

三个线程的“完成第一轮”输出顺序不确定，但正常情况下，第一轮汇合动作执行后，各参与者才能通过这轮 `await()`。汇合动作由最后到达线程执行，不另建一个专用线程。某个线程可能已打印第二轮计算，而另一个线程还没来得及打印“通过第一轮”，这不违反屏障规则：后者已经完成第一轮，只是日志执行较慢。

屏障为阶段之间提供可见性关系：参与者在到达之前完成的操作，可以按该工具的规则传递给汇合动作以及通过相应屏障之后的操作。它不允许任意线程在任意时刻无同步读取其他线程正在改写的数据。[CyclicBarrier API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CyclicBarrier.html)。

#### 为什么会出现 BrokenBarrierException

本轮采用整体失败的语义。等待中的参与者被中断、限时等待超时，或者汇合动作抛异常，都可能破坏当前屏障轮次。其他等待者会收到 `BrokenBarrierException`，不能继续假定所有参与者完成了正常集合。

超时的线程通常得到 `TimeoutException`，被中断的线程通常得到 `InterruptedException`，其他参与者看到的是屏障被破坏。汇合动作抛出的异常由最后到达者面对，其他等待者面对破坏结果。并发情况下这些事件会竞争，程序应以实际异常和整体失败协议处理，而不是强求某种输出顺序。

一个参与者在调用 `await()` 之前就发生业务异常，并不会自动通知屏障。其他人仍可能无限等待。这时需由业务层统一安排超时、取消或屏障失败处理，不能认为 CyclicBarrier 自动监控所有参与者的存活。

#### 最常见的错误：参与者比可运行线程多

固定线程池只有两个工作线程，却提交三个都要调用 `barrier.await()` 的任务。前两个占满线程池后等待第三个，第三个一直在队列里，没有工作线程执行。

```text
工作线程 1：任务 A 等待 C 到达
工作线程 2：任务 B 等待 C 到达
任务队列：任务 C 等待空闲工作线程
```

屏障不会创建额外工作线程解决这个循环。并发参与者必须实际有足够执行机会，或者改用不会占用全部执行资源的阶段编排。`reset()` 也不能盲目调用来“解决卡住”；旧参与者与新一轮混在一起会破坏业务阶段，恢复需要所有参与者共享一致的轮次策略。

<a id="method-10-7"></a>

### 9.4 Semaphore.acquire()：取得一个进入名额，名额不足时等待【重点】

#### 许可是什么

某服务只允许最多三个同时进行的上传，可以创建 `new Semaphore(3)`。这里的 **许可** 是进入受限区的名额，不是某个文件、连接或线程对象。有三个许可意味着三个遵守协议的调用者可以同时进入。

每次成功 `acquire()` 消耗一个许可；`release()` 增加一个许可。构造许可数不必等于工作线程数，例如十个工作线程可以竞争三个进入名额。

#### 内部概念流程

```text
检查当前线程中断状态
    ↓
尝试原子减少可用许可数
    ├─ 许可足够且提交成功 → 返回，已取得名额
    └─ 许可不足/需要等待顺序 → 进入同步器等待队列
                                ↓
                         挂起，得到机会后重试
                                ├─ 成功取得 → 返回
                                └─ 被中断   → 抛 InterruptedException
```

OpenJDK 21 使用 AQS 共享模式维护许可与等待者。“共享模式”表示可以允许不止一个线程同时取得许可，不代表线程之间任意共享的数据自动受到保护。成功返回时只说明取得了许可，没有任何业务操作被自动执行。[Semaphore API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Semaphore.html)。

#### 标准写法以及 acquire 的位置

```java
static final Semaphore UPLOAD_SLOTS = new Semaphore(3);

static void uploadFile() throws InterruptedException {
    UPLOAD_SLOTS.acquire();       // ① 先成功取得，才能进入 try。
    try {
        performUpload();         // ② 受限制的业务操作。
    } finally {
        UPLOAD_SLOTS.release();  // ③ 归还本次取得的一个许可。
    }
}
```

`performUpload()` 代表上传业务。① 可能直接成功，也可能等待，并响应中断。② 正常返回或抛异常都要经过③，避免许可泄漏。把 `acquire()` 放在 `try` 前，是为了确保没有取得许可的路径不执行归还。

```java
// 错误：获取被中断时，finally 仍可能增加一个本不属于本次操作的许可。
try {
    UPLOAD_SLOTS.acquire();
    performUpload();
} finally {
    UPLOAD_SLOTS.release();
}
```

如果获取必须放在同一个 `try` 中，应保存 `acquired` 标记，并仅在标记为真时归还。使用 `tryAcquire()` 的限时获取同样如此：只有返回 `true` 的路径才释放。

#### 示例交错：许可数为 2

初始可用许可为 `2`。A 取得后变成 `1`，B 取得后变成 `0`，C 调用时等待。A 完成并释放后，可用名额增加，C 才有机会成功取得。A 释放不会保证 C 在同一瞬间开始运行，操作系统调度和其他竞争仍会影响执行时间。

许可约束只覆盖“成功 acquire 与对应 release 之间”的区间。若某个调用者跳过 `acquire()` 直接执行上传，这个工具无法强制拦截它；若获取后立即释放，再执行上传，真正的上传过程就不受并发数量限制。

#### 公平、多个许可与资源对象

`new Semaphore(3, true)` 按同步器规定的到达顺序提供公平获取，通常有助于减少插队，但不保证应用中的调用时刻、日志顺序或 CPU 调度顺序完全一致。无参 `tryAcquire()` 可以直接抢走当前可用许可，即使信号量设置为公平。

`acquire(n)` 要求一次取得 n 个许可，不会先拿一部分再等另一部分。大的请求可能等待很久，也可能影响较小请求推进。若实际有三条数据库连接，Semaphore 只管理“最多几个使用者”，还必须用另一个结构分配具体连接并防止两个调用者拿到同一条连接。

<a id="method-10-10"></a>

### 9.5 Semaphore.release()：增加许可，允许等待者重新争取【重点】

#### 归还不是持有者校验

`release()` 返回 `void`，将可用许可增加一，并让等待线程有机会继续获取。它并不检查调用者是否曾经执行过 `acquire()`，也没有互斥锁中的 owner，即“当前持有锁的线程”记录。

这使信号量可以用于跨线程交接：A 获取许可，B 在另一个流程完成时释放。但应用必须建立正确的交接协议，确保恰好释放一次。作为并发数量限制时，最容易推理的方式仍是同一任务取得并在 `finally` 中归还。

#### 内部概念流程

```text
读取当前可用许可数
    ↓
原子地尝试加 1，竞争失败则重试
    ↓
更新成功后通知等待者有新机会
    ↓
等待者重新检查许可并按获取规则争取
```

释放后的等待者不必马上运行；其他符合规则的调用者也可能取得许可。释放动作与后续成功获取之间存在该同步器规定的内存可见性关系，但这不等于为多个并发持有者提供互斥访问。

#### 错误示例：把初始许可数误当成上限

```java
Semaphore semaphore = new Semaphore(2);
semaphore.release();
System.out.println(semaphore.availablePermits()); // 3
```

构造参数 `2` 是初始数量，不是自动强制的最大数量。若 A 获取一次却释放两次，系统会多出一个名额，未来可能同时进入三个任务。相反，漏掉一次释放会永久少一个名额，最终所有新任务都可能卡在获取阶段。

`availablePermits()` 可帮助监控，但它只是读取时的快照，不应用“先查有名额，再直接执行”代替获取。检查结束后，另一个线程就可能已经消耗该许可。

#### 结束实际资源使用之后再归还

```java
// 不恰当：许可先释放，但昂贵操作还没结束。
semaphore.acquire();
try {
    startAsyncUpload();
} finally {
    semaphore.release();
}
```

如果 `startAsyncUpload()` 只提交后台工作便立即返回，这里限制的是“同时提交上传请求的数量”，没有限制“后台实际进行的上传数量”。要限制实际上传，许可生命周期应覆盖异步上传真正完成的时间，并在成功、失败与取消路径统一归还。Semaphore 不会自行跟踪后台任务。

二值信号量也不能完全等同于 `ReentrantLock`：许可为一只表达一个进入名额，没有锁的持有者检查和可重入计数。[Semaphore.release](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Semaphore.html#release())。

<a id="sec-11"></a>

## 10. LockSupport：让线程暂停，把条件判断留给同步协议

`LockSupport` 是锁、同步器等高层工具内部常用的等待基础。它为每个使用它的线程关联最多一个许可：`unpark(thread)` 使该线程有许可，`park()` 有许可就消耗并返回，没有许可则可能等待。许可是线程级状态，不属于某个特定业务对象，两个完全不同的等待协议也可能使用同一线程的许可。

**业务条件与许可是两件事。** 条件表示“结果真的准备好了”；许可表示“这次暂停可以结束或不必开始”。可靠协议依靠 `volatile`、原子变量或锁维护条件，依靠 park 减少无意义的循环消耗。不能只记忆“park 睡眠、unpark 唤醒”，就认为线程协作已经完整。[LockSupport API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/LockSupport.html)。

<a id="method-11-1"></a>

### 10.1 LockSupport.park()：暂停当前线程，返回后仍需检查条件【重点】

#### 调用者和返回含义

`park()` 是静态方法，暂停的是当前执行到这行的线程。方法不接收目标线程参数，不能从 main 调用 park 来暂停 worker。它不要求持有某个对象的监视器，也不自动释放当前线程持有的业务锁。

返回类型是 `void`，既不返回“准备完成”，也不返回唤醒原因。它可能因已有许可、其他线程的 unpark、当前线程中断或虚假唤醒而返回。因此“park 返回”只表示这次暂停结束，不能推出某个外部结果已经存在。

#### 内部概念流程

```text
当前线程调用 park()
    ├─ 已有可用许可 → 消耗许可，直接返回
    ├─ 已有中断状态 → 可以直接返回
    └─ 没有可用许可 → 可能挂起线程
                         ↓
                 unpark / 中断 / 虚假唤醒
                         ↓
                 返回，由调用者重新判断条件
```

底层机制由 JVM 处理；Java 业务代码不应直接用上面的概念分支推测每个竞争瞬间的许可值。尤其是中断与 unpark 同时发生时，应检查业务条件和中断，而不是自行猜测哪个事件先发生。

#### 示例：一个等待者等待一次开门

下例只是展示条件与许可如何配合的单等待者协议，不是通用的 CountDownLatch 替代品：

```java
import java.util.concurrent.locks.LockSupport;

public class ParkGateDemo {
    static final class Gate {
        private volatile boolean open;
        private int value;

        boolean awaitOpen() {
            while (!open) {                          // ①
                if (Thread.currentThread().isInterrupted()) {
                    return false;                    // ②
                }
                LockSupport.park(this);              // ③
            }
            return true;
        }

        void openFor(Thread waiter) {
            value = 42;                              // ④
            open = true;                             // ⑤
            LockSupport.unpark(waiter);              // ⑥
        }

        int value() {
            return value;
        }
    }

    public static void main(String[] args) throws InterruptedException {
        LockSupport.unpark(null); // 提前初始化这个等待工具；不操作任何线程。
        Gate gate = new Gate();
        Thread worker = new Thread(() -> {
            if (gate.awaitOpen()) {
                System.out.println(gate.value());    // 42
            }
        }, "gate-waiter");
        worker.start();
        gate.openFor(worker);                        // 在 start() 之后开门。
        worker.join();
    }
}
```

① 用 `while` 验证门是否已经打开；任何一次返回都要重新检查。② 按本示例的取消契约，中断尚未开门的等待者时退出，并保留中断标记。③ 真正没有条件继续时才暂停，`this` 只是说明阻塞原因的对象，方便诊断，并不是被获取的一把锁。④ 先写实际数据。⑤ 再通过 volatile 条件发布准备完成的状态。⑥ 给已启动等待线程一个继续机会。

读取线程观察到 `open == true` 后，可以根据 volatile 发布关系读取此前写入的 `value`。数据可见性来自这个明确的条件发布，不应仅归因于“执行了一次 unpark”。若门已打开，worker 直接通过条件检查，也不需要 park。

#### 为什么必须循环检查

错误写法是暂停一次就直接消费结果：

```java
// 错误：中断和虚假唤醒同样能使这一行返回。
LockSupport.park();
useResult();
```

结果尚未准备时发生中断，线程也会继续执行 `useResult()`。即使没有中断，也不能排除虚假唤醒。正确结构把“能否继续”放在循环条件中，把 park 当作降低等待成本的手段。

#### 中断标记与外部锁的边界

park 不抛 `InterruptedException`，返回也不自动清除中断标记。如果代码一直 `while (!ready) park()`，却永不处理已设置的中断标记，后续 park 可能立即返回，循环迅速消耗 CPU。程序应明确选择可中断退出，或者暂时记录并清除标记、完成不可中断等待后再恢复；后一种是高层同步器的实现策略，业务代码不宜随意照搬。

持有 `synchronized(lock)` 时调用 park，lock 不会因此释放。负责设置条件并 unpark 的线程若也需要这把锁，就可能永远无法推进。相比之下，`Object.wait()` 按其契约释放调用对象的监视器，这是二者机制上的重要区别。

<a id="method-11-4"></a>

### 10.2 LockSupport.unpark(thread)：为已启动目标线程提供一次继续机会【重点】

#### 许可如何解决检查与暂停之间的空隙

等待线程 T1 先检查 `ready == false`，随后准备 park。T2 恰好在这两步之间完成工作并 unpark(T1)。如果只靠“唤醒当前已经睡着的人”，T1 还没有暂停，通知就可能丢失。

LockSupport 通过许可避免这种常见空隙：当目标已经启动而尚未 park，unpark 可以先留下许可；目标之后 park 就消费许可并返回。目标已经 park 时，unpark 使它有机会恢复。T1 恢复后还要重新检查安全发布的 ready 条件。

```text
T1：读到 ready = false ──→ 准备 park ──→ 消费已有许可 ──→ 重检 ready
T2：                       写 ready = true → unpark(T1)
```

#### 方法的概念流程

`unpark(thread)` 将目标线程的许可变为可用状态；已有许可就保持可用；如果线程正在等待 park，则使其有机会退出这次等待。调用者本身不会因此暂停，也不等待目标执行到下一行。参数是目标线程对象，不是用于 wait/notify 的共享锁对象。

传入 `null` 无效果。对尚未启动的线程，API 不保证此调用有效，所以不能用下面的写法建立协议：

```java
// 不可靠：未启动线程的 unpark 不保证形成之后可消费的许可。
LockSupport.unpark(worker);
worker.start();
```

前一节代码明确先 start，再设置条件并 unpark。即使 worker 尚未到达等待位置，它也可以读取到条件已满足而直接继续。

#### 一个许可不能变成多条通知

```java
LockSupport.unpark(worker);
LockSupport.unpark(worker);
```

若 worker 尚未消费许可，这两次调用仍最多留下一个许可。不能期望 worker 之后执行两次 park 都不等待。它不是消息队列，也不是 `Semaphore(0)` 那样可以累积计数的工具。

同理，许可不记录“这个 unpark 属于哪次业务事件”。如果同一线程的其他工具或中间操作执行了 park，可能消耗原本为自定义协议留下的许可。官方特别指出，检查条件与 park 之间的间接阻塞，包括类加载路径，可能使自行实现的协议失去原本的继续机会。示例提前初始化等待工具，并保持关键间隙没有额外日志、加锁等操作；生产系统一般应优先使用成熟的 latch、队列或条件对象。

#### 为什么先发布条件，再 unpark

将顺序反过来会出现问题：

```java
// 错误顺序：等待者可能醒来发现条件仍为 false，再次 park。
LockSupport.unpark(worker);
ready = true;
```

如果 worker 在第一行之后立即醒来，重检发现 false，又进入 park；第二行只设置条件，没有新的继续机会，可能长时间等待。正确协议先安全发布 `ready = true`，再 unpark，使等待者在获得继续机会后能验证真实条件。

unpark 也不是对任意暂停方式都有效的通用“唤醒”按钮。不能靠它终止 `Thread.sleep()` 或满足 `Object.wait()` 的通知协议；那些方法有自己的结束条件。契约见 [LockSupport.unpark](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/LockSupport.html#unpark(java.lang.Thread))。

<a id="sec-12"></a>

## 11. BlockingQueue：让生产者和消费者通过队列交接数据

生产者生成订单，消费者处理订单。队列把两者执行速度分开：生产者不必等待每张订单处理完成，只需把订单放入；消费者取到订单后执行自己的工作。有界队列还表达了容量限制，防止处理不过来时无限堆积。

`BlockingQueue` 是接口，不同实现的内部结构并不一样。以下机制以容量固定的 `ArrayBlockingQueue` 为例：一个数组保存元素，一把 `ReentrantLock` 保护内部状态，两个 `Condition` 分别代表“队列不为空”和“队列未满”。条件等待能暂时释放这把内部锁，让另一方改变空满状态。

队列保证单次入队、取出等操作的并发正确性，不会自动保证消费后的付款、数据库提交等业务成功。[BlockingQueue API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/BlockingQueue.html)。

<a id="method-12-1"></a>

### 11.1 BlockingQueue.put(element)：有空间就放入，满时等待空间【重点】

#### 方法做什么

签名是 `void put(E element) throws InterruptedException`。成功返回时，该元素已经通过本次调用入队；方法不返回处理结果，也不等待消费者处理完。

满队列时，阻塞的是调用 put 的生产者线程。不能理解成“队列本身卡住了”，其他消费者仍可以取元素，从而空出位置。所有 BlockingQueue 都不接收 null；null 在部分查询接口中被保留用来表示没有元素。

#### 以 ArrayBlockingQueue 为例看内部步骤

```text
可中断地取得队列内部锁
    ↓
while 队列已满：
    在 notFull 条件上等待，暂时释放内部锁
    得到继续机会后重新获取内部锁，再检查是否仍满
    ↓
把元素保存到下一个写入位置
    ↓
推进写入位置，增加元素数量
    ↓
通知 notEmpty 上的等待消费者有机会继续
    ↓
释放内部锁，put 返回
```

等待期间释放的是队列内部的锁。若生产者在某个外部 `synchronized` 中调用 put，外部锁仍然由它持有。消费者需要这把外部锁才能 take 时，可能无法腾出空间，双方就会相互等待。

`while` 重新检查满状态，是因为被通知只表示“可能有空间”，其他生产者可能先抢到空位，或者条件等待发生虚假唤醒。队列的实现已经正确封装这一步，调用方不需要自己用 `size()` 预判。

#### 示例交错：容量为 1

```text
P 放入订单 A → 队列 [A]，put(A) 返回
P 再放订单 B → 队列已满，P 等待
C 取走订单 A → 队列 []，通知等待空间的线程
P 得到机会   → 放入订单 B，put(B) 返回
C 处理订单 A → 此时 B 已经可以在队列中等待
```

第二次 put 等的是可用位置，不是 A 的处理完成。如果消费者先 take 再耗时处理，位置在取出时已经释放，生产者就可以继续。

#### 错误写法与替代选择

```java
// 不必要且不可靠：size() 与后续操作之间可以发生并发变化。
if (queue.size() < capacity) {
    queue.put(order);
}
```

即使检查通过，其他线程也可能先放入元素，put 仍会等待；检查发现已满时，下一瞬间也可能有位置，直接放弃可能漏掉机会。需要一直等空间用 put；需要当前没有容量就放弃用 `offer(element)` 并检查布尔结果；需要限时等空间用带 timeout 的 offer。

`offer(element)` 不因为队列满而等待容量，但内部实现仍可能竞争队列锁。因此“非等待容量”不等于“实现完全无锁”或“任何情况下固定耗时返回”。

#### 中断与所有权交接

等待被中断时，put 可以抛出 `InterruptedException`。调用方必须处理“订单可能尚未进入队列”这一业务状态，而不是打印异常后假装提交成功。正常返回才是该调用完成入队的明确结果。

入队之前对对象的写入，可以通过队列的交接关系被取到该对象的消费者观察到。但入队并不复制整个对象。生产者把同一个可变对象放入后继续无同步修改，仍可能与消费者竞争。通常应使用不可变消息，或者约定入队之后生产者不再修改这份对象。

<a id="method-12-2"></a>

### 11.2 BlockingQueue.take()：有元素就移除并返回，空时等待【重点】

#### 方法做什么

签名为 `E take() throws InterruptedException`。take 同时执行“获得元素”和“把它从队列移除”，不是只查看。成功返回得到一个实际元素，队列不允许 null，所以正常 take 不需要用 null 表示没取到。

空队列时，调用者等待有元素出现；生产者仍能 put，并使等待者有机会继续。多个消费者对同一个元素不会同时完成一次取出，但不同元素可能被不同消费者并发处理。

#### 内部概念流程

```text
可中断地取得队列内部锁
    ↓
while 队列为空：
    等待 notEmpty，暂时释放内部锁
    得到机会后重新取得内部锁，再检查是否仍空
    ↓
取出队首，清理数组中对应引用
    ↓
推进读取位置，减少元素数量
    ↓
通知 notFull 上的等待生产者
    ↓
释放内部锁，返回元素
```

清理槽位中的引用，有助于队列不再无故持有已经移出的对象。但对象是否能被回收，仍取决于消费者等其他地方有没有引用。

#### 完整示例：交接三条消息

```java
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;

public class QueueTransferDemo {
    record Message(int id, String text) {}

    public static void main(String[] args) throws InterruptedException {
        BlockingQueue<Message> queue = new ArrayBlockingQueue<>(2);

        Thread consumer = new Thread(() -> {
            try {
                for (int i = 0; i < 3; i++) {
                    Message message = queue.take();              // ①
                    System.out.println(message.id() + ": " + message.text());
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();              // ②
            }
        }, "consumer");
        consumer.start();

        queue.put(new Message(1, "A"));                           // ③
        queue.put(new Message(2, "B"));
        queue.put(new Message(3, "C"));
        consumer.join();                                        // ④
    }
}
```

① 为空时等待，有数据后移除一条。② 这个示例的消费契约是中断后退出，并恢复中断标记供上层观察。③ main 作为生产者交接不可变消息，有界容量为 2，第三次 put 可能需要等消费者腾出空间。④ main 最后等待消费线程结束；put 成功不能替代这个完成等待。

本例明确消费三条才退出，是为了让有限演示能够结束。持续运行的系统不能简单把队列一空就认为生产已结束，需要另行定义结束标记、取消信号或业务生命周期。BlockingQueue 接口没有通用的 close() 协议。

#### 安全发布到底保证到哪里

生产者先构造消息、写好字段，再把消息放入队列；消费者通过队列取得该消息后，可以观察到入队前的相关写入。这叫安全发布：不仅交接了对象引用，还建立了必要的跨线程可见性关系。

保证不覆盖入队之后生产者继续进行的任意修改，也不把消息内部的可变集合变成线程安全。若消息中保存一个 list，构造后原线程还不停修改 list，record 本身也不能解决这种深层共享问题。

take 成功只表示消息离开队列，不表示消息已处理成功。消费者随后崩溃时，该元素不会自动重新出现。这一点与带确认、重投机制的消息中间件不同。业务需要可靠处理时，必须安排持久化、失败重试和幂等机制。

#### 容易造成误解的组合操作

```java
// 错误：isEmpty() 的结果可能马上过期，take() 仍可能等待。
if (!queue.isEmpty()) {
    process(queue.take());
}
```

其他消费者可以在两行之间先取走最后一个元素。若本次不想等，直接用 `poll()`，检查返回值是否为 null；若想等，就直接 take。先 peek 再 take 同样不能保证取到先前看到的那个对象。

队列的 FIFO 指取出顺序，不保证多消费者的处理完成顺序：先取到 A 的消费者可能较慢，后取到 B 的消费者可以先完成。需要按序完成的业务应额外限制消费方式。

<a id="sec-13"></a>

## 12. ConcurrentHashMap：让“是否存在”和更新成为一次映射操作

`ConcurrentHashMap` 保护 key 到 value 的映射，常用于并发缓存、任务登记和计数器查找。OpenJDK 21 的更新结合桶、原子操作与局部同步；不应把 Java 7 的固定 Segment 模型当成 Java 21 的完整实现，也不能把该容器概括成“所有方法完全无锁”。

它不接收 null key 或 null value，所以查询返回 null 可以明确表示不存在。映射安全不等于 value 内部字段安全：多个线程从 map 取到同一个 `ArrayList` 后无同步修改，仍可能发生问题。它也不为多个 key 的联合更新自动提供事务。[ConcurrentHashMap API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ConcurrentHashMap.html)。

<a id="method-13-2"></a>

### 12.1 ConcurrentHashMap.putIfAbsent(key, value)：仅在缺失时原子登记【重点】

#### 解决什么问题

两个线程都准备处理编号相同的任务。普通代码“查不到就 put”有竞态：

```java
// 错误：A 和 B 都可能在另一个线程插入前看到 key 不存在。
if (!running.containsKey(jobId)) {
    running.put(jobId, marker);
    executeJob();
}
```

即使 containsKey 与 put 各自线程安全，两个步骤合起来也没有原子性。putIfAbsent 将缺失检查和插入合并，只有成功登记者才能按协议进入任务执行。

#### 返回值如何判断

```java
String previous = running.putIfAbsent("job-1", "RUNNING");
if (previous == null) {
    // 本次成功插入。
} else {
    // 原来已有映射；本次没有替换它，previous 是原有值。
}
```

返回类型是 value 类型，不是 boolean；返回 null 表示本次插入成功，而不是插入失败。因为容器不允许保存 null，这个返回值可以承担成功标记。

#### 内部概念流程与并发交错

概念上先定位 key 对应位置，在适当的原子更新或局部同步机制下检查：不存在则发布 value，存在则保留原 value 并返回它。具体是否竞争锁、如何处理冲突和扩容，属于实现细节，不改变“检查与插入不可被拆开”的契约。

如果 A 和 B 同时给 job-1 放不同标记：A 成功插入并得到 null；B 发现已有 A 的标记，返回 A 的标记，不把它替换成自己的。B 不需要额外再读一次才能知道这次操作面对的原值。

#### 示例：同一时刻只允许一次任务进入

以下方法片段中的 RUNNING 是共享字段，需要导入 `java.util.concurrent.ConcurrentHashMap`：

```java
static final ConcurrentHashMap<String, Object> RUNNING =
    new ConcurrentHashMap<>();

static boolean runOnceAtATime(String jobId, Runnable action) {
    Object marker = new Object();                          // ①
    Object existing = RUNNING.putIfAbsent(jobId, marker);   // ②
    if (existing != null) {
        return false;                                     // ③
    }
    try {
        action.run();                                     // ④
        return true;
    } finally {
        RUNNING.remove(jobId, marker);                     // ⑤
    }
}
```

① 为这次运行创建唯一标记。② 原子登记。③ 已有运行者则不执行。④ 只有登记成功者执行任务。⑤ 清理自己的登记，使用条件删除以免误删后来替换成不同标记的映射。这里 Object 默认采用身份相等，唯一标记能区分运行实例。

这个方法的业务含义是“同一个 jobId 同时只运行一个遵守协议的调用”。任务结束后删除登记，之后仍允许再运行。它不是“进程生命周期内只执行一次”，也不是跨 JVM 的全局去重。若 action 启动异步任务后就返回，这个登记只能覆盖提交过程，不能覆盖后台真实运行过程。

#### 其他重要边界

`putIfAbsent(key, createExpensiveValue())` 中的构造函数在调用方法之前就执行，哪怕 key 已存在也会创建候选对象。容器只决定是否存入，不决定 Java 参数表达式要不要计算。需要缺失时才计算，使用下一节的 computeIfAbsent。

登记成功不证明后续 action 成功，也不提供崩溃后的持久化记录。没有 finally 清理时，异常可能留下永久占用标记；盲目清理其他任务标记又会让重复执行进入。返回契约见 [ConcurrentHashMap.putIfAbsent](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ConcurrentHashMap.html#putIfAbsent(K,V))。

<a id="method-13-3"></a>

### 12.2 ConcurrentHashMap.computeIfAbsent(key, fn)：缺失时才构造并发布值【重点】

#### 场景与函数含义

统计每个请求路径出现的次数。每条路径需要一个计数器，已有计数器直接使用，缺失时创建。computeIfAbsent 接收一个 key 和计算函数，函数负责生成 value；方法把原子查找、必要的计算与建立映射组合起来。

```java
ConcurrentHashMap<String, LongAdder> counts = new ConcurrentHashMap<>();
counts.computeIfAbsent("/orders", path -> new LongAdder()).increment();
```

函数参数 path 是需要建立映射的 key，返回的是计数器。方法的结果是已有或新建立的计数器；后面的 increment 是计数器自己提供的线程安全增加操作，不是 map 隐藏地帮任意 value 加锁。

#### 内部概念流程

```text
查找 key 的当前映射
    ├─ 已存在 → 不调用函数，返回已有 value
    └─ 缺失 → 在协调同一映射更新的机制下计算 fn(key)
                  ├─ 返回非 null → 建立映射并返回该值
                  ├─ 返回 null   → 保持缺失，返回 null
                  └─ 抛异常      → 本次新映射不建立，异常向调用者传播
```

对 ConcurrentHashMap，此次方法调用的计算与更新按其原子契约协调。某些其他更新操作会在计算期间等待，因此函数应短小。此方法的回调不采用 AtomicInteger.updateAndGet 那种“竞争后对同一次调用反复重算”的模型；但不能因此认为系统整个生命周期永远只调用一次计算函数。

#### 完整示例：三个线程共用一个计数器

```java
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.LongAdder;

public class ComputeCounterDemo {
    public static void main(String[] args) throws InterruptedException {
        ConcurrentHashMap<String, LongAdder> counts = new ConcurrentHashMap<>();
        Thread[] workers = new Thread[3];

        for (int i = 0; i < workers.length; i++) {
            workers[i] = new Thread(() -> {
                for (int n = 0; n < 1000; n++) {
                    LongAdder counter = counts.computeIfAbsent(
                        "/orders", path -> new LongAdder()
                    );
                    counter.increment();
                }
            });
            workers[i].start();
        }
        for (Thread worker : workers) {
            worker.join();
        }
        System.out.println(counts.get("/orders").sum()); // 3000
    }
}
```

每次查找得到同一个已登记计数器，各线程分别增加它。读取最终计数安排在所有线程 join 之后，避免把 LongAdder 并发更新时的 sum 当作严格原子快照。本例还约定执行期间不删除或替换此映射；否则一个线程可能取得旧计数器后，map 删除并发布新计数器，旧计数器上的增加便不体现在新映射中。

#### 为什么不能当成“终身只加载一次”

函数返回 null 不会建立映射，后续调用仍可能计算；函数抛异常同样没有建立本次新映射；映射被 remove 或 clear 后，下次也可能重新计算。因此若函数具有“发奖、扣款、发邮件”的外部副作用，不能用此方法推断副作用在系统生命周期只发生一次。

对于 key 仍保留成功创建值的正常情况，之后的调用使用已有值而不会再次计算。这保证的是当前映射建立协议，不能替代持久化幂等记录或事务。

#### 回调不得修改同一张 map

```java
// 错误：计算函数在计算期间又修改同一张 map。
cache.computeIfAbsent("A", key -> {
    cache.put("B", new Value());
    return new Value();
});
```

官方契约要求 mapping function 不得在计算过程中修改这张 map，包括其他 key，不仅仅是递归修改 A。可检测的递归更新可能抛 `IllegalStateException`，某些错误嵌套还可能造成等待问题；不能把“某次运行没有出错”当成许可。

长时间网络加载也不适合直接放在这个回调中：某些竞争更新会等它结束，外部资源锁的交叉依赖还会使系统难以推理。需要协调长任务时，可把短小的任务结果占位对象原子登记，再在映射计算之外执行工作，并设计失败清理与完成传播。契约见 [ConcurrentHashMap.computeIfAbsent](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ConcurrentHashMap.html#computeIfAbsent(K,java.util.function.Function))。

<a id="sec-14"></a>

## 13. ThreadLocal：绑定在线程上，而不是绑定在某次请求上

ThreadLocal 常用于在线程内传递请求编号、跟踪上下文等数据。虽然多个线程使用同一个 ThreadLocal 对象，`get()` 和 `set()` 访问的是各自线程的绑定。OpenJDK 中，绑定关系主要放在线程对象内部的 ThreadLocalMap 中，ThreadLocal 对象作为键。

隔离的是绑定：A 线程可以绑定对象 X，B 线程绑定对象 Y。如果初始化函数给每个线程都返回同一个共享可变对象，X 和 Y 实际相同，仍然会产生共享竞争。ThreadLocal 也不会自动把上下文从提交任务的线程传到线程池工作线程。[ThreadLocal API](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/ThreadLocal.html)。

<a id="method-14-4"></a>

### 13.1 ThreadLocal.remove()：清理当前线程的绑定，避免跨任务残留【重点】

#### 为什么清理是关键方法

线程池会复用工作线程。请求 A 在线程 worker-1 中设置请求编号 A，结束后线程并不退出，而是继续执行请求 B。如果 A 的绑定未清理，B 没有设置新值便读取时，就可能读到 A 的编号。

ThreadLocal 的生命周期跟随线程绑定，不自动跟随一次 HTTP 请求或一次 Runnable。remove 是把这两个生命周期正确衔接起来的重要步骤：业务上下文结束时，明确撤销当前线程的绑定。

#### 方法内部的概念流程

```text
找到当前线程
    ↓
查找当前线程的 ThreadLocalMap
    ├─ 没有映射表 → 无需清理，返回
    └─ 有映射表 → 找到本 ThreadLocal 对应条目
                    ↓
                 删除绑定并清理相关失效条目
                    ↓
                 返回，其他线程的绑定不变
```

这是 OpenJDK 21 的实现概念，具体探测和清理结构属于实现细节。remove 返回 void，当前线程没有绑定时调用也不会因此报错。它不是销毁 ThreadLocal 对象，也不是删除所有线程中的值。

#### 标准用法：设置与清理属于同一作用域

```java
static final ThreadLocal<String> REQUEST_ID = new ThreadLocal<>();

static void handle(String requestId) {
    REQUEST_ID.set(requestId);
    try {
        writeLog();              // 业务方法通过 REQUEST_ID.get() 读取。
        processRequest();
    } finally {
        REQUEST_ID.remove();     // 当前线程的业务作用域结束时清理。
    }
}
```

finally 确保异常路径也清理。清理应由设置上下文的工作线程执行；main 在提交之后调用自己的 REQUEST_ID.remove()，只会清理 main 的绑定，不能替线程池里的 worker 清理。

`set(null)` 与 remove 不相同：前者保留一个绑定，值为 null；后者删除绑定。若 ThreadLocal 设置了初始化函数，remove 后下一次 get 会重新初始化，而 set(null) 后 get 读取的是已绑定的 null。

#### 完整示例：复用同一个工作线程

```java
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class ThreadLocalCleanupDemo {
    static final ThreadLocal<String> REQUEST_ID = new ThreadLocal<>();

    public static void main(String[] args) throws Exception {
        try (ExecutorService pool = Executors.newSingleThreadExecutor()) {
            pool.submit(() -> {
                REQUEST_ID.set("request-A");
                try {
                    System.out.println(REQUEST_ID.get()); // request-A
                } finally {
                    REQUEST_ID.remove();
                }
            }).get();

            pool.submit(() ->
                System.out.println(REQUEST_ID.get())      // null
            ).get();
        }
    }
}
```

单线程线程池让两个任务顺序复用同一个工作线程。第一个任务设置 A 并清理；第二个任务没有继承 A。删除第一任务的 remove 后，第二个任务便会打印 request-A，直接展示上下文残留。

普通 ThreadLocal 的 get 在未绑定时采用默认初始化值 null，并建立相应初始化绑定；若使用 withInitial，第二次任务会得到初始化函数生成的值。这里的 null 输出只是该示例的默认设置，不是所有 ThreadLocal 的通用结果。

#### 内存泄漏为何不能仅靠弱引用解决

OpenJDK ThreadLocalMap 的键采用弱引用，而值仍是普通强引用。ThreadLocal 键不再被其他地方持有时，键可能被垃圾回收，但条目的 value 不一定立即从长期存活的工作线程映射中消失。部分后续操作会清理失效条目，却不能当作及时清理的保证。

如果 ThreadLocal 本身是静态字段，键通常还活着，绑定就更不会仅因一次请求结束而自动移除。大的缓存对象或请求对象可能被线程间接长期持有。remove 撤销这份绑定有助于释放引用，但如果其他地方也持有该对象，仍不会立即回收；remove 也不等于对数据库连接等资源执行 close。

#### 嵌套上下文不能一律 remove

外层流程绑定 outer，内层临时绑定 inner。内层退出时如果直接 remove，会把外层需要继续使用的 outer 也丢掉。需要先保存旧值，再恢复它：

```java
// 约定：非 null 表示有效上下文，null 表示没有需要保留的上下文。
static void withRequestId(String id, Runnable action) {
    String previous = REQUEST_ID.get();
    REQUEST_ID.set(id);
    try {
        action.run();
    } finally {
        if (previous == null) {
            REQUEST_ID.remove();
        } else {
            REQUEST_ID.set(previous);
        }
    }
}
```

调用 `withRequestId("outer", () -> withRequestId("inner", action))` 时，内层执行 action 读取 inner，内层退出恢复 outer，外层退出清理其原始空状态。

这段辅助方法有明确约束：null 不表示一个需要保存的业务上下文，且使用没有特殊初始化值的 ThreadLocal。若系统允许显式绑定 null，或者初始化函数带有特殊语义，仅靠 previous 是否为 null 就无法精确区别“无绑定”和“绑定了 null”，需要额外的状态封装或作用域栈，而不是忽略这一区别。

线程间切换也应显式传递必要参数，并在接收线程的作用域内设置与清理。不能认为一份 ThreadLocal 会自动随 CompletableFuture 的阶段调度流动；某个阶段切换线程时，访问的是新线程自己的绑定。契约见 [ThreadLocal.remove](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/ThreadLocal.html#remove())。

<a id="sec-5"></a>

<a id="sec-15"></a>

<a id="other-methods"></a>

## 14. 非重点方法统一速查表

本表集中收录 92 个非重点方法条目。它们用于补全 API 地图；初次学习可先阅读所需的一行，再回到对应的重点方法理解配套用法。表中的时限是等待契约，不是业务端到端的硬实时保证。

| 类别 | 方法 | 主要作用 | 返回或等待 | 最需要记住的一点 |
|---|---|---|---|---|
| Thread | <a id="method-2-3"></a>`Thread.currentThread()` | 获取当前执行线程 | 返回当前线程对象，不启动线程 | 与变量所指的目标线程分清 |
| Thread | <a id="method-2-6"></a>`Thread.join(timeout)` | 限时等待目标线程 | 等待目标结束或超时；millis 重载返回 void | 超时不停止目标；join(0) 无时限 |
| Thread | <a id="method-2-10"></a>`Thread.isAlive()` | 查询目标是否存活 | 返回是否已启动且尚未结束 | 只是快照，不能代替 join |
| Thread | <a id="method-2-11"></a>`Thread.getState()` | 获取诊断状态 | 返回 Thread.State 快照 | 用于诊断，不应据此协调业务 |
| Thread | <a id="method-2-12"></a>`Thread.yield()` | 提示调度器让出执行机会 | 给调度器让出执行机会的提示 | 可能被忽略，不释放锁 |
| Thread | <a id="method-2-13"></a>`Thread.setUncaughtExceptionHandler(...)` | 设置未捕获异常处理器 | 设置未捕获异常处理器 | submit 包装的异常通常在 Future 中 |
| Object | <a id="method-3-2"></a>`Object.wait(timeout)` | 带时间限制的条件等待 | 限时条件等待；恢复前仍需拿回监视器 | wait(0) 无时限，超时不证明条件成立 |
| Object | <a id="method-3-3"></a>`Object.notify()` | 通知一个等待线程 | 通知一个等待者，不释放监视器 | 要求持锁；选择不保证公平；不积累通知 |
| ReentrantLock | <a id="method-4-3"></a>`ReentrantLock.tryLock()` | 立即尝试获取锁 | 立即尝试获取，成功 true、失败 false | true 已持锁；无参版不遵守公平排队 |
| ReentrantLock | <a id="method-4-6"></a>`ReentrantLock.newCondition()` | 创建关联条件队列 | 返回与这把锁关联的 Condition | 创建条件对象不等于已获取锁 |
| Condition | <a id="method-4-8"></a>`Condition.await(timeout, unit)` | 限时条件等待 | 限时等待，返回与超时有关的结果 | 仍要检查业务条件，返回前重新拿锁 |
| Condition | <a id="method-4-9"></a>`Condition.awaitNanos(...)` | 等待并返回剩余预算 | 等待后返回估计剩余纳秒预算 | 循环使用剩余预算，避免不断延长等待 |
| Condition | <a id="method-4-10"></a>`Condition.awaitUninterruptibly()` | 等待时不以中断退出 | 不以 InterruptedException 退出条件等待 | 最后保留中断状态，取消可能不及时 |
| Condition | <a id="method-4-11"></a>`Condition.signal()` | 通知一个关联条件等待者 | 通知这一个条件队列中的一名等待者 | 要求关联锁；通知不立即交出锁 |
| Condition | <a id="method-4-12"></a>`Condition.signalAll()` | 通知该条件的全部等待者 | 通知这一个条件队列的所有等待者 | 不影响同一锁上的其他 Condition |
| ReentrantReadWriteLock | <a id="method-5-1"></a>`ReentrantReadWriteLock.readLock()` | 取得读锁对象 | 返回读锁对象，本调用不加锁 | 之后调用 lock；读者不能任意修改数据 |
| ReentrantReadWriteLock | <a id="method-5-2"></a>`ReentrantReadWriteLock.writeLock()` | 取得写锁对象 | 返回写锁对象，本调用不加锁 | 之后调用 lock；持读锁直接升级可能卡住 |
| StampedLock | <a id="method-5-3"></a>`StampedLock.tryOptimisticRead()` | 取得乐观读戳 | 返回乐观读戳，不实际占用读锁 | 写锁被占用可返回 0；读取后必须校验 |
| StampedLock | <a id="method-5-4"></a>`StampedLock.validate(stamp)` | 验证乐观读是否受写入干扰 | 返回乐观读戳是否仍有效 | 失败就丢弃读值、加读锁重读 |
| StampedLock | <a id="method-5-5"></a>`StampedLock.readLock()` | 获取实际读锁 | 获取实际读锁，必要时等待，返回戳 | 需以对应读戳释放；不要与锁对象 getter 混淆 |
| StampedLock | <a id="method-5-6"></a>`StampedLock.writeLock()` | 获取独占写锁 | 获取独占写锁，必要时等待，返回戳 | StampedLock 不可重入 |
| StampedLock | <a id="method-5-7"></a>`StampedLock.unlockRead(stamp)` | 释放指定读锁获取 | 以有效读戳释放此次读锁 | 乐观读戳不能拿来做读锁释放 |
| StampedLock | <a id="method-5-8"></a>`StampedLock.unlockWrite(stamp)` | 释放指定写锁获取 | 以有效写戳释放此次写锁 | 在 finally 释放，并管理戳的生命周期 |
| Executors | <a id="method-6-1"></a>`Executors.newFixedThreadPool(n)` | 创建固定规模线程池 | 创建固定数量上限的平台工作线程池 | 任务队列基本无界，积压仍可能增长 |
| Executors | <a id="method-6-2"></a>`Executors.newSingleThreadExecutor()` | 串行执行提交的任务 | 创建同时最多执行一个任务的执行器 | 串行执行不保证永远使用同一物理线程 |
| Executors | <a id="method-6-3"></a>`Executors.newCachedThreadPool()` | 按需扩展线程的线程池 | 创建按需增减工作线程的池 | 直接移交队列；阻塞任务多时线程可能过多 |
| Executors | <a id="method-6-4"></a>`Executors.newScheduledThreadPool(n)` | 创建定时执行器 | 创建延迟与周期调度执行器 | 到时仅具备执行资格，不是硬实时 |
| ExecutorService | <a id="method-6-9"></a>`ExecutorService.awaitTermination(timeout, unit)` | 等待池终止 | 等待池终止，终止 true、超时 false | 不发起关闭，超时不保证任务停止 |
| ExecutorService | <a id="method-6-10"></a>`ExecutorService.isShutdown()` | 查询是否已发起关闭 | 返回是否已发起关闭 | true 时仍可能有任务正在运行 |
| ExecutorService | <a id="method-6-11"></a>`ExecutorService.isTerminated()` | 查询是否已经终止 | 返回关闭流程是否已全部终止 | 状态查询不替代限时等待 |
| ExecutorService | <a id="method-6-12"></a>`ExecutorService.close()` | 关闭并等待结束 | 有序关闭并等待终止；Java 19 起支持 | 任务不退出时 close 也可能长期等待 |
| ExecutorService | <a id="method-6-13"></a>`ExecutorService.invokeAll(tasks)` | 执行并等待整组任务 | 等待整组任务，返回各自的 Future | 结果按输入迭代顺序；超时版取消未完成任务 |
| ExecutorService | <a id="method-6-14"></a>`ExecutorService.invokeAny(tasks)` | 取得一个成功结果 | 返回一个正常完成任务的结果 | 不只看谁先失败；对剩余任务请求取消 |
| ScheduledExecutorService | <a id="method-6-15"></a>`ScheduledExecutorService.schedule(...)` | 延迟执行一次 | 安排延迟一次任务，返回 ScheduledFuture | 延迟结束不保证立即获得执行线程 |
| ScheduledExecutorService | <a id="method-6-17"></a>`ScheduledExecutorService.scheduleWithFixedDelay(...)` | 完成后再间隔 | 上次结束后再间隔 delay 执行 | 与固定频率不同；异常也会抑制后续周期 |
| Future | <a id="method-7-2"></a>`Future.get(timeout, unit)` | 限时等待结果 | 等待结果，超时抛 TimeoutException | 结束本次等待，不自动取消任务 |
| Future | <a id="method-7-4"></a>`Future.isDone()` | 查询是否进入完成状态 | 返回是否已进入完成状态 | 成功、失败、取消都算；取消不证明实际退出 |
| Future | <a id="method-7-5"></a>`Future.isCancelled()` | 查询是否取消 | 返回 Future 是否被取消 | 不保证副作用回滚或代码停止 |
| CompletableFuture | <a id="method-8-1"></a>`CompletableFuture.runAsync(...)` | 运行无返回值异步任务 | 安排 Runnable，返回 CompletableFuture<Void> | 没有返回值也要观察异常 |
| CompletableFuture | <a id="method-8-4"></a>`CompletableFuture.thenApplyAsync(fn, executor)` | 调度结果转换 | 通过执行器调度结果转换 | Async 不保证新建专属线程，也不等于非阻塞 I/O |
| CompletableFuture | <a id="method-8-5"></a>`CompletableFuture.thenAccept(consumer)` | 消费结果而不产生新值 | 成功后消费结果，返回 Void 阶段 | 消费动作也可能失败 |
| CompletableFuture | <a id="method-8-6"></a>`CompletableFuture.thenRun(action)` | 成功后执行不依赖结果的动作 | 成功后执行不使用结果的动作 | 上游失败默认不执行，不是无条件 finally |
| CompletableFuture | <a id="method-8-9"></a>`CompletableFuture.allOf(...)` | 等待全部完成 | 返回等待全部输入完成的 Void 阶段 | 不收集值；异常不自动取消其他任务 |
| CompletableFuture | <a id="method-8-10"></a>`CompletableFuture.anyOf(...)` | 等待任意一个先完成 | 由一个先完成输入决定 Object 结果 | 包括失败；空输入得到未完成阶段 |
| CompletableFuture | <a id="method-8-11"></a>`CompletableFuture.exceptionally(fn)` | 失败时转换为替代结果 | 上游失败时计算替代结果 | 默认值应区分正常无数据和系统失败 |
| CompletableFuture | <a id="method-8-12"></a>`CompletableFuture.handle(fn)` | 同时处理成功和失败 | 处理成功或失败，并生成新结果 | 按异常参数判断失败，正常结果也可能为 null |
| CompletableFuture | <a id="method-8-13"></a>`CompletableFuture.whenComplete(action)` | 观察完成情况 | 观察成功或失败，通常沿用原结果 | 观察动作抛异常会影响返回阶段 |
| CompletableFuture | <a id="method-8-15"></a>`CompletableFuture.complete(value)` | 尝试正常完成 | 尝试发布正常结果，成功 true | 完成结果不自动停止原有后台计算 |
| CompletableFuture | <a id="method-8-16"></a>`CompletableFuture.completeExceptionally(error)` | 尝试异常完成 | 尝试发布异常结果，成功 true | 不自动中断任务或回滚外部操作 |
| CompletableFuture | <a id="method-8-17"></a>`CompletableFuture.cancel(...)` | 以取消方式完成 | 使未完成阶段进入取消状态 | mayInterruptIfRunning 不控制此实现的中断 |
| CompletableFuture | <a id="method-8-18"></a>`CompletableFuture.orTimeout(timeout, unit)` | 超时后异常完成 | 超时将同一个 Future 异常完成；Java 9 起 | 不自动停止实际计算；其他使用者也受影响 |
| CompletableFuture | <a id="method-8-19"></a>`CompletableFuture.completeOnTimeout(value, timeout, unit)` | 超时后使用默认值 | 超时将同一个 Future 正常完成为默认值 | 不自动停止任务，要区分默认值与真实结果 |
| AtomicInteger | <a id="method-9-1"></a>`AtomicInteger.get()` | 读取当前值 | 以相应 volatile 读语义返回当前值 | 下一次读取仍可能变，不能保证多步原子性 |
| AtomicInteger | <a id="method-9-2"></a>`AtomicInteger.set(value)` | 原子地覆盖当前值 | 以相应 volatile 写语义原子覆盖值 | set(get()+1) 仍可能丢失更新 |
| AtomicInteger | <a id="method-9-4"></a>`AtomicInteger.incrementAndGet()` | 加一并返回新值 | 原子加一，返回新值 | int 会溢出，不提供跨进程唯一性 |
| AtomicInteger | <a id="method-9-5"></a>`AtomicInteger.getAndIncrement()` | 加一并返回旧值 | 原子加一，返回旧值 | 注意与 incrementAndGet 的返回时点区别 |
| AtomicInteger | <a id="method-9-6"></a>`AtomicInteger.decrementAndGet()` | 减一并返回新值 | 原子减一，返回新值 | 不内置不能小于零的业务检查 |
| AtomicInteger | <a id="method-9-7"></a>`AtomicInteger.addAndGet(delta)` | 加指定值并返回新值 | 原子增加 delta，返回新值 | delta 可为负，整数溢出仍须考虑 |
| AtomicInteger | <a id="method-9-8"></a>`AtomicInteger.getAndAdd(delta)` | 加指定值并返回旧值 | 原子增加 delta，返回旧值 | 适合区间起点；多个调用不是整体事务 |
| AtomicInteger | <a id="method-9-9"></a>`AtomicInteger.getAndSet(value)` | 替换并返回旧值 | 原子替换并返回旧值 | 先 get 再 set 不能提供相同保证 |
| AtomicInteger | <a id="method-9-11"></a>`AtomicInteger.getAndUpdate(fn)` | 按函数更新并返回旧值 | 按函数原子更新，返回旧值 | 函数仍可能重算，应避免外部副作用 |
| AtomicInteger | <a id="method-9-12"></a>`AtomicInteger.compareAndExchange(expected, update)` | 返回实际比较值 | 条件原子更新，返回观察到的实际旧值 | 返回类型不是布尔值；Java 9 起 |
| LongAdder | <a id="method-9-14"></a>`LongAdder.increment()` | 分散竞争地计数 | 分散竞争地增加统计计数 | 不返回本次精确全局序号 |
| LongAdder | <a id="method-9-15"></a>`LongAdder.add(delta)` | 累加指定值 | 分散竞争地累计 delta | 适合统计，不替代库存条件 CAS |
| LongAdder | <a id="method-9-16"></a>`LongAdder.sum()` | 汇总计数 | 汇总 base 和各计数单元 | 并发更新时不是整体原子快照 |
| CountDownLatch | <a id="method-10-3"></a>`CountDownLatch.await(timeout, unit)` | 限时等待归零 | 限时等归零，成功 true、超时 false | 超时不取消子任务、不重置计数 |
| CyclicBarrier | <a id="method-10-5"></a>`CyclicBarrier.await(timeout, unit)` | 限时集合 | 限时登记到达并等本轮集合 | 超时可破坏本轮，影响其他等待者 |
| CyclicBarrier | <a id="method-10-6"></a>`CyclicBarrier.reset()` | 破坏当前轮并重置 | 破坏当前轮，并创建新一轮 | 旧任务与新任务需额外协调 |
| Semaphore | <a id="method-10-8"></a>`Semaphore.tryAcquire()` | 立即尝试取得许可 | 立即尝试取得许可，返回布尔值 | 成功才归还；无参版可能绕过公平排队 |
| Semaphore | <a id="method-10-9"></a>`Semaphore.tryAcquire(timeout, unit)` | 限时取得许可 | 限时、可中断地取得许可 | false 未取得许可，不能无条件 release |
| Phaser | <a id="method-10-11"></a>`Phaser.register()` | 注册一个阶段参与者 | 增加阶段参与者，返回阶段号 | 注册后需到达或注销；提交失败要处理 |
| Phaser | <a id="method-10-12"></a>`Phaser.arriveAndAwaitAdvance()` | 到达并等待阶段推进 | 登记到达并等待阶段推进 | 不通过中断异常退出；终止时可返回负阶段 |
| Phaser | <a id="method-10-13"></a>`Phaser.arriveAndDeregister()` | 到达并退出后续参与 | 登记本轮到达，并注销后续参与 | 默认规则下参与者减到零会终止 |
| LockSupport | <a id="method-11-2"></a>`LockSupport.park(blocker)` | 增加阻塞诊断信息 | 按许可模型挂起，并记录 blocker | blocker 用于诊断，不是自动获取的锁 |
| LockSupport | <a id="method-11-3"></a>`LockSupport.parkNanos(nanos)` | 有时间预算的挂起 | 有时间预算的许可等待 | 可提前返回，纳秒参数不保证调度精度 |
| BlockingQueue | <a id="method-12-3"></a>`BlockingQueue.offer(element)` | 容量不足时返回失败 | 尝试入队，满时 false，不等未来容量 | 仍可能争用内部锁；必须处理 false |
| BlockingQueue | <a id="method-12-4"></a>`BlockingQueue.offer(element, timeout, unit)` | 限时等待入队 | 限时等容量，成功 true、超时 false | 等待可中断，预算不是整个业务的硬时限 |
| BlockingQueue | <a id="method-12-5"></a>`BlockingQueue.poll()` | 取出元素或返回 null | 移除队首，空时 null | 不等待新元素，不允许 null 元素入队 |
| BlockingQueue | <a id="method-12-6"></a>`BlockingQueue.poll(timeout, unit)` | 限时等待元素 | 限时等元素，超时 null | 超时不停止生产者 |
| BlockingQueue | <a id="method-12-7"></a>`BlockingQueue.peek()` | 查看但不移除队首 | 查看队首但不移除，空时 null | 之后取出时，原元素可能已被别人拿走 |
| BlockingQueue | <a id="method-12-8"></a>`BlockingQueue.drainTo(collection, maxElements)` | 批量转移当前元素 | 把当前若干元素转移到目标集合，返回数量 | 不等未来元素；两容器间不是通用事务 |
| ConcurrentHashMap | <a id="method-13-1"></a>`ConcurrentHashMap.get(key)` | 查询当前映射 | 读取当前映射，缺失时 null | map 保护映射，不包办值对象内部线程安全 |
| ConcurrentHashMap | <a id="method-13-4"></a>`ConcurrentHashMap.compute(key, fn)` | 按当前映射计算新值 | 按当前映射计算新值，null 表示移除 | 单 key 原子；计算函数不得另外修改此 map |
| ConcurrentHashMap | <a id="method-13-5"></a>`ConcurrentHashMap.merge(key, value, fn)` | 合并已有值 | 缺失用给定值，存在则按函数合并 | 合并结果 null 删除；回调不另外更新其他映射 |
| ConcurrentHashMap | <a id="method-13-6"></a>`ConcurrentHashMap.replace(key, oldValue, newValue)` | 条件替换 | 当前值相等时替换，成功 true | 值按相等性比较，不是 AtomicReference 的引用身份 |
| ConcurrentHashMap | <a id="method-13-7"></a>`ConcurrentHashMap.remove(key, value)` | 条件删除 | key 仍对应相等 value 时删除 | 只保护这次条件删除，不能识别所有历史变化 |
| ThreadLocal | <a id="method-14-1"></a>`ThreadLocal.withInitial(supplier)` | 创建带初始化逻辑的变量 | 创建带每线程初始化逻辑的 ThreadLocal | 初始化返回同一对象仍会共享对象内容 |
| ThreadLocal | <a id="method-14-2"></a>`ThreadLocal.get()` | 读取当前线程的绑定 | 读取当前线程绑定，缺失时初始化 | remove 后再次 get 会重新初始化 |
| ThreadLocal | <a id="method-14-3"></a>`ThreadLocal.set(value)` | 设置当前线程的绑定 | 设置当前线程绑定 | 不自动跨工作线程传播；set(null) 不等于移除 |
| Thread | <a id="method-15-1"></a>`Thread.startVirtualThread(task)` | 创建并启动虚拟线程 | 创建并启动虚拟线程，返回 Thread；Java 21 | 虚拟线程为守护线程；需要正确等任务结束 |
| Thread | <a id="method-15-2"></a>`Thread.ofVirtual()` | 取得虚拟线程构建器 | 返回虚拟线程构建器；Java 21 | 该方法自身不启动；虚拟对象直接 run 不执行任务 |
| Executors | <a id="method-15-3"></a>`Executors.newVirtualThreadPerTaskExecutor()` | 每任务一条虚拟线程 | 创建每任务一条虚拟线程的执行器；Java 21 | 不自动限制外部资源并发，也不增加 CPU 核数 |

读写锁、StampedLock、Phaser 和虚拟线程均在本表中查询。Java 21 的虚拟线程适合大量等待型任务，资源并发、请求积压与截止时间仍需单独控制；不同 JDK 的具体性能机制应查对应版本资料。


<a id="sec-16"></a>

## 15. 完整实验：从代码观察方法行为

每个程序独立保存为与公开类同名的 `.java` 文件，可使用 Java 21 编译运行。例如：

```shell
javac -encoding UTF-8 StartRunJoinDemo.java
java StartRunJoinDemo
```

以下程序没有第三方依赖，检查使用显式异常而非需要 `-ea` 才启用的 `assert`。并发任务的个别日志顺序可能变化，应以同步关系和检查结果判断正确性。

### 15.1 实验一：start、run 与 join

```java
public class StartRunJoinDemo {
    public static void main(String[] args) throws InterruptedException {
        int[] result = {0};
        Thread worker = new Thread(() -> {
            result[0] = 42;
            System.out.println("执行任务的线程："
                    + Thread.currentThread().getName());
        }, "worker");

        worker.run(); // 普通调用，在 main 上执行
        if (worker.getState() != Thread.State.NEW) {
            throw new IllegalStateException("直接 run 不应启动线程");
        }

        result[0] = 0;
        worker.start(); // worker 执行同一任务
        worker.join();
        if (result[0] != 42 || worker.isAlive()) {
            throw new IllegalStateException("join 后未观察到预期结果");
        }

        try {
            worker.start();
            throw new IllegalStateException("已启动的对象不应再次启动");
        } catch (IllegalThreadStateException expected) {
            System.out.println("重复 start 被拒绝");
        }
        System.out.println("join 后结果：" + result[0]);
    }
}
```

**观察：** 首次输出线程名为 `main`，第二次为 `worker`；直接调用 `run()` 后对象仍未启动。`join()` 后主线程可读取 worker 的写入，即使该结果字段没有单独声明 `volatile`，本实验仍由结束等待提供可见性保证。

### 15.2 实验二：interrupt 与 sleep

```java
import java.util.concurrent.CountDownLatch;

public class InterruptDemo {
    public static void main(String[] args) throws InterruptedException {
        CountDownLatch started = new CountDownLatch(1);
        Thread worker = new Thread(() -> {
            try {
                started.countDown();
                Thread.sleep(60_000);
                throw new IllegalStateException("任务应响应中断");
            } catch (InterruptedException e) {
                System.out.println("捕获异常时的中断标记："
                        + Thread.currentThread().isInterrupted());
                Thread.currentThread().interrupt();
                System.out.println("恢复后的中断标记："
                        + Thread.currentThread().isInterrupted());
            }
        }, "sleeping-worker");

        worker.start();
        started.await();
        worker.interrupt();
        worker.join();
        System.out.println("worker 已结束");
    }
}
```

**观察：** 捕获时标记为 `false`，恢复后为 `true`，任务不必等满一分钟。主线程在 `started.await()` 后发出中断，因此不需要靠任意 `sleep()` 猜测任务是否启动。中断可能发生在 worker 实际进入休眠前，也可能在休眠期间，两种情况都可以触发该可中断方法的响应。

### 15.3 实验三：Condition 实现单槽缓冲区

```java
import java.util.concurrent.locks.Condition;
import java.util.concurrent.locks.ReentrantLock;

public class ConditionBufferDemo {
    static final class OneSlot {
        private final ReentrantLock lock = new ReentrantLock();
        private final Condition notEmpty = lock.newCondition();
        private final Condition notFull = lock.newCondition();
        private Integer value;

        void put(int next) throws InterruptedException {
            lock.lockInterruptibly();
            try {
                while (value != null) {
                    notFull.await();
                }
                value = next;
                notEmpty.signal();
            } finally {
                lock.unlock();
            }
        }

        int take() throws InterruptedException {
            lock.lockInterruptibly();
            try {
                while (value == null) {
                    notEmpty.await();
                }
                int result = value;
                value = null;
                notFull.signal();
                return result;
            } finally {
                lock.unlock();
            }
        }
    }

    public static void main(String[] args) throws Exception {
        OneSlot slot = new OneSlot();
        java.util.concurrent.ExecutorService pool =
                java.util.concurrent.Executors.newFixedThreadPool(2);
        try {
            java.util.concurrent.Future<Void> producer = pool.submit(() -> {
                for (int i = 1; i <= 3; i++) {
                    slot.put(i);
                }
                return null;
            });
            java.util.concurrent.Future<Integer> consumer = pool.submit(() -> {
                int sum = 0;
                for (int i = 1; i <= 3; i++) {
                    int next = slot.take();
                    sum += next;
                    System.out.println("取出：" + next);
                }
                return sum;
            });
            producer.get(5, java.util.concurrent.TimeUnit.SECONDS);
            int sum = consumer.get(5, java.util.concurrent.TimeUnit.SECONDS);
            if (sum != 6) {
                throw new IllegalStateException("消费总和不正确");
            }
            System.out.println("消费总和：" + sum);
        } finally {
            pool.shutdownNow();
            if (!pool.awaitTermination(5, java.util.concurrent.TimeUnit.SECONDS)) {
                throw new IllegalStateException("线程池未终止");
            }
        }
    }
}
```

**观察：** 取出顺序为 1、2、3。槽为空时消费者等待 `notEmpty`，槽满时生产者等待 `notFull`；等待释放同一把锁，另一方才能修改状态。每次通知后仍以 `while` 检查条件，正常、异常路径都确保释放锁。

这个程序用手写缓冲区展示机制，业务中处理同类需求通常可以直接使用 `ArrayBlockingQueue`。

### 15.4 实验四：submit、get、异常与取消

```java
import java.util.concurrent.CancellationException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

public class FutureDemo {
    public static void main(String[] args) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            Future<Integer> normal = pool.submit(() -> 6 * 7);
            System.out.println("正常结果：" + normal.get(5, TimeUnit.SECONDS));

            Future<Integer> failed = pool.submit(() -> {
                throw new IllegalArgumentException("参数错误");
            });
            try {
                failed.get(5, TimeUnit.SECONDS);
                throw new IllegalStateException("失败任务应通过 get 报告异常");
            } catch (ExecutionException e) {
                if (!(e.getCause() instanceof IllegalArgumentException)) {
                    throw e;
                }
                System.out.println("原始异常：" + e.getCause().getMessage());
            }

            CountDownLatch started = new CountDownLatch(1);
            CountDownLatch exited = new CountDownLatch(1);
            Future<?> waiting = pool.submit(() -> {
                try {
                    started.countDown();
                    Thread.sleep(60_000);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                } finally {
                    exited.countDown();
                }
            });
            if (!started.await(5, TimeUnit.SECONDS)) {
                throw new IllegalStateException("任务未启动");
            }
            if (!waiting.cancel(true)) {
                throw new IllegalStateException("任务取消失败");
            }
            try {
                waiting.get();
                throw new IllegalStateException("已取消任务不应返回普通结果");
            } catch (CancellationException expected) {
                System.out.println("Future 已取消");
            }
            if (!exited.await(5, TimeUnit.SECONDS)) {
                throw new IllegalStateException("任务尚未完成退出逻辑");
            }
            System.out.println("任务退出逻辑已结束");
        } finally {
            pool.shutdownNow();
            if (!pool.awaitTermination(5, TimeUnit.SECONDS)) {
                throw new IllegalStateException("线程池未终止");
            }
        }
    }
}
```

**观察：** 普通结果为 42，失败任务的原始异常通过 `getCause()` 取得。程序用 `exited` 单独确认取消任务执行到了退出逻辑，说明 Future 的取消状态与任务实际退出不能混为一谈。

### 15.5 实验五：CAS 保护库存下界

```java
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public class AtomicStockDemo {
    static boolean reserveOne(AtomicInteger stock) {
        for (;;) {
            int before = stock.get();
            if (before <= 0) {
                return false;
            }
            if (stock.compareAndSet(before, before - 1)) {
                return true;
            }
        }
    }

    public static void main(String[] args) throws Exception {
        AtomicInteger stock = new AtomicInteger(10);
        AtomicInteger succeeded = new AtomicInteger();
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            List<Future<?>> tasks = new ArrayList<>();
            for (int i = 0; i < 4; i++) {
                tasks.add(pool.submit(() -> {
                    for (int j = 0; j < 20; j++) {
                        if (reserveOne(stock)) {
                            succeeded.incrementAndGet();
                        }
                    }
                }));
            }
            for (Future<?> task : tasks) {
                task.get(5, TimeUnit.SECONDS);
            }
            if (stock.get() != 0 || succeeded.get() != 10) {
                throw new IllegalStateException("库存约束被破坏");
            }
            System.out.println("成功次数：" + succeeded.get());
            System.out.println("剩余库存：" + stock.get());
        } finally {
            pool.shutdownNow();
            if (!pool.awaitTermination(5, TimeUnit.SECONDS)) {
                throw new IllegalStateException("线程池未终止");
            }
        }
    }
}
```

**观察：** 无论执行顺序如何，成功次数应为 10，剩余库存为 0。CAS 失败后必须重新读取并重新检查库存，否则会使用过期条件。

库存和成功次数是两个独立原子变量，中间时刻并非必须一起变化；这里在所有任务完成后检查最终一致结果。需要在任意时刻同时保持多字段不变量时，应整体设计状态。

### 15.6 实验六：CompletableFuture 的转换、串联和合并

```java
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class CompletableFutureDemo {
    public static void main(String[] args) {
        try (ExecutorService pool = Executors.newFixedThreadPool(3)) {
            CompletableFuture<Integer> base =
                    CompletableFuture.supplyAsync(() -> 100, pool);
            CompletableFuture<Integer> shipping =
                    CompletableFuture.supplyAsync(() -> 8, pool);
            CompletableFuture<Integer> total =
                    base.thenCombine(shipping, Integer::sum);
            CompletableFuture<String> text =
                    total.thenApply(value -> "总价：" + value);

            CompletableFuture<String> userId =
                    CompletableFuture.supplyAsync(() -> "U100", pool);
            CompletableFuture<String> detail = userId.thenCompose(id ->
                    CompletableFuture.supplyAsync(() -> "详情：" + id, pool));

            CompletableFuture<Integer> failed =
                    CompletableFuture.supplyAsync(() -> {
                        throw new IllegalStateException("模拟失败");
                    }, pool);
            CompletableFuture<Integer> recovered =
                    failed.exceptionally(error -> -1);

            CompletableFuture.allOf(text, detail, recovered).join();
            if (total.join() != 108 || recovered.join() != -1) {
                throw new IllegalStateException("异步组合结果不正确");
            }
            System.out.println(text.join());
            System.out.println(detail.join());
            System.out.println("替代结果：" + recovered.join());
        }
    }
}
```

**观察：** 输出总价 108、详情 U100、替代结果 -1。独立任务可并发提交，`thenCombine()` 负责汇合，`thenCompose()` 负责具有前后依赖的异步步骤，`allOf()` 正常结束后再读取各个阶段结果。

有限线程池中的任务不要通过阻塞 `join()` 等待同一个池里尚未运行的依赖任务。本实验在 main 中等待，工作任务不会相互占着工作线程等待排队子任务。

### 15.7 本地验证记录

验证日期：2026-10-08。验证环境：本机 Java / javac 21.0.7。六个完整程序均已使用 `javac -encoding UTF-8` 编译并独立运行，进程退出码均为 0。

| 程序 | 核对结果 |
|---|---|
| `StartRunJoinDemo` | main/worker 执行区别、重复启动异常、join 后结果 42 |
| `InterruptDemo` | 异常时中断标记 false、恢复后 true、线程退出 |
| `ConditionBufferDemo` | 依次消费 1、2、3，总和 6，线程池终止 |
| `FutureDemo` | 正常结果 42、原始异常、取消状态与退出确认 |
| `AtomicStockDemo` | 成功次数 10、库存 0 |
| `CompletableFutureDemo` | 合并结果 108、串联详情 U100、异常替代值 -1 |

本次重排同时检查了 41 个重点方法、92 个速查条目和文档跳转。六个完整实验已重新编译运行，正文新增的 17 个带 `main` 的示例也已按 Java 21 编译运行通过。局部示例、业务占位代码和明确标注的错误反例另作逻辑与语法核对。

这些运行结果用于验证教学示例的基本行为，不构成对全部调度情况的穷尽验证；正文局部片段和概念伪代码未作为独立程序运行。

<a id="sec-17"></a>

## 16. 方法对照、常见误区与排错

### 16.1 常见等待方法对照

表中的“释放锁”只讨论调用方已经持有的锁，不表示实现内部完全不使用锁。

| 方法 | 主要等待对象 | 释放调用方的哪些锁 | 中断通常如何响应 |
|---|---|---|---|
| `Thread.sleep()` | 时间 | 不释放已持有的锁 | 抛 `InterruptedException`，清除标记 |
| `Object.wait()` | 对象上的条件通知 | 释放该对象监视器 | 重新获取监视器后按中断规则处理 |
| `Thread.join()` | 目标线程结束 | 不释放任意业务锁；平台目标经典等待路径释放内部等待使用的目标监视器 | 调用者抛 `InterruptedException` |
| `ReentrantLock.lock()` | 这把锁可被获取 | 不释放其他已持有锁 | 不以中断退出获取 |
| `lockInterruptibly()` | 这把锁可被获取 | 不释放其他已持有锁 | 抛 `InterruptedException` |
| `Condition.await()` | 关联条件 | 释放关联锁，返回前重新获取 | 依据条件实现协调中断与通知 |
| `Future.get()` | 异步结果 | 不释放任意业务锁 | 调用者可中断等待 |
| `CompletableFuture.join()` | 完成阶段结果 | 不释放任意业务锁 | 不以 `InterruptedException` 退出 |
| `CountDownLatch.await()` | 计数归零 | 不释放任意业务锁 | 抛 `InterruptedException` |
| `Semaphore.acquire()` | 可用许可 | 不释放任意业务锁 | 抛 `InterruptedException` |
| `LockSupport.park()` | 单许可、中断等 | 不释放已持有锁 | 返回，保留中断状态 |

### 16.2 常见方法选择

| 需求 | 常见入口 | 需要确认的条件 |
|---|---|---|
| 等待一条明确线程结束 | `Thread.join()` | 等待时不占住它必需的锁 |
| 提交有结果任务 | `submit()` + `Future.get()` | 拒绝、异常、超时分别处理 |
| 启动任务后继续串联步骤 | `thenApply()`、`thenCompose()` | 函数返回普通值还是异步阶段 |
| 等待多个准备任务结束 | `CountDownLatch` | 完成计数不能代替成功判断 |
| 多个任务每轮互相集合 | `CyclicBarrier`、`Phaser` | 参与者与可用执行线程匹配 |
| 限制同时访问外部资源的数量 | `Semaphore` | 成功获取与释放严格配对 |
| 传递生产与消费数据 | `BlockingQueue` | 队列容量及关闭协议 |
| 多线程更新一个计数 | `AtomicInteger`、`AtomicLong` | 是否需要复合条件更新 |
| 高竞争下只做统计累加 | `LongAdder` | 是否能接受并发读取非原子快照 |
| 为一个 key 原子创建值 | `computeIfAbsent()` | 回调短小，不在回调中修改同一 map |
| 保存当前线程的请求上下文 | `ThreadLocal` | 任务结束时清理或恢复 |

### 16.3 超时、中断、取消和失败的区别

| 情况 | 表达什么 | 不自动表达什么 |
|---|---|---|
| 等待超时 | 调用方本次未及时观察到所等结果 | 任务停止、业务失败、自动回滚 |
| 当前线程被中断 | 有执行路径提出中断请求 | 目标业务已经结束 |
| Future 被取消 | 结果对象进入取消状态 | 实际执行代码必然退出 |
| 任务异常完成 | 任务或阶段以异常结束 | 外部副作用已全部撤销 |
| 线程池已 shutdown | 已发起关闭，不再接受新任务 | 已提交任务全部结束 |

### 16.4 十个常见误区

1. **直接 `run()` 等同于启动线程。** 普通平台线程的直接调用由调用方执行；虚拟线程的直接调用还有不同规则。
2. **`sleep()` 能让其他线程拿到当前锁。** 它不释放锁，可能延长其他线程等待。
3. **通知等同于条件成立。** 唤醒后必须重新检查受同步保护的条件。
4. **`notify()` 或 `signal()` 之后立即交出锁。** 通知方仍持有锁，退出或解锁后等待者才有机会获取。
5. **`interrupt()` 等同于强制终止。** 它依赖任务或阻塞 API 配合。
6. **`isDone()` 为 true 就说明业务成功。** 失败、取消也算完成。
7. **获取结果超时会自动取消任务。** 大多数等待超时只结束这次等待。
8. **线程安全容器让所有多步业务逻辑自动安全。** 单次方法的原子性不能自动扩展为多步事务。
9. **`volatile` 可以保护 `count++`。** 可见性保证不替代复合操作原子性。
10. **所有 `CompletableFuture` 回调都在新线程执行。** 执行位置由方法种类、注册时机和执行器决定。

### 16.5 根据现象定位问题

| 现象 | 优先检查 |
|---|---|
| 程序不退出 | 是否有未关闭线程池、非守护线程或不响应中断的循环 |
| `wait()`、通知或解锁抛监视器异常 | 是否持有被操作对象的正确锁，是否多解锁 |
| 某个线程一直等条件 | 条件是否在同一锁下修改，通知是否对应正确队列，是否漏完成计数 |
| 线程池任务等待自己提交的任务 | 子任务是否排在被等待者占满的同一线程池中 |
| 高 CPU 但任务不推进 | 空循环、无退避 CAS 重试、反复 `tryLock()` 或中断后不断 `park()` 返回 |
| 队列或内存越来越大 | 是否使用无界任务队列，处理速度是否长期低于提交速度 |
| 任务失败却没有日志 | 是否 `submit()` 后丢弃 Future，或者异常被转换成默认结果 |
| 取消后仍产生外部操作 | 是否检查中断，相关 I/O 是否支持取消，是否需要业务截止时间 |
| 线程池中出现上一个请求的数据 | ThreadLocal 是否清理，框架嵌套上下文是否恢复 |
| 屏障一直不能放行 | 参与者数量是否正确，是否有参与任务尚未获得线程 |

诊断应结合线程栈、锁信息、队列长度、任务记录和失败原因。依靠多加几个 `sleep()` 改变时序，可能暂时隐藏问题，却不会建立新的同步保证。

<a id="sec-18"></a>

## 17. 学习检查与参考答案

### 17.1 练习题

1. `new Thread(task)`、`worker.run()`、`worker.start()` 分别完成什么？
2. main 调用 `worker.join()` 后，谁等待，谁继续运行？
3. 为什么同步块里的 `sleep()` 不能帮助另一个线程修改同一锁保护的数据？
4. `wait()` 为什么需要持锁，又为什么使用 `while` 而不是 `if`？
5. `isInterrupted()` 和 `interrupted()` 的作用对象与状态变化有什么区别？
6. 为什么恢复中断标记后继续无限调用 `sleep()` 可能造成异常循环？
7. `tryLock()` 返回 `true` 只是查询成功，还是已经获取锁？
8. 为什么 `maximumPoolSize` 很大但线程池仍只使用少量工作线程？
9. `get(1, SECONDS)` 超时后任务是否还可能执行？
10. `FutureTask.cancel(true)` 与 `CompletableFuture.cancel(true)` 有什么不同？
11. `thenApply()` 与 `thenCompose()` 分别适合什么函数？
12. `anyOf()` 与 `invokeAny()` 对失败任务的选择规则相同吗？
13. `counter.set(counter.get() + 1)` 为什么会丢失更新？
14. `AtomicReference` 比较相等引用与 `ConcurrentHashMap.replace()` 比较相等值有什么区别？
15. 为什么 `CountDownLatch.countDown()` 放在 `finally` 中也不能证明任务成功？
16. 为什么在线程池任务中遗忘 `ThreadLocal.remove()` 会影响以后任务？
17. 为什么 `allOf()` 不直接返回所有结果的列表？
18. 六个完整程序分别用哪种机制保证主线程观察到任务结束或结果？

### 17.2 参考答案

1. 创建对象保存任务；普通平台线程的 `run()` 由调用者直接执行；`start()` 安排新线程执行任务。
2. main 等待 worker 结束，worker 继续执行；worker 若还需要 main 占有的锁则可能无法推进。
3. 休眠不释放同步块持有的监视器锁。
4. 持锁使条件检查与开始等待协调；循环用于处理虚假唤醒和其他线程先消耗资源。
5. 前者查询目标线程且不清除，后者读取并清除当前线程标记。
6. 标记恢复后下一次可中断等待可能立即抛异常，反复恢复又继续会不断失败。
7. 已经实际获取锁，必须对应释放。
8. 通常优先排队；无界队列持续接收任务，非核心扩容条件很少触发。
9. 可能。超时结束的是本次等待，任务取消需要额外动作。
10. FutureTask 尝试中断记录的运行线程；CompletableFuture 的该参数不负责中断后台计算。
11. `thenApply()` 的函数返回普通结果；`thenCompose()` 的函数返回完成阶段并连接其结果。
12. 不同。`anyOf()` 接受先完成的异常；`invokeAny()` 寻找成功结果。
13. 读取和写入是两个独立操作，竞争线程可能基于相同旧值覆盖彼此更新。
14. 原子引用使用身份比较，映射条件替换使用值相等性。
15. 最终块可以在失败时执行，所以计数表示任务结束，失败需另记。
16. 工作线程被复用，遗留绑定可能造成上下文污染和对象长期存留。
17. 该接口是完成屏障，返回 `Void`；结果从各自的阶段取得。
18. 前两例通过 `Thread.join()`；缓冲区通过锁、条件与 Future；Future 例通过结果等待和额外退出计数；CAS 例通过原子操作及全部 Future 等待；异步例通过依赖阶段、`allOf()` 和结果等待。

### 17.3 分阶段学习目标

进一步自测可参阅配套的 [Java 并发编程面试题与参考答案](Java并发编程面试题与参考答案.md)。

| 阶段 | 能够独立完成的任务 |
|---|---|
| 线程基础 | 解释调用方与执行方，正确启动线程并等待结束 |
| 等待与中断 | 编写条件循环等待，设计可中断的任务退出 |
| 锁与任务管理 | 确保锁配对，处理拒绝、任务失败、超时与关闭 |
| 并发数据 | 判断单次原子操作与业务整体原子性的边界 |
| 异步组合 | 根据依赖关系选择转换、串联、合并及异常处理 |
| 进阶 | 结合实际 JDK 版本阅读同步器和虚拟线程实现 |

<a id="sec-19"></a>

## 18. 官方资料与源码入口

### 18.1 API 契约查询

优先查询与实际使用 JDK 版本对应的官方 API 页面，重点阅读方法参数、返回值、异常、内存一致性与实现说明。正文中的官方链接位于对应规则附近，以下表格提供集中入口。

| 主题 | 官方资料 |
|---|---|
| 线程及监视器 | [Thread](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Thread.html)、[Object](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/Object.html) |
| 内存模型 | [JLS 第 17 章](https://docs.oracle.com/javase/specs/jls/se21/html/jls-17.html)、[并发包说明](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/package-summary.html) |
| 锁与条件 | [ReentrantLock](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/ReentrantLock.html)、[Condition](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/Condition.html)、[LockSupport](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/LockSupport.html) |
| 读写访问 | [ReentrantReadWriteLock](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/ReentrantReadWriteLock.html)、[StampedLock](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/locks/StampedLock.html) |
| 执行器 | [Executors](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Executors.html)、[ExecutorService](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ExecutorService.html)、[ThreadPoolExecutor](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ThreadPoolExecutor.html) |
| 调度 | [ScheduledExecutorService](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ScheduledExecutorService.html) |
| 结果 | [Future](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Future.html)、[FutureTask](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/FutureTask.html)、[CompletableFuture](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CompletableFuture.html) |
| 原子更新 | [AtomicInteger](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicInteger.html)、[AtomicReference](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/AtomicReference.html)、[LongAdder](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/atomic/LongAdder.html) |
| 阶段协调 | [CountDownLatch](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CountDownLatch.html)、[CyclicBarrier](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/CyclicBarrier.html)、[Semaphore](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Semaphore.html)、[Phaser](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/Phaser.html) |
| 容器与线程绑定 | [BlockingQueue](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/BlockingQueue.html)、[ConcurrentHashMap](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/concurrent/ConcurrentHashMap.html)、[ThreadLocal](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/ThreadLocal.html) |

### 18.2 源码阅读路径

本手册使用 `jdk-21+35` 标签作为明确的 OpenJDK 21 实现参照。后续维护版本可能有修复，实际工程应查询所部署版本的源码。

| 起点 | 适合关注的实现问题 |
|---|---|
| [Thread.java](https://github.com/openjdk/jdk/blob/jdk-21%2B35/src/java.base/share/classes/java/lang/Thread.java) | 平台/虚拟线程分支、启动、休眠、中断、终止等待 |
| [ThreadPoolExecutor.java](https://github.com/openjdk/jdk/blob/jdk-21%2B35/src/java.base/share/classes/java/util/concurrent/ThreadPoolExecutor.java) | `execute()` 三阶段接受流程、工作线程复用和关闭状态 |
| [FutureTask.java](https://github.com/openjdk/jdk/blob/jdk-21%2B35/src/java.base/share/classes/java/util/concurrent/FutureTask.java) | 完成状态、结果发布、等待者与取消竞争 |
| [AbstractQueuedSynchronizer.java](https://github.com/openjdk/jdk/blob/jdk-21%2B35/src/java.base/share/classes/java/util/concurrent/locks/AbstractQueuedSynchronizer.java) | 获取/释放模板、同步队列、条件队列和可中断等待 |

源码阅读应先明确方法的行为契约，再追踪状态变量及成功、失败、中断、超时路径。内部方法名和某个优化步骤可以变化，调用者能够依赖的公开契约才是正确编程的基础。
