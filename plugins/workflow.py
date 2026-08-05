"""多步工作流编排插件 — 把多个工具串成自动化流程（"多 Agent / 工作流"能力）

workflow_run(plan)：plan 是有序步骤列表，每步：
  {
    "step": "步骤ID",               # 可选，供后续步骤引用其结果
    "tool": "工具名",               # 如 run_command / kb_search / db_query / write_file
    "args": {...},                  # 传给该工具的参数
    "inputs": {"参数名": "步骤ID"}   # 可选，把前面某步的结果注入到本步参数
  }
步骤顺序执行、结果互相传递，最后返回汇总报告。

实现上复用 plugin_manager 的 dispatch_tool，与 chat_window 主循环使用完全相同的
工具调度逻辑，保证一致性。配合 worker 的自主模式（提高工具循环轮次），即可完成
复杂的多步骤自动化任务。
"""
import json

PLUGIN_INFO = {
    "name": "workflow",
    "description": "多步工作流编排：把多个工具调用串成有序自动化流程，步骤间可传递结果。",
    "version": "1.0",
}

SYSTEM_PROMPT = """你拥有工作流编排能力（workflow 插件）。

当用户要求完成一个需要多步、调用多个工具才能搞定的复杂任务时，用 workflow_run 把它拆成有序步骤：
- 每步指定 tool（工具名）与 args（参数）
- 用 inputs 把前一步的结果注入到后续步骤的参数，实现步骤间数据传递
- 步骤顺序执行，最后拿到汇总结果

这能让你自主完成多步骤自动化任务，例如：
先 kb_search 检索知识 → 再 write_file 生成脚本 → 再 run_script 执行 → 最后 report 整理结果。

建议：复杂任务先规划步骤，再一次性交给 workflow_run 执行；每步保持单一职责。
"""


def _do_run(args):
    plan = args.get("plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception as e:
            return "错误: plan 不是合法 JSON: %s" % e
    if not isinstance(plan, list) or not plan:
        return "错误: plan 必须是非空步骤列表"

    # 懒加载，避免插件发现阶段的导入副作用
    from qwen_app.plugin_manager import discover_plugins, dispatch_tool
    from qwen_app.config import load_plugin_state

    plugins, _ = discover_plugins()
    enabled = load_plugin_state()

    results = {}
    report = []
    for idx, step in enumerate(plan):
        if not isinstance(step, dict):
            return "错误: 第 %d 步格式不正确（应为对象）" % (idx + 1)
        sid = step.get("step") or ("step%d" % (idx + 1))
        tool = step.get("tool")
        if not tool:
            return "错误: 第 %d 步缺少 tool" % (idx + 1)

        # 注入前序步骤结果
        sargs = dict(step.get("args", {}) or {})
        inputs = step.get("inputs", {}) or {}
        for arg_k, src in inputs.items():
            if src in results:
                sargs[arg_k] = results[src]

        try:
            r = dispatch_tool(plugins, enabled, tool, sargs)
        except Exception as e:
            r = "步骤执行异常: %s" % e
        results[sid] = r

        snippet = (r or "")
        if len(snippet) > 800:
            snippet = snippet[:800] + "\n...[结果已截断]"
        report.append("▶ 步骤 %d [%s] 工具=%s\n%s" % (idx + 1, sid, tool, snippet))

    return "工作流执行完成，共 %d 步:\n\n%s" % (len(plan), "\n\n".join(report))


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "workflow_run",
            "description": "执行多步工作流：把多个工具调用编排成有序自动化流程，步骤间可通过 inputs 传递结果。适合复杂多步骤任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "description": "步骤列表。每步: {step:步骤ID, tool:工具名, args:{...}, inputs:{参数名:前序步骤ID}}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string", "description": "步骤ID，供后续引用"},
                                "tool": {"type": "string", "description": "要调用的工具名，如 run_command/kb_search/db_query"},
                                "args": {"type": "object", "description": "传给工具的参数"},
                                "inputs": {"type": "object", "description": "把前序步骤结果注入本步参数：{本步参数名: 前序步骤ID}"}
                            },
                            "required": ["tool"]
                        }
                    }
                },
                "required": ["plan"]
            }
        }
    },
]


def execute(name, arguments):
    if name == "workflow_run":
        return _do_run(arguments)
    return f"未知工具: {name}"
