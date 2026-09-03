"""PDF 阅读插件 — 提取 PDF 文件中的文本内容"""
import os

PLUGIN_INFO = {
    "name": "pdf",
    "description": "读取 PDF 文件内容，提取纯文本。支持中文PDF。",
    "version": "1.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": "读取PDF文件的文本内容，支持指定页码范围。适用于阅读报告、论文、合同等PDF文档。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "PDF文件路径，如 report.pdf 或绝对路径"
                    },
                    "start_page": {
                        "type": "integer",
                        "description": "起始页码（从1开始，可选，默认第1页）"
                    },
                    "end_page": {
                        "type": "integer",
                        "description": "结束页码（可选，默认最后一页）"
                    }
                },
                "required": ["filepath"]
            }
        }
    },
]


def _safe_path(filepath):
    """解析为安全路径：相对路径归到当前对话工作目录，绝对路径保持不变。

    用 realpath 规范化（解析 ../、符号链接）并强制约束在对话工作目录内，
    防止路径穿越。与 write_file 插件行为保持一致。
    """
    if os.path.isabs(filepath):
        return filepath
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    try:
        from qwen_app.workspace import resolve_workspace
        root = resolve_workspace()
    except Exception:
        pass
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root, filepath))
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        raise ValueError(f"路径越界，禁止访问对话工作目录之外的位置: {filepath}")
    return candidate


def _resolve_read_path(filepath):
    """读取用路径解析：相对路径先在当前对话工作目录查找，找不到回退项目根。"""
    if os.path.isabs(filepath):
        return filepath
    ws = None
    try:
        from qwen_app.workspace import get_active_workspace
        ws = get_active_workspace()
    except Exception:
        ws = None
    proj_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    candidates = []
    if ws:
        candidates.append(os.path.join(ws, filepath))
    candidates.append(os.path.join(proj_root, filepath))
    for c in candidates:
        if os.path.exists(c):
            return c
    return os.path.join(ws if ws else proj_root, filepath)


def execute(name, arguments):
    if name != "read_pdf":
        return f"未知工具: {name}"

    try:
        import pdfplumber
    except ImportError:
        return "错误: 请先安装 pdfplumber (pip install pdfplumber)"

    filepath = arguments.get("filepath", "")
    if not filepath:
        return "错误: 未提供文件路径"

    path = _resolve_read_path(filepath)
    if not os.path.exists(path):
        return f"错误: 文件不存在 - {path}"

    start_page = arguments.get("start_page", 1)
    end_page = arguments.get("end_page")

    try:
        pdf = pdfplumber.open(path)
    except Exception as e:
        return f"无法打开PDF: {e}"

    total_pages = len(pdf.pages)

    if end_page is None:
        end_page = total_pages
    start_page = max(1, start_page)
    end_page = min(end_page, total_pages)

    all_text = []
    for page_num in range(start_page - 1, end_page):
        try:
            page = pdf.pages[page_num]
            text = page.extract_text()
            if text:
                all_text.append(f"--- 第 {page_num + 1} 页 ---\n{text}")
            else:
                all_text.append(f"--- 第 {page_num + 1} 页 ---\n(该页无可提取文本，可能为扫描图片)")
        except Exception as e:
            all_text.append(f"--- 第 {page_num + 1} 页 ---\n提取失败: {e}")

    pdf.close()

    content = "\n\n".join(all_text)
    if len(content) > 8000:
        content = content[:8000] + "\n\n...[内容已截断，PDF总页数: " + str(total_pages) + "]"

    return (
        f"[{os.path.basename(filepath)}]\n"
        f"总页数: {total_pages}, 当前提取: 第 {start_page}-{end_page} 页\n"
        f"\n{content}"
    )
