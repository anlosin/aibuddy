# Logging 架构（P1+P2 重构 · 架构师交付）

> 角色：Bob（架构师）｜收件人：齐齐（工程师）｜主线主理人：齐齐林（team-lead）
> 范围：`D:\pycharm\PythonProject\qwen`（PyQt5 5.15.11 + Qt 5.15.2 + Python 3.13）
> 当前基线：**739 tests 全过**（HEAD=`91ba17d`，main 分支）
> 目标：用 stdlib `logging` 统一 `print()` 与 `except Exception` 的日志输出；不引入第三方；不破坏 `data/logs/app.log` 现有路径与 `--selftest` 既有行为。

---

## 0. TL;DR（90 秒看完）

- **新增 1 个文件**：`qwen_app/log.py`（log 工厂 + 路由 + 测试降级）。
- **替换 146 处 `print()`** + **给关键路径 493 处 `except Exception` 补 `logger.exception`**。
- **根 logger = `qwen`**，INFO 滚动写 `data/logs/app.log`（5MB × 5），WARNING+ 同步 stderr；命名约定 `qwen_app.<module>` / `qwen_app.plugins.<name>`。
- **6 批顺序执行**，每批独立可回滚；3 个护栏测试与代码同源。
- **零第三方依赖**（不引入 loguru 等），纯 stdlib `logging`。

---

## 1. 核心设计

### 1.1 `qwen_app/log.py` —— 日志工厂（签名）

```python
# qwen_app/log.py
import logging
from . import paths

def get_logger(name: str) -> logging.Logger:
    """模块级 logger 工厂。

    约定：
      - 所有业务模块: get_logger("qwen_app.<module>")
        例 get_logger("qwen_app.scheduler")
      - 插件: get_logger("qwen_app.plugins.<name>")
        例 get_logger("qwen_app.plugins.ssh_runner")
    子 logger 沿用根 logger 的 handlers 与 effective level（不重复 setup）。
    同名 get_logger 返回 Python 标准 cache 的同一实例。
    """
    return logging.getLogger(name)

def init_logging(level: str | int | None = None) -> None:
    """幂等初始化。main.py 启动期调一次；测试不显式调（自动 WARNING）。

    行为：
      1. 根 logger = "qwen"（不是默认的 "root"，避免污染其他库）。
      2. file handler = RotatingFileHandler(data_dir()/logs/app.log, 5MB × 5)。
      3. stderr handler = StreamHandler(sys.stderr, level=WARNING)。
      4. 测试环境（QT_QPA_PLATFORM=offscreen 或 PYTEST_CURRENT_TEST 存在）
         → 根 logger level 提到 WARNING；file handler 仍写但只 WARNING+。
    """
    # 幂等守卫：handler 已挂载则 no-op
    ...

def is_test_env() -> bool:
    """检测是否在测试运行（影响日志级别降级）。"""
    return (
        "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("QT_QPA_PLATFORM") == "offscreen"
        or os.environ.get("QWEN_TESTING") == "1"
    )
```

### 1.2 命名规范

| 层级 | 命名 | 示例 | 文件 |
|------|------|------|------|
| 根 | `qwen` | — | （qwen_app/log.py 创建） |
| 调度 | `qwen_app.scheduler` | `get_logger("qwen_app.scheduler")` | scheduler.py |
| Bridge | `qwen_app.bridge.<slice>` | `get_logger("qwen_app.bridge.bubble")` | _bridge_bubble.py 等 |
| 桥主体 | `qwen_app.chat_bridge` | `get_logger("qwen_app.chat_bridge")` | chat_bridge.py |
| 插件管理 | `qwen_app.plugin_manager` | `get_logger("qwen_app.plugin_manager")` | plugin_manager.py |
| 插件 | `qwen_app.plugins.<name>` | `get_logger("qwen_app.plugins.ssh_runner")` | plugins/<name>.py |
| 入口 | `qwen_app.main` | `get_logger("qwen_app.main")` | main.py |

**为什么不直接用 `__name__`**：`__name__` 在「源码运行」是 `qwen_app.scheduler`，在 PyInstaller `onefile` 解包后变成 `qwen_app` 同级 namespace 包路径且不可控；显式传字符串更稳定，且与 `_bridge_<slice>` 这类无固定 `__name__` 的 mixin 一致。

### 1.3 级别路由

