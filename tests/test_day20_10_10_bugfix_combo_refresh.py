# -*- coding: utf-8 -*-
"""Day 20.10.10 B1: 「添加新模型后主界面模型下拉不刷新」护栏。

复现路径：
1. 菜单 → 模型管理 → 添加模型（填 name / base_url / api_key / model_id）→ 保存
2. 关闭对话框 → 切到主界面 → 顶部模型下拉仍只有旧模型

根因：
- ChatBridge 没有 modelsChanged 信号
- _bridge_model.py 只有 list_models / set_current_model，没有添加/编辑/删除 slot
- settings_dialog.on_add / on_edit / on_del / on_use 直接 save_models 写盘，
  没有通知 ChatBridge 触发 QML 端 modelCombo 重拉
- Main.qml modelCombo.model: bridge.list_models() 是无 NOTIFY 绑定，
  bridge 端数据变了 QML 不会重新求值

修复后必须钉死的行为：
1. ChatBridge 上有 modelsChanged = pyqtSignal()
2. signal 参数 ≤2（QTBUG-94360 硬约束 —— 实测取 0 参数就够）
3. _bridge_model.add_model / update_model / delete_model 三个 slot 存在
4. 三个 slot 写盘 + emit modelsChanged；若动了当前激活模型，还 emit
   currentModelNameChanged 并同步 _last_model_name
5. settings_dialog 写盘后通过 host._b 调 bridge.modelsChanged.emit()
6. Main.qml 里 onModelsChanged 真的接上，且会重拉 + 回写 currentIndex
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_PATH = os.path.join(_ROOT, 'qwen_app', 'chat_bridge.py')
BRIDGE_MODEL_PATH = os.path.join(_ROOT, 'qwen_app', '_bridge_model.py')
SETTINGS_PATH = os.path.join(_ROOT, 'qwen_app', 'settings_dialog.py')
MAIN_QML_PATH = os.path.join(_ROOT, 'qwen_app', 'qml', 'Main.qml')


# ────────────── 静态契约 ──────────────

class TestModelsChangedSignalContract(unittest.TestCase):
    """ChatBridge 必须有 modelsChanged 信号（参数 ≤2）。"""

    def test_bridge_declares_modelsChanged(self):
        with open(BRIDGE_PATH, encoding='utf-8') as f:
            src = f.read()
        m = re.search(r'modelsChanged\s*=\s*pyqtSignal\(([^)]*)\)', src)
        self.assertIsNotNone(
            m,
            'ChatBridge 必须声明 modelsChanged = pyqtSignal()（Day 20.10.10 B1）')

    def test_modelsChanged_arity_within_QTBUG94360_limit(self):
        """QML Connections 监听 ≥3 参信号会引发启动期 0xC0000005/2.0"""
        with open(BRIDGE_PATH, encoding='utf-8') as f:
            src = f.read()
        m = re.search(r'modelsChanged\s*=\s*pyqtSignal\(([^)]*)\)', src)
        self.assertIsNotNone(m, 'modelsChanged 信号声明缺失')
        n = len([a for a in m.group(1).split(',') if a.strip()])
        self.assertLessEqual(
            n, 2,
            'modelsChanged 参数必须 ≤2（QTBUG-94360）')


# ────────────── 行为契约 ──────────────

class _SaveRecorder:
    """拦截 config.save_models + config.save_config，记录调用但不碰真实文件。"""

    def __init__(self):
        self.models_saved = []
        self.cfg_saved = []

    def save_models(self, models, current_id):
        self.models_saved.append({'models': list(models), 'current_id': current_id})

    def save_config(self, cfg):
        self.cfg_saved.append(dict(cfg))


class _MixinSetup:
    """setUp 公共：换 config.save_models / save_config 为 recorder；ChatBridge 启动读 cfg
    会调 load_config，保留原 read 行为不变，只劫持 write。"""

    def setUp(self):
        from qwen_app import config as _cfg
        self._real_save_models = _cfg.save_models
        self._real_save_config = _cfg.save_config
        self._real_load_models = _cfg.load_models
        self._real_load_config = _cfg.load_config
        self.recorder = _SaveRecorder()
        _cfg.save_models = self.recorder.save_models
        _cfg.save_config = self.recorder.save_config

        # 准备一份内存 cfg：初始有 1 个模型 + 空 models 列表备份
        self._seed_cfg = {
            'models': [
                {'id': 'm_seed', 'name': 'seed',
                 'base_url': 'https://api.example.com/v1',
                 'api_key': 'sk-seed', 'model_id': 'seed-model',
                 'proxy': ''}
            ],
            'current_model': 'm_seed',
            'enable_thinking': False,
            'enable_tools': True,
        }
        self._load_calls = 0

        def fake_load_models():
            self._load_calls += 1
            return list(self._seed_cfg['models']), self._seed_cfg.get('current_model')

        def fake_load_config():
            self._load_calls += 1
            return dict(self._seed_cfg)

        _cfg.load_models = fake_load_models
        _cfg.load_config = fake_load_config

    def tearDown(self):
        from qwen_app import config as _cfg
        _cfg.save_models = self._real_save_models
        _cfg.save_config = self._real_save_config
        _cfg.load_models = self._real_load_models
        _cfg.load_config = self._real_load_config


class TestAddModelEmitsModelsChanged(_MixinSetup, unittest.TestCase):
    """add_model：写盘 + emit modelsChanged + 设当前激活时 emit currentModelNameChanged"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def test_add_model_returns_true_and_emits(self):
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events_changed = []
        events_name = []
        b.modelsChanged.connect(lambda: events_changed.append(True))
        b.currentModelNameChanged.connect(lambda n: events_name.append(n))
        # as_current=False（不是第一条），但用 False 避免意外改 current_id
        ok = b.add_model(
            'new-model', 'https://api.example2.com/v1',
            'sk-new', 'new-model-id', '', False)
        self.assertTrue(ok, 'add_model 应返回 True')
        self.assertEqual(len(events_changed), 1,
                         'add_model 必须 emit 一次 modelsChanged')
        # current 不变 → 不应触发 currentModelNameChanged
        self.assertEqual(events_name, [],
                         '非第一条 add_model 不应 emit currentModelNameChanged')

    def test_add_model_as_first_emits_name_changed(self):
        """第一条 add_model（自动设为当前）应 emit currentModelNameChanged"""
        # 重置 seed 让 save_models 看到当前是空
        self._seed_cfg = {
            'models': [], 'current_model': None,
            'enable_thinking': False, 'enable_tools': True,
        }
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events_changed = []
        events_name = []
        b.modelsChanged.connect(lambda: events_changed.append(True))
        b.currentModelNameChanged.connect(lambda n: events_name.append(n))
        ok = b.add_model(
            'first-model', 'https://api.example.com/v1',
            'sk-first', 'first-model-id', '', False)
        self.assertTrue(ok)
        self.assertEqual(events_changed, [True],
                         'add_model 必须 emit modelsChanged')
        self.assertEqual(events_name, ['first-model'],
                         '第一条 add_model 自动设当前 → emit currentModelNameChanged')
        # _last_model_name 必须同步
        self.assertEqual(b._last_model_name, 'first-model')

    def test_add_model_with_as_current_true_emits_name(self):
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events_changed = []
        events_name = []
        b.modelsChanged.connect(lambda: events_changed.append(True))
        b.currentModelNameChanged.connect(lambda n: events_name.append(n))
        ok = b.add_model(
            'primary', 'https://api.example.com/v1',
            'sk-p', 'primary-id', '', True)   # as_current=True
        self.assertTrue(ok)
        self.assertEqual(events_changed, [True])
        self.assertEqual(events_name, ['primary'],
                         'as_current=True 必须 emit currentModelNameChanged')

    def test_add_model_invalid_input_rejected(self):
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events = []
        b.modelsChanged.connect(lambda: events.append(True))
        # base_url 空 → 拒绝
        self.assertFalse(b.add_model('x', '', 'sk', 'mid', '', False))
        # model_id 空 → 拒绝
        self.assertFalse(b.add_model('x', 'http://a', 'sk', '', '', False))
        self.assertEqual(events, [],
                         '参数非法时不应 emit modelsChanged')


