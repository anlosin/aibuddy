"""ChatBridge 功能切片 —— PluginMixin（插件管理）。

拆分约定（施工图 §2.3 硬规则）：
- mixin 继承普通 ``object``，不写 ``__init__``，不继承 QObject；
- 信号 / pyqtProperty / ``__init__`` / 类常量全部留在 ``chat_bridge.py`` 主类体；
- 本模块**不准** import ``chat_bridge``（防成环）；
- 方法体从 chat_bridge.py 逐字搬入（含装饰器与注释），逻辑零改动。
"""
import os

from PyQt5.QtCore import QTimer, QFileSystemWatcher, pyqtSlot


class PluginMixin(object):
    """插件相关方法：watcher 生命周期 + 发现 / 重载 / 已启用名单。"""

    def _init_plugin_watcher(self):
        """Day 14: 监听 plugins 目录变化。

        Day 20.6.16 (P0-AUD2-7)：必须监视 ``external_plugins_dir()``（用户能改的那份），
        而不是模块级常量 ``PLUGINS_DIR`` —— 后者在 plugin_manager 导入时一次性
        计算成 ``builtin`` 或 ``external`` 中之一。打包态首启动外部 plugins 不存在
        时 PLUGINS_DIR == builtin；随后 ensure_external_plugins() 把模板复制到
        exe 同级，但 watcher 已冻在 builtin → 用户改 exe 同级插件收不到信号，
        热重载失效。external_plugins_dir() 是**函数**，永远指向用户能改的目录。
        """
        try:
            from . import paths
            ext = paths.external_plugins_dir()
            if not os.path.isdir(ext):
                return
            self._plugin_watcher = QFileSystemWatcher([ext])
            self._plugin_watcher.directoryChanged.connect(self._on_plugin_dir_changed)
        except Exception as e:
            print(f"[chat_bridge] 初始化 plugin watcher 失败: {e}")

    @pyqtSlot()
    def enable_plugin_watcher(self):
        """Day 14: main.py QML Component.onCompleted 调这个启用 watcher

        单元测试不需要 watcher（在 __init__ 跑 reload_modules 慢且影响测试），
        推迟到 Qt 事件循环就绪后再启用。
        """
        if self._plugin_watcher is None:
            self._init_plugin_watcher()

    def _on_plugin_dir_changed(self, path):
        """Day 14: 插件目录变化时触发热重载（防抖 300ms）"""
        # Day 15: 复用 timer（避免频繁创建）
        if not hasattr(self, "_plugin_reload_timer") or self._plugin_reload_timer is None:
            self._plugin_reload_timer = QTimer()
            self._plugin_reload_timer.setSingleShot(True)
            self._plugin_reload_timer.timeout.connect(self.reload_plugins)
        else:
            self._plugin_reload_timer.stop()
        self._plugin_reload_timer.start(300)

    # ============ 内部辅助 ============
    def _discover_plugins(self) -> dict:
        """Day 13: 扫描 plugins 目录（plugin_manager.discover_plugins）"""
        try:
            from . import plugin_manager
            plugins, _ = plugin_manager.discover_plugins()
            return plugins
        except Exception as e:
            print(f"[chat_bridge] discover_plugins 失败: {e}")
            return {}

    @pyqtSlot()
    def reload_plugins(self):
        """Day 14: 强制重载 plugins 目录（reload_modules=True 清 sys.modules 缓存）

        Day 18 (H1 修复)：如果当前有 worker 在跑（_worker_count > 0），
        直接 reload 会让 worker 持有的 plugin module 对象引用 unbound，
        工具调用会抛 AttributeError（半写半未写风险）。推迟到所有 worker 退出后
        再 reload（最多等 5 秒，超时仍尝试但 emit 失败信号）。
        Day 18 (M7)：失败时通过 pluginReloadFailed 信号把错误传回 QML，
        不再仅 print 到 stderr（GUI 之前看不到）。
        """
        with self._worker_count_lock:
            if self._worker_count > 0:
                self._reload_pending = True
                self._reload_attempts = getattr(self, "_reload_attempts", 0) + 1
                if self._reload_attempts <= 50:        # 50 × 100ms = 5s 超时
                    QTimer.singleShot(100, self.reload_plugins)
                    return
                # 超时：强制尝试 + emit 失败警告
                self._reload_pending = False
                self.pluginReloadFailed.emit("worker 在 5 秒内未退出，强制重载可能半写")
        self._reload_attempts = 0
        self._do_reload_plugins()

    def _do_reload_plugins(self):
        """实际执行 reload（被 reload_plugins / 延迟重试调用）"""
        try:
            from . import plugin_manager
            plugins, _ = plugin_manager.discover_plugins(reload_modules=True)
            self.pluginReloaded.emit(len(plugins))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"[chat_bridge] 插件热更新失败: {err}")
            self.pluginReloadFailed.emit(err)

    def _enabled_plugin_names(self) -> list:
        """Day 13: 列出已启用插件名（带 TOOLS 字段的）

        Day 19 (C-NEW-2 修复)：从 cfg 持久化的 enabled_plugins 字段读，
        与 chat_window._load_plugins 一致 —— 之前是「所有 TOOLS 插件视为启用」，
        QtQuick 路径下用户在「插件管理」里禁用某个插件完全无效。
        """
        try:
            from . import config as _cfg
            from . import plugin_manager
            # 读持久化的启用列表（load_plugin_state 已在 user 排除不存在的）
            saved = _cfg.load_plugin_state()
            plugins, _ = plugin_manager.discover_plugins()
            # 取交集：磁盘上存在 + 标记启用 + 实际有 TOOLS
            return [n for n in saved
                    if n in plugins and hasattr(plugins[n], "TOOLS") and plugins[n].TOOLS]
        except Exception:
            return []
