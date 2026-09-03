"""PowerPoint 演示文稿插件 — 创建/读取 .pptx 文件（对应 WorkBuddy 的 pptx 文件处理能力）"""
import os

PLUGIN_INFO = {
    "name": "pptx",
    "description": "创建 PowerPoint 演示文稿(.pptx)或读取已有文稿内容。支持封面、多张幻灯片，每页可含标题、正文与要点列表。",
    "version": "1.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_pptx",
            "description": "创建一个 PowerPoint 演示文稿(.pptx)。提供标题（用作封面）和若干幻灯片，每页可含标题、正文与要点列表。文件保存在程序同级目录下。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名，如 presentation.pptx"
                    },
                    "title": {
                        "type": "string",
                        "description": "演示文稿标题（显示在封面页）"
                    },
                    "slides": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {
                                    "type": "string",
                                    "description": "幻灯片标题"
                                },
                                "content": {
                                    "type": "string",
                                    "description": "幻灯片正文段落内容"
                                },
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "要点列表（可选），每项为一行要点"
                                }
                            },
                            "required": ["heading"]
                        },
                        "description": "幻灯片列表，每页包含 heading(标题)、content(正文)、bullets(可选要点列表)"
                    }
                },
                "required": ["filename", "title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_pptx",
            "description": "读取一个 PowerPoint 演示文稿的内容，返回每页的标题与文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "PPTX 文件路径"
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
    if name == "create_pptx":
        return _create(arguments)
    if name == "read_pptx":
        return _read(arguments)
    return f"未知工具: {name}"


def _create(args):
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError:
        return "错误: 请先安装 python-pptx (pip install python-pptx)"

    filename = args.get("filename", "presentation.pptx")
    title = args.get("title", "未命名演示文稿")
    slides = args.get("slides", [])

    prs = Presentation()

    # 封面页（标题 + 副标题布局）
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = title
    if len(cover.placeholders) > 1:
        cover.placeholders[1].text = "由 AI 助手生成"

    if not slides:
        prs.slides.add_slide(prs.slide_layouts[1])
    else:
        for s in slides:
            heading = s.get("heading", "")
            content = s.get("content", "")
            bullets = s.get("bullets", []) or []
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = heading
            body = slide.placeholders[1]
            tf = body.text_frame
            tf.word_wrap = True
            first = True
            if content:
                p = tf.paragraphs[0]
                p.text = content
                first = False
            for b in bullets:
                p = tf.paragraphs[0] if first else tf.add_paragraph()
                p.text = b
                first = False

    path = _safe_path(filename)
    prs.save(path)
    return (
        f"PowerPoint 演示文稿已创建: {filename}\n"
        f"路径: {os.path.abspath(path)}\n"
        f"幻灯片数: {len(prs.slides)}"
    )


def _read(args):
    try:
        from pptx import Presentation
    except ImportError:
        return "错误: 请先安装 python-pptx (pip install python-pptx)"

    filepath = args.get("filepath", "")
    if not filepath:
        return "错误: 未提供文件路径"

    path = _resolve_read_path(filepath)
    if not os.path.exists(path):
        return f"错误: 文件不存在 - {path}"

    try:
        prs = Presentation(path)
    except Exception as e:
        return f"无法打开文件: {e}"

    lines = []
    for i, slide in enumerate(prs.slides, 1):
        lines.append(f"--- 第 {i} 页 ---")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if t:
                        lines.append(t)

    content = "\n".join(lines)
    if len(content) > 6000:
        content = content[:6000] + "\n...[内容已截断]"

    return f"[{filepath}]\n页数: {len(prs.slides)}\n\n{content}"