```
                        ┌─────────────────────────┐
                        │  Logger "qwen"（根）    │ level=INFO
                        └────────────┬────────────┘
              ┌──────────────────────┼───────────────────────┐
              ▼                      ▼                       ▼
   RotatingFileHandler        StreamHandler           propagate=True
   data/logs/app.log          sys.stderr              (子 logger 沿用)
   5MB × 5                    WARNING 以上
   level=INFO                 (QA 与 console=0 双通道)
              │
              ▼
   测试环境（PYTEST_CURRENT_TEST /
   QT_QPA_PLATFORM=offscreen）→ 根 logger level 升到 WARNING
   （file handler 也跟着升；测试日志不再污染 ut_full3.txt）
```

**核心规则**：
1. **子 logger 不重复 setup**：只调 `get_logger("qwen_app.x")`，handler 全在根上。子 logger 的 effective level 由根 logger 决定（Python 默认 propagate 行为）。
2. **init_logging() 必须幂等**：handler 已挂则跳过；这是 main.py 与 `run_full_tests.py` 同一进程内多次调用的关键。
3. **测试环境检测三合一**：`PYTEST_CURRENT_TEST`（unittest 自动设）/`QT_QPA_PLATFORM=offscreen`（既有测试 100% 满足）/ `QWEN_TESTING=1`（手动开关）。

### 1.4 与现有 `app.log` 的整合方案（避免双写）

**现状**：`main.py:_install_stream_guard()` 在 PyInstaller `--noconsole` 下把 stdout/stderr Tee 到 `data/logs/app.log`。

**风险**：直接把 `print()` 换成 `logger.info()` 会导致：
- 源码态：原本 `print → console`，现在 `logger → file + console`，行为更对，**没问题**。
- 打包态：原本 `print → console=null + file`，现在 `logger → file + stderr`，**stderr 在打包态也是 null** → 用户看不到 ERROR。

**整合方案**：

| 行为 | 调整 |
|------|------|
| **保留** `_install_stream_guard()` | 打包态 stdout/stderr 仍 Tee 到 `app.log`（防其它库/三方 print 也落地） |
| **新增** `init_logging()` 在 `_install_stream_guard()` 之后调一次 | logger 走 `RotatingFileHandler → app.log`（自动 append，与 tee 复用同一文件） |
| **去重** `main.py` 里 `[main]` 前缀的 `print` | 只保留启动横幅（banner）；其余业务启动 log 走 `logger.info` |
| **关键** | logger 与 tee 写到同一文件不冲突：logger 加 formatter（带时间戳与 logger 名）；tee 是 raw text。日志肉眼可读即可。 |

**为什么不抽到 `tee` 中转**：
- `tee` 兜的是「其它未知库的 print」，无法保证它们带前缀。
- logger 是「我们自己的代码可控的输出」，应当走格式化路径。
- 两路共存能完整覆盖，比抽掉 tee 更安全。

### 1.5 保留 `print()` 的场景（不替）

| 文件 | 行 | 场景 | 理由 |
|------|---|------|------|
| `main.py` | ~316、~389 | `[main]` 启动横幅 / 启动状态 | 控制台用户首屏看到的「应用活着」信号；不靠 logger 才最快 |
| `main.py` | `_run_selftest` 的 `emit()` | `--selftest` 模式输出 | 该模式**专门用 stdout** 给运维/CI 抓取，不能改 logger |
| `plugins/_cmd_blocklist.py:20` | 文档字符串里的 `print(1)` 示例 | docstring | 不是代码，是文档 |

**判定准则**：控制台（用户/运维）**直接看到的非结构化横幅**保留 print；任何「我写代码出错了」/「我想留下排查线索」的输出 → logger。

---

## 2. silent-OK / silent-WARN / must-log-ERROR 三档判定

> 写给工程师：哪些 `except Exception: pass` 可以保留无声，哪些必须 `logger.exception(...)`。

### 2.1 silent-OK（保留 `except ... pass`，但加注释说明）

适用于**路径语义上期望异常发生**的清理 / 兜底：
- `Scheduler._run_one` 末尾 `for cb in list(...): try: cb(...); except Exception: pass` —— 回调链断一个不能影响其他（GUI 已关闭）
- `_install_stream_guard` 里 `sink.write` 失败 —— tee 写日志失败不能再抛
- `paths._dir_writable` `try: ... except Exception: return False` —— 探测不可写是真结果
- `paths.ensure_external_plugins` 整体 `except Exception: return 0, ext` —— 释放失败不阻塞启动
- `paths.scan_plugin_names` `except Exception: pass` —— 单目录扫描失败要继续看其它目录
- 插件热重载 watcher `_init_plugin_watcher` `except Exception as e: print(...)` —— 文件系统监听初始化失败，主流程可继续
- `QFileSystemWatcher` `try: ... except Exception` 类 wrapper —— Qt 系统调用失败常见

