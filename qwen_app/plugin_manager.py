"""插件管理器 — 扫描/加载/分派插件 + 版本管理"""
import os
import sys
import re
import importlib.util
import json
import hashlib
import copy


# plugin_manager.py 位于 qwen_app/ 内，plugins/ 在项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PLUGINS_DIR = os.path.join(PROJECT_ROOT, "plugins")

# ── 版本号比较 ──

def parse_version(version_str):
    """将 '1.2.3' 解析为 (1, 2, 3)，非标准格式返回 (0, 0, 0)"""
    try:
        parts = re.split(r"[.-]", str(version_str))
        return tuple(int(p) for p in parts if p.isdigit())
    except Exception:
        return (0, 0, 0)


def compare_versions(v1, v2):
    """
    比较两个版本号，返回:
      1  → v1 > v2 (v1 更新)
      -1 → v1 < v2
      0  → 相等
    """
    p1 = parse_version(v1)
    p2 = parse_version(v2)
    max_len = max(len(p1), len(p2))
    p1 = p1 + (0,) * (max_len - len(p1))
    p2 = p2 + (0,) * (max_len - len(p2))
    if p1 > p2:
        return 1
    if p1 < p2:
        return -1
    return 0


# ── 插件元信息 ──

def get_plugin_meta(plugin_dir=None):
    """扫描目录，返回每个文件的元信息（不加载模块），用于快速预览"""
    if plugin_dir is None:
        plugin_dir = PLUGINS_DIR
    metas = {}
    if not os.path.isdir(plugin_dir):
        return metas
    for fname in sorted(os.listdir(plugin_dir)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        pname = fname[:-3]
        full = os.path.join(plugin_dir, fname)
        stat = os.stat(full)
        size_kb = round(stat.st_size / 1024, 1)
        # 尝试提取 PLUGIN_INFO 而不完整加载
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            # 用简单的正则提取版本号和描述
            ver_match = re.search(r'"version"\s*:\s*"([^"]+)"', content)
            desc_match = re.search(r'"description"\s*:\s*"([^"]+)"', content)
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
            metas[pname] = {
                "file": fname,
                "size_kb": size_kb,
                "mtime": stat.st_mtime,
                "version": ver_match.group(1) if ver_match else "0.0.0",
                "description": desc_match.group(1) if desc_match else "",
                "display_name": name_match.group(1) if name_match else pname,
            }
        except Exception:
            metas[pname] = {
                "file": fname,
                "size_kb": size_kb,
                "mtime": stat.st_mtime,
                "version": "0.0.0",
                "description": "",
                "display_name": pname,
            }
    return metas


def discover_plugins(plugin_dir=None, reload_modules=False):
    """扫描插件目录，返回 {plugin_name: module} 和 {plugin_name: PLUGIN_INFO}
    
    插件可定义以下属性（PLUGIN_INFO 必需，其余至少满足一项）：
    - SYSTEM_PROMPT (str) — 注入到系统提示词的技能指令
    - TOOLS (list) + execute(func) — 函数调用工具
    - 纯 Skill 只要 PLUGIN_INFO + SYSTEM_PROMPT 即可
    
    reload_modules=True 时强制重新加载（用于热更新）"""
    if plugin_dir is None:
        plugin_dir = PLUGINS_DIR
    plugins = {}
    infos = {}
    if not os.path.isdir(plugin_dir):
        return plugins, infos
    for fname in sorted(os.listdir(plugin_dir)):
        if fname.endswith(".py") and not fname.startswith("_"):
            pname = fname[:-3]
            full = os.path.join(plugin_dir, fname)
            mod_name = f"plugin_{pname}"
            try:
                if reload_modules and mod_name in sys.modules:
                    del sys.modules[mod_name]
                spec = importlib.util.spec_from_file_location(mod_name, full)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if not hasattr(mod, "PLUGIN_INFO"):
                    continue
                has_tool = hasattr(mod, "TOOLS") and hasattr(mod, "execute")
                has_skill = hasattr(mod, "SYSTEM_PROMPT")
                if has_tool or has_skill:
                    plugins[pname] = mod
                    infos[pname] = getattr(mod, "PLUGIN_INFO")
            except Exception as e:
                print(f"加载插件 {pname} 失败: {e}")
    return plugins, infos


def _normalize_schema(schema):
    """递归补全 JSON Schema，使其兼容 OpenAI/DeepSeek/Qwen 的 function calling。

    这些兼容接口严格要求所有 type=object 的节点必须携带 additionalProperties
    字段，缺失会触发 schema 校验错误（如 'additionalProperties is required'），
    导致整个工具被模型丢弃、无法调用。这里递归补上 additionalProperties=False，
    覆盖嵌套的 properties / items / anyOf 等任意深度。
    """
    if not isinstance(schema, dict):
        return schema
    schema = dict(schema)
    if schema.get("type") == "object" and "additionalProperties" not in schema:
        schema["additionalProperties"] = False
    if isinstance(schema.get("properties"), dict):
        schema["properties"] = {
            k: _normalize_schema(v) for k, v in schema["properties"].items()
        }
    if isinstance(schema.get("items"), dict):
        schema["items"] = _normalize_schema(schema["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(schema.get(key), list):
            schema[key] = [_normalize_schema(s) for s in schema[key]]
    return schema


def get_enabled_tools(plugins, enabled_names):
    """收集已启用插件中所有工具定义，并规范化 schema 以兼容模型 function calling

    通过 _normalize_schema 递归为所有 object 节点补上 additionalProperties=False，
    否则 DeepSeek/Qwen 等兼容接口会因 schema 校验失败而丢弃工具，导致模型无法调用。
    """
    all_tools = []
    for name in enabled_names:
        mod = plugins.get(name)
        if mod:
            for t in getattr(mod, "TOOLS", []):
                tool = copy.deepcopy(t)
                fn = tool.get("function", {})
                if isinstance(fn.get("parameters"), dict):
                    fn["parameters"] = _normalize_schema(fn["parameters"])
                all_tools.append(tool)
    return all_tools


def dispatch_tool(plugins, enabled_names, tool_name, arguments):
    """将工具调用分派到对应的已启用插件"""
    for name in enabled_names:
        mod = plugins.get(name)
        if mod:
            own_names = [t["function"]["name"] for t in getattr(mod, "TOOLS", [])]
            if tool_name in own_names:
                return getattr(mod, "execute")(tool_name, arguments)
    return f"未知工具: {tool_name}"


def get_system_prompts(plugins, enabled_names):
    """收集已启用插件中的 SYSTEM_PROMPT，返回合并后的提示词字符串"""
    parts = []
    for name in enabled_names:
        mod = plugins.get(name)
        if mod and hasattr(mod, "SYSTEM_PROMPT"):
            sp = getattr(mod, "SYSTEM_PROMPT", "")
            if sp.strip():
                parts.append(sp.strip())
    return "\n\n".join(parts)
