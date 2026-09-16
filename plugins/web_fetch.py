"""网页抓取插件 — 读取网页内容并提取纯文本

安全：
- Day 18 修复 C4：自实现 HTTP/HTTPS 客户端 + 强制绑定已校验 IP
  （urllib.request.urlopen 在 DNS 校验后还会再解析一次 DNS，
   攻击者用低 TTL DNS 可在两次解析之间切换到内网 IP — TOCTOU）。
  这里校验完成后所有 IO 都通过 socket.create_connection 绑定到
  已校验过的公网 IP，杜绝 TOCTOU 窗口。
"""
import ipaddress
import socket
import ssl
import urllib.request
import urllib.error
import urllib.parse
from html.parser import HTMLParser


PLUGIN_INFO = {
    "name": "web_fetch",
    "description": "读取指定网页的文本内容，自动提取正文、去除广告等干扰",
    "version": "1.1",
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


# Day 19 (H-NEW-4 修复): 限制响应体大小，防止恶意服务端返回 GB 级 body
# 把进程 OOM 死。超过 MAX_BODY_SIZE 截断 + 警告（不抛错，避免 LLM
# 路径上异常导致后续步骤全断）。
MAX_BODY_SIZE = 4 * 1024 * 1024  # 4MB


def _is_public_ip(ip_str):
    """判断 IP 是否为公网地址，拒绝私有/回环/链路本地/保留地址段（防 SSRF）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _validate_and_resolve(url):
    """校验 URL 目标地址安全并返回 (host, port, validated_ip)。

    Day 18 修复 C4：
    - 拒绝非 http(s) 协议
    - host 为 IP 字面量 → 校验段
    - host 为域名 → DNS 解析后对**所有** A/AAAA 记录校验；任一为内网即拒绝
    - 返回一个具体的公网 IP，调用方后续 IO 全部 socket.create_connection 绑定这个 IP，
      避免 urlopen 内部再做 DNS 解析（消除 TOCTOU 窗口）。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https 协议")
    host = parsed.hostname
    if not host:
        raise ValueError("无法解析目标主机")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # 1) host 是 IP 字面量（含 IPv6）→ 直接校验
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(str(literal)):
            raise ValueError(f"禁止访问内网/保留地址: {host}")
        return host, port, str(literal)

    # 2) host 是域名 → DNS 解析后逐 IP 校验，取第一个公网 IP 绑定
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"域名解析失败: {host}")
    validated_ip = None
    for info in infos:
        addr = info[4][0]
        # 去除 IPv4-mapped IPv6 (::ffff:127.0.0.1 这种绕过)
        if addr.startswith("::ffff:"):
            addr = addr[7:]
        if not _is_public_ip(addr):
            raise ValueError(f"禁止访问内网/保留地址: {host} -> {addr}")
        if validated_ip is None:
            validated_ip = addr
    if validated_ip is None:
        raise ValueError(f"域名无可用公网 IP: {host}")
    return host, port, validated_ip


def _ip_safe_fetch(url, timeout=15):
    """Day 18 修复 C4：自实现 HTTP 客户端，绑定已校验 IP 发起请求。

    返回 (status, headers, raw_bytes)。HTTPS 走 ssl.wrap_socket。
    HTTP/1.1 协议层只解析 Content-Length / Transfer-Encoding=chunked；
    简单的 GET 请求足够本插件使用，不实现重定向跟随（手动 redirect
    需重新走 _validate_and_resolve，避免 TOCTOU）。
    """
    host, port, validated_ip = _validate_and_resolve(url)
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query

    # 强制按字面值连接已校验 IP，但 HTTP Host header 用域名（HTTPS SNI 同理）
    sock = socket.create_connection((validated_ip, port), timeout=timeout)
    try:
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)

        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}" + (f":{port}" if port not in (80, 443) else "") + "\r\n"
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"
            "Accept: text/html, text/plain;q=0.9, */*;q=0.8\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.sendall(req.encode("ascii"))

        # 读响应头
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("连接已关闭，未读到响应头")
            chunks.append(chunk)
            buf = b"".join(chunks)
            sep = buf.find(b"\r\n\r\n")
            if sep >= 0:
                head, rest = buf[:sep].decode("iso-8859-1"), buf[sep + 4:]
                break

        # 解析 status line + headers
        lines = head.split("\r\n")
        status_line = lines[0]
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/"):
            raise ValueError(f"非 HTTP 响应: {status_line}")
        status = int(parts[1])
        headers = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, _, v = ln.partition(":")
                headers[k.strip().lower()] = v.strip()

        # 读 body（Day 19 (H-NEW-4): 限制大小防 OOM）
        cl = headers.get("content-length")
        if cl is not None:
            need = int(cl)
            if need > MAX_BODY_SIZE:
                return status, headers, b""  # 上层会看到空 body
            body = rest + _recv_exact(sock, need - len(rest))
            if len(body) > MAX_BODY_SIZE:
                body = body[:MAX_BODY_SIZE]
        elif headers.get("transfer-encoding", "").lower() == "chunked":
            body = _read_chunked(sock, rest)
            if len(body) > MAX_BODY_SIZE:
                body = body[:MAX_BODY_SIZE]
        else:
            # 直到关闭（HTTP/1.1 Connection: close）—— 边收边计字节数
            buf = bytearray(rest)
            while len(buf) < MAX_BODY_SIZE:
                try:
                    chunk = sock.recv(4096)
                except Exception:
                    break
                if not chunk:
                    break
                buf.extend(chunk)
            body = bytes(buf[:MAX_BODY_SIZE])
        return status, headers, body
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _recv_exact(sock, n):
    """从 sock 精确读 n 字节。"""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"连接中断：期望 {n} 字节，已读 {len(buf)}")
        buf += chunk
    return buf


