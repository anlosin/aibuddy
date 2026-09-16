"""AI 对话助手 — 入口。

Day 9: 默认走 QtQuick UI（QML）。若 QML 加载失败 → fallback 到 PyQt5 chat_window。

启动顺序：
1. QApplication 创建（同时支持 QML + widgets）
2. ChatBridge 实例（QML 侧使用）
3. 加载 Main.qml，注入 bridge 为上下文属性
4. 加载失败 → fallback到 PyQt5 ChatWindow

用法：
    python main.py                # 默认 QtQuick
    python main.py --pyqt5        # 强制用 PyQt5 ChatWindow
    python main.py --qtquick      # 默认（仅为明确语义）
"""
import os
import sys


# 把项目根目录加入 sys.path，使 qwen_app 包可导入（直接 `python main.py` 也能跑）
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 抑制 libpng iCCP 警告（Qt 5.15.x 已修复大部分 PNG 警告；warnings.filterwarnings 留作兜底）
# 抑制 jieba 的 SyntaxWarning（jieba 库用普通字符串写正则 "\\." / "\\s"，
# Python 3.12+ 会告警 invalid escape sequence，但 jieba 多年没修——
# 这些不是真错误，会污染用户首屏让用户以为程序坏了）
import warnings
warnings.filterwarnings("ignore", message=".*iCCP.*")
warnings.filterwarnings("ignore", message=".*invalid escape sequence.*")
# 一些 jieba 抛 DeprecationWarning 也一并忽略
warnings.filterwarnings("ignore", category=DeprecationWarning, module="jieba.*")


# ── Day 20.6.13 打包支持：下面两步必须**先于**任何其他 qwen_app 导入 ──
from qwen_app import paths


class _NullStream:
    """日志文件也打不开时的最终兜底（吞掉所有输出，绝不抛异常）。"""

    encoding = "utf-8"

    def write(self, _s):
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False

    def writable(self):
        return True


class _Tee:
    """把写入同时送给多个流（跳过 None 与写入失败的流），保证「原来能看到的
    还能看到，日志也一定留档」。"""

    encoding = "utf-8"

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
            except Exception:
                pass
        return len(s) if isinstance(s, str) else 0

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def writable(self):
        return True


def _install_stream_guard():
    """打包后把 stdout/stderr 同时接到 ``<可写数据目录>/logs/app.log``。

    两个必须处理的现实：
      1. ``--noconsole`` 下 ``sys.stdout`` **可能为 None**，任何 ``print`` 都会
         抛 ``AttributeError: 'NoneType' object has no attribute 'write'`` —— 本项目
         print 遍布各模块，足以让程序在启动路径上直接崩；
      2. 打包态没有控制台，出问题时用户和我们**都没有任何落点**可看。
    所以打包态一律 tee 到日志文件（源码态完全不动，保持控制台原汁原味）。
    """
    if not paths.is_frozen():
        return

    sink = None
    try:
        os.makedirs(paths.log_dir(), exist_ok=True)
        sink = open(os.path.join(paths.log_dir(), "app.log"), "a",
                    encoding="utf-8", buffering=1)
    except Exception:
        sink = None
    if sink is None:
        sink = _NullStream()

    sys.stdout = _Tee(sys.stdout, sink)
    sys.stderr = _Tee(sys.stderr, sink)


# 1) 打包态：把内嵌插件模板释放到 exe 同级 plugins/，让「用户可增删改 + 热重载」
#    的设计在 exe 上依然成立。必须早于 plugin_manager 导入 —— 它的模块级
#    PLUGINS_DIR 在导入那一刻就固化了插件目录。
paths.ensure_external_plugins()

# 2) 无控制台时兜住 stdout/stderr，必须赶在任何输出之前。
_install_stream_guard()


from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWidgets import QApplication
from PyQt5.QtQml import QQmlApplicationEngine

from qwen_app.chat_bridge import ChatBridge


