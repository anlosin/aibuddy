"""统一日志工厂 — 把 stdlib ``logging`` 收敛到根 logger ``qwen``。

设计要点（与 ``tests/docs/refactor_logging_arch.md`` §1.1 / §1.3 对齐）：

1. **根 logger 名 = "qwen"**：避免污染其它库的 root logger；所有业务模块
   通过 ``get_logger("qwen_app.<module>")`` 或 ``get_logger("qwen_app.plugins.<name>")``
   加入层级，子 logger 自动 propagate 到根。
2. **文件 + stderr 双 handler**：
   - ``RotatingFileHandler(data_dir()/logs/app.log, maxBytes=5MB, backupCount=5)``,
     level=INFO。源态写源码态 ``data/logs/app.log``；打包态写可写数据目录。
   - ``StreamHandler(sys.stderr, level=WARNING)``，用于把 WARNING+ 同时打到控制台。
3. **测试环境自动降级 WARNING**（``PYTEST_CURRENT_TEST`` /
   ``QT_QPA_PLATFORM=offscreen`` / ``QWEN_TESTING=1``）—— 避免污染 ut_full3.txt。
4. **幂等** ``init_logging()``：handler 已挂则 no-op；run_full_tests 与 main.py
   同一进程可重复调用。
5. **零第三方依赖**：纯 stdlib ``logging``，不引入 loguru/structlog。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import paths


# ── 常量 ──

ROOT_LOGGER_NAME = "qwen"
"""统一根 logger 名 —— 子 logger 全部挂在它下面（避免污染 root logger）。"""

_FILE_MAX_BYTES = 5 * 1024 * 1024    # 5MB / 文件
_FILE_BACKUP_COUNT = 5               # 滚动 5 个备份 + 当前 = 共 6 个
_FILE_LEVEL = logging.INFO
_STDERR_LEVEL = logging.WARNING


# ── 测试环境检测 ──

def is_test_env() -> bool:
    """检测当前是否在测试环境（影响日志级别降级）。

    三合一：``PYTEST_CURRENT_TEST``（unittest 自动设）/ ``QT_QPA_PLATFORM=offscreen``
    （既有 QWidget 测试 100% 满足）/ ``QWEN_TESTING=1``（手动开关）。

    返回 True → ``init_logging()`` 把根 logger level 提到 WARNING；
    返回 False → 默认 INFO。
    """
    return (
        "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("QT_QPA_PLATFORM") == "offscreen"
        or os.environ.get("QWEN_TESTING") == "1"
    )


# ── logger 工厂 ──

def get_logger(name: str) -> logging.Logger:
    """模块级 logger 工厂。

    约定：
      - 业务模块: ``get_logger("qwen_app.<module>")``
        例: ``get_logger("qwen_app.scheduler")``
      - 桥 mixin: ``get_logger("qwen_app.bridge.<slice>")``
        例: ``get_logger("qwen_app.bridge.bubble")``
      - 插件: ``get_logger("qwen_app.plugins.<name>")``
        例: ``get_logger("qwen_app.plugins.ssh_runner")``
      - 主入口: ``get_logger("qwen_app.main")``

    子 logger 沿用根 logger 的 handlers 与 effective level（Python 默认 propagate）。
    同名 get_logger 返回标准 cache 的同一实例 —— 多次导入同一个模块共享。
    """
    return logging.getLogger(name)


# ── 初始化 ──

_INITIALIZED = False


def init_logging(level=None) -> None:
    """幂等初始化。main.py 启动期调一次；测试**不显式调**（自动 WARNING）。

    行为：
      1. 根 logger = ``"qwen"``（不是默认 root logger）。
      2. file handler = ``RotatingFileHandler`` 写 ``data/logs/app.log``（5MB×5），
         写失败（PyInstaller --noconsole / 网络盘）降级为 ``FileHandler`` 不滚动。
      3. stderr handler = ``StreamHandler(sys.stderr, level=WARNING)``；
         stderr 为 None / 写入失败时跳过（不影响 file handler）。
      4. 测试环境（见 ``is_test_env``）→ 根 logger level 提到 WARNING；
         生产 / dev 模式用 INFO。
      5. **parent chain 自修复** —— ``reparent_existing_qwen_loggers``：模块顶部
         ``logger = get_logger("qwen_app.<module>")`` 早于 ``init_logging()`` 被
         执行时，``logging`` 库建好父链 ``qwen_app.<module>`` → ``qwen_app`` →
         ``root``（因为当时 ``qwen`` logger 还不存在）。这里把 ``qwen_app*`` 与
         ``qwen.*`` 已存 logger 的 parent 全部指到新建的 ``qwen`` 根 logger，
         让 propagate 真正命中 file / stderr handler。
         幂等：已挂到 ``qwen`` 上的再指一次也安全（Python logger 弱引用）。

    Args:
        level: 自定义根 logger level（None=按 is_test_env 自动）。
               传 ``"INFO"`` / ``logging.INFO`` / ``10`` 都接受。
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    root = logging.getLogger(ROOT_LOGGER_NAME)
    # 避免 log message 二次经过 root logger（如果调用方主动配过）
    root.propagate = False

    # ── 关键：先预创建 "qwen_app" 顶层 ancestor ──
    # 这样 ``reparent_existing_qwen_loggers`` 才能把它从 parent=root 修成 parent=qwen。
    # 不预创建的话：init 时如果业务模块还没 import（罕见，但 PyInstaller 启动早期可能发生），
    # qwen_app 就不在 loggerDict 里 → reparent 跳过它 → 后续动态 import 业务模块时
    # 新建的 qwen_app.<X>.logger.parent 仍会经 qwen_app → root 走到 Python 默认 root
    # 而非我们的 file handler。这是 P0 修复的隐性必要步骤。
    logging.getLogger("qwen_app")

    # ── 决定 level ──
    if level is None:
        if is_test_env():
            effective_level = logging.WARNING
        else:
            effective_level = logging.INFO
    else:
        effective_level = _coerce_level(level)

    root.setLevel(effective_level)

    # ── file handler（滚动 + 失败降级）──
    fh = _build_file_handler()
    if fh is not None:
        root.addHandler(fh)

    # ── stderr handler（WARNING+，None/失败时跳过）──
    sh = _build_stderr_handler()
    if sh is not None:
        root.addHandler(sh)

    # ── parent chain 自修复 ──
    # 业务模块顶部 `logger = get_logger("qwen_app.<module>")` 早于 main.py
    # 调 init_logging() 时，Python logging 已建好 `qwen_app.<module>` →
    # `qwen_app` → `root` 的 parent 链；那时 'qwen' logger 还没创建。
    # 这里把已存在的 qwen_app* / qwen.* logger 的 parent 重新挂到 'qwen' 根
    # logger 上，让它们的 propagate 命中 file / stderr handler。
    # 见 QA P0 bug：`qwen_app.scheduler.parent.name` 之前是 'root'（链上游）。
    reparent_existing_qwen_loggers(root)

    _INITIALIZED = True


