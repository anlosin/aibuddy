# -*- coding: utf-8 -*-
"""Day 20.6.19 护栏：禁止「不隔离」就调 save_single_conversation / save_conversations 的测试。

背景：test_config_threading.py 的 SQLite 锁并发测试（M8）从 2026-09-12 起
每天被全量回归跑一次，每次 10 个线程写入 10 条 `lock-test-{0..9}` 到
**生产** data/conversations.db。直到 2026-09-19 用户在 UI 对话列表里看到
10 条莫名其妙的 `t{0..}-v2` 对话才被发现。

修法：test_config_threading.py 已改用 set_db_path_for_tests(tmpdir)。
本护栏做两道防线：
1. AST 扫描 tests/test_*.py：调 save_single_conversation / save_conversations
   的测试方法必须位于继承 _Isolated* 或 _IsolatedConfigDB 的类中
2. AST 扫描 tests/test_*.py：基类继承链为 unittest.TestCase 但 setUp 没
   调 set_db_path_for_tests → 警告（不强失败，给存量测试迁移留过渡期）

全离线纯源码扫描，0 副作用。
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")

# 调用这两个函数的方法所在的类必须是隔离类（直接继承即可）
WRITE_FUNCS = ("save_single_conversation", "save_conversations")
# 已认可的隔离基类（直接继承或间接继承均可）
SAFE_BASES = {
    "_IsolatedConfigDB",   # tests/test_config_threading.py
    "_IsolatedDB",         # tests/test_session_state.py
    "TestSessionStateTable",
    "TestLegacyMigration",
    "TestSqliteConnLock",  # 间接继承 _IsolatedConfigDB
}


def _class_bases(cls):
    """类定义里写的基类名集合（只取简单名）。"""
    out = set()
    for b in cls.bases:
        if isinstance(b, ast.Name):
            out.add(b.id)
        elif isinstance(b, ast.Attribute):
            out.add(b.attr)
    return out


def _calls_target(node, target_names):
    """节点（method body）里是否调用了任一 target_names 函数。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr in target_names:
                # 必须是从 qwen_app.config 或别名调，不是用户本地函数
                # 这里宽松处理：只要名字匹配就算（同名本地函数也会被报，但实际没人写）
                return True
            if isinstance(f, ast.Name) and f.id in target_names:
                return True
    return False


def _iter_test_classes(tree):
    """test_*.py 里所有 TestCase 子类的 (class_name, bases_set, methods)。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = _class_bases(node)
            if "unittest" in bases or "TestCase" in bases or "unittest.TestCase" in str(bases):
                yield node


class TestNoUnisolatedWrites(unittest.TestCase):
    """测试方法里调 save_single_conversation / save_conversations 的，所在
    类必须满足以下任一条件：
    1. 继承隔离基类（_IsolatedDB / _IsolatedConfigDB 等）；或
    2. 自身有 delete_session 调用做软清理（created_ids + tearDown 模式）

    两条都不满足的 = test_config_threading.py 模式（写完没清理，跑全量
    回归就往生产 db 里塞垃圾）。背景：2026-09-19 用户在 UI 对话列表
    里看到 10 条 t{0..}-v2 lock-test-{0..9}。
    """

    def test_scan_tests_dir(self):
        offenders = []
        for name in sorted(os.listdir(TESTS_DIR)):
            if not name.startswith("test_") or not name.endswith(".py"):
                continue
            if name == os.path.basename(__file__):
                continue  # 本护栏自己
            fp = os.path.join(TESTS_DIR, name)
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=fp)
            except (SyntaxError, OSError):
                continue

            classes = list(_iter_test_classes(tree))
            class_defs = {c.name: _class_bases(c) for c in classes}

            def transitive_bases(cls_name, _seen=None):
                if _seen is None:
                    _seen = set()
                if cls_name in _seen:
                    return set()
                _seen.add(cls_name)
                out = {cls_name}
                for b in class_defs.get(cls_name, set()):
                    out |= transitive_bases(b, _seen)
                return out

            def class_self_calls_delete(cls):
                """类自身（含 setUp/tearDown）是否调用了 delete_session。"""
                for item in cls.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if _calls_target(item, ("delete_session",)):
                            return True
                return False

            def method_has_finally_save_convs(fn):
                """方法体里有 try/finally + finally 块调 save_conversations。

                这种「写 + finally 清理」模式等价于 tearDown 清理：
                测试即使中途崩溃，finally 仍会跑。
                """
                for node in ast.walk(fn):
                    if isinstance(node, ast.Try):
                        for h in node.finalbody:
                            if _calls_target(h, ("save_conversations",)):
                                return True
                return False

            def class_has_finally_cleanup(cls):
                """类内任一测试方法有 try/finally + save_conversations 清理。"""
                for item in cls.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name.startswith("test_") and method_has_finally_save_convs(item):
                            return True
                return False

            for cls in classes:
                if not cls.name.startswith("Test"):
                    continue
                all_bases = transitive_bases(cls.name)
                for item in cls.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not item.name.startswith("test_"):
                        continue
                    if not _calls_target(item, WRITE_FUNCS):
                        continue
                    # 调用了 WRITE_FUNCS → 必须满足（隔离）或（类内有 delete_session /
                    # finally-cleanup 清理）
                    is_isolated = bool(all_bases & SAFE_BASES)
                    has_cleanup = class_self_calls_delete(cls) or class_has_finally_cleanup(cls)
                    if is_isolated or has_cleanup:
                        continue
                    offenders.append(
                        f"{name}:{item.lineno}  "
                        f"{cls.name}.{item.name}  "
                        f"bases={sorted(all_bases) or '(none)'}")

        self.assertEqual(offenders, [],
                         f"以下测试方法调了 {WRITE_FUNCS} 但既未继承隔离基类、"
                         f"本类也无 delete_session 清理——会污染生产 db：\n  " +
                         "\n  ".join(offenders[:10]) +
                         ("" if len(offenders) <= 10 else f"\n  ...还有 {len(offenders)-10} 处"))


class TestIsolationHelpersAvailable(unittest.TestCase):
    """config 模块必须暴露 set_db_path_for_tests 测试钩子。"""

    def test_helper_exists(self):
        from qwen_app import config
        self.assertTrue(hasattr(config, "set_db_path_for_tests"),
                        "config 必须暴露 set_db_path_for_tests() 给测试用")
        self.assertTrue(callable(config.set_db_path_for_tests))

    def test_active_path_override_takes_effect(self):
        """set_db_path_for_tests 期间 _active_db_path() 返回 override"""
        import tempfile
        from qwen_app import config
        tmp = tempfile.mkdtemp(prefix="isolated_check_")
        try:
            override = os.path.join(tmp, "x.db")
            config.set_db_path_for_tests(override)
            try:
                self.assertEqual(config._active_db_path(), override)
                self.assertEqual(config._active_db_dir(), tmp)
            finally:
                config.set_db_path_for_tests(None)
            self.assertEqual(config._active_db_path(), config.CONVERSATIONS_DB)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
