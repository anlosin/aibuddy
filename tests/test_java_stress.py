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
    .venv\Scripts\python.exe test_java_stress.py --lang cicd                   # 仅 CI/CD 题库（约 200 题）
    .venv\Scripts\python.exe test_java_stress.py --lang k8s                    # 仅 Kubernetes 题库（约 200 题）
    .venv\Scripts\python.exe test_java_stress.py --lang network                # 仅网络/协议题库（约 200 题）
    .venv\Scripts\python.exe test_java_stress.py --lang devops                 # 运维/平台题库（CI/CD+K8s+网络，各约 200）
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
#  运维 / 平台工程问题池（按方向拆分：CI/CD、Kubernetes、网络，各约 200 题）
# ═══════════════════════════════════════════
CICD_QUESTIONS = [
    "Git 分支模型中 Git Flow 与 Trunk-Based Development 的区别？各自的适用场景？",
    "GitHub Flow 与 GitLab Flow 的核心差异？为什么许多团队选择简化模型？",
    "rebase 与 merge 的区别？交互式 rebase 在 CI 前的代码整理中有什么作用？",
    "squash merge 的优缺点？如何在 PR 合入时保持线性历史？",
    "如何为一次发布打 tag？轻量 tag 与注解 tag 的区别？",
    "分支保护规则（branch protection）如何防止直接 push 到 main？required reviews 如何配置？",
    "CODEOWNERS 文件的作用？如何用它实现目录级评审路由？",
    "如何配置 PR 的必需状态检查（required status checks）？跳过检查的风险？",
    "Git 的 pre-commit、pre-push 钩子在 CI 前能做哪些本地校验？",
    "monorepo 下的 CI 如何只构建受影响的包（affected packages）？Nx/Turborepo 的思路？",
    "多仓库（polyrepo）与 monorepo 在 CI 设计上的主要权衡？",
    "GitLab CI 的 `.gitlab-ci.yml` 中 `stages`、`jobs`、`needs` 三者如何决定执行顺序？",
    "GitLab CI 的 `rules` 与 `only/except` 的区别？如何按分支/标签/MR 触发？",
    "GitLab CI 的 `cache` 关键词如何配置 key 与 policy？pull-push 与 pull 的区别？",
    "GitLab CI 的 `artifacts` 如何跨 job 传递文件？`expire_in` 如何管理存储成本？",
    "GitLab CI 的 `interruptible` 与 `resource_group` 有什么作用？如何避免并发部署冲突？",
    "GitLab CI 的 `retry` 与 `when` 关键词？如何针对特定错误码重试？",
    "GitLab CI 的 child pipeline 与 parent-child pipeline 是什么？如何拆分大型流水线？",
    "GitLab CI 如何复用配置？`include`、`extends`、YAML anchors 的区别？",
    "GitLab CI 的 `environment` 与 `deployments` 如何追踪多环境发布？",
    "GitHub Actions 的 `workflow`、`job`、`step`、`action` 四层结构如何组织？",
    "GitHub Actions 的 `matrix` 策略如何实现多版本/多 OS 组合测试？`fail-fast` 的作用？",
    "GitHub Actions 的 `needs`、`if`、`strategy` 如何控制 job 依赖与条件执行？",
    "GitHub Actions 的 `cache` action 与 `actions/cache` 的 key 设计？依赖缓存命中率优化？",
    "GitHub Actions 的 reusable workflows 如何复用流水线？`workflow_call` 触发？",
    "GitHub Actions 的 OIDC（`permissions: id-token`）如何实现免密钥访问云资源？",
    "GitHub Actions 的 `concurrency` 组如何防止并发部署覆盖？`cancel-in-progress`？",
    "GitHub Actions 的 `secrets` 与 `env` 的区别？environment secrets 如何按环境隔离？",
    "GitHub Actions 中 self-hosted runner 的安全风险？如何隔离不受信的 fork PR？",
    "GitHub Actions 的 `workflow_dispatch` 手动触发如何传输入参数？",
    "GitHub Actions 的 `paths` 过滤如何做到只在点特定文件变更时运行？",
    "Jenkins 声明式 Pipeline 的 `agent`、`stages`、`steps` 结构？与脚本式差异？",
    "Jenkins 的 `stage` 间如何传递变量？`env`、`withEnv` 的作用域？",
    "Jenkins 的 Shared Library 如何复用 Groovy 步骤？`vars/` 与 `src/` 的区别？",
    "Jenkins 的 `agent` 配置：node label、docker、kubernetes 的区别？",
    "Jenkins 的凭据管理（credentials）？如何安全引用 secret 而不明文？",
    "Jenkins 的 `parallel` 阶段如何并行执行？并行中的失败处理？",
    "Jenkins 的 webhook 触发与 `pollSCM` 的区别？蓝绿部署如何结合？",
    "Jenkins 的 `input` 步骤如何实现人工卡点（manual approval）？",
    "Jenkins 如何通过 `buildDiscarder` 管理构建历史与磁盘？",
    "CI 中依赖缓存（dependency cache）策略？Gradle/Maven/npm/pip 各自的缓存目录？",
    "CI 中 Docker 层缓存如何复用？`--cache-from` 与 buildkit 的缓存导出？",
    "Bazel 的远程缓存（remote cache）与远程执行（remote execution）原理？",
    "sccache 如何为 Rust/C++ 提供编译缓存？相比 ccache 的改进？",
    "增量构建（incremental build）如何在 CI 中只编译变更模块？Bazel/Turbo 的思路？",
    "CI 中如何做构建结果缓存（build result reuse）避免重复工作？",
    "如何度量并优化 CI 时长？p95 流水线与关键路径分析？",
    "分布式构建（distributed build）在移动端（Android/iOS）CI 的应用？",
    "构建缓存命中率下降的常见原因？如何排查缓存失效？",
    "CI 中如何缓存 Go module、pip wheel、npm tarball 以提速？",
    "制品（artifact）管理为什么需要独立制品库？Nexus 与 Artifactory 的定位？",
    "制品版本如何与 Git commit / tag 关联？immutable artifact 原则？",
    "制品签名（cosign/sigstore）与供应链安全（SLSA）？为什么需要？",
    "制品的晋级（promotion）流程：dev→staging→prod 如何管控？",
    "私有 registry 在 CI 中如何认证？CI_JOB_TOKEN 与 OIDC 的取舍？",
    "制品的保留策略（retention）与存储成本平衡？",
    "container image 作为制品的最佳实践？digest 固定而非 tag 浮动？",
    "软件物料清单（SBOM）在制品中如何生成（Syft/CycloneDX）？",
    "制品的漏洞扫描（Trivy/Grype）如何集成进发布卡点？",
    "制品的不可变性（immutability）被破坏会导致什么风险？",
    "CI/CD 中 secrets 应如何存储？为什么不该硬编码进仓库或镜像？",
    "密钥轮换（rotation）在 CI 中如何实现？短期凭证（short-lived）的优势？",
    "Vault 在 CI 中如何动态下发密钥？AppRole 与 Kubernetes auth 的区别？",
    "OIDC 如何替代长期 access key 访问 AWS/GCP/Azure？原理？",
    "CI 中如何防止 secret 泄露到日志？redaction 与 masking 策略？",
    "多环境密钥如何隔离？environment-scoped secrets 与 KMS 加密？",
    "第三方 action/step 的依赖投毒（supply chain）风险？pin 到 commit SHA 的理由？",
    "私钥、证书在 CI 中如何安全注入？临时挂载与内存文件系统？",
    "CI 中最小权限原则（least privilege）如何落地？",
    "如何审计 CI 中的密钥使用？secret scanning（gitleaks）卡点？",
    "CI 中测试分片（test sharding）如何缩短时间？按文件/hash 切分？",
    "flaky test 如何识别与处理？配额重试与 quarantine 机制？",
    "测试覆盖率卡点如何设置？lcov/cobertura 的格式与阈值？",
    "静态代码分析（SonarQube/CodeQL）如何在 CI 中作为质量门？",
    "集成测试与 e2e 测试在 CI 中的成本？如何分层减少时长？",
    "性能回归测试（benchmark）能否进 CI？如何设阈值？",
    "契约测试（contract testing/Pact）在微服务 CI 中的作用？",
    "模糊测试（fuzzing）如何集成进 CI 流水线？",
    "测试报告的归集与可视化？JUnit XML 与 Allure 报告？",
    "测试环境的数据如何准备与清理（test fixtures/seed）？",
    "CI 中如何做数据库迁移测试？shadow database 思路？",
    "mutation testing 在 CI 中是否值得？pitest/Stryker？",
    "并行 job 间的测试数据隔离如何保证？",
    "如何对前端做 visual regression 测试（截图对比）？",
    "蓝绿部署（Blue-Green）的具体切换步骤？数据库如何兼容双版本？",
    "金丝雀发布（Canary）如何按流量比例逐步放量？与监控联动？",
    "灰度（rolling canary）与特性开关（feature flag）如何配合？",
    "滚动更新（rolling update）与重建（recreate）策略对比？",
    "部署回滚（rollback）的策略？数据库回滚为何比应用回滚更难？",
    "不可变基础设施（immutable infrastructure）理念？为何不用配置漂移？",
    "渐进式交付（Progressive Delivery）与 Argo Rollouts 的 canary 步骤？",
    "多区域（multi-region）部署的一致性与延迟权衡？",
    "数据库 schema 与代码的向后兼容发布（expand/contract）模式？",
    "特性开关（feature flag）如何实现不停机发布与快速回退？",
    "发布列车（release train）与固定节奏发布（fixed cadence）？",
    "语义化版本（SemVer）在 API/库/服务中如何应用？破坏性变更处理？",
    "changelog 自动生成（conventional commits + release-please）？",
    "预发布环境（staging/preview）与 production 的 parity 如何保证？",
    "部署冒烟测试（smoke test）在发布后如何自动执行？",
    "数据库 migration 的向前/向后兼容与零停机迁移（online DDL）？",
    "GitOps 的核心思想？声明式配置 + 持续 reconcile 的好处？",
    "ArgoCD 的 sync 机制？out-of-sync 状态如何产生与处理？",
    "ArgoCD 的 Application、AppProject、ApplicationSet 的关系？",
    "ArgoCD 的 health assessment 如何判定资源健康？自定义 health 检查？",
    "ArgoCD 的 sync policy：auto-sync、prune、self-heal 的作用？",
    "ArgoCD 与 Flux 的架构差异？push vs pull 模型？",
    "Flux CD 的 Source Controller 与 Reconciliation 循环？",
    "Flux 的 Kustomization 与 HelmRelease 资源如何驱动部署？",
    "GitOps 中 secrets 如何管理？sealed-secrets / external-secrets？",
    "GitOps 的 drift detection 如何发现集群被手动改动？",
    "GitOps 的多集群分发？ArgoCD ApplicationSet 的拓扑生成？",
    "如何用 GitOps 实现环境Promotion（dev→prod 的 Git 流转）？",
    "Tekton 的 Task、Pipeline、PipelineRun、TaskRun 关系？",
    "Tekton 的 Workspace 与 PipelineResource 用途？为何被取代？",
    "Drone CI 的 pipeline 配置与容器化 runner 思路？",
    "CircleCI 的 orbs 与 executors 如何复用配置？",
    "CI 中的缓存与制品在中途失败时的保留策略？",
    "如何为 CI 做成本优化？spot 实例 runner 与自动扩缩？",
    "CI 流水线的可观测性？各阶段耗时与失败率监控？",
    "如何做 CI 的灾备？runner 池跨 AZ 部署？",
    "CI 中容器镜像构建的供应链签名（cosign）如何卡点？",
    "SLSA 等级（L1-L4）对 CI 系统的要求？生成型（generation）与来源（provenance）？",
    "in-toto 的 attestation 框架？link metadata 的作用？",
    "CI 中如何验证依赖完整性（npm provenance / sigstore）？",
    "二进制透明度日志（binary transparency）在发布中的作用？",
    "发布流水线中的合规卡点（合规即代码）如何实现？",
    "如何用 CI 自动生成 API 文档（OpenAPI）并发布？",
    "i18n 资源如何在 CI 中自动化提取与校验？",
    "移动端（Android/iOS）CI 中的签名与证书安全管理？",
    "移动端 OTA 更新的灰度发布如何在 CI 编排？",
    "前端构建（webpack/vite）在 CI 中的缓存与产物优化？",
    "WASM 模块的 CI 构建与测试如何集成？",
    "数据库版本化的迁移工具（Flyway/Liquibase）如何在 CI 执行？",
    "混沌工程（chaos）演练能否纳入发布前卡点？",
    "如何对 CI 自身做端到端测试（meta-CI）？",
    "runner 的自动扩缩（autoscaling）如何实现？队列积压处理？",
    "CI 中如何隔离不同租户/项目的构建环境？",
    "构建沙箱（sandbox）如何限制网络与文件系统访问？",
    "CI 的并发配额（concurrency quota）如何分配与限流？",
    "如何用 CI 做定时任务（nightly build）与定期安全扫描？",
    "CI 中的条件执行：仅在 main 或 tag 时部署？",
    "如何通过 CI 自动创建 release note 与 GitHub Release？",
    "CI 中 docker buildx 多平台镜像（arm64/amd64）构建？",
    "Buildpacks 如何实现「源码→镜像」无需 Dockerfile？",
    "Kaniko 如何在无 Docker daemon 环境构建镜像？",
    "CI 中 SBOM 与漏洞数据库（GHSA/NVD）如何关联告警？",
    "发布后的健康巡检（synthetic monitoring）能否由 CI 触发？",
    "如何用 CI 做多语言 monorepo 的统一版本发布（changesets）？",
    "CI 中的 secret 注入到容器内的最佳实践（env vs file vs mount）？",
    "如何衡量 CI 的 DORA 指标（部署频率/前置时间/变更失败率/恢复时间）？",
    "什么是持续集成（CI）与持续交付（CD）与持续部署的区别？",
    "特性分支（feature branch）过久不合并的集成地狱如何避免？",
    "主干开发（trunk-based）下如何做短生命周期分支与 reviewer 快速响应？",
    "提交信息规范（Conventional Commits）对自动化发布的意义？",
    "CI 中如何做依赖更新自动化（Dependabot/Renovate）？",
    "Renovate 的分组（grouping）与 automerge 策略如何配置？",
    "锁文件（lockfile）在 CI 可重现构建中的作用？",
    "可重现构建（reproducible build）为何重要？如何验证？",
    "CI 中如何处理跨时区/跨区域的团队协作与流水线窗口？",
    "CI 的配置漂移：UI 配置 vs 代码配置谁优先？",
    "如何对流水线做版本管理（pipeline versioning）？",
    "CI 中如何安全地运行不受信的外部贡献者（fork）PR？",
    "sandbox 逃逸风险：CI runner 共享内核的危害？",
    "如何为 CI 做审计日志与合规留存？",
    "CI 中并行 job 的 artifact 大小限制与传输成本？",
    "如何对大型 monorepo 做 CI 的「影响分析」（impacted targets）？",
    "preview/PR 环境生命周期管理：自动创建与自动销毁？",
    "CI 中的「skip CI」标注（[skip ci]）风险与管控？",
    "如何用 CI 自动同步文档站（docs site）构建与部署？",
    "数据库回滚脚本是否应进版本库？与 forward migration 的关系？",
    "CI 中如何对 IaC（Terraform）做 plan/apply 的卡点审批？",
    "Terraform 的 state 在 CI 中如何安全存储（remote state + lock）？",
    "基础设施变更如何做到「计划可见、应用受控」？",
    "CI 中如何对策略即代码（OPA/Conftest）做合规校验？",
    "如何用 CI 实现证书自动续期（cert-manager + renew hook）？",
    "CI 中如何校验 Kubernetes 清单（kubeconform/kubeval）？",
    "配置漂移检测（drift detection）在发布流水线中的位置？",
    "如何对 CI/CD 配置本身做 lint（yamllint/actionlint）？",
    "CI 中如何限制 job 运行时间（timeout）防止挂死？",
    "流水线分层的「快速通道」（lint+unit）与「慢速通道」（e2e）如何设计？",
    "如何衡量并降低 CI 的「变更前置时间」（lead time for changes）？",
    "CI 中如何做构建产物的去重（deduplication）减少存储？",
    "多集群 GitOps 的「分片（sharding）」与「聚合（aggregation）」模式？",
    "如何在 CI 中集成策略引擎（Kyverno/OPA）做部署前校验？",
    "发布流水线中的「人工审批」如何留痕与可审计？",
    "CI 中如何对前端做 Lighthouse 性能卡点？",
    "如何用 CI 做跨浏览器的可视化回归（Percy/Applitools）？",
    "CI 中容器镜像的层最小化（distroless/scratch）对供应链的意义？",
    "如何用 CI 自动生成并推送 Helm chart 到 chart museum？",
    "CI 中如何对机器学习模型做版本与数据集可追溯（MLOps）？",
    "模型训练的 CI（CI for ML）与普通应用 CI 的差异？",
    "如何在 CI 中做配置加密（SOPS/Age）与解密注入？",
    "CI 中 Webhook 签名验证（HMAC）为何重要？",
    "如何用 CI 做基础设施的「dry-run」变更评审？",
    "发布后的「金丝雀分析」（canary analysis）如何用指标自动判定？",
    "如何在 CI 中集成 SAST/DAST（SAST: Bandit/Semgrep, DAST: ZAP）？",
    "CI 中依赖漏洞的「可忽略策略」（ignore policy）如何管控？",
    "如何用 CI 做许可证合规扫描（license compliance）？",
    "CI 中如何做容器镜像的「最小化用户（non-root）」校验？",
    "端到端流水线如何做到「一次提交、全程自动、可观测、可回滚」？",
]

