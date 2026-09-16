"""配置管理与对话持久化"""
import os
import json
import sqlite3
import threading


# 包目录（qwen_app/）与项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 运行时数据统一收纳在 data/（json、日志、数据库、知识库等），根目录保持干净
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

CONFIG_PATH = os.path.join(DATA_DIR, "model_config.json")
CONVERSATIONS_DIR = os.path.join(DATA_DIR, "conversations")
CONVERSATIONS_DB = os.path.join(CONVERSATIONS_DIR, "conversations.db")

# SQLite 连接是线程局部的（PyQt 主线程 + scheduler 后台线程）
# Day 18 (M8) 线程约束：
# - GUI 与 scheduler_run.py 不能同进程同启；同启会撞 SQLITE_BUSY
#   （虽然 WAL 允许多读单写，但 PRAGMA user_version / create 是写操作）
# - _local.conn 是 thread-local，但 _get_db() / _record() 内仍加锁保护
#   防止两个 _record 路径同时写。锁不影响单连接的速度。
import threading
_local = threading.local()
_CONN_LOCK = threading.Lock()
_all_conns = set()      # A3: 跟踪所有打开的连接（atexit 统一关闭）


def _get_db():
    """获取当前线程的 SQLite 连接（惰性创建）

    Day 19 (H-NEW-6 修复): _all_conns.add() 必须在 _CONN_LOCK 内，
    否则 close_all_conns 遍历时其他线程 add 会触发
    RuntimeError: Set changed size during iteration。
    """
    db = getattr(_local, "conn", None)
    if db is None:
        # Day 19: 加锁保护 _all_conns.add 防止并发迭代异常
        with _CONN_LOCK:
            os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
            db = sqlite3.connect(CONVERSATIONS_DB)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            _local.conn = db
            _all_conns.add(db)
    return db


def close_all_conns():
    """A3: 关闭所有打开的连接，触发 WAL checkpoint 刷盘。

    应在以下场景调用：
    - chat_window.closeEvent（GUI 关闭）
    - scheduler_run.py 退出（独立模式）
    - atexit（兜底，进程结束一定跑一次）

    Day 19 (M-NEW-6 修复): 重复调用必须幂等。atexit 在进程退出兜底跑一次，
    但 chat_window.closeEvent 也调 close_all_conns，顺序不定；如果窗口关
    闭先跑、随后 atexit 再跑，二次 pop 同一连接 → db.execute(...) 抛
    sqlite3.ProgrammingError: Cannot operate on a closed database，
    进程带着 traceback 退出（用户能看到的「闪退」）。修复：先做 "SELECT 1"
    探活，ProgrammingError 直接 skip。
    """
    while _all_conns:
        db = _all_conns.pop()
        # Day 19 (M-NEW-6): 探活 + 跳过已关闭连接
        try:
            db.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            # 已关闭（之前 close 过 / set 中残留 stale 引用），跳过
            continue
        except Exception:
            # 其他连接级错误：跳过，避免拖垮整个关闭流程
            continue
        try:
            # PRAGMA wal_checkpoint 主动刷 WAL 到主库文件，避免残留
            try:
                db.execute("PRAGMA wal_checkpoint(FULL)")
            except Exception:
                pass
            db.commit()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
    # 清掉所有线程的缓存
    for tid in list(_local.__dict__.keys()):
        if tid == "conn":
            delattr(_local, "conn")


# A3: 进程退出兜底关闭（GUI 路径会额外在 closeEvent 调）
import atexit
atexit.register(close_all_conns)


