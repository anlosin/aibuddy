"""共享 helper：读取 bridge 模块组源码（chat_bridge.py + 全部 _bridge_*.py）。

背景：ChatBridge 拆分为 mixin 组合后，静态断言测试不能只读
chat_bridge.py 单文件 —— 「在哪里找」放宽到整个模块组，
护栏语义不变（施工图 §2.6）。

用法（测试文件内）::

    from tests._bridge_source import bridge_source
    src = bridge_source()
"""
import glob
import os

from qwen_app import chat_bridge

# chat_bridge.py 本体 + 同目录全部 _bridge_*.py（mixin 模块）
_BRIDGE_FILES = [chat_bridge.__file__] + sorted(
    glob.glob(os.path.join(os.path.dirname(chat_bridge.__file__), "_bridge_*.py")))


def bridge_files():
    """返回 bridge 模块组全部文件绝对路径列表。"""
    return list(_BRIDGE_FILES)


def bridge_source() -> str:
    """拼接 bridge 模块组全部源码（供静态断言扫描）。"""
    return "\n".join(open(p, encoding="utf-8").read() for p in _BRIDGE_FILES)