### 2.2 silent-WARN（`logger.warning(...)`，不记 traceback）

适用于**可恢复 / 有兜底但应该被看见**的失败：
- `scheduler.is_due` 配置损坏 → 任务跳过本次 → `logger.warning("[Scheduler] 任务 X 配置异常: ...")`
- 插件 `dispatch_tool` 中 execute 异常 → `logger.warning("[plugin_manager] 工具 X 失败: ...")`
- 插件 `load` 失败 → `logger.warning("加载插件 X 失败: ...")`
- 桥 slot 失败的兜底（`copy_to_clipboard` 失败、`delete_bubble` 失败等）→ `logger.warning` + toast 仍发

### 2.3 must-log-ERROR（`logger.error(...)` 或 `logger.exception(...)`）

适用于**用户数据丢失 / 系统不可用**：
- 任务执行失败 → `logger.error("[Scheduler] 任务 X 执行失败: ...")`
- 自动保存 automations.json / automation_runs.json 失败 → `logger.error`
- 模型列表写盘失败 → `logger.error`
- 当前专家持久化失败 → `logger.error`
- `app.log` 写入失败（最末兜底）→ `logger.error` + stderr
- bridge 内部 `_append_history` 失败 → `logger.error`（用户消息可能丢）

---

## 3. 批次划分（6 批，顺序执行）

> 每批**独立可测可回滚**，依赖关系明确；批 2-5 之间业务独立，可顺序也可小并行；批 1 是地基必须先做。

```
   批 1 (基础设施)
       │
       ▼
   批 2 (scheduler)  ─┐
                      ├── 可与 3 / 4 并行（同模块依赖小）
   批 3 (bridge mixin)─┤
                      │
   批 4 (plugins)     ─┘
       │
       ▼
   批 5 (main.py 整合)
       │
       ▼
   批 6 (silent-pass 注释)
```

### 批 1：log.py 骨架 + 护栏测试（独立）

| 任务 | 文件 | 改动行数预估 |
|------|------|-------------|
| T1.1 | `qwen_app/log.py`（新增） | ~80 行 |
| T1.2 | `tests/test_day20_10_10_log.py`（新增） | ~120 行 |

**T1.1 内容**：`get_logger()` 工厂 + `init_logging()` 幂等初始化 + `is_test_env()` 检测 + `RotatingFileHandler(5MB×5)` + `StreamHandler(stderr, WARNING)`。

**T1.2 用例**（5 个）：
1. `test_get_logger_returns_cached_instance` —— 同名复用
2. `test_get_logger_naming_hierarchy` —— 子 logger 名字正确
3. `test_init_logging_idempotent` —— 多次调不重复挂 handler
4. `test_root_logger_has_file_handler` —— file handler 写入 `data/logs/app.log`
5. `test_test_env_raises_level_to_warning` —— 测试环境下 root level ≥ WARNING

**IS_PASS 标准**：5/5 通过；其余 739 测试不回归。

### 批 2：`scheduler.py` print → logger + 关键 except 加 log

| 任务 | 文件 | 改动行数预估 |
|------|------|-------------|
| T2.1 | `qwen_app/scheduler.py` | ~25 行改（含 4 处 print、5 处关键 except） |

**T2.1 具体位置**：
- L66：`save_automations` 写盘失败 → `logger.error`
- L85：`save_runs_index` 写盘失败 → `logger.error`
- L181-182：`is_due` 配置异常 → `logger.warning`（silent-WARN）
- L750：`_record` 写 md 日志失败 → `logger.error`
- 文件顶部新增 `from .log import get_logger; logger = get_logger("qwen_app.scheduler")`

### 批 3：chat_bridge.py + 7 个 mixin print → logger

