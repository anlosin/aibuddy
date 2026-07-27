"""小说创作插件 — 提供结构化的小说创作辅助工具与专业写作系统提示词"""
import os
import json
from datetime import datetime

PLUGIN_INFO = {
    "name": "小说创作助手",
    "description": "专业小说创作辅助工具，支持项目管理、角色设定、世界观构建、大纲生成、章节保存等全流程创作功能",
    "version": "1.0.0",
}

# ── 小说项目根目录 ──
_NOVELS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "novels"
)

# ── 系统提示词：引导 AI 成为专业小说家 ──
SYSTEM_PROMPT = """你是一位荣获多项文学奖项的职业小说家，精通长篇/中篇/短篇小说创作。请始终遵循以下创作准则：

## 叙事原则
1. **展示而非告知**（Show, don't tell）—— 用场景、动作、对话来呈现，而非直接陈述
2. **感官沉浸** —— 调动视觉、听觉、嗅觉、触觉，让读者身临其境
3. **节奏控制** —— 张弛有度，高潮与舒缓交替，避免全程紧绷或平淡
4. **伏笔与回收** —— 前文埋下的线索在后文要有呼应，形成闭环

## 人物塑造
1. **立体化** —— 每个角色都有优点和缺陷，避免脸谱化
2. **动机驱动** —— 角色的行为必须有合理的内在动机，而非剧情需要
3. **成长弧** —— 主角在故事中要有可感知的内心变化和成长
4. **独特声音** —— 不同角色的说话方式要有区分度

## 结构把控
1. **开头钩子** —— 首章前三段必须抓住读者注意力
2. **三幕结构** —— 设定→冲突→解决的经典框架，或有意打破它
3. **章节节奏** —— 每章结尾留悬念或情绪余韵，驱动翻页
4. **支线交织** —— 支线与主线要有交汇点，避免脱节

## 文风要求
- 语言精练，拒绝冗余形容词堆砌
- 对话自然口语化，推动剧情或揭示性格
- 描写与叙述比例适当，避免大段纯描写
- 根据题材调整文风（奇幻重氛围、悬疑重紧张、言情重情感）

## 创作流程
当用户请求创作小说时，建议按以下流程推进：
1. 确认题材、风格、目标字数
2. 构建世界观设定（`novel_worldview` 工具）
3. 创建主要角色档案（`novel_character` 工具）
4. 生成章节大纲（`novel_outline` 工具）
5. 逐章创作并保存（`save_chapter` 工具）

每次输出章节内容时，保证 1500-3000 字的高质量正文，并在结尾附简要创作笔记。"""


