# -*- coding: utf-8 -*-
"""Day 20.6.12 元测试：全插件加载契约（P0-TEST-3）

来源：Day 20.6.10 全量审计 + Day 20.6.11 复核（tests/artifacts/audit_verification.md）
      P0-TEST-3「无全插件 import 元测试」—— 复核认定为真 P0，且是性价比最高的一项。

为什么必须补：
  plugin_manager.discover_plugins() 对插件加载失败是**静默跳过**（只 print 一行日志），
  所以「插件拖进目录但 import 就崩」在生产里的表现是「工具突然消失」，
  既无告警也无异常冒泡。此前 25 个插件没有任何一个被测试真正 import 过
  （审计确认 docx/pptx/xlsx 在 tests/ 里只是报告内的子串，没有真实 from plugins import）。

本测试直接遍历 plugins/*.py 逐个独立加载，**异常不吞没**：
  1. import 契约 —— 每个插件文件都能 exec_module 成功
  2. PLUGIN_INFO 契约 —— name/description/version 齐全且非空
  3. 能力契约 —— 至少声明 TOOLS 或 SYSTEM_PROMPT（否则加载了也没用）
  4. 工具契约 —— 有 TOOLS 必须有可调用 execute；每个 tool 定义合规
  5. 工具名全局唯一 —— dispatch_tool 按名字线性匹配，重名会静默错派
  6. schema 规范化 —— get_enabled_tools 对全部插件都不炸且补齐 additionalProperties
  7. 与生产路径一致 —— discover_plugins 不得静默跳过任何一个插件
"""
import ast
import os
import sys
import unittest
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(ROOT, "plugins")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _plugin_names():
    if not os.path.isdir(PLUGINS_DIR):
        return []
    return sorted(
        f[:-3] for f in os.listdir(PLUGINS_DIR)
        if f.endswith(".py") and not f.startswith("_")
    )


