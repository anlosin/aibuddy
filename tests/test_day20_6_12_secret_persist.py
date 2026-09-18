# -*- coding: utf-8 -*-
"""Day 20.6.12 护栏：ssh/sql 连接配置必须真的落盘，密码必须真的进凭据库

背景（本轮实测发现，**比审计报告更严重**）：
  ssh_runner.py 与 sql_helper.py 里用 `from . import _secret_store` 做包内相对导入，
  但插件是由 plugin_manager 用 spec_from_file_location 以**独立模块名**
  （plugin_ssh_runner）加载的，模块 __package__ 是空字符串 ——
  相对导入**必然**抛 "attempted relative import with no known parent package"。

  而 ssh_runner._save_cfg / sql_helper._save_cfg 把整个函数体裹在
  `try: ... except Exception: pass` 里，异常被静默吞掉 —— 后果是：
    1. **连 sanitized 配置都不写盘** → ssh_connect/db_connect 报"保存成功"，
       重启后连接全部消失；
    2. keyring 通道从未真正生效（审计报告说"ssh/sql 密码已接入 keyring"，
       那是只读源码表面得出的结论）。

  修复：改为绝对导入 `from plugins import _secret_store`，并把
  "导入失败" 与 "写盘失败" 从静默改为显式告警。

护栏：
  1. _save_cfg 必须真的写出配置文件（回归本 bug）
  2. 密码/私钥口令不得出现在磁盘配置里（只进凭据库）
  3. 密码确实以 (service, name) 送进 _secret_store.set_secret
  4. 凭据库不可用时，配置**仍然要落盘**（不能因 keyring 挂掉丢配置）
  5. _connect 必须真的去凭据库查密码
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins import ssh_runner, sql_helper, _secret_store  # noqa: E402


class TestSecretPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="secretpersist_")
        self._ssh_file_sentinel = ssh_runner.CONN_FILE
        self._sql_file_sentinel = sql_helper.CONN_FILE
        self._ssh_cfg = ssh_runner._CFG
        self._sql_cfg = sql_helper._CFG
        # Day 20.6.16：CONN_FILE 已退化为源码态兼容常量；测试改为传 path。
        self.ssh_file = os.path.join(self.tmp, "ssh_connections.json")
        self.sql_file = os.path.join(self.tmp, "db_connections.json")

        self.calls = []
        self._set_patcher = mock.patch.object(
            _secret_store, "set_secret",
            side_effect=lambda s, n, p: (self.calls.append((s, n, p)), True)[1],
        )
        self._set_patcher.start()

    def tearDown(self):
        try:
            self._set_patcher.stop()
        except RuntimeError:
            pass
        ssh_runner.CONN_FILE = self._ssh_file_sentinel
        sql_helper.CONN_FILE = self._sql_file_sentinel
        ssh_runner._CFG = self._ssh_cfg
        sql_helper._CFG = self._sql_cfg
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── ssh ──
    def test_ssh_cfg_written_and_password_to_keyring(self):
        """回归核心：ssh 配置必须落盘，密码必须进凭据库且不明文落盘"""
        ssh_runner._CFG = {
            "srv": {"host": "1.2.3.4", "user": "root",
                    "password": "s3cr3t", "key_passphrase": "kp-secret"}
        }
        ssh_runner._save_cfg(path=self.ssh_file)

        self.assertTrue(os.path.exists(self.ssh_file),
                        "ssh 配置未落盘 —— 旧 bug（相对导入失败被静默吞掉）复现")
        with open(self.ssh_file, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("s3cr3t", raw, "密码不得明文落盘")
        self.assertNotIn("kp-secret", raw, "私钥口令不得明文落盘")
        data = json.loads(raw)
        self.assertEqual(data["srv"]["host"], "1.2.3.4")

        kinds = [(s, n) for s, n, _ in self.calls]
        self.assertIn(("ssh", "srv"), kinds, "密码未送进凭据库")
        self.assertIn(("ssh", "srv#keypass"), kinds, "私钥口令未送进凭据库")

    def test_ssh_cfg_written_even_without_secret_store(self):
        """凭据库不可用时配置仍必须落盘 —— **且密码必须降级到磁盘**（P0-AUD2-4）

        旧行为：keyring 不可用 → 密码丢失。
        新行为：与 config._sanitize_api_keys 同策略 —— 降级到磁盘明文，
        严格遵守项目「never lose user key」原则。

        实现：mock set_secret 抛 RuntimeError 模拟 backend 不可用（与
        test_secret_integration.TestKeyringUnavailableNeverLoseKey 同型）。
        原写法用 patch.dict(sys.modules, ...) 不可靠 —— Python import 在
        sys.modules 命中 None 时会**重新加载**子模块，绕开了 patch。
        """
        real_set = _secret_store.set_secret
        def fail(*a, **kw):
            raise RuntimeError("backend unavailable (simulated)")
        _secret_store.set_secret = fail
        try:
            ssh_runner._CFG = {"srv": {"host": "h1", "user": "u", "password": "pw"}}
            ssh_runner._save_cfg(path=self.ssh_file)
        finally:
            _secret_store.set_secret = real_set

        self.assertTrue(os.path.exists(self.ssh_file),
                        "凭据库不可用时配置也必须落盘")
        with open(self.ssh_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["srv"]["host"], "h1")
        # P0-AUD2-4：降级后 password 必须**仍在**——否则用户密码永久丢失
        self.assertEqual(data["srv"]["password"], "pw",
                         "keyring 不可用时密码必须降级到磁盘，否则永久丢失")

    def test_ssh_connect_consults_secret_store(self):
        """_connect 必须先去凭据库取密码（此前相对导入失败 → 永远取不到）"""
        try:
            import paramiko  # noqa: F401
        except ImportError:
            self.skipTest("未安装 paramiko")
        cfg = {"name": "srv", "host": "127.0.0.1", "port": 1, "user": "u"}
        with mock.patch.object(_secret_store, "get_secret",
                               return_value="pw-from-keyring") as g:
            ssh_runner._connect(cfg)      # 连接必然失败，但查询必须发生
        self.assertTrue(g.called, "_connect 未查询凭据库")
        self.assertEqual(tuple(g.call_args_list[0][0][:2]), ("ssh", "srv"))

    # ── sql ──
    def test_sql_cfg_written_and_password_to_keyring(self):
        """同一缺陷在 sql_helper 的同构复现"""
        sql_helper._CFG = {"db1": {"type": "mysql", "host": "dbhost",
                                   "password": "dbpass"}}
        sql_helper._save_cfg(path=self.sql_file)

        self.assertTrue(os.path.exists(self.sql_file),
                        "db 配置未落盘 —— 旧 bug 复现")
        with open(self.sql_file, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("dbpass", raw, "数据库密码不得明文落盘")
        data = json.loads(raw)
        self.assertEqual(data["db1"]["host"], "dbhost")
        self.assertIn(("sql", "db1"), [(s, n) for s, n, _ in self.calls])

    def test_sql_cfg_written_even_without_secret_store(self):
        """凭据库不可用时 db 配置仍必须落盘 —— **且密码必须降级**（P0-AUD2-4）

        实现：mock set_secret 抛异常（同上 test_ssh_* 的写法）。
        """
        real_set = _secret_store.set_secret
        def fail(*a, **kw):
            raise RuntimeError("backend unavailable (simulated)")
        _secret_store.set_secret = fail
        try:
            sql_helper._CFG = {"db1": {"type": "sqlite", "path": ":memory:",
                                       "password": "pw"}}
            sql_helper._save_cfg(path=self.sql_file)
        finally:
            _secret_store.set_secret = real_set

        self.assertTrue(os.path.exists(self.sql_file))
        with open(self.sql_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["db1"]["type"], "sqlite")
        # P0-AUD2-4：keyring 不可用时密码必须**降级到磁盘**
        self.assertEqual(data["db1"]["password"], "pw",
                         "keyring 不可用时密码必须降级到磁盘，否则永久丢失")


if __name__ == "__main__":
    unittest.main()
