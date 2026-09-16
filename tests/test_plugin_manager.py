# -*- coding: utf-8 -*-
"""plugin_manager 单元测试（Day 20.6.12，P0-TEST-2 + P0-TEST-1）

来源：Day 20.6.10 审计 → Day 20.6.11 复核（audit_verification.md）。
事实：tests/ 下没有 test_plugin_manager.py —— `discover_plugins` /
`get_enabled_tools` / `dispatch_tool` **三个本体函数没有任何直接测试**，
只有间接 patch（test_day20_4_automation_first_click.py patch 了 discover_plugins）。
而「插件启用状态被测试写成 []」的配置污染刚在 Day 20.6.6 真实发生过一次，
所以 load_plugin_state 的语义也必须钉死。

本文件用**临时目录里的合成插件**测 discover_plugins，不依赖真实 plugins/ 内容；
dispatch_tool 的异常兜底与「声明了工具却没有 execute」也是本轮新增的加固。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from qwen_app import config, tools, plugin_manager  # noqa: E402

VALID_PLUGIN = '''
PLUGIN_INFO = {"name": "%(name)s", "description": "测试插件", "version": "1.0"}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "%(name)s_tool",
            "description": "测试工具",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "object",
                          "properties": {"y": {"type": "string"}}}
                },
                "required": [],
            },
        },
    },
]


def execute(name, arguments):
    if arguments.get("boom"):
        raise ValueError("插件内部错误")
    return "OK:" + name
'''

SKILL_ONLY_PLUGIN = '''
PLUGIN_INFO = {"name": "%(name)s", "description": "纯技能", "version": "1.0"}
SYSTEM_PROMPT = "你是测试技能。"
'''

NO_INFO_PLUGIN = '''
X = 1
'''

SYNTAX_ERROR_PLUGIN = '''
def broken(:
    pass
'''

TOOLS_WITHOUT_EXECUTE = '''
PLUGIN_INFO = {"name": "%(name)s", "description": "坏插件", "version": "1.0"}
TOOLS = [{"type": "function", "function": {"name": "%(name)s_tool",
          "description": "d", "parameters": {"type": "object", "properties": {}}}}]
'''


class _TmpPluginDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pluginmgr_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, name, template):
        path = os.path.join(self.tmp, name + ".py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(template % {"name": name})
        return path


class TestDiscoverPlugins(_TmpPluginDir):
    def test_loads_valid_plugin(self):
        self.make("alpha", VALID_PLUGIN)
        plugins, infos = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        self.assertIn("alpha", plugins)
        self.assertEqual(infos["alpha"]["name"], "alpha")

    def test_loads_skill_only_plugin(self):
        self.make("skillz", SKILL_ONLY_PLUGIN)
        plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        self.assertIn("skillz", plugins, "只有 SYSTEM_PROMPT 的技能插件也要能加载")

    def test_skips_file_without_plugin_info(self):
        self.make("noinfo", NO_INFO_PLUGIN)
        self.make("alpha", VALID_PLUGIN)
        plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        self.assertNotIn("noinfo", plugins)
        self.assertIn("alpha", plugins)

    def test_syntax_error_does_not_raise(self):
        """坏插件只能被跳过，不能让整个发现流程崩掉"""
        self.make("broken", SYNTAX_ERROR_PLUGIN)
        self.make("alpha", VALID_PLUGIN)
        plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        self.assertIn("alpha", plugins)
        self.assertNotIn("broken", plugins)

    def test_skips_underscore_files(self):
        self.make("_private", VALID_PLUGIN)
        plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        self.assertNotIn("_private", plugins)

    def test_missing_dir_returns_empty(self):
        plugins, infos = plugin_manager.discover_plugins(
            plugin_dir=os.path.join(self.tmp, "nope"))
        self.assertEqual(plugins, {})
        self.assertEqual(infos, {})

    def test_reload_modules_refreshes(self):
        p = self.make("alpha", VALID_PLUGIN)
        a, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        with open(p, "w", encoding="utf-8") as f:
            f.write(VALID_PLUGIN % {"name": "alpha"} + "\nSHOULD_RELOAD = True\n")
        b, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp, reload_modules=True)
        self.assertTrue(getattr(b["alpha"], "SHOULD_RELOAD", False),
                        "reload_modules=True 应重新执行插件文件")


class TestGetEnabledTools(_TmpPluginDir):
    def setUp(self):
        super().setUp()
        self.make("alpha", VALID_PLUGIN)
        self.make("beta", VALID_PLUGIN)
        self.plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)

    def test_only_enabled_plugins(self):
        got = plugin_manager.get_enabled_tools(self.plugins, ["alpha"])
        names = [t["function"]["name"] for t in got]
        self.assertEqual(names, ["alpha_tool"])

    def test_empty_enabled_returns_empty(self):
        self.assertEqual(plugin_manager.get_enabled_tools(self.plugins, []), [])

    def test_unknown_enabled_name_ignored(self):
        got = plugin_manager.get_enabled_tools(self.plugins, ["nope"])
        self.assertEqual(got, [])

    def test_schema_normalization_nested(self):
        """顶层与嵌套 object 都必须补上 additionalProperties（模型才不丢工具）"""
        got = plugin_manager.get_enabled_tools(self.plugins, ["alpha"])
        params = got[0]["function"]["parameters"]
        self.assertIn("additionalProperties", params)
        self.assertIn("additionalProperties", params["properties"]["x"])

    def test_returns_deepcopy(self):
        """返回的工具是副本：调用方改动不得污染插件原 TOOLS"""
        got = plugin_manager.get_enabled_tools(self.plugins, ["alpha"])
        got[0]["function"]["name"] = "tampered"
        self.assertEqual(self.plugins["alpha"].TOOLS[0]["function"]["name"],
                         "alpha_tool")


class TestDispatchTool(_TmpPluginDir):
    def setUp(self):
        super().setUp()
        self.make("alpha", VALID_PLUGIN)
        self.make("beta", VALID_PLUGIN)
        self.make("hollow", TOOLS_WITHOUT_EXECUTE)
        self.plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)

    def test_dispatch_to_owning_plugin(self):
        r = plugin_manager.dispatch_tool(self.plugins, ["alpha", "beta"],
                                         "beta_tool", {})
        self.assertEqual(r, "OK:beta_tool")

    def test_disabled_plugin_not_dispatched(self):
        r = plugin_manager.dispatch_tool(self.plugins, ["alpha"], "beta_tool", {})
        self.assertIn("未知工具", r)

    def test_unknown_tool(self):
        r = plugin_manager.dispatch_tool(self.plugins, ["alpha"], "nope_tool", {})
        self.assertIn("未知工具", r)

    def test_plugin_exception_is_contained(self):
        """插件抛异常必须兜底成错误串，不能打穿到对话线程"""
        r = plugin_manager.dispatch_tool(self.plugins, ["alpha"], "alpha_tool",
                                         {"boom": True})
        self.assertIsInstance(r, str)
        self.assertIn("执行失败", r)
        self.assertIn("ValueError", r)

    def test_tools_without_execute_gives_clear_error(self):
        """绕过 discover 直接塞进 plugins dict（热重载/缓存中间态）时，
        必须给出明确错误，而不是 AttributeError 打穿线程"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "hollow_mod", os.path.join(self.tmp, "hollow.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = plugin_manager.dispatch_tool({"hollow": mod}, ["hollow"], "hollow_tool", {})
        self.assertIn("错误", r)
        self.assertNotIn("Traceback", r)

    def test_tools_without_execute_is_skipped_with_warning(self):
        """坏插件应留下告警，不能"神秘消失"（用户反馈过插件莫名不在列表里）"""
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        self.assertNotIn("hollow", plugins)
        self.assertIn("hollow", buf.getvalue(),
                      "声明了 TOOLS 却没有 execute 的插件必须告警，不能静默丢弃")

    def test_system_prompts_collected(self):
        self.make("skillz", SKILL_ONLY_PLUGIN)
        plugins, _ = plugin_manager.discover_plugins(plugin_dir=self.tmp)
        s = plugin_manager.get_system_prompts(plugins, ["skillz"])
        self.assertIn("测试技能", s)
        self.assertEqual(plugin_manager.get_system_prompts(plugins, []), "")


