"""QML demo 离屏截图 — 浅色/深色各一张。

用法：
    QT_QPA_PLATFORM=offscreen QSG_RHI_BACKEND=software \
        ./.venv/Scripts/python.exe -m qwen_app.qml_screenshot
"""
import os
import sys


def render_theme(theme_name, out_path, app, engine):
    """复用传入的 app/engine，切换主题后截图"""
    from PyQt5.QtCore import QTimer
    from PyQt5.QtQuick import QQuickWindow

    roots = engine.rootObjects()
    if not roots:
        print(f"ERROR [{theme_name}]: QML 加载失败")
        return
    win = roots[0]
    win.setProperty("themeName", theme_name)

    captured = []
    def do_shot():
        content = win.contentItem() if hasattr(win, "contentItem") else None
        if content is None:
            print(f"FAIL [{theme_name}]: no contentItem")
            QTimer.singleShot(100, app.quit)
            return
        result = content.grabToImage()
        if result is None:
            print(f"FAIL [{theme_name}]: grabToImage returned None")
            QTimer.singleShot(100, app.quit)
            return
        def on_ready():
            if result.image().save(out_path):
                print(f"OK [{theme_name}]: {out_path} "
                      f"({os.path.getsize(out_path)} bytes)")
            else:
                print(f"FAIL [{theme_name}]: save failed")
            QTimer.singleShot(100, app.quit)
        result.ready.connect(on_ready)

    QTimer.singleShot(2500, do_shot)
    app.exec_()


def main():
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PyQt5.QtCore import QUrl
    from PyQt5.QtGui import QGuiApplication
    from PyQt5.QtQml import QQmlApplicationEngine

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda warns: [print("QML WARN:", w.toString(), file=sys.stderr) for w in warns])
    qml_dir = os.path.join(os.path.dirname(__file__), "qml")
    engine.addImportPath(qml_dir)
    engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, "Main.qml")))

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_light = os.path.join(repo, "preview_qtquick_light.png")
    out_dark = os.path.join(repo, "preview_qtquick_dark.png")
    render_theme("light", out_light, app, engine)
    render_theme("dark", out_dark, app, engine)


if __name__ == "__main__":
    main()
