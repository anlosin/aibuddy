"""Day 18 中风险修复 + 死代码清理回归测试 — M4 (compressor timeout) + L4 (死代码) + M5 (QML Shortcut scope)。"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestCompressorTimeout(unittest.TestCase):
    """M4: compressor 必须显式传 timeout，否则长对话压缩会冻 GUI。"""

    def test_compress_calls_with_timeout(self):
        """压缩 API 调用必须带 timeout 参数（不能依赖客户端默认 600 秒）"""
        import inspect
        from qwen_app import compressor
        src = inspect.getsource(compressor.ConversationCompressor._generate_first_summary)
        self.assertIn("timeout", src, "必须显式传 timeout 给 openai 客户端")
        # 必须是数值字面量（不接受从其他变量间接赋值，避免误关）
        self.assertRegex(src, r"timeout\s*=\s*\d+")


class TestCompressorDeadCodeRemoved(unittest.TestCase):
    """L4: compressor.is_compressing / cancel_operation 是死代码，必须删除。"""

    def test_no_is_compressing_property(self):
        from qwen_app import compressor
        self.assertFalse(hasattr(compressor.ConversationCompressor, "is_compressing"),
                         "_is_compressing 从未为 True，property 是死代码")

    def test_no_cancel_operation_method(self):
        from qwen_app import compressor
        self.assertFalse(hasattr(compressor.ConversationCompressor, "cancel_operation"),
                         "cancel_operation 也只设回 False，从未真正取消")
        self.assertFalse(hasattr(compressor.ConversationCompressor, "_is_compressing"),
                         "_is_compressing 也应一起删")

    def test_no_external_dependencies_on_removed_api(self):
        """确认删完后整个项目没有别处再调这两个 API"""
        import subprocess
        # grep 全代码（排除 .venv/.git/.workbuddy/cache）
        r = subprocess.run(
            [sys.executable, "-c",
             "import os, re\n"
             "for r,_,fs in os.walk(r'" + _ROOT.replace("\\", "\\\\") + r"'):\n"
             "    if any(s in r for s in ('.venv','.git','__pycache__','.workbuddy')): continue\n"
             "    for f in fs:\n"
             "        if not f.endswith('.py'): continue\n"
             "        p = os.path.join(r,f)\n"
             "        try: s = open(p, encoding='utf-8').read()\n"
             "        except: continue\n"
             "        if 'cancel_operation' in s: print(p)\n"
             "        if re.search(r'\\bis_compressing\\b', s): print(p)\n"
            ],
            capture_output=True, text=True, cwd=_ROOT,
        )
        offenders = [ln for ln in r.stdout.splitlines() if ln.strip()]
        # 只允许 compressor.py 自己出现的标识（其实已经删了，应该空）
        external = [o for o in offenders if not o.endswith("compressor.py")]
        self.assertEqual(external, [],
                         f"删除的死代码仍被引用：{external}")


class TestQmlShortcutScope(unittest.TestCase):
    """M5: QML Shortcut 不应在 inputField 聚焦时抢 Ctrl+L/N/T。

    读 Main.qml 源码确认：破坏性快捷键都加了 !inputField.activeFocus 守卫。
    """

    def test_shortcut_blocks_have_inputfocus_guard(self):
        qml_path = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")
        src = open(qml_path, encoding="utf-8").read()
        # 找出每个 Shortcut 块（粗匹配 '{' 到下一个 '}'）
        import re
        blocks = re.findall(
            r"Shortcut\s*\{([^}]*)\}", src, re.DOTALL
        )
        self.assertGreaterEqual(len(blocks), 5, "应该至少 5 个 Shortcut")

        # 破坏性快捷键（清空 / 切换主题 / 新建对话）必须有 focus 守卫
        destructive = ["Ctrl+L", "Ctrl+T", "Ctrl+N"]
        for key in destructive:
            block = next((b for b in blocks if f'sequence: "{key}"' in b), None)
            self.assertIsNotNone(block, f"找不到 {key} Shortcut 块")
            self.assertIn("!inputField.activeFocus", block,
                          f"{key} 必须在 inputField 失焦时才生效（防抢输入框的 Ctrl+L/T/N）")

    def test_stop_shortcuts_not_changed(self):
        """Esc / Ctrl+K 是停止生成的，不需要 focus 守卫（即使在 inputField focus
        时按 Esc 也应能停止当前生成）"""
        qml_path = os.path.join(_ROOT, "qwen_app", "qml", "Main.qml")
        src = open(qml_path, encoding="utf-8").read()
        import re
        blocks = re.findall(r"Shortcut\s*\{([^}]*)\}", src, re.DOTALL)
        for key in ("Esc", "Ctrl+K"):
            block = next((b for b in blocks if f'sequence: "{key}"' in b), None)
            self.assertIsNotNone(block)
            # 这些是 isBusy 守卫，不是 focus 守卫
            self.assertIn("bridge.isBusy", block)


if __name__ == "__main__":
    unittest.main()