K8S_QUESTIONS = [
    "Kubernetes 整体架构？控制平面（apiserver/scheduler/controller-manager/etcd）各自职责？",
    "kube-apiserver 为何是集群唯一入口？其扩展性与高可用如何做？",
    "etcd 在 K8s 中的作用？quorum 与 raft 一致性如何保证？",
    "kube-scheduler 的调度流程？预选（filter）与优选（score）阶段？",
    "kube-controller-manager 包含哪些控制器？Deployment/ReplicaSet 控制器如何工作？",
    "kubelet 的职责？如何向 apiserver 上报节点与 Pod 状态？",
    "kube-proxy 的 iptables 与 ipvs 模式原理？如何选择？",
    "CRI、CNI、CSI 三大接口分别解决什么问题？",
    "Pod 的完整生命周期阶段？Pending/Running/Succeeded/Failed/Unknown 何时出现？",
    "init container 与主容器的执行顺序？用途与资源计算？",
    "postStart 与 preStop 钩子的执行时机与常见坑？",
    "容器重启策略（restartPolicy）Always/OnFailure/Never 的区别？",
    "Deployment 如何管理 ReplicaSet 实现版本化滚动更新？",
    "Deployment 的滚动更新参数 maxSurge 与 maxUnavailable 如何调？",
    "Deployment 回滚（rollback）到指定 revision 的操作与原理？",
    "ReplicaSet 与 ReplicationController 的区别？为何被取代？",
    "StatefulSet 如何保证 Pod 稳定网络标识与持久存储？",
    "StatefulSet 的滚动更新顺序（OrderedReady/Parallel）？",
    "StatefulSet 的 partition 如何实现金丝雀更新？",
    "DaemonSet 的用途？如何只在某些节点上运行（nodeSelector）？",
    "Job 与 CronJob 的区别？CronJob 的并发策略（concurrencyPolicy）？",
    "CronJob 的 startingDeadlineSeconds 与误触发补偿？",
    "Pod 的 QoS 等级（Guaranteed/Burstable/BestEffort）？OOM 优先级？",
    "requests 与 limits 的区别？为何只设 limit 不设 request 有风险？",
    "为何 CPU 可压缩而内存不可压缩？对调度与驱逐的影响？",
    "节点资源超卖（overcommit）的原理与风险？",
    "Service 的 ClusterIP/NodePort/LoadBalancer/ExternalName 区别？",
    "Headless Service（clusterIP: None）的用途？用于 StatefulSet 与发现？",
    "Service 的 sessionAffinity 与无头服务的客户端负载均衡？",
    "EndpointSlice 相比 Endpoints 的改进？大规模端点性能？",
    "kube-proxy 如何维护 Service 到 Pod 的转发规则？",
    "Ingress 与 Service 的关系？为何 Ingress 不是 Service 类型？",
    "Ingress Controller（nginx/Traefik/Contour）如何工作？",
    "Ingress 的 path 匹配规则与 rewrite-target 配置？",
    "Gateway API 与 Ingress 的区别？HTTPRoute/GRPCRoute 优势？",
    "Service 的 externalTrafficPolicy: Local 的作用与代价？",
    "K8s 网络模型要求？每个 Pod 有独立 IP 且跨节点可直接互通？",
    "CNI 插件职责？Calico/Flannel/Cilium/Weave 的区别？",
    "overlay 网络（VXLAN）与 underlay（BGP）的区别与性能？",
    "Cilium 基于 eBPF 的网络实现相比 iptables 的优势？",
    "Pod 间通信的数据包路径？从容器到节点到对端？",
    "Service 的 ClusterIP 是虚拟 IP，kube-proxy 如何做 DNAT？",
    "DNS 在 K8s 中的解析流程？CoreDNS 的配置与插件？",
    "集群内如何通过服务名解析？search domain 与 ndots 问题？",
    "NetworkPolicy 如何实现 Pod 间网络隔离？默认白名单还是黑名单？",
    "NetworkPolicy 的 ingress/egress 规则与 namespaceSelector？",
    "为什么 NetworkPolicy 依赖 CNI 支持？Calico/Cilium 的实现？",
    "Pod 被 OOMKilled 的排查？limits 与节点内存压力？",
    "CrashLoopBackOff 的常见根因？如何看日志与事件？",
    "ImagePullBackOff 与 ErrImagePull 的区别？镜像拉取失败排查？",
    "Pod 处于 Terminating 卡住？finalizer 与 preStop 阻塞？",
    "节点 NotReady 的常见原因？kubelet 心跳与 taint？",
    "Pod 调度失败（FailedScheduling）的 unschedulable 原因？",
    "容器启动缓慢？readiness 未就绪导致流量未进入？",
    "探针 liveness/readiness/startup 各自作用？配置误区？",
    "探针的 exec/httpGet/tcpSocket 三种方式？initialDelaySeconds？",
    "readiness 失败但 liveness 正常会怎样？滚动更新中影响？",
    "startupProbe 解决什么问题？慢启动容器的 liveness 误杀？",
    "HPA 的扩缩容算法？基于 CPU/内存/自定义指标的触发？",
    "HPA 的目标利用率（targetUtilization）如何计算？",
    "HPA 与 VPA（Vertical Pod Autoscaler）能否同时用？",
    "HPA 的冷却窗口（downscale stabilization）防止抖动？",
    "自定义指标 HPA（Prometheus adapter）如何配置？",
    "Cluster Autoscaler 如何根据 Pending Pod 扩节点？",
    "VPA 的 updateMode（Auto/Recreate/Off）？为何与生产谨慎？",
    "ConfigMap 与 Secret 的区别？ConfigMap 更新是否热加载？",
    "ConfigMap 作为环境变量与卷挂载的区别？卷挂载的热更新？",
    "Secret 的几种类型（Opaque/dockerconfigjson/tls）？",
    "Secret 的 base64 编码并非加密？静态加密（encryption at rest）？",
    "如何通过 secret 管理 TLS 证书？cert-manager 的作用？",
    "ConfigMap/Secret 体积限制与挂载大量小文件性能？",
    "滚动更新时 ConfigMap 变更是否生效？需要触发 Pod 重启？",
    "PV、PVC、StorageClass 的关系？动态供给（dynamic provisioning）？",
    "PV 的访问模式（RWO/RWX/ROX）？RWX 依赖底层存储？",
    "StorageClass 的 provisioner 与 reclaimPolicy（Delete/Retain）？",
    "PVC 绑定（binding）过程？延迟绑定（WaitForFirstConsumer）？",
    "持久卷的扩容（volume expansion）？文件系统在线扩容？",
    "卷快照（VolumeSnapshot）与备份恢复（Velero）？",
    "节点亲和性 nodeAffinity（required/preferred）？",
    "Pod 亲和性 podAffinity 与反亲和性 podAntiAffinity？",
    "taints 与 tolerations 如何配合实现专用节点池？",
    "节点选择器 nodeSelector 与亲和性的取舍？",
    "拓扑分布约束（topologySpreadConstraints）实现跨区均匀？",
    "污点 NoSchedule/PreferNoSchedule/NoExecute 的影响？",
    "调度优先级（priorityClass）与抢占（preemption）？",
    "静态 Pod（static pod）与 kubelet 管理？用途？",
    "节点压力驱逐（node pressure eviction）与 eviction threshold？",
    "资源配额 ResourceQuota 与 LimitRange 如何限制命名空间？",
    "命名空间（Namespace）的隔离边界？网络与存储是否隔离？",
    "多租户隔离在 K8s 中的层级（NS/RBAC/NetworkPolicy/quota）？",
    "RBAC 的 Role/ClusterRole/RoleBinding/ClusterRoleBinding 关系？",
    "ServiceAccount 与 Pod 绑定？token 投影（token projection）？",
    "最小权限原则在 K8s RBAC 中的落地？",
    "聚合 API（Aggregated API）与 APIService 的作用？",
    "准入控制（Admission Controller）链？内置与动态？",
    "Mutating/Validating Webhook 如何拦截请求做修改与校验？",
    "动态准入的失败策略（failurePolicy）ignore/fail 的取舍？",
    "Pod Security Admission 的 privileged/baseline/restricted 三级？",
    "为何 PodSecurityPolicy 被废弃？PSA 的替代思路？",
    "安全上下文（securityContext）runAsNonRoot/readOnlyRootFilesystem/capabilities？",
    "seccomp 与 seccompProfile 在容器中的作用？",
    "AppArmor 在 K8s 中的注解配置（annotations）？",
    "容器逃逸的常见路径？如何通过加固减少风险？",
    "镜像最小化（distroless/非 root）对安全的影响？",
    "网络策略与零信任（zero trust）在 K8s 的落地？",
    "密钥管理的 external-secrets 与 secrets-store-csi-driver？",
    "K8s 的 Operator 模式？自定义控制器如何 reconcile？",
    "CRD 的定义？如何设计 schema（openAPIV3）？",
    "controller-runtime 的 Reconciler 与 Manager 结构？",
    "Operator 的 finalizer 与 ownerReference 如何做级联清理？",
    "自定义资源的 status 子资源与观测性？",
    "ArgoCD/Flux 这类 GitOps 工具本质是 Operator 吗？",
    "多集群管理 Karmada 的架构？PropagationPolicy 作用？",
    "KubeFed 与 Karmada 的区别？联邦调度的难点？",
    "集群联邦中的成员集群认证与网络？",
    "多集群服务发现（MCS API）如何工作？",
    "etcd 的备份与恢复（etcdctl snapshot）？",
    "etcd 的性能瓶颈？写入放大与磁盘 IO？",
    "etcd 的 compaction 与 defrag 为什么必要？",
    "K8s 事件（Event）机制？为何会丢失？events 留存？",
    "kubectl 的常用调试命令？get/describe/logs/exec/port-forward？",
    "kubectl debug（临时容器 ephemeral container）如何排障？",
    "kubectl drain/cordon/uncordon 节点维护流程？",
    "kubectl apply 与 create 的区别？声明式与命令式？",
    "kubectl 的 dry-run=server/client 与 diff 用途？",
    "kubectl 插件机制（krew）？自定义子命令？",
    "集群升级（kubeadm upgrade）的流程与注意事项？",
    "节点逐步升级（cordon+drain+upgrade+uncordon）？",
    "版本偏差（version skew）策略？组件兼容窗口？",
    "K8s 证书体系？kubeadm 证书轮换（certificate renewal）？",
    "组件间 mTLS 通信？如何轮换 apiserver 证书？",
    "K8s 的审计日志（audit log）配置与留存？",
    "可观测性三大支柱在 K8s 的落地（metrics/logs/traces）？",
    "metrics-server 与 Prometheus 的关系？HPA 数据来源？",
    "日志收集架构（DaemonSet 采集 + 集中式）？",
    "分布式追踪（OpenTelemetry）在 K8s 的注入？",
    "资源使用监控（cadvisor）与节点级（node exporter）？",
    "告警规则（PrometheusRule）与 Alertmanager 路由？",
    "集群级 Grafana 看板与多租户视图？",
    "K8s 的 Custom Metrics API 与 adapter 注册？",
    "服务质量监控：Pod 重启、OOM、驱逐告警？",
    "容量规划：如何根据历史用量预测节点扩容？",
    "K8s 的垃圾回收（garbage collection）？已终止 Pod 与镜像？",
    "镜像垃圾回收（imageGC）的阈值（HighThreshold/LowThreshold）？",
    "节点上容器运行时（containerd/CRI-O）的区别？",
    "容器运行时接口（CRI）与 dockershim 移除的影响？",
    "K8s 的 device plugin 机制？GPU 等硬件如何暴露？",
    "拓扑感知调度（topology manager）与 NUMA 亲和？",
    "大页（hugepages）在 K8s 如何分配与使用？",
    "实时性（real-time）Pod 与静态 CPU 管理策略？",
    "本地持久卷（local PV）的用途与限制？",
    "裸金属（bare-metal）集群的网络方案对比？",
    "边缘计算场景的 K3s/MicroK8s 轻量化？",
    "虚拟集群（virtual cluster / vcluster）的隔离思路？",
    "K8s 的 server-side apply（SSA）与冲突解决（field manager）？",
    "kubectl apply 的 last-applied-configuration 与三方合并？",
    "Helm 的模板渲染（template/render）与 values 覆盖？",
    "Helm 的 release 管理与回滚（rollback）？",
    "Helm chart 的 hook 与依赖（dependencies）？",
    "Kustomize 的 overlay 与 base 如何做环境差异化？",
    "Helm 与 Kustomize 的取舍？声明式配置管理？",
    "清单校验（kubeconform/kubeval）在 CI 的位置？",
    "策略即代码（OPA/Gatekeeper/Kyverno）如何校验清单？",
    "Kyverno 的 mutate/validate/generate 规则？",
    "OPA Gatekeeper 的 ConstraintTemplate 与 Constraint？",
    "集群自动扩缩（CA）与 HPA 的协同与冲突？",
    "spot 实例节点与 Pod 中断预算（PDB）？",
    "PodDisruptionBudget 如何保护可用性？自愿中断？",
    "节点亲和与拓扑分布结合实现高可用部署？",
    "有状态应用的备份（Velero）与跨集群迁移？",
    "K8s 中的服务网格（Istio/Linkerd）数据面与控制面？",
    "sidecar 注入（auto/manual）与流量拦截（iptables/IPVS）？",
    "Istio 的 VirtualService/DestinationRule/Gateway 作用？",
    "mTLS 在服务网格中的透明加密与身份？",
    "流量管理：金丝雀、镜像（mirror）、熔断、重试？",
    "可观测性：服务网格的指标、访问日志、分布式追踪？",
    "服务网格的性能开销与是否值得引入？",
    "K8s 的准入与网格 sidecar 注入的顺序问题？",
    "多租户下的资源公平性（优先级与配额）？",
    "控制平面的可扩展性（大规模集群的瓶颈）？",
    "万节点级集群的 etcd 分片（etcd learner/separate）？",
    "kube-apiserver 的聚合层性能与限流（priority & fairness）？",
    "大规模集群的监听器（watch）优化与 bookmark？",
    "节点数量上限与每个节点 Pod 上限（默认 110）？",
    "endpointslice 与大规模 Service 的性能？",
    "K8s 中的设备管理（DRA）动态资源分配新特性？",
    "网关 API 的 GAMMA 倡议（服务网格接入）？",
    "如何对 K8s 做合规基线（CIS Benchmark）扫描？",
    "集群灾备（DR）：etcd 备份 + 多区域控制平面？",
    "GitOps 驱动的集群状态恢复（disaster recovery）？",
    "K8s 中的批处理（spark/flink on k8s）调度特点？",
    "机器学习训练（MPI/TFJob）的 K8s 算子（kubeflow）？",
    "推理服务（KServe/Triton）在 K8s 的自动扩缩？",
    "游戏/实时服务的 K8s 适配挑战？",
    "如何用 K8s 做蓝绿/金丝雀（Argo Rollouts）？",
    "渐进式交付的 Analysis 与指标判定（Prometheus/Web）？",
    "K8s 中的配置漂移检测与自愈（self-heal）？",
    "集群升级的「先控制平面后节点」顺序为何重要？",
    "如何限制 Pod 的 sysctls 与特权能力？",
    "K8s 的声明式终态（desired state）与控制器调和思想总结？",
]

