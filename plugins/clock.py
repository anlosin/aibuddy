"""时钟插件 — 获取当前日期时间"""
from datetime import datetime

PLUGIN_INFO = {
    "name": "clock",
    "description": "获取当前日期和时间，包括星期几",
    "version": "1.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前日期和时间，包括星期几",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
]


def execute(name, arguments):
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return f"{now.strftime('%Y年%m月%d日 %H:%M:%S')} {weekdays[now.weekday()]}"