def _load(name):
    """独立加载插件文件；异常直接抛出 —— 这正是本测试的意义所在"""
    full = os.path.join(PLUGINS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("meta_" + name, full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPluginLoadingContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = _plugin_names()
        cls.mods = {}
        cls.errors = {}
        for n in cls.names:
            try:
                cls.mods[n] = _load(n)
            except Exception as e:            # noqa: BLE001 — 故意抓全部，集中报错
                cls.errors[n] = f"{type(e).__name__}: {e}"
        try:
            from qwen_app import plugin_manager
            cls.pm = plugin_manager
        except Exception as e:                # noqa: BLE001
            cls.pm = None
            cls.pm_error = e

    def test_plugin_dir_not_empty(self):
        self.assertTrue(self.names, "plugins/ 下没有发现任何插件文件")

    def test_every_plugin_imports(self):
        """核心断言：任何插件 import 失败都必须让测试变红，而不是被静默跳过"""
        if self.errors:
            detail = "\n".join(f"  - {k}: {v}" for k, v in sorted(self.errors.items()))
            self.fail(f"{len(self.errors)} 个插件加载失败：\n{detail}")

    def test_no_relative_imports(self):
        """插件不得使用包内相对导入 —— 在插件里必然 ImportError

        plugin_manager 用 spec_from_file_location 以独立模块名（plugin_xxx）
        加载插件，模块 __package__ 为空字符串，`from . import x` 会抛
        "attempted relative import with no known parent package"。
        Day 20.6.12 实测发现 ssh_runner / sql_helper 的 `from . import _secret_store`
        正因如此静默失效（连接配置不写盘、keyring 从未生效），故用 AST 钉死，
        防止后人再写回相对导入。
        """
        bad = []
        for n in self.names:
            full = os.path.join(PLUGINS_DIR, n + ".py")
            with open(full, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=full)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0:
                    dots = "." * node.level
                    bad.append(f"{n}.py:{node.lineno}: from {dots}{node.module or ''} import ...")
        self.assertEqual(
            bad, [],
            "插件不得使用包内相对导入（加载时 __package__ 为空，必然 ImportError）：\n  "
            + "\n  ".join(bad),
        )

    def test_plugin_info_contract(self):
        for n in self.names:
            mod = self.mods.get(n)
            if mod is None:
                continue
            with self.subTest(plugin=n):
                info = getattr(mod, "PLUGIN_INFO", None)
                self.assertIsInstance(info, dict, f"{n} 缺少 PLUGIN_INFO")
                for key in ("name", "description", "version"):
                    self.assertTrue(
                        str(info.get(key, "")).strip(),
                        f"{n}.PLUGIN_INFO 缺少非空字段 {key}",
                    )

    def test_declares_capability(self):
        """至少声明 TOOLS 或 SYSTEM_PROMPT，否则插件被 discover 时会被丢弃"""
        for n in self.names:
            mod = self.mods.get(n)
            if mod is None:
                continue
            with self.subTest(plugin=n):
                self.assertTrue(
                    hasattr(mod, "TOOLS") or hasattr(mod, "SYSTEM_PROMPT"),
                    f"{n} 既无 TOOLS 也无 SYSTEM_PROMPT，加载后无任何作用",
                )

    def test_tools_contract(self):
        """有工具声明的插件必须真能分派：TOOLS 非空 ⇒ execute 必须可调用

        注：本项目纯 Skill 插件（code_review / code_explain / code_optimizer /
        debug_helper / translator）统一用 `TOOLS = []` + `execute = None` 占位，
        只提供 SYSTEM_PROMPT。空 TOOLS 不会产生任何模型可见工具，
        dispatch_tool 也永远匹配不到，故这类插件不要求 execute。
        """
        for n in self.names:
            mod = self.mods.get(n)
            if mod is None:
                continue
            with self.subTest(plugin=n):
                tools = getattr(mod, "TOOLS", None)
                if not tools:
                    continue          # 纯 Skill 插件：无工具，自然无需 execute
                self.assertTrue(
                    callable(getattr(mod, "execute", None)),
                    f"{n} 声明了 {len(tools)} 个工具但没有可调用的 execute —— "
                    f"模型调用其工具时 dispatch_tool 会抛 AttributeError",
                )
                self.assertIsInstance(tools, list, f"{n}.TOOLS 必须是 list")
                for t in tools:
                    self.assertIsInstance(t, dict, f"{n} 的工具定义必须是 dict")
                    fn = t.get("function", {})
                    tname = fn.get("name")
                    self.assertTrue(tname, f"{n} 有工具缺少 function.name")
                    self.assertTrue(fn.get("description"),
                                    f"{n}.{tname} 缺少 description（模型靠它决定是否调用）")
                    params = fn.get("parameters")
                    self.assertIsInstance(params, dict, f"{n}.{tname} 缺少 parameters")
                    self.assertEqual(
                        params.get("type"), "object",
                        f"{n}.{tname} parameters.type 必须是 object",
                    )

    def test_tool_names_globally_unique(self):
        """dispatch_tool 按工具名线性匹配 → 重名会分派到先命中的插件（静默错派）"""
        seen = {}
        for n in sorted(self.names):
            mod = self.mods.get(n)
            if mod is None:
                continue
            for t in getattr(mod, "TOOLS", []):
                tname = t.get("function", {}).get("name")
                self.assertNotIn(
                    tname, seen,
                    f"工具名重复: {tname}（{seen.get(tname)} 与 {n}）—— 会被静默分派到前者",
                )
                seen[tname] = n
        self.assertTrue(seen, "全部插件加起来没有任何工具定义")

    def test_schema_normalization_safe(self):
        """每个插件的 schema 都能被规范化，且规范化确实补齐 additionalProperties"""
        if self.pm is None:
            self.skipTest("plugin_manager 不可用")
        tools = self.pm.get_enabled_tools(dict(self.mods), sorted(self.mods))
        self.assertTrue(tools, "get_enabled_tools 返回空列表")
        for t in tools:
            params = t["function"]["parameters"]
            self.assertEqual(params.get("type"), "object")
            self.assertIn(
                "additionalProperties", params,
                f"{t['function']['name']} 规范化后仍缺 additionalProperties",
            )

    def test_discover_plugins_matches_direct_load(self):
        """生产路径（discover_plugins）不得静默跳过任何插件"""
        if self.pm is None:
            self.skipTest("plugin_manager 不可用")
        plugins, infos = self.pm.discover_plugins(plugin_dir=PLUGINS_DIR)
        missing = [n for n in self.names if n not in plugins]
        self.assertEqual(missing, [], f"discover_plugins 静默跳过了: {missing}")
        for n in self.names:
            mod = self.mods.get(n)
            if mod is None:
                continue
            if hasattr(mod, "TOOLS") or hasattr(mod, "SYSTEM_PROMPT"):
                self.assertIn(n, infos, f"{n} 未被 discover_plugins 收录")


if __name__ == "__main__":
    unittest.main()