class TestUpdateModelEmitsModelsChanged(_MixinSetup, unittest.TestCase):
    """update_model：写盘 + emit modelsChanged；改当前激活时 emit currentModelNameChanged"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def test_update_model_emits_modelsChanged(self):
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events_changed = []
        events_name = []
        b.modelsChanged.connect(lambda: events_changed.append(True))
        b.currentModelNameChanged.connect(lambda n: events_name.append(n))
        # 编辑非当前激活（seed 是当前激活，添加另一个再编辑它）
        # 先添加第二个
        b.add_model('second', 'http://b.com/v1', 'sk2', 'mid2', '', False)
        events_changed.clear()
        ok = b.update_model(
            list(self._seed_cfg['models'])[0]['id'],  # 不存在 id 试试
            'x', 'http://y', '', 'y', '')
        # 实际上 seed 是当前激活 → 编辑它会触发 currentModelNameChanged
        self.assertTrue(ok)
        self.assertEqual(events_changed, [True])
        # seed 编辑了 → currentModelNameChanged 应 emit 一次
        self.assertEqual(len(events_name), 1,
                         '编辑当前激活模型必须 emit currentModelNameChanged')

    def test_update_model_unknown_id_rejected(self):
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events = []
        b.modelsChanged.connect(lambda: events.append(True))
        ok = b.update_model('no_such_id', 'x', 'http://a', 'sk', 'a', '')
        self.assertFalse(ok)
        self.assertEqual(events, [],
                         '未知 id 不应 emit modelsChanged')


class TestDeleteModelEmitsModelsChanged(_MixinSetup, unittest.TestCase):
    """delete_model：写盘 + emit modelsChanged；删当前时回退到剩余第一个 + emit name"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def test_delete_model_emits_modelsChanged(self):
        # 准备两个模型（seed + 新增）
        self._seed_cfg = {
            'models': [
                {'id': 'm_a', 'name': 'A',
                 'base_url': 'http://a', 'api_key': 'sk-a',
                 'model_id': 'mid-a', 'proxy': ''},
                {'id': 'm_b', 'name': 'B',
                 'base_url': 'http://b', 'api_key': 'sk-b',
                 'model_id': 'mid-b', 'proxy': ''},
            ],
            'current_model': 'm_a',
        }
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        # 触发一次 init 让 _last_model_name 同步
        events_changed = []
        events_name = []
        b.modelsChanged.connect(lambda: events_changed.append(True))
        b.currentModelNameChanged.connect(lambda n: events_name.append(n))
        # 删 m_b（非当前）→ 不应触发 currentModelNameChanged
        ok = b.delete_model('m_b')
        self.assertTrue(ok)
        self.assertEqual(events_changed, [True])
        self.assertEqual(events_name, [])

    def test_delete_current_falls_back_and_emits_name(self):
        self._seed_cfg = {
            'models': [
                {'id': 'm_a', 'name': 'A',
                 'base_url': 'http://a', 'api_key': 'sk-a',
                 'model_id': 'mid-a', 'proxy': ''},
                {'id': 'm_b', 'name': 'B',
                 'base_url': 'http://b', 'api_key': 'sk-b',
                 'model_id': 'mid-b', 'proxy': ''},
            ],
            'current_model': 'm_a',
        }
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events_changed = []
        events_name = []
        b.modelsChanged.connect(lambda: events_changed.append(True))
        b.currentModelNameChanged.connect(lambda n: events_name.append(n))
        # 删当前 m_a → 回退到 m_b
        ok = b.delete_model('m_a')
        self.assertTrue(ok)
        self.assertEqual(events_changed, [True])
        self.assertEqual(events_name, ['B'],
                         '删当前激活模型应回退到剩余第一个并 emit name')
        self.assertEqual(b._last_model_name, 'B')

    def test_delete_last_model_blocked(self):
        """只剩 1 个模型时不允许删（与 settings_dialog.on_del 一致）"""
        # seed 只有 1 个模型
        from qwen_app.chat_bridge import ChatBridge
        b = ChatBridge(theme='light')
        events = []
        b.modelsChanged.connect(lambda: events.append(True))
        ok = b.delete_model('m_seed')
        self.assertFalse(ok)
        self.assertEqual(events, [],
                         '删最后一个模型应被拒绝，不 emit modelsChanged')


