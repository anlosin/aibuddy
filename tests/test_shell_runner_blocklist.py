"""Day 18 shell_runner 黑名单加固回归测试 — H7。"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


class TestShellRunnerBlocklistHardening(unittest.TestCase):
    """H7: 黑名单必须能拦截新发现的绕过手法。"""

    def setUp(self):
        from plugins import shell_runner
        self.runner = shell_runner

    def assertBlocked(self, cmd, hint=""):
        result = self.runner._refuse_if_blocked(cmd)
        self.assertIsNotNone(
            result,
            "应该被拦截但没拦: %s%s" % (cmd, "  " + hint if hint else "")
        )

    def assertNotBlocked(self, cmd, hint=""):
        result = self.runner._refuse_if_blocked(cmd)
        self.assertIsNone(
            result,
            "应该被允许但被拦截了: %s%s\n拒绝原因: %s"
            % (cmd, "  " + hint if hint else "", result or "")
        )

    def test_python_c_with_os_system_blocked(self):
        self.assertBlocked(
            'python -c "import os; os.system(\'rm -rf /\')"',
            "经典 python -c 嵌套")

    def test_python_c_with_subprocess_blocked(self):
        self.assertBlocked(
            'python3 -c "import subprocess; subprocess.call([\'rm\',\'-rf\',\'/\'])"',
            "python -c 调 subprocess")

    def test_powershell_command_blocked(self):
        self.assertBlocked(
            'powershell -Command "Remove-Item -Recurse -Force C:\\"',
            "PowerShell 嵌套执行器")

    def test_powershell_encoded_command_blocked(self):
        self.assertBlocked(
            'pwsh -EncodedCommand ZQBjAGgAbwAgACIAdgBhAGwAdQBlACIA',
            "PowerShell EncodedCommand（Base64 攻击）")

    def test_cmd_c_blocked(self):
        self.assertBlocked('cmd /c "rd /s /q C:\\Windows"',
                           "cmd /c 嵌套执行器")

    def test_node_eval_blocked(self):
        self.assertBlocked('node -e "require(\'child_process\').exec(\'rm -rf /\')"',
                           "node -e 调 child_process")

    def test_ruby_perl_eval_blocked(self):
        self.assertBlocked('ruby -e "system \'rm -rf /\'"',
                           "ruby -e 调 system")
        self.assertBlocked('perl -e "system(\'rm -rf /\')"',
                           "perl -e 调 system")

    def test_parallel_rm_blocked(self):
        """模型常用 for/do & 加速清理，但并行删也是绕过"""
        self.assertBlocked(
            'for f in /var/log/*; do rm -rf $f & done',
            "for 循环后台并行 rm")

    def test_while_loop_rm_blocked(self):
        self.assertBlocked(
            'while read p; do rm -rf $p; done < /tmp/list',
            "while 循环 rm")

    def test_legitimate_commands_still_work(self):
        """不能误伤正常命令"""
        self.assertNotBlocked('ls -la', "基础 ls")
        self.assertNotBlocked('git status', "git 状态")
        self.assertNotBlocked('pip install requests', "pip 安装")
        self.assertNotBlocked('python script.py', "python 跑脚本（无 -c）")
        self.assertNotBlocked('python -c "print(1)"', "python -c 安全调用")
        self.assertNotBlocked('echo hello && pwd', "echo 串接")
        self.assertNotBlocked('cat /etc/hosts', "读文件")

    def test_safe_python_eval_not_blocked(self):
        """python -c 做安全计算（无 os.system / subprocess）应被允许"""
        self.assertNotBlocked('python -c "print(2 + 2)"')
        self.assertNotBlocked('python3 -c "import json; print(json.dumps({\'a\':1}))"')


if __name__ == "__main__":
    unittest.main()
