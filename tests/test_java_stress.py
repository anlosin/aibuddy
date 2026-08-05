r"""
多语言技术问答压力测试脚本
============================
读取 model_config.json 中的 API 配置，模拟 worker.py 的调用逻辑，
循环发送技术面试问题（支持 Java / Python / Go），统计每轮回答的
字数、耗时和成功/失败状态，并输出模型返回的完整答案，
直到 API 报错或手动中断（Ctrl+C）。

系统提示约束:
    - 必须使用中文回答
    - 每个回答不少于 1000 字

用法:
    .venv\Scripts\python.exe test_java_stress.py                              # 默认全部 601 题顺序测试
    .venv\Scripts\python.exe test_java_stress.py --lang java                   # 仅 Java 183 题
    .venv\Scripts\python.exe test_java_stress.py --lang python --max-rounds 30 # Python 题，30 轮
    .venv\Scripts\python.exe test_java_stress.py --lang go --no-context        # Go 题，每轮独立
    .venv\Scripts\python.exe test_java_stress.py --show-answer 500             # 答案截断500字
    .venv\Scripts\python.exe test_java_stress.py --show-answer off             # 不输出答案
"""
import json
import time
import csv
import os
import sys
import signal
import argparse
from datetime import datetime

# ── 复用项目配置 ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qwen_app.config import load_config, make_openai_client

try:
    from openai import OpenAI
except ImportError:
    print("[FATAL] 需要安装 openai 库: pip install openai")
    sys.exit(1)


