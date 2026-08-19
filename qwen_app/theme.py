"""主题与气泡渲染（从 chat_window.py 拆分）。

提供浅色/深色主题配色、整套界面 QSS、消息气泡 HTML 构建，以及把主题
应用到主窗口的薄包装。所有纯函数只依赖传入参数，不持有状态。
"""

import html
import re


def get_palette(theme):
    """返回指定主题下的配色（用于气泡与列表）。"""
    if theme == "dark":
        return {
            "chat_bg": "#161616",
            "ai_bubble": "#2A2A2A", "ai_fg": "#E8E8E8",
            "user_bubble": "#2F7D5B", "user_fg": "#FFFFFF",
            "tool_bg": "#3A3020", "tool_fg": "#E0C089",
            "sys_bg": "#2A2A2A", "sys_fg": "#9A9A9A",
            "err_bg": "#3A1F1F", "err_fg": "#FF9A9A",
            "think_bg": "#242424", "think_fg": "#9A9A9A",
            "avatar_ai": "#4E6EF2", "avatar_user": "#23B36B",
            "avatar_tool": "#E0913A", "avatar_think": "#7A7FB0",
            "ts_fg": "#777777",
            "conv_sel": "#34406B", "conv_hover": "#2A2E38", "conv_fg": "#E5E5E5",
            "conv_time": "#888888",
        }
    return {
        "chat_bg": "#F5F5F7",
        "ai_bubble": "#FFFFFF", "ai_fg": "#1F1F1F",
        "user_bubble": "#95EC69", "user_fg": "#1A1A1A",
        "tool_bg": "#FFF7E6", "tool_fg": "#9A6B1A",
        "sys_bg": "#EDEFF2", "sys_fg": "#8A8F99",
        "err_bg": "#FDECEC", "err_fg": "#D93025",
        "think_bg": "#F0F1F3", "think_fg": "#8A8F99",
        "avatar_ai": "#4E6EF2", "avatar_user": "#23B36B",
        "avatar_tool": "#E0913A", "avatar_think": "#9AA0C0",
            "ts_fg": "#B0B4BD",
            "conv_sel": "#D6E4FF", "conv_hover": "#EEF2FF", "conv_fg": "#333333",
            "conv_time": "#909399",
        }


