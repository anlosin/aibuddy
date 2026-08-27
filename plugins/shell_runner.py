"""命令/脚本执行插件 — 在宿主机运行 shell / Python，实现真正的"干活"能力

安全设计：
- 破坏性命令黑名单（rm -rf /、format、shutdown、fork bomb 等）命中直接拒绝
- 命令默认在 workspace 根目录下执行，避免误操作系统关键目录
- 所有执行带超时保护，输出截断，防止 GUI 卡死或被刷屏
- 复杂任务建议先写脚本文件再 run_script，便于审查与复用
"""
import os
import sys
import re
import tempfile
import subprocess

PLUGIN_INFO = {
    "name": "shell_runner",
    "description": "在本地机器执行 shell 命令、运行 Python 代码片段或脚本文件。用于自动化任务、数据处理、系统查询等真实“干活”场景。",
    "version": "1.0",
}

SYSTEM_PROMPT = """你拥有在本地机器执行命令和脚本的能力（shell_runner 插件），可以真正“干活”而不仅仅是聊天。

适用场景：
- 运行 shell 命令完成文件处理、系统查询、服务操作
- 执行 Python 片段做数据处理、计算、自动化
- 运行已存在的脚本文件（.py / .bat / .cmd / .sh / .js）

安全准则：
- 优先使用安全、可逆的操作
- 不要执行破坏性命令（如 rm -rf /、格式化磁盘、关机重启等），插件会直接拒绝
- 涉及重要数据或批量操作前，先用 ls / 预览确认影响范围
- 复杂任务先写脚本文件再用 run_script 运行，便于复用与人工审查
"""