def _read_chunked(sock, initial=b""):
    """读 Transfer-Encoding: chunked 响应体。"""
    buf = initial
    body = b""
    while True:
        # 读 chunk size 行（以 \r\n 结尾）
        while b"\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("chunked 编码：连接中断于 size 行")
            buf += chunk
        size_line, _, buf = buf.partition(b"\r\n")
        try:
            size = int(size_line.split(b";", 1)[0].strip(), 16)
        except ValueError:
            raise ValueError(f"非法 chunk size: {size_line!r}")
        if size == 0:
            return body
        # 读 size 字节 + 后续 \r\n
        while len(buf) < size + 2:
            chunk = sock.recv(size + 2 - len(buf))
            if not chunk:
                raise ConnectionError("chunked 编码：连接中断于 data")
            buf += chunk
        body += buf[:size]
        buf = buf[size + 2:]


_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def _fetch_follow_redirects(url, timeout=15, max_hops=5):
    """跟随重定向（最多 max_hops 跳），**每一跳都重新走 _ip_safe_fetch 内的
    SSRF 校验** —— 防止外网页面 302 跳内网的绕过。

    返回 (final_url, status, headers, raw_bytes)。
    """
    for hop in range(max_hops + 1):
        status, headers, body = _ip_safe_fetch(url, timeout)
        if status not in _REDIRECT_STATUSES:
            return url, status, headers, body
        if hop >= max_hops:
            break
        loc = headers.get("location", "")
        if not loc:
            break
        url = urllib.parse.urljoin(url, loc)
    raise ConnectionError(f"重定向未完成（超 {max_hops} 跳或缺少 Location）")


def _decode_body(raw, declared_charset):
    """按 优先声明字符集 → meta 嗅探 → utf-8 → gbk/gb18030 解码。

    国内大量站点是 GBK 且响应头不带 charset，之前直接 utf-8+replace 会
    整页乱码。strict 解码失败才降级到下一候选，最后 utf-8+replace 兜底。
    """
    import re
    candidates = []
    if declared_charset:
        candidates.append(declared_charset)
    head = raw[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?([\w\-]+)', head, re.IGNORECASE)
    if m:
        candidates.append(m.group(1))
    candidates += ["utf-8", "gbk", "gb18030"]
    seen = set()
    for enc in candidates:
        enc = enc.strip().lower()
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def execute(name, arguments):
    if name != "fetch_webpage":
        return f"未知工具: {name}"

    url = arguments.get("url", "").strip()
    if not url:
        return "错误：未提供URL"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        # SSRF 防护：每跳内部 _validate_and_resolve；自动跟随重定向
        final_url, status, headers, raw = _fetch_follow_redirects(url)
    except ValueError as e:
        return f"⛔ 安全拦截: {e}"
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        return (f"🌐 网络错误: {e}\n"
                f"提示：当前可能处于内网/离线环境，外网页面无法访问。"
                f"如需读取资料，可先用知识库插件 kb_build 索引本地文档，再 kb_search 检索。")
    except Exception as e:
        return f"网页抓取出错: {e}"

    if not (200 <= status < 300):
        return f"HTTP错误 {status}: 无法访问该页面"

    content_type = headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return f"不支持的内容类型: {content_type}"

    # 解码：声明 charset → meta 嗅探 → utf-8 → gbk/gb18030
    charset = None
    for part in content_type.split(";"):
        if "charset" in part:
            charset = part.split("=")[-1].strip()
    text = _decode_body(raw, charset)

    extractor = _TextExtractor()
    extractor.feed(text)
    body = "".join(extractor.text)
    body = _clean_text(body)

    if len(body) > 8000:
        body = body[:8000] + "\n...[内容已截断，网页原文较长]"

    return f"[{final_url}]\n\n{body}"