def theme_qss(theme):
    """返回整套界面 QSS（浅色 / 深色）。"""
    if theme == "dark":
        return """
        QMainWindow { background-color: #1A1A1A; }
        QFrame#sidebar { background-color: #202020; border-right: 1px solid #2C2C2C; }
        QPushButton#btnNewChat { background-color: #2A2A2A; border: 1px solid #3A3A3A; border-radius: 8px; padding: 8px 16px; font-size: 13px; color: #E5E5E5; text-align: left; }
        QPushButton#btnNewChat:hover { background-color: #2F3350; border-color: #4E6EF2; }
        QListWidget#convList { border: none; background: transparent; font-size: 13px; outline: none; padding: 4px 4px; }
        QListWidget#convList::item { background: transparent; padding: 0px; margin: 0px; }
        QLabel#sidebarTitle { font-size: 14px; font-weight: bold; padding: 16px 16px 8px 16px; color: #E5E5E5; }
        QWidget#btnConvMenu { background: transparent; border: none; font-size: 14px; color: #888888; padding: 4px 6px; border-radius: 4px; }
        QWidget#btnConvMenu:hover { background-color: #333333; color: #E5E5E5; }
        QFrame#inputFrame { background-color: #1A1A1A; border-top: 1px solid #2C2C2C; }
        QLineEdit { border: 1px solid #3A3A3A; border-radius: 18px; padding: 10px 16px; font-size: 13px; background: #262626; min-height: 20px; color: #E5E5E5; }
        QLineEdit:focus { border-color: #4E6EF2; }
        QPushButton#btnSend { background-color: #4E6EF2; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
        QPushButton#btnSend:hover { background-color: #3B5BEF; }
        QPushButton#btnSend:pressed { background-color: #2D4CD9; }
        QPushButton#btnSend:disabled { background-color: #3A3F55; }
        QPushButton#btnStop { background-color: #E04848; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
        QPushButton#btnStop:hover { background-color: #CC3333; }
        QWidget#expertBar { background-color: #1E1E1E; border-bottom: 1px solid #2C2C2C; }
        QLabel#expertLabel { font-size: 12px; color: #AAAAAA; }
        QComboBox#expertCombo { border: 1px solid #3A3A3A; border-radius: 6px; padding: 4px 10px; font-size: 12px; color: #E5E5E5; background: #262626; min-height: 20px; }
        QComboBox#expertCombo:focus { border-color: #4E6EF2; }
        QComboBox#expertCombo:on { border-color: #4E6EF2; }
        QComboBox#expertCombo QAbstractItemView { font-size: 12px; background: #262626; border: 1px solid #3A3A3A; border-radius: 6px; outline: 0; selection-background-color: #34406B; selection-color: #E5E5E5; }
        QComboBox#expertCombo QAbstractItemView::item { color: #E5E5E5; padding: 6px 12px; }
        QComboBox#expertCombo QAbstractItemView::item:selected { color: #E5E5E5; background: #34406B; }
        QComboBox#expertCombo QAbstractItemView::item:hover { color: #E5E5E5; background: #2E3340; }
        QTextEdit#chatDisplay { font-family: 'Microsoft YaHei'; font-size: 13px; border: none; background-color: #161616; color: #E8E8E8; }
        QStatusBar { background: #1A1A1A; color: #AAAAAA; }
        QStatusBar::item { border: none; }
        QMenu { background-color: #262626; border: 1px solid #3A3A3A; border-radius: 6px; padding: 4px; }
        QMenu::item { padding: 8px 24px; font-size: 13px; color: #E5E5E5; }
        QMenu::item:selected { background-color: #34406B; border-radius: 4px; }
        """
    return """
        QMainWindow { background-color: #FFFFFF; }
        QFrame#sidebar { background-color: #F7F8FA; border-right: 1px solid #E5E6EB; }
        QPushButton#btnNewChat { background-color: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 8px; padding: 8px 16px; font-size: 13px; color: #333333; text-align: left; }
        QPushButton#btnNewChat:hover { background-color: #EEF2FF; border-color: #4E6EF2; }
        QListWidget#convList { border: none; background: transparent; font-size: 13px; outline: none; padding: 4px 4px; }
        QListWidget#convList::item { background: transparent; padding: 0px; margin: 0px; }
        QLabel#sidebarTitle { font-size: 14px; font-weight: bold; padding: 16px 16px 8px 16px; color: #333333; }
        QWidget#btnConvMenu { background: transparent; border: none; font-size: 14px; color: #999999; padding: 4px 6px; border-radius: 4px; }
        QWidget#btnConvMenu:hover { background-color: #E5E6EB; color: #333333; }
        QFrame#inputFrame { background-color: #F7F8FA; border-top: 1px solid #E5E6EB; }
        QLineEdit { border: 1px solid #E5E6EB; border-radius: 18px; padding: 10px 16px; font-size: 13px; background: #FFFFFF; min-height: 20px; color: #333333; }
        QLineEdit:focus { border-color: #4E6EF2; }
        QPushButton#btnSend { background-color: #4E6EF2; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
        QPushButton#btnSend:hover { background-color: #3B5BEF; }
        QPushButton#btnSend:pressed { background-color: #2D4CD9; }
        QPushButton#btnSend:disabled { background-color: #B0B8D0; }
        QPushButton#btnStop { background-color: #E04848; color: white; border: none; border-radius: 18px; padding: 10px 24px; font-size: 13px; font-weight: bold; }
        QPushButton#btnStop:hover { background-color: #CC3333; }
        QWidget#expertBar { background-color: #F7F8FA; border-bottom: 1px solid #E5E6EB; }
        QLabel#expertLabel { font-size: 12px; color: #666666; }
        QComboBox#expertCombo { border: 1px solid #E5E6EB; border-radius: 6px; padding: 4px 10px; font-size: 12px; color: #333333; background: #FFFFFF; min-height: 20px; }
        QComboBox#expertCombo:focus { border-color: #4E6EF2; }
        QComboBox#expertCombo:on { border-color: #4E6EF2; }
        QComboBox#expertCombo QAbstractItemView { font-size: 12px; background: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; outline: 0; selection-background-color: #EEF1FF; selection-color: #333333; }
        QComboBox#expertCombo QAbstractItemView::item { color: #333333; padding: 6px 12px; }
        QComboBox#expertCombo QAbstractItemView::item:selected { color: #333333; background: #EEF1FF; }
        QComboBox#expertCombo QAbstractItemView::item:hover { color: #333333; background: #F2F4F8; }
        QTextEdit#chatDisplay { font-family: 'Microsoft YaHei'; font-size: 13px; border: none; background-color: #F5F5F7; color: #1F1F1F; }
        QStatusBar { background: #F7F8FA; color: #666666; }
        QStatusBar::item { border: none; }
        QMenu { background-color: #FFFFFF; border: 1px solid #E5E6EB; border-radius: 6px; padding: 4px; }
        QMenu::item { padding: 8px 24px; font-size: 13px; color: #333333; }
        QMenu::item:selected { background-color: #EEF2FF; border-radius: 4px; }
        """


