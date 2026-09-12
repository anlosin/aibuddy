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
_local = threading.local()


def _get_db():
    """获取当前线程的 SQLite 连接（惰性创建）"""
    db = getattr(_local, "conn", None)
    if db is None:
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
        db = sqlite3.connect(CONVERSATIONS_DB)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        _local.conn = db
    return db


def init_conversations_db():
    """建表（幂等）

    schema_version 是真实的 PRAGMA user_version 计数器；
    session_state 是独立的 current_id 持久化表（之前用 PRAGMA user_version 存
    current_id，会话 ID 截到 31 位整数后还原会丢字符/撞 ID，已迁出）。
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


# 当前 schema 版本号；任何表结构变更后必须 +1 并在 _migrate_to_vN 实现迁移
SCHEMA_VERSION = 1


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

def load_config():
    """加载模型配置，返回字典"""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(cfg):
    """保存模型配置"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


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
    """
    init_conversations_db()
    db = _get_db()
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
    if current_id:
        db.execute(
            "UPDATE session_state SET current_id=? WHERE id=1", (current_id,)
        )
    db.commit()


def save_conversations(conversations, current_id):
    """全量同步对话列表到 SQLite（保持与旧 JSON 接口一致）"""
    init_conversations_db()
    db = _get_db()
    try:
        # 获取现有 ID 集合
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
        # 删除已不在列表中的对话
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
