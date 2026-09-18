"""Day 20.6.15: 专家功能在 QtQuick 主界面的回归护栏

背景（用户报「专家好像没有了」）：
- 专家机制一直存在（experts/*.json + expert_router + bridge.list_experts /
  set_expert，Day 19 就建好了 QML 专用 slot），但 Main.qml 从未接过 ——
  专家选择器只存在于 PyQt5 备用窗口 chat_window。
- 同时 set_expert / 前缀路由只改 in-memory，不持久化、不发信号（三缺二）。

修复后必须钉死的行为：
1. chat_bridge 有 expertChanged(str) 信号，且参数 ≤2（QTBUG-94360 硬约束）
2. set_expert = in-memory + 持久化(cfg["current_expert"]) + emit 三件套
3. ChatBridge 初始化从 cfg 读 current_expert 并校验存在性
4. _bridge_send 两处前缀路由都走 _apply_expert（不许再直改 in-memory）
5. Main.qml 真的接了 list_experts / set_expert / onExpertChanged
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication
from qwen_app import config as _config
from qwen_app.chat_bridge import ChatBridge

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _SaveRecorder:
    """拦截 config.save_config，记录写入但不碰真实配置文件。"""

    def __init__(self):
        self.saved = []

    def __call__(self, cfg):
        self.saved.append(dict(cfg))


class ExpertBridgeTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def setUp(self):
        self._real_load = _config.load_config
        self._real_save = _config.save_config
        self.recorder = _SaveRecorder()
        _config.save_config = self.recorder

    def tearDown(self):
        _config.load_config = self._real_load
        _config.save_config = self._real_save


class TestExpertSignalContract(unittest.TestCase):
    """信号契约：存在 + 参数个数受 QTBUG-94360 约束。"""

    def test_bridge_has_expert_changed_signal(self):
        self.assertTrue(hasattr(ChatBridge, 'expertChanged'),
                        'Day 20.6.15: ChatBridge 必须声明 expertChanged 信号')

    def test_expert_changed_signal_has_at_most_2_args(self):
        """QTBUG-94360：QML Connections 连 ≥3 参信号会随机原生崩溃。"""
        with open(os.path.join(_ROOT, 'qwen_app', 'chat_bridge.py'),
                  encoding='utf-8') as fh:
            src = fh.read()
        m = re.search(r'expertChanged\s*=\s*pyqtSignal\(([^)]*)\)', src)
        self.assertIsNotNone(m, 'expertChanged 信号声明没找到')
        n = len([a for a in m.group(1).split(',') if a.strip()])
        self.assertLessEqual(n, 2, 'expertChanged 参数必须 ≤2（多字段用 QVariantMap）')


class TestSetExpertThreePiece(ExpertBridgeTestBase):
    """set_expert 三件套：in-memory + 持久化 + emit。"""

    def test_set_expert_persists_and_emits(self):
        b = ChatBridge(theme="light")
        events = []
        b.expertChanged.connect(lambda eid: events.append(eid))
        experts = b._load_experts()
        target = next(e for e in experts if e != 'general')
        self.assertTrue(b.set_expert(target))
        self.assertEqual(b._current_expert_id, target)
        self.assertEqual(events, [target])
        self.assertTrue(self.recorder.saved, '切换专家必须持久化到 cfg')
        self.assertEqual(self.recorder.saved[-1].get('current_expert'), target)

    def test_set_expert_unknown_returns_false_no_emit_no_save(self):
        b = ChatBridge(theme="light")
        events = []
        b.expertChanged.connect(lambda eid: events.append(eid))
        before = b._current_expert_id
        self.assertFalse(b.set_expert('no_such_expert'))
        self.assertEqual(b._current_expert_id, before)
        self.assertEqual(events, [])
        self.assertEqual(self.recorder.saved, [])

    def test_init_loads_persisted_expert(self):
        """cfg 里存过 current_expert → 启动后即为当前专家。"""
        def fake_load():
            cfg = self._real_load()
            cfg['current_expert'] = 'analyst'
            return cfg
        _config.load_config = fake_load
        try:
            b = ChatBridge(theme="light")
            self.assertEqual(b._current_expert_id, 'analyst')
        finally:
            _config.load_config = self._real_load

    def test_init_ignores_unknown_persisted_expert(self):
        """残留的失效 id 必须回退 general，不能让下拉框空转。"""
        def fake_load():
            cfg = self._real_load()
            cfg['current_expert'] = 'deleted_long_ago'
            return cfg
        _config.load_config = fake_load
        try:
            b = ChatBridge(theme="light")
            self.assertEqual(b._current_expert_id, 'general')
        finally:
            _config.load_config = self._real_load


class TestPrefixRoutingUsesUnifiedEntry(unittest.TestCase):
    """/dev 前缀路由不许再直改 in-memory（必须走 _apply_expert）。"""

    def test_no_direct_inmemory_assignment_left(self):
        with open(os.path.join(_ROOT, 'qwen_app', '_bridge_send.py'),
                  encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('self._current_expert_id = matched_id', src,
                         '前缀路由直改 in-memory 是三缺二的老 bug，必须走 _apply_expert')
        self.assertEqual(src.count('self._apply_expert(matched_id)'), 2,
                         'send_message 与 resend 两处路由都要走统一入口')

    def test_bridge_has_apply_expert_helper(self):
        self.assertTrue(hasattr(ChatBridge, '_apply_expert'))


class TestQmlWiring(unittest.TestCase):
    """Main.qml 必须真的接上专家功能（这是本次「专家没有了」的直接根因）。"""

    def setUp(self):
        with open(os.path.join(_ROOT, 'qwen_app', 'qml', 'Main.qml'),
                  encoding='utf-8') as fh:
            self.qml = fh.read()

    def test_qml_lists_experts(self):
        self.assertIn('bridge.list_experts()', self.qml,
                      '专家下拉框数据源缺失')

    def test_qml_calls_set_expert(self):
        self.assertIn('bridge.set_expert(', self.qml,
                      '下拉框切换必须调用 bridge.set_expert')

    def test_qml_follows_expert_changed(self):
        self.assertRegex(self.qml, r'function\s+onExpertChanged\s*\(',
                         '缺 onExpertChanged：/dev 前缀切换后下拉框不会跟随')


if __name__ == '__main__':
    unittest.main()
