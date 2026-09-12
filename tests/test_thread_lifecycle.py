"""Day 18 线程生命周期修复回归测试 — H1 (reload 并发) + M7 (失败信号) + H6 (stop 不阻塞)。"""
import os
import sys
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _pump(ms=200):
    """跑 Qt 事件循环 ms 毫秒"""
    from PyQt5.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


class TestReloadPluginsConcurrency(unittest.TestCase):
    """H1: reload_plugins 必须等所有 worker 跑完再清 sys.modules。"""

    def test_reload_when_busy_is_deferred(self):
        """模拟：worker 在跑时调 reload_plugins，应延迟到 worker finished 后"""
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge

        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")

        # 模拟有 worker 在跑（给 _worker 一个有 deleteLater 的 fake，避免测试在
        # _on_worker_finished 里炸；这里只验证 reload 的推迟逻辑）
        class _StubWorker:
            def deleteLater(self):
                pass
        b._worker = _StubWorker()
        b._worker_count = 1
        reload_events = []
        b.pluginReloaded.connect(lambda n: reload_events.append(n))
        failed_events = []
        b.pluginReloadFailed.connect(lambda e: failed_events.append(e))

        # 此时 reload 应被推迟
        b.reload_plugins()
        self.assertTrue(b._reload_pending, "worker 在跑时 reload 必须置 pending")
        # 不应立即 emit pluginReloaded
        _pump(50)
        self.assertEqual(reload_events, [], "worker 未退出前不应 emit pluginReloaded")

        # 模拟 worker 退出（直接调用 _on_worker_finished，它会检测 _worker_count=0 并重试 reload）
        b._worker_count = 0
        b._on_worker_finished()
        # reload 应在 QTimer.singleShot(0,...) 内执行
        _pump(100)
        self.assertTrue(reload_events, "worker 退出后应自动触发 reload")
        self.assertFalse(b._reload_pending)

    def test_reload_idle_executes_immediately(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge

        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")

        reload_events = []
        b.pluginReloaded.connect(lambda n: reload_events.append(n))

        b.reload_plugins()
        _pump(100)
        self.assertEqual(len(reload_events), 1)
        self.assertEqual(reload_events[0], len(b._discover_plugins()))

    def test_reload_failure_emits_signal(self):
        """M7: reload 失败必须通过 pluginReloadFailed 信号传回，不能仅 print"""
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge

        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        b._worker_count = 0     # 直接重载路径

        # 让 plugin_manager.discover_plugins 抛错
        from qwen_app import plugin_manager
        orig = plugin_manager.discover_plugins

        def boom(*a, **kw):
            raise RuntimeError("simulated")

        plugin_manager.discover_plugins = boom
        try:
            failed_events = []
            b.pluginReloadFailed.connect(lambda e: failed_events.append(e))
            b.reload_plugins()
            _pump(50)
            self.assertEqual(len(failed_events), 1)
            self.assertIn("simulated", failed_events[0])
        finally:
            plugin_manager.discover_plugins = orig

    def test_reload_timeout_force_fires_failure_signal(self):
        """H1 超时：worker 在 5s 内未退出 → emit pluginReloadFailed 警告"""
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge

        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        b._worker = object()
        b._worker_count = 1
        b._reload_attempts = 50      # 已用满 50 次，下一次直接超时

        failed_events = []
        b.pluginReloadFailed.connect(lambda e: failed_events.append(e))
        b.reload_plugins()
        _pump(50)
        self.assertTrue(failed_events)
        self.assertIn("5 秒", failed_events[0])


class TestStopWorkerNotBlocking(unittest.TestCase):
    """H6: on_stop_response 不应阻塞 GUI 主线程。"""

    def test_stop_returns_quickly_without_wait(self):
        """_stop_worker_thread 只发信号，不 wait — 调用应该立即返回"""
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_window import ChatWindow

        app = QApplication.instance() or QApplication(sys.argv)
        # 模拟有 worker 在跑
        class FakeWorker:
            def stop(self):
                pass
        w = ChatWindow.__new__(ChatWindow)
        w.worker_thread = FakeWorker()

        t0 = time.perf_counter()
        w._stop_worker_thread(timeout_ms=3000)        # timeout 参数不再使用
        elapsed = time.perf_counter() - t0
        # 修复前：wait(3000) 会阻塞最长 3 秒；修复后应 < 50ms
        self.assertLess(elapsed, 0.05,
                         f"stop 卡了 {elapsed*1000:.0f}ms —— 还在 wait")
        self.assertIsNone(w.worker_thread)


class TestWorkerThreadHasDeleteLater(unittest.TestCase):
    """H2: WorkerThread 在两条 UI 路径上都必须 deleteLater。"""

    def _patch_worker_thread(self):
        from qwen_app import chat_bridge
        orig = chat_bridge.WorkerThread
        captured = {}

        class FakeWT:
            def __init__(self, *a, **kw):
                self._slots = {}
                self.deleted = False
            def start(self):
                pass
            def __getattr__(self, name):
                # 任何信号访问都返回一个伪 connect 对象
                if name.startswith("_"):
                    raise AttributeError(name)
                return _FakeSignal(self, name)
            def deleteLater(self):
                self.deleted = True

        class _FakeSignal:
            def __init__(self, wt, name):
                self._wt = wt
                self._name = name
            def connect(self, slot):
                self._wt._slots.setdefault(self._name, []).append(slot)

        chat_bridge.WorkerThread = FakeWT
        return orig, chat_bridge

    def _trigger_finished(self, wt):
        for s in wt._slots.get("finished", []):
            s()

    def test_chat_bridge_connects_deleteLater(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge

        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        b._worker_count = 0
        orig, mod = self._patch_worker_thread()
        try:
            b.start_real_chat("hello", use_fake=True)
            wt = b._worker
            self.assertIsInstance(wt, mod.WorkerThread)
            self._trigger_finished(wt)
            self.assertTrue(wt.deleted, "WorkerThread.finished 后必须 deleteLater")
        finally:
            mod.WorkerThread = orig

    def test_chat_window_connects_deleteLater(self):
        """PyQt5 fallback 路径 (chat_window.py) 也必须 deleteLater（H2 修复点）"""
        import inspect
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_window import ChatWindow

        app = QApplication.instance() or QApplication(sys.argv)
        src = inspect.getsource(ChatWindow)
        # ChatWindow.on_send_message 里有 self.worker_thread.finished.connect(lambda wt=...)
        # 确保 lambda 包含 deleteLater 调用
        self.assertIn("deleteLater", src)
        # 确保 finished 信号被 connect 了 deleteLater（不能仅 connect 到 _on_worker_finished）
        # 简化：通过源码确认 deleteLater 在 WorkerThread 的连接列表里
        self.assertIn("finished.connect(lambda wt", src.replace("\n", " "),
                      "PyQt5 路径必须在 worker finished 时 deleteLater")


if __name__ == "__main__":
    unittest.main()