# ═══════════════════════════════════════════
#  Java 技术问题池（183 题，覆盖 27 个方向）
# ═══════════════════════════════════════════
JAVA_QUESTIONS = [
    # ── 核心基础 ──
    "Java 中 HashMap 和 ConcurrentHashMap 的区别是什么？",
    "解释 Java 内存模型（JMM）中的 happens-before 原则",
    "Java 中的 volatile 关键字有什么作用？和 synchronized 有什么区别？",
    "什么是 Java 的 CAS 操作？ABA 问题怎么解决？",
    "Java 中 ThreadLocal 的实现原理和使用场景是什么？",
    "解释 Java 类加载机制中的双亲委派模型",
    "Java 中强引用、软引用、弱引用、虚引用的区别？",
    "什么是 Java 的 happens-before 内存屏障？",
    "Java 中 synchronized 的锁升级过程是怎样的？",
    "ReentrantLock 和 synchronized 的区别是什么？",

    # ── 并发编程 ──
    "Java 线程池的核心参数有哪些？拒绝策略有几种？",
    "CompletableFuture 相比 Future 有什么优势？",
    "Java 中 Semaphore、CountDownLatch、CyclicBarrier 的区别？",
    "什么是 Java 的 ForkJoinPool？适合什么场景？",
    "Java 中如何正确停止一个线程？为什么 stop() 被废弃？",
    "Java 虚拟线程（Virtual Thread / Project Loom）解决了什么问题？",
    "StampedLock 是什么？相比 ReentrantReadWriteLock 有什么优势？",
    "Java 中 Exchanger 的作用和使用场景",

    # ── JVM ──
    "JVM 的垃圾回收算法有哪些？各有什么优缺点？",
    "G1 收集器 和 ZGC 的区别是什么？",
    "如何排查 Java 应用的 OOM 问题？",
    "JVM 中对象的生命周期是怎样的？什么时候会被回收？",
    "什么是 JVM 的逃逸分析？有什么优化效果？",
    "Java 中如何分析 CPU 100% 的问题？给出排查步骤",
    "JVM 的 -XX:MaxMetaspaceSize 和 -XX:MaxPermSize 有什么区别？",

    # ── 集合框架 ──
    "ArrayList 和 LinkedList 的底层实现和性能差异？",
    "HashMap 1.8 相比 1.7 有哪些改进？为什么引入红黑树？",
    "Java 中 TreeMap 的底层实现原理？",
    "CopyOnWriteArrayList 适合什么场景？有什么缺点？",

    # ── Java 8+ 新特性 ──
    "Java 8 的 Stream API 中 map 和 flatMap 的区别？",
    "Java 8 的 Optional 如何正确使用？有哪些最佳实践？",
    "Java 8 中函数式接口和默认方法的作用",
    "Java 14 的 Records 是什么？解决了什么问题？",
    "Java 16 的 Pattern Matching for instanceof 有什么好处？",
    "Java 17 的 Sealed Classes 是什么？",

    # ── Spring 生态 ──
    "Spring IOC 容器的启动流程是怎样的？",
    "Spring AOP 的实现原理？JDK 动态代理和 CGLIB 的区别？",
    "Spring Bean 的生命周期是怎样的？",
    "Spring 事务传播机制有几种？各自的行为是什么？",
    "Spring Boot 自动配置的原理是什么？",
    "Spring Cloud 和 Spring Cloud Alibaba 的关系？",

    # ── 设计模式 & 架构 ──
    "在 Java 中如何实现一个线程安全的单例模式？",
    "Java 中观察者模式和发布订阅模式的区别？",
    "Java 中策略模式和状态模式的区别？",
    "如何用 Java 实现一个简单的责任链模式？",

    # ── 性能 & 调优 ──
    "Java 中如何排查 Full GC 频繁的问题？",
    "Java 应用性能调优的常用工具有哪些？",
    "什么是 Java 的 JIT 编译？C1 和 C2 编译器有什么区别？",
    "Java 中如何减少 GC 停顿时间？",

    # ── IO & 网络 ──
    "Java NIO 和 BIO 的区别？Selector 的作用是什么？",
    "Java 中 Netty 的线程模型是怎样的？",
    "Java 中如何实现零拷贝（zero-copy）？",

    # ── 数据库 & 持久层 ──
    "MyBatis 中 #{} 和 ${} 的区别是什么？为什么推荐用 #{}？",
    "MyBatis 一级缓存和二级缓存的区别？各自的作用范围？",
    "JPA 和 MyBatis 的适用场景分别是什么？如何选择？",
    "Hibernate 中 get() 和 load() 方法的区别？",
    "JDBC 中 Statement 和 PreparedStatement 的区别？",
    "数据库连接池（HikariCP）的核心参数和调优策略？",
    "MyBatis-Plus 相比原生 MyBatis 有哪些增强？",
    "如何处理数据库读写分离场景下的数据一致性问题？",

    # ── 微服务架构 ──
    "Spring Cloud 中服务注册与发现的原理？Eureka 和 Nacos 的区别？",
    "Spring Cloud Gateway 的过滤器链机制是怎样的？",
    "微服务间如何实现分布式事务？Seata 的 AT 模式原理是什么？",
    "微服务架构中如何实现服务降级和熔断？Sentinel 和 Hystrix 的区别？",
    "Spring Cloud Config 和 Nacos 配置中心的使用场景对比？",
    "微服务链路追踪（Sleuth + Zipkin）的实现原理？",
    "什么是服务网格（Service Mesh）？和微服务框架的关系？",

    # ── 消息队列 ──
    "Kafka 如何保证消息不丢失？acks 参数的作用？",
    "RabbitMQ 的消息确认机制（ACK）和死信队列是什么？",
    "RocketMQ 的事务消息实现原理是什么？",
    "消息队列中如何保证消息的顺序性？",
    "Kafka 的 ISR 机制是什么？为什么能保证高可用？",
    "消息队列如何解决重复消费的问题？有哪些幂等方案？",
    "消息积压了怎么办？有哪些排查思路和解决方案？",

    # ── 分布式系统 ──
    "CAP 理论在 Java 分布式系统中的应用和权衡？",
    "分布式锁的实现方式有哪些？Redisson 的原理是什么？",
    "一致性哈希算法的原理和在分布式缓存中的应用？",
    "如何用 Zookeeper 实现分布式配置管理和服务发现？",
    "分布式 ID 生成方案有哪些？雪花算法（Snowflake）的原理？",
    "什么是分布式 Session？如何实现？",

    # ── 安全 ──
    "Spring Security 的认证流程是怎样的？过滤器链原理？",
    "JWT 的组成结构和验证流程？优缺点是什么？",
    "OAuth 2.0 的四种授权模式分别适用于什么场景？",
    "如何防止 SQL 注入、XSS 和 CSRF 攻击？",
    "对称加密和非对称加密的区别？Java 中有哪些常用实现？",
    "RBAC 和 ABAC 权限模型的区别？各自适用场景？",

    # ── 测试 ──
    "JUnit 5 相比 JUnit 4 有哪些改进和新特性？",
    "Mockito 中 @Mock 和 @Spy 的区别是什么？",
    "什么是单元测试的覆盖率？行覆盖和分支覆盖的区别？",
    "如何对 Spring Boot 应用进行集成测试？@SpringBootTest 的作用？",
    "TDD（测试驱动开发）的核心流程是什么？有什么优缺点？",

    # ── 构建 & 工程化 ──
    "Maven 的依赖传递和依赖冲突如何解决？",
    "Gradle 相比 Maven 有哪些优势？什么场景下更适合用 Gradle？",
    "Maven 的生命周期（Lifecycle）中 clean、compile、package、install 的关系？",
    "CI/CD 流水线中 Java 应用的自动化构建和部署流程是怎样的？",
    "如何在 Maven 多模块项目中管理公共依赖版本？",

    # ── 更多并发 & AQS ──
    "AQS（AbstractQueuedSynchronizer）的实现原理是什么？",
    "LockSupport.park() 和 unpark() 的底层实现原理？",
    "Java 中的 LongAdder 和 AtomicLong 有什么区别？",
    "ThreadLocal 的内存泄漏问题是怎么产生的？如何避免？",
    "Java 的 happens-before 规则中，volatile 写和读之间有什么关系？",
    "Java 中 disruptor 无锁队列的原理和适用场景？",

    # ── 更多 JVM ──
    "JVM 的类加载器有哪几种？各自负责加载什么路径？",
    "如何打破双亲委派模型？Tomcat 为什么要打破它？",
    "JVM 调优常用参数有哪些？每个参数的作用？",
    "Java 对象头（Object Header）包含哪些信息？",
    "什么是 TLAB（Thread Local Allocation Buffer）？",
    "JVM 中的直接内存是什么？如何避免直接内存溢出？",

    # ── 更多 Spring ──
    "Spring MVC 的请求处理流程是怎样的？DispatcherServlet 的作用？",
    "@Autowired、@Resource 和 @Inject 的区别是什么？",
    "Spring 的循环依赖是如何解决的？什么情况下解决不了？",
    "Spring 中 @Transactional 失效的场景有哪些？",
    "Spring Event 事件机制的使用场景和实现原理？",
    "Spring 的 @Async 注解的原理是什么？默认线程池的坑有哪些？",

    # ── 数据结构算法实战 ──
    "Java 中如何用 PriorityQueue 实现 TopK 问题？",
    "Java 的 BitSet 适合什么场景？布隆过滤器的原理？",
    "用 Java 实现一个 LRU 缓存？LinkedHashMap 如何支持？",
    "跳表（SkipList）的原理？ConcurrentSkipListMap 的时间复杂度？",
    "Java 中如何实现一个生产者-消费者模型？有几种方式？",

    # ── Redis 缓存深度 ──
    "Redis 缓存穿透、缓存击穿、缓存雪崩的区别和解决方案？",
    "Redis 的过期策略有哪些？惰性删除和定期删除如何配合？",
    "Redis 内存淘汰策略（LRU/LFU/TTL）的区别和选型？",
    "Redis 持久化 RDB 和 AOF 的区别？混合持久化是什么？",
    "Redis 主从复制的原理？全量同步和增量同步的流程？",
    "Redis Cluster 的数据分片原理？16384 个槽是如何分配的？",
    "Redis 哨兵模式的工作原理？主观下线和客观下线的区别？",
    "如何用 Redis 实现分布式锁？RedLock 算法解决了什么问题？",
    "Redis 的 String 类型底层 SDS 相比 C 字符串有什么优势？",
    "Redis 的 ZSet 跳表实现原理？为什么不用红黑树？",
    "缓存和数据库双写一致性问题怎么解决？有哪些方案？",

    # ── MySQL 深度优化 ──
    "MySQL 的 InnoDB 引擎中，B+ 树索引和 Hash 索引的区别？",
    "MySQL EXPLAIN 中 type 字段从优到劣的排序是什么？",
    "MySQL 覆盖索引和索引下推分别是什么？有什么优化效果？",
    "MySQL 的 MVCC 实现原理？ReadView 是如何工作的？",
    "MySQL 的隔离级别有哪些？各自解决了哪些并发问题？",
    "MySQL 分库分表的常用方案？ShardingSphere 的核心原理？",
    "MySQL 慢查询如何排查和优化？有哪些常用工具？",
    "MySQL 中一条 UPDATE 语句的执行流程是怎样的？",
    "MySQL 自增主键用完了会怎样？有什么替代方案？",
    "MySQL 中 count(*)、count(1)、count(字段) 的性能差异？",

    # ── 系统设计 ──
    "如何设计一个秒杀系统？需要考虑哪些核心问题？",
    "如何设计一个短链接系统？短码生成策略有哪些？",
    "如何设计一个分布式限流系统？令牌桶和漏桶算法的区别？",
    "如何设计一个高可用的配置中心？需要考虑哪些要点？",
    "如何设计一个千万级用户的 IM 系统架构？",
    "如何设计一个分布式定时任务调度系统？",
    "接口幂等性如何设计？数据库唯一索引和 Token 机制各有什么优劣？",
    "大表数据迁移如何做到零停机？有哪些常用方案？",

    # ── 容器化 & DevOps ──
    "Docker 镜像的分层结构原理是什么？如何优化镜像大小？",
    "Docker 的网络模式有哪几种？各自的使用场景？",
    "Kubernetes 中 Pod 的调度流程是怎样的？",
    "Kubernetes 的 Service 有哪几种类型？ClusterIP 和 NodePort 的区别？",
    "K8s 中如何实现滚动更新和回滚？RollingUpdate 的策略参数？",
    "什么是 GitOps？和传统 CI/CD 有什么区别？",

    # ── 网络协议 ──
    "HTTP/1.1、HTTP/2、HTTP/3 的主要区别和改进？",
    "HTTPS 的握手流程是怎样的？TLS 1.3 相比 1.2 简化了什么？",
    "TCP 三次握手和四次挥手的详细过程？为什么是三次和四次？",
    "TCP 的拥塞控制算法有哪些？BBR 相比 CUBIC 有什么优势？",
    "DNS 解析的完整流程是怎样的？CDN 在其中扮演什么角色？",
    "WebSocket 和 HTTP 长轮询的区别？各自适用场景？",

    # ── Linux & 操作系统 ──
    "Linux 中如何排查一个 Java 进程 CPU 占用过高的问题？",
    "Linux 的虚拟内存机制是怎样的？page fault 是什么？",
    "Linux IO 模型有哪些？select、poll、epoll 的区别？",
    "Linux 中如何查看和分析线程堆栈？jstack 的使用技巧？",
    "什么是零拷贝？mmap 和 sendfile 的实现原理？",

    # ── DDD & 架构设计 ──
    "领域驱动设计（DDD）中实体、值对象、聚合根的区别？",
    "什么是 CQRS 模式？和事件溯源（Event Sourcing）如何配合？",
    "六边形架构（端口适配器模式）的核心理念是什么？",
    "如何做技术选型？评估一个技术方案时应该考虑哪些维度？",
    "常见的系统拆分策略有哪些？按业务和按领域拆分的利弊？",
    "什么是防腐层（Anti-Corruption Layer）？在微服务中如何应用？",

    # ── 源码解读 ──
    "Spring IOC 容器中 BeanFactory 和 ApplicationContext 的继承体系？",
    "Spring Boot 的启动流程中，SpringApplication.run() 内部做了哪些事？",
    "MyBatis 如何通过 Mapper 接口生成代理对象？MapperProxy 的原理？",
    "Netty 中 ChannelPipeline 的责任链模式是如何实现的？",
    "Dubbo 的服务导出和服务引入流程分别是怎样的？",
    "RocketMQ 的存储架构中，CommitLog 和 ConsumeQueue 的关系？",

    # ── 排障实战 ──
    "线上服务突然出现大量 5xx 错误，你的排查思路是什么？",
    "Java 应用频繁 Full GC 但堆内存使用率不高，可能是什么原因？",
    "数据库连接池耗尽（Connection pool exhausted）怎么排查和解决？",
    "微服务调用链中超时时间如何合理设置？雪崩效应如何避免？",
    "日志中频繁出现 SocketTimeoutException: Read timed out，如何定位？",
    "一个 SQL 在测试环境很快，上线后变慢，可能的原因有哪些？",
]


