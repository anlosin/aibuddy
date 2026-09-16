# -*- coding: utf-8 -*-
"""Day 20.6.9 护栏：weather v2.0（Open-Meteo 主源）+ web_fetch v1.1（重定向+字符集）

背景（Day 20.6.9）：wttr.in SSL 证书过期 → weather 插件 100% 失败；
web_fetch 不跟随重定向、GBK 页面乱码。护栏全部离线（mock）。
"""
import os
import sys
import unittest
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, alias):
    spec = importlib.util.spec_from_file_location(
        alias, os.path.join(ROOT, "plugins", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestWeatherOpenMeteo(unittest.TestCase):
    def setUp(self):
        self.ws = _load("weather", "weather_guard")

    def test_open_meteo_primary_formats_output(self):
        """Open-Meteo 主源：geocode→forecast 全链路格式化正确"""
        calls = []
        def fake_get(url, timeout):
            calls.append(url.split("?")[0])
            if "geocoding" in url:
                return {"results": [{"latitude": 31.22, "longitude": 121.46,
                                     "name": "上海"}]}
            return {
                "current": {"temperature_2m": 26.9, "apparent_temperature": 26.8,
                            "relative_humidity_2m": 44, "weather_code": 3,
                            "wind_speed_10m": 9.0, "wind_direction_10m": 23},
                "daily": {"time": ["2026-09-16", "2026-09-17", "2026-09-18"],
                          "weather_code": [3, 53, 53],
                          "temperature_2m_max": [30.2, 30.0, 31.1],
                          "temperature_2m_min": [21.0, 22.4, 23.0]},
            }
        self.ws._http_get_json = fake_get
        r = self.ws.execute("get_weather", {"city": "上海"})
        self.assertNotIn("查询失败", r)
        self.assertIn("当前: 阴", r)          # WMO 3 → 阴
        self.assertIn("27°C", r)              # 26.9 四舍五入
        self.assertIn("风速: 9km/h 北东北", r)  # 23° → 北东北
        self.assertIn("2026-09-17: 毛毛雨", r)  # WMO 53
        self.assertEqual(calls[0].startswith("https://geocoding-api.open-meteo.com"), True)
        self.assertEqual(calls[1].startswith("https://api.open-meteo.com"), True)

    def test_city_not_found_404_semantics(self):
        """geocode 无结果 → 提示城市名错误而非崩溃"""
        self.ws._http_get_json = lambda url, timeout: {"results": []}
        r = self.ws.execute("get_weather", {"city": "不存在的城市"})
        self.assertIn("未找到城市", r)

    def test_fallback_to_wttr_when_open_meteo_down(self):
        """Open-Meteo 挂掉 → 回退 wttr.in"""
        def boom(url, timeout):
            raise OSError("open-meteo unreachable")
        self.ws._http_get_json = boom
        def fake_wttr(city, timeout=10):
            return "[wttr 回退成功]"
        self.ws._get_via_wttr = fake_wttr
        r = self.ws.execute("get_weather", {"city": "上海"})
        self.assertIn("wttr 回退成功", r)

    def test_both_sources_down_reports_error(self):
        """两个源都挂 → 明确报错，不抛异常"""
        def boom(url, timeout):
            raise OSError("down")
        self.ws._http_get_json = boom
        r = self.ws.execute("get_weather", {"city": "上海"})
        self.assertIn("查询失败", r)
        self.assertIn("wttr.in", r)

    def test_wmo_mapping(self):
        """WMO 代码映射：0=晴天 95=雷阵雨 未知=代码兜底"""
        self.assertEqual(self.ws._wmo_desc(0), "晴天")
        self.assertEqual(self.ws._wmo_desc(95), "雷阵雨")
        self.assertEqual(self.ws._wmo_desc(999), "天气代码999")


class TestWebFetchRedirectCharset(unittest.TestCase):
    def setUp(self):
        self.wf = _load("web_fetch", "web_fetch_guard")

    def test_follows_redirect_and_returns_final_url(self):
        """301 → 跟随，返回最终 URL 的内容"""
        seq = []
        def fake_fetch(url, timeout=15):
            seq.append(url)
            if url == "http://example.com/a":
                return 301, {"location": "https://example.com/b"}, b""
            return 200, {"content-type": "text/html; charset=utf-8"}, \
                b"<html><body><p>final body</p></body></html>"
        self.wf._ip_safe_fetch = fake_fetch
        r = self.wf.execute("fetch_webpage", {"url": "http://example.com/a"})
        self.assertIn("https://example.com/b", r)
        self.assertIn("final body", r)
        self.assertEqual(seq, ["http://example.com/a", "https://example.com/b"])

    def test_redirect_to_private_ip_blocked(self):
        """外网 302 跳内网 → SSRF 拦截（每跳重新校验）"""
        def fake_fetch(url, timeout=15):
            if url == "http://evil.example.com/x":
                return 302, {"location": "http://192.168.1.1/admin"}, b""
            raise ValueError("禁止访问内网/保留地址: 192.168.1.1")
        self.wf._ip_safe_fetch = fake_fetch
        r = self.wf.execute("fetch_webpage", {"url": "http://evil.example.com/x"})
        self.assertIn("安全拦截", r)

    def test_too_many_redirects(self):
        """重定向环 → 报错而非死循环"""
        def fake_fetch(url, timeout=15):
            return 302, {"location": url + "x"}, b""
        self.wf._ip_safe_fetch = fake_fetch
        r = self.wf.execute("fetch_webpage", {"url": "http://example.com/loop"})
        self.assertIn("网络错误", r)

    def test_gbk_page_decoded(self):
        """GBK 页面（响应头无 charset，meta 有）→ 中文不乱码"""
        html = ('<html><head><meta charset="gbk"></head><body>'
                '<p>上海今天天气不错</p></body></html>').encode("gbk")
        self.wf._ip_safe_fetch = lambda url, timeout=15: (
            200, {"content-type": "text/html"}, html)
        r = self.wf.execute("fetch_webpage", {"url": "https://example.com/cn"})
        self.assertIn("上海今天天气不错", r)
        self.assertNotIn("�", r)

    def test_header_charset_wins_over_meta(self):
        """响应头声明优先于 meta 嗅探"""
        html = '<html><head><meta charset="gbk"></head><body><p>数据</p></body></html>'.encode("utf-8")
        self.wf._ip_safe_fetch = lambda url, timeout=15: (
            200, {"content-type": "text/html; charset=utf-8"}, html)
        r = self.wf.execute("fetch_webpage", {"url": "https://example.com/x"})
        self.assertIn("数据", r)


if __name__ == "__main__":
    unittest.main()
