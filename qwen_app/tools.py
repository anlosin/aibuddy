"""默认配置（工具定义已迁移到 plugins/ 目录）"""

from . import paths


def _scan_plugins():
    """扫描生效的插件目录，返回所有插件名（不含 __init__ 与私有模块）

    统一走 qwen_app.paths.scan_plugin_names()（与 config._scan_plugins 同源）。
    打包态会同时覆盖「exe 同级 plugins/」与内置插件，用户新增的插件因此也能
    出现在默认启用列表里。
    """
    return paths.scan_plugin_names()


DEFAULT_CONFIG = {
    "model_id": "DeepSeek-R1-Distill-Qwen-32B",
    "api_key": "",   # ⚠️ 请通过 model_config.json 配置真实 API Key
    "base_url": "",   # ⚠️ 请通过 model_config.json 配置 API 地址
    "enable_thinking": False,
    "enable_tools": True,
    "enabled_plugins": _scan_plugins(),
    "workspace_root": "",
    "agent_mode": False,
    "max_agent_rounds": 12,
    "proxy": "",
}
