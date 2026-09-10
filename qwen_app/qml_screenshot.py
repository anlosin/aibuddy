"""QML demo 离屏截图 — 浅色/深色 + Day1-2 流式演示。

用法：
    QT_QPA_PLATFORM=offscreen QSG_RHI_BACKEND=software \
        ./.venv/Scripts/python.exe -m qwen_app.qml_screenshot
    或加参数 day12 跑 Day 1-2 流式演示
"""
import os
import sys

from PyQt5.QtCore import QUrl, QTimer
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlApplicationEngine


def _grab_to_file(engine, app, out_path, label):
    from PyQt5.QtQuick import QQuickWindow
    win = engine.rootObjects()[0]
    if isinstance(win, QQuickWindow):
        content = win.contentItem()
    else:
        try:
            import PyQt5.sip as sip
            qquick = sip.cast(win, QQuickWindow)
            content = qquick.contentItem()
        except Exception as e:
            print(f"FAIL [{label}]: cast err {e}")
            QTimer.singleShot(100, app.quit)
            return
    if content is None:
        print(f"FAIL [{label}]: no contentItem (win type={type(win).__name__})")
        QTimer.singleShot(100, app.quit)
        return
    result = content.grabToImage()
    def on_ready():
        if result.image().save(out_path):
            print(f"OK [{label}]: {out_path} ({os.path.getsize(out_path)} bytes)")
        else:
            print(f"FAIL [{label}]: save failed")
        QTimer.singleShot(100, app.quit)
    result.ready.connect(on_ready)


def render_static_themes(repo, app, engine, bridge):
    plans = [("light", "preview_qtquick_light.png"),
             ("dark",  "preview_qtquick_dark.png")]
    for theme, fname in plans:
        out_path = os.path.join(repo, fname)
        bridge.set_theme(theme)
        QTimer.singleShot(800, lambda p=out_path, t=theme: _grab_to_file(engine, app, p, t))
        app.exec_()


def render_day12(repo, app, engine, bridge):
    out_path = os.path.join(repo, "preview_qtquick_day12.png")
    print(f"Day 1-2 截图: {out_path}")

    def step1_send():
        print("  [step1] bridge.send_message('帮我写个 Python 快速排序')")
        bridge.send_message("帮我写个 Python 快速排序")
        # Day 3-5: 真实流式（fake client 每 80ms 推一个 chunk，约 1.5s 跑完）
        QTimer.singleShot(5000, step2_grab)

    def step2_grab():
        _grab_to_file(engine, app, out_path, "day12")

    QTimer.singleShot(2500, step1_send)
    app.exec_()


def render_day11(repo, app, engine, bridge):
    """Day 11: 工具调用 UI 演示 — 直接 emit messageAdded 模拟 worker.tool_call_start/result"""
    out_path = os.path.join(repo, "preview_qtquick_day11.png")
    print(f"Day 11 截图（工具调用）: {out_path}")

    def step1_seed_user():
        # 1) 用户消息
        bridge.messageAdded.emit("user", "帮我查一下天气", "10:30", False, "")

    def step2_seed_tool_call():
        # 2) 工具调用（chat_bridge._on_worker_tool_call_start 的逻辑）
        bridge._on_worker_tool_call_start("get_weather", '{"city": "上海", "unit": "celsius"}')

    def step3_seed_tool_result():
        # 3) 工具结果
        result = "上海当前天气：晴，气温 18°C，湿度 45%，东南风 3 级。空气质量指数 AQI=65（良）。"
        bridge._on_worker_tool_call_result("get_weather", '{"city": "上海"}', result)

    def step4_seed_ai():
        # 4) AI 总结回答
        bridge.messageAdded.emit("ai", "", "10:31", False, "")
        bridge.messageAdded.emit("ai", "上海今天是晴天，气温 18°C，空气质量良好，适合户外活动。", "10:31", False, "")

    def step5_grab():
        _grab_to_file(engine, app, out_path, "day11")

    QTimer.singleShot(1500, step1_seed_user)
    QTimer.singleShot(2000, step2_seed_tool_call)
    QTimer.singleShot(2500, step3_seed_tool_result)
    QTimer.singleShot(3000, step4_seed_ai)
    QTimer.singleShot(4500, step5_grab)
    app.exec_()


def main():
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from .chat_bridge import ChatBridge

    want_day12 = "day12" in sys.argv
    want_day11 = "day11" in sys.argv
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda warns: [print("QML WARN:", w.toString(), file=sys.stderr) for w in warns])

    # 关键：bridge 必须在 engine.load() 之前注入
    bridge = ChatBridge(theme="light")
    # Day 9: screenshot 截图场景默认用 fake（不烧 token），用户跑 main.py 才是真 LLM
    from . import chat_bridge as _cb_mod
    _orig_start = _cb_mod.ChatBridge.start_real_chat
    _cb_mod.ChatBridge.start_real_chat = lambda self, text, **kw: _orig_start(self, text, use_fake=True)
    engine.rootContext().setContextProperty("bridge",bridge)

    qml_dir = os.path.join(os.path.dirname(__file__), "qml")
    engine.addImportPath(qml_dir)
    engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, "Main.qml")))

    if not engine.rootObjects():
        print("ERROR: QML 加载失败")
        sys.exit(1)

    win = engine.rootObjects()[0]
    bridge_loaded = win.property("bridge") is not None
    print(f"  bridge 属性已注入: {bridge_loaded}")

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if want_day12:
        render_day12(repo, app, engine, bridge)
    elif want_day11:
        render_day11(repo, app, engine, bridge)
    else:
        render_static_themes(repo, app, engine,bridge)


if __name__ == "__main__":
    main()