# ── 破坏性命令黑名单：命中直接拒绝执行 ──
# 说明：run_command 保留 shell=True 以支持管道/重定向/内建命令（这是本插件
# "干活"能力的核心），因此安全边界压缩到黑名单广度。黑名单覆盖越权重命名、
# 参数变体(-rf/-fr/-r -f)、家目录/敏感绝对路径删除、Windows PowerShell/CMD
# 递归删除、远程下载执行器等，尽力收缩绕过空间。仍无法穷尽的场景，建议
# 复杂/危险任务走 run_script 先落盘人工审查再执行。
BLOCKED_PATTERNS = [
    # ── rm 递归删除：覆盖 -rf/-fr/-r -f/--recursive 参数变体 + 根/家目录/敏感绝对路径 ──
    r"\brm\b[^\n|&;]*?(?:-\w*[rR]+\w*|--recursive)[^\n|&;]*?[\s\'\"\"]+(/|~|\$\{?HOME\}?)",
    # ── Windows 递归/强制删除 ──
    r"\bRemove-Item\b[^|\n]*-(Recurse|Force|r)\b.*-Force\b",   # PowerShell 递归强制
    r"\brd\s+/[sq]\b",                                        # rd /s /q
    r"\bdel\s+/[sqf]\b",                                      # del /s /q /f
    # ── 危险单条命令 / 格式化 / 写设备 ──
    r"\bformat\s+[a-z]:",         # format C:
    r"\bmkfs",                    # mkfs*
    r"\bdd\s+if=.*of=/dev/",      # dd 写设备
    r">\s*/dev/sd",               # 覆盖磁盘设备
    r"\btruncate\s+-s\s+0\s+/",   # truncate 设备
    # ── fork bomb / 关机 / 分区 / 权限破坏 ──
    r":\(\).*\{\s*:\|:",          # fork bomb
    r"\bshutdown\b",              # shutdown
    r"\breboot\b",                # reboot
    r"\bhalt\b",                  # halt
    r"\bpoweroff\b",              # poweroff
    r"\bfdisk\b",                 # fdisk
    r"\bparted\b",                # parted
    r"\bchmod\s+-R\s+0",          # chmod -R 000
    r"\bDISM\b",                  # DISM
    r"\bmkfs\.",                  # mkfs.ext4 等
    # ── 远程下载并执行（curl|wget ... | sh/bash / xargs sh）──
    r"\b(curl|wget)\b[^|\n]*\|\s*(sh|bash|zsh|cmd|powershell)\b",
    r"\bxargs\b[^|\n]*\s(sh|bash|zsh)\b",
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

OUTPUT_LIMIT = 6000  # 输出截断上限（字符）


def _workspace_root():
    """读取配置的 workspace 根目录，缺省为项目目录"""
    try:
        from qwen_app.config import load_config
        cfg = load_config()
        root = cfg.get("workspace_root")
        if root and os.path.isdir(root):
            return os.path.abspath(root)
    except Exception:
        pass
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _safe_script_path(filename):
    """解析脚本路径并强制约束在 workspace 根目录内，防止路径穿越。

    相对路径与绝对路径一视同仁：用 realpath 规范化（解析 ../、符号链接、
    冗余分隔符），最终路径必须位于 workspace 根目录之下，否则抛 ValueError。
    """
    root = _workspace_root()
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(
        filename if os.path.isabs(filename) else os.path.join(root, filename)
    )
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        raise ValueError(f"脚本路径越界，禁止执行工作区之外的文件: {filename}")
    return candidate


def _refuse_if_blocked(command):
    for r in _BLOCKED_RE:
        if r.search(command or ""):
            return ("⛔ 出于安全考虑，该命令被拒绝执行（命中破坏性操作黑名单：%s）。"
                    "如需执行，请改用更安全的等价写法，或联系管理员调整策略。"
                    % r.pattern)
    return None


def _run(cmd_args, cwd, timeout, shell=False):
    """统一子进程执行入口"""
    try:
        proc = subprocess.run(
            cmd_args,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
            shell=shell,
            encoding="utf-8",
            errors="replace",
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return "⏱ 执行超时（超过 %d 秒），已终止。" % timeout
    except FileNotFoundError as e:
        return "❌ 找不到可执行文件: %s" % e
    except Exception as e:
        return "❌ 执行失败: %s" % e

    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT] + "\n\n... [输出已截断，完整长度 %d 字符]" % len(out)
    return "退出码: %d\n%s" % (code, out)


def _do_command(args):
    command = args.get("command", "")
    if not command:
        return "错误: 未提供 command"
    refuse = _refuse_if_blocked(command)
    if refuse:
        return refuse
    timeout = int(args.get("timeout", 30))
    cwd = args.get("cwd") or _workspace_root()
    if not os.path.isdir(cwd):
        cwd = _workspace_root()
    # Windows 下用 shell=True 运行整条命令
    return _run(command, cwd, timeout, shell=True)


def _do_python(args):
    code = args.get("code", "")
    if not code:
        return "错误: 未提供 code"
    timeout = int(args.get("timeout", 30))
    root = _workspace_root()
    # 写到 workspace 临时文件再执行，避免 -c 的引号转义问题
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".py", dir=root, prefix="_run_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        return _run([sys.executable, tmp], root, timeout, shell=False)
    except Exception as e:
        return "❌ 执行失败: %s" % e
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _interpreter_for(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".py":
        return [sys.executable]
    if ext in (".bat", ".cmd"):
        return ["cmd", "/c"]
    if ext == ".sh":
        return ["bash"]
    if ext == ".js":
        return ["node"]
    if ext in (".ps1",):
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File"]
    return None


def _do_script(args):
    filename = args.get("filename", "")
    if not filename:
        return "错误: 未提供 filename"
    root = _workspace_root()
    try:
        path = _safe_script_path(filename)
    except ValueError as e:
        return f"错误: {e}"
    if not os.path.exists(path):
        return "错误: 脚本不存在 - %s" % path
    interp = _interpreter_for(filename)
    if interp is None:
        return "错误: 不支持的脚本类型（支持 .py/.bat/.cmd/.sh/.js/.ps1）"
    timeout = int(args.get("timeout", 60))
    cmd = interp + [path]
    return _run(cmd, root, timeout, shell=False)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在本地机器执行一条 shell 命令，返回退出码与输出（stdout+stderr）。用于文件处理、系统查询、服务操作等。破坏性命令会被拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令，如 'dir'、'python --version'、'ls -la'。注意避免破坏性操作。"
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录（可选），默认在 workspace 根目录。建议用相对路径或留空。"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30，最大建议 300。",
                        "default": 30
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "在子进程中执行一段 Python 代码并返回输出。用于数据处理、计算、自动化。代码在独立进程运行，不会影响主程序。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的完整 Python 代码"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30。",
                        "default": 30
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": "运行 workspace 中已存在的脚本文件（.py/.bat/.cmd/.sh/.js/.ps1）。适合运行已写好的自动化脚本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "脚本文件名（workspace 下），如 analyze.py、deploy.bat"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 60。",
                        "default": 60
                    }
                },
                "required": ["filename"]
            }
        }
    },
]


def execute(name, arguments):
    if name == "run_command":
        return _do_command(arguments)
    if name == "run_python":
        return _do_python(arguments)
    if name == "run_script":
        return _do_script(arguments)
    return f"未知工具: {name}"
