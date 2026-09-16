"""统一的路径解析 —— 兼容「源码运行」与「PyInstaller 打包」两种形态。

为什么需要这一层（Day 20.6.13）：
    项目原先所有路径都由 ``__file__`` 推导（config / plugin_manager /
    scheduler / workspace / tools 五处）。源码运行时 ``__file__`` 在项目根下，
    一切正常；但 PyInstaller 打包后 ``__file__`` 指向**解包目录** ``sys._MEIPASS``：

    · onefile —— ``_MEIPASS`` 是临时目录，**进程退出即删** → 用户存在 data/ 里的
      API Key、会话库、自动化任务每次启动全部归零；
    · onedir —— 数据写进程序目录，装到 ``C:\\Program Files\\`` 下无写权限。

    所以必须把「**只读资源**」和「**可写数据**」分开解析：
      只读资源（QML / experts / 内置插件模板）→ ``_MEIPASS``（打包）/ 项目根（源码）
      可写数据（配置 / 会话库 / 自动化 / 工作区）→ exe 同级 data/（便携）
      外部插件（用户可增删改、支持热重载）→ exe 同级 plugins/

约定（源码态与打包态行为差异只在「打包特有」的分支里，源码态一律保持原样）：
    QWEN_DATA_DIR 环境变量 > exe 同级 data/（可写时）> %APPDATA%\\qwen
"""
import os
import sys

# ── 冻结检测 ──

def is_frozen():
    """是否运行在 PyInstaller 等打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def _src_root():
    """源码态的项目根（qwen_app/paths.py -> qwen_app -> 项目根）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    """程序所在目录 —— 可写数据的默认落点。

    · 打包态：exe 所在目录（便携模式：拷走整个文件夹即带走配置）
    · 源码态：项目根
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _src_root()


def resource_dir():
    """只读资源根 —— QML / experts / 内置插件模板。

    · 打包态：``sys._MEIPASS``（PyInstaller 解包目录）
    · 源码态：项目根
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", app_dir())
    return _src_root()


# ── 可写性探测 ──

def _dir_writable(path):
    """真探测目录可写性（``os.access`` 在 Windows 上不可靠）。"""
    probe = os.path.join(path, ".qwen_write_probe")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("")
        os.remove(probe)
        return True
    except Exception:
        return False


_data_dir_cache = None


def data_dir():
    """可写数据根目录（模型配置 / 会话库 / 自动化 / 知识库 / 工作区）。

    优先级：
      1. 环境变量 ``QWEN_DATA_DIR``（测试与高级用户可用它重定向）
      2. 源码态：``<项目根>/data``（与原行为完全一致）
      3. 打包态：``<exe目录>/data``（便携）；**目录不可写时**回退
         ``%APPDATA%\\qwen``（例如装在 Program Files 下）
    """
    global _data_dir_cache
    if _data_dir_cache:
        return _data_dir_cache

    env = (os.environ.get("QWEN_DATA_DIR") or "").strip()
    if env:
        _data_dir_cache = os.path.abspath(env)
        return _data_dir_cache

    if not is_frozen():
        _data_dir_cache = os.path.join(_src_root(), "data")
        return _data_dir_cache

    portable = os.path.join(app_dir(), "data")
    if _dir_writable(app_dir()):
        _data_dir_cache = portable
        return _data_dir_cache

    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    _data_dir_cache = os.path.join(base, "qwen")
    return _data_dir_cache


def _reset_cache_for_tests():
    """仅用于测试：清掉 data_dir() 的进程内缓存，便于模拟打包态重新解析。"""
    global _data_dir_cache
    _data_dir_cache = None


def log_dir():
    """运行日志目录（--noconsole 下 stdout/stderr 的落点）。"""
    return os.path.join(data_dir(), "logs")


def workspaces_base():
    """对话 / 定时任务工作目录的父目录（必须可写）。

    · 源码态：``<项目根>/.workbuddy/workspaces``（保持历史位置不变）
    · 打包态：``<可写数据目录>/workspaces`` —— 工作区产物是用户数据，
      不能落在 _MEIPASS（临时目录，退出即删）。
    """
    if is_frozen():
        return os.path.join(data_dir(), "workspaces")
    return os.path.join(_src_root(), ".workbuddy", "workspaces")


# ── 插件目录（外部优先，保留热重载能力） ──

def builtin_plugins_dir():
    """内置插件目录（只读模板，来自打包资源或项目根）。"""
    return os.path.join(resource_dir(), "plugins")


def external_plugins_dir():
    """外部插件目录（用户可增删改，watcher 监视它做热重载）。"""
    if is_frozen():
        return os.path.join(app_dir(), "plugins")
    # 源码态「外部」即项目根的 plugins/（也就是内置目录本身）
    return os.path.join(_src_root(), "plugins")


def ensure_external_plugins():
    """打包态：外部插件目录为空时，把内置插件模板释放一份过去。

    这样用户拿到 exe 后，同级会有一个可自由修改的 plugins/ 目录，
    「替换 .py 热更新」的设计能力才真正可用。

    返回 (释放的文件数, 目标目录)；源码态恒为 (0, 项目 plugins/)。
    """
    ext = external_plugins_dir()
    if not is_frozen():
        return 0, ext

    try:
        os.makedirs(ext, exist_ok=True)
        existing = [f for f in os.listdir(ext) if f.endswith(".py")]
        if existing:
            return 0, ext

        import shutil
        src = builtin_plugins_dir()
        copied = 0
        if os.path.isdir(src):
            for name in os.listdir(src):
                if name == "__pycache__":
                    continue
                s, d = os.path.join(src, name), os.path.join(ext, name)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
                copied += 1
        return copied, ext
    except Exception:
        return 0, ext


def plugins_dirs():
    """生效的插件搜索目录列表（去重，外部优先）。"""
    dirs = []
    for d in (external_plugins_dir(), builtin_plugins_dir()):
        if d and d not in dirs and os.path.isdir(d):
            dirs.append(d)
    return dirs or [builtin_plugins_dir()]


def plugins_dir():
    """主插件目录（discover/watcher 使用）—— 外部存在则用外部。"""
    ext = external_plugins_dir()
    if os.path.isdir(ext):
        try:
            if any(f.endswith(".py") for f in os.listdir(ext)):
                return ext
        except Exception:
            pass
    return builtin_plugins_dir()


def scan_plugin_names():
    """扫描生效的插件目录，返回插件名（去重、有序）。

    排除下划线开头的包内私有模块（``__init__`` / ``_secret_store`` /
    ``_cmd_blocklist``）——它们不是插件，不应出现在「启用列表」里。

    config._scan_plugins 与 tools._scan_plugins 曾各存一份同样的实现，
    这里统一成单一事实源（Day 20.6.13）。
    """
    names = []
    for base in plugins_dirs():
        try:
            for f in sorted(os.listdir(base)):
                if f.endswith(".py") and not f.startswith("_"):
                    name = f[:-3]
                    if name not in names:
                        names.append(name)
        except Exception:
            pass
    return names
