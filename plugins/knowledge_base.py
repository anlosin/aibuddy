"""内网知识库插件 — 离线 BM25 检索，替代联网搜索

适用场景：内网无外网时，需要在本地文档/代码库中检索知识。
特点：
- 完全离线，不依赖任何外部 API 或网络
- 自实现 BM25，零额外依赖（中文分词优先用 jieba，缺失时回退 CJK 二元分词）
- 支持常见文本格式；PDF/DOCX 用项目已有库懒加载（缺失则跳过并提示）
- 索引持久化到 knowledge_base/index.json，可重复构建

工具：kb_build 扫描建库、kb_search 检索、kb_status 查看状态。
"""
import os
import re
import json
import math

PLUGIN_INFO = {
    "name": "knowledge_base",
    "description": "离线知识库检索（BM25）。在内网无外网时，从本地文档/代码库中检索知识，替代联网搜索。",
    "version": "1.0",
}

SYSTEM_PROMPT = """你拥有离线知识库检索能力（knowledge_base 插件），这是内网无外网环境下替代联网搜索的关键能力。

当用户询问的内容可能涉及本地文档、代码库、内部资料时：
1. 优先用 kb_search 在本地知识库中检索相关片段
2. 若知识库为空或未覆盖，用 kb_build 先构建（指定文档所在文件夹）
3. 结合检索到的片段作答，并注明来源文件
4. 内网环境不要依赖 web_search/web_fetch（它们通常无法访问外网）

检索技巧：查询用关键词组合，而非完整问句；可分多次检索不同角度。
"""

INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "knowledge_base", "index.json")

TEXT_EXT = {".txt", ".md", ".py", ".json", ".csv", ".html", ".css", ".js", ".ts",
            ".xml", ".ini", ".yaml", ".yml", ".log", ".java", ".go", ".c", ".cpp",
            ".h", ".sh", ".bat", ".ps1", ".sql", ".rst"}
DOC_EXT = {".pdf", ".docx"}  # 需对应库支持

K1 = 1.5
B = 0.75
CHUNK_SIZE = 400
CHUNK_OVERLAP = 80

# ── 分词 ──
try:
    import jieba
    jieba.setLogLevel(20)
    _HAS_JIEBA = True
except Exception:
    _HAS_JIEBA = False

_LATIN = re.compile(r"[a-zA-Z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text):
    text = (text or "").lower()
    terms = []
    for m in _LATIN.finditer(text):
        terms.append(m.group(0))
    for m in _CJK.finditer(text):
        run = m.group(0)
        if _HAS_JIEBA:
            terms.extend(t for t in jieba.lcut(run) if t.strip())
        else:
            # CJK 二元分词回退
            if len(run) == 1:
                terms.append(run)
            else:
                terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def _read_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXT:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    if ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                return "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception:
            return ""
    if ext == ".docx":
        try:
            import docx
            d = docx.Document(path)
            return "\n".join(p.text for p in d.paragraphs)
        except Exception:
            return ""
    return ""


def _chunk_text(text, source, title):
    chunks = []
    # 先按空行/标题分段，再按长度切
    paras = re.split(r"\n\s*\n|(?=^#{1,6}\s)", text)
    buf = ""
    for para in paras:
        para = para.strip()
        if not para:
            continue
        buf = (buf + "\n" + para) if buf else para
        if len(buf) >= CHUNK_SIZE:
            chunks.append({"text": buf[:CHUNK_SIZE], "source": source, "title": title})
            buf = buf[CHUNK_SIZE - CHUNK_OVERLAP:]
    if buf.strip():
        chunks.append({"text": buf.strip(), "source": source, "title": title})
    return chunks


def _do_build(args):
    folder = args.get("folder", ".")
    ext_filter = args.get("extensions")  # 可选，如 ".md,.py,.txt"，逗号分隔
    if ext_filter:
        allowed = {e.strip().lower() if e.startswith(".") else "." + e.strip().lower()
                   for e in ext_filter.split(",") if e.strip()}
    else:
        allowed = None

    base = folder if os.path.isabs(folder) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", folder)
    if not os.path.isdir(base):
        return f"错误: 目录不存在 - {base}"

    docs = []          # 每篇文档的分词结果
    chunk_records = []  # 原始分块（用于检索返回）
    file_count = 0
    chunk_count = 0
    for dp, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".venv", ".git", "node_modules", "__pycache__", "knowledge_base")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if allowed and ext not in allowed:
                continue
            if ext not in TEXT_EXT and ext not in DOC_EXT:
                continue
            fp = os.path.join(dp, f)
            text = _read_text(fp)
            if not text.strip():
                continue
            title = (text.strip().splitlines()[0][:60] if text.strip() else f)
            rel = os.path.relpath(fp, base)
            for ch in _chunk_text(text, rel, title):
                toks = tokenize(ch["text"])
                if not toks:
                    continue
                docs.append(toks)
                chunk_records.append(ch)
                chunk_count += 1
            file_count += 1

    if not docs:
        return f"未在 {base} 中找到可索引的文本（已处理 {file_count} 个文件）"

    # 构建 BM25 统计
    N = len(docs)
    df = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    avgdl = sum(len(d) for d in docs) / N

    index = {
        "version": 1,
        "built_from": os.path.abspath(base),
        "file_count": file_count,
        "chunk_count": chunk_count,
        "avgdl": avgdl,
        "df": df,
        "chunks": chunk_records,
        "has_jieba": _HAS_JIEBA,
    }
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    return (f"✅ 知识库构建完成\n来源: {base}\n"
            f"文件: {file_count} 个\n分块: {chunk_count} 个\n"
            f"分词: {'jieba' if _HAS_JIEBA else 'CJK二元回退'}")


