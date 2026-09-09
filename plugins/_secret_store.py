"""凭据存储封装 — 把密码加密存在系统凭据库（Windows 凭据管理器 /
macOS Keychain / Linux Secret Service），重启后无需重输；keyring
不可用时自动降级为进程内内存缓存，不阻塞使用。

设计目标：
1. 调用方只需 3 个函数：set_secret / get_secret / delete_secret
2. 存盘 cfg 不含明文密码（只有 keyring account 引用）
3. keyring 抛任何异常 → 退回内存 dict，调用方不感知
4. 同 account 再次 set 视为覆盖（修改密码场景）
5. 进程退出时内存缓存自动消失（无需清理）
"""
import threading
import traceback


# 进程内内存降级缓存：keyring 不可用时暂存
_FALLBACK = {}
_FALLBACK_LOCK = threading.Lock()

# 探测一次 keyring 是否可用 + 后端类型
_AVAILABLE = None
_BACKEND = None


def _probe():
    """首次调用时探测 keyring 是否可用 + 后端类型，结果缓存到模块级变量"""
    global _AVAILABLE, _BACKEND
    if _AVAILABLE is not None:
        return
    try:
        import keyring
        from keyring.errors import KeyringError
        backend = keyring.get_keyring()
        # 触发一次真实读写以确认后端真的能用（部分 Linux 无 daemon 时
        # get_keyring() 返回的对象看起来可用但 set/get 会抛 NoKeyringError）
        probe_service = "__aibuddy_probe__"
        probe_user = "probe"
        keyring.set_password(probe_service, probe_user, "x")
        keyring.delete_password(probe_service, probe_user)
        _BACKEND = type(backend).__module__ + "." + type(backend).__name__
        _AVAILABLE = True
    except Exception as e:
        _AVAILABLE = False
        _BACKEND = f"unavailable: {type(e).__name__}: {e}"
        # 只在首次探测时打印一次，避免每个工具调用都刷屏
        print(f"[SecretStore] keyring 不可用，启用内存降级缓存: {_BACKEND}")


def is_available():
    """返回 keyring 后端名；不可用时返回 False"""
    _probe()
    return _BACKEND if _AVAILABLE else False


def _account(service, name):
    """统一 account 命名：aibuddy:<service>:<name>"""
    return f"aibuddy:{service}:{name}"


def _username(service, name):
    """统一 keyring 的 username 字段：让用户在凭据管理器里一眼看出这是哪个连接的密码。

    - sql:<conn_name>  →  sql-<conn_name>          （数据库连接名）
    - ssh:<conn_name>  →  ssh-<conn_name>          （SSH 连接名）
    - ssh:<conn_name>#keypass  →  ssh-<conn_name>-key  （私钥口令）
    """
    suffix = ""
    if name.endswith("#keypass"):
        base = name[: -len("#keypass")]
        suffix = "-key"
        name = base
    return f"{service}-{name}{suffix}"


def set_secret(service, name, password):
    """保存密码到系统凭据库；同名 service+name 视为覆盖（修改密码场景）。

    返回 True=成功 / False=失败（已降级到内存）
    """
    if not password:
        delete_secret(service, name)
        return True
    _probe()
    account = _account(service, name)
    username = _username(service, name)
    if _AVAILABLE:
        try:
            import keyring
            keyring.set_password(account, username, password)
            return True
        except Exception:
            traceback.print_exc()
            # 降级到内存
    with _FALLBACK_LOCK:
        _FALLBACK[account] = password
    return False


def get_secret(service, name):
    """从系统凭据库取密码；keyring 不可用或没有则从内存缓存取；都没有返回 None。"""
    _probe()
    account = _account(service, name)
    username = _username(service, name)
    if _AVAILABLE:
        try:
            import keyring
            v = keyring.get_password(account, username)
            if v is not None:
                return v
        except Exception:
            pass
    with _FALLBACK_LOCK:
        return _FALLBACK.get(account)


def delete_secret(service, name):
    """从系统凭据库和内存缓存中同时删除（防止残留）"""
    _probe()
    account = _account(service, name)
    username = _username(service, name)
    with _FALLBACK_LOCK:
        _FALLBACK.pop(account, None)
    if _AVAILABLE:
        try:
            import keyring
            keyring.delete_password(account, username)
        except Exception:
            # 不存在时 keyring 也会抛 PasswordDeleteError，吞掉即可
            pass