# 顶层对象的「保活区」。
#
# 为什么必须存在：QQmlApplicationEngine / ChatWindow 都是 Python 侧创建、由 PyQt
# 持有所有权的 QObject。若它们只是 run_qtquick()/run_pyqt5() 的局部变量，函数一返回
# 就被 Python GC 回收 —— engine 析构会连带销毁它创建的 QML 根窗口。结果是进程活着、
# app.exec_() 在跑、启动日志也正常打印，但**一个窗口都没有**（"启动不显示界面"）。
_KEEP_ALIVE = {}


def _set_qt_attributes():
    """高 DPI 属性必须在 QApplication 创建『之前』设置，否则不生效并打印告警。"""
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


def _setup_qt_app(app):
    """复用 PyQt5 原有的全局加固（颜色/字体）"""
    app.setStyle("Fusion")
    # Fusion 风格下，自定义白底 QSS 的 QComboBox 弹窗项在 hover/选中时
    # 文字颜色未被显式定义会继承成透明（悬停即空白行）。此处统一给所有下拉弹窗
    # 的 item 各状态定义深色文字 + 浅色背景，彻底杜绝该 bug。
    app.setStyleSheet("""
        QComboBox QAbstractItemView {
            background: #FFFFFF;
            border: 1px solid #E5E6EB;
            border-radius: 6px;
            outline: 0;
            selection-background-color: #EEF1FF;
            selection-color: #333;
        }
        QComboBox QAbstractItemView::item {
            color: #333;
            padding: 6px 12px;
        }
        QComboBox QAbstractItemView::item:selected {
            color: #333;
            background: #EEF1FF;
        }
        QComboBox QAbstractItemView::item:hover {
            color: #333;
            background: #F2F4F8;
        }
        /* Day 20.6: QtQuick 菜单栏在暗色模式下的可见性 —— 强制深色背景 +
           浅色字（深色下默认 #333 文字会被黑色系统背景吃掉，肉眼看不见）。
           QMenuBar/QMenu 来自 MenuBar/Menu 的 native 渲染路径；侧边栏 ⋯
           菜单和气泡 ⋯ 菜单走 QML Popup 不受影响，所以单独覆盖 native 那一段。 */
        QMenuBar { background: #15161A; color: #E8E8E8; padding: 0; border: 0; }
        QMenuBar::item { background: transparent; color: #E8E8E8; padding: 6px 10px; }
        QMenuBar::item:selected { background: #1E2027; color: #E8E8E8; }
        QMenu { background: #15161A; color: #E8E8E8; border: 1px solid #22232A; padding: 4px; }
        QMenu::item { padding: 6px 22px; color: #E8E8E8; background: transparent; }
        QMenu::item:selected { background: #1E2027; color: #FFFFFF; }
        QMenu::item:disabled { color: #5A5E68; }
        QMenu::separator { height: 1px; background: #22232A; margin: 4px 8px; }
    """)
    font = app.font()
    font.setFamily("Microsoft YaHei")
    app.setFont(font)


