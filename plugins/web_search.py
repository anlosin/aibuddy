"""网页搜索技能 —— 通过 DuckDuckGo 搜索网页（三重回退策略）"""
import sys
import urllib.request
import urllib.parse

PLUGIN_INFO = {
    "name": "网页搜索",
    "description": "通过 DuckDuckGo 搜索互联网信息，获取最新资讯",
    "version": "1.1.0",
}

SYSTEM_PROMPT = """你是一个可以搜索互联网的AI助手。当用户询问最新信息、实时数据、新闻事件时，请使用 web_search 工具搜索互联网获取准确信息。

重要规则：
1. 涉及最新新闻、实时数据、当前事件时，必须先搜索再回答
2. 搜索结果可能不完美，请整合多个结果给用户最佳答案
3. 搜索时使用简洁的关键词，不要用完整句子"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网，获取最新信息和网页摘要。适用于查询新闻、实时数据、百科知识等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，使用简洁的关键词组合，例如 'Python 3.14 新特性' 而非 'Python最新版本有什么新功能'"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回的最大结果数量，默认5，最多10",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# ── 策略1：duckduckgo_search / ddgs 库（最稳定） ──
def _search_via_library(query, max_results=5):
    """通过 duckduckgo_search / ddgs 库搜索"""
    # 先后尝试新包名 ddgs 和旧包名 duckduckgo_search
    DDGS = None
    for package_name in ("ddgs", "duckduckgo_search"):
        try:
            mod = __import__(package_name, fromlist=["DDGS"])
            DDGS = mod.DDGS
            break
        except ImportError:
            continue

    if DDGS is None:
        return None  # 回退到策略2

    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return None
        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            body = r.get("body", "")
            href = r.get("href", "")
            lines.append(f"{i}. {title}\n   {body}\n   🔗 {href}")
        return "\n\n".join(lines)
    except ImportError:
        return None  # 回退到策略2
    except Exception as e:
        print(f"[web_search] 库搜索错误: {e}")
        return None


# ── 策略2: DuckDuckGo Instant Answer API（零依赖、免费） ──
def _search_via_api(query, max_results=5):
    """通过 api.duckduckgo.com 即时答案 API 搜索 + 相关主题"""
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        })
        url = f"https://api.duckduckgo.com/?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "QwenAssistant/1.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))

        lines = []

        # 摘要 / 即时答案
        abstract = data.get("AbstractText", "")
        if abstract:
            source = data.get("AbstractSource", data.get("AbstractURL", ""))
            lines.append(f"📌 摘要: {abstract}")
            if source:
                lines.append(f"   来源: {source}")

        # 相关主题
        related = data.get("RelatedTopics", [])
        count = 0
        for topic in related:
            if "Text" in topic:
                text = topic["Text"]
                first_url = topic.get("FirstURL", "")
                lines.append(f"{count + 1}. {text}")
                if first_url:
                    lines.append(f"   🔗 {first_url}")
                count += 1
                if count >= max_results:
                    break
            elif "Topics" in topic:  # 拆解子主题
                for sub in topic["Topics"][:max_results - count]:
                    lines.append(f"{count + 1}. {sub.get('Text', '')}")
                    if sub.get("FirstURL"):
                        lines.append(f"   🔗 {sub['FirstURL']}")
                    count += 1
                    if count >= max_results:
                        break

        if not lines:
            return None
        return "\n\n".join(lines)

    except Exception as e:
        print(f"[web_search] API 错误: {e}")
        return None


# ── 策略3: DuckDuckGo HTML 抓取（最终回退，使用正则提取） ──
def _search_via_html(query, max_results=5):
    """通过正则匹配从 DuckDuckGo HTML 页面提取结果（不再依赖特定 CSS 类名）"""
    try:
        import re
        url = "https://html.duckduckgo.com/html/?"
        params = urllib.parse.urlencode({"q": query})
        req = urllib.request.Request(
            url + params,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")

        # 策略 A: 使用灵活的类名匹配
        import html
        from html.parser import HTMLParser

        class FlexibleParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results = []
                self._current = {}
                self._depth = 0
                self._text_depth = 0
                self._text = ""

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                cls = attrs_dict.get("class", "")
                if tag == "div" and any(x in cls for x in ("result", "Result")):
                    self._depth += 1
                    if self._depth == 1:
                        self._current = {}
                        self._text = ""
                if self._depth > 0 and tag == "a":
                    href = attrs_dict.get("href", "")
                    if href and "//duckduckgo.com/l/" not in href:
                        self._current["href"] = href
                    self._text_depth += 1

            def handle_endtag(self, tag):
                if tag == "div" and self._depth > 0:
                    self._depth -= 1
                    if self._depth == 0:
                        text = self._text.strip()
                        if text:
                            self._current["snippet"] = text
                        if self._current.get("title") or self._current.get("snippet"):
                            self.results.append(dict(self._current))
                if tag == "a" and self._text_depth > 0:
                    self._text_depth -= 1
                    if self._text_depth == 0 and self._text.strip() and "title" not in self._current:
                        self._current["title"] = html.unescape(self._text.strip())

            def handle_data(self, data):
                if self._depth > 0:
                    self._text += data

        parser = FlexibleParser()
        parser.feed(content)

        if parser.results:
            lines = []
            for i, r in enumerate(parser.results[:max_results], 1):
                title = r.get("title", "无标题")
                snippet = r.get("snippet", "")
                href = r.get("href", "")
                line = f"{i}. {title}"
                if snippet:
                    line += f"\n   {snippet}"
                if href:
                    line += f"\n   🔗 {href}"
                lines.append(line)
            if lines:
                return "\n\n".join(lines)

        # 策略 B: 正则提取 <a class="result-link"> 和后续文本
        title_pattern = re.findall(
            r'class="[^"]*result[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>',
            content, re.DOTALL | re.IGNORECASE
        )
        snippet_pattern = re.findall(
            r'class="[^"]*snippet[^"]*"[^>]*>(.*?)</',
            content, re.DOTALL | re.IGNORECASE
        )

        if title_pattern:
            lines = []
            for i in range(min(len(title_pattern), max_results)):
                title = re.sub(r'<[^>]+>', '', title_pattern[i]).strip()
                title = html.unescape(title)
                snippet = ""
                if i < len(snippet_pattern):
                    snippet = re.sub(r'<[^>]+>', '', snippet_pattern[i]).strip()
                    snippet = html.unescape(snippet)
                line = f"{i + 1}. {title}"
                if snippet:
                    line += f"\n   {snippet}"
                lines.append(line)
            if lines:
                return "\n\n".join(lines)

        return None

    except Exception as e:
        print(f"[web_search] HTML 抓取错误: {e}")
        return None


# ── 主搜索入口（三重回退） ──
def _search_duckduckgo(query, max_results=5):
    """三重回退策略搜索"""
    if not query or not query.strip():
        return "请提供搜索关键词。"

    # 策略1：duckduckgo_search 库
    result = _search_via_library(query, max_results)
    if result:
        return f"[搜索: {query}]\n\n{result}"

    # 策略2：DuckDuckGo Instant Answer API
    result = _search_via_api(query, max_results)
    if result:
        return f"[搜索: {query}]\n\n{result}"

    # 策略3：HTML 正则抓取
    result = _search_via_html(query, max_results)
    if result:
        return f"[搜索: {query}]\n\n{result}"

    return (f"未能从联网搜索获取「{query}」的结果。\n"
            f"提示：若当前处于内网/离线环境，外网搜索不可用，"
            f"建议改用知识库插件（先 kb_build 构建本地资料索引，再 kb_search 检索）。")


def execute(tool_name, arguments):
    if tool_name == "web_search":
        return _search_duckduckgo(
            arguments.get("query", ""),
            arguments.get("max_results", 5)
        )
    return f"未知工具: {tool_name}"