def _load_index():
    if not os.path.exists(INDEX_PATH):
        return None
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _bm25_score(query_terms, doc_terms, df, N, avgdl):
    if not doc_terms:
        return 0.0
    dl = len(doc_terms)
    tf = {}
    for t in doc_terms:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for qt in set(query_terms):
        if qt not in df:
            continue
        idf = math.log(1 + (N - df[qt] + 0.5) / (df[qt] + 0.5))
        f = tf.get(qt, 0)
        if f == 0:
            continue
        score += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * dl / avgdl))
    return score


def _do_search(args):
    query = args.get("query", "")
    if not query:
        return "错误: 未提供 query"
    top_k = int(args.get("top_k", 5))
    index = _load_index()
    if not index:
        return ("知识库尚未构建。请先用 kb_build 指定文档文件夹构建索引"
                "（例如 kb_build({'folder': 'docs'})）。")
    docs = [tokenize(c["text"]) for c in index["chunks"]]
    qterms = tokenize(query)
    if not qterms:
        return "错误: 查询无可分词的关键词"
    N = index["chunk_count"]
    avgdl = index["avgdl"]
    df = {k: int(v) for k, v in index["df"].items()}
    scored = []
    for i, d in enumerate(docs):
        s = _bm25_score(qterms, d, df, N, avgdl)
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    if not scored:
        return f"知识库中未找到与 '{query}' 相关的内容。"
    out = [f"知识库检索到 {len(scored)} 条相关片段（显示前 {min(top_k, len(scored))} 条）:\n"]
    for s, i in scored[:top_k]:
        ch = index["chunks"][i]
        snippet = ch["text"].replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:300] + "…"
        out.append(f"【相关度 {s:.2f}】📄 {ch['source']}\n  {snippet}\n")
    return "\n".join(out)


def _do_status(args):
    index = _load_index()
    if not index:
        return "知识库状态: 未构建（使用 kb_build 创建）"
    return (f"知识库状态: 已构建\n来源: {index.get('built_from')}\n"
            f"文件: {index.get('file_count')} 个\n分块: {index.get('chunk_count')} 个\n"
            f"分词器: {'jieba' if index.get('has_jieba') else 'CJK二元回退'}")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kb_build",
            "description": "扫描本地文件夹构建离线知识库索引（BM25）。支持 txt/md/py/json/csv/html/docx/pdf 等。索引持久化，可重复构建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "要索引的文件夹路径，如 'docs'、'D:/kb'"},
                    "extensions": {"type": "string", "description": "可选，限制文件类型，逗号分隔如 '.md,.py'，默认全部支持类型"}
                },
                "required": ["folder"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "在离线知识库中检索与查询相关的文档片段（含来源文件与相关性评分）。内网环境替代联网搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词（建议用关键词组合而非完整问句）"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 5", "default": 5}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kb_status",
            "description": "查看知识库构建状态（文件数、分块数、分词器）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
]


def execute(name, arguments):
    handlers = {
        "kb_build": _do_build,
        "kb_search": _do_search,
        "kb_status": _do_status,
    }
    fn = handlers.get(name)
    if fn:
        return fn(arguments)
    return f"未知工具: {name}"
