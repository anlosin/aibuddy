"""QtQuick demo 入口（feature/qtquick-ui 分支专用）。

不改 main.py。独立运行：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m qwen_app.qml_demo
"""
import os
import sys


def main():
    # 强制 offscreen 渲染（截图场景）
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PyQt5.QtCore import QUrl
    from PyQt5.QtGui import QGuiApplication
    from PyQt5.QtQml import QQmlApplicationEngine

    app = QGuiApplication(sys.argv)
    app.setOrganizationName("aibuddy")
    app.setApplicationName("QtQuickDemo")

    engine = QQmlApplicationEngine()
    # 捕获加载错误
    engine.warnings.connect(lambda warns: [print("QML WARN:", w.toString(), file=sys.stderr) for w in warns])
    qml_dir = os.path.join(os.path.dirname(__file__), "qml")
    engine.addImportPath(qml_dir)
    engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, "Main.qml")))

    if not engine.rootObjects():
        print("ERROR: QML 加载失败")
        sys.exit(1)
    print("QtQuick demo loaded OK")
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
