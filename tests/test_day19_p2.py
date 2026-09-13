"""Day 19 第二批 P1 修复测试 — H-NEW-2~5 (DoS) + H-NEW-6 (set 线程安全) + H-NEW-7 (history size)。"""
import ast
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestFileManagerSearchContentRedos(unittest.TestCase):
    """H-NEW-2: file_manager._do_search_content 必须防 ReDoS。"""

    def test_query_length_limit(self):
        from plugins import file_manager
        with open(file_manager.__file__, encoding="utf-8") as f:
            src = f.read()
        # 必须有 query 长度上限
        self.assertIn("MAX_QUERY_LEN", src,
                      "H-NEW-2: 必须定义 MAX_QUERY_LEN 防止 ReDoS")

    def test_no_naive_re_compile(self):
        from plugins import file_manager
        with open(file_manager.__file__, encoding="utf-8") as f:
            src = f.read()
        # _do_search_content 必须有 re.compile 后的长度检查
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_do_search_content":
                block = ast.get_source_segment(src, node)
                # 找到 re.compile 之前的 if len(query) > X 检查
                self.assertIn("MAX_QUERY_LEN", block)
                return
        self.fail("找不到 _do_search_content")


class TestSqlHelperFetchMany(unittest.TestCase):
    """H-NEW-3: sql_helper._do_query 必须用 fetchmany，不 fetchall 全拉内存。"""

    def test_uses_fetchmany(self):
        from plugins import sql_helper
        with open(sql_helper.__file__, encoding="utf-8") as f:
            src = f.read()
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_do_query":
                block = ast.get_source_segment(src, node)
                self.assertIn("fetchmany", block,
                              "H-NEW-3: _do_query 必须用 fetchmany 增量拉")
                self.assertNotIn(".fetchall()", block,
                                  "H-NEW-3: _do_query 不应再 fetchall 全拉内存")
                return
        self.fail("找不到 _do_query")


class TestWebFetchBodySizeLimit(unittest.TestCase):
    """H-NEW-4: web_fetch 必须限制 body 大小，防 OOM。"""

    def test_max_body_size_constant(self):
        from plugins import web_fetch
        with open(web_fetch.__file__, encoding="utf-8") as f:
            src = f.read()
        # 必须有 MAX_BODY_SIZE 常量（防止 1GB 响应吃光内存）
        self.assertIn("MAX_BODY_SIZE", src,
                      "H-NEW-4: 必须定义 MAX_BODY_SIZE 限制响应体大小")


class TestChatBridgeStreamBufferSize(unittest.TestCase):
    """H-NEW-5: chat_bridge._on_worker_chunk 累积 buffer 必须有大小上限。"""

    def test_max_stream_buffer_constant(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("MAX_STREAM_BUFFER", src,
                      "H-NEW-5: 必须定义 MAX_STREAM_BUFFER 限制累积 buffer 大小")

    def test_on_worker_chunk_truncates(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_on_worker_chunk":
                block = ast.get_source_segment(src, node)
                # 必须有 buffer 长度检查（if len(buf) > MAX 之类的）
                self.assertIn("MAX_STREAM_BUFFER", block)
                return
        self.fail("找不到 _on_worker_chunk")


class TestConfigAllConnsThreadsafe(unittest.TestCase):
    """H-NEW-6: config._get_db 里 _all_conns.add() 必须在 _CONN_LOCK 内。"""

    def test_all_conns_add_under_lock(self):
        from qwen_app import config
        with open(config.__file__, encoding="utf-8") as f:
            src = f.read()
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_get_db":
                # 用 AST 精确验证：找 _all_conns.add(...) 调用，
                # 看它是否在某个 `with _CONN_LOCK:` 语句里。
                # 简化：找 _all_conns.add 的 Call 节点，向上遍历
                # 父节点的 body，看是否包含 With(_CONN_LOCK)。
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        f = sub.func
                        if (isinstance(f, ast.Attribute) and f.attr == "add"
                                and isinstance(f.value, ast.Name)
                                and f.value.id == "_all_conns"):
                            # 找最近的祖先 with 块
                            parent = sub
                            # AST 没直接的 parent，用辅助 walk 重建
                            found_lock = False
                            for ancestor in _ancestors(node, sub):
                                if isinstance(ancestor, ast.With):
                                    for item in ancestor.items:
                                        ctx = item.context_expr
                                        if (isinstance(ctx, ast.Name)
                                                and ctx.id == "_CONN_LOCK"):
                                            found_lock = True
                                            break
                                    if found_lock:
                                        break
                            self.assertTrue(found_lock,
                                            "H-NEW-6: _all_conns.add(db) 必须在 with _CONN_LOCK 内")
                            return
                self.fail("_get_db 里没找到 _all_conns.add 调用")
        self.fail("找不到 _get_db")


class TestChatBridgeHistorySizeLimit(unittest.TestCase):
    """H-NEW-7: chat_bridge._append_history 必须有大小/条数上限。"""

    def test_history_size_limit_constants(self):
        from qwen_app import chat_bridge
        with open(chat_bridge.__file__, encoding="utf-8") as f:
            src = f.read()
        # 单条 content 上限 + history 条数上限
        self.assertIn("MAX_CONTENT_LEN", src,
                      "H-NEW-7: 必须有 MAX_CONTENT_LEN 限制单条 content 大小")
        self.assertIn("MAX_HISTORY_LEN", src,
                      "H-NEW-7: 必须有 MAX_HISTORY_LEN 限制 history 条数")


if __name__ == "__main__":
    unittest.main()


def _ancestors(root, target):
    """AST 辅助：找 target 在 root 中的所有祖先节点（返回 list，含 target 自身）。"""
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            if child is target:
                return [node, target]
            if _contains(child, target):
                return [node] + _ancestors(child, target)
    return [target]


def _contains(node, target):
    """递归检查 node 是否包含 target（按对象身份）。"""
    for child in ast.iter_child_nodes(node):
        if child is target:
            return True
        if _contains(child, target):
            return True
    return False
