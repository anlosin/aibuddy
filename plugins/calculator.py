"""计算器插件 — 支持四则运算、幂运算、开方、三角函数等"""
import math

PLUGIN_INFO = {
    "name": "calculator",
    "description": "数学计算工具，支持四则运算、幂运算、开方、三角函数等",
    "version": "1.0",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行数学计算，支持四则运算、幂运算、开方、三角函数等。如 '2+3*4', 'sqrt(16)', 'sin(pi/2)'",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的数学表达式"
                    }
                },
                "required": ["expression"]
            }
        }
    },
]


def execute(name, arguments):
    expr = arguments.get("expression", "")
    if not expr:
        return "错误：表达式为空"
    safe_dict = {"__builtins__": {}}
    safe_dict.update(vars(math))
    try:
        result = eval(expr, safe_dict, {})
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"
