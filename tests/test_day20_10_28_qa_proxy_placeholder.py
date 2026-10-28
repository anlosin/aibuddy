# -*- coding: utf-8 -*-
"""Day 20.10.28 QA 独立护栏：代理 placeholder 改动的「边界守卫」

背景
----
工程师的改动只有一处：``qwen_app/settings_dialog.py`` 里「代理:」输入框的
placeholder 文案，加上配套护栏 ``test_day20_10_28_proxy_placeholder.py``。

用户原始需求是「做 A 吧，**工具暂时不要动**」—— A 只改 placeholder 提示
socks5 支持；插件（web_fetch / web_search / weather 等）绝对不能被顺带改动。
这类「只改一行文案」的改动最大的风险不是这行文案写错，而是**边界被悄悄突破**：
有人顺手给插件加了个代理支持，或把 ``__pycache__/*.pyc`` 提交进了仓库。

本文件是 QA（严过关）侧的独立护栏，刻意**不复用**工程师那套 AST 静态扫描
方案，而是从三个独立视角交叉验证：

1. ``TestRepoHygiene``    —— git 索引卫生（.pyc 误提交 / 暂存面爆炸 / 插件被暂存）
2. ``TestPluginBoundary`` —— 「工具不动」语义边界（插件里不得出现代理相关代码）
3. ``TestRuntimePlaceholder`` —— **运行时**真跑一遍 Qt 表单，读回真实的
   placeholder 文本（工程师的护栏是静态 AST，本护栏是运行时动态取值，
   两者互相独立，谁都覆盖不到对方的盲区）
4. ``TestHttpxSocksSupport`` —— httpx 确实支持 socks5/socks5h（静态白名单）

硬性约束
--------
本文件只读仓库，**绝不执行 ``git add``** 等写索引操作，避免污染暂存区。
"""
import ast
import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SETTINGS_DIALOG = os.path.join(_ROOT, "qwen_app", "settings_dialog.py")
CONFIG_PY = os.path.join(_ROOT, "qwen_app", "config.py")
PLUGINS_DIR = os.path.join(_ROOT, "plugins")

# 「工具暂时不要动」涉及的核心插件（用户明确点名）
FROZEN_PLUGINS = ("web_fetch.py", "web_search.py", "weather.py")

# 插件里绝对不该出现的代理相关符号
FORBIDDEN_IN_PLUGINS = ("httpx", "ProxyHandler", "socks5")


