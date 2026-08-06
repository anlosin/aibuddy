"""代码执行插件 — 在本地运行 Python / Go / Java 代码片段

设计要点：
- 统一入口 run_code(language, code, timeout)，覆盖三种语言
- 所有执行在独立临时目录中进行，执行后清理，不污染 workspace 与项目目录
- 带超时保护与输出截断，防止 GUI 卡死或被刷屏
- 依赖本机已安装对应运行时（python / go / javac+java），缺失时返回友好提示
- Python 使用助手自带解释器（sys.executable，已安装项目依赖，如 openai/docx 等）
- Go 使用 `go run`（单文件即可，自动处理标准库 import）
- Java 使用 Java 11+ 单文件源码启动 `java File.java`（无需先 javac）
"""
import os
import sys
import re
import shutil
import tempfile
import subprocess

PLUGIN_INFO = {
    "name": "code_runner",
    "description": "在本地执行 Python / Go / Java 代码片段并返回输出，用于算法验证、小工具编写、语言特性测试等。",
    "version": "1.0",
}

SYSTEM_PROMPT = """你拥有在本地执行代码的能力（code_runner 插件），可以运行 Python、Go、Java 三种语言的代码片段并看到真实输出。

适用场景：
- 验证算法、数据结构、语言特性
- 写一次性小工具做计算或数据处理
- 用户明确要求“运行这段代码 / 执行 Python / Go / Java”

约定：
- 通过 run_code 工具执行，明确指定 language(python/go/java) 与 code
- Python 使用助手自带的解释器（已安装项目依赖）
- 若用户机器未安装 Go 或 Java 运行时，工具会给出安装提示，请友好告知用户先安装对应 JDK / Go 工具链
- 复杂或多文件项目，建议先写文件再用 shell_runner 的 run_script 运行
"""

OUTPUT_LIMIT = 8000

LANG_EXT = {"python": ".py", "go": ".go", "java": ".java"}


def _detect(interpreter):
    """检测运行时是否在 PATH 中，返回可执行路径或 None"""
    return shutil.which(interpreter)


def _class_name_from_code(code):
    """从 Java 代码推断主类名：优先 public class，其次第一个 class，否则 Main"""
    m = re.search(r"public\s+class\s+(\w+)", code)
    if m:
        return m.group(1)
    m = re.search(r"class\s+(\w+)", code)
    if m:
        return m.group(1)
    return "Main"


def _tmp_dir():
    return tempfile.mkdtemp(prefix="coderun_")


def _cap(out, code):
    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT] + "\n\n... [输出已截断，完整长度 %d 字符]" % len(out)
    return "退出码: %d\n%s" % (code, out)


def _do_python(code, timeout):
    d = _tmp_dir()
    try:
        path = os.path.join(d, "main.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            proc = subprocess.run(
                [sys.executable, path], cwd=d, timeout=timeout,
                capture_output=True, text=True, shell=False,
                encoding="utf-8", errors="replace",
            )
            return _cap((proc.stdout or "") + (proc.stderr or ""), proc.returncode)
        except subprocess.TimeoutExpired:
            return "⏱ 执行超时（超过 %d 秒），已终止。" % timeout
        except Exception as e:
            return "❌ 执行失败: %s" % e
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _do_go(code, timeout):
    go = _detect("go")
    if not go:
        return ("❌ 本机未检测到 Go 运行时（go 命令不可用）。\n"
                "请先安装 Go 工具链 (https://go.dev/dl) 并将其加入 PATH，然后重试。")
    d = _tmp_dir()
    try:
        path = os.path.join(d, "main.go")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        # 单文件 go run 即可；设置本地 GOCACHE 避免污染用户目录
        env = dict(os.environ)
        env["GOCACHE"] = os.path.join(d, ".cache")
        os.makedirs(env["GOCACHE"], exist_ok=True)
        try:
            proc = subprocess.run(
                [go, "run", path], cwd=d, timeout=timeout,
                capture_output=True, text=True, shell=False,
                encoding="utf-8", errors="replace", env=env,
            )
            return _cap((proc.stdout or "") + (proc.stderr or ""), proc.returncode)
        except subprocess.TimeoutExpired:
            return "⏱ 执行超时（超过 %d 秒），已终止。" % timeout
        except Exception as e:
            return "❌ 执行失败: %s" % e
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _do_java(code, timeout):
    java = _detect("java")
    if not java:
        return ("❌ 本机未检测到 Java 运行时（java 命令不可用）。\n"
                "请先安装 JDK 11+ (https://adoptium.net) 并将其加入 PATH，然后重试。")
    d = _tmp_dir()
    try:
        cls = _class_name_from_code(code)
        path = os.path.join(d, cls + ".java")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        # Java 11+ 单文件源码启动：java File.java（无需先 javac）
        try:
            proc = subprocess.run(
                [java, path], cwd=d, timeout=timeout,
                capture_output=True, text=True, shell=False,
                encoding="utf-8", errors="replace",
            )
            return _cap((proc.stdout or "") + (proc.stderr or ""), proc.returncode)
        except subprocess.TimeoutExpired:
            return "⏱ 执行超时（超过 %d 秒），已终止。" % timeout
        except Exception as e:
            return "❌ 执行失败: %s" % e
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _do_code(args):
    lang = (args.get("language") or "").lower()
    code = args.get("code", "")
    if not code:
        return "错误: 未提供 code"
    if lang not in LANG_EXT:
        return "错误: 不支持的语言 '%s'（支持 python / go / java）" % lang
    timeout = int(args.get("timeout", 30))
    if timeout <= 0 or timeout > 300:
        timeout = 30
    if lang == "python":
        return _do_python(code, timeout)
    if lang == "go":
        return _do_go(code, timeout)
    if lang == "java":
        return _do_java(code, timeout)
    return "错误: 未知语言"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": "在本地执行一段 Python / Go / Java 代码并返回 stdout/stderr 与退出码。用于算法验证、小工具编写、语言特性测试。运行在独立临时目录，执行后自动清理。",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": "代码语言，取值: python / go / java",
                        "enum": ["python", "go", "java"]
                    },
                    "code": {
                        "type": "string",
                        "description": "要执行的完整源代码"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30，范围 1-300。",
                        "default": 30
                    }
                },
                "required": ["language", "code"]
            }
        }
    }
]


def execute(name, arguments):
    if name == "run_code":
        return _do_code(arguments)
    return f"未知工具: {name}"
