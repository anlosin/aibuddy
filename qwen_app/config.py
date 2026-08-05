"""配置管理与对话持久化"""
import os
import json
import sqlite3
import threading


# 包目录（qwen_app/）与项目根目录（运行时数据、plugins/ 等都放在根目录）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


CONFIG_PATH = os.path.join(PROJECT_ROOT, "model_config.json")
CONVERSATIONS_DIR = os.path.join(PROJECT_ROOT, "conversations")
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
    """建表（幂等）"""
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
    db.commit()


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
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


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

    返回格式与旧 JSON 一致，chat_window.py 无需改动。
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
        # current_id 存为 pragma（单值，跨线程安全）
        cur = db.execute("PRAGMA user_version").fetchone()
        current_id = str(cur[0]) if cur and cur[0] else None
        # 如果 current_id 指向的对话已被删除，回退到第一个
        if current_id and not any(c["id"] == current_id for c in convs):
            current_id = convs[0]["id"] if convs else None
        return convs, current_id
    except Exception:
        return [], None


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
        # 用 user_version 存 current_id（整数，最多存 8 位 ID）
        # 如果 ID 不是纯数字，用 hash
        if current_id:
            try:
                ver = int(current_id, 16) & 0x7FFFFFFF
            except (ValueError, TypeError):
                ver = abs(hash(current_id)) & 0x7FFFFFFF
            db.execute(f"PRAGMA user_version={ver}")
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
