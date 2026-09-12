"""A1：聊天消息渲染统一抽象层。

两条 UI 路径共用一个抽象：

- QtQuick 路径（chat_bridge.py → Main.qml MessageBubble）需要纯字符串
  （RichText 富文本或 Markdown 原文），由 QML delegate 渲染
- PyQt5 路径（chat_window.py → QTextEdit）需要 HTML 字符串，
  由 Qt QTextEdit 直接插入

MessageRenderer 把"消息内容 + 主题 + 消息类型"映射成两种产物：
- render_text(): 给 QtQuick 用（PlainText 流式 / RichText finalize）
- render_html(): 给 PyQt5 用（HTML 表格气泡）

设计原则：
- 无状态：所有方法纯函数，不依赖全局
- 共享工具函数：markdown_to_html / sanitize / build_bubble 都在这里
- 两条路径都调这个模块，bug 修一处即可
"""
from . import theme as _theme
from .sanitizer import sanitize


def sanitize_text(text: str) -> str:
    """LaTeX 残留清洗（chat_window 与 chat_bridge 之前各自调一次 sanitizer.sanitize）。"""
    return sanitize(text or "")


def render_text_final(who: str, raw_text: str, theme: str = "light") -> str:
    """给 QtQuick MessageBubble 用：富文本（已用 markdown_to_html 渲染）。

    who 决定是否走 markdown：tool_call / tool_result 走 PlainText 避免 markdown 干扰，
    user/ai/thinking 走 RichText。
    """
    if not raw_text:
        return ""
    is_tool = who in ("tool_call", "tool_result")
    # tool 类型 PlainText 避免 markdown 干扰（参数 JSON 里有 ** 时会误吃）
    if is_tool:
        return _html_escape(raw_text)
    return _theme.markdown_to_html(raw_text, theme)


def render_html_bubble(theme: str, sender: str, text: str, tag: str, time_str: str) -> str:
    """给 PyQt5 QTextEdit 用：完整的 HTML 表格气泡（与 build_bubble 等价）。"""
    return _theme.build_bubble(theme, sender, text, tag, time_str)


def _html_escape(s: str) -> str:
    import html
    return html.escape(s, quote=False)