# ═══════════════════════════════════════════════════════════════════
#  Python 技术问题池（204 题，覆盖 18 个方向）
# ═══════════════════════════════════════════════════════════════════
PYTHON_QUESTIONS = [
    # ── 核心基础 ──
    "Python GIL（全局解释器锁）的原理是什么？对多线程性能有什么影响？",
    "Python 的内存管理机制是怎样的？引用计数和分代垃圾回收如何配合？",
    "Python 中 list 和 tuple 的底层实现有什么区别？为什么 tuple 不可变？",
    "Python 中 dict 的底层实现原理？为什么 Python 3.7+ 保证字典插入顺序？",
    "Python 中 == 和 is 的区别？什么情况下 a is b 为 True？",
    "Python 的可变类型和不可变类型有哪些？函数参数传递是值传递还是引用传递？",
    "Python 中深拷贝和浅拷贝的区别？如何实现深拷贝？copy.deepcopy 的原理？",
    "Python 的小整数缓存机制（-5 到 256）是什么？字符串 intern 机制呢？",
    "Python 的 LEGB 规则是什么？闭包中变量查找的顺序是怎样的？",
    "Python 中 *args 和 **kwargs 的原理和使用场景？",
    "Python 3.8+ 的海象运算符（:=）有什么用？给出实际使用场景",
    "Python 中 global 和 nonlocal 关键字的区别？",
    "Python 中静态方法、类方法和实例方法的区别？各自适用什么场景？",
    "Python 的 property 装饰器原理？如何实现只读属性和计算属性？",
    "Python 中 __new__ 和 __init__ 的区别？单例模式如何利用 __new__ 实现？",
    "Python 中 __slots__ 的作用和优缺点？什么时候应该用？",
    "Python 的鸭子类型是什么？和 Java 的接口有什么区别？",
    "Python 中 __getattr__ 和 __getattribute__ 的区别？如何实现属性代理？",
    "Python 中的 MRO（方法解析顺序）是什么？C3 线性化算法的原理？",
    "Python 中如何判断一个对象的类型？type()、isinstance()、__class__ 的区别？",

    # ── 装饰器与元类 ──
    "Python 装饰器的原理是什么？如何编写带参数的装饰器？",
    "functools.wraps 的作用是什么？为什么装饰器必须使用它？",
    "类装饰器的实现原理和使用场景？和函数装饰器有什么区别？",
    "Python 中元类（metaclass）是什么？type 和 object 的关系？",
    "如何用元类实现 ORM 模型的字段映射？",
    "__init_subclass__ 和元类有什么关系？Python 3.6+ 推荐用哪个？",
    "描述符（descriptor）协议是什么？数据描述符和非数据描述符的区别？",
    "property、classmethod、staticmethod 底层都是描述符吗？",
    "Python 中 __enter__ 和 __exit__ 实现上下文管理器的原理？",
    "contextlib.contextmanager 装饰器的实现原理？yield 在其中的作用？",
    "如何用装饰器实现函数缓存（memoization）？functools.lru_cache 的原理？",
    "如何编写一个可以同时装饰函数和类的通用装饰器？",
    "Python 中抽象基类（ABC）的原理和 @abstractmethod 的作用？",
    "Protocol（结构化子类型）和 ABC 的区别？Python 3.8+ 的 typing.Protocol",
    "Python 的 __class_getitem__ 是什么？如何实现泛型类型？",

    # ── 并发编程 ──
    "Python 的 threading 模块中 Lock 和 RLock 的区别？什么时候用 RLock？",
    "Python 中 threading.Condition 的使用场景？生产者-消费者模型如何实现？",
    "Python multiprocessing 的进程间通信方式有哪些？Queue、Pipe、SharedMemory？",
    "Python asyncio 的事件循环原理？coroutine、task、future 的关系？",
    "async/await 的底层原理是什么？和回调有什么区别？",
    "asyncio.gather 和 asyncio.wait 的区别？各自适用什么场景？",
    "asyncio 中 Semaphore 和 threading.Semaphore 的区别？异步代码中应该用哪个？",
    "Python 中如何实现异步 HTTP 请求？aiohttp 的原理？",
    "Python 3.11+ 的 TaskGroup 和 asyncio.gather 相比有什么优势？",
    "Python 中 concurrent.futures.ThreadPoolExecutor 和 ProcessPoolExecutor 的区别？",
    "Python 中如何避免 GIL 的影响来实现真正的并行计算？",
    "multiprocessing.Pool 和 concurrent.futures.ProcessPoolExecutor 如何选择？",
    "Python 中协程和线程的区别？各自适用什么场景？",
    "asyncio 中如何处理超时？asyncio.wait_for 和 asyncio.timeout 的区别？",
    "Python 中如何实现异步队列？asyncio.Queue 和 queue.Queue 的区别？",
    "Python 的 threading.local 和 contextvars 的区别？异步代码中应该用哪个？",
    "Python 中如何实现定时任务？sched 模块和第三方库的对比？",
    "Python 中如何安全地终止线程？和 Java 一样不能直接 stop 吗？",
    "asyncio 的 run_in_executor 如何在异步代码中调用同步阻塞函数？",
    "Python 的 multiprocessing 中 fork、spawn、forkserver 三种启动方式的区别？",

    # ── 面向对象进阶 ──
    "Python 的多重继承有什么问题？Mixin 模式如何解决？",
    "Python 中 super() 的真正作用是什么？不只是在调用父类方法？",
    "Python 的 __mro__ 属性如何查看方法解析顺序？",
    "Python 中如何实现接口？ABC 和 Protocol 各有什么优劣？",
    "Python 的 dataclass 装饰器有什么用？和 namedtuple、TypedDict 的区别？",
    "Python 中 __eq__ 和 __hash__ 的关系？为什么可变对象默认不可哈希？",
    "Python 的 __repr__ 和 __str__ 的区别？什么时候用哪个？",
    "Python 中如何实现运算符重载？__add__、__radd__、__iadd__ 的区别？",
    "Python 的 __iter__ 和 __next__ 协议？如何实现自定义迭代器？",
    "生成器函数和生成器表达式的区别？yield 的底层原理是什么？",
    "Python 中 send() 方法向生成器传值的原理？这和协程有什么关系？",
    "Python 中 yield from 的作用？和直接 yield 有什么区别？",
    "Python 的 __del__ 方法为什么不可靠？__del__ 和 atexit 的区别？",
    "Python 中如何实现弱引用？weakref 模块的使用场景？",
    "Python 的枚举类型 Enum 的实现原理？IntEnum、StrEnum 的区别？",

    # ── 标准库深潜 ──
    "collections 模块中 defaultdict、Counter、OrderedDict、deque 的使用场景？",
    "itertools 模块中 chain、groupby、combinations、permutations 的使用？",
    "functools 模块中 reduce、partial、wraps、lru_cache 的原理和用法？",
    "os 和 pathlib 模块的区别？为什么 Python 3.4+ 推荐用 pathlib？",
    "logging 模块的层级结构？如何配置不同级别的日志输出到不同目标？",
    "re 模块中 match、search、findall、sub 的区别？贪婪匹配和非贪婪匹配？",
    "json 模块中 dumps/loads 和 dump/load 的区别？如何处理 datetime 序列化？",
    "pickle 序列化的安全风险是什么？什么时候不该用 pickle？",
    "subprocess 模块中 Popen 和 run 的区别？如何捕获子进程输出？",
    "typing 模块中 TypeVar、Generic、Union、Optional、Callable 的用法？",
    "dataclasses 模块中 field、asdict、astuple 的作用？",
    "enum 模块中 auto() 的原理？如何自定义枚举值？",
    "contextlib 模块中 suppress、redirect_stdout、ExitStack 的使用场景？",
    "inspect 模块可以做什么？获取函数签名、源代码、调用栈？",
    "traceback 模块如何格式化异常信息？traceback.format_exc() 的用法？",

    # ── Web 框架 ──
    "Django 的 ORM 中 select_related 和 prefetch_related 的区别？",
    "Django 中间件（Middleware）的执行流程？请求和响应分别经过哪些处理？",
    "Django 的信号（Signals）机制是什么？和观察者模式的关系？",
    "Django 的 request/response 生命周期是怎样的？从 URL 到视图的全过程？",
    "Flask 的上下文机制（App Context 和 Request Context）是怎样的？",
    "Flask 的蓝图（Blueprint）有什么用？和 Django 的 app 有什么区别？",
    "FastAPI 的依赖注入系统原理？Depends 的实现机制？",
    "FastAPI 中 Pydantic 模型校验的原理？和 dataclass 的区别？",
    "FastAPI 中 async 路由和 sync 路由的处理有什么不同？",
    "Django REST Framework 的序列化器（Serializer）原理？",
    "Django 中如何实现数据库事务？atomic 装饰器的原理？",
    "Django 的 migrations 系统原理？如何处理 migration 冲突？",
    "Flask 中 g 对象和 session 的区别？各自的生命周期？",
    "WSGI 和 ASGI 的区别？为什么 FastAPI 选择 ASGI？",
    "Django 中 select_for_update 的作用？什么时候用来实现悲观锁？",

    # ── 数据科学 ──
    "NumPy 中 ndarray 的广播机制原理？",
    "Pandas 中 loc、iloc、at、iat 的区别？",
    "Pandas 中 groupby 的实现原理？agg、transform、apply 的区别？",
    "Pandas 中 merge 和 join 的区别？how 参数有哪些选项？",
    "NumPy 中 reshape 和 resize 的区别？-1 在 reshape 中表示什么？",
    "Pandas 中 DataFrame 的内存优化有哪些方法？",
    "NumPy 中向量化操作为什么比 Python 循环快？",
    "Pandas 中如何处理缺失值？fillna、dropna、interpolate 的区别？",
    "NumPy 中 np.random 的随机数生成器和 Python random 的区别？",
    "Pandas 中 MultiIndex 多层索引的使用场景和操作方法？",

    # ── 测试与调试 ──
    "pytest 中 fixture 的作用域（scope）有哪些？如何实现 fixture 的参数化？",
    "pytest 中 conftest.py 的作用？fixture 如何跨文件共享？",
    "pytest 中 parametrize 装饰器的原理？如何做数据驱动测试？",
    "unittest.mock 中 patch 和 MagicMock 的区别？如何 mock 外部 API？",
    "pytest 中 marker 的作用？如何自定义 marker 并过滤测试？",
    "Python 中如何测试异步函数？pytest-asyncio 的原理？",
    "pdb 和 ipdb 的常用调试命令有哪些？如何设置条件断点？",
    "Python 中如何做性能分析？cProfile 和 line_profiler 的区别？",
    "Python 中 memory_profiler 如何分析内存使用？tracemalloc 的作用？",
    "pytest 中如何做覆盖率测试？pytest-cov 的原理？",
    "Python 中 hypothesis 属性测试（property-based testing）的原理？",
    "pytest 中 monkeypatch 和 mock.patch 的区别？各自适用场景？",

    # ── 性能优化 ──
    "Python 中如何做性能优化？有哪些常见手段？",
    "Python 的 Cython 是什么？如何用 Cython 加速 Python 代码？",
    "Python 的 Numba JIT 编译器原理？适合什么场景？",
    "Python 中列表推导式为什么比 for 循环快？",
    "Python 中如何减少内存占用？__slots__、generator、array 模块？",
    "Python 的字符串拼接哪种方式最快？+、join、f-string 的性能对比？",
    "Python 中如何利用多进程绕过 GIL？multiprocessing 的开销有哪些？",
    "Python 中 dis 模块如何查看字节码？如何利用它优化性能？",
    "Python 的 GIL 在 IO 密集型和 CPU 密集型任务中的表现差异？",
    "Python 中如何使用 memoryview 来避免不必要的内存拷贝？",
    "Python 的 ctypes 和 cffi 有什么区别？如何调用 C 库？",
    "Python 中如何做连接池优化？数据库连接池和 HTTP 连接池的配置？",

    # ── CPython 内部 ──
    "CPython 的字节码执行引擎是怎样的？为什么说 Python 是解释执行的？",
    "CPython 中 PyFrameObject 和 PyCodeObject 的关系？",
    "CPython 中函数调用的开销在哪里？CALL 字节码的执行过程？",
    "CPython 3.11+ 的 specializing adaptive interpreter（特化解释器）是什么？",
    "CPython 中 GC 的三代回收机制细节？阈值是怎么设定的？",
    "CPython 中 dict 的压缩表（compact dict）实现是什么？Python 3.6+ 的改进？",
    "CPython 中 tuple 的底层结构？为什么空 tuple 是单例？",
    "CPython 中字符串的内部表示有几种？ASCII、UCS1、UCS2、UCS4 的选择？",
    "CPython 中 import 机制的全过程？sys.modules 缓存的作用？",
    "CPython 3.12+ 的 Per-Interpreter GIL 是什么？能解决什么问题？",

    # ── 类型注解与现代 Python ──
    "Python 类型注解的原理？运行时会检查类型吗？",
    "typing 模块中 Literal、Final、Annotated 的用法？",
    "Python 3.10+ 的 match-case 语句（结构化模式匹配）的原理？",
    "Python 3.12+ 的类型参数语法（Type Parameter Syntax）是什么？",
    "Python 中 TypedDict 的作用？和 dataclass 的区别？",
    "mypy 和 pyright 的区别？各自的优势？",
    "Python 中 Protocol 如何实现结构化子类型（鸭子类型的静态版本）？",
    "Python 中 TypeGuard 和 TypeIs 的区别？Python 3.13+ 的改进？",
    "Python 中如何使用 overloading？@overload 装饰器的作用？",
    "Python 3.12+ 的 f-string 改进有哪些？嵌套引号和多行表达式？",

    # ── 文件 IO 与序列化 ──
    "Python 中 open() 函数的 buffering 参数有什么作用？行缓冲和全缓冲的区别？",
    "Python 中 io.StringIO 和 io.BytesIO 的使用场景？",
    "Python 中如何高效读取大文件？逐行读取和分块读取的对比？",
    "Python 中 csv 模块和 pandas.read_csv 的区别？各自适用场景？",
    "Python 中 shutil 和 os 模块在文件操作上的区别？",
    "Python 中 tempfile 模块的使用？TemporaryFile 和 NamedTemporaryFile 的区别？",
    "Python 中如何处理编码问题？encode 和 decode 的 errors 参数有哪些选项？",
    "Python 中 json 序列化时如何处理自定义对象？default 参数的用法？",

    # ── 网络编程 ──
    "Python 中 socket 编程的基本流程？TCP 服务端和客户端如何实现？",
    "Python 中 selectors 模块的作用？和 select、poll、epoll 的关系？",
    "Python 中如何实现 HTTP 服务器？http.server 和 WSGI 的关系？",
    "Python 中 requests 库的 Session 对象有什么用？连接池的原理？",
    "Python 中 urllib 和 requests 的区别？为什么推荐 requests？",
    "Python 中如何实现 WebSocket？websockets 库的异步原理？",
    "Python 中如何做 HTTP 长轮询？和 WebSocket 的区别？",
    "Python 中 gRPC 的实现原理？protobuf 序列化的优势？",
    "Python 中如何实现 RPC？xmlrpc 和 jsonrpc 的区别？",
    "Python 中 asyncio 做网络编程和同步 socket 编程的对比？",

    # ── 安全 ──
    "Python 中 hashlib 模块的常用哈希算法？如何安全地存储密码？",
    "Python 中 hmac 模块的作用？如何验证消息完整性？",
    "Python 中 secrets 模块和 random 模块的区别？为什么密码学场景要用 secrets？",
    "Python 中 cryptography 库的对称加密和非对称加密如何实现？",
    "Python 中如何防止 SQL 注入？参数化查询的原理？",
    "Python 中 JWT 的生成和验证流程？PyJWT 库的使用？",
    "Python 中如何实现 OAuth 2.0？authlib 库的使用？",
    "Python 中如何安全地处理用户输入？input 注入的风险有哪些？",

    # ── 数据库与 ORM ──
    "SQLAlchemy 的 Core 和 ORM 模式的区别？各自适用场景？",
    "SQLAlchemy 中 Session 的工作原理？identity map 的作用？",
    "SQLAlchemy 中 eager loading 和 lazy loading 的区别？joinedload、subqueryload、selectinload？",
    "Django ORM 中 F() 表达式和 Q() 对象的作用？",
    "Django ORM 中 annotate 和 aggregate 的区别？",
    "Python 中如何实现数据库连接池？SQLAlchemy 的连接池策略？",
    "SQLAlchemy 中如何处理数据库事务？flush 和 commit 的区别？",
    "Redis-Py 中连接池的原理？pipeline 的作用？",

    # ── 微服务与分布式 ──
    "Python 中 Celery 的工作原理？broker 和 backend 的作用？",
    "Celery 中 delay() 和 apply_async() 的区别？如何配置任务重试？",
    "Python 中如何实现分布式锁？基于 Redis 的实现方案？",
    "Python 中如何做服务发现？consul 和 etcd 的 Python 客户端？",
    "Python 中 FastAPI 如何实现微服务架构？和 Django 的对比？",
    "Python 中如何实现 API 网关？和 Nginx 反向代理的区别？",
    "Python 中 gRPC 微服务通信的优缺点？和 REST API 的对比？",
    "Python 中如何实现分布式任务调度？Celery beat 和 APScheduler 的区别？",

    # ── DevOps 与部署 ──
    "Python 项目的虚拟环境管理：venv、virtualenv、poetry、conda 的对比？",
    "Python 中 setup.py 和 pyproject.toml 的区别？为什么推荐 pyproject.toml？",
    "Python 中如何打包发布到 PyPI？wheel 和 sdist 的区别？",
    "Python 中 Docker 部署的最佳实践？多阶段构建如何减小镜像？",
    "Python 中 Gunicorn 的 worker 模型？sync、gevent、uvicorn worker 的区别？",
    "Python 中 uWSGI 和 Gunicorn 的区别？各自的优势？",
    "Python 中如何做 APM（应用性能监控）？OpenTelemetry 的 Python 集成？",
    "Python 中如何实现配置管理？环境变量、配置文件、配置中心的对比？",

    # ── 函数式编程 ──
    "Python 中 map、filter、reduce 的用法和区别？",
    "Python 中 lambda 表达式的限制和适用场景？",
    "Python 中偏函数（partial）的原理和使用场景？",
    "Python 中如何实现函数组合（function composition）？",
    "Python 中闭包的原理？如何实现计数器或缓存装饰器？",

    # ── 排障实战 ──
    "Python 线程池中的线程如何安全地停止？如何取消未执行的任务？",
    "Python 应用内存持续增长怎么排查？tracemalloc 的使用技巧？",
    "Python 中 Circular Import（循环导入）怎么解决？有哪些方案？",
    "Python 异步代码中出现 BlockingOperationError 怎么排查？",
    "Python 中 requests 请求偶尔超时但 curl 正常，可能是什么原因？",
]


