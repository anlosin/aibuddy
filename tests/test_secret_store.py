"""_secret_store.py 单元测试

覆盖：
1. 真存真取真删（Windows 凭据后端真打通）
2. 覆盖语义（同名 set 覆盖旧值）
3. 内存降级路径（mock keyring 抛 KeyringError → 退回内存 dict）
4. keyring → 内存一致性（keyring 取不到时仍能从内存拿到）
5. delete 同时清两个存储
"""
import sys
import os
import unittest
from unittest.mock import patch

# 让 _secret_store 能被 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plugins._secret_store as ss


class TestSecretStoreRealKeyring(unittest.TestCase):
    """真 keyring（Windows 凭据管理器）端到端测试"""

    SERVICE = "test"
    NAME = "unittest_real"

    def setUp(self):
        # 清理可能的残留
        ss.delete_secret(self.SERVICE, self.NAME)

    def tearDown(self):
        ss.delete_secret(self.SERVICE, self.NAME)

    def test_1_availability(self):
        """验证 keyring 后端确实可用（Windows 凭据管理器）"""
        b = ss.is_available()
        self.assertTrue(b, f"keyring 不可用: {b}")
        # 期望后端是 winvault / kwallet / mac 等任一系统级后端
        self.assertIn(".", b)

    def test_2_roundtrip(self):
        """存 → 取 → 一致"""
        ss.set_secret(self.SERVICE, self.NAME, "p@ssw0rd!中文")
        got = ss.get_secret(self.SERVICE, self.NAME)
        self.assertEqual(got, "p@ssw0rd!中文")

    def test_3_overwrite(self):
        """同名 set 视为覆盖"""
        ss.set_secret(self.SERVICE, self.NAME, "old")
        self.assertEqual(ss.get_secret(self.SERVICE, self.NAME), "old")
        ss.set_secret(self.SERVICE, self.NAME, "new")
        self.assertEqual(ss.get_secret(self.SERVICE, self.NAME), "new")

    def test_4_delete(self):
        """存 → 删 → 拿不到"""
        ss.set_secret(self.SERVICE, self.NAME, "x")
        ss.delete_secret(self.SERVICE, self.NAME)
        self.assertIsNone(ss.get_secret(self.SERVICE, self.NAME))


class TestSecretStoreFallback(unittest.TestCase):
    """mock keyring 抛异常，验证降级路径"""

    SERVICE = "test"
    NAME = "unittest_fallback"

    def setUp(self):
        # 重置模块级缓存，确保每次 setUp 都重新探测
        ss._AVAILABLE = None
        ss._BACKEND = None
        ss._FALLBACK.clear()

    def tearDown(self):
        ss._FALLBACK.clear()
        ss._AVAILABLE = None
        ss._BACKEND = None

    def test_5_keyring_unavailable_falls_back_to_memory(self):
        """mock keyring 抛 KeyringError → set 退回内存 + 返回 False"""
        with patch("keyring.get_keyring", side_effect=Exception("mock not available")):
            ok = ss.set_secret(self.SERVICE, self.NAME, "mem_secret")
        # set_secret 内部已探测，应降级
        self.assertFalse(ok)
        # get 应能拿到
        got = ss.get_secret(self.SERVICE, self.NAME)
        self.assertEqual(got, "mem_secret")

    def test_6_is_available_returns_false(self):
        """keyring 不可用时 is_available 返回 False"""
        with patch("keyring.get_keyring", side_effect=Exception("nope")):
            avail = ss.is_available()
        self.assertFalse(avail)

    def test_7_delete_clears_memory(self):
        """keyring 不可用时 delete 也能清内存"""
        with patch("keyring.get_keyring", side_effect=Exception("nope")):
            ss.set_secret(self.SERVICE, self.NAME, "to_delete")
        # 此时存在内存
        with patch("keyring.get_keyring", side_effect=Exception("nope")):
            ss.delete_secret(self.SERVICE, self.NAME)
        # 再 get 应为 None
        with patch("keyring.get_keyring", side_effect=Exception("nope")):
            self.assertIsNone(ss.get_secret(self.SERVICE, self.NAME))


class TestSecretStoreEdgeCases(unittest.TestCase):
    """边界场景"""

    def setUp(self):
        ss._FALLBACK.clear()

    def test_8_empty_password_deletes(self):
        """set_secret(password='') 等同于删除（避免空密码污染）"""
        ss.set_secret("t", "empty_pw", "")
        self.assertIsNone(ss.get_secret("t", "empty_pw"))

    def test_9_get_nonexistent_returns_none(self):
        """从未存过的凭据返回 None（不抛错）"""
        self.assertIsNone(ss.get_secret("never", "existed"))

    def test_10_account_naming_uniqueness(self):
        """不同 service+name 互不干扰"""
        ss.set_secret("svcA", "x", "pw_A")
        ss.set_secret("svcB", "x", "pw_B")
        self.assertEqual(ss.get_secret("svcA", "x"), "pw_A")
        self.assertEqual(ss.get_secret("svcB", "x"), "pw_B")
        ss.delete_secret("svcA", "x")
        ss.delete_secret("svcB", "x")

    def test_11_username_field_naming(self):
        """keyring username 字段应语义化：'sql-<name>' / 'ssh-<name>' / 'ssh-<name>-key'

        这样用户在 Windows 凭据管理器里能一眼看出这是哪个连接的密码
        """
        # sql
        ss.set_secret("sql", "prod", "p1")
        # ssh 主密码
        ss.set_secret("ssh", "server_42", "s1")
        # ssh 私钥口令（name 末尾带 #keypass）
        ss.set_secret("ssh", "server_42#keypass", "k1")

        # 真取：每条都应能取回（证明 username 字段算对了）
        self.assertEqual(ss.get_secret("sql", "prod"), "p1")
        self.assertEqual(ss.get_secret("ssh", "server_42"), "s1")
        self.assertEqual(ss.get_secret("ssh", "server_42#keypass"), "k1")

        # 真删（用同样的 username 规则）应能清掉，否则凭据管理器会有垃圾
        ss.delete_secret("sql", "prod")
        ss.delete_secret("ssh", "server_42")
        ss.delete_secret("ssh", "server_42#keypass")
        self.assertIsNone(ss.get_secret("sql", "prod"))
        self.assertIsNone(ss.get_secret("ssh", "server_42"))
        self.assertIsNone(ss.get_secret("ssh", "server_42#keypass"))


if __name__ == "__main__":
    # 详细输出
    unittest.main(verbosity=2)
