"""文件读写插件 — 创建/读取/追加任意文本文件"""
import os

PLUGIN_INFO = {
    "name": "write_file",
    "description": "创建或读取任意文本文件（.txt/.py/.json/.html/.css/.md/.ini 等）。支持写入、追加、读取操作。文件保存在程序同级目录下。",
    "version": "1.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建一个文本文件或覆盖已有文件。支持 .txt、.py、.json、.html、.css、.md 等任意文本格式。文件保存在程序同级目录下，提供文件名即可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名（含扩展名），如 script.py、config.json、index.html、README.md 等。不要包含路径，只写文件名。"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的完整文本内容"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8。可指定 gbk 等。",
                        "default": "utf-8"
                    }
                },
                "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "向已有文本文件末尾追加内容。如果文件不存在则创建新文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名，如 log.txt"
                    },
                    "content": {
                        "type": "string",
                        "description": "要追加的文本内容"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8",
                        "default": "utf-8"
                    }
                },
                "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容。支持 .txt、.py、.json、.html、.md 等任意文本格式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "文件路径（可以是相对路径或绝对路径）"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8",
                        "default": "utf-8"
                    }
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出程序同级目录下的文件列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "可选的文件名过滤模式，如 *.py、*.txt。不填则列出所有文件。"
                    }
                },
                "required": []
            }
        }
    },
]


def _safe_path(filepath):
    """解析为安全路径：绝对路径原样返回；相对路径解析到工作区根目录
    （优先配置里的 workspace_root，否则回退项目根目录）"""
    if os.path.isabs(filepath):
        return filepath
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        from qwen_app.config import load_config
        cfg = load_config()
        ws = cfg.get("workspace_root")
        if ws and os.path.isdir(ws):
            root = ws
        else:
            root = os.path.abspath(os.path.join(root, ".."))
    except Exception:
        root = os.path.abspath(os.path.join(root, ".."))
    return os.path.join(root, filepath)


def execute(name, arguments):
    if name == "write_file":
        return _write(arguments)
    if name == "append_file":
        return _append(arguments)
    if name == "read_file":
        return _read(arguments)
    if name == "list_files":
        return _list(arguments)
    return f"未知工具: {name}"


def _write(args):
    filename = args.get("filename", "")
    content = args.get("content", "")
    encoding = args.get("encoding", "utf-8")

    if not filename:
        return "错误: 未提供文件名"

    path = _safe_path(filename)

    # 安全检查：阻止覆盖程序核心文件
    unsafe_names = {"main.py", "chat_window.py", "worker.py", "tools.py",
                    "config.py", "sanitizer.py", "plugin_manager.py",
                    "model_config.json"}
    if filename.lower() in unsafe_names:
        return f"错误: 不能覆盖核心文件 '{filename}'，请使用其他文件名。"

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)
    except Exception as e:
        return f"写入文件失败: {e}"

    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    return (
        f"文件已写入: {filename}\n"
        f"路径: {os.path.abspath(path)}\n"
        f"大小: {size} 字节\n"
        f"行数: {content.count(chr(10)) + 1}"
    )


def _append(args):
    filename = args.get("filename", "")
    content = args.get("content", "")
    encoding = args.get("encoding", "utf-8")

    if not filename:
        return "错误: 未提供文件名"

    path = _safe_path(filename)

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding=encoding) as f:
            f.write(content)
    except Exception as e:
        return f"追加文件失败: {e}"

    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    return (
        f"内容已追加到: {filename}\n"
        f"路径: {os.path.abspath(path)}\n"
        f"总大小: {size} 字节"
    )


def _read(args):
    filepath = args.get("filepath", "")
    encoding = args.get("encoding", "utf-8")

    if not filepath:
        return "错误: 未提供文件路径"

    path = _safe_path(filepath)
    if not os.path.exists(path):
        return f"错误: 文件不存在 - {path}"

    try:
        with open(path, "r", encoding=encoding) as f:
            content = f.read()
    except UnicodeDecodeError:
        # 编码回退
        try:
            with open(path, "r", encoding="gbk") as f:
                content = f.read()
        except Exception as e:
            return f"读取文件失败（编码错误）: {e}"
    except Exception as e:
        return f"读取文件失败: {e}"

    # 过长截断
    if len(content) > 8000:
        content = content[:8000] + "\n\n... [内容已截断，完整长度: {} 字符]".format(len(content))

    return f"[{filepath}]\n{content}"


def _list(args):
    pattern = args.get("pattern", "")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.abspath(os.path.join(base_dir, ".."))

    try:
        files = os.listdir(parent)
    except Exception as e:
        return f"列出文件失败: {e}"

    # 过滤
    if pattern:
        import fnmatch
        files = [f for f in files if fnmatch.fnmatch(f, pattern)]

    # 排序：文件夹在前
    files_dirs = [f for f in files if os.path.isdir(os.path.join(parent, f))]
    files_only = [f for f in files if not os.path.isdir(os.path.join(parent, f))]
    result = []
    if files_dirs:
        result.append("📁 文件夹:")
        result.extend(f"  {d}/" for d in sorted(files_dirs))
    if files_only:
        result.append("📄 文件:")
        for f in sorted(files_only):
            try:
                size = os.path.getsize(os.path.join(parent, f))
                result.append(f"  {f}  ({size:,} B)")
            except OSError:
                result.append(f"  {f}")

    if not result:
        return f"目录下没有匹配 '{pattern}' 的文件"
    return "\n".join(result)
