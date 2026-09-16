# -*- coding: utf-8 -*-
"""Day 20.6.8 护栏：web_search 插件 Bing 首选策略（Day 20.6.8, websearch 修复）

背景：DuckDuckGo 在国内网络不可达（三策略全部超时），插件从未成功搜索过。
修复：新增 Bing 抓取策略并设为首选（国内直连可达），DDG 三条降级保留。
护栏全部离线：mock urllib.request.urlopen，不发真实请求。
"""
import os
import sys
import unittest
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIXTURE = """<html><body>
<ol id="b_results">
<li class="b_algo"><h2><a href="https://www.bing.com/">Search - Microsoft Bing</a></h2>
<p class="b_lineclamp4">Bing promo page</p></li>
<li class="b_algo"><h2><a href="https://example.com/a">第一条结果</a></h2>
<p class="b_lineclamp4">第一条摘要内容</p></li>
<li class="b_algo"><h2><a href="https://example.com/b">第二条结果</a></h2>
<p>第二条摘要</p></li>
</ol></body></html>"""


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "web_search_guard", os.path.join(ROOT, "plugins", "web_search.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestBingFirst(unittest.TestCase):
    def setUp(self):
        self.ws = _load_plugin()

    def test_bing_parses_fixture(self):
        """Bing 策略能解析 b_algo 块：标题/摘要/链接齐全"""
        import urllib.request
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResp(FIXTURE)
        try:
            r = self.ws._search_via_bing("q", 5)
        finally:
            urllib.request.urlopen = orig
        self.assertIsNotNone(r)
        self.assertIn("第一条结果", r)
        self.assertIn("第一条摘要内容", r)
        self.assertIn("https://example.com/a", r)
        self.assertIn("第二条结果", r)

    def test_bing_filters_self_promo(self):
        """Bing 自家首页推广项被过滤，不出现在结果里"""
        import urllib.request
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _FakeResp(FIXTURE)
        try:
            r = self.ws._search_via_bing("q", 5)
        finally:
            urllib.request.urlopen = orig
        self.assertNotIn("Search - Microsoft Bing", r)
        self.assertNotIn("Bing promo page", r)
        # 过滤后第一条应为真实结果
        self.assertTrue(r.startswith("1. 第一条结果"))

    def test_main_entry_bing_first(self):
        """主入口首选 Bing：Bing 有结果时直接返回，不碰 DDG"""
        self.ws._search_via_bing = lambda q, n=5: "BING_OK"
        self.ws._search_via_library = lambda q, n=5: (_ for _ in ()).throw(
            AssertionError("Bing 成功时不应调用 DDG 库策略"))
        r = self.ws._search_duckduckgo("q", 5)
        self.assertIn("BING_OK", r)

    def test_main_entry_falls_through_when_bing_none(self):
        """Bing 返回 None 时降级到 DDG 库策略"""
        self.ws._search_via_bing = lambda q, n=5: None
        self.ws._search_via_library = lambda q, n=5: "DDG_LIB_OK"
        r = self.ws._search_duckduckgo("q", 5)
        self.assertIn("DDG_LIB_OK", r)

    def test_version_bumped(self):
        """版本号更新为 1.2.x（Bing 首选）"""
        self.assertEqual(self.ws.PLUGIN_INFO["version"].split(".")[0:2], ["1", "2"])
        self.assertIn("Bing", self.ws.PLUGIN_INFO["description"])


if __name__ == "__main__":
    unittest.main()
