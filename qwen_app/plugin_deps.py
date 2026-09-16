"""插件运行时第三方依赖 —— **单一事实源**。

为什么单独放一个模块（Day 20.6.13）：
    plugins/*.py 是由 ``importlib.util.spec_from_file_location`` **动态加载**的，
    PyInstaller 的静态分析**完全看不见**这些文件里的 ``import paramiko`` 之类。
    因此这些依赖必须在两处显式声明，且两处必须一致：

      1. 打包时  —— ``qwen.spec`` 把它们塞进 ``hiddenimports``；
      2. 打包后  —— ``main.py --selftest`` 逐个真实 import，验证 exe 里没有漏包。

    放在同一个模块里，避免「spec 加了、自检没加」这种漂移。

每项都标注了引入它的插件，方便日后移除插件时同步清理。
"""

def scan_plugin_imports(plugin_dirs=None):
    """AST 扫描插件源码，收集它们 import 的**全部模块全名**（含标准库）。

    为什么需要它（Day 20.6.13 实测踩到）：
        ``web_fetch.py`` 顶部 ``from html.parser import HTMLParser`` ——
        ``html.parser`` 是标准库，但插件是**动态加载**的，PyInstaller 静态分析
        根本不知道这个 import 存在，于是它**不在包里**。打包产物启动正常，
        一点「网页抓取」就 ``No module named 'html.parser'``。

        所以不能只维护「第三方依赖」清单：**插件里出现的每个模块都要显式声明**。
        这里直接读源码抽出来，新增插件/新增 import 会自动被纳入，
        不需要人来同步维护。

    排除：项目内部模块（``plugins`` / ``qwen_app``）与相对导入。
    """
    import ast
    import os

    if plugin_dirs is None:
        try:
            from . import paths
            plugin_dirs = paths.plugins_dirs()
        except Exception:
            plugin_dirs = [os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")]

    names = set()
    for base in plugin_dirs:
        try:
            files = sorted(os.listdir(base))
        except Exception:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            try:
                with open(os.path.join(base, fn), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        names.add(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:          # 相对导入 = 包内模块，跳过
                        continue
                    if node.module:
                        names.add(node.module)

    return sorted(n for n in names
                  if n.split(".")[0] not in ("plugins", "qwen_app"))


# ── 手工补充清单 ──
# scan_plugin_imports() 抽不到、但运行时**确实需要**的模块：
# 这些是「在 Python 代码里 import 不到的动态依赖」。
DYNAMIC_DEPS = [
    "keyring.backends.Windows",  # 由 keyring 在运行时按平台挑选，非插件 import
    "socksio",                   # 由 httpx 在启用 socks5 代理时动态导入
]

# 顶层模块名（importlib.import_module 可直接用）
PLUGIN_THIRD_PARTY_DEPS = [
    "paramiko",                  # ssh_runner.py       SSH 远程操作
    "docx",                      # docx.py / knowledge_base.py（python-docx）
    "pptx",                      # pptx.py             PPT 生成
    "openpyxl",                  # excel.py            xlsx 读写
    "xlsxwriter",                # excel.py            xlsx 写出
    "pdfplumber",                # pdf.py / knowledge_base.py
    "jieba",                     # knowledge_base.py   中文分词
    "keyring",                   # _secret_store.py    系统凭据库
    "keyring.backends.Windows",  #   Windows 凭据管理器后端
    "mysql.connector",           # sql_helper.py       MySQL / GaussDB for MySQL
    "psycopg2",                  # sql_helper.py       PostgreSQL / GaussDB
    "socksio",                   # SOCKS5 代理（httpx 依赖）
]

# 需要「整包递归收集子模块」的库（存在按需动态 import 的子模块）
RECURSE_SUBMODULES = [
    "pdfminer",          # CMap / 编码表等按需加载
    "keyring.backends",  # 各平台后端
]


def all_required_imports():
    """打包与自检**共同使用**的完整模块清单（去重、保序）。

    = 手工清单（第三方 + 动态依赖） ∪ AST 扫描出的插件 import（含标准库）
    """
    ordered = []
    for name in list(PLUGIN_THIRD_PARTY_DEPS) + list(DYNAMIC_DEPS) + scan_plugin_imports():
        if name and name not in ordered:
            ordered.append(name)
    return ordered