def avatar_td(bg, icon):
    return (f'<td width="36" align="center" style="vertical-align:top;">'
            f'<div style="width:34px;height:34px;border-radius:17px;'
            f'background:{bg};color:#ffffff;text-align:center;'
            f'font-size:18px;line-height:34px;">{icon}</div></td>')


def markdown_to_html(text, theme="light"):
    """把轻量 Markdown 渲染为 QTextEdit 可用的 HTML。

    支持：标题(#~######)、粗体(**x**)、斜体(*x*)、删除线(~~x~~)、
    行内码(`x`)、围栏代码块(```...```)、链接([t](http...))、无序列表、
    水平分割线。所有原文先经 html.escape 防 XSS，仅注入本函数生成的白名单标签。
    """
    if theme == "dark":
        code_bg, code_fg, ic_bg = "#1E1E1E", "#E8E8E8", "#333333"
    else:
        code_bg, code_fg, ic_bg = "#F2F3F5", "#1F1F1F", "#EDEFF2"

    # 1) 抽取围栏代码块（保留原始内容，稍后统一转义）
    fences = []
    def _stash_fence(m):
        fences.append(m.group(2))
        return "\u0000F%d\u0000" % (len(fences) - 1)
    text = re.sub(r"```[ \t]*([^\n`]*)\n?(.*?)```", _stash_fence, text, flags=re.DOTALL)

    # 2) 抽取行内代码
    inlines = []
    def _stash_ic(m):
        inlines.append(m.group(1))
        return "\u0000I%d\u0000" % (len(inlines) - 1)
    text = re.sub(r"`([^`\n]+)`", _stash_ic, text)

    # 3) 转义其余文本（防 XSS 核心步骤）
    text = html.escape(text, quote=False)

    # 4) 标题（行首 #~######）
    out_lines = []
    for ln in text.split("\n"):
        hm = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if hm:
            lvl = len(hm.group(1))
            out_lines.append("<h%d>%s</h%d>" % (lvl, hm.group(2), lvl))
        else:
            out_lines.append(ln)
    text = "\n".join(out_lines)

    # 5) 水平分割线
    text = re.sub(r"^[\-\*_]{3,}\s*$", "<hr>", text, flags=re.MULTILINE)
    # 6) 无序列表
    text = re.sub(r"^([\-\*+])\s+", "• ", text, flags=re.MULTILINE)
    # 7) 粗体 **x**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # 8) 斜体 *x*（仅 * ，避免误伤下划线标识符）
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<i>\1</i>", text)
    # 9) 删除线 ~~x~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # 10) 链接 [t](http...)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)

    # 11) 还原行内代码（已转义）
    def _restore_ic(m):
        return ('<code style="font-family:Consolas,\'Courier New\',monospace;'
                'font-size:12px;background:%s;border-radius:4px;padding:1px 5px;">%s</code>'
                % (ic_bg, html.escape(inlines[int(m.group(1))], quote=False)))
    text = re.sub(r"\u0000I(\d+)\u0000", _restore_ic, text)

    # 12) 换行 -> <br>
    text = text.replace("\n", "<br>")

    # 13) 还原围栏代码块（保留换行，用 <pre>）
    def _restore_fence(m):
        esc = html.escape(fences[int(m.group(1))].rstrip("\n"), quote=False)
        return ('<pre style="font-family:Consolas,\'Courier New\',monospace;'
                'font-size:12px;color:%s;background:%s;border-radius:8px;'
                'padding:10px 12px;white-space:pre-wrap;word-break:break-word;'
                'margin:6px 0;">%s</pre>' % (code_fg, code_bg, esc))
    text = re.sub(r"\u0000F(\d+)\u0000", _restore_fence, text)

    return text


