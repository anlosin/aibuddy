"""SSH 远程执行插件 — 通过 SSH 连接远程 Linux/Unix 主机，执行命令、传输文件，实现跨机器的“干活”能力

与 shell_runner 互补：
- shell_runner 在本机（Windows 宿主）执行
- ssh_runner 在远程 Linux 主机执行（最适合内网服务器运维、批量操作）

连接管理模仿 sql_helper 的范式：
- 连接配置持久化到 ssh_connections.json
- 同进程内缓存已建立的 SSHClient，超时/断开时自动重连
- 支持密码认证与私钥认证（含加密私钥的 passphrase）

安全设计：
- 复用 shell_runner 的破坏性命令黑名单（命中直接拒绝），避免误删远程主机文件
- 连接失败 / 认证失败返回友好错误，不抛异常崩溃
- 默认 AutoAddPolicy 方便内网（未知主机密钥自动接受），可通过 auto_add_host_key=false 关闭
"""
import os
import re
import json

PLUGIN_INFO = {
    "name": "ssh_runner",
    "description": "通过 SSH 连接远程 Linux/Unix 主机，执行命令、上传/下载文件。用于内网服务器运维、批量操作和跨机器自动化。",
    "version": "1.0",
}

SYSTEM_PROMPT = """你拥有通过 SSH 操作远程 Linux/Unix 主机的能力（ssh_runner 插件），可以真正跨机器“干活”，而不仅仅是在本机执行。

适用场景：
- 登录内网服务器执行命令（查看进程、磁盘、日志、部署服务）
- 在远程主机做文件处理、系统查询、批量脚本操作
- 上传本地脚本到服务器执行，或下载服务器上的日志/结果文件

使用流程：
1. 若是第一次连接某台主机，先调用 ssh_connect 配置并保存连接（密码或私钥认证）
2. 之后用 ssh_command 在远程主机执行命令；ssh_upload / ssh_download 传输文件
3. 可用 ssh_list_connections 查看已保存的连接

安全准则：
- 优先使用安全、可逆的操作，涉及重要数据或批量操作前先预览确认
- 不要执行破坏性命令（如 rm -rf /、格式化、关机等），插件会直接拒绝
- 需要 root 权限时再考虑 sudo，并尽量用精确的命令
- 文件路径尽量用绝对路径，避免搞错远程工作目录
"""

CONN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ssh_connections.json")

OUTPUT_LIMIT = 8000  # 远程命令输出截断上限（字符）

# ── 破坏性命令黑名单（与 shell_runner 保持一致，命中直接拒绝）──
BLOCKED_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\brm\s+-rf\s+/\*",
    r"\brd\s+/s",
    r"\bdel\s+/[sqf]",
    r"\bformat\s+[a-z]:",
    r"\bmkfs",
    r"\bdd\s+if=.*of=/dev/",
    r">\s*/dev/sd",
    r":\(\).*\{\s*:\|:",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bpoweroff\b",
    r"\bfdisk\b",
    r"\bparted\b",
    r"\bchmod\s+-R\s+0",
    r"\btruncate\s+-s\s+0\s+/",
    r"\bmkfs\.",
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

# 同进程内连接缓存：name -> paramiko.SSHClient
_CLIENTS = {}
# 连接配置缓存
_CFG = {}


def _load_cfg():
    global _CFG
    try:
        if os.path.exists(CONN_FILE):
            with open(CONN_FILE, "r", encoding="utf-8") as f:
                _CFG = json.load(f)
    except Exception:
        _CFG = {}


