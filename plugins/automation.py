"""自动化任务插件 — 让用户在对话里直接创建 / 查询 / 删除定时任务。

通过工具调用（function calling）触发，复用 Scheduler 模块级单例拿到调度器实例。
无需手动打开「自动化 → 任务管理」对话框即可用自然语言新建定时任务。

调度类型说明（schedule_type）：
- interval : 每隔 N 分/时/天执行（配合 interval_value + interval_unit）
- daily    : 每天固定时间（配合 time，格式 HH:MM）
- weekly   : 每周指定星期几 + 时间（配合 weekdays + time）
- once     : 指定一次时间（配合 datetime_str，格式 yyyy-MM-dd HH:mm）

执行模型：model 留空或填「跟随主模型」→ 默认跟随当前主模型；
也可填注册表里的模型名/模型 id（如「ali」）固定用该模型。
"""
import json

PLUGIN_INFO = {
    "name": "自动化任务",
    "version": "1.0.0",
    "description": "在对话中创建、查询、删除定时任务（每天/每周/每隔N分钟/一次性）。",
}

SYSTEM_PROMPT = (
    "你可以在对话中直接帮用户管理定时任务：\n"
    "1) 新建定时任务用 create_automation 工具；\n"
    "2) 查看已有任务用 list_automations 工具；\n"
    "3) 删除任务用 delete_automation 工具。\n"
    "创建前先与用户确认任务名称、要执行的提示词、执行频率和具体时间；"
    "用户说「每天」「每周」「每隔N分钟」「明天几点」等时主动识别为定时需求。"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_automation",
            "description": "创建一个定时任务。返回创建结果（含任务 id）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称，简短描述用途"},
                    "prompt": {"type": "string", "description": "定时执行时发送给模型的提示词"},
                    "schedule_type": {
                        "type": "string",
                        "enum": ["interval", "daily", "weekly", "once"],
                        "description": "调度类型：interval=间隔执行 / daily=每天固定时间 / weekly=每周 / once=一次性",
                    },
                    "interval_value": {"type": "integer", "description": "interval 类型的间隔数值，默认 1"},
                    "interval_unit": {
                        "type": "string",
                        "enum": ["minutes", "hours", "days"],
                        "description": "interval 类型的间隔单位，默认 minutes",
                    },
                    "time": {"type": "string", "description": "daily/weekly 的执行时间，格式 HH:MM，如 09:30"},
                    "weekdays": {
                        "type": "string",
                        "description": "weekly 的执行星期，逗号分隔，0=周一 ... 6=周日，如 '0,1,2,3,4' 表示工作日",
                    },
                    "datetime_str": {"type": "string", "description": "once 的执行时间，格式 yyyy-MM-dd HH:mm"},
                    "model": {
                        "type": "string",
                        "description": "执行模型：留空或「跟随主模型」= 跟随主模型；也可填模型名或模型 id（如 ali）",
                    },
                    "enabled": {"type": "boolean", "description": "是否立即启用，默认 true"},
                    "max_rounds": {"type": "integer", "description": "工具调用循环最大轮数，默认 12"},
                },
                "required": ["name", "prompt", "schedule_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_automations",
            "description": "列出当前所有定时任务（含周期描述、启用状态、下次执行时间）。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_automation",
            "description": "删除一个定时任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "要删除的任务 id 或名称",
                    },
                },
                "required": ["task"],
            },
        },
    },
]

_UNIT_CN = {"minutes": "分钟", "hours": "小时", "days": "天"}
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _scheduler():
    """获取活跃调度器单例（插件 execute 拿不到 ChatWindow，靠它桥接）"""
    from qwen_app.scheduler import get_active_scheduler
    return get_active_scheduler()


def _resolve_model_id(model):
    """把用户给的 model（名称/ id/ 空/「跟随主模型」）解析为注册表模型 id。
    返回 (model_id, 显示名)。未指定返回 ("", "跟随主模型")；找不到返回 (None, err)。
    """
    model = (model or "").strip()
    if not model or model == "跟随主模型":
        return "", "跟随主模型"
    from qwen_app.config import load_models
    models, _ = load_models()
    for m in models:
        if m.get("id") == model or m.get("name") == model or m.get("model_id") == model:
            return m.get("id", ""), f"{m.get('name', '')}({m.get('model_id', '')})"
    return None, f"未找到模型「{model}」"


