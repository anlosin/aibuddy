# -*- coding: utf-8 -*-
"""Day 20.6.22 自动化任务 tool_call 修复护栏

背景：weather 自动化任务 139 次执行全部「工具调用（0 次）」——
模型把调用意图写成正文 <tool_call>{...}</tool_call>（文本协议），
而 run_automation 只读 OpenAI 结构化 message.tool_calls 字段。

- P0：文本 <tool_call> 必须 fallback 解析并真实 dispatch
- P1：system prompt 必须注入当前真实时间（防「明天」→ 幻觉 2023 日期）
- P2：agent 循环必须有总耗时上限（9-20 三连跑各 ~360s error 空回复）
- P4：工具引导必须点名语义最匹配工具（查天气用 get_weather 非 web_search）
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import qwen_app.scheduler as sched


class _FakeClient:
    """按序返回预设响应，记录每次 create 的 kwargs。"""

    def __init__(self, responses):
        self._rs = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._rs.pop(0)


def _resp(content, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _tc(name, arguments, tc_id="c1"):
    return SimpleNamespace(
        id=tc_id, function=SimpleNamespace(name=name, arguments=arguments))


class _DispatchPatcher:
    """patch 模块属性 sched.dispatch_tool（slot 引用模块加载时名字，
    按 memory 教训 #17 必须补丁模块属性而非内部实现）。"""

    def __init__(self, result="tool-result"):
        self.result = result
        self.calls = []
        self._orig = None

    def __enter__(self):
        self._orig = sched.dispatch_tool

        def _fake(plugins, enabled, name, args):
            self.calls.append((name, args))
            return self.result
        sched.dispatch_tool = _fake
        return self

    def __exit__(self, *exc):
        sched.dispatch_tool = self._orig
        return False


def _run(auto, client, max_rounds=3, **kw):
    return sched.run_automation(
        auto, client, "test-model", [], [],
        enable_thinking=False, enable_tools=True,
        max_rounds=max_rounds, **kw)


class TestParseTextToolCalls(unittest.TestCase):
    """P0：_parse_text_tool_calls 解析器单元测试"""

    def test_qwen_style_single_call(self):
        content = '我先查天气\n<tool_call>\n{"name": "get_weather", ' \
                  '"arguments": {"city": "上海"}}\n</tool_call>'
        calls = sched._parse_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        name, args, _matched = calls[0]
        self.assertEqual(name, "get_weather")
        self.assertEqual(args, {"city": "上海"})

    def test_openai_nested_function_form(self):
        content = ('<tool_call>{"function": {"name": "web_search", '
                   '"arguments": {"query": "k8s"}}}</tool_call>')
        calls = sched._parse_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "web_search")
        self.assertEqual(calls[0][1], {"query": "k8s"})

    def test_multiple_blocks(self):
        content = ('<tool_call>{"name": "a", "arguments": {}}</tool_call>'
                   '中间文本'
                   '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>')
        calls = sched._parse_text_tool_calls(content)
        self.assertEqual([c[0] for c in calls], ["a", "b"])

    def test_arguments_as_json_string(self):
        content = ('<tool_call>{"name": "a", '
                   '"arguments": "{\\"q\\": 1}"}</tool_call>')
        calls = sched._parse_text_tool_calls(content)
        self.assertEqual(calls[0][1], {"q": 1})

    def test_invalid_json_silently_ignored(self):
        content = '<tool_call>not json at all</tool_call>'
        self.assertEqual(sched._parse_text_tool_calls(content), [])

    def test_missing_name_ignored(self):
        content = '<tool_call>{"arguments": {}}</tool_call>'
        self.assertEqual(sched._parse_text_tool_calls(content), [])

    def test_no_tool_call_returns_empty(self):
        self.assertEqual(sched._parse_text_tool_calls("普通回复"), [])
        self.assertEqual(sched._parse_text_tool_calls(""), [])
        self.assertEqual(sched._parse_text_tool_calls(None), [])