# ────────────── QML 端契约 ──────────────

class TestMainQmlModelsChangedHandler(unittest.TestCase):
    """Main.qml 必须真的接上 onModelsChanged，并重拉 + 回写 currentIndex。"""

    def setUp(self):
        with open(MAIN_QML_PATH, encoding='utf-8') as f:
            self.qml = f.read()

    def test_qml_has_onModelsChanged_handler(self):
        self.assertRegex(
            self.qml,
            r'function\s+onModelsChanged\s*\(',
            'Main.qml 必须接 onModelsChanged handler')

    def test_qml_handler_reloads_model_and_currentIndex(self):
        """onModelsChanged 必须重拉 list_models() 并显式回写 currentIndex
        （与 onExpertChanged 同模板：只重拉 model 不够，currentIndex 不会自动跟随）"""
        m = re.search(
            r'function\s+onModelsChanged\s*\([^)]*\)\s*\{(.*?)^\s*\}',
            self.qml, re.DOTALL | re.MULTILINE)
        self.assertIsNotNone(m, '找不到 onModelsChanged 函数体')
        body = m.group(1)
        self.assertIn('modelCombo.model =', body,
                      'onModelsChanged 必须给 modelCombo.model 重新赋值')
        self.assertIn('modelCombo.currentIndex =', body,
                      'onModelsChanged 必须显式回写 modelCombo.currentIndex')
        self.assertIn('bridge.list_models()', body,
                      'onModelsChanged 必须从 bridge 重拉 list_models')


