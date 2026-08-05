# Worker System 模块

## 架构说明

本模块提供线程安全的后台任务管理基础设施。

### 模块组成

| 文件 | 功能 |
|------|------|
| `thread_pool.py` | QThreadPool 单例，管理并发任务生命周期 |
| `task.py` | AsyncWorker (QRunnable) 轻量级异步任务 |

### 使用方式

#### 完整功能（推荐）
项目主线程使用 `worker.WorkerThread`，已包含：
- 思考标签解析（`<thinking>` 标签流式拆分）
- 多轮工具调用循环（最多 5 轮）
- 插件工具分派
- 线程安全停止（`threading.Event`）
- API 超时保护（60 秒）
- 流式连接资源清理

```python
from worker import WorkerThread

thread = WorkerThread(client, model_id, enable_thinking, enable_tools, messages,
                      plugins=plugins, enabled_plugins=enabled_plugins)
thread.chunk_received.connect(handle_chunk)
thread.response_complete.connect(handle_complete)
thread.error_occurred.connect(handle_error)
thread.start()

# 线程安全停止
thread.stop()
thread.wait(3000)  # 最多等 3 秒
```

#### 轻量级任务（未来扩展）
对于不需要思考标签/工具调用的简单请求，可使用 `AsyncWorker`：

```python
from worker_system import WorkerPool, AsyncWorker

pool = WorkerPool()
worker = AsyncWorker(client, model_id, messages)
worker.signals.chunk_received.connect(handle_chunk)
pool.submit(worker)

# 停止
worker.stop()
```

### 线程安全设计

1. **停止信号**: 使用 `threading.Event` 替代裸布尔变量，确保跨线程内存可见性
2. **超时保护**: API 请求设置 60 秒超时，防止无限阻塞
3. **资源清理**: `finally` 块中安全关闭流式连接
4. **优雅降级**: `wait(3000)` 超时后 `terminate()` 强杀，避免 UI 冻结