# ── 工具定义 ──
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "novel_init",
            "description": "初始化一个新的小说项目，创建项目目录结构。项目将保存在 novels/ 目录下。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "小说标题"
                    },
                    "genre": {
                        "type": "string",
                        "description": "题材类型，如：奇幻、科幻、悬疑、言情、武侠、历史、都市、恐怖、轻小说等"
                    },
                    "target_words": {
                        "type": "string",
                        "description": "目标总字数，如 '10万'、'30万'。可选，默认不设限。"
                    },
                    "style": {
                        "type": "string",
                        "description": "写作风格描述，如 '轻松幽默'、'黑暗沉重'、 '诗意唯美'。可选。"
                    }
                },
                "required": ["title", "genre"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "novel_worldview",
            "description": "保存或更新小说的世界观设定。包括时代背景、地理环境、社会规则、魔法/科技体系等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "小说项目名称（即 novel_init 时使用的标题）"
                    },
                    "era": {
                        "type": "string",
                        "description": "时代背景，如 '架空 medieval'、'近未来 2045'、'当代中国'"
                    },
                    "geography": {
                        "type": "string",
                        "description": "地理环境描述：主要地点、地形、气候等"
                    },
                    "society": {
                        "type": "string",
                        "description": "社会规则：政治体制、阶层结构、文化风俗等"
                    },
                    "power_system": {
                        "type": "string",
                        "description": "力量体系：魔法系统、科技水平、武功体系等。现实题材可填 '无'。"
                    },
                    "extra": {
                        "type": "string",
                        "description": "其他补充设定"
                    }
                },
                "required": ["project", "era", "geography", "society"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "novel_character",
            "description": "创建或更新一个角色档案卡。支持主角、配角、反派等角色类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "小说项目名称"
                    },
                    "name": {
                        "type": "string",
                        "description": "角色姓名"
                    },
                    "role": {
                        "type": "string",
                        "description": "角色定位：protagonist(主角)、deuteragonist(第二主角)、antagonist(反派)、supporting(配角)、minor(次要角色)"
                    },
                    "age": {
                        "type": "string",
                        "description": "年龄或年龄范围"
                    },
                    "appearance": {
                        "type": "string",
                        "description": "外貌描述"
                    },
                    "personality": {
                        "type": "string",
                        "description": "性格特征，包括优点和缺点"
                    },
                    "background": {
                        "type": "string",
                        "description": "背景故事：出身、经历、关键事件等"
                    },
                    "motivation": {
                        "type": "string",
                        "description": "核心动机：角色最想要什么、最害怕什么"
                    },
                    "abilities": {
                        "type": "string",
                        "description": "能力/技能：战斗、智谋、特殊能力等"
                    },
                    "arc": {
                        "type": "string",
                        "description": "角色成长弧：从什么状态到什么状态的转变"
                    }
                },
                "required": ["project", "name", "role", "personality", "motivation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "novel_outline",
            "description": "生成或更新小说章节大纲。可以一次性保存完整大纲，也可以逐章添加。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "小说项目名称"
                    },
                    "action": {
                        "type": "string",
                        "description": "操作类型：'save'(保存完整大纲，覆盖原有)、'add'(添加新章节到大纲末尾)、'update'(更新指定章节)"
                    },
                    "outline": {
                        "type": "string",
                        "description": "大纲内容。save 时为完整大纲JSON字符串，add 时为单章描述，update 时为 '第X章:新描述'。建议格式：每章一行 '第N章 章节名 - 简要描述'"
                    }
                },
                "required": ["project", "action", "outline"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_chapter",
            "description": "保存一个章节的正文内容到小说项目中。支持覆盖已存在章节。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "小说项目名称"
                    },
                    "chapter_num": {
                        "type": "integer",
                        "description": "章节编号，如 1、2、3"
                    },
                    "chapter_title": {
                        "type": "string",
                        "description": "章节标题"
                    },
                    "content": {
                        "type": "string",
                        "description": "章节正文内容"
                    }
                },
                "required": ["project", "chapter_num", "chapter_title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "load_novel",
            "description": "加载小说项目的完整信息，包括世界观、角色列表、大纲和已有章节列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "小说项目名称"
                    }
                },
                "required": ["project"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_novels",
            "description": "列出所有已创建的小说项目。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
]


# ── 辅助函数 ──

def _project_dir(title):
    """根据标题生成安全的项目目录名"""
    safe = "".join(c for c in title if c.isalnum() or c in "._-")
    if not safe:
        safe = "untitled"
    return os.path.join(_NOVELS_DIR, safe)


def _ensure_project(project):
    """确保项目目录存在，返回路径。不存在则返回 None。"""
    pdir = _project_dir(project)
    if not os.path.isdir(pdir):
        return None
    return pdir


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 工具实现 ──

