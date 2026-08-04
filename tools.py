"""默认配置（工具定义已迁移到 plugins/ 目录）"""

import os


def _scan_plugins():
    """扫描 plugins/ 目录，返回所有插件名（不含 __init__）"""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
    names = []
    try:
        for f in sorted(os.listdir(base)):
            if f.endswith(".py") and f not in ("__init__.py",):
                names.append(f[:-3])
    except Exception:
        pass
    return names


DEFAULT_CONFIG = {
    "model_id": "DeepSeek-R1-Distill-Qwen-32B",
    "api_key": "",   # ⚠️ 请通过 model_config.json 配置真实 API Key
    "base_url": "",   # ⚠️ 请通过 model_config.json 配置 API 地址
    "enable_thinking": True,
    "enable_tools": False,
    "enabled_plugins": _scan_plugins(),
    "workspace_root": "",
    "agent_mode": False,
    "max_agent_rounds": 12,
    "proxy": "",
}
