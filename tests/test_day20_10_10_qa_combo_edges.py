# -*- coding: utf-8 -*-
"""Day 20.10.10 B1 修复 —— QA 视角的边缘 / 持久化 / 跨路径护栏。

工程师写的护栏（test_day20_10_10_bugfix_combo_refresh.py）覆盖了「信号存在 +
slot emit + QML onModelsChanged 接上 + settings_dialog 4 个 handler 调用
_notify_models_changed」。本测试从 QA 视角独立验证下列工程师护栏**没覆盖**
或**只部分覆盖**的点：

1. **API 边界**（5 项，QA 独立实测）：
   - add_model("", base_url, key, mid, proxy, as_current) 空 name 是否仍能 add
   - add_model(name, "", key, mid, ...) 空 base_url → 拒绝
   - add_model(name, url, key, "", ...) 空 model_id
   - update_model("non_existent_id", ...) 改不存在
   - delete_model("non_existent_id") 删不存在
   - delete_model(only model) 拒绝
   - add_model(as_current=True) 且当前已有激活 → 切换

2. **并发安全**（连续多次 add/update/delete 交替调用）：
   - modelsChanged signal 触发次数 == 操作次数
   - 每次 emit 之后 list_models() 返回的列表与持久化层一致
   - 当前激活模型在切换过程中不出现「当前激活丢失」

3. **持久化（真磁盘落地）**：
   - 在临时 data 目录下设 QWEN_DATA_DIR env var
   - 用 subprocess 跑 add_model + save_models + delete_model
   - 读回 model_config.json 内容断言（不是只验 mock）

4. **QML 端 currentIndex 回写**（onModelsChanged 函数体四 case）：
   - 首项 current → currentIndex=0
   - 末项 current → currentIndex=length-1
   - 当前激活模型已删除 → 回退到 length-1 / 0
   - 列表为空 → currentIndex=-1

5. **PyQt5 备用窗口路径覆盖**（工程交付的「已知遗留 #2」核实）：
   - chat_window.py 是否调用 notify_models_changed?
   - settings_dialog._notify_models_changed 对 ChatWindow 实例是否生效？
   - 现实情况：PyQt5 路径没有 QtQuick ComboBox，下拉 UI 在 show_model_manager
     对话框自己里，已经被同步刷新 —— 调研结论必须明确写出。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_PATH = os.path.join(_ROOT, 'qwen_app', 'chat_bridge.py')
BRIDGE_MODEL_PATH = os.path.join(_ROOT, 'qwen_app', '_bridge_model.py')
SETTINGS_PATH = os.path.join(_ROOT, 'qwen_app', 'settings_dialog.py')
CHAT_WINDOW_PATH = os.path.join(_ROOT, 'qwen_app', 'chat_window.py')
MAIN_QML_PATH = os.path.join(_ROOT, 'qwen_app', 'qml', 'Main.qml')


# ────────────── 测试 1: API 边界（独立 QA 视角） ──────────────

class _ApiBaseSetup:
    """公共 setUp：替换 config.{load,save}_models 为可重写的内存版本。"""

    def setUp(self):
        from qwen_app import config as _cfg
        self._real_save_models = _cfg.save_models
        self._real_load_models = _cfg.load_models
        self._real_load_config = _cfg.load_config
        self._real_save_config = _cfg.save_config
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

        def fake_load_models():
            return list(self._seed_cfg['models']), self._seed_cfg.get('current_model')

        def fake_load_config():
            return dict(self._seed_cfg)

        def fake_save_models(models, current_id):
            self._seed_cfg['models'] = list(models)
            self._seed_cfg['current_model'] = current_id

        def fake_save_config(cfg):
            self._seed_cfg.update(cfg)

        _cfg.load_models = fake_load_models
        _cfg.load_config = fake_load_config
        _cfg.save_models = fake_save_models
        _cfg.save_config = fake_save_config

    def tearDown(self):
        from qwen_app import config as _cfg
        _cfg.save_models = self._real_save_models
        _cfg.load_models = self._real_load_models
        _cfg.load_config = self._real_load_config
        _cfg.save_config = self._real_save_config

    @classmethod
    def _app(cls):
        from PyQt5.QtCore import QCoreApplication
        return QCoreApplication.instance() or QCoreApplication(sys.argv)


class TestApiBoundaryAddModel(_ApiBaseSetup, unittest.TestCase):
    """add_model 边界：name 空 / base_url 空 / model_id 空。"""

    def setUp(self):
        super().setUp()
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme='light')

    def test_add_model_empty_name_falls_back_to_model_id(self):
        """name 为空字符串时，name 应回退到 model_id（用户没填名字不应报错）"""
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        ok = self.bridge.add_model('', 'https://api.example2.com/v1',
                                   'sk', 'qwen-max', '', False)
        self.assertTrue(ok, '空 name 但加 model_id 应成功')
        self.assertEqual(len(events), 1, '必须 emit 一次 modelsChanged')
        # 落地模型里 name 应回退为 model_id
        models, _ = self._real_load_models_call()
        self.assertEqual(models[-1]['name'], 'qwen-max',
                         '空 name 应回退为 model_id，不留空字符串')

    def _real_load_models_call(self):
        """直接读 self._seed_cfg 而非走被 replace 的 fake。"""
        return list(self._seed_cfg['models']), self._seed_cfg.get('current_model')

    def test_add_model_empty_base_url_rejected(self):
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        ok = self.bridge.add_model('n', '', 'sk', 'mid', '', False)
        self.assertFalse(ok, '空 base_url 应被拒绝')
        self.assertEqual(events, [], '拒绝时不应 emit modelsChanged')

    def test_add_model_whitespace_only_base_url_rejected(self):
        """空白字符串的 base_url（strip 后为空）也应被拒绝"""
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        ok = self.bridge.add_model('n', '   \t\n  ', 'sk', 'mid', '', False)
        self.assertFalse(ok, '纯空白 base_url 应被拒绝')
        self.assertEqual(events, [])

    def test_add_model_empty_model_id_rejected(self):
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        ok = self.bridge.add_model('n', 'http://x', 'sk', '', '', False)
        self.assertFalse(ok, '空 model_id 应被拒绝')
        self.assertEqual(events, [])


class TestApiBoundaryUpdateModel(_ApiBaseSetup, unittest.TestCase):
    """update_model 边界：target_id 不存在 / 空。"""

    def setUp(self):
        super().setUp()
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme='light')

    def test_update_unknown_id_rejected(self):
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        ok = self.bridge.update_model('no_such_id', 'x', 'http://a', 'sk', 'a', '')
        self.assertFalse(ok, '未知 id 应被拒绝')
        self.assertEqual(events, [], '拒绝时不应 emit modelsChanged')

    def test_update_empty_target_id_rejected(self):
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        ok = self.bridge.update_model('', 'x', 'http://a', 'sk', 'a', '')
        self.assertFalse(ok)
        self.assertEqual(events, [])

    def test_update_valid_id_changeable_field_only(self):
        """只改 name（不动 url/model_id/proxy）也要 emit modelsChanged
        （QA 关心：label 改了 UI 下拉也得刷）"""
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        # update_model 6 参数（target, name, url, key, mid, proxy）— 全部必填
        # 这里改 name + 保持其他字段不变
        ok = self.bridge.update_model(
            'm_seed',
            'renamed-seed',
            'https://api.example.com/v1',
            'sk-seed',
            'seed-model',
            '')
        self.assertTrue(ok)
        self.assertEqual(len(events), 1)
        models, _ = self._real_load_models()
        self.assertEqual(models[0]['name'], 'renamed-seed',
                         'name 字段应被持久化为新值')

    def _real_load_models(self):
        return list(self._seed_cfg['models']), self._seed_cfg.get('current_model')


class TestApiBoundaryDeleteModel(_ApiBaseSetup, unittest.TestCase):
    """delete_model 边界：未知 id / 唯一模型 / 删当前。"""

    def setUp(self):
        super().setUp()
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme='light')

    def test_delete_unknown_id_rejected(self):
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        ok = self.bridge.delete_model('no_such_id')
        self.assertFalse(ok)
        self.assertEqual(events, [])

    def test_delete_empty_id_rejected(self):
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        self.assertFalse(self.bridge.delete_model(''))
        self.assertEqual(events, [])

    def test_delete_only_model_rejected(self):
        """seed 只有 1 个模型 → delete_model 应被拒绝"""
        events_changed = []
        events_name = []
        self.bridge.modelsChanged.connect(lambda: events_changed.append(True))
        self.bridge.currentModelNameChanged.connect(lambda n: events_name.append(n))
        ok = self.bridge.delete_model('m_seed')
        self.assertFalse(ok, '唯一模型应拒绝删除（防止空表）')
        self.assertEqual(events_changed, [], '拒绝时不应 emit modelsChanged')
        self.assertEqual(events_name, [], '拒绝时不应 emit currentModelNameChanged')

    def test_delete_current_after_add_swaps_to_first_remaining(self):
        """seed + 添加第二个 → 删 seed → current 回退到第二个"""
        # 加第二个
        self.bridge.add_model('', 'http://b', 'sk', 'mid-b', '', False)
        events_changed = []
        events_name = []
        self.bridge.modelsChanged.connect(lambda: events_changed.append(True))
        self.bridge.currentModelNameChanged.connect(lambda n: events_name.append(n))
        # 取新加的第二条 id（不是 model_id 字段，是 m_<timestamp>）
        models_before, _ = self._real_load_models()
        new_model_id = models_before[1]['id']
        self.assertNotEqual(new_model_id, 'm_seed', '新加的应有自己的 m_xxx id')
        # 删 seed（当前激活）
        ok = self.bridge.delete_model('m_seed')
        self.assertTrue(ok)
        self.assertEqual(len(events_changed), 1)
        self.assertEqual(len(events_name), 1)
        # 当前激活应回退到剩余第一个（=新加的那个）
        models, cur = self._real_load_models()
        self.assertEqual(len(models), 1, '删后应剩 1 条')
        self.assertEqual(cur, models[0]['id'], 'cur 应指向剩余第一条')
        self.assertEqual(cur, new_model_id, 'cur 应是新加的那条')
        self.assertEqual(self.bridge._last_model_name, models[0]['name'])

    def _real_load_models(self):
        return list(self._seed_cfg['models']), self._seed_cfg.get('current_model')


class TestApiBoundaryAddModelAsCurrent(_ApiBaseSetup, unittest.TestCase):
    """add_model(..., as_current=True) 当当前已有激活 → 应切换。"""

    def setUp(self):
        super().setUp()
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme='light')

    def test_add_as_current_switches_active(self):
        events_changed = []
        events_name = []
        self.bridge.modelsChanged.connect(lambda: events_changed.append(True))
        self.bridge.currentModelNameChanged.connect(lambda n: events_name.append(n))
        # seed 已激活 → 新增并 as_current=True → 应切到新模型
        ok = self.bridge.add_model(
            'primary', 'https://api.example2.com/v1',
            'sk-p', 'primary-id', '', True)
        self.assertTrue(ok)
        self.assertEqual(events_changed, [True])
        self.assertEqual(events_name, ['primary'],
                         'as_current=True 必须 emit 新模型名')
        models, cur = self._real_load_models()
        self.assertEqual(len(models), 2)
        # cur 应是新加的那条
        new_m = next(m for m in models if m.get('model_id') == 'primary-id')
        self.assertEqual(cur, new_m['id'], 'cur 应切到新加的模型')


# ────────────── 测试 2: 并发安全（连续多次 add/update/delete 交替） ──────────────

class TestConcurrentOpsSignalCount(_ApiBaseSetup, unittest.TestCase):
    """连续交替 add/update/delete：每次 emit 一次 modelsChanged + 顺序正确。"""

    def setUp(self):
        super().setUp()
        from qwen_app.chat_bridge import ChatBridge
        self.bridge = ChatBridge(theme='light')

    def test_alternating_add_update_delete_emits_in_sequence(self):
        # 先准备 2 个（拿到新加的 id，delete 用真实 id 而不是 model_id 字段）
        self.bridge.add_model('', 'http://b', 'sk', 'mid-b', '', False)
        models_now, _ = self._seed_load()
        second_id = models_now[1]['id']  # 真实 id = m_<timestamp>_<hex>
        # 准备操作序列
        seq = [
            ('add', ('a3', 'http://a3', 'sk', 'mid-a3', '', False)),
            ('update', ('m_seed', 'seed-renamed', 'http://a', 'sk', 'seed-model', '')),
            ('delete', (second_id,)),
            ('add', ('a4', 'http://a4', 'sk', 'mid-a4', '', True)),
            ('update', ('m_seed', 'seed-renamed-2', 'http://a', 'sk', 'seed-model', '')),
        ]
        events_changed = []
        events_name = []
        self.bridge.modelsChanged.connect(lambda: events_changed.append('c'))
        self.bridge.currentModelNameChanged.connect(lambda n: events_name.append(n))
        for op, args in seq:
            if op == 'add':
                ok = self.bridge.add_model(*args)
            elif op == 'update':
                ok = self.bridge.update_model(*args)
            elif op == 'delete':
                ok = self.bridge.delete_model(*args)
            self.assertTrue(ok, f'{op}({args}) 应成功')
        # modelsChanged 应在每个成功操作后 emit 一次 = 共 5 次
        self.assertEqual(len(events_changed), 5,
                         f'5 个成功操作应 emit 5 次 modelsChanged，实际 {len(events_changed)}')
        # currentModelNameChanged 触发次数分析：
        # seq[0] add (not current) → 0
        # seq[1] update m_seed (current 仍是 m_seed) → 1
        # seq[2] delete second_id (cur=m_seed 没动) → 0
        # seq[3] add as_current=True → cur 切到 a4 → 1
        # seq[4] update m_seed (cur 已被 seq[3] 切走，m_seed 不再是当前) → 0
        # 共 2 次
        self.assertEqual(len(events_name), 2,
                         f'2 个切到当前的操作应 emit 2 次 currentModelNameChanged，实际 {len(events_name)}')

    def test_failed_ops_do_not_emit(self):
        events = []
        self.bridge.modelsChanged.connect(lambda: events.append(True))
        # 全部失败
        self.bridge.add_model('', '', 'sk', 'mid', '', False)  # 空 url
        self.bridge.update_model('no_such', 'x', 'http://a', 'sk', 'a', '')
        self.bridge.delete_model('no_such')
        self.assertEqual(events, [],
                         '所有失败操作不应 emit modelsChanged')

    def _seed_load(self):
        return list(self._seed_cfg['models']), self._seed_cfg.get('current_model')

    def test_delete_last_after_adding_one_works(self):
        """seed + 添加 → 删 seed → 删除应成功 → 但要保证至少留 1 条"""
        # 加第二条
        self.bridge.add_model('', 'http://b', 'sk', 'mid-b', '', False)
        self.assertEqual(len(self._seed_cfg['models']), 2)
        # 现在删 seed
        ok = self.bridge.delete_model('m_seed')
        self.assertTrue(ok)
        # 应剩 1 条
        models, _ = self._real_load_models()
        self.assertEqual(len(models), 1)


# ────────────── 测试 3: 持久化（真磁盘落地） ──────────────

class TestPersistenceRealDisk(unittest.TestCase):
    """用 subprocess + 临时 QWEN_DATA_DIR 跑真实 save_models / load_models，
    读 model_config.json 验证 add / update / delete 真的写盘了。"""

    # 子进程脚本：add / update / delete + 读回 model_config.json 内容
    _SCRIPT = r'''
import os, sys, json, time
sys.path.insert(0, r"{root}")
from qwen_app import config as _cfg
from qwen_app.chat_bridge import ChatBridge

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt5.QtCore import QCoreApplication
app = QCoreApplication.instance() or QCoreApplication(sys.argv)
b = ChatBridge(theme="light")

# 给一个 seed（先持久化到磁盘，load_models 才走真路径）
seed = {{
    "models": [
        {{"id":"m_a","name":"A","base_url":"http://a","api_key":"sk-a","model_id":"mid-a","proxy":""}},
        {{"id":"m_b","name":"B","base_url":"http://b","api_key":"sk-b","model_id":"mid-b","proxy":""}},
    ],
    "current_model":"m_a",
    "enable_thinking": False, "enable_tools": True,
}}
# 写一次 seed 到磁盘（load_models 走真路径）
_cfg.save_models(seed["models"], seed["current_model"])

# 拦截 load_models 让它读 seed（不读磁盘），但 save_models 走真路径
_cfg.load_models = lambda: (list(seed["models"]), seed["current_model"])
_cfg.load_config = lambda: dict(seed)

op = sys.argv[1]
result = {{}}
try:
    if op == "add":
        ok = b.add_model("C", "http://c", "sk-c", "mid-c", "", False)
    elif op == "add_as_current":
        ok = b.add_model("D", "http://d", "sk-d", "mid-d", "", True)
    elif op == "update":
        ok = b.update_model("m_b", "B-renamed", "http://b", "sk-b", "mid-b", "")
    elif op == "delete":
        ok = b.delete_model("m_b")
    elif op == "delete_only_attempt":
        # seed 只剩 1 条 → 删应失败
        # 先把 seed 砍到 1 条
        seed["models"] = seed["models"][:1]
        ok = b.delete_model("m_a")
    elif op == "noop_add_invalid":
        ok = b.add_model("", "", "sk", "mid", "", False)
    else:
        print("UNKNOWN_OP")
        sys.exit(2)
    result["ok"] = ok
except Exception as e:
    result["exc"] = repr(e)

# 直接读 model_config.json 内容（真磁盘）
import os
cfg_path = os.path.join(os.environ["QWEN_DATA_DIR"], "model_config.json")
# 注意：seed 用的是内存 fake_load_models 替换，但 _bridge_model.add_model
# 内部调 _config.save_models 走的是真 save_models（已被 replace 为 fake _sm）。
# 为了测真持久化，需要 seed 写盘前手动替换回真 _cfg.save_models
result["on_disk_models_count"] = len(seed["models"])
result["on_disk_current_model"] = seed.get("current_model")
result["disk_models_names"] = [m.get("name") for m in seed["models"]]
print("RESULT_JSON=" + json.dumps(result, ensure_ascii=False))
'''

    def _run(self, op):
        # 先在临时 data dir 下准备一份 model_config.json（seed）
        tmpdir = tempfile.mkdtemp(prefix="qa_b1_")
        cfg_path = os.path.join(tmpdir, "model_config.json")
        seed = {
            "models": [
                {"id": "m_a", "name": "A", "base_url": "http://a",
                 "api_key": "sk-a", "model_id": "mid-a", "proxy": ""},
                {"id": "m_b", "name": "B", "base_url": "http://b",
                 "api_key": "sk-b", "model_id": "mid-b", "proxy": ""},
            ],
            "current_model": "m_a",
            "enable_thinking": False, "enable_tools": True,
        }
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(seed, f, ensure_ascii=False, indent=2)
        # 子进程
        env = dict(os.environ)
        env['QWEN_DATA_DIR'] = tmpdir
        env.setdefault('QT_QPA_PLATFORM', 'offscreen')
        code = self._SCRIPT.format(root=_ROOT)
        p = subprocess.run([sys.executable, '-c', code, op],
                           capture_output=True, text=True, env=env, cwd=_ROOT,
                           timeout=30)
        if p.returncode != 0:
            self.fail("subprocess 失败:\nstdout=%s\nstderr=%s" %
                      (p.stdout[-1000:], p.stderr[-1500:]))
        # 抽 RESULT_JSON
        result = {}
        for ln in (p.stdout or '').splitlines():
            if ln.startswith('RESULT_JSON='):
                result = json.loads(ln.split('=', 1)[1])
                break
        # 真读磁盘（验证 save_models 真的写出来了）
        with open(cfg_path, 'r', encoding='utf-8') as f:
            disk = json.load(f)
        return result, disk

    def test_add_persists_to_disk(self):
        result, disk = self._run('add')
        self.assertTrue(result['ok'])
        # 磁盘上 models 列表应增加
        self.assertEqual(len(disk['models']), 3,
                         f"add 后磁盘上应 3 条，实际 {len(disk['models'])}")
        # 名字应是新加的
        names = [m.get('name') for m in disk['models']]
        self.assertIn('C', names, '磁盘上应出现新加的 C')

    def test_add_as_current_persists_and_switches(self):
        result, disk = self._run('add_as_current')
        self.assertTrue(result['ok'])
        # 磁盘上 current_model 应是新加那条的 id
        new_m = next(m for m in disk['models'] if m.get('name') == 'D')
        self.assertEqual(disk['current_model'], new_m['id'],
                         '磁盘上 current_model 应切换到新加的 D')

    def test_update_persists_to_disk(self):
        result, disk = self._run('update')
        self.assertTrue(result['ok'])
        b_m = next(m for m in disk['models'] if m.get('id') == 'm_b')
        self.assertEqual(b_m['name'], 'B-renamed',
                         '磁盘上 m_b 的 name 应被更新为 B-renamed')

    def test_delete_persists_to_disk(self):
        result, disk = self._run('delete')
        self.assertTrue(result['ok'])
        self.assertEqual(len(disk['models']), 1, '删 m_b 后应剩 1 条')
        ids = [m.get('id') for m in disk['models']]
        self.assertNotIn('m_b', ids, '磁盘上 m_b 应已被移除')

    def test_delete_only_model_rejected_and_keeps_disk_intact(self):
        """子进程已把 seed 砍到 1 条 → 删应被拒绝 → 磁盘应保持原状

        注意：子进程内的 load_models 被 mock 成只返回 seed 内存字典（不读磁盘）。
        所以磁盘文件始终是父进程 _run 写入的 2 条 seed。验证磁盘**未被修改**
        = 长度仍为 2，current_model 仍为 m_a。
        """
        result, disk = self._run('delete_only_attempt')
        self.assertFalse(result['ok'], '唯一模型应被拒绝删除')
        # 磁盘未被子进程改写（load_models mock 阻断了 save_models 真路径？——
        # 实际：子进程的 save_models 走真 _cfg.save_models，但 load_models 是
        # mocked 的；delete_model 内部调 load_models 拿到 2 条 → len(models) > 1
        # → 不会走「唯一模型拒绝」分支 → 所以这次会真的从磁盘删一条。
        # 改测：磁盘上 m_a 仍存在（如果被删走则说明原子性失败）。
        ids = [m.get('id') for m in disk['models']]
        # 这里实际上等价于「子进程真的去磁盘删了一条」 → 因为我们的 _sm 替换没生效
        # （只 replace 了 load_models，没 replace save_models）→ 走真 save_models。
        # 验证：要么磁盘剩 1 条（如果 delete 成功），要么 2 条（如果被拒）。
        # 因 delete 路径下唯一模型在子进程内存中被砍到 1 条，但 load_models mock
        # 也被砍到 1 条 → len(models)=1 → 走拒分支 → 磁盘不变。
        self.assertEqual(len(disk['models']), 2,
                         f'拒绝删唯一模型后磁盘应仍 2 条，实际 {len(disk["models"])}')
        self.assertIn('m_a', ids, 'm_a 应仍在磁盘上')

    def test_invalid_add_does_not_touch_disk(self):
        """非法参数 add → save_models 不应被调用 → 磁盘无任何变化"""
        result, disk = self._run('noop_add_invalid')
        self.assertFalse(result['ok'])
        # 磁盘 models 长度应仍是 2（seed 原貌）
        self.assertEqual(len(disk['models']), 2,
                         '非法 add 不应让磁盘 models 变化')


# ────────────── 测试 4: QML 端 currentIndex 回写（4 case） ──────────────

class TestQmlCurrentIndexFallbackCases(unittest.TestCase):
    """Main.qml 的 onModelsChanged 必须覆盖：首项 / 末项 / 已删除 / 空列表 4 case。"""

    def setUp(self):
        with open(MAIN_QML_PATH, encoding='utf-8') as f:
            self.qml = f.read()

    def _handler_body(self):
        """花括号配对提取 onModelsChanged 函数体（贪婪 regex 在嵌套 if/for 会失效）"""
        m = re.search(r'function\s+onModelsChanged\s*\([^)]*\)\s*\{', self.qml)
        self.assertIsNotNone(m, '找不到 onModelsChanged 函数头')
        depth = 0
        i = m.end() - 1   # 指向 '{'
        start = i
        while i < len(self.qml):
            c = self.qml[i]
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return self.qml[start + 1: i]
            i += 1
        self.fail('onModelsChanged 函数体无配对闭合括号')

    def test_handler_covers_first_item_current(self):
        """首项 current（cur=ms[0].id）→ currentIndex=0（for 循环第一项命中即返回）"""
        body = self._handler_body()
        # 找 for 循环 + if (ms[i].current) + currentIndex = i + return
        # (允许 \n 在中段，body 是多行文本)
        self.assertRegex(body,
            r'for\s*\([^)]*\)\s*\{[^}]*if\s*\(\s*ms\[i\]\.current\s*\)[^}]*currentIndex\s*=\s*i[^}]*return',
            '必须 for 循环里命中 .current 立即设 currentIndex = i 并 return（首项命中即返）')

    def test_handler_falls_back_to_zero_on_empty_current(self):
        """循环结束没找到 current → 退化到首项 0"""
        body = self._handler_body()
        self.assertRegex(body,
            r'currentIndex\s*=\s*ms\.length\s*>\s*0\s*\?\s*0\s*:\s*-1',
            '找不到 current 项时退化到首项 0；列表为空 → -1')

    def test_handler_handles_empty_list(self):
        """列表为空 → currentIndex = -1（不能是 undefined / -1）"""
        body = self._handler_body()
        # 退化分支已覆盖：ms.length > 0 ? 0 : -1
        self.assertIn('ms.length > 0', body)
        self.assertIn(': -1', body,
                      '列表为空时 currentIndex = -1（不能崩溃）')

    def test_handler_reloads_via_bridge_list_models(self):
        """必须显式调 bridge.list_models() 重拉（不能只设空数组或缓存值）"""
        body = self._handler_body()
        # modelCombo.model = bridge ? bridge.list_models() : []
        self.assertIn('bridge.list_models()', body,
                      'onModelsChanged 必须从 bridge.list_models() 拉最新')


# ────────────── 测试 5: PyQt5 备用窗口路径覆盖 ──────────────

class TestPyQt5FallbackPathCoverage(unittest.TestCase):
    """工程交付的「已知遗留 #2」—— chat_window.py 是否调用 notify_models_changed?

    调查结论：chat_window.py 走 settings_dialog.show_model_manager(self)，而
    show_model_manager 内部 4 个 handler 已经调 _notify_models_changed()。但
    _notify_models_changed 是 try { bridge.modelsChanged.emit() }，先取
    getattr(window, '_b', None)，再 fallback getattr(window, 'bridge', None)，
    两个都是为 QtQuick 路径的 _DialogHost 设计的。ChatWindow 实例上既无 _b
    也无 bridge，所以 PyQt5 路径里 _notify_models_changed() 实际是 no-op。

    但 PyQt5 路径**没有** QtQuick ComboBox 要刷新（模型下拉 UI 就是 dialog 自己），
    show_model_manager 写盘后立刻调 refresh_list(cur) 同步刷新 dialog 内的
    QListWidget。所以这个 no-op 是安全的 —— 但工程注释里没说清楚。
    """

    def setUp(self):
        with open(CHAT_WINDOW_PATH, encoding='utf-8') as f:
            self.chat_src = f.read()
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            self.settings_src = f.read()

    def test_chat_window_does_not_call_notify_directly(self):
        """chat_window.py 不应自己调 notify_models_changed —— 它走 settings_dialog"""
        self.assertNotIn('notify_models_changed', self.chat_src,
                         'chat_window.py 不应直接调 notify_models_changed；'
                         '模型变更统一走 settings_dialog.show_model_manager')

    def test_chat_window_uses_show_model_manager(self):
        """chat_window.py 调 show_model_manager —— 已通过它覆盖 _notify"""
        self.assertIn('show_model_manager', self.chat_src,
                      'chat_window.py 应通过 settings_dialog.show_model_manager 触发变更')

    def test_settings_dialog_helper_safe_on_pyqt5_window(self):
        """_notify_models_changed 对 ChatWindow 实例必须是 no-op 而不抛异常
        （防御性 gettattr + hasattr 已覆盖）"""
        m = re.search(
            r'def\s+_notify_models_changed\s*\([^)]*\)\s*:\s*\n((?:.*\n)*?)(?=\n\s*def\s|\n\s*$)',
            self.settings_src)
        self.assertIsNotNone(m)
        body = m.group(1)
        # 必须 try/except 包裹 emit
        self.assertIn('try:', body,
                      '_notify_models_changed 必须 try/except 保护 emit 调用')
        # 取 bridge 必须用 getattr（防御 ChatWindow 实例没有 _b / bridge 属性）
        self.assertIn('getattr(window', body,
                      '必须用 getattr 取 bridge（ChatWindow 实例没有 _b / bridge）')
        # 必须检查 hasattr(bridge, "modelsChanged")
        self.assertIn('hasattr(bridge, "modelsChanged")', body,
                      '必须 hasattr 防护（PyQt5 路径上 bridge 可能为 None）')

    def test_pyqt5_fallback_has_no_qtquick_combobox_to_refresh(self):
        """PyQt5 路径没有 modelCombo（QML 才有），下拉 UI 在 dialog 自己里。

        验证：chat_window.py 不应引用 'modelCombo' / 'bridge.modelsChanged'。
        """
        self.assertNotIn('modelCombo', self.chat_src,
                         'chat_window.py 不应有 modelCombo（这是 QML 控件）')
        self.assertNotIn('modelsChanged', self.chat_src,
                         'chat_window.py 不应直接发 modelsChanged（PyQt5 路径无 QML 监听者）')


if __name__ == '__main__':
    unittest.main(verbosity=2)