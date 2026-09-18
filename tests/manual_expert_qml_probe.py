# -*- coding: utf-8 -*-
"""Day 20.6.15 一次性冒烟：offscreen 真加载 Main.qml，验证专家下拉框接线。

验证点（用户报「专家好像没有了」的修复）：
1. Main.qml 真加载出 1 个窗口
2. expertCombo 拿到 3 个专家且高亮 current
3. bridge.set_expert() → expertChanged → QML model 重拉 + currentIndex 跟随
   （此前只重拉 model 不回写 index，会高亮旧项 —— 已修）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.argv = ["qwen"]

from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QApplication
from PyQt5.QtQml import QQmlApplicationEngine

app = QApplication(sys.argv)
from qwen_app.chat_bridge import ChatBridge

bridge = ChatBridge(theme="light")
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("bridge", bridge)
engine.load(os.path.abspath("qwen_app/qml/Main.qml"))
wins = engine.rootObjects()
print("rootObjects:", len(wins))
assert wins, "Main.qml 加载失败"
win = wins[0]

combo = win.findChild(QObject, "expertCombo")
assert combo is not None, "找不到 expertCombo（objectName 丢失？）"
print("expertCombo 初始: count =", combo.property("count"),
      "currentIndex =", combo.property("currentIndex"))
assert combo.property("count") == 3, "应有 3 个专家"

# 真切换一次：set_expert → expertChanged → QML 重拉 + index 跟随
before = combo.property("currentIndex")
assert bridge.set_expert("developer"), "set_expert(developer) 失败"
app.processEvents()
after_model = combo.property("model")
after_idx = combo.property("currentIndex")
print("切换后: currentIndex =", after_idx,
      "model[after_idx] =", after_model[after_idx]["id"] if after_model else None)
assert after_idx != before, "currentIndex 未跟随（高亮旧项的老 bug 回归）"
assert after_model[after_idx]["id"] == "developer", "高亮的不是 developer"

# 再切回 general，确认双向都跟随
assert bridge.set_expert("general")
app.processEvents()
m2 = combo.property("model")
i2 = combo.property("currentIndex")
print("切回 general: currentIndex =", i2, "->", m2[i2]["id"])
assert m2[i2]["id"] == "general"

# 持久化：cfg 里 current_expert 已写为 general（含本次冒烟的切换链）
from qwen_app import config as C
print("cfg.current_expert =", C.load_config().get("current_expert"))
print("QML SMOKE OK")
