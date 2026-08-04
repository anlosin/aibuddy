"""AI 对话助手 — 入口"""
import sys
import os

# 抑制 libpng iCCP 警告（PyQt5 自带图标色彩配置不规范，不影响功能）
import warnings
warnings.filterwarnings("ignore", message=".*iCCP.*")

class _StderrFilter:
    """过滤 stderr 中 libpng 的 iCCP 警告"""
    def __init__(self, real_stderr):
        self._stderr = real_stderr
    def write(self, s):
        if "iCCP" not in s and "cHRM" not in s and "sRGB" not in s:
            self._stderr.write(s)
    def flush(self):
        self._stderr.flush()

sys.stderr = _StderrFilter(sys.stderr)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from chat_window import ChatWindow

if __name__ == "__main__":
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 全局加固：Fusion 风格下，自定义白底 QSS 的 QComboBox 弹窗项在 hover/选中时
    # 文字颜色未被显式定义会继承成透明（悬停即空白行）。此处统一给所有下拉弹窗
    # 的 item 各状态定义深色文字 + 浅色背景，彻底杜绝该 bug。
    app.setStyleSheet("""
        QComboBox QAbstractItemView {
            background: #FFFFFF;
            border: 1px solid #E5E6EB;
            border-radius: 6px;
            outline: 0;
            selection-background-color: #EEF1FF;
            selection-color: #333;
        }
        QComboBox QAbstractItemView::item {
            color: #333;
            padding: 6px 12px;
        }
        QComboBox QAbstractItemView::item:selected {
            color: #333;
            background: #EEF1FF;
        }
        QComboBox QAbstractItemView::item:hover {
            color: #333;
            background: #F2F4F8;
        }
    """)

    font = app.font()
    font.setFamily("Microsoft YaHei")
    app.setFont(font)

    window = ChatWindow()
    window.show()
    sys.exit(app.exec_())
