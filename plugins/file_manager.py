"""文件/工作区管理插件 — 浏览、搜索、内容检索、复制/移动/删除

与 write_file 互补：write_file 负责读写，本插件负责目录浏览、文件名/内容检索、
文件搬运与整理。路径规则与 write_file 一致：相对路径解析到项目根目录，
绝对路径按原样处理（用户自有机器）。删除操作有核心文件保护。
"""
import os
import re
import fnmatch
import shutil

PLUGIN_INFO = {
    "name": "file_manager",
    "description": "浏览工作区目录、按名称/内容搜索文件、复制/移动/删除文件、创建目录。用于本地文件整理与检索。",
    "version": "1.0",
}

SYSTEM_PROMPT = """你拥有文件/工作区管理能力（file_manager 插件）：

- list_directory：浏览目录（支持递归与层数限制）
- search_files：按文件名模式（glob）搜索
- search_content：在文件内容中检索关键字（返回 文件名:行号:内容）
- copy_file / move_file / delete_file / make_dir：文件搬运与整理

使用前先用 list_directory / search_files 确认目标，再执行改动类操作。
删除操作受保护，核心文件与虚拟环境目录无法删除。
"""

# 删除保护：禁止删除这些（及项目核心文件、.venv）
PROTECTED_NAMES = {
    "main.py", "chat_window.py", "worker.py", "tools.py", "config.py",
    "sanitizer.py", "plugin_manager.py", "model_config.json",
    "compressor.py", "start.bat",
}
PROTECTED_DIRS = {".venv", ".git", "node_modules", "__pycache__"}


def _root():
    """工作区根目录：优先用配置里的 workspace_root，否则回退项目根目录"""
    try:
        from config import load_config
        cfg = load_config()
        root = cfg.get("workspace_root")
        if root and os.path.isdir(root):
            return os.path.abspath(root)
    except Exception:
        pass
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _resolve(path):
    if os.path.isabs(path):
        return path
    return os.path.join(_root(), path)


def _is_protected(path):
    name = os.path.basename(path.rstrip("/\\"))
    if name in PROTECTED_NAMES:
        return True
    parts = os.path.normpath(path).split(os.sep)
    if any(p in PROTECTED_DIRS for p in parts):
        return True
    return False


def _human(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _do_list(args):
    target = args.get("path", ".")
    recursive = bool(args.get("recursive", False))
    max_depth = int(args.get("max_depth", 2))
    base = _resolve(target)
    if not os.path.isdir(base):
        return f"错误: 目录不存在 - {base}"

    lines = []
    if recursive:
        for dp, dirs, files in os.walk(base):
            depth = dp[len(base):].count(os.sep)
            if depth > max_depth:
                dirs[:] = []
                continue
            indent = "  " * depth
            lines.append(f"{indent}📁 {os.path.basename(dp) or base}/")
            for f in sorted(files)[:200]:
                fp = os.path.join(dp, f)
                try:
                    sz = _human(os.path.getsize(fp))
                except OSError:
                    sz = "?"
                lines.append(f"{indent}  📄 {f}  ({sz})")
            dirs[:] = [d for d in sorted(dirs) if d not in PROTECTED_DIRS][:200]
    else:
        try:
            entries = sorted(os.listdir(base))
        except Exception as e:
            return f"列出失败: {e}"
        for e in entries:
            ep = os.path.join(base, e)
            if os.path.isdir(ep):
                lines.append(f"📁 {e}/")
            else:
                try:
                    sz = _human(os.path.getsize(ep))
                except OSError:
                    sz = "?"
                lines.append(f"📄 {e}  ({sz})")
    if not lines:
        return f"（空目录）{base}"
    return f"[目录: {base}]\n" + "\n".join(lines)


def _do_search_files(args):
    pattern = args.get("pattern", "*")
    target = args.get("path", ".")
    base = _resolve(target)
    if not os.path.isdir(base):
        return f"错误: 目录不存在 - {base}"
    found = []
    for dp, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in PROTECTED_DIRS]
        for f in files:
            if fnmatch.fnmatch(f, pattern):
                found.append(os.path.join(dp, f))
        if len(found) >= 500:
            break
    if not found:
        return f"未找到匹配 '{pattern}' 的文件"
    return f"匹配 {len(found)} 个文件:\n" + "\n".join(f"  {p}" for p in found[:500])