def reparent_existing_qwen_loggers(qwen_log: logging.Logger) -> int:
    """把 ``logging.root.manager.loggerDict`` 里所有处于断裂链顶层的
    ``qwen_app*`` / ``qwen.*`` logger 的 parent 重新挂到 ``qwen_log``。

    解决 P0：业务模块顶部 ``logger = get_logger("qwen_app.<module>")`` 早于
    ``init_logging()`` 被执行，造成 parent 链是 ``qwen_app.<module>`` →
    ``qwen_app`` → ``root``，而不是 ``→ qwen``。``init_logging()`` 末尾调用本函数
    把链修回正确拓扑。

    设计：
      - 只 reparent 那些 parent 当前指向 ``"root"`` 的顶层 ancestor（如 ``qwen_app``
        自身 / ``qwen.foo`` 这种直接挂在 ``root`` 下的中间层）。其它中间层与叶子
        logger 通过 propagate 沿新父链自动连到 ``qwen`` —— 保持 Python logging
        自然层级（不强行 flatten）。
      - 只动 ``qwen_app`` / ``qwen_app.*`` / ``qwen.*`` 前缀（业务命名空间）；
        不动其它库的 logger。
      - ``logging.root.manager.loggerDict`` 是私有 API，但 Python stdlib
        文档明确支持（``logging.setLogRecordFactory`` 等也用）。
      - 幂等：第二次调时所有匹配项的 parent 已是 ``qwen``，循环跳过，return 0。
      - 不重复挂 handler（仅 parent 赋值，不动 handler list）。

    Args:
        qwen_log: 已创建好的 ``"qwen"`` 根 logger 实例（来自
            ``logging.getLogger(ROOT_LOGGER_NAME)``）。

    Returns:
        重挂的 logger 数量（方便测试断言）。
    """
    count = 0
    # 复制 keys 防止遍历中字典变更（虽然本函数只读不写，但保险）
    for name in list(logging.root.manager.loggerDict.keys()):
        if name == ROOT_LOGGER_NAME:
            continue  # 跳过自身
        # 命名空间边界：仅 qwen_app[.*] / qwen.[*]（不匹配 qwen_other_lib）
        in_namespace = (
            name == "qwen_app"
            or name.startswith("qwen_app.")
            or name.startswith("qwen.")
        )
        if not in_namespace:
            continue
        child = logging.getLogger(name)
        # 只动「parent 还指向 root」的 —— 这就是 init 之前被建错顶层的情况。
        # 其它中间层（如 qwen_app.bridge.parent = qwen_app）会通过 propagate
        # 沿父链自动到达 qwen，不动它们以保持 Python 自然层级。
        if child.parent is qwen_log:
            continue  # 已正确，跳过
        if child.parent is not None and child.parent.name != "root":
            continue  # 中间层（parent 是 qwen_app / qwen.x），不动
        child.parent = qwen_log
        count += 1
    return count


