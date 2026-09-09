"""QML 聊天窗口入口（阶段 2 Day 1-2）。

把 ChatBridge 通过 setContextProperty 注入到 QML 上下文，
QML 端通过 `bridge` 属性访问 Python 对象与信号。

用法：
    QT_QPA_PLATFORM=offscreen QSG_RHI_BACKEND=software \
        ./.venv/Scripts/python.exe -m qwen_app.chat_qml_window
"""
import os
import sys


def main():
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PyQt5.QtCore import QUrl
    from PyQt5.QtGui import QGuiApplication
    from PyQt5.QtQml import QQmlApplicationEngine

    from .chat_bridge import ChatBridge

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda warns: [print("QML WARN:", w.toString(), file=sys.stderr) for w in warns])

    # 注入桥
    bridge = ChatBridge(theme="light")
    engine.rootContext().setContextProperty("bridge", bridge)

    qml_dir = os.path.join(os.path.dirname(__file__), "qml")
    engine.addImportPath(qml_dir)
    engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, "Main.qml")))

    roots = engine.rootObjects()
    if not roots:
        print("ERROR: QML 加载失败")
        sys.exit(1)
    win = roots[0]
    print("QtQuick window loaded:", type(win).__name__)
    print(f"  bridge theme = {bridge.get_theme()}")
    print(f"  qml themeName = {win.property('themeName')}")
    print("试试在输入框里发消息 — 3 秒后会有 mock AI 流式回复")

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