# ═══════════════════════════════════════════════════════════════════
#  Go 技术问题池（200 题，覆盖 18 个方向）
# ═══════════════════════════════════════════════════════════════════
GO_QUESTIONS = [
    # ── 核心基础 ──
    "Go 中 goroutine 的底层实现原理？和 OS 线程的关系是什么？",
    "Go 中 channel 的底层实现？有缓冲和无缓冲 channel 的区别？",
    "Go 中 interface 的内部结构是什么？eface 和 iface 的区别？",
    "Go 中 interface 隐式实现的优缺点？和 Java 的显式 implements 对比？",
    "Go 中 nil interface 和 interface 包含 nil 值的区别？这个坑怎么避免？",
    "Go 中 select 语句的原理？default 分支如何实现非阻塞操作？",
    "Go 中 defer 的执行顺序？defer 和 return 值的关系是什么？",
    "Go 中 defer 在循环中的陷阱？defer 的性能开销有多大？",
    "Go 中 slice 的底层结构？扩容机制是怎样的？容量如何计算？",
    "Go 中 map 的底层实现？为什么 map 不是并发安全的？",
    "Go 中 string 的底层结构？为什么 string 是不可变的？",
    "Go 中值传递和指针传递的选择原则？什么场景该用指针？",
    "Go 中 new 和 make 的区别？各自用于什么类型？",
    "Go 中 struct 的内存对齐规则？如何优化 struct 的内存布局？",
    "Go 中 iota 常量生成器的原理？常见的使用模式有哪些？",
    "Go 中类型断言和类型 switch 的区别？各自的使用场景？",
    "Go 中 method set 的规则？T 和 *T 的方法集有什么不同？",
    "Go 中 embedding（嵌入）和继承的区别？Go 如何实现代码复用？",
    "Go 中包的初始化顺序？init() 函数的执行时机和注意事项？",
    "Go 中 Go module 的版本管理机制？Semantic Import Versioning 是什么？",

    # ── 并发编程 ──
    "Go 的 GMP 调度模型是什么？G、M、P 分别代表什么？",
    "Go 中 goroutine 的栈是怎么增长的？初始栈大小是多少？",
    "Go 中如何控制 goroutine 的并发数量？Worker Pool 模式怎么实现？",
    "Go 中 sync.WaitGroup 的原理和使用方法？有哪些常见陷阱？",
    "Go 中 sync.Once 的实现原理？为什么能保证只执行一次？",
    "Go 中 sync.Mutex 和 sync.RWMutex 的区别？什么时候用 RWMutex？",
    "Go 中 sync.Cond 的使用场景？和 channel 的对比？",
    "Go 中 sync.Map 的原理？和加锁 map 相比有什么优势？",
    "Go 中 sync.Pool 的作用？如何利用它减少 GC 压力？",
    "Go 中 atomic 包提供了哪些操作？CAS 的实现原理？",
    "Go 中 context 包的设计哲学？WithCancel、WithTimeout、WithValue 如何使用？",
    "Go 中如何优雅地取消 goroutine？context.Cancel 的传播机制？",
    "Go 中如何实现超时控制？context.WithTimeout 和 time.After 的区别？",
    "Go 中 channel 的发送和接收操作在底层是如何阻塞和唤醒的？",
    "Go 中如何实现 Fan-In 和 Fan-Out 模式？",
    "Go 中如何实现 Pipeline 模式？stage 之间的错误如何传播？",
    "Go 中 errgroup 包的作用？和 WaitGroup 有什么区别？",
    "Go 中如何检测 goroutine 泄漏？有哪些常见场景？",
    "Go 中 runtime.Gosched()、runtime.LockOSThread() 的作用？",
    "Go 中如何实现定时任务？time.Ticker 和 time.Timer 的区别？",
    "Go 中 select 的随机选择机制？如何实现非阻塞操作？",
    "Go 中如何实现限流？令牌桶和漏桶算法的 Go 实现？",
    "Go 中如何实现分布式锁？基于 Redis 和 etcd 的方案对比？",
    "Go 1.22+ 的 range over int 和 loop variable scoping 修复了什么问题？",
    "Go 中 semaphore.Weighted 的使用？和 channel 实现的信号量有什么区别？",

    # ── 内存与 GC ──
    "Go 的三色标记法是什么？写屏障的作用是什么？",
    "Go 的 GC 触发条件有哪些？GOGC 参数如何调优？",
    "Go 中逃逸分析是什么？什么情况下值会逃逸到堆上？",
    "Go 中如何减少 GC 压力？sync.Pool、对象复用、预分配？",
    "Go 中 runtime.ReadMemStats 如何查看内存信息？",
    "Go 中 pprof 工具如何做内存分析？heap profile 的使用方法？",
    "Go 中 unsafe 包的用途和风险？Pointer 转换规则是什么？",
    "Go 中 cgo 的内存管理？Go 和 C 之间的数据如何传递？",
    "Go 中 finalizer 的作用？runtime.SetFinalizer 有什么限制？",
    "Go 中 mmap 的使用？如何利用 mmap 做大文件处理？",
    "Go 中如何实现自定义内存分配器？arena 分配是什么？",
    "Go 1.22+ 的 memory ballast 技巧是什么？",
    "Go 中 struct tag 的内存开销？tag 存在哪里？",
    "Go 中大 slice 和小 slice 的 GC 行为差异？如何避免引用大数组的一小部分？",
    "Go 中 GOMEMLIMIT 参数的作用？和 GOGC 如何配合？",

    # ── 错误处理 ──
    "Go 中 error 的设计哲学？为什么 Go 选择显式错误处理而不是异常？",
    "Go 中 errors.Is 和 errors.As 的区别？Go 1.13+ 的错误包装机制？",
    "Go 中如何实现自定义 error 类型？Error() 方法的设计？",
    "Go 中 panic 和 recover 的机制？什么场景该用 panic？",
    "Go 中 errors.Join 的作用？Go 1.20+ 的多错误合并？",
    "Go 中如何做错误分类？sentinel error 和 typed error 的对比？",
    "Go 中如何实现错误链？fmt.Errorf 的 %w 动词？",
    "Go 中如何处理 goroutine 中的 panic？recover 的正确位置？",
    "Go 中 error wrapping 的最佳实践？每一层都应该包装错误吗？",
    "Go 中如何实现重试逻辑？exponential backoff 的实现？",
    "Go 中如何做错误监控？Sentry、OpenTelemetry 的错误上报？",
    "Go 中如何区分业务错误和系统错误？错误码如何设计？",

    # ── 标准库 ──
    "Go 中 io.Reader 和 io.Writer 接口的设计哲学？为什么这么基础？",
    "Go 中 io.Copy 的原理？如何实现零拷贝？",
    "Go 中 bufio.Scanner 和 bufio.Reader 的区别？如何逐行读取大文件？",
    "Go 中 bytes.Buffer 和 strings.Builder 的区别？字符串拼接的性能？",
    "Go 中 sort 包的排序算法？slice 排序和自定义排序如何实现？",
    "Go 中 time 包的 Time 类型？时区处理和单调时钟？",
    "Go 中 net/http 包的 Server 架构？Handler 接口的设计？",
    "Go 中 encoding/json 的性能优化？如何处理 JSON tag 和自定义序列化？",
    "Go 中 reflect 包的原理？反射的性能开销有多大？",
    "Go 中 context 包的 WithValue 如何实现？值查找的链表结构？",
    "Go 中 sync.atomic 包中的 Value 类型？原子加载和存储？",
    "Go 中 flag 包和 cobra 库的对比？CLI 工具如何开发？",
    "Go 中 os/exec 包如何执行外部命令？管道和信号处理？",
    "Go 中 crypto 包的常用加密功能？AES、RSA 的实现？",
    "Go 中 database/sql 包的设计？连接池和预编译语句？",

    # ── 测试 ──
    "Go 中 testing 包的基本用法？表驱动测试的模式怎么写？",
    "Go 中 testify 库的常用功能？assert 和 mock 的使用？",
    "Go 中 benchmark 测试怎么写？B.N 的含义是什么？",
    "Go 中如何做 HTTP 测试？httptest.NewServer 和 NewRecorder 的区别？",
    "Go 中如何做 mock 测试？gomock 和 mockery 的对比？",
    "Go 中 t.Run 子测试的用法？并行测试 t.Parallel() 如何实现？",
    "Go 中如何测试覆盖率？go test -cover 的原理？",
    "Go 中 fuzzing 测试怎么写？Go 1.18+ 的原生 fuzz 支持？",
    "Go 中如何做性能基准对比？benchstat 工具的使用？",
    "Go 中 testing.TB 接口的设计？如何在测试和 benchmark 间复用代码？",

    # ── Web 与框架 ──
    "Go 中 net/http 如何实现一个 HTTP 服务器？Handler 和 ServeMux？",
    "Go 中 Gin 框架的中间件机制？和 Echo、Fiber 的对比？",
    "Go 中 Gin 的 Context 设计？和标准库 http.Request 的关系？",
    "Go 中如何实现 WebSocket？gorilla/websocket 和 nhooyr.io/websocket 的对比？",
    "Go 中如何做路由？httprouter 和 Gin 的 radix tree 路由？",
    "Go 中如何实现 RESTful API？资源路由的设计规范？",
    "Go 中如何做请求验证？validator 库的 tag 机制？",
    "Go 中如何统一处理 HTTP 错误响应？中间件模式？",
    "Go 中 Swagger/OpenAPI 文档如何自动生成？swag 库的使用？",
    "Go 中如何实现文件上传和下载？multipart 处理？",
    "Go 中如何做 SSE（Server-Sent Events）？和 WebSocket 的区别？",
    "Go 中 gRPC 服务的实现流程？protobuf 定义和代码生成？",
    "Go 中 gRPC 拦截器（interceptor）的原理？如何实现日志和认证？",
    "Go 中如何实现 GraphQL 服务？gqlgen 库的使用？",
    "Go 中 HTTP/2 和 HTTP/3 的支持？quic-go 的使用？",

    # ── 数据库 ──
    "Go 中 database/sql 的设计模式？为什么返回 *sql.DB 而不是具体连接？",
    "Go 中 sql.DB 的连接池配置？SetMaxOpenConns、SetMaxIdleConns、SetConnMaxLifetime",
    "Go 中 GORM 的链式调用原理？Session 和 WithContext 的设计？",
    "Go 中 GORM 的 Hook 机制？BeforeCreate、AfterUpdate 的使用？",
    "Go 中 sqlx 和 GORM 的区别？什么场景该用 sqlx？",
    "Go 中如何实现数据库迁移？golang-migrate 和 goose 的对比？",
    "Go 中如何处理 SQL 事务？Tx 对象的使用和隔离级别？",
    "Go 中 sql.NullString 和普通 string 的区别？如何处理 NULL 值？",
    "Go 中 ent ORM 框架的代码生成机制？和 GORM 的对比？",
    "Go 中如何实现读写分离？多个数据源如何管理？",
    "Go 中 Redis 客户端 go-redis 的连接池和 Pipeline 机制？",
    "Go 中如何实现缓存模式？cache-aside、write-through 的 Go 实现？",

    # ── 微服务 ──
    "Go 中如何实现服务注册与发现？consul 和 etcd 的 Go 客户端？",
    "Go 中 gRPC 服务发现和负载均衡如何实现？",
    "Go 中如何实现熔断和降级？Hystrix-Go 和 Sentinel-Go 的对比？",
    "Go 中如何实现分布式链路追踪？OpenTelemetry 的 Go 集成？",
    "Go 中如何实现配置中心？viper 和 nacos-sdk-go 的使用？",
    "Go 中 Kratos 框架的设计理念？和 Go-Zero 的对比？",
    "Go 中如何实现消息队列消费？Kafka 和 RabbitMQ 的 Go 客户端？",
    "Go 中如何实现分布式事务？Saga 和 TCC 的 Go 实现？",
    "Go 中如何做 API 网关？Kong 和自研网关的选择？",
    "Go 中如何实现 gRPC 流式通信？Server streaming、Client streaming、Bidirectional？",
    "Go 中 service mesh 的实践？Istio 和 Linkerd 对 Go 服务的影响？",
    "Go 微服务中如何统一错误码？gRPC status code 的使用？",

    # ── 性能调优 ──
    "Go 中 pprof 的使用？CPU profile、heap profile、goroutine profile？",
    "Go 中 trace 工具的使用？runtime tracer 的原理？",
    "Go 中如何分析 goroutine 泄漏？pprof goroutine 的使用？",
    "Go 中如何做火焰图？go-torch 和 pprof 的 -http 模式？",
    "Go 中 benchmark 的常见陷阱？编译器优化对 benchmark 的影响？",
    "Go 中如何优化内存分配？逃逸分析、对象池、预分配？",
    "Go 中如何减少 GC 停顿？GOGC 调优、GOMEMLIMIT、减少堆分配？",
    "Go 中 sync.Pool 的正确使用方式？哪些对象适合放 Pool？",
    "Go 中字符串和 []byte 的转换优化？如何避免拷贝？",
    "Go 中如何做 SIMD 优化？汇编和 cgo 的选择？",
    "Go 中如何减少锁竞争？sync/atomic、分片锁、无锁数据结构？",
    "Go 1.21+ 的 PGO（Profile-Guided Optimization）是什么？如何启用？",

    # ── 设计模式 ──
    "Go 中如何实现单例模式？sync.Once 的使用？",
    "Go 中如何实现工厂模式？函数式选项（Functional Options）模式？",
    "Go 中如何实现观察者模式？channel vs callback？",
    "Go 中如何实现策略模式？函数作为一等公民的优势？",
    "Go 中如何实现装饰器模式？中间件包装的惯用写法？",
    "Go 中如何实现责任链模式？HTTP 中间件的链式调用？",
    "Go 中如何实现 Builder 模式？链式方法调用？",
    "Go 中如何实现 Visitor 模式？Go 的双重分派问题？",
    "Go 中如何实现空对象模式？nil 安全处理？",
    "Go 中如何实现泛型？Go 1.18+ 的类型参数机制？",

    # ── 模块与构建 ──
    "Go module 的 go.mod 文件结构？require、replace、exclude 指令？",
    "Go 中 semver 版本管理？v2+ 模块的 major version 后缀？",
    "Go 中 vendor 目录的作用？何时使用 vendor 模式？",
    "Go 中 GOPRIVATE 和 GOPROXY 的配置？私有模块如何拉取？",
    "Go 中 go generate 的使用？代码生成工具如何开发？",
    "Go 中 CGO_ENABLED 的作用？静态编译和动态链接？",
    "Go 中交叉编译如何实现？GOOS 和 GOARCH 的设置？",
    "Go 中 build tag 和 //go:build 指令的区别？",

    # ── 反射与 unsafe ──
    "Go 中 reflect.Type 和 reflect.Value 的关系？",
    "Go 中如何通过反射调用方法？reflect.Value.Call 的使用？",
    "Go 中 reflect 包的性能优化？如何缓存反射结果？",
    "Go 中 unsafe.Pointer 的三条转换规则？",
    "Go 中如何用 unsafe 绕过类型系统？有什么风险？",
    "Go 中 reflect.DeepEqual 的原理？为什么不能直接用 == 比较复杂类型？",
    "Go 中 struct tag 如何通过反射读取？json、xml、db tag 的处理？",
    "Go 中如何实现通用 Deep Copy？反射和 unsafe 的结合？",

    # ── 云原生与 DevOps ──
    "Go 在 Docker 中的多阶段构建最佳实践？scratch 和 alpine 镜像？",
    "Go 中如何实现 Kubernetes Operator？controller-runtime 的使用？",
    "Go 中如何编写 Kubernetes CRD？client-go 的 Informer 机制？",
    "Go 中如何实现健康检查？Liveness、Readiness、Startup Probe？",
    "Go 中如何做优雅关机？http.Server.Shutdown 和 context 的配合？",
    "Go 中如何实现配置热更新？viper 的 WatchConfig？",
    "Go 中如何做日志收集？structured logging（slog）的使用？",
    "Go 中如何实现 Feature Flag？灰度发布的 Go 实现？",
    "Go 中 gRPC 和 Kubernetes 的配合？健康检查和服务网格？",
    "Go 中如何做 CI/CD？GitHub Actions 和 GitLab CI 的 Go 模板？",

    # ── 网络与协议 ──
    "Go 中 TCP 粘包怎么处理？自定义协议的封包和解包？",
    "Go 中如何实现 TCP 服务器？net.Listen 和 net.Accept 的流程？",
    "Go 中 UDP 服务器怎么实现？和 TCP 服务器的区别？",
    "Go 中如何做 DNS 解析？net.LookupHost 和自定义 Resolver？",
    "Go 中 TLS/HTTPS 的配置？证书管理和 mTLS 的实现？",
    "Go 中如何实现 HTTP 代理？CONNECT 方法和正向代理？",
    "Go 中 HTTP 客户端如何优化？Transport 的连接复用和超时配置？",
    "Go 中如何实现自定义 DNS 负载均衡？resolver 和 balancer 的设计？",
    "Go 中如何处理半关闭状态？TCP 的 FIN_WAIT 状态？",
    "Go 中 HTTP/2 Server Push 的实现？和 HTTP/3 的对比？",

    # ── 工程实践 ──
    "Go 项目如何组织目录结构？标准布局和 Clean Architecture 的实践？",
    "Go 中如何做依赖注入？wire 和 fx 的对比？",
    "Go 中如何实现优雅升级（零停机重启）？socket 传递？",
    "Go 中如何处理时区问题？time.Time 的序列化和反序列化？",
    "Go 中如何做国际化（i18n）？gotext 和 go-i18n 的使用？",
    "Go 中如何实现定时任务调度？cron 库和 robfig/cron 的使用？",
    "Go 中如何做并发安全的单例初始化？sync.Once vs init()？",
    "Go 中如何实现优雅的错误恢复中间件？recover 的正确使用？",
    "Go 中如何做 API 版本管理？URL 版本和 Header 版本的对比？",
    "Go 中如何处理大量并发连接？goroutine-per-connection 模型的优缺点？",
]


