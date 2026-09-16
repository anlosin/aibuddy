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
- run_python 执行的是**任意代码**，黑名单只能拦住明显的破坏性写法、
  不构成沙箱；请勿在代码里执行不可逆操作（删除 / 格式化 / 改权限）
"""

# ── 破坏性命令黑名单：与 ssh_runner / code_runner 共用同一份 ──
# Day 20.6.12：原先本文件内联维护这份黑名单，ssh_runner 又另抄了一份并注释
# 「与 shell_runner 保持一致」（实际缺 Day 18 的全部加固）。现已抽到
# plugins/_cmd_blocklist.py 作为单一事实来源，两边 import 同一对象，
# 从结构上消除再次漂移的可能。漏点清单、设计立场与误伤红线见该模块。
from plugins._cmd_blocklist import (      # noqa: E402
    BLOCKED_PATTERNS,
    refuse_if_blocked as _refuse_if_blocked,
)

OUTPUT_LIMIT = 6000  # 输出截断上限（字符）


def _workspace_root():
    """读取当前 active workspace（来自对话/任务），缺省回退到全局默认目录。

    active workspace 由 chat_window / scheduler 在执行前调用
    qwen_app.workspace.set_active_workspace() 设置。
    """
    try:
        from qwen_app.workspace import resolve_workspace
        return resolve_workspace()
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
    # Day 20.6.12（P0-SEC-2）：此前只有 run_command 走黑名单，run_python 零检查，
    # 而 Python 代码同样能 os.system("rm -rf /")。现对代码文本一并筛查。
    # 注意这是「尽力而为」的护栏而非沙箱（立场见 _cmd_blocklist）。
    refuse = _refuse_if_blocked(code)
    if refuse:
        return refuse
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
