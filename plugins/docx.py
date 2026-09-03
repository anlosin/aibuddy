"""Word 文档插件 — 创建/读取 .docx 文件"""
import os

PLUGIN_INFO = {
    "name": "docx",
    "description": "创建 Word 文档(.docx)或读取已有文档内容。支持标题、段落、表格。",
    "version": "1.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_docx",
            "description": "创建一个 Word 文档(.docx)。提供标题和段落内容，可包含多个章节。文件保存在程序同级目录下。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名，如 report.docx"
                    },
                    "title": {
                        "type": "string",
                        "description": "文档标题"
                    },
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string", "description": "章节标题"},
                                "content": {"type": "string", "description": "章节正文内容"}
                            },
                            "required": ["heading", "content"]
                        },
                        "description": "文档章节列表，每项包含 heading(标题) 和 content(内容)"
                    }
                },
                "required": ["filename", "title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_docx",
            "description": "读取一个 Word 文档的内容，返回段落文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Word 文件路径"
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
    """读取用路径解析：相对路径先在当前对话工作目录查找，找不到回退项目根。

    与 _safe_path（写，严格约束对话目录）不同，读取允许回退到项目根，
    以便读取历史对话或旧版本在项目根生成的产物。绝对路径保持不变。
    """
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
    # 都不存在：返回对话目录（无则项目根）下的路径，供调用方报"文件不存在"
    return os.path.join(ws if ws else proj_root, filepath)


def execute(name, arguments):
    if name == "create_docx":
        return _create(arguments)
    if name == "read_docx":
        return _read(arguments)
    return f"未知工具: {name}"


def _create(args):
    try:
        from docx import Document
        from docx.shared import Pt, Inches, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        return "错误: 请先安装 python-docx (pip install python-docx)"

    filename = args.get("filename", "document.docx")
    title = args.get("title", "未命名文档")
    sections = args.get("sections", [])

    doc = Document()

    # 文档标题
    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    if sections:
        for sec in sections:
            heading = sec.get("heading", "")
            content = sec.get("content", "")
            if heading:
                doc.add_heading(heading, level=1)
            if content:
                p = doc.add_paragraph(content)
                p.style.font.size = Pt(11)
    else:
        doc.add_paragraph("（无内容）")

    path = _safe_path(filename)
    doc.save(path)
    return (
        f"Word 文档已创建: {filename}\n"
        f"路径: {os.path.abspath(path)}\n"
        f"章节数: {len(sections)}"
    )


def _read(args):
    try:
        from docx import Document
    except ImportError:
        return "错误: 请先安装 python-docx (pip install python-docx)"

    filepath = args.get("filepath", "")
    if not filepath:
        return "错误: 未提供文件路径"

    path = _resolve_read_path(filepath)
    if not os.path.exists(path):
        return f"错误: 文件不存在 - {path}"

    try:
        doc = Document(path)
    except Exception as e:
        return f"无法打开文件: {e}"

    paragraphs = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if text:
            if p.style.name.startswith("Heading"):
                paragraphs.append(f"\n## {text}")
            else:
                paragraphs.append(text)

    content = "\n".join(paragraphs)
    if len(content) > 6000:
        content = content[:6000] + "\n...[内容已截断]"

    return f"[{filepath}]\n段落数: {len(doc.paragraphs)}\n\n{content}"
