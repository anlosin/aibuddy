"""回归测试：Scheduler.add_automation 必须支持 workspace 参数。

背景：automation_dialogs.py 的 _new / _edit_selected 都以 workspace=... 调用
add_automation，但原签名缺该参数 → 真调度器下点「新建任务」抛
TypeError: add_automation() got an unexpected keyword argument 'workspace'。
（_DummyScheduler.add_automation(**kw) 在测试环境把它吞掉，导致此前未覆盖。）

另外 scheduler._run_one 读 auto.get("workspace") 决定 isolated/shared 工作目录，
因此 add_automation 构造的 dict 必须写入该 key（缺省归一为 "isolated"）。
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestAddAutomationWorkspace(unittest.TestCase):
    """add_automation 的 workspace 参数：透传 / 缺省归一。"""

    def setUp(self):
        # 把 automations.json 重定向到临时目录，避免污染真实 data/
        import qwen_app.scheduler as scheduler_mod
        self._scheduler_mod = scheduler_mod
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_automations_file = scheduler_mod.AUTOMATIONS_FILE
        scheduler_mod.AUTOMATIONS_FILE = os.path.join(self._tmpdir.name,
                                                      "automations.json")
        from qwen_app.scheduler import Scheduler
        # 不传 parent（独立模式），client=None 不会在 add_automation 路径被用到
        self.sched = Scheduler(client=None, model_id="fake")

    def tearDown(self):
        self._scheduler_mod.AUTOMATIONS_FILE = self._old_automations_file
        self._tmpdir.cleanup()

    def test_workspace_shared_passthrough(self):
        auto = self.sched.add_automation(
            "任务A", "hello", {"type": "interval", "unit": "minutes", "every": 30},
            workspace="shared")
        self.assertEqual(auto["workspace"], "shared")

    def test_workspace_default_isolated(self):
        auto = self.sched.add_automation(
            "任务B", "hello", {"type": "interval", "unit": "minutes", "every": 30})
        self.assertEqual(auto["workspace"], "isolated")

    def test_workspace_empty_normalized_to_isolated(self):
        auto = self.sched.add_automation(
            "任务C", "hello", {"type": "interval", "unit": "minutes", "every": 30},
            workspace="")
        self.assertEqual(auto["workspace"], "isolated")


if __name__ == "__main__":
    unittest.main()
