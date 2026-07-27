"""配置管理与对话持久化"""
import os
import json


def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_project_dir(), "model_config.json")
CONVERSATIONS_DIR = os.path.join(_project_dir(), "conversations")


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


# ═══ 对话列表 ═══

def load_conversations():
    """加载对话列表，返回 (conversations: list, current_id: str|None)"""
    try:
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
        index_path = os.path.join(CONVERSATIONS_DIR, "index.json")
        if os.path.exists(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get("conversations", []), data.get("current_id", None)
    except Exception:
        pass
    return [], None


def save_conversations(conversations, current_id):
    """保存对话列表"""
    try:
        os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
        index_path = os.path.join(CONVERSATIONS_DIR, "index.json")
        data = {
            "conversations": conversations,
            "current_id": current_id,
        }
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存对话列表失败: {e}")


# ═══ 插件状态 ═══

def load_plugin_state():
    """从配置中加载已启用插件列表"""
    cfg = load_config()
    return cfg.get("enabled_plugins", ["calculator", "clock"])


def save_plugin_state(enabled_plugins):
    """保存已启用插件列表到配置"""
    cfg = load_config()
    cfg["enabled_plugins"] = list(enabled_plugins)
    save_config(cfg)