def init_conversations_db():
    """建表 + 自动跑 schema 迁移（幂等）

    A2：启动时读 schema_version，按 _MIGRATIONS 链依次跑到最新版。
    任何表结构变更后必须：
      1) SCHEMA_VERSION += 1
      2) 在 _MIGRATIONS 末尾加 _migrate_to_vN 回调（接收 db，幂等）
    """
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL DEFAULT '新对话',
            history     TEXT NOT NULL DEFAULT '[]',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS session_state (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            current_id  TEXT
        )
    """)
    db.execute("INSERT OR IGNORE INTO session_state (id, current_id) VALUES (1, NULL)")
    _migrate_legacy_user_version(db)
    db.commit()
    # A2: 跑 schema 迁移链
    _run_migrations(db)


# 当前 schema 版本号；任何表结构变更后必须 +1 并在 _MIGRATIONS 实现迁移
SCHEMA_VERSION = 1


def _get_schema_version(db):
    """从 PRAGMA user_version 读当前 schema 版本（首次启动返回 0）。"""
    cur = db.execute("PRAGMA user_version").fetchone()
    return int(cur[0]) if cur and cur[0] else 0


def _set_schema_version(db, v):
    """写 schema 版本到 PRAGMA user_version。"""
    db.execute(f"PRAGMA user_version={int(v)}")


# A2: schema 迁移链。每个迁移函数幂等（多次跑结果一致），按版本号从小到大顺序执行
def _migrate_legacy_user_version(db):
    """v0 → v1: 老数据库用 PRAGMA user_version 存 current_id（被截断到 31 位），
    现迁到独立 session_state 表。

    策略：
    1. user_version 已是 SCHEMA_VERSION → 跳过
    2. session_state.current_id 已有值 → 只同步 user_version（防御性）
    3. 查 user_version → 还原成 hex → 反查 conversations.id 精确匹配
       命中则 UPDATE session_state；不命中则保留 None
    4. 不论命中与否，PRAGMA user_version 置为 SCHEMA_VERSION
    5. current_id 仍为 None 时回退到 conversations 第一条
       （保证 UI 不空，碰运气能恢复用户之前在看的会话）
    """
    cur_ver = db.execute("PRAGMA user_version").fetchone()[0]
    if cur_ver == SCHEMA_VERSION:
        return  # 已是最新 schema

    # session_state 已有值（可能是其他迁移已写过的），只补 user_version
    existing = db.execute(
        "SELECT current_id FROM session_state WHERE id=1"
    ).fetchone()
    if existing and existing[0]:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return

    # 老格式：user_version 存的是 current_id 的 int 截位
    if cur_ver:
        try:
            candidate = hex(int(cur_ver))[2:]
            hit = db.execute(
                "SELECT id FROM conversations WHERE id = ?", (candidate,)
            ).fetchone()
            if hit:
                db.execute(
                    "UPDATE session_state SET current_id = ? WHERE id = 1",
                    (hit[0],),
                )
        except Exception:
            pass

    # 不论命中与否，schema_version 归位
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # current_id 仍为 None → 回退到 conversations 第一条
    cur = db.execute(
        "SELECT current_id FROM session_state WHERE id=1"
    ).fetchone()
    if not cur or not cur[0]:
        first = db.execute(
            "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if first:
            db.execute(
                "UPDATE session_state SET current_id = ? WHERE id = 1",
                (first[0],),
            )


# 迁移注册表：键 = 目标版本号，值 = (旧版本号, 迁移函数)
# 用字典保持顺序（Python 3.7+ dict 有序），按 SCHEMA_VERSION 升序跑
_MIGRATIONS = {
    1: (0, _migrate_legacy_user_version),
    # v2 例子: 2: (1, lambda db: db.execute("ALTER TABLE conversations ADD COLUMN ...")
}


def _run_migrations(db):
    """A2: 自动从当前 schema 版本跑到 SCHEMA_VERSION。"""
    current = _get_schema_version(db)
    if current >= SCHEMA_VERSION:
        return  # 已是最新

    # 按版本号升序跑未执行的迁移
    for target in sorted(_MIGRATIONS.keys()):
        if target <= current:
            continue
        prev, fn = _MIGRATIONS[target]
        try:
            fn(db)
            _set_schema_version(db, target)
            db.commit()
        except Exception as e:
            print(f"[config] 迁移到 schema v{target} 失败: {e}")
            # 不 raise：保证启动可用；下次启动再试
            break


def _migrate_legacy_user_version(db):
    """兼容迁移：老版本用 PRAGMA user_version 存 current_id（被截到 31 位整数）。

    这里把 user_version 的值当作 "可能的 current_id 候选" —— 反查 conversations.id
    能精确匹配上的直接修复到 session_state；匹配不上的回退到第一条对话，
    保证不丢用户可见数据（多切几次会话碰运气才能恢复，但首启动场景下多数可还原）。
    同步把 schema_version 设为 SCHEMA_VERSION，让 _migrate_to_v1 不再重复执行。
    """
    cur_ver = db.execute("PRAGMA user_version").fetchone()[0]
    if cur_ver == SCHEMA_VERSION:
        return                                  # 已是最新 schema，无需迁移
    # 检查 session_state 是否已有 current_id
    existing = db.execute("SELECT current_id FROM session_state WHERE id=1").fetchone()
    if existing and existing[0]:
        # 已是新 schema 但 PRAGMA 没同步 —— 把 PRAGMA 修正过来即可
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return
    if cur_ver:
        # 老格式：user_version 存的是 current_id 的 int 截位 → 还原成 hex → 反查
        try:
            candidate = hex(int(cur_ver))[2:]
            hit = db.execute(
                "SELECT id FROM conversations WHERE id = ?", (candidate,)
            ).fetchone()
            if hit:
                db.execute(
                    "UPDATE session_state SET current_id = ? WHERE id = 1", (hit[0],)
                )
        except Exception:
            pass
    # 不论命中与否：把 user_version 重置为真正的 schema 版本
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    # 会话仍为 None 时回退到第一条（保证 UI 不空）
    cur = db.execute("SELECT current_id FROM session_state WHERE id=1").fetchone()
    if not cur or not cur[0]:
        first = db.execute(
            "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if first:
            db.execute(
                "UPDATE session_state SET current_id = ? WHERE id = 1", (first[0],)
            )


# ═══ 模型配置 ═══

# ═══ API Key 安全存储（系统凭据库 / keyring） ═══
# 目标（Day 20.6.12，audit_verification.md P0-SEC-7）：
#   api_key 不再明文写进 data/model_config.json。项目**本来就有**
#   plugins/_secret_store（keyring + 内存降级，ssh/sql 密码均已接入），
#   API Key 是当时唯一漏网的敏感字段 —— 属于自身不一致而非新增需求。
#
# 降级策略（关键，防止把用户的 Key 弄丢）：
#   **只有 keyring 真实可用时才脱敏磁盘**；不可用时保持明文落盘不动。
#   宁可暂时保留既有明文，也不能出现「磁盘清了、keyring 没存上」的丢 Key。
_MODEL_KEY_SERVICE = "model"
_KEY_CACHE = {}


def _get_secret_store():
    """返回 plugins._secret_store 模块；不可用返回 None（此时不做任何脱敏）"""
    try:
        from plugins import _secret_store as ss
        return ss
    except Exception:
        return None


def _model_secret_name(m):
    """模型在凭据库里的条目名（优先用不可变的 id）"""
    return str(m.get("id") or m.get("model_id") or m.get("name") or "default")


def _keyring_usable():
    ss = _get_secret_store()
    try:
        return ss if (ss and ss.is_available()) else None
    except Exception:
        return None


def _read_key(ss, name):
    """带进程内缓存的读取：一次会话内同一模型只查一次凭据库"""
    if name in _KEY_CACHE:
        return _KEY_CACHE[name]
    try:
        v = ss.get_secret(_MODEL_KEY_SERVICE, name)
    except Exception:
        v = None
    if v:
        _KEY_CACHE[name] = v
    return v


def _sanitize_api_keys(cfg):
    """写盘前：把 api_key 存进系统凭据库，磁盘上只留空字符串

    仅当 keyring 真可用时脱敏；任一步失败都会保留原明文（不丢 Key）。
    """
    ss = _keyring_usable()
    if not ss:
        return cfg
    out = dict(cfg)
    models = out.get("models")
    if isinstance(models, list):
        new_models = []
        for m in models:
            if isinstance(m, dict):
                m2 = dict(m)
                key = (m2.get("api_key") or "").strip()
                if key:
                    name = _model_secret_name(m2)
                    try:
                        ss.set_secret(_MODEL_KEY_SERVICE, name, key)
                        _KEY_CACHE[name] = key
                        m2["api_key"] = ""
                    except Exception:
                        pass          # 存不进去就保留明文，宁可不脱敏也不丢 Key
                new_models.append(m2)
            else:
                new_models.append(m)
        out["models"] = new_models
    # 兼容旧版扁平字段
    flat = (out.get("api_key") or "").strip()
    if flat:
        try:
            ss.set_secret(_MODEL_KEY_SERVICE, "flat", flat)
            _KEY_CACHE["flat"] = flat
            out["api_key"] = ""
        except Exception:
            pass
    return out


def _hydrate_api_keys(cfg):
    """读盘后：磁盘为空但凭据库里有 → 填回内存字典

    磁盘上若仍是明文（尚未迁移 / 脱敏失败），原样保留不动 —— 兼容旧数据。
    """
    ss = _get_secret_store()
    if not ss:
        return cfg
    models = cfg.get("models")
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict) and not (m.get("api_key") or "").strip():
                v = _read_key(ss, _model_secret_name(m))
                if v:
                    m["api_key"] = v
    if not (cfg.get("api_key") or "").strip():
        v = _read_key(ss, "flat")
        if v:
            cfg["api_key"] = v
    return cfg


def load_config():
    """加载模型配置，返回字典（api_key 由系统凭据库回填）"""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return _hydrate_api_keys(json.load(f))
    except Exception:
        pass
    return {}


def save_config(cfg):
    """保存模型配置（api_key 存入系统凭据库，磁盘不留明文）"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    safe_cfg = _sanitize_api_keys(cfg)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(safe_cfg, f, ensure_ascii=False, indent=2)
    # 内存中的调用方字典不应被"顺手清空"——保持调用方拿到的对象原样
    return safe_cfg