# ══════════════════════════════════════════════════════════════
# git 只读helper
# ══════════════════════════════════════════════════════════════
def _git(*args):
    """执行只读 git 命令，返回 stdout 文本；失败抛 RuntimeError。"""
    try:
        proc = subprocess.run(
            ["git", "-C", _ROOT, *args],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"git 命令不可用: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败 ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def _staged_names():
    """返回已暂存（staged）文件路径列表。"""
    out = _git("diff", "--cached", "--name-only")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _is_git_repo():
    try:
        _git("rev-parse", "--is-inside-work-tree")
        return True
    except RuntimeError:
        return False


# ══════════════════════════════════════════════════════════════
# 1. git 索引卫生
# ══════════════════════════════════════════════════════════════
class TestRepoHygiene(unittest.TestCase):
    """暂存区卫生：防止 .pyc 误提交 / 暂存面爆炸 / 插件被顺手改掉"""

    def setUp(self):
        if not _is_git_repo():
            self.skipTest("当前目录不是 git 仓库，跳过索引卫生检查")
        self.staged = _staged_names()

    def test_no_pycache_staged(self):
        """暂存区里不得出现 .pyc / .pyo 字节码文件。

        背景：``.gitignore`` 里为了把源码目录 ``qwen_app/`` 从「调试残留」
        通配规则里救出来，写了 ``!/qwen_app/**``。这条否定规则会**连带把
        ``qwen_app/__pycache__/*.pyc`` 一起重新纳入版本控制范围**，
        导致跑一次测试就可能把 .pyc 暂存进去。此护栏专门防这个坑复发。
        """
        bad = [p for p in self.staged if p.lower().endswith((".pyc", ".pyo"))]
        self.assertEqual(
            [], bad,
            f"暂存区混入了字节码文件（.gitignore 的 '!/qwen_app/**' 覆盖了 "
            f"'__pycache__/'）：{bad}")

    def test_no_pycache_dir_staged(self):
        """暂存区里不得出现 __pycache__ 目录下的任何文件。"""
        bad = [p for p in self.staged if "__pycache__" in p.split("/")]
        self.assertEqual([], bad, f"暂存区混入了 __pycache__ 下的文件：{bad}")

    def test_plugins_not_staged(self):
        """本次改动不应暂存任何 plugins/ 下的文件（用户：「工具暂时不要动」）。"""
        bad = [p for p in self.staged
               if p.replace("\\", "/").startswith("plugins/")]
        self.assertEqual([], bad, f"插件被暂存了，违反「工具暂时不要动」：{bad}")

    def test_config_not_staged(self):
        """本次改动不应暂存 qwen_app/config.py。

        make_openai_client 是「proxy 字符串 → httpx.Client」的实际通路，
        本次只改文案、不改行为，config.py 必须零改动。
        """
        bad = [p for p in self.staged
               if p.replace("\\", "/") == "qwen_app/config.py"]
        self.assertEqual([], bad, "config.py 被暂存，proxy 通路不应被改动")

    def test_staged_scope_is_small(self):
        """暂存文件数应当很小——防止「顺手」把一堆无关改动一起提交。

        本次改动的合理上限：1 个源码文件 + 若干 tests/test_*.py。
        """
        self.assertLessEqual(
            len(self.staged), 8,
            f"暂存文件数异常偏多({len(self.staged)})，疑似混入无关改动："
            f"{self.staged}")


# ══════════════════════════════════════════════════════════════
# 2. 「工具不动」语义边界
# ══════════════════════════════════════════════════════════════
class TestPluginBoundary(unittest.TestCase):
    """插件不得因为这次改动被塞进代理相关代码"""

    def test_plugins_untouched_by_this_change(self):
        """web_fetch / web_search / weather 里不得出现 httpx / ProxyHandler / socks5。

        这些插件走的是自己的 HTTP 通道（requests 等），与模型连接的
        httpx.Client(proxy=...) 无关。用户明确说「工具暂时不要动」，
        因此这里断言源码里**根本不存在**代理相关痕迹。
        """
        violations = []
        for name in FROZEN_PLUGINS:
            path = os.path.join(PLUGINS_DIR, name)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            for token in FORBIDDEN_IN_PLUGINS:
                if token in content:
                    violations.append(f"{name} 含 {token!r}")
        self.assertEqual(
            [], violations,
            "插件里出现了代理相关代码，违反「工具暂时不要动」：" + "; ".join(violations))

    def test_no_plugin_imports_httpx(self):
        """整个 plugins/ 目录都不得 import httpx。"""
        offenders = []
        if os.path.isdir(PLUGINS_DIR):
            for name in sorted(os.listdir(PLUGINS_DIR)):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(PLUGINS_DIR, name)
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
                try:
                    tree = ast.parse(src, filename=path)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        mods = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        mods = [node.module or ""]
                    else:
                        continue
                    for mod in mods:
                        if mod.split(".")[0] == "httpx":
                            offenders.append(f"{name} import {mod}")
        self.assertEqual([], offenders, f"插件引入了 httpx：{offenders}")


# ══════════════════════════════════════════════════════════════
# 3. 运行时 placeholder（与工程师的 AST 静态扫描互相独立）
# ══════════════════════════════════════════════════════════════
class TestRuntimePlaceholder(unittest.TestCase):
    """真起一个 Qt 表单，动态读回「代理:」输入框的 placeholder 文本"""

    @classmethod
    def setUpClass(cls):
        # 必须在 import PyQt5 / 建 QApplication 之前设置
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt5.QtWidgets import QApplication
        except ImportError as exc:  # pragma: no cover - 环境缺依赖时跳过
            raise unittest.SkipTest(f"PyQt5 不可用: {exc}") from exc
        cls._app = QApplication.instance() or QApplication([])

    def _proxy_placeholder(self):
        """构建模型编辑表单，按 label「代理:」定位到对应的 QLineEdit 并取文本。"""
        from PyQt5.QtWidgets import QDialog, QFormLayout
        from qwen_app.settings_dialog import _model_edit_form

        dlg = QDialog()
        try:
            form, _get_values = _model_edit_form(None, dlg)
            layout = form.layout()
            self.assertIsInstance(layout, QFormLayout)
            for row in range(layout.rowCount()):
                label_item = layout.itemAt(row, QFormLayout.LabelRole)
                field_item = layout.itemAt(row, QFormLayout.FieldRole)
                if label_item is None or field_item is None:
                    continue
                label = label_item.widget()
                if label is None or not label.text().strip().startswith("代理"):
                    continue
                field = field_item.widget()
                self.assertIsNotNone(field, "「代理:」行没有对应输入框")
                return field.placeholderText()
            self.fail("模型编辑表单里找不到「代理:」输入框")
        finally:
            dlg.close()

    def test_runtime_placeholder_mentions_socks5(self):
        """运行时 placeholder 必须真的提到 socks5。"""
        text = self._proxy_placeholder()
        self.assertIn("socks5", text, f"运行时 placeholder 未提到 socks5：{text!r}")

    def test_runtime_placeholder_prefers_socks5h(self):
        """运行时 placeholder 必须真的提到 socks5h://（推荐写法）。"""
        text = self._proxy_placeholder()
        self.assertIn("socks5h://", text, f"运行时 placeholder 未提到 socks5h://：{text!r}")

    def test_runtime_placeholder_keeps_http_and_direct(self):
        """运行时 placeholder 必须同时保留 http:// 示例与「留空=直连」语义。"""
        text = self._proxy_placeholder()
        self.assertIn("http://", text, f"运行时 placeholder 丢了 http:// 示例：{text!r}")
        self.assertIn("直连", text, f"运行时 placeholder 丢了「直连」提示：{text!r}")


# ══════════════════════════════════════════════════════════════
# 4. httpx 确实支持 socks5 / socks5h
# ══════════════════════════════════════════════════════════════
class TestHttpxSocksSupport(unittest.TestCase):
    """placeholder 里写的 socks5h:// 必须真的被 httpx 接受"""

    def test_httpx_supports_socks5_schemes(self):
        """httpx 源码里必须存在 socks5 / socks5h 的 scheme 分支。"""
        try:
            import httpx._config as hx_config
            import httpx._transports.default as hx_default
        except ImportError as exc:  # pragma: no cover
            self.skipTest(f"httpx 不可用: {exc}")

        config_path = getattr(hx_config, "__file__", "")
        default_path = getattr(hx_default, "__file__", "")
        self.assertTrue(config_path and default_path, "拿不到 httpx 源码路径")

        with open(config_path, encoding="utf-8") as fh:
            config_src = fh.read()
        with open(default_path, encoding="utf-8") as fh:
            default_src = fh.read()

        self.assertIn(
            "socks5h", config_src,
            "httpx._config 的 scheme 白名单里没有 socks5h")
        self.assertIn(
            "socks5", default_src,
            "httpx._transports.default 里没有 socks5 分支")
        self.assertIn(
            "socksio", default_src,
            "httpx._transports.default 没有 socksio 依赖检查")

    def test_socksio_installed(self):
        """socks5 代理需要 socksio 依赖，项目 venv 里必须装得上。"""
        try:
            import socksio
        except ImportError as exc:
            self.fail(f"项目 venv 缺少 socksio，socks5 代理不可用：{exc}")
        self.assertTrue(hasattr(socksio, "__version__"), "socksio 缺少 __version__")


if __name__ == "__main__":
    unittest.main()