# ═══════════════════════════════════════════
#  多语言配置（问题池 + 系统提示）
# ═══════════════════════════════════════════

# 各语言系统提示模板
_SYSTEM_PROMPT_TEMPLATE = (
    "你是一位资深的{lang}技术专家。请严格遵守以下要求：\n"
    "1. 必须使用中文回答所有问题。\n"
    "2. 每个回答的正文内容不少于 1000 字，要求详尽、深入、有理有据，"
    "必要时给出代码示例和实际工程经验。"
)

# 通用系统提示（all 模式下使用）
_SYSTEM_PROMPT_ALL = (
    "你是一位资深的全栈技术专家，精通 Java、Python、Go 等主流编程语言。"
    "请严格遵守以下要求：\n"
    "1. 必须使用中文回答所有问题。\n"
    "2. 每个回答的正文内容不少于 1000 字，要求详尽、深入、有理有据，"
    "必要时给出代码示例和实际工程经验。"
)

# 语言注册表：{语言名: (问题池, 系统提示)}
LANG_REGISTRY = {
    "java":   (JAVA_QUESTIONS,   _SYSTEM_PROMPT_TEMPLATE.format(lang="Java")),
    "python": (PYTHON_QUESTIONS, _SYSTEM_PROMPT_TEMPLATE.format(lang="Python")),
    "go":     (GO_QUESTIONS,     _SYSTEM_PROMPT_TEMPLATE.format(lang="Go")),
    "all":    (JAVA_QUESTIONS + PYTHON_QUESTIONS + GO_QUESTIONS, _SYSTEM_PROMPT_ALL),
}