def _do_search_content(args):
    query = args.get("query", "")
    if not query:
        return "错误: 未提供 query"
    target = args.get("path", ".")
    glob = args.get("glob", "*")
    max_results = int(args.get("max_results", 50))
    base = _resolve(target)
    if not os.path.isdir(base):
        return f"错误: 目录不存在 - {base}"
    try:
        rx = re.compile(query, re.IGNORECASE)
    except re.error as e:
        return f"正则错误: {e}"

    TEXT_EXT = {".txt", ".md", ".py", ".json", ".csv", ".html", ".css", ".js",
                ".ts", ".xml", ".ini", ".yaml", ".yml", ".log", ".java", ".go",
                ".c", ".cpp", ".h", ".sh", ".bat", ".ps1", ".sql", ".rst"}
    results = []
    for dp, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in PROTECTED_DIRS]
        for f in files:
            if not fnmatch.fnmatch(f, glob):
                continue
            if os.path.splitext(f)[1].lower() not in TEXT_EXT:
                continue
            fp = os.path.join(dp, f)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            results.append(f"{fp}:{i}: {line.rstrip()}")
                            if len(results) >= max_results:
                                break
            except Exception:
                continue
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break
    if not results:
        return f"未在文件内容中找到匹配 '{query}'"
    return f"内容检索命中 {len(results)} 处（已截断到 {max_results}）:\n" + "\n".join(results)


def _do_copy(args):
    src = _resolve(args.get("src", ""))
    dst = _resolve(args.get("dst", ""))
    if not src or not dst:
        return "错误: 需提供 src 与 dst"
    if not os.path.exists(src):
        return f"错误: 源不存在 - {src}"
    if _is_protected(dst):
        return f"错误: 目标受保护，禁止写入 - {os.path.basename(dst)}"
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.copy2(src, dst)
    except Exception as e:
        return f"复制失败: {e}"
    return f"已复制: {src}\n→ {dst}"


def _do_move(args):
    src = _resolve(args.get("src", ""))
    dst = _resolve(args.get("dst", ""))
    if not src or not dst:
        return "错误: 需提供 src 与 dst"
    if not os.path.exists(src):
        return f"错误: 源不存在 - {src}"
    if _is_protected(src):
        return f"错误: 源文件受保护，禁止移动 - {os.path.basename(src)}"
    if _is_protected(dst):
        return f"错误: 目标受保护，禁止写入 - {os.path.basename(dst)}"
    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(src, dst)
    except Exception as e:
        return f"移动失败: {e}"
    return f"已移动: {src}\n→ {dst}"


def _do_delete(args):
    target = _resolve(args.get("path", ""))
    if not target:
        return "错误: 未提供 path"
    if not os.path.exists(target):
        return f"错误: 目标不存在 - {target}"
    if _is_protected(target):
        return f"错误: 该文件/目录受保护，禁止删除 - {os.path.basename(target)}"
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    except Exception as e:
        return f"删除失败: {e}"
    return f"已删除: {target}"


def _do_make_dir(args):
    target = _resolve(args.get("path", ""))
    if not target:
        return "错误: 未提供 path"
    if _is_protected(target):
        return f"错误: 该名称受保护 - {os.path.basename(target)}"
    try:
        os.makedirs(target, exist_ok=True)
    except Exception as e:
        return f"创建目录失败: {e}"
    return f"已创建目录: {target}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "浏览目录内容。可递归并限制层数。返回子目录与文件（含大小）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认 '.'（工作区根）"},
                    "recursive": {"type": "boolean", "description": "是否递归，默认 false"},
                    "max_depth": {"type": "integer", "description": "递归最大层数，默认 2", "default": 2}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "按文件名模式（glob，如 *.py、report*.docx）递归搜索文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式，如 '*.py'"},
                    "path": {"type": "string", "description": "搜索起始目录，默认工作区根"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "在文件内容中检索关键字（支持正则），返回 文件名:行号:内容。适合在代码库/文档中定位信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索内容（支持正则），如 'def run_command'、'错误码\\d+'"},
                    "path": {"type": "string", "description": "搜索起始目录，默认工作区根"},
                    "glob": {"type": "string", "description": "文件类型过滤，默认 '*'", "default": "*"},
                    "max_results": {"type": "integer", "description": "最大返回条数，默认 50", "default": 50}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "复制文件或目录到目标路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "源路径"},
                    "dst": {"type": "string", "description": "目标路径"}
                },
                "required": ["src", "dst"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "移动（重命名）文件或目录。受保护文件不可移动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "源路径"},
                    "dst": {"type": "string", "description": "目标路径"}
                },
                "required": ["src", "dst"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除文件或目录。核心文件与 .venv 等受保护，禁止删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的路径"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "make_dir",
            "description": "创建目录（可多级）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径"}
                },
                "required": ["path"]
            }
        }
    },
]


def execute(name, arguments):
    handlers = {
        "list_directory": _do_list,
        "search_files": _do_search_files,
        "search_content": _do_search_content,
        "copy_file": _do_copy,
        "move_file": _do_move,
        "delete_file": _do_delete,
        "make_dir": _do_make_dir,
    }
    fn = handlers.get(name)
    if fn:
        return fn(arguments)
    return f"未知工具: {name}"