def _coerce_level(level):
    """把字符串 / int 都解析成 logging level（无效则 INFO）。"""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        try:
            return getattr(logging, level.upper())
        except AttributeError:
            pass
    return logging.INFO


def _build_file_handler():
    """构造 ``RotatingFileHandler``；初始化失败降级 ``FileHandler(append)``。

    日志目录创建失败 / 权限不足 / 网络盘 OneDrive rename 失败 → fallback。
    """
    try:
        log_dir = paths.log_dir()
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "app.log")
        # encoding utf-8 + line buffered（与 tee 复用同一文件不冲突）
        fh = RotatingFileHandler(
            log_path,
            maxBytes=_FILE_MAX_BYTES,
            backupCount=_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(_FILE_LEVEL)
        fh.setFormatter(logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        return fh
    except Exception:
        # 降级：append 模式不滚动（避免 OneDrive 等 rename 失败）
        try:
            log_dir = paths.log_dir()
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "app.log")
            from logging import FileHandler
            fh = FileHandler(log_path, mode="a", encoding="utf-8")
            fh.setLevel(_FILE_LEVEL)
            fh.setFormatter(logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            return fh
        except Exception:
            # 实在打不开日志文件也不抛 —— 不让启动失败
            return None


def _build_stderr_handler():
    """构造 ``StreamHandler(sys.stderr)``；stderr 为 None / 不可写时返回 None。

    PyInstaller ``--noconsole`` 下 ``sys.stderr`` 可能是 None；logger 写 stderr
    失败抛异常会影响正常流程。失败时直接跳过 stderr handler（不影响 file handler）。
    """
    try:
        if sys.stderr is None:
            return None
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setLevel(_STDERR_LEVEL)
        sh.setFormatter(logging.Formatter(
            fmt="%(levelname)s %(name)s: %(message)s",
        ))
        return sh
    except Exception:
        return None


def reset_for_tests() -> None:
    """仅用于测试：清掉 init_logging 的幂等标记并移除全部 handler。

    让 ``test_init_logging_idempotent`` 之类的用例可以反复测。
    """
    global _INITIALIZED
    root = logging.getLogger(ROOT_LOGGER_NAME)
    for h in list(root.handlers):
        try:
            h.close()
        except Exception:
            pass
        root.removeHandler(h)
    root.setLevel(logging.NOTSET)
    root.propagate = True
    _INITIALIZED = False