NETWORK_QUESTIONS = [
    "OSI 七层模型各层职责？与 TCP/IP 四层如何对应？",
    "TCP 三次握手详细过程？为什么不是两次？",
    "TCP 四次挥手过程？为何 TIME_WAIT 需要 2MSL？",
    "TCP 的 SYN Flood 攻击与防御（SYN cookie）？",
    "TCP 的序列号与确认号如何保证有序可靠？",
    "TCP 的滑动窗口（sliding window）与流量控制？",
    "拥塞控制慢启动、拥塞避免、快重传、快恢复？",
    "TCP 的 MSS 与窗口缩放（window scaling）选项？",
    "BBR 与传统 CUBIC 的区别？BBR 适用场景？",
    "TCP 的 Nagle 算法与延迟确认（delayed ACK）的相互作用？",
    "TCP_NODELAY 的作用？为何交互式应用要关 Nagle？",
    "SO_KEEPALIVE 的探测机制与超时参数？",
    "SO_REUSEADDR 与 SO_REUSEPORT 的区别？",
    "TCP 半关闭（half-close）与 shutdown() 的 SHUT_WR？",
    "TCP 粘包/拆包问题？应用层定长/分隔符/长度字段方案？",
    "零窗口（zero window）与糊涂窗口综合征（SWS）？",
    "TCP 的 MTU 发现（PMTUD）与黑洞问题？",
    "UDP 与 TCP 的核心区别？何时选 UDP？",
    "UDP 的不可靠性如何在上层补偿（QUIC/应用层重传）？",
    "QUIC 协议为何基于 UDP？解决了 TCP 的哪些问题？",
    "HTTP/1.0、1.1、2、3 的演进与主要特性？",
    "HTTP/1.1 的管线化（pipelining）为何失败？",
    "HTTP/2 的多路复用如何消除队头阻塞（HOL blocking）？",
    "HTTP/2 的头部压缩（HPACK）？",
    "HTTP/2 的流优先级（stream priority）与依赖？",
    "HTTP/3 基于 QUIC 如何解决队头阻塞与连接迁移？",
    "HTTPS 的 RSA 与 ECDHE 密钥交换区别？",
    "TLS 1.2 与 1.3 握手差异？1.3 的 0-RTT/1-RTT？",
    "TLS 的对称与非对称加密配合？为何不全用非对称？",
    "证书链与信任锚？CA、中间证书、根证书？",
    "证书吊销机制 CRL 与 OCSP？OCSP stapling？",
    "前向安全性（PFS）为何重要？",
    "自签名证书与公共 CA 的区别？内网如何信任？",
    "mTLS 双向认证流程？服务间如何互信？",
    "HSTS 的作用？为何能防 SSL stripping？",
    "SNI 与 ALPN？TLS 握手如何选协议与虚拟主机？",
    "DNS 的完整解析流程？递归与迭代查询？",
    "递归解析器、根、TLD、权威服务器的角色？",
    "DNS 的 A/AAAA/CNAME/MX/TXT/NS 记录类型？",
    "DNS 的 TTL 与缓存层级？负缓存（negative caching）？",
    "DNS 轮询（round-robin）做负载均衡的局限？",
    "DNS 的 Anycast 如何实现就近接入？",
    "EDNS Client Subnet（ECS）的作用与隐私？",
    "DoH（DNS over HTTPS）与 DoT（DNS over TLS）？",
    "DNSSEC 的工作原理？防污染与签名验证？",
    "split-horizon DNS（分视图）的内外网解析？",
    "智能 DNS 与 GSLB 的全球流量调度？",
    "dig/nslookup/host 的常用排查命令？",
    "DNS 解析缓慢的排查思路（递归链、缓存、网络）？",
    "CDN 的工作原理？边缘缓存与回源（origin pull）？",
    "CDN 的缓存命中率优化？Cache-Control/ETag？",
    "CDN 的 purge（刷新）与 stale-while-revalidate？",
    "CDN 的动静态分离与边缘计算（edge function）？",
    "GSLB 的地理/延迟/健康路由策略？",
    "Anycast 与 DNS 在 GSLB 中的结合？",
    "负载均衡 L4 与 L7 的区别？各自典型场景？",
    "轮询/加权轮询/最小连接/哈希的算法取舍？",
    "一致性哈希（consistent hashing）在 LB 中为何重要？",
    "负载均衡的健康检查（health check）机制？",
    "会话保持（session persistence/sticky）的实现？",
    "正向代理与反向代理的区别？",
    "透明代理（transparent proxy）如何拦截流量？",
    "代理的 CONNECT 方法与 HTTPS 隧道？",
    "正向代理的认证与访问控制？",
    "反向代理的缓冲（buffering）与超时配置？",
    "Nginx 的 master-worker 进程模型？事件驱动（epoll）？",
    "Nginx 的 worker_connections 与并发上限？",
    "Nginx 的 location 匹配优先级（=、^~、~、/）？",
    "Nginx 的 rewrite 规则与 break/last/redirect/permanent？",
    "Nginx 的 upstream 与负载均衡算法配置？",
    "Nginx 的 keepalive 与上游连接复用？",
    "Nginx 的限流（limit_req/limit_conn）？漏桶算法？",
    "Nginx 的缓存 proxy_cache 与缓存键设计？",
    "Nginx 的 gzip/brotli 压缩配置与权衡？",
    "Nginx 的 TLS 终止（termination）与透传（passthrough）？",
    "Nginx 的 try_files 与前端路由（SPA）回退？",
    "Nginx 的 error_page 与自定义错误页？",
    "Nginx 的 stream 模块做 TCP/UDP 代理？",
    "Envoy 与 Nginx 作为反向代理的差异？",
    "HAProxy 的 L4/L7 负载均衡特点？",
    "WebSocket 握手（Upgrade 头）？与 HTTP 长轮询/SSE 区别？",
    "WebSocket 在反向代理（Nginx）下的配置要点？",
    "SSE（Server-Sent Events）与 WebSocket 的取舍？",
    "gRPC 为何基于 HTTP/2？多路复用/流控/头部压缩？",
    "gRPC 的四种方法类型（unary/stream）？",
    "gRPC 的负载均衡（客户端 LB vs 代理 LB）？",
    "gRPC 的 deadline 与拦截器（interceptor）？",
    "HTTP/2 在 gRPC 下的队头阻塞是否完全消除？",
    "长连接（keep-alive）与连接池对吞吐/延迟影响？",
    "连接池的参数（最大连接、空闲超时、获取超时）？",
    "网络命名空间（network namespace）与容器网络隔离？",
    "veth pair 与网桥（bridge）如何连通容器？",
    "overlay 网络 VXLAN 的封装与外层 IP？",
    "路由表与策略路由（policy routing）？",
    "ARP 协议与 ARP 缓存？ARP 欺骗（spoofing）？",
    "MAC 与 IP 的地址解析过程？",
    "NAT（SNAT/DNAT/MASQUERADE）原理？",
    "iptables 的表（filter/nat/mangle/raw）与链？",
    "iptables 的 DNAT/SNAT 典型规则？",
    "iptables 的 conntrack（连接跟踪）状态？",
    "nftables 与 iptables 的关系？为何是继任者？",
    "ipvs 在 L4 负载均衡中的使用（IPVS-DR/TUN/NAT）？",
    "子网划分与 CIDR？计算网段可用 IP 数量？",
    "私有地址段（RFC1918）与公网地址？",
    "VLAN 与子网的区别？隔离层级？",
    "网关（gateway）与默认路由的作用？",
    "静态路由与动态路由（BGP/OSPF）的区别？",
    "BGP 的路径属性与选路？eBGP/iBGP？",
    "任播（Anycast）的路由实现与 DDoS 防护？",
    "MTU 与 MSS 的关系？典型值与分片？",
    "IP 分片与重组？DF 位与 PMTUD？",
    "ICMP 的作用？ping/traceroute 如何利用？",
    "traceroute 的 UDP/ICMP/TCP 实现差异？",
    "mtr 如何结合 ping+traceroute 持续监测？",
    "tcpdump 的过滤表达式（host/port/proto）？",
    "Wireshark 的常见分析（握手、重传、乱序）？",
    "抓包定位 TCP 重传/乱序/重复 ACK？",
    "丢包排查：链路、队列、带宽、限速？",
    "带宽与吞吐（throughput）与延迟（latency）的区别？",
    "RTT 与带宽延迟积（BDP）？窗口大小与 BDP 关系？",
    "网络拥塞的 Bufferbloat 与 CoDel/RED 队列管理？",
    "QoS 与流量整形（traffic shaping）/ policing？",
    "延迟敏感应用的网络优化（绑定 CPU、NUMA）？",
    "多队列网卡（RSS）与收包负载均衡？",
    "零拷贝（zero-copy）mmap/sendfile/splice？",
    "内核旁路（DPDK/kernel bypass）与用户态协议栈？",
    "TCP 快速打开（TFO）减少握手延迟？",
    "io_uring 相比 epoll 的异步 I/O 优势？",
    "服务网格 sidecar 如何拦截流量（iptables REDIRECT/IPVS）？",
    "eBPF 在网络可观测性与安全中的用途？",
    "网络策略（NetworkPolicy）与微服务零信任？",
    "mTLS 在 sidecar 间的透明加密实现？",
    "服务发现（DNS/etcd/consul）与客户端负载均衡？",
    "连接迁移（connection migration）在 QUIC/mobile 场景？",
    "弱网（高丢包高延迟）下的传输优化（FEC/ARQ）？",
    "移动网络下的 TCP 优化（INITCWnd、TFO）？",
    "HTTP 缓存：强缓存（Cache-Control）与协商缓存（ETag/Last-Modified）？",
    "条件请求 If-Modified-Since / If-None-Match？",
    "Range 请求与断点续传、分块下载？",
    "HTTP 的 1xx/2xx/3xx/4xx/5xx 状态码分类与典型？",
    "重定向 301/302/307/308 的区别？",
    "HTTP 的 Cookie 与 SameSite/HttpOnly/Secure 属性？",
    "CORS 的预检（preflight）与跨域头？",
    "CSP（内容安全策略）与 XSS 防护？",
    "HTTP 的压缩协商（Accept-Encoding）？",
    "WebSocket 断线重连与心跳（ping/pong）？",
    "HTTP/2 的服务器推送（server push）与 103 Early Hints？",
    "gRPC-Web 与浏览器兼容（Envoy 转换）？",
    "长连接保活：应用层心跳 vs TCP keepalive？",
    "连接耗尽（连接泄漏）的排查与修复？",
    "端口耗尽（本地端口用尽）的调优（tw_reuse/tw_recycle）？",
    "FIN_WAIT_2 / CLOSE_WAIT 堆积的原因与处理？",
    "TIME_WAIT 过多的危害与缓解（tw_reuse）？",
    "半打开连接（half-open）检测？",
    "网络隔离（security group / firewall）的分层？",
    "东西向（east-west）与南北向（north-south）流量？",
    "微服务通信：同步（REST/gRPC）vs 异步（消息队列）？",
    "消息队列的网络可靠性（at-least-once/at-most-once）？",
    "流控与熔断（circuit breaker）在网络层的实现？",
    "限流算法：令牌桶 vs 漏桶？分布式限流？",
    "重试风暴（retry storm）与退避（backoff/jitter）？",
    "超时（timeout）设置原则？级联超时与传播？",
    "优雅关闭（graceful shutdown）时的连接 draining？",
    "蓝绿/金丝雀在网络层的流量切分（LB/网格）？",
    "网络延迟的瓶颈定位（应用/内核/网卡/链路）？",
    "网卡多队列与中断亲和（IRQ affinity）？",
    "巨帧（jumbo frame）对吞吐的提升与风险？",
    "TCP 的初始拥塞窗口（initcwnd）调优？",
    "内核网络参数调优（somaxconn/tcp_max_syn_backlog）？",
    "网络可观测性指标（重传率、RTT、丢包、乱序）？",
    "真实用户监控（RUM）与 Synthetic 监控？",
    "边缘节点（edge/POP）的网络架构？",
    "私有网络互联（VPN/专线/SD-WAN）？",
    "VPN（IPsec/WireGuard）的隧道与加密？",
    "WireGuard 相比 OpenVPN/IPsec 的简化？",
    "内网穿透（NAT traversal）与 STUN/TURN/ICE？",
    "P2P 连接的 NAT 打洞（hole punching）原理？",
    "多播（multicast）与广播（broadcast）的使用场景？",
    "网络故障的根因分析（RCA）方法论？",
    "如何设计高可用的网络架构（多 AZ/多链路）？",
    "DNS 解析失败时的本地 hosts 兜底与缓存？",
    "HTTP/3 的 CONNECT 与代理兼容性问题？",
    "TLS 会话复用（session ticket/resumption）？",
    "OCSP stapling 减少证书校验延迟？",
    "证书透明度日志（Certificate Transparency）？",
    "中间人（MITM）代理如何解密 HTTPS 及风险？",
    "网络地址转换对端到端加密的影响？",
    "IPv6 与 IPv4 的差异？双栈（dual-stack）部署？",
    "NAT64/DNS64 解决 IPv6-only 网络访问 IPv4？",
    "任意播（Anycast）DNS（如 1.1.1.1/8.8.8.8）原理？",
    "网络性能基准测试工具（iperf3/wrk/ab）使用？",
    "抓包权限与特权（CAP_NET_RAW）在生产环境？",
    "容器跨主机网络的 MTU 与 VXLAN 开销？",
    "服务网格的 mTLS 证书轮换（SPIFFE/SPIRE）？",
    "网络隔离的分层：VPC/子网/安全组/NACL？",
    "流量镜像（traffic mirroring）用于测试与排查？",
    "边缘计算下的网络拓扑与就近接入？",
    "网络层的 DDoS 防护（清洗中心/Anycast/BGP Flowspec）？",
    "QUIC 的 0-RTT 重放攻击风险与缓解？",
    "端到端网络排障的系统化思路总结（自顶向下/自底向上）？",
]


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
    "cicd":   (CICD_QUESTIONS,   _SYSTEM_PROMPT_TEMPLATE.format(lang="CI/CD 持续集成与持续交付")),
    "k8s":    (K8S_QUESTIONS,    _SYSTEM_PROMPT_TEMPLATE.format(lang="Kubernetes 容器编排")),
    "network":(NETWORK_QUESTIONS, _SYSTEM_PROMPT_TEMPLATE.format(lang="网络与协议")),
    "devops": (CICD_QUESTIONS + K8S_QUESTIONS + NETWORK_QUESTIONS, _SYSTEM_PROMPT_TEMPLATE.format(lang="运维/平台工程")),
    "all":    (JAVA_QUESTIONS + PYTHON_QUESTIONS + GO_QUESTIONS + CICD_QUESTIONS + K8S_QUESTIONS + NETWORK_QUESTIONS, _SYSTEM_PROMPT_ALL),
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
        lang_title = {"java": "Java", "python": "Python", "go": "Go", "cicd": "CI/CD", "k8s": "Kubernetes", "network": "网络/协议", "devops": "运维/平台工程", "all": "全语言"}.get(self.lang, self.lang.upper())
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
                        help="测试语言: all / java / python / go / cicd / k8s / network / devops（默认 all，全部题库顺序测试）")
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
