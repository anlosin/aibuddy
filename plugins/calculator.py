"""计算器插件 — 支持四则运算、幂运算、开方、三角函数等"""
import ast
import math
import operator

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


# ── 安全计算：弃用 eval，改用 AST 白名单解析 ──
# 只放行：数字常量、四则/幂/取模/整除、一元正负、括号、math 函数调用。
# 属性访问(ast.Attribute)、下标(ast.Subscript)、字符串等一律拒绝，杜绝沙盒逃逸。
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
# math 中的可调用函数与数值常量（pi、e、tau、inf、nan）
_ALLOWED_FUNCS = {
    k: v for k, v in vars(math).items()
    if callable(v) or isinstance(v, (int, float))
}


def _eval_node(node):
    """递归求值单个 AST 节点，遇到非白名单结构抛 ValueError"""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Name) and node.id in _ALLOWED_FUNCS:
        return _ALLOWED_FUNCS[node.id]
    if isinstance(node, ast.Call):
        func = _eval_node(node.func)
        if not callable(func):
            raise ValueError("不可调用的对象")
        return func(*[_eval_node(a) for a in node.args])
    raise ValueError("表达式包含不允许的语法")


def execute(name, arguments):
    expr = arguments.get("expression", "")
    if not expr:
        return "错误：表达式为空"
    try:
        tree = ast.parse(expr, mode="eval")
        return str(_eval_node(tree))
    except Exception as e:
        return f"计算错误: {e}"
