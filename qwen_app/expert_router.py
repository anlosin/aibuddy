"""专家路由 — 加载 / 匹配 / 构建专家 system prompt

对标 WorkBuddy 的专家机制（声明式定义 + 工具调度 + 记忆）：
- experts/*.json 声明式定义专家（身份约束 / 插件子集 / 工具开关 / 思考开关）
- 用户输入前缀（如 /dev）或 UI 下拉框切换专家
- 切换后，该专家的 system_prompt 与插件子集注入到对话，其余回退全局

与现有插件系统的关系：专家只是「把插件 SYSTEM_PROMPT + TOOLS 按需组合并叠加身份约束」
的一层薄封装，不改动 plugin_manager / worker 的核心逻辑。
"""
import os
import json


def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


EXPERTS_DIR = os.path.join(_project_dir(), "experts")

# 工具调用引导文案（与 chat_window 原 _build_tool_system_prompt 保持一致）
_TOOL_GUIDE = (
    "你可以调用以下工具来完成用户请求：\n{names}\n\n"
    "重要规则：\n"
    "1. 当用户询问需要这些工具才能完成的任务时，你必须调用工具，而不是说你做不到。\n"
    "2. 调用工具后，根据返回结果给用户一个友好的回复。\n"
    "3. 不要在回复中说「我无法访问文件」「我没有这个能力」之类的话——你拥有这些工具。"
)

# 自主 / Agent 模式引导（原 chat_window 的 agent_part 文案）
_AGENT_GUIDE = (
    "【自主模式已开启】对于复杂任务，你应该先拆解成多个子步骤，"
    "主动连续调用多个工具直到任务完成，再把结果整理成清晰回复交给用户。"
    "可优先使用 workflow_run 把多步任务编排成一个工作流。"
    "遇到需要检索内部资料的情况优先用知识库(kb_search)，而非联网搜索。"
)


def load_experts():
    """扫描 experts/ 目录，返回 {expert_id: dict}（按文件名排序）"""
    experts = {}
    if not os.path.isdir(EXPERTS_DIR):
        return experts
    for fname in sorted(os.listdir(EXPERTS_DIR)):
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        full = os.path.join(EXPERTS_DIR, fname)
        try:
            with open(full, "r", encoding="utf-8") as f:
                data = json.load(f)
            eid = data.get("id") or fname[:-3]
            data["id"] = eid
            # 字段默认值
            data.setdefault("name", eid)
            data.setdefault("description", "")
            data.setdefault("trigger", "")
            data.setdefault("system_prompt", "")
            data.setdefault("enabled_plugins", None)   # None = 沿用全局
            data.setdefault("enable_tools", None)       # None = 沿用全局
            data.setdefault("enable_thinking", None)    # None = 沿用全局
            data.setdefault("max_rounds", None)         # None = 沿用全局轮次
            experts[eid] = data
        except Exception as e:
            print(f"加载专家 {fname} 失败: {e}")
    return experts


def match_expert(text, experts):
    """按前缀（trigger）匹配专家。

    返回 (expert_id, stripped_text)。无匹配时返回 (None, text)。
    stripped_text 为去掉前缀后的用户输入，用于继续发送给模型。
    """
    text = text.strip()
    for eid, e in experts.items():
        trigger = (e.get("trigger") or "").strip()
        if trigger and text.startswith(trigger):
            return eid, text[len(trigger):].strip()
    return None, text


def resolve_settings(expert, global_enabled_plugins, global_enable_tools,
                     global_enable_thinking, global_agent_mode, global_max_rounds):
    """根据专家定义解析实际生效的插件 / 工具 / 思考 / 轮次设置。

    专家字段为 None 时回退到全局设置；否则使用专家指定值。
    """
    ep = expert.get("enabled_plugins")
    if ep is None:
        ep = global_enabled_plugins

    use_tools = global_enable_tools
    if expert.get("enable_tools") is not None:
        use_tools = expert["enable_tools"]

    use_thinking = global_enable_thinking
    if expert.get("enable_thinking") is not None:
        use_thinking = expert["enable_thinking"]

    if expert.get("max_rounds"):
        rounds = expert["max_rounds"]
    elif use_tools and global_agent_mode:
        rounds = global_max_rounds
    else:
        rounds = 5

    return ep, use_tools, use_thinking, rounds


def build_system_prompt(expert, plugins, enabled_plugins, enable_tools,
                        agent_mode=False):
    """构建专家的 system prompt：专家指令 + 插件技能 + 工具引导（+ 自主模式引导）

    - 专家 system_prompt 优先注入
    - 再叠加专家（或全局）启用插件的 SYSTEM_PROMPT 技能
    - 启用了工具则追加工具调用引导；开启 agent_mode 则追加自主编排引导
    """
    from .plugin_manager import get_system_prompts, get_enabled_tools

    parts = []

    sp = (expert.get("system_prompt") or "").strip()
    if sp:
        parts.append(sp)

    ep = enabled_plugins or []
    skill_prompts = get_system_prompts(plugins, ep)
    if skill_prompts:
        parts.append(skill_prompts)

    if enable_tools and ep:
        tool_list = get_enabled_tools(plugins, ep)
        tool_names = [t["function"]["name"] for t in tool_list]
        if tool_names:
            parts.append(_TOOL_GUIDE.format(names=", ".join(tool_names)))

    if agent_mode and enable_tools:
        parts.append(_AGENT_GUIDE)

    return "\n\n".join(parts) if parts else None
