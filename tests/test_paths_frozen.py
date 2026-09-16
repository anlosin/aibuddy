# -*- coding: utf-8 -*-
"""Day 20.6.13 护栏：打包态路径解析必须把「只读资源」与「可写数据」分开。

为什么这是硬约束：
    项目原先所有路径都由 ``__file__`` 推导。源码态没问题；但 PyInstaller 打包后
    ``__file__`` 指向解包目录 ``sys._MEIPASS``：
      · onefile —— _MEIPASS 是临时目录，**进程退出即删** → 用户的 API Key、
        会话库、自动化任务每次启动归零；
      · onedir —— 数据写进程序目录，装到 Program Files 下无写权限。
    所以打包态的 ``data_dir()`` 必须落到 **exe 同级**（便携），
    ``resource_dir()`` 才允许指向 ``_MEIPASS``。

这里用 monkeypatch 模拟 ``sys.frozen`` / ``sys.executable`` / ``sys._MEIPASS``
来验证冻结分支（不需要真的打包），并覆盖「exe 目录不可写 → 回退 %APPDATA%」
这条兜底路径 —— 那条路径最容易在真机上才暴露。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qwen_app import paths  # noqa: E402


class _FakeFrozen:
    """把解释器伪装成 PyInstaller 产物。"""

    def __init__(self, exe_dir, meipass):
        self.exe_dir = exe_dir
        self.meipass = meipass

    def __enter__(self):
        self._saved = (getattr(sys, "frozen", None), sys.executable,
                       getattr(sys, "_MEIPASS", None),
                       os.environ.get("APPDATA"))
        # sys.executable 指向 <exe_dir>/qwen.exe
        sys.executable = os.path.join(self.exe_dir, "qwen.exe")
        sys.frozen = True
        sys._MEIPASS = self.meipass
        paths._reset_cache_for_tests()
        return self

    def __exit__(self, *exc):
        frozen, exe, meipass, appdata = self._saved
        if frozen is None:
            sys.__dict__.pop("frozen", None)
        else:
            sys.frozen = frozen
        sys.executable = exe
        if meipass is None:
            sys.__dict__.pop("_MEIPASS", None)
        else:
            sys._MEIPASS = meipass
        if appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = appdata
        paths._reset_cache_for_tests()


class TestDevModePaths(unittest.TestCase):
    """源码态必须与打包改造**完全一致**（不能因为支持打包而挪了开发目录）"""

    def test_not_frozen(self):
        self.assertFalse(paths.is_frozen())

    def test_data_dir_is_project_data(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(paths.data_dir(), os.path.join(root, "data"))

    def test_resource_dir_is_project_root(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(paths.resource_dir(), root)

    def test_workspaces_base_keeps_legacy_location(self):
        """源码态工作区必须仍在 <项目根>/.workbuddy/workspaces（历史位置）"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(paths.workspaces_base(),
                         os.path.join(root, ".workbuddy", "workspaces"))

    def test_external_plugins_is_project_plugins(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(paths.external_plugins_dir(), os.path.join(root, "plugins"))
        self.assertEqual(paths.plugins_dir(), os.path.join(root, "plugins"))


class TestFrozenModePaths(unittest.TestCase):
    """打包态：可写数据走 exe 同级；只读资源走 _MEIPASS"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qwen_frozen_")
        self.exe_dir = os.path.join(self.tmp, "app")
        self.res_dir = os.path.join(self.tmp, "app", "_internal")
        os.makedirs(self.exe_dir, exist_ok=True)
        os.makedirs(os.path.join(self.res_dir, "plugins"), exist_ok=True)
        os.makedirs(os.path.join(self.res_dir, "qwen_app", "qml"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_data_dir_goes_next_to_exe_not_meipass(self):
        with _FakeFrozen(self.exe_dir, self.res_dir):
            self.assertTrue(paths.is_frozen())
            d = paths.data_dir()
            self.assertEqual(d, os.path.join(self.exe_dir, "data"))
            self.assertFalse(
                d.startswith(self.res_dir),
                "数据目录绝不能落在 _MEIPASS（临时解包目录，进程退出即删）")

    def test_resource_dir_is_meipass(self):
        with _FakeFrozen(self.exe_dir, self.res_dir):
            self.assertEqual(paths.resource_dir(), self.res_dir)
            self.assertTrue(os.path.isdir(
                os.path.join(paths.resource_dir(), "qwen_app", "qml")))

    def test_workspaces_base_is_writable_location(self):
        with _FakeFrozen(self.exe_dir, self.res_dir):
            wb = paths.workspaces_base()
            self.assertFalse(wb.startswith(self.res_dir))
            self.assertTrue(wb.startswith(os.path.join(self.exe_dir, "data")))

    def test_env_override_wins(self):
        target = os.path.join(self.tmp, "custom_data")
        os.environ["QWEN_DATA_DIR"] = target
        try:
            with _FakeFrozen(self.exe_dir, self.res_dir):
                self.assertEqual(paths.data_dir(), os.path.abspath(target))
        finally:
            os.environ.pop("QWEN_DATA_DIR", None)

    def test_fallback_to_appdata_when_exe_dir_readonly(self):
        """exe 目录不可写（如装在 Program Files）→ 必须回退 %APPDATA%\\qwen"""
        fake_appdata = os.path.join(self.tmp, "AppData")
        os.makedirs(fake_appdata, exist_ok=True)
        os.environ["APPDATA"] = fake_appdata
        with _FakeFrozen(self.exe_dir, self.res_dir):
            # 让可写性探测失败
            orig = paths._dir_writable
            paths._dir_writable = lambda _p: False
            try:
                d = paths.data_dir()
            finally:
                paths._dir_writable = orig
            self.assertEqual(d, os.path.join(fake_appdata, "qwen"))


class TestExternalPluginsRelease(unittest.TestCase):
    """打包态首启动要把内置插件模板释放到 exe 同级（用户可改 + 热重载）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qwen_rel_")
        self.exe_dir = os.path.join(self.tmp, "app")
        self.res_dir = os.path.join(self.tmp, "app", "_internal")
        builtin = os.path.join(self.res_dir, "plugins")
        os.makedirs(builtin, exist_ok=True)
        with open(os.path.join(builtin, "demo_plugin.py"), "w", encoding="utf-8") as fh:
            fh.write("PLUGIN_INFO = {}\nTOOLS = []\n")
        with open(os.path.join(builtin, "_secret_store.py"), "w", encoding="utf-8") as fh:
            fh.write("# helper\n")
        os.makedirs(os.path.join(builtin, "k8s_templates"), exist_ok=True)
        with open(os.path.join(builtin, "k8s_templates", "d.yaml"), "w",
                  encoding="utf-8") as fh:
            fh.write("kind: Deployment\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_release_copies_tree_and_is_idempotent(self):
        with _FakeFrozen(self.exe_dir, self.res_dir):
            n, dest = paths.ensure_external_plugins()
            self.assertGreater(n, 0)
            ext = paths.external_plugins_dir()
            self.assertEqual(dest, ext)
            self.assertTrue(os.path.isfile(os.path.join(ext, "demo_plugin.py")))
            self.assertTrue(os.path.isfile(os.path.join(ext, "_secret_store.py")))
            self.assertTrue(os.path.isfile(
                os.path.join(ext, "k8s_templates", "d.yaml")))

            # 生效目录切到外部（用户能改、watcher 才能监视到）
            self.assertEqual(paths.plugins_dir(), ext)

            # 幂等：第二次不应再复制
            n2, _ = paths.ensure_external_plugins()
            self.assertEqual(n2, 0)

            # 用户改过的文件不能被内置模板覆盖
            target = os.path.join(ext, "demo_plugin.py")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("# user edited\n")
            paths.ensure_external_plugins()
            with open(target, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "# user edited\n")


class TestScanPluginNames(unittest.TestCase):
    """插件名扫描：私有模块必须排除，与 config/tools 同源"""

    def test_excludes_private_but_keeps_public(self):
        names = paths.scan_plugin_names()
        self.assertNotIn("__init__", names)
        self.assertNotIn("_secret_store", names)
        self.assertNotIn("_cmd_blocklist", names)
        self.assertIn("write_file", names)
        self.assertIn("shell_runner", names)

    def test_config_and_tools_agree(self):
        from qwen_app import config, tools
        self.assertEqual(config._scan_plugins(), tools._scan_plugins())
        self.assertEqual(config._scan_plugins(), paths.scan_plugin_names())


if __name__ == "__main__":
    unittest.main(verbosity=2)
