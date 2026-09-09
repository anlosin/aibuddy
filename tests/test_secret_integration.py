"""sql_helper / ssh_runner 集成测试：密码通过 keyring 保存，磁盘 cfg 不含明文。

不依赖真实 MySQL/SSH 服务器；只测"配置落盘"和"密码取回"逻辑。
"""
import sys
import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# 让 plugins 能被 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSqlHelperNoPasswordLeak(unittest.TestCase):
    """sql_helper._save_cfg 落盘后不应包含明文密码"""

    def setUp(self):
        # 隔离每个测试的 _CFG 状态
        import plugins.sql_helper as sh
        sh._CFG = {}
        # 把 CONN_FILE 指向临时文件，避免污染真实 data/
        self.tmpdir = tempfile.mkdtemp()
        self.tmpfile = os.path.join(self.tmpdir, "sql_conns.json")
        self._orig = sh.CONN_FILE
        sh.CONN_FILE = self.tmpfile
        # 清掉可能残留的 keyring 条目
        import plugins._secret_store as ss
        ss.delete_secret("sql", "test_user1")

    def tearDown(self):
        import plugins.sql_helper as sh
        sh.CONN_FILE = self._orig
        import plugins._secret_store as ss
        ss.delete_secret("sql", "test_user1")

    def test_1_disk_cfg_has_no_password(self):
        """落盘 JSON 不应包含 password 字段"""
        import plugins.sql_helper as sh
        sh._CFG["test_user1"] = {
            "type": "mysql", "host": "127.0.0.1", "port": 3306,
            "user": "root", "password": "mySecret123!",
        }
        sh._save_cfg()
        # 读回磁盘
        with open(self.tmpfile, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["test_user1"].get("password"), None,
                         f"磁盘 cfg 仍含 password: {saved}")
        self.assertNotIn("password", saved["test_user1"],
                         f"磁盘 cfg 含 password key: {saved}")

    def test_2_password_saved_to_keyring(self):
        """密码应存入 Windows 凭据管理器"""
        import plugins.sql_helper as sh
        sh._CFG["test_user1"] = {
            "type": "mysql", "host": "127.0.0.1", "port": 3306,
            "user": "root", "password": "mySecret123!",
        }
        sh._save_cfg()
        # 从 keyring 验证
        import plugins._secret_store as ss
        got = ss.get_secret("sql", "test_user1")
        self.assertEqual(got, "mySecret123!")

    def test_3_password_override(self):
        """同名再次 set_secret 应覆盖旧密码"""
        import plugins.sql_helper as sh
        sh._CFG["test_user1"] = {"type": "mysql", "password": "old_pw"}
        sh._save_cfg()
        sh._CFG["test_user1"]["password"] = "new_pw"
        sh._save_cfg()
        import plugins._secret_store as ss
        self.assertEqual(ss.get_secret("sql", "test_user1"), "new_pw")

    def test_4_connect_retrieves_password_from_keyring(self):
        """_connect 应能从 keyring 取到密码（mock db driver 避免真实连接）"""
        import plugins.sql_helper as sh
        sh._CFG["test_user1"] = {"type": "mysql", "password": "from_keyring"}
        sh._save_cfg()
        # 清掉 cfg 里的 password，模拟"只存了 keyring"的场景
        saved = {"type": "mysql", "host": "127.0.0.1", "port": 3306,
                 "user": "root", "name": "test_user1"}
        # 直接调 _connect 但 mock mysql.connector.connect 拦截
        with patch("mysql.connector.connect") as mock_connect:
            mock_connect.return_value = MagicMock()
            conn, err = sh._connect(saved)
        self.assertIsNone(err)
        # 检查传给 mysql.connector.connect 的 password 参数
        kwargs = mock_connect.call_args.kwargs
        self.assertEqual(kwargs.get("password"), "from_keyring",
                         f"_connect 未从 keyring 取密码: {kwargs}")


class TestSshRunnerNoPasswordLeak(unittest.TestCase):
    """ssh_runner._save_cfg 落盘后不应包含明文密码 / key_passphrase"""

    def setUp(self):
        import plugins.ssh_runner as sr
        sr._CFG = {}
        self.tmpdir = tempfile.mkdtemp()
        self.tmpfile = os.path.join(self.tmpdir, "ssh_conns.json")
        self._orig = sr.CONN_FILE
        sr.CONN_FILE = self.tmpfile
        import plugins._secret_store as ss
        ss.delete_secret("ssh", "test_server")
        ss.delete_secret("ssh", "test_server#keypass")

    def tearDown(self):
        import plugins.ssh_runner as sr
        sr.CONN_FILE = self._orig
        import plugins._secret_store as ss
        ss.delete_secret("ssh", "test_server")
        ss.delete_secret("ssh", "test_server#keypass")

    def test_5_disk_cfg_has_no_password(self):
        """落盘 JSON 不应包含 password 与 key_passphrase"""
        import plugins.ssh_runner as sr
        sr._CFG["test_server"] = {
            "host": "1.2.3.4", "port": 22, "user": "deploy",
            "password": "sshSecret!",
            "key_path": "/home/deploy/.ssh/id_rsa",
            "key_passphrase": "keyPass!",
        }
        sr._save_cfg()
        with open(self.tmpfile, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("password", saved["test_server"])
        self.assertNotIn("key_passphrase", saved["test_server"])

    def test_6_password_and_keypass_saved_to_keyring(self):
        """密码 + 私钥口令都应存入 keyring（不同 account 区分）"""
        import plugins.ssh_runner as sr
        sr._CFG["test_server"] = {
            "host": "1.2.3.4", "user": "deploy",
            "password": "sshSecret!", "key_passphrase": "keyPass!",
        }
        sr._save_cfg()
        import plugins._secret_store as ss
        self.assertEqual(ss.get_secret("ssh", "test_server"), "sshSecret!")
        self.assertEqual(ss.get_secret("ssh", "test_server#keypass"), "keyPass!")

    def test_7_get_client_retrieves_password_from_keyring(self):
        """_get_client 应能从 keyring 取密码（mock paramiko）"""
        import plugins.ssh_runner as sr
        sr._CFG["test_server"] = {
            "host": "1.2.3.4", "port": 22, "user": "deploy", "name": "test_server",
            "password": "from_keyring_ssh",
        }
        sr._save_cfg()
        # 模拟"重启": _load_cfg 从磁盘覆盖 _CFG（磁盘 cfg 不含 password 但含 name）
        sr._load_cfg()
        with patch("paramiko.SSHClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            client, err = sr._get_client("test_server")
        self.assertIsNone(err)
        # 检查 connect 调用的 password 参数
        connect_kwargs = mock_client.connect.call_args.kwargs
        self.assertEqual(connect_kwargs.get("password"), "from_keyring_ssh",
                         f"_get_client 未从 keyring 取密码: {connect_kwargs}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