# ────────────── settings_dialog.py PyQt5 路径同步通知 ──────────────

class TestSettingsDialogNotifiesBridge(unittest.TestCase):
    """settings_dialog.show_model_manager 写盘后必须通知 bridge.modelsChanged
    （PyQt5 备用窗口路径也修这一刀；QtQuick 路径直接调 bridge.add_model 等 slot
    已经 emit，本测试只覆盖 PyQt5 路径通过 host._b emit 的分支）。"""

    def test_settings_dialog_calls_notify(self):
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            src = f.read()
        self.assertIn(
            '_notify_models_changed', src,
            'settings_dialog.show_model_manager 必须有 _notify_models_changed 工具函数')
        # 4 个写盘点（on_add / on_edit / on_del / on_use）必须都调它
        # 找 4 个 handler 内 save_models 调用，统计每个附近是否带 _notify_models_changed()
        for handler in ('on_add', 'on_edit', 'on_del', 'on_use'):
            # 找 handler 函数体
            m = re.search(
                rf'def\s+{handler}\s*\([^)]*\)\s*:\s*\n((?:.*\n)*?)(?=\n\s*def\s|\n\s*$)',
                src)
            self.assertIsNotNone(m, f'找不到 {handler} 函数体')
            body = m.group(1)
            self.assertIn(
                '_notify_models_changed', body,
                f'{handler} 必须调 _notify_models_changed（QtQuick 路径同步通知）')

    def test_notify_helper_targets_bridge_modelsChanged(self):
        """_notify_models_changed 必须触发 bridge.modelsChanged.emit()"""
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            src = f.read()
        m = re.search(
            r'def\s+_notify_models_changed\s*\([^)]*\)\s*:\s*\n((?:.*\n)*?)(?=\n\s*def\s|\n\s*$)',
            src)
        self.assertIsNotNone(m, '找不到 _notify_models_changed 函数体')
        self.assertIn('modelsChanged.emit', m.group(1),
                      '_notify_models_changed 必须 emit bridge.modelsChanged')


if __name__ == '__main__':
    unittest.main(verbosity=2)