def _novel_init(args):
    title = args.get("title", "").strip()
    genre = args.get("genre", "").strip()
    target_words = args.get("target_words", "")
    style = args.get("style", "")

    if not title:
        return "错误: 未提供小说标题"
    if not genre:
        return "错误: 未提供题材类型"

    pdir = _project_dir(title)

    if os.path.exists(pdir):
        return f"警告: 小说项目 '{title}' 已存在于 {pdir}\n如需重新初始化请先删除原项目目录。"

    os.makedirs(os.path.join(pdir, "chapters"), exist_ok=True)

    meta = {
        "title": title,
        "genre": genre,
        "target_words": target_words,
        "style": style,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "initialized",
        "chapter_count": 0,
    }
    _write_json(os.path.join(pdir, "meta.json"), meta)
    _write_json(os.path.join(pdir, "worldview.json"), {})
    _write_json(os.path.join(pdir, "characters.json"), [])
    _write_json(os.path.join(pdir, "outline.json"), {"chapters": []})

    return (
        f"小说项目已初始化\n"
        f"  标题: {title}\n"
        f"  题材: {genre}\n"
        f"  目标字数: {target_words or '不限'}\n"
        f"  风格: {style or '默认'}\n"
        f"  路径: {os.path.abspath(pdir)}\n\n"
        f"项目结构:\n"
        f"  novels/{title}/\n"
        f"    meta.json        — 项目元信息\n"
        f"    worldview.json   — 世界观设定\n"
        f"    characters.json  — 角色档案\n"
        f"    outline.json     — 章节大纲\n"
        f"    chapters/        — 章节正文\n\n"
        f"下一步建议:\n"
        f"  1. 使用 novel_worldview 设定世界观\n"
        f"  2. 使用 novel_character 创建主要角色\n"
        f"  3. 使用 novel_outline 生成章节大纲\n"
        f"  4. 使用 save_chapter 逐章创作"
    )