| 任务 | 文件 | 改动行数预估 |
|------|------|-------------|
| T3.1 | `qwen_app/chat_bridge.py` | ~5 行（1 处 print） |
| T3.2 | `qwen_app/_bridge_bubble.py` | ~12 行（12 处 print） |
| T3.3 | `qwen_app/_bridge_send.py` | ~5 行（5 处 print） |
| T3.4 | `qwen_app/_bridge_model.py` | ~10 行（10 处 print） |
| T3.5 | `qwen_app/_bridge_plugin.py` | ~3 行（3 处 print） |
| T3.6 | `qwen_app/_bridge_menu.py` | ~7 行（7 处 print） |
| T3.7 | `qwen_app/_bridge_stream.py` | ~2 行（2 处 print） |

**每个 mixin 顶部**：新增 `from .log import get_logger; logger = get_logger("qwen_app.bridge.<slice>")`。

**保留 `print` 的**：`_bridge_bubble.py:231`（`[TTS] {text}`）属于 TTS 退化路径「打印到日志」的语义保留 → 改 logger.info 即可（它仍是输出文本内容）；不是 silent-pass。

### 批 4：`plugin_manager.py` + 关键插件 print → logger

| 任务 | 文件 | 改动行数预估 |
|------|------|-------------|
| T4.1 | `qwen_app/plugin_manager.py` | ~5 行（3 处 print + 1 处 traceback） |
| T4.2 | `plugins/_secret_store.py` | ~2 行（1 处 print） |
| T4.3 | `plugins/sql_helper.py` | ~5 行（3 处 print） |
| T4.4 | `plugins/ssh_runner.py` | ~5 行（4 处 print） |
| T4.5 | `plugins/web_search.py` | ~5 行（4 处 print） |
| T4.6 | 其它插件（calculator/clock/weather/web_fetch/excel/docx/k8s_manifest/novel_writer）| 0 处 print（实测 0 个，无需改）|

**批 4.1 特别说明**：`_secret_store.py` 是被其它插件**绝对导入**（`from plugins import _secret_store`），其 print 改 logger 必须确保 `qwen_app.log` 已初始化（批 1 完成）。这是依赖方向：批 4 依赖批 1。

### 批 5：`main.py` print 与 app.log tee 整合

| 任务 | 文件 | 改动行数预估 |
|------|------|-------------|
| T5.1 | `main.py` | ~15 行改 |

**T5.1 具体位置**：
- L122 `_install_stream_guard()` 之后新增 `init_logging()` 调一次（**不传参走默认路径**，让 `is_test_env` 自动检测）
- 启动横幅（`[main] QtQuick UI 已启动` / `[main] bridge.isBusy = ...`）→ **保留 print**（首屏信号）
- `_safe_enable_watcher` / `_safe_start_scheduler` / `_safe_migrate_api_keys` / `run_pyqt5` 里的 print → `logger.info` / `logger.warning`
- `_run_selftest` 内的 `emit()` 内部 `print(s)` **保留 print**（selftest 模式专用）
- QtQuick 加载失败 fallback print → `logger.warning`
- 顶层 `run_qtquick` 里 `traceback.print_exc()` → `logger.exception`（带 traceback）

**去重策略**：logger 与 tee 写到同一 `app.log` 文件不会冲突（tee 是 raw、logger 是格式化），但视觉上有点混乱。**取舍：保持共存**，因为：
- 删 tee → 任何非我们控制的 `print`（包括第三方库）就丢了
- 只用 tee → 我们自己的代码缺时间戳/level 信息
- 共存 → 排查时既有结构化日志又有原始 print，兼容现有运维/CI 抓取

### 批 6：silent-pass 加注释

| 任务 | 文件 | 改动行数预估 |
|------|------|-------------|
| T6.1 | 6 个文件约 ~25 处 silent-pass 加 1 行注释 | ~25 行 |

**T6.1 注释模板**：
```python
except Exception:
    # silent-OK: <具体理由 1 行>
    pass
```

**判定脚本**（架构师提供 grep 模式，工程师批量执行）：
```bash
# 找出所有 silent-pass 候选（保留 scheduler_run / 注释 / docstring 的不算）
grep -rn "except Exception" qwen_app/ plugins/ main.py \
  | grep -v "logger\." \
  | grep -E ": *pass$|: *continue$"
```

**预期 silent-pass 候选清单**（架构师已审过 ~493 处中的 silent-OK 部分）：
- `qwen_app/scheduler.py` 回调循环（~2 处）
- `qwen_app/paths.py` 探测/扫描（~4 处）
- `qwen_app/chat_bridge.py` slot 兜底 + `_on_worker_finished` 计数
- `qwen_app/_bridge_*` plugin reload / watcher init
- `main.py` `_install_stream_guard` / `_safe_migrate_api_keys`
- `plugins/_secret_store.py` / `plugins/sql_helper.py` keyring 降级路径

