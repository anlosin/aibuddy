"""
线程安全测试
"""
import os
import sys
import unittest
from unittest.mock import MagicMock
from PyQt5.QtCore import QCoreApplication

# 确保项目根目录在 sys.path 中，使 qwen_app 包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qwen_app.worker_system import WorkerPool, AsyncWorker


class ThreadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication([])

    def test_concurrent_tasks(self):
        """测试线程池并发限制"""
        pool = WorkerPool()
        mock_client = MagicMock()
        
        # 提交 5 个任务（应受限于最大 3 线程）
        workers = []
        for i in range(5):
            worker = AsyncWorker(
                client=mock_client,
                model_id=f"test-model-{i}",
                messages=[{"role": "user", "content": f"test-{i}"}]
            )
            pool.submit(worker)
            workers.append(worker)
        
        # 验证活跃线程数不超过 3
        self.assertLessEqual(pool.active_count(), 3)

    def test_task_stop(self):
        """测试任务中断"""
        worker = AsyncWorker(
            client=MagicMock(),
            model_id="test",
            messages=[]
        )
        worker.stop()
        
        # 验证停止信号已设置
        self.assertTrue(worker._stop_event.is_set())


if __name__ == "__main__":
    unittest.main()