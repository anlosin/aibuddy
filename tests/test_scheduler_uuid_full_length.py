"""Day 19.1.1 P3-low: scheduler auto id 是 12-char hex 截断，应改完整 UUID。"""
import os
import sys
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestSchedulerUuidFullLength(unittest.TestCase):
    def test_scheduler_uses_full_uuid(self):
        from qwen_app import scheduler
        with open(scheduler.__file__, encoding="utf-8") as f:
            src = f.read()
        bad = re.findall(
            r'uuid\.uuid4\(\)\.hex\[:\d+\]|uuid\.uuid4\(\)\)\[:\d+\]',
            src,
        )
        self.assertEqual(
            bad, [],
            f"scheduler.py 含 UUID 截断，应改为完整 UUID：{bad}",
        )

    def test_scheduler_add_automation_returns_full_uuid(self):
        """运行期验证：add_automation 返回完整 UUID。"""
        from PyQt5.QtWidgets import QApplication
        from qwen_app.scheduler import Scheduler
        app = QApplication.instance() or QApplication(sys.argv)
        s = Scheduler()
        a = s.add_automation("test", "do thing", "every 1h")
        try:
            self.assertEqual(len(a["id"]), 36,
                             f"应为 36 字符，实际 {len(a['id'])}: {a['id']!r}")
            import uuid as _uuid
            _uuid.UUID(a["id"])  # 合法 UUID 校验
        finally:
            # 清理：移除测试数据
            if a in s.automations:
                s.automations.remove(a)
                s._save()


if __name__ == "__main__":
    unittest.main()