# 向后兼容：默认系统提示（Java）
SYSTEM_PROMPT = LANG_REGISTRY["java"][1]


# ═══════════════════════════════════════════
#  思考标签解析（与 worker.py 保持一致）
# ═══════════════════════════════════════════
START_TAGS = ["\u8fea\u58eb", "\U0001F914", "<thinking>"]
END_TAGS = ["iever", "\U0001F64B", "</thinking>"]


def _find_first_tag(text, tags):
    best_pos = -1
    best_tag = None
    for tag in tags:
        pos = text.find(tag)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
            best_tag = tag
    return best_pos, best_tag


def strip_thinking(content):
    """移除思考标签内容，只保留正式回答"""
    result = ""
    buffer = content
    in_thinking = False
    while buffer:
        if in_thinking:
            pos, tag = _find_first_tag(buffer, END_TAGS)
            if tag:
                buffer = buffer[pos + len(tag):]
                in_thinking = False
            else:
                break
        else:
            pos, tag = _find_first_tag(buffer, START_TAGS)
            if tag:
                result += buffer[:pos]
                buffer = buffer[pos + len(tag):]
                in_thinking = True
            else:
                result += buffer
                break
    return result


# ═══════════════════════════════════════════
#  统计辅助
# ═══════════════════════════════════════════
def count_words(text):
    """统计字数：中文按字符计数，英文按单词计数"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    # 移除中文后按空格分词统计英文单词
    cleaned = ''.join(c if not ('\u4e00' <= c <= '\u9fff') else ' ' for c in text)
    english_words = len([w for w in cleaned.split() if w])
    return chinese_chars + english_words


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s}s"


# ═══════════════════════════════════════════
#  核心测试逻辑
# ═══════════════════════════════════════════

class StressTester:
    def __init__(self, max_rounds=None, no_context=False, api_timeout=120,
                 delay=30, max_retries=3, answer_mode=("full", 0), lang="java"):
        cfg = load_config()
        self.model_id = cfg.get("model_id", "")
        self.api_key = cfg.get("api_key", "")
        self.base_url = cfg.get("base_url", "")
        self.proxy = cfg.get("proxy", "")
        self.enable_thinking = cfg.get("enable_thinking", False)

        if not self.api_key or not self.base_url:
            print("[FATAL] model_config.json 中缺少 api_key 或 base_url")
            sys.exit(1)

        # 语言配置
        lang_lower = lang.lower()
        if lang_lower not in LANG_REGISTRY:
            print(f"[FATAL] 不支持的语言 '{lang}'，可选: {', '.join(LANG_REGISTRY.keys())}")
            sys.exit(1)
        self.lang = lang_lower
        self.questions, self.system_prompt = LANG_REGISTRY[lang_lower]

        self.client = make_openai_client(self.api_key, self.base_url, self.proxy)
        self.max_rounds = max_rounds
        self.no_context = no_context
        self.api_timeout = api_timeout
        self.delay = delay            # 轮间间隔秒数
        self.max_retries = max_retries  # 429 限流最大重试次数
        self.answer_mode = answer_mode  # ("off"|"full"|"truncate", 截断字数)

        self.results = []  # 每轮统计
        self.messages = []  # 对话上下文
        self.total_chars = 0
        self.total_time = 0.0
        self.round_num = 0
        self.stopped = False
        self.retry_count = 0  # 总重试次数

    def _call_api(self, messages, extra):
        """实际调用 API，返回 (回答文本, 耗时, 错误信息)"""
        start_time = time.time()

        try:
            completion = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                extra_body=extra,
                stream=True,
                timeout=self.api_timeout,
            )

            full_response = ""
            content_buffer = ""
            in_thinking = False

            for chunk in completion:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 标准 reasoning_content 字段
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    continue  # 跳过思考内容，不计入回答

                if hasattr(delta, "content") and delta.content:
                    content_buffer += delta.content
                    # 解析思考标签
                    while content_buffer:
                        if in_thinking:
                            pos, tag = _find_first_tag(content_buffer, END_TAGS)
                            if tag:
                                content_buffer = content_buffer[pos + len(tag):]
                                in_thinking = False
                            else:
                                break
                        else:
                            pos, tag = _find_first_tag(content_buffer, START_TAGS)
                            if tag:
                                full_response += content_buffer[:pos]
                                content_buffer = content_buffer[pos + len(tag):]
                                in_thinking = True
                            else:
                                # 检查是否是标签前缀
                                safe_len = len(content_buffer)
                                for t in START_TAGS:
                                    for i in range(1, min(len(t), len(content_buffer)) + 1):
                                        if content_buffer.endswith(t[:i]):
                                            safe_len = min(safe_len, len(content_buffer) - i)
                                if safe_len > 0:
                                    full_response += content_buffer[:safe_len]
                                content_buffer = content_buffer[safe_len:]
                                break

            # flush
            if content_buffer and not in_thinking:
                full_response += content_buffer

            elapsed = time.time() - start_time

            # 追加助手回复到上下文
            if not self.no_context:
                self.messages.append({"role": "assistant", "content": full_response})

            return full_response.strip(), elapsed, None

        except Exception as e:
            elapsed = time.time() - start_time
            return "", elapsed, str(e)

    def send_question(self, question):
        """发送一个问题，带 429 限流重试，返回 (回答文本, 耗时, 错误信息)"""
        if self.no_context or not self.messages:
            self.messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": question},
            ]
        else:
            self.messages.append({"role": "user", "content": question})

        extra = {}
        if self.enable_thinking:
            extra["enable_thinking"] = True

        total_elapsed = 0.0

        for attempt in range(self.max_retries + 1):
            if self.stopped:
                return "", 0.0, "用户中断"

            answer, elapsed, error = self._call_api(list(self.messages), extra)
            total_elapsed += elapsed

            if error and "429" in error and attempt < self.max_retries:
                wait = 60 * (attempt + 1)  # 第1次等60s，第2次等120s
                self.retry_count += 1
                print(f"          ⏳ TPM限流，等待 {wait}s 后重试 (第{attempt+1}/{self.max_retries}次)...")
                # 分段等待以便 Ctrl+C 能中断
                for _ in range(wait):
                    if self.stopped:
                        return "", total_elapsed, "用户中断"
                    time.sleep(1)
                continue

            # 成功或非429错误 → 直接返回
            # 回退上下文中最后一条 user 消息（失败时不污染上下文）
            if error and not self.no_context and self.messages:
                self.messages.pop()

            return answer, total_elapsed, error

        return "", total_elapsed, "重试次数耗尽"

    def _print_answer(self, answer):
        """根据 answer_mode 输出模型返回的答案"""
        mode, limit = self.answer_mode
        if mode == "off" or not answer:
            return

        sep = "─" * 60
        print()

        if mode == "truncate" and len(answer) > limit:
            print(f"          {sep}")
            print(f"          📝 答案（前 {limit} 字 / 共 {len(answer)} 字）:")
            print(f"          {sep}")
            text = answer[:limit] + "..."
        else:
            print(f"          {sep}")
            print(f"          📝 答案（共 {len(answer)} 字）:")
            print(f"          {sep}")
            text = answer

        for line in text.split("\n"):
            print(f"          {line}")
        print(f"          {sep}")

    def run(self):
        lang_title = {"java": "Java", "python": "Python", "go": "Go", "all": "全语言"}.get(self.lang, self.lang.upper())
        print("=" * 70)
        print(f"  {lang_title} 技术问答压力测试")
        print("=" * 70)
        print(f"  模型:     {self.model_id}")
        print(f"  API:      {self.base_url}")
        print(f"  思考模式: {'开启' if self.enable_thinking else '关闭'}")
        print(f"  上下文:   {'独立' if self.no_context else '累积'}")
        print(f"  问题池:   {len(self.questions)} 题")
        print(f"  回答要求: 中文回答 | 不少于 1000 字")
        print(f"  最大轮次: {self.max_rounds or '无限（直到出错）'}")
        print(f"  超时:     {self.api_timeout}s")
        print(f"  轮间间隔: {self.delay}s")
        print(f"  限流重试: 最多 {self.max_retries} 次")
        mode, limit = self.answer_mode
        if mode == "off":
            answer_desc = "关闭"
        elif mode == "full":
            answer_desc = "完整输出"
        else:
            answer_desc = f"截断 {limit} 字"
        print(f"  答案输出: {answer_desc}")
        print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        print()

        # 注册 Ctrl+C 优雅退出
        def signal_handler(sig, frame):
            self.stopped = True
            print("\n\n[!] 收到中断信号，正在生成报告...")
        signal.signal(signal.SIGINT, signal_handler)

        question_idx = 0

        while not self.stopped:
            if self.max_rounds and self.round_num >= self.max_rounds:
                print(f"\n[✓] 已达到最大轮次 {self.max_rounds}，测试完成")
                break

            question = self.questions[question_idx % len(self.questions)]
            self.round_num += 1

            print(f"[Round {self.round_num:3d}] Q: {question}")
            sys.stdout.flush()

            answer, elapsed, error = self.send_question(question)

            if error:
                word_count = 0
                status = "FAIL"
                self.results.append({
                    "round": self.round_num,
                    "question": question,
                    "answer": "",
                    "chars": 0,
                    "time": round(elapsed, 1),
                    "status": status,
                    "error": error,
                })
                print(f"          A: ❌ 失败 ({format_duration(elapsed)})")
                print(f"          错误: {error[:120]}")
                print()
                # 非429的致命错误 → 停止测试
                if "429" not in error and "用户中断" not in error:
                    break
                # 429 重试耗尽也停止
                if "重试次数耗尽" in error:
                    break
            else:
                word_count = count_words(answer)
                self.total_chars += word_count
                self.total_time += elapsed
                status = "OK"

                self.results.append({
                    "round": self.round_num,
                    "question": question,
                    "answer": answer,
                    "chars": word_count,
                    "time": round(elapsed, 1),
                    "status": status,
                    "error": "",
                })

                avg = self.total_chars / self.round_num
                print(f"          A: ✅ {word_count} 字 | {format_duration(elapsed)} | 累计 {self.total_chars} 字 | 均 {avg:.0f} 字/轮")

                # 输出模型返回的答案
                self._print_answer(answer)
                print()

            question_idx += 1

            # 轮间间隔
            if not self.stopped and self.delay > 0:
                for _ in range(self.delay):
                    if self.stopped:
                        break
                    time.sleep(1)

        self.print_summary()

    def print_summary(self):
        print()
        print("=" * 70)
        print("  测试报告")
        print("=" * 70)

        total = len(self.results)
        success = sum(1 for r in self.results if r["status"] == "OK")
        failed = total - success

        print(f"  总轮次:     {total}")
        print(f"  成功:       {success}")
        print(f"  失败:       {failed}")
        print(f"  限流重试:   {self.retry_count} 次")
        print(f"  总字数:     {self.total_chars}")
        print(f"  总耗时:     {format_duration(self.total_time)}")
        if success > 0:
            print(f"  平均字数:   {self.total_chars / success:.0f} 字/轮")
            print(f"  平均耗时:   {self.total_time / success:.1f}s/轮")
            times = [r["time"] for r in self.results if r["status"] == "OK"]
            chars = [r["chars"] for r in self.results if r["status"] == "OK"]
            if times:
                print(f"  最快/最慢:  {min(times):.1f}s / {max(times):.1f}s")
            if chars:
                print(f"  最短/最长:  {min(chars)} 字 / {max(chars)} 字")
        print()

        # 详细表格
        if self.results:
            print("-" * 70)
            print(f"  {'轮次':>4} | {'状态':4} | {'字数':>6} | {'耗时':>8} | 问题摘要")
            print("-" * 70)
            for r in self.results:
                status_icon = "✅" if r["status"] == "OK" else "❌"
                print(f"  {r['round']:4d} | {status_icon}  | {r['chars']:6d} | {r['time']:7.1f}s | {r['question']}")
            print("-" * 70)

        # 保存 CSV
        self.save_csv()

    def save_csv(self):
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f"test_results_{self.lang}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "round", "question", "answer", "chars", "time", "status", "error"
            ])
            writer.writeheader()
            for r in self.results:
                writer.writerow({
                    "round": r["round"],
                    "question": r["question"],
                    "answer": r["answer"],
                    "chars": r["chars"],
                    "time": r["time"],
                    "status": r["status"],
                    "error": r["error"],
                })

        # 同时保存完整问答到 JSON（方便人工阅读）
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 f"test_results_{self.lang}.json")
        report = {
            "model": self.model_id,
            "api": self.base_url,
            "language": self.lang,
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_rounds": len(self.results),
            "success": sum(1 for r in self.results if r["status"] == "OK"),
            "total_chars": self.total_chars,
            "total_time": round(self.total_time, 1),
            "results": self.results,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        print(f"\n  CSV 已保存:  {csv_path}")
        print(f"  JSON 已保存: {json_path}")
        print("=" * 70)


# ═══════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="多语言技术问答压力测试")
    parser.add_argument("--lang", type=str, default="all",
                        choices=list(LANG_REGISTRY.keys()),
                        help="测试语言: all / java / python / go（默认 all，全部 601 题顺序测试）")
    parser.add_argument("--max-rounds", type=int, default=None,
                        help="最大测试轮次（默认无限，直到出错）")
    parser.add_argument("--no-context", action="store_true",
                        help="每轮独立对话，不累积上下文")
    parser.add_argument("--timeout", type=int, default=120,
                        help="单次 API 超时秒数（默认 120）")
    parser.add_argument("--delay", type=int, default=30,
                        help="轮间间隔秒数（默认 30，避免 TPM 限流）")
    parser.add_argument("--retries", type=int, default=3,
                        help="429 限流最大重试次数（默认 3）")
    parser.add_argument("--show-answer", type=str, default="full",
                        help="终端打印答案: full=完整输出, off=不输出, "
                             "数字=截断到指定字数（默认 full）")
    args = parser.parse_args()

    # 解析 show-answer 参数
    show_answer = args.show_answer.lower()
    if show_answer == "off":
        answer_mode = ("off", 0)
    elif show_answer == "full":
        answer_mode = ("full", 0)
    elif show_answer.isdigit():
        answer_mode = ("truncate", int(show_answer))
    else:
        print(f"[WARN] 无效的 --show-answer 值 '{args.show_answer}'，使用默认 full")
        answer_mode = ("full", 0)

    tester = StressTester(
        max_rounds=args.max_rounds,
        no_context=args.no_context,
        api_timeout=args.timeout,
        delay=args.delay,
        max_retries=args.retries,
        answer_mode=answer_mode,
        lang=args.lang,
    )
    tester.run()


if __name__ == "__main__":
    main()
