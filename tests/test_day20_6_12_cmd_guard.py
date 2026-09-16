# -*- coding: utf-8 -*-
"""Day 20.6.12 护栏：命令黑名单同源 + 补漏 + 代码执行入口一并受检

来源：Day 20.6.10 审计 → Day 20.6.11 复核（audit_verification.md）
      P0-SEC-1（黑名单可绕过）、P0-SEC-2（run_python/run_code 零检查）、
      P0-SEC-5（ssh 黑名单弱于 shell，且注释谎称一致）。

复核结论（决定了本文件的断言取向）：
  * 事实成立，但**不采纳「重构成白名单」的建议** —— 白名单会让
    git / pip / docker / kubectl 全部不可用，等于废掉本插件的核心能力。
    黑名单是「尽力而为的护栏」，目标是 ① 覆盖已知具体绕过 ② 零误伤。
  * ssh_runner 的注释写着「与 shell_runner 保持一致」而实际只有 17 条，
    缺 Day 18 (H7) 全部加固 —— **注释说谎本身必须修**，故断言两边
    `BLOCKED_PATTERNS` 是**同一个对象**（物理上不可能再漂移）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins import shell_runner, ssh_runner, code_runner  # noqa: E402
from plugins import _cmd_blocklist                          # noqa: E402

# 本次修复新增拦截的具体绕过写法（审计逐条列出的漏点）
NEWLY_BLOCKED = [
    ("rm -rf ..", "越界到上级目录"),
    ("rm -rf ../..", "越界两级"),
    ("rm -rf C:\\", "Windows 盘符根"),
    ("rm -rf C:/Windows", "Windows 系统目录"),
    ("rm -rf .", "递归删除当前目录（等于清空工作区）"),
    ("rm -rf ./", "同上，带斜杠写法"),
    ("dd of=/dev/sda", "dd 写设备（原正则要求先有 if=）"),
    ("dd if=/dev/zero of=/dev/sdb bs=1M", "dd 带 if= 写设备"),
    (": > /etc/passwd", "重定向覆盖系统文件"),
    ("echo x > /etc/hosts", "覆盖系统目录下文件"),
    ("echo x >> /var/log/syslog", "追加写系统目录文件"),
    ("chmod 000 secret.txt", "权限清零"),
    ("chmod -R 000 dir", "递归权限清零"),
    ("chmod 777 /", "根目录放开权限"),
    ("chown -R nobody /", "根目录改属主"),
    ("chgrp -R nogroup /", "根目录改组"),
    ("mv shadow /etc/shadow", "移动到系统目录"),
    ('cmd /c "dir"', "cmd /c（原正则写死 exe?，其实从未拦到）"),
    ("cmd.exe /c dir", "cmd.exe /c"),
    ("cmd /k dir", "cmd /k 同样会执行命令"),
    # Day 18 (H7) 加固项：ssh 侧此前全缺，现必须同样拦得住
    ('python -c "import os; os.system(\'rm -rf /\')"', "python -c 嵌套执行器"),
    ("pwsh -EncodedCommand ZQBjAGgAbwAgACIAdgBhAGwAdQBlACIA", "PowerShell EncodedCommand"),
    ('cmd /c "rd /s /q C:\\Windows"', "cmd /c 嵌套执行器"),
    ('node -e "require(\'child_process\').exec(\'x\')"', "node -e"),
    ("for f in /var/log/*; do rm -rf $f & done", "for/do 后台并行 rm"),
    ("xargs -P 8 rm", "xargs -P 并行 rm"),
]

# 必须零误伤（日常命令 + 工作区内正常清理）
STILL_ALLOWED = [
    "ls -la",
    "git status",
    "pip install requests",
    "python script.py",
    'python -c "print(1)"',
    'python3 -c "import json; print(json.dumps({\'a\':1}))"',
    "echo hello && pwd",
    "cat /etc/hosts",
    "df -h",
    "docker ps",
    "kubectl get pods -A",
    "rm -rf build/",
    "rm -rf ./dist",
    "rm -rf dist",
    "rm -f stale.txt",
    "mv a.txt b.txt",
    "mv a.txt /tmp/b.txt",
    "chmod 644 file.txt",
    "chmod +x run.sh",
    "mvn -v",
]


class TestBlocklistShared(unittest.TestCase):
    def test_same_object(self):
        """ssh 与 shell 的黑名单必须是**同一个对象**（注释说谎的结构性修复）"""
        self.assertIs(
            shell_runner.BLOCKED_PATTERNS, ssh_runner.BLOCKED_PATTERNS,
            "两个插件的黑名单不是同一份 —— 注释「保持一致」又会变成谎言",
        )
        self.assertIs(shell_runner.BLOCKED_PATTERNS, _cmd_blocklist.BLOCKED_PATTERNS)

    def test_parity_shell_vs_ssh(self):
        """同一命令在两个入口必须得到一致的拦截结论"""
        for cmd in [c for c, _ in NEWLY_BLOCKED] + STILL_ALLOWED:
            with self.subTest(cmd=cmd):
                a = shell_runner._refuse_if_blocked(cmd)
                b = ssh_runner._refuse_if_blocked(cmd)
                self.assertEqual(a is None, b is None,
                                 f"shell 与 ssh 结论不一致: {cmd!r}")

    def test_day18_hardening_present(self):
        """ssh 侧必须包含 Day 18 (H7) 的全部加固（此前缺失）"""
        for cmd in ('python -c "import os; os.system(\'rm -rf /\')"',
                    'pwsh -EncodedCommand AAAA',
                    'cmd /c "dir"',
                    'node -e "1"',
                    'perl -e "1"',
                    'ruby -e "1"'):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(ssh_runner._refuse_if_blocked(cmd),
                                     f"ssh 未拦 Day18 加固项: {cmd}")


class TestNewlyBlocked(unittest.TestCase):
    def test_all_new_gaps_blocked(self):
        """审计列出的具体绕过写法必须全部拦下（不加白名单重构）"""
        for cmd, why in NEWLY_BLOCKED:
            with self.subTest(cmd=cmd, why=why):
                self.assertIsNotNone(
                    shell_runner._refuse_if_blocked(cmd),
                    f"应拦未拦: {cmd!r}（{why}）",
                )

    def test_no_false_positive(self):
        """日常命令与工作区内清理必须放行（黑名单的误伤红线）"""
        for cmd in STILL_ALLOWED:
            with self.subTest(cmd=cmd):
                r = shell_runner._refuse_if_blocked(cmd)
                self.assertIsNone(r, f"误伤正常命令: {cmd!r}\n拒绝原因: {r}")


class TestCodeExecutionEntrypoints(unittest.TestCase):
    """P0-SEC-2：run_python / run_code 不能再是零检查通道"""

    def test_run_python_checks_code(self):
        r = shell_runner.execute("run_python", {"code": 'import os; os.system("rm -rf /")'})
        self.assertIn("拒绝", r, "run_python 未对代码文本做黑名单筛查")
        self.assertNotIn("退出码", r, "代码不应真的被执行")

    def test_run_code_checks_code_python(self):
        r = code_runner.execute("run_code", {
            "language": "python", "code": 'import os; os.system("rm -rf /")'})
        self.assertIn("拒绝", r, "run_code(python) 未做黑名单筛查")

    def test_run_code_checks_code_java(self):
        r = code_runner.execute("run_code", {
            "language": "java",
            "code": 'class Main { void f() throws Exception {'
                    ' Runtime.getRuntime().exec("rm -rf /"); } }'})
        self.assertIn("拒绝", r, "run_code(java) 未做黑名单筛查")

    def test_safe_code_still_runs(self):
        """正常代码不能被误杀（真跑一次，验证没有被一刀切拦住）"""
        r = shell_runner.execute("run_python", {"code": "print(6 * 7)"})
        self.assertIn("42", r)

    def test_normal_command_still_runs(self):
        r = shell_runner.execute("run_command", {"command": "echo hello_guard"})
        self.assertIn("hello_guard", r)


class TestSshHostKeyDefault(unittest.TestCase):
    """P0-SEC-4：默认不信任未知主机密钥（消除 MITM 默认敞口）"""

    def _schema_default(self):
        for t in ssh_runner.TOOLS:
            fn = t["function"]
            if fn["name"] == "ssh_connect":
                return fn["parameters"]["properties"]["auto_add_host_key"]
        self.fail("ssh_connect 工具定义缺失")

    def test_schema_default_false(self):
        prop = self._schema_default()
        self.assertIs(prop.get("default"), False,
                      "ssh_connect 的 auto_add_host_key 默认值必须为 False")
        self.assertIn("false", prop.get("description", ""),
                      "描述里要讲清默认值与风险")

    def test_code_default_false(self):
        """__init__/code 侧默认值也必须是 False（不能只在 schema 里改）"""
        import inspect
        src = inspect.getsource(ssh_runner)
        self.assertNotIn('cfg.get("auto_add_host_key", True)', src,
                         "_connect 仍在默认信任未知主机密钥")
        self.assertNotIn('args.get("auto_add_host_key", True)', src,
                         "_do_connect 仍在默认信任未知主机密钥")


if __name__ == "__main__":
    unittest.main()
