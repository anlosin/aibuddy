"""Day 19 修复回归测试 — C-NEW-1/M-NEW-2/M-NEW-5 + C-NEW-4 + H-NEW-1。"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestChatBridgeUsesRealModelId(unittest.TestCase):
    """C-NEW-1/M-NEW-5: start_real_chat 必须用真实 model_id，不再硬编码 'mock-qwen'。
    且 enable_thinking/enable_tools 必须从全局偏好读，不再硬编码。"""

    def test_no_hardcoded_mock_qwen_in_start_real_chat(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # 在 start_real_chat 函数体内，WorkerThread(...) 的 model_id
        # 参数必须用 real_model_id 变量（不再硬编码字面量 "mock-qwen"）。
        # 注意：默认变量赋值 `real_model_id = "mock-qwen"` 是允许的（仅缺省时回退）。
        # 关键是 WorkerThread 调用本身。
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_real_chat":
                block = ast.get_source_segment(src, node)
                # 找 WorkerThread(...) 调用
                self.assertIn("WorkerThread(", block)
                # model_id= 后必须跟变量名（real_model_id），不能是字面量字符串
                # 找形如 model_id="..."  的硬编码
                import re
                # 在 WorkerThread 上下文里找 'model_id="..."'
                wt_idx = block.find("WorkerThread(")
                # 找 WorkerThread(...) 的闭合右括号
                depth = 0
                end = wt_idx
                for j in range(wt_idx, len(block)):
                    if block[j] == "(": depth += 1
                    elif block[j] == ")":
                        depth -= 1
                        if depth == 0:
                            end = j + 1
                            break
                wt_block = block[wt_idx:end]
                # wt_block 必须不含 model_id="..." 硬编码
                self.assertNotIn('model_id="', wt_block,
                                  "C-NEW-1: WorkerThread(...) 的 model_id 不能硬编码字面量字符串")
                return
        self.fail("找不到 start_real_chat")

    def test_no_hardcoded_enable_thinking_false(self):
        from qwen_app import chat_bridge
        import ast
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_real_chat":
                block = ast.get_source_segment(src, node)
                # 旧的硬编码 'enable_thinking=False' 必须消失
                self.assertNotIn("enable_thinking=False", block,
                                  "M-NEW-5：enable_thinking 必须从全局偏好读，不再硬编码 False")
                return
        self.fail("找不到 start_real_chat")

    def test_uses_global_prefs_attribute(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # 必须有 _enable_thinking / _enable_tools 属性 + get_preferences/set_preferences
        self.assertIn("_enable_thinking", src)
        self.assertIn("_enable_tools", src)
        self.assertIn("def set_preferences", src, "必须有 set_preferences slot 给 QML 改偏好")
        self.assertIn("def get_preferences", src, "必须有 get_preferences slot 给 QML 读偏好")


class TestChatBridgeImagePathWhitelist(unittest.TestCase):
    """C-NEW-4: 图片附件路径必须白名单 + 拒绝对路径。"""

    def test_image_ext_whitelist_defined(self):
        from qwen_app import chat_bridge
        self.assertTrue(hasattr(chat_bridge.ChatBridge, "_IMAGE_EXT"))
        exts = chat_bridge.ChatBridge._IMAGE_EXT
        # 必须是常见图片格式
        for need in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            self.assertIn(need, exts)
        # 不应包含 .txt / .env / .id_rsa 等敏感类型
        for forbid in (".txt", ".id_rsa", ".env", ".json"):
            self.assertNotIn(forbid, exts)

    def test_mime_map_complete(self):
        from qwen_app import chat_bridge
        mimes = chat_bridge.ChatBridge._IMAGE_MIMES
        for ext in chat_bridge.ChatBridge._IMAGE_EXT:
            self.assertIn(ext, mimes,
                          f"_IMAGE_MIMES 必须含 {ext}")
            self.assertTrue(mimes[ext].startswith("image/"),
                            f"{ext} 的 mime 必须是 image/*，实际 {mimes[ext]}")

    def test_build_user_content_rejects_absolute_path(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge
        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        # 绝对路径（含 POSIX 风格）应被拒绝
        for bad in ("/etc/passwd", "/tmp/secrets.txt", "C:\\Users\\test\\.ssh\\id_rsa"):
            result = b._build_user_content("hi", [bad])
            # 应只返回 text 块，没有 image_url 块
            if isinstance(result, list):
                self.assertEqual(len(result), 1, f"绝对路径 {bad} 应被拒绝")
                self.assertEqual(result[0]["type"], "text")
            else:
                # 返回纯 text（没有 image_paths）
                self.assertEqual(result, "hi")

    def test_build_user_content_rejects_non_image_ext(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge
        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        # 相对路径但扩展名不在白名单
        # 先建一个 .env 文件（实际不存在，读不到也无所谓——白名单先拒）
        result = b._build_user_content("hi", ["secrets.env"])
        if isinstance(result, list):
            self.assertEqual(len(result), 1, "非图片扩展名应被拒绝")
        else:
            self.assertEqual(result, "hi")

    def test_build_user_content_skips_oversize(self):
        """4MB+ 图片应跳过（避免 base64 体积爆炸 + LLM 端 OOM）"""
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge
        import tempfile
        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        # 临时创建 5MB 的 png
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG" + b"\x00" * (5 * 1024 * 1024))
            big_path = f.name
        try:
            result = b._build_user_content("hi", [big_path])
            # 应只返回 text 块
            if isinstance(result, list):
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["type"], "text")
            else:
                self.assertEqual(result, "hi")
        finally:
            os.remove(big_path)

    def test_build_user_content_no_paths_returns_text(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge
        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        # 无图片：直接返回 text
        result = b._build_user_content("hello", [])
        self.assertEqual(result, "hello")


class TestSshRunnerCwdQuoting(unittest.TestCase):
    """H-NEW-1: cwd 必须 shell 转义，注入被拒。"""

    def test_cwd_uses_shlex_quote(self):
        from plugins import ssh_runner
        with open(ssh_runner.__file__, encoding="utf-8") as f:
            src = f.read()
        # 必须 import shlex 且在 _do_command 内调用 shlex.quote
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_do_command":
                block = ast.get_source_segment(src, node)
                self.assertIn("import shlex", block,
                              "H-NEW-1：_do_command 必须 import shlex")
                self.assertIn("shlex.quote", block,
                              "必须用 shlex.quote(cwd) 转义 cwd")
                return
        self.fail("找不到 _do_command")

    def test_no_old_string_format_cwd(self):
        """旧版 'cd "%s" && %s' % (cwd, command) 必须消失。"""
        from plugins import ssh_runner
        with open(ssh_runner.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('cd "%s" && %s', src,
                          "H-NEW-1：旧版 cwd 字符串格式化必须删除（命令注入）")


class TestChatBridgePluginState(unittest.TestCase):
    """C-NEW-2: _enabled_plugin_names 必须从 cfg 持久化读，不再硬编码。"""

    def test_reads_from_persisted_plugin_state(self):
        # 拆分后 _enabled_plugin_names 可能位于 _bridge_plugin.py —— 扫模块组
        from tests._bridge_source import bridge_source
        src = bridge_source()
        # _enabled_plugin_names 必须调 load_plugin_state
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_enabled_plugin_names":
                block = ast.get_source_segment(src, node)
                self.assertIn("load_plugin_state", block,
                              "C-NEW-2：_enabled_plugin_names 必须从持久化 enabled_plugins 字段读")
                return
        self.fail("找不到 _enabled_plugin_names")


class TestChatBridgeExpertIntegration(unittest.TestCase):
    """C-NEW-3: chat_bridge 必须集成 expert_router（专家前缀 + system_prompt 注入）。"""

    def test_uses_match_expert(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # send_message 必须 import match_expert
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "send_message":
                block = ast.get_source_segment(src, node)
                self.assertIn("match_expert", block,
                              "C-NEW-3：send_message 必须调 match_expert 处理 /dev 前缀")
                return
        self.fail("找不到 send_message")

    def test_uses_build_system_prompt(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # 必须有 _build_system_prompt 方法 + 调 expert_router.build_system_prompt
        self.assertIn("def _build_system_prompt", src,
                      "C-NEW-3：必须有 _build_system_prompt 方法")
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_build_system_prompt":
                block = ast.get_source_segment(src, node)
                self.assertIn("build_system_prompt", block,
                              "_build_system_prompt 必须调 expert_router.build_system_prompt")
                return
        self.fail("找不到 _build_system_prompt")

    def test_start_real_chat_takes_system_prompt(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # start_real_chat 签名必须含 system_prompt 参数
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_real_chat":
                args = [a.arg for a in node.args.args]
                self.assertIn("system_prompt", args,
                              "C-NEW-3：start_real_chat 必须接 system_prompt 参数")
                return
        self.fail("找不到 start_real_chat")

    def test_has_set_expert_and_list_experts_slots(self):
        from qwen_app import chat_bridge
        # QML 端下拉框切专家需要的 slot
        self.assertTrue(hasattr(chat_bridge.ChatBridge, "set_expert"),
                        "C-NEW-3：必须有 set_expert slot")
        self.assertTrue(hasattr(chat_bridge.ChatBridge, "list_experts"),
                        "C-NEW-3：必须有 list_experts slot")

    def test_default_expert_is_general(self):
        from PyQt5.QtWidgets import QApplication
        from qwen_app.chat_bridge import ChatBridge
        app = QApplication.instance() or QApplication(sys.argv)
        b = ChatBridge(theme="light")
        # 初始默认专家
        self.assertEqual(b._current_expert_id, "general")


if __name__ == "__main__":
    unittest.main()
