# -*- coding: utf-8 -*-
"""Day 20.6.17 (P1-AUD2-2 收窄修复) — knowledge_base 相对 folder 解析回归

修复前：`kb_build` 的相对 folder 永远按 `插件目录/../folder` 解析 ——
  源码态落到项目根（凑合能用）；**打包态落到 exe 目录**，folder="." 会遍历
  整个程序目录（含 _internal / plugins）建索引（P1-AUD2-2 的真实触发点，
  触发概率 100%，不是 fallback）。

修复后：相对 folder **工作区优先**（与 write_file / file_manager 语义一致）；
  工作区里没有该子目录时才兜底程序目录（源码态=项目根 / 打包态=exe 旁）。

全部离线、全部落在 tmp，不碰真实 data/。
"""
import importlib.util
import os
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_kb():
    spec = importlib.util.spec_from_file_location(
        "kb_guard", os.path.join(ROOT, "plugins", "knowledge_base.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class KbBaseResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kb_base_")
        self._old_env = os.environ.get("QWEN_DATA_DIR")
        os.environ["QWEN_DATA_DIR"] = os.path.join(self.tmp, "data")
        from qwen_app import paths
        paths._reset_cache_for_tests()
        self.kb = _load_kb()

    def tearDown(self):
        from qwen_app import workspace, paths
        workspace.reset_for_tests()
        if self._old_env is None:
            os.environ.pop("QWEN_DATA_DIR", None)
        else:
            os.environ["QWEN_DATA_DIR"] = self._old_env
        paths._reset_cache_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_ws(self):
        from qwen_app import workspace
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws, exist_ok=True)
        workspace.set_active_workspace(ws)
        return ws

    def test_relative_folder_resolves_to_workspace_first(self):
        """相对 folder 且工作区里有该子目录 → 必须落在工作区，不是项目根"""
        ws = self._set_ws()
        docs = os.path.join(ws, "docs")
        os.makedirs(docs)
        with open(os.path.join(docs, "note.md"), "w", encoding="utf-8") as f:
            f.write("知识库测试文档 alpha beta\n包含关键词 token_gamma")

        out = self.kb.execute("kb_build", {"folder": "docs"})
        self.assertIn("知识库构建完成", out)
        self.assertTrue(out.startswith("✅"))
        # 来源必须是工作区内的 docs
        self.assertEqual(out.split("来源: ")[1].splitlines()[0],
                         os.path.abspath(docs))

    def test_dot_resolves_to_workspace_not_exe_dir(self):
        """folder="."（打包态的真实危险场景）→ 落在对话工作区，绝不遍历程序目录"""
        ws = self._set_ws()
        with open(os.path.join(ws, "readme.txt"), "w", encoding="utf-8") as f:
            f.write("workspace root doc keyword_zeta")

        out = self.kb.execute("kb_build", {"folder": "."})
        self.assertIn("知识库构建完成", out)
        built_from = out.split("来源: ")[1].splitlines()[0]
        self.assertEqual(os.path.realpath(built_from), os.path.realpath(ws))

    def test_fallback_to_app_dir_when_not_in_workspace(self):
        """工作区没有该子目录 → 兜底程序目录（paths.app_dir()），保持旧行为"""
        self._set_ws()
        app_docs = os.path.join(self.tmp, "app_root", "projdocs")
        os.makedirs(app_docs)
        with open(os.path.join(app_docs, "a.md"), "w", encoding="utf-8") as f:
            f.write("app dir doc keyword_theta")

        from qwen_app import paths
        with mock.patch.object(paths, "app_dir",
                               return_value=os.path.join(self.tmp, "app_root")):
            out = self.kb.execute("kb_build", {"folder": "projdocs"})
        self.assertIn("知识库构建完成", out)
        self.assertEqual(out.split("来源: ")[1].splitlines()[0],
                         os.path.abspath(app_docs))

    def test_abs_path_unchanged(self):
        """绝对路径行为不变（直接使用）"""
        doc_dir = os.path.join(self.tmp, "absdocs")
        os.makedirs(doc_dir)
        with open(os.path.join(doc_dir, "x.md"), "w", encoding="utf-8") as f:
            f.write("abs doc keyword_eta")

        out = self.kb.execute("kb_build", {"folder": doc_dir})
        self.assertIn("知识库构建完成", out)
        self.assertEqual(out.split("来源: ")[1].splitlines()[0],
                         os.path.abspath(doc_dir))

    def test_missing_dir_errors_cleanly(self):
        """两边都不存在 → 明确报错，不炸不误扫"""
        self._set_ws()
        out = self.kb.execute("kb_build", {"folder": "no_such_dir_xyz"})
        self.assertIn("目录不存在", out)


if __name__ == "__main__":
    unittest.main()