def migrate_api_keys_to_keyring():
    """把磁盘上残留的明文 api_key 迁移进系统凭据库（幂等）

    由 main.py 在启动期调用一次（单线程，避免与 GUI / 调度器线程并发写盘）。
    返回 (是否发生迁移, 说明)。

    安全前提：只有 keyring 真实可用、且 _sanitize_api_keys 成功把 Key 写进
    凭据库（写失败会保留明文）时才重写磁盘 —— 保证不会出现
    「磁盘清了、凭据库没存上」的用户 Key 丢失。
    """
    if not os.path.exists(CONFIG_PATH):
        return False, "无配置文件，跳过"
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        return False, f"读取失败: {e}"

    has_plaintext = bool((raw.get("api_key") or "").strip()) or any(
        isinstance(m, dict) and (m.get("api_key") or "").strip()
        for m in (raw.get("models") or [])
    )
    if not has_plaintext:
        return False, "磁盘无明文，无需迁移"
    if not _keyring_usable():
        return False, "系统凭据库不可用，保留明文（避免丢 Key）"

    safe = _sanitize_api_keys(raw)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(safe, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return False, f"写回失败: {e}"
    return True, "已迁移到系统凭据库"


# ── 多模型注册表 ──
# data/model_config.json 结构：
#   models: [ {id, name, base_url, api_key, model_id, proxy,
#              enable_thinking, enable_tools}, ... ]
#   current_model: 当前激活模型的 id
# 旧版扁平字段（model_id/api_key/base_url/proxy/enable_thinking/enable_tools）
# 保留兼容：首次加载若无 models，自动迁移为一条模型记录并写回。

_MODEL_FIELDS = ("base_url", "api_key", "model_id", "proxy",
                 "enable_thinking", "enable_tools")


def _migrate_models(cfg):
    """旧扁平配置 → models 注册表迁移（无损：原字段保留）。

    若 cfg 已含非空 models 列表则原样返回；否则把扁平字段打包成一条
    「默认」模型（字段缺失时给安全默认值），并设为 current_model。
    """
    models = cfg.get("models")
    if isinstance(models, list) and models:
        # 补齐可能缺失的 id
        for m in models:
            if isinstance(m, dict) and not m.get("id"):
                m["id"] = _new_model_id()
        if not cfg.get("current_model"):
            cfg["current_model"] = models[0].get("id")
        return cfg

    # 从扁平字段构造一条默认模型
    default_model = {
        "id": _new_model_id(),
        "name": "默认模型",
        "base_url": cfg.get("base_url", ""),
        "api_key": cfg.get("api_key", ""),
        "model_id": cfg.get("model_id", ""),
        "proxy": cfg.get("proxy", ""),
        "enable_thinking": cfg.get("enable_thinking", False),
        "enable_tools": cfg.get("enable_tools", True),
    }
    cfg["models"] = [default_model]
    cfg["current_model"] = default_model["id"]
    return cfg


def _new_model_id():
    import time
    import random
    # 时间戳保证趋势唯一，随机后缀防同一毫秒内撞 id
    return "m_%d_%04x" % (int(time.time() * 1000), random.getrandbits(16))


def load_models():
    """加载模型注册表，返回 (models: list, current_id: str)

    自动完成旧配置迁移；空 key 的模型也会保留（用户可能还没填）。
    """
    cfg = _migrate_models(load_config())
    models = cfg.get("models", [])
    current_id = cfg.get("current_model") or (models[0]["id"] if models else None)
    # current_model 指向的模型不存在时回退到第一个
    if current_id and not any(m.get("id") == current_id for m in models):
        current_id = models[0]["id"] if models else None
    return models, current_id


def save_models(models, current_id):
    """保存模型注册表（保留配置中其他字段不动）"""
    cfg = load_config()
    cfg["models"] = models
    cfg["current_model"] = current_id
    save_config(cfg)


def get_current_model():
    """获取当前激活模型的完整字典（含 id/name），找不到返回 None"""
    models, current_id = load_models()
    for m in models:
        if m.get("id") == current_id:
            return m
    return models[0] if models else None


def make_openai_client(api_key, base_url, proxy=""):
    """构建 OpenAI 兼容客户端。

    proxy 非空时，仅让「这一个」客户端（即模型连接）走代理——
    通过自定义 httpx.Client 注入，插件（requests/paramiko/urllib 等）
    使用的其它连接不受影响。proxy 留空则不设置代理。
    支持 http://、https://、socks5:// 等 httpx 接受的格式。
    """
    from openai import OpenAI
    kwargs = {"api_key": api_key, "base_url": base_url}
    p = (proxy or "").strip()
    if p:
        import httpx
        kwargs["http_client"] = httpx.Client(proxy=p)
    return OpenAI(**kwargs)


# ═══ 对话列表（SQLite） ═══

def load_conversations():
    """从 SQLite 加载对话列表，返回 (conversations: list, current_id: str|None)

    current_id 现在持久化在独立的 session_state 表（不再用 PRAGMA user_version
    那种会被截断的方式）。返回格式与旧 JSON 一致，chat_window.py 无需改动。
    """
    init_conversations_db()
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT id, title, history, created_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        convs = []
        for r in rows:
            try:
                history = json.loads(r["history"])
            except Exception:
                history = []
            convs.append({
                "id": r["id"],
                "title": r["title"],
                "history": history,
                "created_at": r["created_at"],
            })
        cur = db.execute("SELECT current_id FROM session_state WHERE id=1").fetchone()
        current_id = cur[0] if cur else None
        # 如果 current_id 指向的对话已被删除，回退到第一个
        if current_id and not any(c["id"] == current_id for c in convs):
            current_id = convs[0]["id"] if convs else None
            if current_id:
                db.execute(
                    "UPDATE session_state SET current_id=? WHERE id=1", (current_id,)
                )
                db.commit()
        return convs, current_id
    except Exception:
        return [], None


def set_current_conversation(conv_id):
    """显式设置当前会话（持久化到 session_state）。

    与 save_single_conversation/save_conversations 不同：本函数只动 current_id，
    不重写 history，避免读改写竞争（多 worker 并发场景下更安全）。
    """
    init_conversations_db()
    db = _get_db()
    db.execute("UPDATE session_state SET current_id=? WHERE id=1", (conv_id,))
    db.commit()


def save_single_conversation(conv, current_id):
    """增量更新：只写一条对话到 SQLite，不碰其他对话。

    适用于仅修改当前对话 history/title 的场景（发送消息、切换对话等），
    避免全量同步带来的不必要 I/O 开销。

    Day 18 (M8)：包 _CONN_LOCK 防同线程内两路并发写。
    scheduler._record 与 chat_bridge._append_history 都会调本函数，
    之前无锁保护，理论上同一线程内 task + 自动重试可能踩到。
    """
    init_conversations_db()
    db = _get_db()
    history_json = json.dumps(conv.get("history", []), ensure_ascii=False)
    with _CONN_LOCK:
        db.execute("""
            INSERT INTO conversations (id, title, history, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                history=excluded.history,
                updated_at=excluded.updated_at
        """, (
            conv["id"],
            conv.get("title", "新对话"),
            history_json,
            conv.get("created_at", ""),
            conv.get("updated_at", conv.get("created_at", "")),
        ))
        if current_id:
            db.execute(
                "UPDATE session_state SET current_id=? WHERE id=1", (current_id,)
            )
        db.commit()


def save_conversations(conversations, current_id):
    """全量同步对话列表到 SQLite（保持与旧 JSON 接口一致）

    Day 18 (M8)：包 _CONN_LOCK + 内层 try/except，保证锁正确释放。
    """
    init_conversations_db()
    db = _get_db()
    with _CONN_LOCK:
        try:
            existing = {r["id"] for r in db.execute("SELECT id FROM conversations").fetchall()}
            incoming = set()
            for conv in conversations:
                incoming.add(conv["id"])
                history_json = json.dumps(conv.get("history", []), ensure_ascii=False)
                db.execute("""
                    INSERT INTO conversations (id, title, history, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title,
                        history=excluded.history,
                        updated_at=excluded.updated_at
                """, (
                    conv["id"],
                    conv.get("title", "新对话"),
                    history_json,
                    conv.get("created_at", ""),
                    conv.get("updated_at", conv.get("created_at", "")),
                ))
            for oid in existing - incoming:
                db.execute("DELETE FROM conversations WHERE id=?", (oid,))
            if current_id:
                db.execute(
                    "UPDATE session_state SET current_id=? WHERE id=1", (current_id,)
                )
            db.commit()
        except Exception as e:
            print(f"保存对话列表失败: {e}")


# ═══ 插件状态 ═══

def _scan_plugins():
    """扫描 plugins/ 目录，返回所有插件名（不含 __init__）"""
    base = os.path.join(PROJECT_ROOT, "plugins")
    names = []
    try:
        for f in sorted(os.listdir(base)):
            if f.endswith(".py") and f not in ("__init__.py",):
                names.append(f[:-3])
    except Exception:
        pass
    return names


def load_plugin_state():
    """从配置中加载已启用插件列表；如未配置则默认启用全部插件"""
    cfg = load_config()
    val = cfg.get("enabled_plugins")
    if val is not None:
        return val
    return _scan_plugins()


def save_plugin_state(enabled_plugins):
    """保存已启用插件列表到配置"""
    cfg = load_config()
    cfg["enabled_plugins"] = list(enabled_plugins)
    save_config(cfg)
