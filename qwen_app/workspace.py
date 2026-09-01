"""工作目录管理 — 每个对话 / 每个定时任务一个独立工作目录

设计目标：
- 每个对话 / 每个定时任务有独立子目录，工具产物互不污染
- 调度链：active_workspace（来自对话/任务） > workspace_root（全局兜底） > 项目根/.workbuddy/workspaces/（默认）
- 命名：talk_<id>_<YYYYMMDD_HHMMSS> / cron_<id>_<YYYYMMDD_HHMMSS>
- 惰性创建：首次 get_active_or_create() 时才建目录，避免启动时为每个老任务创建空目录
- 线程安全：模块级单例 + lock；set/clear 由调用方在 worker 线程前后包裹
"""
import os
import threading
import time

# ── 路径常量 ──
# 项目根 = 本文件父目录的父目录（即 qwen_app/workspace.py -> qwen_app/ -> 项目根）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_BASE = os.path.join(_PROJECT_ROOT, ".workbuddy", "workspaces")

# 兼容：若老配置写了 workspace_root，作为「父目录」使用（指向哪里就在那里建 .workspaces/）
# 不再单独保留顶层目录配置。

# ── 命名 ──
_PREFIX_CONV = "talk_"
_PREFIX_CRON = "cron_"


def _ts():
    """创建时间戳后缀：YYYYMMDD_HHMMSS"""
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def default_base():
    """默认工作目录的父目录：项目根/.workbuddy/workspaces"""
    return _DEFAULT_BASE


def resolve_base():
    """实际使用的父目录：老配置的 workspace_root 优先（视为父目录），否则默认。"""
    try:
        from .config import load_config
        cfg = load_config()
        legacy = (cfg.get("workspace_root") or "").strip()
        if legacy and os.path.isdir(legacy):
            # 老配置视为父目录：子目录统一放 <legacy>/.workspaces/ 下
            return os.path.join(os.path.abspath(legacy), ".workspaces")
    except Exception:
        pass
    return _DEFAULT_BASE


def conv_workspace_path(conv_id, created_at=None):
    """对话工作目录路径（不一定存在）。

    conv_id: 对话 id（短 id 字符串）
    created_at: 对话 created_at ISO 字符串（用作时间戳后缀），
                缺省时用当前时间（新对话）。
    """
    ts = _ts_from_iso(created_at) if created_at else _ts()
    return os.path.join(resolve_base(), f"{_PREFIX_CONV}{conv_id}_{ts}")


def cron_workspace_path(auto_id, created_at=None):
    """定时任务工作目录路径（不一定存在）。

    auto_id: 任务 id（12 位 hex）
    created_at: 任务 created_at ISO 字符串；缺省时用当前时间。
    """
    ts = _ts_from_iso(created_at) if created_at else _ts()
    return os.path.join(resolve_base(), f"{_PREFIX_CRON}{auto_id}_{ts}")


def _ts_from_iso(iso):
    """ISO 字符串 -> YYYYMMDD_HHMMSS。解析失败回退当前时间。"""
    try:
        # 形如 2026-08-31T14:23:10.917636
        s = iso.replace("T", " ")[:19]
        return time.strftime("%Y%m%d_%H%M%S", time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return _ts()


# ── 线程局部 active 上下文（避免多 worker 并发误清） ──
# 用 threading.local 而不是模块级变量：
# - Worker A 在自己的线程 set，Worker B 在自己线程 set，互不干扰
# - A 跑完 finally clear 时只清 A 自己的 thread-local，不会清掉 B 的
# - 主线程（GUI）也独立，不影响后台 worker
_tls = threading.local()


def set_active_workspace(path):
    """设置当前线程的活跃工作目录（worker 线程/插件调用前由调用方设置）。"""
    _tls.active_workspace = os.path.abspath(path) if path else None


def clear_active_workspace():
    """清理当前线程的活跃工作目录（调用方在 try/finally 里调用）。"""
    _tls.active_workspace = None


def get_active_workspace():
    """读取当前线程的活跃工作目录（无则 None）。插件应使用 resolve_workspace()。"""
    return getattr(_tls, "active_workspace", None)


def resolve_workspace():
    """解析当前应使用的工作目录（插件调用入口）。

    优先级：当前线程 active workspace > 全局回退（默认 .workbuddy/workspaces/）。
    返回的路径保证存在；不存在则惰性创建。
    """
    p = get_active_workspace()
    if not p:
        # 无 active 上下文：回退到默认全局目录（共享区）
        p = default_base()
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    return os.path.abspath(p)


def reset_for_tests():
    """仅用于测试：清空当前线程的 active 上下文"""
    _tls.active_workspace = None