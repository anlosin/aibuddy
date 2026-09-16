# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec —— qwen AI 对话助手（Windows onedir 便携版）

构建：
    .venv\\Scripts\\python.exe -m PyInstaller qwen.spec --noconfirm
产物：
    dist/qwen/qwen.exe          ← 双击运行
    dist/qwen/data/             ← 首次运行自动生成（配置/会话/自动化，便携）
    dist/qwen/plugins/          ← 首次运行自动释放（可增删改，支持热重载）

设计要点（对应 Day 20.6.13 的打包改造，详见 qwen_app/paths.py）：
  1. **只读资源**（QML / experts / 内置插件模板）打进 _MEIPASS，运行时由
     paths.resource_dir() 定位。
  2. **可写数据**（model_config.json / conversations / automations）**不进包**，
     运行时由 paths.data_dir() 落到 exe 同级 data/（便携）；exe 目录不可写
     （如装在 Program Files）时自动回退 %APPDATA%\\qwen。
     —— 这是打包最关键的坑：若照搬源码态的 __file__ 推导，数据会被写进
     _MEIPASS，onefile 下进程退出即删（配置全丢），onedir 下写进程序目录。
  3. 插件由 importlib.util.spec_from_file_location **动态加载**，PyInstaller 的
     静态分析**看不见插件里的第三方 import** → 必须手工声明 hiddenimports
     （见下方 PLUGIN_DEPS）。
  4. console=False：main.py 已加 stdout/stderr 兜底，把输出重定向到
     data/logs/app.log（否则 print 会在 None 上抛 AttributeError）。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.getcwd())
NAME = "qwen"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── 随包分发的只读资源 ──
datas = [
    (os.path.join(ROOT, "qwen_app", "qml"),     "qwen_app/qml"),
    (os.path.join(ROOT, "qwen_app", "experts"), "qwen_app/experts"),
    # 内置插件模板（含 k8s_templates/ 子目录）。首启动会被复制到 exe 同级
    # plugins/，用户之后改的是那一份。
    (os.path.join(ROOT, "plugins"),             "plugins"),
    (os.path.join(ROOT, "model_config.example.json"), "."),
]

# ── 第三方数据文件（词典 / CMap）──
for _pkg in ("jieba", "pdfminer", "pdfplumber"):
    try:
        datas += collect_data_files(_pkg)
    except Exception:
        pass

# ── Qt QML 运行时：QtQuick 2.15 / QtQuick.Controls 2.15 / QtQuick.Layouts 1.15
#    是 QML 侧真实 import 的模块，必须带上 Qt5/qml 目录（含 Controls 的 Default 样式）
try:
    import PyQt5
    _qml_dir = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "qml")
    if os.path.isdir(_qml_dir):
        datas.append((_qml_dir, "PyQt5/Qt5/qml"))
except Exception:
    pass

# ── 插件依赖清单 ──
# 插件是 importlib **动态加载**的，PyInstaller 的静态分析看不到 plugins/*.py
# 里的任何 import —— 漏一个就是「某个功能在 exe 里 ImportError」，而且**连标准库
# 也会漏**（实测：web_fetch 的 `from html.parser import HTMLParser` 就没进包）。
#
# 清单由 qwen_app/plugin_deps.py 生成（AST 扫描插件源码 + 手工补充动态依赖），
# 与 `qwen.exe --selftest` 用的是**同一份**，避免「spec 加了、自检没加」的漂移。
from qwen_app.plugin_deps import RECURSE_SUBMODULES, all_required_imports

hiddenimports = all_required_imports()
# 存在按需动态 import 子模块的库，整体递归收进来
for _pkg in RECURSE_SUBMODULES:
    hiddenimports += collect_submodules(_pkg)

# ── 裁剪：这些在本项目里确定用不到，剔掉可显著减小体积 ──
# 注意 QtSql / QtTest 等都没用到（数据库走 stdlib sqlite3 / 第三方驱动）
excludes = [
    "tkinter", "pydoc", "doctest",
    "matplotlib", "numpy", "scipy", "pandas",
    "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngineCore", "PyQt5.QtWebEngine",
    "PyQt5.QtBluetooth", "PyQt5.QtNfc", "PyQt5.QtSensors",
    "PyQt5.QtSerialPort", "PyQt5.QtPositioning", "PyQt5.QtLocation",
    "PyQt5.QtXmlPatterns", "PyQt5.QtDesigner", "PyQt5.QtHelp",
    "PyQt5.QtTest", "PyQt5.QtSql", "PyQt5.QtMultimedia", "PyQt5.QtMultimediaWidgets",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # GUI 程序：不弹控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=NAME,
)