---

## 4. 任务列表（给工程师，≤5）

> 按依赖顺序 ≤5 任务，每任务 ≥3 文件；第一个任务是项目基础设施（log.py + 护栏测试）。

### T01：项目基础设施（log.py + 护栏测试 + 工具接入）

**优先级**：P0（必须最先做，其它批依赖它）

**Source Files**：
- `qwen_app/log.py`（新增）
- `tests/test_day20_10_10_log.py`（新增）
- `tests/docs/refactor_logging_arch.md`（已存在 → 工程师阅读）
- `qwen_app/__init__.py`（**不引入 logger**，保持干净）

**说明**：
1. 创建 `qwen_app/log.py`（含 `get_logger` / `init_logging` / `is_test_env` 三个公开 API）。
2. 创建 `tests/test_day20_10_10_log.py` 跑 5 个用例，全部 PASS 才进 T02。
3. 验证：单独跑 `python tests/test_day20_10_10_log.py` + 全量 `python tests/run_full_tests.py` 仍 739 通过。

### T02：scheduler.py 与 chat_bridge.py 主体迁移（批 2 + 批 3 部分）

**优先级**：P1

**Source Files**：
- `qwen_app/scheduler.py`
- `qwen_app/chat_bridge.py`
- `qwen_app/_bridge_bubble.py`
- `qwen_app/_bridge_send.py`

**说明**：4 处 scheduler print + 5 处关键 except + chat_bridge 1 处 print + bubble 12 处 + send 5 处。引入 logger 顶部 import。

### T03：剩余 bridge mixin + plugin_manager 迁移（批 3 剩余 + 批 4 部分）

**优先级**：P1

**Source Files**：
- `qwen_app/_bridge_model.py`
- `qwen_app/_bridge_plugin.py`
- `qwen_app/_bridge_menu.py`
- `qwen_app/_bridge_stream.py`
- `qwen_app/plugin_manager.py`

**说明**：40 处 print → logger。bridge_menu.py 含 main.py 启动期调用路径，注意 `start_automation_scheduler` 内的 `print(..., flush=True)` 是关键路径。

### T04：关键插件迁移 + main.py 整合 + silent-pass 注释（批 4 + 批 5 + 批 6）

**优先级**：P1

**Source Files**：
- `plugins/_secret_store.py`
- `plugins/sql_helper.py`
- `plugins/ssh_runner.py`
- `plugins/web_search.py`
- `main.py`
- `qwen_app/scheduler.py`（silent-pass 注释，~2 处）
- `qwen_app/paths.py`（silent-pass 注释，~4 处）
- `qwen_app/_bridge_plugin.py`（silent-pass 注释，~2 处）

**说明**：12 处插件 print + main.py 15 行整合 + ~25 处 silent-pass 注释。

### T05：护栏测试 (静态扫描 + runtime 行为)

**优先级**：P0（与代码同源，工程师在 T01~T04 任一批完成后立即写）

**Source Files**：
- `tests/test_day20_10_10_log.py`（T01 已建）
- `tests/test_day20_10_10_no_bare_print.py`（新增，静态扫描）
- `tests/test_day20_10_10_critical_paths_log.py`（新增，runtime 行为）

**T05.1 用例（静态扫描 `no_bare_print.py`）**：
1. `test_no_bare_print_in_qwen_app_business` —— 业务代码不含裸 `print(...)`
2. `test_main_py_banner_prints_kept` —— 启动横幅（精确字符串匹配）保留
3. `test_selftest_prints_kept` —— `_run_selftest` 内 `emit()` 的 `print(s)` 保留
4. `test_test_fixtures_not_scanned` —— `tests/test_*.py` 不扫（避免污染）

**T05.2 用例（runtime `critical_paths_log.py`）**：
1. `test_scheduler_run_failure_logs_error` —— 模拟任务跑失败，caplog 抓 ERROR
2. `test_bridge_save_cfg_failure_logs_error` —— 模拟 config 写盘失败，caplog 抓 ERROR
3. `test_silent_ok_paths_dont_force_log` —— cancel / cleanup 不强制写日志

**IS_PASS 标准**：T01 + T02 + T03 + T04 完成后，5 + 4 + 3 = 12 个新增测试用例全过；原 739 测试不回归。

