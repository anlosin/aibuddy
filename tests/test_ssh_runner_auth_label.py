"""Day 19 (M-NEW-1): ssh_runner._auth_label 单元测试。

覆盖：key_path 优先 / 无 key_path 有密码 / 全无 三种分支。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestSshRunnerAuthLabel(unittest.TestCase):
    """M-NEW-1: 抽成 helper 后必须保留全部三个分支的行为。"""

    def test_key_path_priority(self):
        """key_path 非空 → 返回 私钥(...)，即使同时有密码也应让位"""
        from plugins import ssh_runner
        cfg = {"key_path": "/home/u/.ssh/id_rsa", "password": "anything"}
        label = ssh_runner._auth_label(cfg, has_pw=True)
        self.assertEqual(label, "私钥(/home/u/.ssh/id_rsa)")

    def test_password_only(self):
        """无 key_path 但有密码 → 密码"""
        from plugins import ssh_runner
        cfg = {"password": "mySecret"}
        label = ssh_runner._auth_label(cfg, has_pw=True)
        self.assertEqual(label, "密码")

    def test_password_only_with_explicit_false_has_pw(self):
        """has_pw=False 时 cfg 里即使意外写了 password 也应回退到无密码分支"""
        # 设计上 cfg.get("password") 与 has_pw 都来自 cfg["password"]，本测试
        # 故意制造分歧场景：has_pw=False 但 cfg 含 password（不可能但当作边界检查）
        from plugins import ssh_runner
        cfg = {"password": "ghost"}
        label = ssh_runner._auth_label(cfg, has_pw=False)
        self.assertEqual(label, "无密码/agent")

    def test_neither(self):
        """key_path 与 password 都无 → 无密码/agent（SSH agent 兜底）"""
        from plugins import ssh_runner
        cfg = {}
        label = ssh_runner._auth_label(cfg, has_pw=False)
        self.assertEqual(label, "无密码/agent")

    def test_empty_key_path_falls_through_to_password(self):
        """key_path 是空字符串（不算设置）→ 走密码分支"""
        from plugins import ssh_runner
        cfg = {"key_path": "", "password": "pw"}
        label = ssh_runner._auth_label(cfg, has_pw=True)
        self.assertEqual(label, "密码")


if __name__ == "__main__":
    unittest.main(verbosity=2)