class TestRunAutomationTextToolCall(unittest.TestCase):
    """P0 集成：文本 <tool_call> 走真实 dispatch 并把结果回喂模型"""

    def test_text_tool_call_dispatched_then_final(self):
        client = _FakeClient([
            _resp('查天气\n<tool_call>{"name": "get_weather", '
                  '"arguments": {"city": "上海"}}</tool_call>'),
            _resp("上海明天晴，25 度。"),
        ])
        with _DispatchPatcher() as dp:
            final, tool_logs, error = _run({"prompt": "查天气"}, client)
        self.assertEqual(error, "")
        self.assertEqual(final, "上海明天晴，25 度。")
        # 真实 dispatch 了文本协议里的调用
        self.assertEqual(dp.calls, [("get_weather", {"city": "上海"})])
        self.assertEqual(len(tool_logs), 1)
        self.assertEqual(tool_logs[0][0], "get_weather")
        # 第二轮请求里必须带 tool 角色结果 + assistant tool_calls 结构
        second = client.calls[1]["messages"]
        roles = [m["role"] for m in second]
        self.assertIn("tool", roles)
        asst = next(m for m in second if m["role"] == "assistant")
        self.assertEqual(asst["tool_calls"][0]["function"]["name"],
                         "get_weather")
        # assistant content 里不应残留 <tool_call> 原文
        self.assertNotIn("<tool_call>", asst["content"])

    def test_structured_tool_calls_still_work(self):
        client = _FakeClient([
            _resp(None, tool_calls=[
                _tc("get_weather", '{"city": "北京"}', tc_id="call_x")]),
            _resp("北京晴。"),
        ])
        with _DispatchPatcher() as dp:
            final, tool_logs, error = _run({"prompt": "查天气"}, client)
        self.assertEqual(error, "")
        self.assertEqual(final, "北京晴。")
        self.assertEqual(dp.calls, [("get_weather", {"city": "北京"})])
        second = client.calls[1]["messages"]
        asst = next(m for m in second if m["role"] == "assistant")
        self.assertEqual(asst["tool_calls"][0]["id"], "call_x")

    def test_plain_reply_no_dispatch(self):
        client = _FakeClient([_resp("直接回答，无需工具")])
        with _DispatchPatcher() as dp:
            final, tool_logs, error = _run({"prompt": "hi"}, client)
        self.assertEqual(final, "直接回答，无需工具")
        self.assertEqual(dp.calls, [])
        self.assertEqual(tool_logs, [])

    def test_rounds_exhausted_reports_error(self):
        """轮次用尽仍拿不到最终回复 → 必须带 error 而非静默空回复"""
        body = '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
        n = 4
        client = _FakeClient([_resp(body)] * n)
        with _DispatchPatcher():
            final, tool_logs, error = _run({"prompt": "x"}, client,
                                           max_rounds=2)
        self.assertTrue(error)
        self.assertIn("上限", error)


class TestSystemPromptInjection(unittest.TestCase):
    """P1/P4：时间注入 + 工具语义引导"""

    def test_current_time_injected(self):
        client = _FakeClient([_resp("ok")])
        final, _tl, error = _run({"prompt": "hi"}, client)
        self.assertEqual(error, "")
        first = client.calls[0]["messages"][0]
        self.assertEqual(first["role"], "system")
        self.assertIn("当前真实时间", first["content"])
        # 含今天日期（防幻觉日期的核心锚点）
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), first["content"])
        self.assertIn("禁止自行推测", first["content"])

    def test_tool_guidance_names_semantic_matching(self):
        """有工具时，引导文本必须列出具体工具名 + 语义匹配规则（P4）"""
        import qwen_app.plugin_manager as pm
        fake_tools = [
            {"type": "function", "function": {"name": "get_weather",
                                              "parameters": {}}},
            {"type": "function", "function": {"name": "web_search",
                                              "parameters": {}}},
        ]
        orig_get, orig_sp = pm.get_enabled_tools, pm.get_system_prompts
        pm.get_enabled_tools = lambda *a, **k: fake_tools
        pm.get_system_prompts = lambda *a, **k: ""
        try:
            sys_p = sched._build_system_prompt([], [], enable_tools=True)
        finally:
            pm.get_enabled_tools, pm.get_system_prompts = orig_get, orig_sp
        self.assertIn("get_weather", sys_p)
        self.assertIn("web_search", sys_p)
        self.assertIn("语义最直接匹配", sys_p)
        self.assertIn("禁止编造", sys_p)


class TestRunDeadline(unittest.TestCase):
    """P2：agent 循环总耗时上限"""

    def test_deadline_hits_before_first_call(self):
        client = _FakeClient([_resp("ok")] * 3)
        # 用假 datetime：第一次 now() 算 deadline，第二次已 +1000s
        real_dt = sched.datetime

        class _FakeDateTime(datetime):
            _n = 0

            @classmethod
            def now(cls):
                cls._n += 1
                base = datetime(2026, 9, 20, 12, 0, 0)
                return base if cls._n == 1 else base + timedelta(seconds=1000)

        sched.datetime = _FakeDateTime
        try:
            final, tool_logs, error = _run(
                {"prompt": "x"}, client, max_total_seconds=600)
        finally:
            sched.datetime = real_dt
        self.assertIn("总耗时超过", error)
        # 第一轮 API 调用都没发生就被拦截
        self.assertEqual(client.calls, [])

    def test_task_level_max_total_seconds(self):
        """auto 里的 max_total_seconds 覆盖默认值"""
        self.assertEqual(sched.RUN_MAX_TOTAL_SECONDS, 600)
        auto = {"prompt": "x", "max_total_seconds": 30}
        client = _FakeClient([_resp("ok")])
        with _DispatchPatcher():
            final, _tl, error = sched.run_automation(
                auto, client, "m", [], [],
                enable_thinking=False, enable_tools=True, max_rounds=1,
                max_total_seconds=auto["max_total_seconds"])
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