def build_bubble(theme, sender, text, tag, time_str):
    """构建单条消息的 HTML 气泡（含居中时间戳 + 头像 + 气泡）。"""
    p = get_palette(theme)
    # 非 system 消息走轻量 Markdown 渲染；system 提示保持纯文本。
    if tag == "system":
        esc = html.escape(text).replace("\n", "<br>")
    else:
        esc = markdown_to_html(text, theme)
    ts_fg = p["ts_fg"]
    mono = 'font-family:"Consolas","Courier New",monospace;font-size:12px;'
    spacer = '<td width="10%"></td>'

    if tag == "user":
        # 气泡仍位于右侧（符合"自己消息在右"的聊天习惯），但内部文字左对齐，
        # 避免代码/多行内容右对齐导致左边参差、难以阅读。
        bubble = ('<td width="72%" align="left" style="background:{ub};'
                  'color:{uf};border-radius:16px;padding:9px 13px;'
                  'font-family:"Microsoft YaHei";font-size:13px;line-height:1.55;">{esc}</td>'
                  ).format(ub=p["user_bubble"], uf=p["user_fg"], esc=esc)
        av = avatar_td(p["avatar_user"], "🧑")
        row = f'<tr>{spacer}{bubble}{av}</tr>'
    elif tag == "tool":
        bubble = ('<td width="72%" align="left" style="background:{tb};'
                  'color:{tf};border-radius:12px;padding:8px 12px;{mono}'
                  'line-height:1.5;">{esc}</td>'
                  ).format(tb=p["tool_bg"], tf=p["tool_fg"], mono=mono, esc=esc)
        av = avatar_td(p["avatar_tool"], "🔧")
        row = f'<tr>{av}{bubble}{spacer}</tr>'
    elif tag == "thinking":
        bubble = ('<td width="72%" align="left" style="background:{kb};'
                  'color:{kf};border-radius:14px;padding:9px 13px;'
                  'font-family:"Microsoft YaHei";font-size:13px;line-height:1.55;'
                  'font-style:italic;">{esc}</td>'
                  ).format(kb=p["think_bg"], kf=p["think_fg"], esc=esc)
        av = avatar_td(p["avatar_think"], "💭")
        row = f'<tr>{av}{bubble}{spacer}</tr>'
    elif tag == "error":
        bubble = ('<td width="80%" align="left" style="background:{eb};'
                  'color:{ef};border-radius:12px;padding:8px 12px;'
                  'font-family:"Microsoft YaHei";font-size:13px;line-height:1.5;">{esc}</td>'
                  ).format(eb=p["err_bg"], ef=p["err_fg"], esc=esc)
        row = f'<tr>{spacer}{bubble}<td width="10%"></td></tr>'
    elif tag == "system":
        # 系统提示：时间戳 + 居中胶囊，整行放入单一表格
        return (f'<table width="100%" cellpadding="3" cellspacing="0">'
                f'<tr><td align="center" style="color:{ts_fg};font-size:11px;'
                f'font-family:"Microsoft YaHei";padding:4px 0 2px 0;">{time_str}</td></tr>'
                f'<tr><td align="center"><span style="background:{p["sys_bg"]};'
                f'color:{p["sys_fg"]};padding:3px 12px;border-radius:10px;'
                f'font-size:12px;font-family:"Microsoft YaHei";">{esc}</span></td></tr>'
                f'</table>')
    else:  # 默认：AI 回复（左侧，机器人头像）
        bubble = ('<td width="72%" align="left" style="background:{ab};'
                  'color:{af};border-radius:16px;padding:9px 13px;'
                  'font-family:"Microsoft YaHei";font-size:13px;line-height:1.55;">{esc}</td>'
                  ).format(ab=p["ai_bubble"], af=p["ai_fg"], esc=esc)
        av = avatar_td(p["avatar_ai"], "🤖")
        row = f'<tr>{av}{bubble}{spacer}</tr>'

    # 时间戳作为表格首行，用 <td align="center"> 承载（Qt 可靠居中）
    return (f'<table width="100%" cellpadding="3" cellspacing="0">'
            f'<tr><td align="center" colspan="3" style="color:{ts_fg};'
            f'font-size:11px;font-family:"Microsoft YaHei";'
            f'padding:6px 0 2px 0;">{time_str}</td></tr>'
            f'{row}</table>')


def apply_theme_to_window(window, rerender_chat=True):
    """把浅色/深色主题应用到主窗口：注入 QSS 并重渲染列表与聊天气泡。"""
    # 延迟导入避免与 session 形成循环依赖（session 已 import theme）
    from .session import refresh_conv_list, load_current_conv
    window.setStyleSheet(theme_qss(window.theme))
    window.status_label.setStyleSheet(
        f"color: {get_palette(window.theme)['ts_fg']}; padding: 0 8px;")
    refresh_conv_list(window)
    if rerender_chat:
        load_current_conv(window)