def _novel_worldview(args):
    project = args.get("project", "").strip()
    if not project:
        return "错误: 未提供项目名称"

    pdir = _ensure_project(project)
    if not pdir:
        return f"错误: 小说项目 '{project}' 不存在，请先用 novel_init 初始化"

    wv = {
        "era": args.get("era", ""),
        "geography": args.get("geography", ""),
        "society": args.get("society", ""),
        "power_system": args.get("power_system", ""),
        "extra": args.get("extra", ""),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(os.path.join(pdir, "worldview.json"), wv)

    parts = [f"世界观设定已保存 — 《{project}》\n"]
    if wv["era"]:
        parts.append(f"  时代背景: {wv['era']}")
    if wv["geography"]:
        parts.append(f"  地理环境: {wv['geography']}")
    if wv["society"]:
        parts.append(f"  社会规则: {wv['society']}")
    if wv["power_system"]:
        parts.append(f"  力量体系: {wv['power_system']}")
    if wv["extra"]:
        parts.append(f"  补充设定: {wv['extra']}")
    return "\n".join(parts)


def _novel_character(args):
    project = args.get("project", "").strip()
    if not project:
        return "错误: 未提供项目名称"

    pdir = _ensure_project(project)
    if not pdir:
        return f"错误: 小说项目 '{project}' 不存在，请先用 novel_init 初始化"

    name = args.get("name", "").strip()
    if not name:
        return "错误: 未提供角色姓名"

    role_labels = {
        "protagonist": "主角",
        "deuteragonist": "第二主角",
        "antagonist": "反派",
        "supporting": "配角",
        "minor": "次要角色",
    }
    role = args.get("role", "supporting")

    char = {
        "name": name,
        "role": role,
        "role_label": role_labels.get(role, role),
        "age": args.get("age", ""),
        "appearance": args.get("appearance", ""),
        "personality": args.get("personality", ""),
        "background": args.get("background", ""),
        "motivation": args.get("motivation", ""),
        "abilities": args.get("abilities", ""),
        "arc": args.get("arc", ""),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    cpath = os.path.join(pdir, "characters.json")
    chars = _read_json(cpath, [])

    # 如果同名角色已存在则更新
    found = False
    for i, c in enumerate(chars):
        if c.get("name") == name:
            chars[i] = char
            found = True
            break
    if not found:
        chars.append(char)

    _write_json(cpath, chars)

    result = f"角色档案已{'更新' if found else '创建'} — {char['role_label']}「{name}」\n"
    for k in ["age", "appearance", "personality", "background", "motivation", "abilities", "arc"]:
        val = char.get(k, "")
        if val:
            label_map = {
                "age": "年龄", "appearance": "外貌", "personality": "性格",
                "background": "背景", "motivation": "动机", "abilities": "能力", "arc": "成长弧"
            }
            result += f"  {label_map.get(k, k)}: {val}\n"
    result += f"\n当前角色总数: {len(chars)}"
    return result.strip()


def _novel_outline(args):
    project = args.get("project", "").strip()
    if not project:
        return "错误: 未提供项目名称"

    pdir = _ensure_project(project)
    if not pdir:
        return f"错误: 小说项目 '{project}' 不存在，请先用 novel_init 初始化"

    action = args.get("action", "save")
    outline_text = args.get("outline", "")

    if not outline_text:
        return "错误: 未提供大纲内容"

    opath = os.path.join(pdir, "outline.json")

    if action == "save":
        chapters = []
        for line in outline_text.strip().split("\n"):
            line = line.strip()
            if line:
                chapters.append(line)
        _write_json(opath, {"chapters": chapters, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")})
        return f"大纲已保存 — 《{project}》共 {len(chapters)} 章\n\n" + "\n".join(f"  {c}" for c in chapters)

    elif action == "add":
        data = _read_json(opath, {"chapters": []})
        data["chapters"].append(outline_text.strip())
        data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _write_json(opath, data)
        return f"已添加新章节到大纲 — 《{project}》当前共 {len(data['chapters'])} 章"

    elif action == "update":
        # 格式: "第X章:新描述"
        if ":" not in outline_text:
            return "错误: update 操作需要格式 '第X章:新描述'"
        target, new_desc = outline_text.split(":", 1)
        target = target.strip()
        new_desc = new_desc.strip()
        data = _read_json(opath, {"chapters": []})
        updated = False
        for i, c in enumerate(data["chapters"]):
            if target in c:
                data["chapters"][i] = f"{target} {new_desc}" if not new_desc.startswith(target) else new_desc
                updated = True
                break
        if not updated:
            return f"未找到匹配 '{target}' 的章节"
        data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _write_json(opath, data)
        return f"已更新大纲章节: {target}"

    return f"错误: 未知操作 '{action}'，支持 save / add / update"


def _save_chapter(args):
    project = args.get("project", "").strip()
    if not project:
        return "错误: 未提供项目名称"

    pdir = _ensure_project(project)
    if not pdir:
        return f"错误: 小说项目 '{project}' 不存在，请先用 novel_init 初始化"

    chapter_num = args.get("chapter_num", 0)
    chapter_title = args.get("chapter_title", "").strip()
    content = args.get("content", "")

    if not chapter_title:
        return "错误: 未提供章节标题"
    if not content:
        return "错误: 未提供章节内容"

    # 安全文件名
    safe_title = "".join(c for c in chapter_title if c.isalnum() or c in "._- ")
    safe_title = safe_title.strip().replace(" ", "_") or "untitled"
    filename = f"ch{chapter_num:03d}_{safe_title}.txt"
    cpath = os.path.join(pdir, "chapters", filename)

    header = f"第{chapter_num}章 {chapter_title}\n{'=' * 40}\n\n"
    full_content = header + content

    with open(cpath, "w", encoding="utf-8") as f:
        f.write(full_content)

    word_count = len(content)

    # 更新 meta
    mpath = os.path.join(pdir, "meta.json")
    meta = _read_json(mpath, {})
    chapters_dir = os.path.join(pdir, "chapters")
    meta["chapter_count"] = len([
        f for f in os.listdir(chapters_dir) if f.endswith(".txt")
    ]) if os.path.isdir(chapters_dir) else 0
    meta["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _write_json(mpath, meta)

    return (
        f"章节已保存\n"
        f"  项目: 《{project}》\n"
        f"  章节: 第{chapter_num}章 {chapter_title}\n"
        f"  字数: {word_count}\n"
        f"  文件: {filename}\n"
        f"  路径: {os.path.abspath(cpath)}"
    )


def _load_novel(args):
    project = args.get("project", "").strip()
    if not project:
        return "错误: 未提供项目名称"

    pdir = _ensure_project(project)
    if not pdir:
        return f"错误: 小说项目 '{project}' 不存在"

    meta = _read_json(os.path.join(pdir, "meta.json"), {})
    wv = _read_json(os.path.join(pdir, "worldview.json"), {})
    chars = _read_json(os.path.join(pdir, "characters.json"), [])
    outline = _read_json(os.path.join(pdir, "outline.json"), {"chapters": []})

    parts = [f"小说项目: 《{project}》"]
    parts.append(f"  题材: {meta.get('genre', '未知')}")
    parts.append(f"  风格: {meta.get('style', '默认')}")
    parts.append(f"  创建时间: {meta.get('created', '未知')}")
    parts.append(f"  章节数: {meta.get('chapter_count', 0)}")
    parts.append("")

    if wv and any(wv.values()):
        parts.append("【世界观设定】")
        for k, v in wv.items():
            if v and k != "updated":
                labels = {"era": "时代", "geography": "地理", "society": "社会",
                          "power_system": "力量体系", "extra": "补充"}
                parts.append(f"  {labels.get(k, k)}: {v}")
        parts.append("")

    if chars:
        parts.append(f"【角色档案】({len(chars)} 人)")
        for c in chars:
            parts.append(f"  [{c.get('role_label', '?')}] {c.get('name', '?')} — {c.get('personality', '')[:30]}")
        parts.append("")

    if outline.get("chapters"):
        parts.append(f"【章节大纲】({len(outline['chapters'])} 章)")
        for ch in outline["chapters"]:
            parts.append(f"  {ch}")
        parts.append("")

    chapters_dir = os.path.join(pdir, "chapters")
    if os.path.isdir(chapters_dir):
        ch_files = sorted(f for f in os.listdir(chapters_dir) if f.endswith(".txt"))
        if ch_files:
            parts.append(f"【已写章节】({len(ch_files)} 章)")
            for f in ch_files:
                parts.append(f"  {f}")
        else:
            parts.append("【已写章节】暂无")

    return "\n".join(parts)


def _list_novels(args):
    if not os.path.isdir(_NOVELS_DIR):
        return "暂无小说项目（novels/ 目录不存在）"

    projects = []
    for name in sorted(os.listdir(_NOVELS_DIR)):
        pdir = os.path.join(_NOVELS_DIR, name)
        if not os.path.isdir(pdir):
            continue
        meta = _read_json(os.path.join(pdir, "meta.json"), {})
        if meta:
            projects.append({
                "dir": name,
                "title": meta.get("title", name),
                "genre": meta.get("genre", "?"),
                "chapters": meta.get("chapter_count", 0),
                "style": meta.get("style", ""),
            })

    if not projects:
        return "暂无小说项目"

    lines = [f"小说项目列表（共 {len(projects)} 个）\n"]
    for p in projects:
        line = f"  《{p['title']}》 — {p['genre']}"
        if p["style"]:
            line += f" | {p['style']}"
        line += f" | {p['chapters']} 章"
        lines.append(line)
    return "\n".join(lines)


# ── 分派入口 ──

def execute(name, arguments):
    dispatch = {
        "novel_init": _novel_init,
        "novel_worldview": _novel_worldview,
        "novel_character": _novel_character,
        "novel_outline": _novel_outline,
        "save_chapter": _save_chapter,
        "load_novel": _load_novel,
        "list_novels": _list_novels,
    }
    handler = dispatch.get(name)
    if handler:
        try:
            return handler(arguments)
        except Exception as e:
            return f"工具执行错误 [{name}]: {e}"
    return f"未知工具: {name}"
