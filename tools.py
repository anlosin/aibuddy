"""默认配置（工具定义已迁移到 plugins/ 目录）"""

DEFAULT_CONFIG = {
    "model_id": "DeepSeek-R1-Distill-Qwen-32B",
    "api_key": "6d6f5ebbafd04708943269625b204482",
    "base_url": "https://wishub-x1.ctyun.cn/v1",
    "enable_thinking": True,
    "enable_tools": False,
    "enabled_plugins": ["calculator", "clock"],
    "workspace_root": "",
    "agent_mode": False,
    "max_agent_rounds": 12,
    "proxy": "",
}