def _save_cfg():
    try:
        with open(CONN_FILE, "w", encoding="utf-8") as f:
            json.dump(_CFG, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _refuse_if_blocked(command):
    for r in _BLOCKED_RE:
        if r.search(command or ""):
            return ("⛔ 出于安全考虑，该命令被拒绝执行（命中破坏性操作黑名单：%s）。"
                    "如需执行，请改用更安全的等价写法，或联系管理员调整策略。"
                    % r.pattern)
    return None


def _get_client(name):
    """获取（必要时重连）指定名称的 SSHClient，返回 (client, err)"""
    if name in _CLIENTS and _CLIENTS[name] is not None:
        try:
            t = _CLIENTS[name].get_transport()
            if t is not None and t.is_active():
                return _CLIENTS[name], None
        except Exception:
            _CLIENTS[name] = None
    _load_cfg()
    cfg = _CFG.get(name)
    if not cfg:
        return None, f"未找到连接配置 '{name}'，请先调用 ssh_connect"
    client, err = _connect(cfg)
    if err:
        return None, err
    _CLIENTS[name] = client
    return client, None


def _connect(cfg):
    """根据配置建立 SSHClient，返回 (client, err)"""
    try:
        import paramiko
    except ImportError:
        return None, "缺少依赖 paramiko，请在项目 venv 中执行：pip install paramiko"
    try:
        client = paramiko.SSHClient()
        if cfg.get("auto_add_host_key", True):
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        kwargs = {
            "hostname": cfg.get("host"),
            "port": int(cfg.get("port", 22)),
            "username": cfg.get("user"),
            "timeout": int(cfg.get("timeout", 15)),
            "compress": True,
        }
        key_path = cfg.get("key_path")
        password = cfg.get("password")
        if key_path:
            kwargs["key_filename"] = key_path
            if cfg.get("key_passphrase"):
                kwargs["passphrase"] = cfg["key_passphrase"]
        elif password:
            kwargs["password"] = password
        else:
            # 尝试无密码（如已配置 SSH agent / 密钥默认位置）
            pass
        client.connect(**kwargs)
        return client, None
    except Exception as e:
        return None, f"连接失败: {e}"


def _do_connect(args):
    name = args.get("name") or "default"
    cfg = {
        "host": args.get("host"),
        "port": int(args.get("port", 22)),
        "user": args.get("user"),
        "password": args.get("password", ""),
        "key_path": args.get("key_path"),
        "key_passphrase": args.get("key_passphrase", ""),
        "auto_add_host_key": bool(args.get("auto_add_host_key", True)),
        "timeout": int(args.get("timeout", 15)),
    }
    if not cfg.get("host"):
        return "错误: 未提供 host"
    if not cfg.get("user"):
        return "错误: 未提供 user"
    client, err = _connect(cfg)
    if err:
        return f"连接测试失败: {err}"
    try:
        # 探活
        stdin, stdout, stderr = client.exec_command("echo __ssh_runner_probe__")
        out = stdout.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if code != 0 or "__ssh_runner_probe__" not in out:
            return f"连接建立但探活失败: {stderr.read().decode('utf-8','replace')[:300]}"
    except Exception as e:
        return f"连接建立但探活失败: {e}"
    finally:
        try:
            client.close()
        except Exception:
            pass
    _load_cfg()
    _CFG[name] = cfg
    _save_cfg()
    auth = "私钥(%s)" % cfg["key_path"] if cfg.get("key_path") else "密码" if cfg.get("password") else "无密码/agent"
    return f"✅ 连接成功并已保存: [{name}] {cfg['user']}@{cfg['host']}:{cfg['port']} (认证方式: {auth})"


def _do_command(args):
    name = args.get("name") or "default"
    command = args.get("command", "")
    if not command:
        return "错误: 未提供 command"
    refuse = _refuse_if_blocked(command)
    if refuse:
        return refuse
    timeout = int(args.get("timeout", 60))
    cwd = args.get("cwd")
    use_sudo = bool(args.get("use_sudo", False))
    run_cmd = command
    if cwd:
        run_cmd = 'cd "%s" && %s' % (cwd, command)

    client, err = _get_client(name)
    if err:
        return err
    try:
        if use_sudo:
            run_cmd = "sudo -S -p '' " + run_cmd
        stdin, stdout, stderr = client.exec_command(run_cmd, timeout=timeout)
        if use_sudo:
            pw = _CFG.get(name, {}).get("password", "")
            if pw:
                stdin.write(pw + "\n")
                stdin.flush()
        try:
            out = stdout.read().decode("utf-8", "replace")
        except Exception:
            out = ""
        try:
            err_out = stderr.read().decode("utf-8", "replace")
        except Exception:
            err_out = ""
        try:
            code = stdout.channel.recv_exit_status()
        except Exception:
            code = -1
        full = out
        if err_out:
            full = (full + "\n[STDERR]\n" + err_out).strip()
        if len(full) > OUTPUT_LIMIT:
            full = full[:OUTPUT_LIMIT] + "\n\n... [输出已截断，完整长度 %d 字符]" % len(full)
        return "主机 [%s] 退出码: %d\n%s" % (name, code, full)
    except Exception as e:
        # 连接可能中途断开，清缓存触发下次重连
        _CLIENTS[name] = None
        return f"❌ 命令执行失败: {e}"


def _sftp_transfer(args, upload):
    name = args.get("name") or "default"
    local_path = args.get("local_path", "")
    remote_path = args.get("remote_path", "")
    if not local_path or not remote_path:
        return "错误: 需同时提供 local_path 和 remote_path"
    client, err = _get_client(name)
    if err:
        return err
    try:
        sftp = client.open_sftp()
        if upload:
            sftp.put(local_path, remote_path)
            sftp.close()
            return f"✅ 已上传: {local_path} → [{name}]{remote_path}"
        else:
            sftp.get(remote_path, local_path)
            sftp.close()
            return f"✅ 已下载: [{name}]{remote_path} → {local_path}"
    except Exception as e:
        _CLIENTS[name] = None
        return f"❌ 文件传输失败: {e}"


def _do_list_connections(args):
    _load_cfg()
    if not _CFG:
        return "（尚未保存任何 SSH 连接，请先用 ssh_connect 添加）"
    lines = ["已保存的 SSH 连接:"]
    for n, c in _CFG.items():
        auth = "私钥" if c.get("key_path") else ("密码" if c.get("password") else "无密码/agent")
        lines.append(f"  • {n}: {c.get('user','?')}@{c.get('host','?')}:{c.get('port',22)}  [{auth}]")
    return "\n".join(lines)


def _do_disconnect(args):
    name = args.get("name")
    if name:
        if name in _CLIENTS:
            try:
                _CLIENTS[name].close()
            except Exception:
                pass
            _CLIENTS.pop(name, None)
        return f"已关闭连接: {name}"
    # 全部关闭
    for n, c in list(_CLIENTS.items()):
        try:
            c.close()
        except Exception:
            pass
    _CLIENTS.clear()
    return "已关闭全部 SSH 连接"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ssh_connect",
            "description": "配置并测试到远程主机的 SSH 连接，配置会持久化以便后续复用。支持密码认证或私钥认证（key_path）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"},
                    "host": {"type": "string", "description": "远程主机地址（IP 或域名）"},
                    "port": {"type": "integer", "description": "SSH 端口，默认 22"},
                    "user": {"type": "string", "description": "登录用户名"},
                    "password": {"type": "string", "description": "密码（与 key_path 二选一）"},
                    "key_path": {"type": "string", "description": "私钥文件路径（与 password 二选一，如 ~/.ssh/id_rsa）"},
                    "key_passphrase": {"type": "string", "description": "若私钥有密码保护，提供 passphrase"},
                    "auto_add_host_key": {"type": "boolean", "description": "是否自动接受未知主机密钥（内网方便，默认 true）", "default": True},
                    "timeout": {"type": "integer", "description": "连接超时秒数，默认 15", "default": 15}
                },
                "required": ["host", "user"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_command",
            "description": "在远程主机执行一条 shell 命令，返回退出码与输出（stdout+stderr）。破坏性命令会被拒绝。可指定工作目录与是否用 sudo。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"},
                    "command": {"type": "string", "description": "要执行的远程命令，如 'uname -a'、'df -h'、'systemctl status nginx'"},
                    "cwd": {"type": "string", "description": "远程工作目录（可选），命令会先 cd 到该目录再执行"},
                    "use_sudo": {"type": "boolean", "description": "是否使用 sudo 执行（默认 false）", "default": False},
                    "timeout": {"type": "integer", "description": "命令超时秒数，默认 60", "default": 60}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_upload",
            "description": "通过 SFTP 上传本地文件到远程主机。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"},
                    "local_path": {"type": "string", "description": "本地文件路径"},
                    "remote_path": {"type": "string", "description": "远程目标路径（绝对路径）"}
                },
                "required": ["local_path", "remote_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_download",
            "description": "通过 SFTP 从远程主机下载文件到本地。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名，默认 default"},
                    "remote_path": {"type": "string", "description": "远程文件路径（绝对路径）"},
                    "local_path": {"type": "string", "description": "本地保存路径"}
                },
                "required": ["remote_path", "local_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_list_connections",
            "description": "列出已保存的 SSH 连接（主机、用户、认证方式），便于确认连接名。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_disconnect",
            "description": "关闭 SSH 连接（释放资源）。可指定 name 只关闭一个，或省略关闭全部。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接名（可选），省略则关闭全部"}
                },
                "required": []
            }
        }
    },
]


def execute(name, arguments):
    handlers = {
        "ssh_connect": _do_connect,
        "ssh_command": _do_command,
        "ssh_upload": lambda a: _sftp_transfer(a, upload=True),
        "ssh_download": lambda a: _sftp_transfer(a, upload=False),
        "ssh_list_connections": _do_list_connections,
        "ssh_disconnect": _do_disconnect,
    }
    fn = handlers.get(name)
    if fn:
        return fn(arguments)
    return f"未知工具: {name}"
