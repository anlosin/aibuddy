"""ChatBridge 功能切片 —— MenuMixin（PyQt5 对话框 / 自动化菜单入口）。

拆分约定（施工图 §2.3 硬规则）：
- mixin 继承普通 ``object``，不写 ``__init__``，不继承 QObject；
- 信号 / pyqtProperty / ``__init__`` / 类常量全部留在 ``chat_bridge.py`` 主类体；
- 本模块**不准** import ``chat_bridge``（防成环）；
- 方法体从 chat_bridge.py 逐字搬入（含装饰器与注释），逻辑零改动。
"""
from PyQt5.QtCore import pyqtSlot

from .settings_dialog import show_settings, show_model_manager, show_plugin_manager  # Day 20.1: QtQuick 调 PyQt5 对话框
from ._dialog_host import _DialogHost  # Day 20.2: PyQt5 对话框需要 QWidget + 状态，bridge 是 QObject，用 host 适配


class MenuMixin(object):
    """设置 / 模型 / 插件 / 自动化管理对话框入口 + _DialogHost 懒构造。"""

    # ============ Day 20.1: 设置/模型/自动化菜单入口 ============
    # QtQuick 路径下没有 PyQt5 的菜单栏，Day 20 工程师漏补「设置/模型/自动化」
    # 三组菜单。PyQt5 对话框（settings_dialog / automation_dialogs）在
    # 已有 QApplication 进程里也能弹，所以 QML 调这些 slot 时直接 spawn
    # PyQt5 子窗口 —— 视觉风格略不一致但功能完整。
    #
    # Day 20.2: PyQt5 对话框的 show_* 函数是为 ChatWindow(QWidget) 写的，要
    # 求 parent 是 QWidget 且 host 暴露十几个公开字段/方法。ChatBridge 是
    # QObject —— 硬传 self 会 QDialog(ChatBridge) TypeError。用 _DialogHost
    # 包一层，host 缓存到 self._dialog_host 上重复复用（多组对话框共享）。
    @pyqtSlot()
    def show_settings_dialog(self):
        """Day 20.1: QtQuick 菜单栏「设置 > 模型设置」入口"""
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            show_settings(host)
        except Exception as e:
            print(f"[chat_bridge] show_settings 失败: {e}")
            self.toast.emit(f"打开设置失败: {e}")

    @pyqtSlot()
    def show_plugin_manager_dialog(self):
        """Day 20.1: QtQuick 菜单栏「设置 > 插件管理」入口"""
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            show_plugin_manager(host)
        except Exception as e:
            print(f"[chat_bridge] show_plugin_manager 失败: {e}")
            self.toast.emit(f"打开插件管理失败: {e}")

    @pyqtSlot()
    def show_model_manager_dialog(self):
        """Day 20.1: QtQuick 菜单栏「模型 > 模型管理」入口"""
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            show_model_manager(host)
        except Exception as e:
            print(f"[chat_bridge] show_model_manager 失败: {e}")
            self.toast.emit(f"打开模型管理失败: {e}")

    def _get_dialog_host(self):
        """获取（或懒创建）_DialogHost。host 是 QWidget，对话框可当 parent；
        对话框访问的字段 / 方法都在 host 上代理到 bridge 或 cfg。"""
        from PyQt5.QtWidgets import QApplication
        if QApplication.instance() is None:
            return None
        host = getattr(self, "_dialog_host", None)
        if host is None:
            host = _DialogHost(self)
            self._dialog_host = host
        return host

    @pyqtSlot()
    def show_automation_manager_dialog(self):
        """Day 20.1/20.3: QtQuick 菜单栏「工具 > 任务管理」入口

        Day 20.1 时 automation_dialogs.py 是空模块，slot 只能 toast 占位。
        Day 20.3: automation_dialogs 补了 ``show_automation_manager(host)`` 工厂
        （跟 settings_dialog.show_* 同一套模式），host 走 _DialogHost，会按
        standalone 模式懒构造 Scheduler（与 chat_window 的 chat_scheduler 行为
        对齐：30s 定时 + 立即 check_due）。
        Day 20.4 强化：scheduler 懒构造可能因 make_openai_client / discover_plugins
        / Scheduler 构造失败走退化路径；dialog 内部所有调用都包了 try；
        本 slot 仅在 import / dialog 构造阶段抛异常时才会被 except 兜底。
        """
        try:
            host = self._get_dialog_host()
            if host is None:
                self.toast.emit("QApplication 未就绪")
                return
            from .automation_dialogs import show_automation_manager
            show_automation_manager(host)
            # 成功提示（不阻塞 exec_，但用户能看见）
            # 注意：show_automation_manager 阻塞到 dialog 关闭才返回，
            # 这条 toast 在 dialog 关掉后才发
            n = len(getattr(host.scheduler, "automations", []) or [])
            self.toast.emit(f"任务管理已关闭（{n} 个任务）")
        except Exception as e:
            print(f"[chat_bridge] show_automation_manager 失败: {e}")
            import traceback
            traceback.print_exc()
            self.toast.emit(f"打开自动化管理失败: {e}")

    @pyqtSlot()
    def run_automation_check_due(self):
        """Day 20.1/20.3: 菜单栏「工具 > 立即检查」入口

        复用 _DialogHost.scheduler 懒构造（与 show_automation_manager_dialog
        共享同一个 Scheduler 实例）。如果用户在打开「任务管理」之前就点了
        「立即检查」，host.scheduler 会在这里第一次触发懒构造。
        """
        try:
            host = self._get_dialog_host()
            if host is None or host.scheduler is None:
                self.toast.emit("调度器未启动")
                return
            host.scheduler.check_due()
            self.toast.emit("已触发到期任务检查")
        except Exception as e:
            print(f"[chat_bridge] run_automation_check_due 失败: {e}")
            self.toast.emit(f"检查任务失败: {e}")
