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
    if os.path.isabs(filepath):
        return filepath
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", filepath)


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

    path = _safe_path(filepath)
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
