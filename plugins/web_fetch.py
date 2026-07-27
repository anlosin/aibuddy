"""网页抓取插件 — 读取网页内容并提取纯文本"""
import re
import urllib.request
import urllib.error
import urllib.parse
from html.parser import HTMLParser


PLUGIN_INFO = {
    "name": "web_fetch",
    "description": "读取指定网页的文本内容，自动提取正文、去除广告等干扰",
    "version": "1.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "获取指定网址的网页内容，提取纯文本正文。适用于阅读新闻、博客、文档等。需要提供完整URL（含 https://）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要获取的网页完整URL，如 https://example.com/article"
                    }
                },
                "required": ["url"]
            }
        }
    },
]


class _TextExtractor(HTMLParser):
    """HTML→纯文本提取器，跳过 script/style 标签"""
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "iframe"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "iframe"):
            self.skip = False
        # 块级标签后加换行
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                    "br", "tr", "article", "section", "header", "footer"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.skip:
            stripped = data.strip()
            if stripped:
                self.text.append(stripped)


def _clean_text(text):
    """清理多余空行和空白"""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    # 去重连续相同行（常见于广告/模板重复）
    cleaned = []
    for line in lines:
        if not cleaned or line != cleaned[-1]:
            cleaned.append(line)
    return "\n".join(cleaned)


def execute(name, arguments):
    if name != "fetch_webpage":
        return f"未知工具: {name}"

    url = arguments.get("url", "").strip()
    if not url:
        return "错误：未提供URL"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")

            # 非HTML直接返回
            if "text/html" not in content_type and "text/plain" not in content_type:
                return f"不支持的内容类型: {content_type}"

            raw = resp.read()
            # 尝试解码
            charset = None
            for part in content_type.split(";"):
                if "charset" in part:
                    charset = part.split("=")[-1].strip()
            html = raw.decode(charset or "utf-8", errors="replace")

        extractor = _TextExtractor()
        extractor.feed(html)
        text = "".join(extractor.text)
        text = _clean_text(text)

        if len(text) > 8000:
            text = text[:8000] + "\n...[内容已截断，网页原文较长]"

        return f"[{url}]\n\n{text}"

    except urllib.error.HTTPError as e:
        return f"HTTP错误 {e.code}: 无法访问该页面"
    except urllib.error.URLError as e:
        return (f"🌐 网络错误: {e.reason}\n"
                f"提示：当前可能处于内网/离线环境，外网页面无法访问。"
                f"如需读取资料，可先用知识库插件 kb_build 索引本地文档，再 kb_search 检索。")
    except Exception as e:
        return f"网页抓取出错: {e}"