def _build_schedule(args):
    """根据 schedule_type 组装 scheduler 认可的 schedule 字典"""
    typ = (args.get("schedule_type") or "interval").lower()
    if typ == "interval":
        unit = (args.get("interval_unit") or "minutes").lower()
        if unit not in ("minutes", "hours", "days"):
            unit = "minutes"
        every = int(args.get("interval_value") or 1)
        if every < 1:
            every = 1
        return {"type": "interval", "every": every, "unit": unit}
    if typ == "daily":
        t = args.get("time") or "09:00"
        return {"type": "daily", "time": t}
    if typ == "weekly":
        t = args.get("time") or "09:00"
        raw = str(args.get("weekdays") or "0,1,2,3,4")
        days = []
        for p in raw.replace("，", ",").split(","):
            p = p.strip()
            if p.isdigit() and 0 <= int(p) <= 6:
                days.append(int(p))
        if not days:
            days = [0, 1, 2, 3, 4]
        return {"type": "weekly", "time": t, "weekdays": days}
    if typ == "once":
        dt = args.get("datetime_str") or ""
        return {"type": "once", "datetime": dt}
    # 兜底：默认 interval
    return {"type": "interval", "every": 1, "unit": "minutes"}


def _create(args):
    sch = _scheduler()
    if not sch:
        return "错误：调度器未初始化，无法创建定时任务。"
    name = (args.get("name") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    if not name or not prompt:
        return "错误：任务名称和提示词都不能为空。"
    model_id, label = _resolve_model_id(args.get("model"))
    if model_id is None:
        return f"错误：{label}"
    schedule = _build_schedule(args)
    auto = sch.add_automation(
        name=name,
        prompt=prompt,
        schedule=schedule,
        enabled=bool(args.get("enabled", True)),
        max_rounds=int(args.get("max_rounds") or 12),
        model_id=model_id,
    )
    from qwen_app.scheduler import describe_schedule
    return (
        f"已创建定时任务「{name}」（id={auto['id']}）。\n"
        f"- 执行频率: {describe_schedule(schedule)}\n"
        f"- 执行模型: {label}\n"
        f"- 状态: {'已启用' if args.get('enabled', True) else '已禁用'}"
    )


def _list(args):
    sch = _scheduler()
    if not sch:
        return "错误：调度器未初始化。"
    from qwen_app.scheduler import describe_schedule, next_run_time
    from datetime import datetime
    autos = sch.automations
    if not autos:
        return "当前还没有任何定时任务。"
    lines = [f"共 {len(autos)} 个定时任务："]
    now = datetime.now()
    for a in autos:
        enabled = a.get("enabled", True)
        stat = "启用" if enabled else "禁用"
        nxt = next_run_time(a, now)
        nxt_txt = nxt.strftime("%m-%d %H:%M") if nxt else "—"
        lines.append(
            f"- [{a.get('id')}] {a.get('name')} | {describe_schedule(a.get('schedule', {}))} "
            f"| {stat} | 下次:{nxt_txt}"
        )
    return "\n".join(lines)


def _delete(args):
    sch = _scheduler()
    if not sch:
        return "错误：调度器未初始化。"
    task = (args.get("task") or "").strip()
    if not task:
        return "错误：请提供要删除的任务 id 或名称。"
    target = next(
        (a for a in sch.automations
         if a.get("id") == task or a.get("name") == task), None)
    if not target:
        return f"未找到任务「{task}」。可用 list_automations 查看任务 id/名称。"
    sch.delete_automation(target["id"])
    return f"已删除定时任务「{target.get('name')}」（id={target.get('id')}）。"


_HANDLERS = {
    "create_automation": _create,
    "list_automations": _list,
    "delete_automation": _delete,
}


def execute(tool_name, arguments):
    if not isinstance(arguments, dict):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except Exception:
            arguments = {}
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return f"未知工具: {tool_name}"
    try:
        return handler(arguments)
    except Exception as e:
        return f"执行 {tool_name} 失败: {type(e).__name__}: {e}"