---

## 5. 风险评估（5 条）

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| 1 | **PyInstaller `--noconsole` 下 stderr 为 None**，logger 写 stderr 失败抛异常 | 中 | 中 | `init_logging()` 内 try/except 包裹 stderr handler 挂载，失败降级只用 file handler；测试覆盖 `test_root_logger_has_file_handler` |
| 2 | **`RotatingFileHandler` 写 `app.log` 与 `_install_stream_guard` tee 写到同一文件**导致 file lock / OSError | 低 | 中 | 两者都开 `buffering=1`（line buffered）+ 同进程无并发线程写；Linux/Windows 都安全；如观察冲突回退方案：tee 改写到 `app_stdout.log` 区分 |
| 3 | **测试环境自动降级 WARNING** 会让测试日志变干净，**但调试期需要 INFO** | 低 | 低 | `QWEN_TESTING=0` 或 `QWEN_LOG_LEVEL=INFO` 环境变量可强制提到 INFO；写进 `is_test_env()` 文档 |
| 4 | **跨平台 `RotatingFileHandler` 在 Windows 网络盘 / OneDrive 上 rename 失败** | 低 | 高 | 退化方案：`RotatingFileHandler` 初始化包 try/except，失败 fallback `FileHandler(..., mode='a')` 不滚动，**永不抛** |
| 5 | **bridge 大量 print 改 logger 后，单条 log 的 level 名 + 模块名让 ut_full3.txt 输出变长** | 高 | 低 | 测试已用 `is_test_env` 降级 WARNING；print-only DEBUG/INFO 不打。仅 ERROR/WARNING 会到 ut_full3.txt，长度增量 < 50 行 |

---

## 6. 不做的事（边界，避免越界）

- ❌ 不引入 `loguru` / `structlog` 等三方库。
- ❌ 不改测试文件 `tests/test_*.py` 里的 print（fixture 用的）。
- ❌ 不重写 `_install_stream_guard`（保留作为兜底 tee）。
- ❌ 不动 `chat_window.py` / `chat_qml_window.py`（非 P1+P2 范围；它们的 print 由本批间接通过 chat_bridge 覆盖）。
- ❌ 不动 `scheduler_run.py` 的 print（独立模式 CLI 直接输出）。
- ❌ 不改 `qwen_app/chat_render.py` / `worker.py` / `config.py`（实测 print < 5 处且非关键路径；保持稳定优先；如有遗留由下一轮处理）。

---

## 7. 验收清单

- [ ] T01 ~ T04 完成 + T05 护栏全过
- [ ] `python tests/run_full_tests.py` 仍 739+12 = **751 测试全过**
- [ ] `grep -rn "print(" qwen_app/scheduler.py qwen_app/chat_bridge.py qwen_app/_bridge_*.py qwen_app/plugin_manager.py main.py` 仅剩 5 处保留：
  - main.py 启动横幅 ~3 处
  - main.py `_run_selftest` 内 `print(s)` ~1 处
  - main.py `traceback.print_exc` 0 处（改为 logger.exception）
- [ ] 关键路径 493 处 `except Exception` 至少 80% 有 logger；剩余 ~20% 是 silent-OK 路径（带 1 行注释）
- [ ] `data/logs/app.log` 同时包含 logger 输出（带时间戳）与 tee 输出（raw print）；grep `[Scheduler]` / `[chat_bridge]` 能定位
- [ ] 测试环境跑 `run_full_tests.py` 不再被 INFO 日志污染 ut_full3.txt

---

## 8. 附：与既有 app.log 落点对比

| 场景 | 现状 | 重构后 |
|------|------|--------|
| 源码态 console=True | print → 控制台 | logger → file + ERROR → stderr；print 保留横幅 → 控制台 |
| 源码态 console=False | N/A | N/A |
| 打包态 --noconsole | print → tee → app.log | logger → app.log；tee 仍兜 raw print → app.log；用户查日志更丰富 |
| 打包态 --selftest | `lines + print(s)` → stdout + selftest.log | **不变**（print 保留，selftest 模式不被 logger 改写） |
| 测试（offscreen） | 不写文件 | logger WARNING+ → app.log；INFO 不打 → ut_full3.txt 不污染 |

---

> **架构师 Bob 完稿。下一步：工程师按 T01-T05 顺序实现；批 1 完成后立即在 PR 里 review log.py 与护栏测试，再放后续批。**