class TestVersionCompare(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(plugin_manager.compare_versions("1.2.0", "1.2.0"), 0)
        self.assertEqual(plugin_manager.compare_versions("1.10.0", "1.9.0"), 1)
        self.assertEqual(plugin_manager.compare_versions("1.0", "1.0.1"), -1)

    def test_parse_nonstandard(self):
        self.assertEqual(plugin_manager.parse_version("abc"), ())


class TestPluginStateSemantics(unittest.TestCase):
    """Day 20.6.6 配置污染事故的语义护栏（enabled_plugins 被写成 []）"""

    def test_missing_key_means_all_enabled(self):
        with mock.patch.object(config, "load_config", return_value={}):
            names = config.load_plugin_state()
        self.assertEqual(names, config._scan_plugins())
        self.assertTrue(names, "键缺失时必须默认启用全部插件")

    def test_explicit_empty_means_all_disabled(self):
        with mock.patch.object(config, "load_config",
                               return_value={"enabled_plugins": []}):
            self.assertEqual(config.load_plugin_state(), [])

    def test_explicit_list_is_returned_as_is(self):
        with mock.patch.object(config, "load_config",
                               return_value={"enabled_plugins": ["clock"]}):
            self.assertEqual(config.load_plugin_state(), ["clock"])

    def test_save_roundtrip(self):
        with mock.patch.object(config, "load_config", return_value={}), \
                mock.patch.object(config, "save_config") as sc:
            config.save_plugin_state(["clock", "weather"])
        self.assertEqual(sc.call_args[0][0]["enabled_plugins"], ["clock", "weather"])


class TestScanPlugins(unittest.TestCase):
    def test_excludes_private_modules(self):
        """包内私有模块不是插件，不能出现在启用列表（否则设置界面多出幽灵项）"""
        names = config._scan_plugins()
        for n in ("__init__", "_secret_store", "_cmd_blocklist"):
            self.assertNotIn(n, names, f"{n} 不应被视为插件")
        self.assertIn("clock", names)
        self.assertTrue(names)

    def test_config_and_tools_scans_agree(self):
        self.assertEqual(sorted(config._scan_plugins()), sorted(tools._scan_plugins()))

    def test_matches_discoverable_plugins(self):
        """_scan_plugins 列出的名字必须都能被 discover_plugins 真正加载"""
        plugins, _ = plugin_manager.discover_plugins()
        missing = [n for n in config._scan_plugins() if n not in plugins]
        self.assertEqual(missing, [], f"启用列表里有加载不出来的幽灵插件: {missing}")


class TestHighRiskPluginSmoke(unittest.TestCase):
    """P0-TEST-1：只补安全相关的冒烟（其余插件不急着铺测试）"""

    def test_write_file_rejects_traversal(self):
        from plugins import write_file
        r = write_file.execute("write_file",
                               {"filename": "../escape_guard.txt", "content": "x"})
        self.assertIn("错误", r)

    def test_write_file_writes_inside_workspace(self):
        from plugins import write_file
        from qwen_app.workspace import resolve_workspace
        name = "_guard_probe_%d.txt" % os.getpid()
        try:
            r = write_file.execute("write_file", {"filename": name, "content": "hello"})
            self.assertIn("文件已写入", r)
            self.assertTrue(os.path.exists(os.path.join(resolve_workspace(), name)))
        finally:
            p = os.path.join(resolve_workspace(), name)
            if os.path.exists(p):
                os.remove(p)

    def test_code_runner_runs_python(self):
        from plugins import code_runner
        r = code_runner.execute("run_code",
                                {"language": "python", "code": "print(3 * 7)"})
        self.assertIn("21", r)

    def test_shell_runner_runs_echo(self):
        from plugins import shell_runner
        r = shell_runner.execute("run_command", {"command": "echo guard_probe"})
        self.assertIn("guard_probe", r)

    def test_shell_runner_rejects_blocked(self):
        from plugins import shell_runner
        r = shell_runner.execute("run_command", {"command": "rm -rf /"})
        self.assertIn("拒绝", r)


if __name__ == "__main__":
    unittest.main()
