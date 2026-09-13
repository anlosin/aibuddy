"""Day 19 (M-NEW-3): developer.json 专家配置与项目实际栈对齐测试。

旧版 system_prompt 提到 Laravel / Livewire / Three.js（与本项目 PyQt5/Qwen
完全无关，会干扰模型），新版本必须改为 Python / PyQt5 / QtQuick / QML / venv /
openai client 等真实技术栈，并保留"资深全栈开发工程师"语气 + 4 步回答框架。
"""
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DEVELOPER_JSON = os.path.join(_ROOT, "qwen_app", "experts", "developer.json")


class TestDeveloperExpertProjectStack(unittest.TestCase):
    """M-NEW-3: developer.json 的 system_prompt 必须与本项目实际技术栈匹配。"""

    @classmethod
    def setUpClass(cls):
        with open(DEVELOPER_JSON, encoding="utf-8") as f:
            cls.cfg = json.load(f)

    def test_no_laravel(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertNotIn("Laravel", sp,
                         "M-NEW-3: developer.json 必须删除 Laravel（本项目是 PyQt5）")

    def test_no_livewire(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertNotIn("Livewire", sp,
                         "M-NEW-3: developer.json 必须删除 Livewire（本项目无 PHP）")

    def test_no_three_js(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertNotIn("Three.js", sp,
                         "M-NEW-3: developer.json 必须删除 Three.js（本项目是 QML）")
        self.assertNotIn("three.js", sp,
                         "M-NEW-3: developer.json 必须删除 three.js（小写也算）")

    def test_contains_python(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertIn("Python", sp,
                      "M-NEW-3: developer.json 应提到 Python（项目主力语言）")

    def test_contains_pyqt5(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertIn("PyQt5", sp,
                      "M-NEW-3: developer.json 应提到 PyQt5（GUI 栈）")

    def test_contains_qml_or_qtquick(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertTrue("QML" in sp or "QtQuick" in sp,
                        "M-NEW-3: developer.json 应提到 QML 或 QtQuick（前端栈）")

    def test_mentions_venv_or_openai(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertTrue(".venv" in sp or "openai" in sp.lower(),
                        "M-NEW-3: developer.json 应提到 venv / openai client（项目实际工具）")

    def test_has_four_step_answer_framework(self):
        """保留 4 步回答框架（1 完整代码 / 2 设计决策 / 3 性能与安全 / 4 中文+术语）"""
        sp = self.cfg.get("system_prompt", "")
        # 4 个编号步骤 — 用 "1." / "2." / "3." / "4." 数字枚举
        import re
        steps = re.findall(r"^\s*(\d+)\.", sp, re.MULTILINE)
        self.assertGreaterEqual(
            len(steps), 4,
            f"M-NEW-3: developer.json 必须保留 4 步回答框架（找到 {len(steps)} 步: {steps}）")

    def test_tone_keeps_senior_engineer_role(self):
        sp = self.cfg.get("system_prompt", "")
        self.assertIn("资深", sp,
                      "M-NEW-3: developer.json 必须保留「资深」开发工程师语气")
        self.assertIn("全栈", sp,
                      "M-NEW-3: developer.json 必须保留「全栈」关键词")


if __name__ == "__main__":
    unittest.main(verbosity=2)
