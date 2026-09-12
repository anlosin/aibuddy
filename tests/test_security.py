"""Day 18 安全修复回归测试 — C3 (read_text_file) + C4 (web_fetch SSRF TOCTOU)。"""
import os
import socket
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestReadTextFileSecurity(unittest.TestCase):
    """C3: read_text_file 拒绝绝对路径 + 拒绝非白名单扩展名。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication(sys.argv)
        from qwen_app.chat_bridge import ChatBridge
        cls.bridge = ChatBridge(theme="light")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp)
        # 写一些文件
        self._write("hello.py", "print('hi')")
        self._write("ssh_key", "-----BEGIN RSA PRIVATE KEY-----")
        self._write("evil.exe", b"MZ\x00\x00")
        self._write("notes.md", "# notes")
        self._write("config.json", "{}")
        self._write("no_ext", "x")

    def tearDown(self):
        os.chdir(self._old_cwd)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        with open(os.path.join(self.tmp, name), "w" if isinstance(content, str) else "wb") as f:
            f.write(content)

    def test_accepts_whitelisted_extension(self):
        r = self.bridge.read_text_file("hello.py")
        self.assertIn("print", r)

    def test_accepts_md(self):
        r = self.bridge.read_text_file("notes.md")
        self.assertIn("# notes", r)

    def test_accepts_json(self):
        r = self.bridge.read_text_file("config.json")
        self.assertIn("{}", r)

    def test_rejects_no_extension(self):
        r = self.bridge.read_text_file("no_ext")
        self.assertIn("不支持的文件类型", r)

    def test_rejects_ssh_key(self):
        # 没有扩展名的 ssh_key 即使名字里不含白名单扩展 → 拒绝
        r = self.bridge.read_text_file("ssh_key")
        self.assertIn("不支持的文件类型", r)
        self.assertNotIn("BEGIN", r)              # 关键：内容不能泄漏

    def test_rejects_exe(self):
        r = self.bridge.read_text_file("evil.exe")
        self.assertIn("不支持的文件类型", r)

    def test_rejects_absolute_path(self):
        abs_py = os.path.join(self.tmp, "hello.py")
        r = self.bridge.read_text_file(abs_py)
        self.assertIn("不支持绝对路径", r)
        self.assertNotIn("print", r)               # 关键：内容不能泄漏

    def test_empty_returns_empty(self):
        self.assertEqual(self.bridge.read_text_file(""), "")

    def test_nonexistent_whitelisted(self):
        r = self.bridge.read_text_file("nonexistent.py")
        self.assertIn("[读文件失败", r)

    def test_absolute_path_arbitrary_sensitive_blocked(self):
        """核心：直接传 ~/.ssh/id_rsa 这种绝对路径必须被拒绝（C3 修前会读出私钥）"""
        for sensitive in [
            "C:/Users/admin/.ssh/id_rsa",
            "/etc/passwd",
            "C:/Windows/System32/drivers/etc/hosts",
        ]:
            with self.subTest(sensitive=sensitive):
                r = self.bridge.read_text_file(sensitive)
                self.assertIn("不支持绝对路径", r)
                # 关键：内容绝不泄漏
                self.assertNotIn("BEGIN", r)
                self.assertNotIn("root:", r)


class TestWebFetchSSRFProtection(unittest.TestCase):
    """C4: web_fetch._validate_and_resolve 必须阻止内网/保留地址；并返回具体绑定 IP。"""

    def test_rejects_loopback_ip(self):
        from plugins.web_fetch import _validate_and_resolve
        for bad in ["http://127.0.0.1/admin", "http://localhost:8080/", "http://[::1]/"]:
            with self.assertRaises(ValueError, msg=f"必须拦截: {bad}"):
                _validate_and_resolve(bad)

    def test_rejects_private_ip(self):
        from plugins.web_fetch import _validate_and_resolve
        for bad in [
            "http://10.0.0.1/", "http://172.16.0.1/", "http://192.168.1.1/",
            "http://169.254.169.254/",                    # AWS metadata
            "http://0.0.0.0/",
        ]:
            with self.assertRaises(ValueError, msg=f"必须拦截: {bad}"):
                _validate_and_resolve(bad)

    def test_rejects_non_http_scheme(self):
        from plugins.web_fetch import _validate_and_resolve
        for bad in ["file:///etc/passwd", "ftp://example.com/", "gopher://example.com/"]:
            with self.assertRaises(ValueError, msg=f"必须拦截: {bad}"):
                _validate_and_resolve(bad)

    def test_rejects_domain_resolving_to_private_ip(self):
        """模拟域名解析到内网 IP：mock socket.getaddrinfo 返回 127.0.0.1"""
        from plugins import web_fetch

        def fake_getaddrinfo(host, port, *a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 80))]

        orig = socket.getaddrinfo
        socket.getaddrinfo = fake_getaddrinfo
        try:
            with self.assertRaises(ValueError, msg="域名解析到内网 IP 必须拦截"):
                web_fetch._validate_and_resolve("http://attacker.com/")
        finally:
            socket.getaddrinfo = orig

    def test_rejects_ipv4_mapped_ipv6_loopback(self):
        """IPv4-mapped IPv6 (::ffff:127.0.0.1) 是常见绕过，单独检查"""
        from plugins import web_fetch

        def fake_getaddrinfo(host, port, *a, **kw):
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                     ("::ffff:127.0.0.1", port or 80, 0, 0))]

        orig = socket.getaddrinfo
        socket.getaddrinfo = fake_getaddrinfo
        try:
            with self.assertRaises(ValueError, msg="IPv4-mapped IPv6 loopback 必须拦截"):
                web_fetch._validate_and_resolve("http://attacker.com/")
        finally:
            socket.getaddrinfo = orig

    def test_public_ip_returns_binding_ip(self):
        """合法公网 IP 应返回具体 IP 用于 socket 绑定（防 TOCTOU）"""
        from plugins.web_fetch import _validate_and_resolve
        host, port, ip = _validate_and_resolve("http://1.1.1.1/")
        self.assertEqual(host, "1.1.1.1")
        self.assertEqual(port, 80)
        self.assertEqual(ip, "1.1.1.1")

    def test_public_domain_resolves_to_public_ip(self):
        from plugins import web_fetch

        def fake_getaddrinfo(host, port, *a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port or 80))]

        orig = socket.getaddrinfo
        socket.getaddrinfo = fake_getaddrinfo
        try:
            host, port, ip = web_fetch._validate_and_resolve("http://example.com/")
            self.assertEqual(host, "example.com")
            self.assertEqual(ip, "8.8.8.8")              # 返回绑定用 IP（防 TOCTOU）
        finally:
            socket.getaddrinfo = orig

    def test_no_redirect_follow(self):
        """注释/代码层确认：不跟随重定向（避免 30x 跳到内网）"""
        import inspect
        from plugins import web_fetch
        src = inspect.getsource(web_fetch._ip_safe_fetch)
        # 自实现的 _ip_safe_fetch 不应包含自动重定向逻辑
        self.assertNotIn("Location", src)
        self.assertNotIn("301", src)
        self.assertNotIn("302", src)


if __name__ == "__main__":
    unittest.main()
