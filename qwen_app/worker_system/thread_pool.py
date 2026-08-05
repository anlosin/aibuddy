"""
线程池模块 — 基于 QThreadPool 的线程安全任务调度
"""
import weakref
from PyQt5.QtCore import QThreadPool, QMutex, QMutexLocker
from PyQt5.QtWidgets import QApplication


class WorkerPool:
    """线程池单例 — 管理所有后台 Worker 任务的生命周期"""

    _instance = None
    _lock = QMutex()

    def __new__(cls):
        with QMutexLocker(cls._lock):
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_pool()
        return cls._instance

    def _init_pool(self):
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(3)          # 最大并发 3
        self.pool.setExpiryTimeout(15000)        # 空闲线程 15 秒后回收
        self.active_tasks = weakref.WeakSet()    # 活跃任务集合（弱引用，不阻止 GC）

    def submit(self, task):
        """提交一个 Worker 任务到线程池"""
        with QMutexLocker(self._lock):
            self.pool.start(task)
            self.active_tasks.add(task)
            QApplication.processEvents()  # 确保 UI 在提交后及时刷新

    def active_count(self):
        """当前活跃任务数"""
        return len(self.active_tasks)

    def wait_for_done(self, msecs=30000):
        """等待所有任务完成（最多等待 msecs 毫秒）"""
        self.pool.waitForDone(msecs)