def run_qtquick(app):
    """走 QtQuick UI 路径。返回是否启动成功。"""
    try:
        engine = QQmlApplicationEngine()
        engine.warnings.connect(
            lambda warns: [print("QML WARN:", w.toString(), file=sys.stderr) for w in warns])

        bridge = ChatBridge(theme="light")
        engine.rootContext().setContextProperty("bridge", bridge)

        # 保活：engine 一旦被 GC，QML 根窗口立即销毁（见 _KEEP_ALIVE 注释）
        _KEEP_ALIVE["engine"] = engine
        _KEEP_ALIVE["bridge"] = bridge

        # 只读资源目录：源码态=项目根；打包态=PyInstaller 的 _MEIPASS
        qml_dir = os.path.join(paths.resource_dir(), "qwen_app", "qml")
        engine.addImportPath(qml_dir)
        engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, "Main.qml")))

        roots = engine.rootObjects()
        if not roots:
            print("[main] QtQuick UI 加载失败，fallback 到 PyQt5", file=sys.stderr)
            _KEEP_ALIVE.pop("engine", None)   # 别让 fallback 一直拎着这个失败的 engine
            _KEEP_ALIVE.pop("bridge", None)
            return False
        win = roots[0]
        if not win.property("visible"):
            win.setProperty("visible", True)
        print("[main] QtQuick UI 已启动", flush=True)
        print(f"[main] bridge.isBusy = {bridge.isBusy}, 当前会话 = {bridge._current_conv_id}", flush=True)
        print(f"[main] 当前模型 = {bridge._last_model_name}", flush=True)
        # Day 14: 启用插件热更新 watcher（Qt 事件循环已就绪后）
        # Day 16: 延迟到 exec_() 内启用 — 用户报告 0xC0000005 在这调用后立即崩
        # 改在 QTimer.singleShot(0, ...) 后再启，确保 Qt 事件循环完全就绪
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, lambda: _safe_enable_watcher(bridge))
        # Day 20.6: 启动即拉起自动化调度器（Scheduler + 30s check_due 定时器）。
        # 原来只在首次打开「任务管理」对话框时才懒构造 —— 不开对话框任务永远
        # 不按时执行。同样延迟到事件循环就绪后，失败不影响主 UI。
        QTimer.singleShot(0, lambda: _safe_start_scheduler(bridge))
        return True
    except Exception as e:
        print(f"[main] QtQuick 启动异常: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc()
        return False


def _safe_enable_watcher(bridge):
    """Day 16: 延迟到事件循环启动后再启用插件 watcher（避开启动竞态）"""
    try:
        bridge.enable_plugin_watcher()
        print("[main] 插件 watcher 已启用", flush=True)
    except Exception as e:
        print(f"[main] 插件 watcher 启动失败（不影响主 UI）: {e}", file=sys.stderr, flush=True)


def _safe_start_scheduler(bridge):
    """Day 20.6: 启动即构造自动化调度器（Scheduler + 30s check_due QTimer）。

    必须在事件循环就绪后调用（QTimer 需要事件循环才能 tick）。
    失败只打日志 —— 调度器挂了不能拖垮主界面；用户打开「任务管理」
    对话框时 _DialogHost.scheduler 仍会走兜底懒构造路径。
    """
    try:
        bridge.start_automation_scheduler()
    except Exception as e:
        print(f"[main] 自动化调度器启动失败（不影响主 UI）: {e}", file=sys.stderr, flush=True)


def run_pyqt5(app):
    """Fallback 路径：原 PyQt5 ChatWindow"""
    from qwen_app.chat_window import ChatWindow
    print("[main] 走 PyQt5 ChatWindow (原始路径)", file=sys.stderr)
    window = ChatWindow()
    window.show()
    _KEEP_ALIVE["window"] = window    # 同上：局部变量会被 GC，窗口随之消失
    return True


def _safe_migrate_api_keys():
    """Day 20.6.12: 启动期把磁盘上残留的明文 API Key 迁移进系统凭据库。

    幂等；只在 keyring 真实可用且写入成功后才重写磁盘，否则保留明文
    （宁可暂时留明文，也不能出现「磁盘清了、凭据库没存上」的丢 Key）。
    此处是单线程启动期，不会与 GUI / 调度器线程并发写配置文件。
    失败只打日志，绝不阻塞启动。
    """
    try:
        from qwen_app.config import migrate_api_keys_to_keyring
        moved, detail = migrate_api_keys_to_keyring()
        if moved:
            print(f"[main] API Key 已迁移到系统凭据库（{detail}）", flush=True)
    except Exception as e:
        print(f"[main] API Key 迁移跳过（不影响启动）: {e}", file=sys.stderr, flush=True)


def _run_selftest():
    """打包环境自检（不启动 GUI）。用法：``qwen.exe --selftest``

    打包产物最常见的「不完整」不是窗口出不来，而是某个**插件的第三方依赖**
    没被 PyInstaller 收进来 —— 平时看不出来，等用户点到那个功能才 ImportError
    （插件是动态加载的，静态分析扫不到，见 qwen_app/plugin_deps.py）。

    这里把 spec 声明过的依赖逐个真实 import，再真加载一遍全部插件，并把结论
    同时写到 stdout 与 ``<data>/logs/selftest.log``（--noconsole 下只有后者能看到）。

    返回进程退出码：0 = 全通过。
    """
    import importlib

    lines = []

    def emit(s=""):
        lines.append(s)
        try:
            print(s)
        except Exception:
            pass

    ok = True
    emit("=== qwen 打包自检 (--selftest) ===")
    emit(f"frozen         = {paths.is_frozen()}")
    emit(f"sys.executable = {sys.executable}")
    emit(f"sys.stdout     = {'None' if sys.stdout is None else type(sys.stdout).__name__}")
    emit(f"sys.stderr     = {'None' if sys.stderr is None else type(sys.stderr).__name__}")
    emit(f"resource_dir   = {paths.resource_dir()}")
    emit(f"app_dir        = {paths.app_dir()}")
    emit(f"data_dir       = {paths.data_dir()}")
    emit(f"plugins_dir    = {paths.plugins_dir()}")
    emit(f"Main.qml 存在  = "
         f"{os.path.isfile(os.path.join(paths.resource_dir(), 'qwen_app', 'qml', 'Main.qml'))}")
    emit("")

    emit("--- 插件依赖（AST 扫描插件源码 + 动态依赖）---")
    try:
        from qwen_app.plugin_deps import all_required_imports
        required = all_required_imports()
    except Exception as e:
        required = []
        ok = False
        emit(f"  FAIL 无法生成依赖清单: {e}")
    bad = []
    for mod in required:
        try:
            importlib.import_module(mod)
        except Exception as e:
            bad.append(f"{mod}: {type(e).__name__}: {e}")
    emit(f"  共 {len(required)} 个模块，失败 {len(bad)} 个")
    for b in bad:
        ok = False
        emit(f"  FAIL  {b}")
    emit("")

    emit("--- 插件加载 ---")
    try:
        from qwen_app.plugin_manager import discover_plugins, get_enabled_tools, get_plugin_meta
        from qwen_app.config import load_plugin_state

        plugins, _infos = discover_plugins()
        expected = set(get_plugin_meta(plugin_dir=paths.plugins_dir()))
        failed = sorted(expected - set(plugins))
        emit(f"  目录内插件 {len(expected)} 个，加载成功 {len(plugins)} 个")
        if failed:
            ok = False
            emit(f"  加载失败: {failed}")
        tools = get_enabled_tools(plugins, load_plugin_state())
        emit(f"  启用工具数 = {len(tools)}")
    except Exception as e:
        ok = False
        emit(f"  FAIL 插件加载异常: {type(e).__name__}: {e}")

    emit("")
    emit("RESULT: " + ("PASS" if ok else "FAIL"))

    blob = "\n".join(lines) + "\n"
    try:
        os.makedirs(paths.log_dir(), exist_ok=True)
        with open(os.path.join(paths.log_dir(), "selftest.log"), "w", encoding="utf-8") as fh:
            fh.write(blob)
    except Exception:
        pass
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    force_pyqt5 = "--pyqt5" in args
    force_qtquick = "--qtquick" in args
    # 默认走 QtQuick

    # 打包自检：不启动 GUI，直接跑完退出（见 _run_selftest docstring）
    if "--selftest" in args:
        sys.exit(_run_selftest())

    _set_qt_attributes()              # 必须在 QApplication 之前
    app = QApplication(sys.argv)
    _setup_qt_app(app)
    _safe_migrate_api_keys()

    if force_pyqt5:
        run_pyqt5(app)
    elif force_qtquick:
        run_qtquick(app)
    else:
        # 默认 QtQuick；加载失败则 fallback
        ok = run_qtquick(app)
        if not ok:
            run_pyqt5(app)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
