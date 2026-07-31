"""自动化调度核心 — 类似 WorkBuddy 的「自动化」：定时执行某个 prompt，并**按次记录每次执行结果**

设计要点：
- 数据持久化到 automations.json（任务定义），每次执行的结果写入
  automation_logs/<任务id>_<时间戳>.md 全文 + automation_runs.json 索引（封顶 300 条）
- 调度支持 4 种周期：interval（每隔 N 分/时/日）、daily（每天 HH:MM）、
  weekly（每周指定星期几 HH:MM）、once（指定一次时间）
- 同一任务执行期间用 running 集合防重入，避免间隔过小导致重复跑
- run_automation 是**无界面**的 agent 循环：复用 plugin_manager 的
  get_enabled_tools / dispatch_tool，与 worker.py 的工具调用逻辑对齐
  （enable_thinking 经 extra_body 透传、tools + tool_choice=auto）
- Scheduler 本身是纯 Python 类（不依赖 Qt），GUI 用 QTimer 驱动 check_due，
  独立模式（scheduler_run.py）用阻塞循环驱动，二者共用同一套核心逻辑

使用：
- GUI 内：chat_window 创建 Scheduler，用 QTimer 每 30s 调 check_due()
- 独立模式：python scheduler_run.py（可在内网服务器用 systemd/计划任务 24/7 跑）
"""
import os
import sys
import json
import uuid
import threading
from datetime import datetime, timedelta

# 模块级导入，供 run_automation / _build_system_prompt 共用
from plugin_manager import get_enabled_tools, get_system_prompts, dispatch_tool

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATIONS_FILE = os.path.join(PROJECT_DIR, "automations.json")
RUNS_FILE = os.path.join(PROJECT_DIR, "automation_runs.json")
LOG_DIR = os.path.join(PROJECT_DIR, "automation_logs")
RUNS_INDEX_CAP = 300  # automation_runs.json 最多保留记录数


# ═════════════════════════════════════════
#  数据模型：加载 / 保存
# ═════════════════════════════════════════

def load_automations():
    """读取任务定义列表，文件不存在时返回空列表"""
    if not os.path.exists(AUTOMATIONS_FILE):
        return []
    try:
        with open(AUTOMATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_automations(automations):
    try:
        with open(AUTOMATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(automations, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Scheduler] 保存任务失败: {e}")


def load_runs_index():
    if not os.path.exists(RUNS_FILE):
        return []
    try:
        with open(RUNS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_runs_index(index):
    try:
        with open(RUNS_FILE, "w", encoding="utf-8") as f:
            json.dump(index[-RUNS_INDEX_CAP:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Scheduler] 保存运行索引失败: {e}")


# ═════════════════════════════════════════
#  调度计算：next_run_time / is_due
# ═════════════════════════════════════════

_WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _parse_clock(s):
    h, m = (s or "00:00").split(":")
    return int(h), int(m)


def next_run_time(auto, now):
    """返回该任务的下一次应执行时间（datetime）；已过期则返回 None（once 专用）"""
    s = auto.get("schedule") or {}
    typ = (s.get("type") or "interval").lower()
    last = auto.get("last_run")
    last_dt = None
    try:
        if last:
            last_dt = datetime.fromisoformat(last)
    except Exception:
        last_dt = None

    if typ == "interval":
        unit = (s.get("unit") or "minutes").lower()
        every = max(1, int(s.get("every", 1)))
        delta = {
            "minutes": timedelta(minutes=every),
            "hours": timedelta(hours=every),
            "days": timedelta(days=every),
        }.get(unit, timedelta(minutes=every))
        if last_dt is None:
            return now  # 从未执行过 → 立即到期
        nxt = last_dt + delta
        while nxt <= now:
            nxt += delta
        return nxt

    if typ == "daily":
        h, m = _parse_clock(s.get("time"))
        nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt

    if typ == "weekly":
        h, m = _parse_clock(s.get("time"))
        days = s.get("weekdays") or [0, 1, 2, 3, 4, 5, 6]
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        for _ in range(8):  # 最多向后看一周
            if cand.weekday() in days and cand > now:
                return cand
            cand += timedelta(days=1)
        return cand

    if typ == "once":
        try:
            dt = datetime.fromisoformat(s.get("datetime"))
        except Exception:
            return None
        return dt if dt >= now else None  # 已过的 once 不再触发

    return now


def is_due(auto, now):
    if not auto.get("enabled", True):
        return False
    nxt = next_run_time(auto, now)
    return nxt is not None and nxt <= now


def describe_schedule(sched):
    """人类可读的周期描述"""
    s = sched or {}
    typ = (s.get("type") or "interval").lower()
    if typ == "interval":
        unit_cn = {"minutes": "分钟", "hours": "小时", "days": "天"}.get(
            (s.get("unit") or "minutes").lower(), s.get("unit"))
        return f"每隔 {s.get('every', 1)} {unit_cn}"
    if typ == "daily":
        return f"每天 {s.get('time', '00:00')}"
    if typ == "weekly":
        ds = s.get("weekdays") or []
        names = "/".join(_WEEKDAY_NAMES[d] for d in ds)
        return f"{names} {s.get('time', '00:00')}"
    if typ == "once":
        return f"单次 {s.get('datetime', '')}"
    return "未知周期"


# ═════════════════════════════════════════
#  无界面 agent 循环（复用 plugin_manager）
# ═════════════════════════════════════════

def _build_system_prompt(plugins, enabled_plugins, enable_tools):
    from plugin_manager import get_enabled_tools, get_system_prompts
    parts = []
    sp = get_system_prompts(plugins, enabled_plugins)
    if sp:
        parts.append(sp)
    if enable_tools:
        tools = get_enabled_tools(plugins, enabled_plugins)
        names = [t["function"]["name"] for t in tools]
        if names:
            parts.append(
                "你可以调用以下工具来完成任务：\n" + ", ".join(names) +
                "\n\n规则：需要这些工具才能完成的任务必须调用工具；"
                "调用后根据结果给出最终回复；不要说你做不到——你拥有这些工具。"
                "对于复杂任务，先拆解成多步，连续调用工具直到完成。"
            )
    return "\n\n".join(parts) if parts else "你是一个能干活的智能助手。"


def run_automation(auto, client, model_id, plugins, enabled_plugins,
                    enable_thinking, enable_tools, max_rounds):
    """无界面执行一个自动化任务，返回 (final_text, tool_logs, error)

    tool_logs 为 [(工具名, 参数摘要, 结果摘要), ...]，用于写执行记录。
    """
    prompt = auto.get("prompt", "")
    tool_logs = []
    error = ""
    final = ""
    extra = {}
    if enable_thinking:
        extra["enable_thinking"] = True
    try:
        sys_p = _build_system_prompt(plugins, enabled_plugins, enable_tools)
        messages = []
        if sys_p:
            messages.append({"role": "system", "content": sys_p})
        messages.append({"role": "user", "content": prompt})

        tools = get_enabled_tools(plugins, enabled_plugins) if enable_tools else []
        for rnd in range(max_rounds + 1):
            kwargs = {
                "model": model_id,
                "messages": messages,
                "extra_body": extra,
                "stream": False,
                "timeout": 120,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            resp = client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            content = msg.content or ""
            tcs = getattr(msg, "tool_calls", None)
            if tcs:
                asst = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [],
                }
                for i, tc in enumerate(tcs):
                    tc_id = tc.id or f"call_{i}"
                    asst["tool_calls"].append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    })
                messages.append(asst)
                for i, tc in enumerate(tcs):
                    fname = tc.function.name
                    try:
                        fargs = json.loads(tc.function.arguments or "{}")
                    except Exception:
                        fargs = {}
                    result = dispatch_tool(plugins, enabled_plugins, fname, fargs)
                    tool_logs.append((fname, str(fargs)[:200], str(result)[:400]))
                    messages.append({
                        "role": "tool",
                        "content": str(result),
                        "tool_call_id": tc.id or f"call_{i}",
                    })
                continue
            else:
                final = content
                break
        if not final:
            final = "(模型未返回有效内容)"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return final, tool_logs, error


def dispatch_tool(plugins, enabled_names, tool_name, arguments):
    """复用 plugin_manager.dispatch_tool，避免重复实现"""
    from plugin_manager import dispatch_tool as _dt
    return _dt(plugins, enabled_names, tool_name, arguments)


# ═════════════════════════════════════════
#  Scheduler — 调度核心（纯 Python，无 Qt 依赖）
# ═════════════════════════════════════════

class Scheduler:
    def __init__(self, parent=None, plugins=None, enabled_plugins=None,
                 max_rounds=12, client=None, model_id=None,
                 enable_thinking=False, enable_tools=False, **ignored):
        # parent 是 ChatWindow 实例，运行时实时读取对话主模型的 client/model_id 等
        self._parent = parent
        # 无 parent 时（独立模式）直接存这些值
        self._client = client
        self._model_id = model_id
        self._enable_thinking = enable_thinking
        self._enable_tools = enable_tools
        self._plugins = plugins or []
        self._enabled_plugins = enabled_plugins or []
        self._max_rounds = max_rounds
        self.automations = load_automations()
        self._running = set()
        self._lock = threading.Lock()
        self.on_log = None  # 可选回调(name, status)，GUI 用来刷新状态栏
        os.makedirs(LOG_DIR, exist_ok=True)

    @property
    def client(self):
        return self._parent.client if self._parent else self._client

    @property
    def model_id(self):
        return self._parent.model_id if self._parent else (self._model_id or "")

    @property
    def enable_thinking(self):
        return self._parent.enable_thinking if self._parent else self._enable_thinking

    @property
    def enable_tools(self):
        return self._parent.enable_tools if self._parent else self._enable_tools

    @property
    def plugins(self):
        return self._plugins

    @property
    def enabled_plugins(self):
        return self._enabled_plugins

    @property
    def max_rounds(self):
        return self._max_rounds

    def check_due(self, now=None):
        """检查并启动所有到期任务。可在 QTimer 或阻塞循环中调用。"""
        now = now or datetime.now()
        due = [a for a in self.automations if is_due(a, now)]
        for auto in due:
            aid = auto.get("id")
            if aid in self._running:
                continue  # 防重入
            t = threading.Thread(
                target=self._run_one, args=(auto,), daemon=True)
            t.start()

    def run_now(self, auto_id):
        """立即执行指定任务（忽略周期），用于「立即运行」按钮"""
        auto = next((a for a in self.automations if a.get("id") == auto_id), None)
        if not auto:
            return None, "未找到任务"
        if auto.get("id") in self._running:
            return None, "该任务正在执行中"
        started = datetime.now()
        final, tool_logs, error = run_automation(
            auto, self.client, self.model_id, self.plugins,
            self.enabled_plugins, self.enable_thinking, self.enable_tools,
            auto.get("max_rounds", self.max_rounds))
        finished = datetime.now()
        status = "error" if error else "ok"
        self._record(auto, started, finished, status, final, tool_logs, error)
        return final, error

    def _run_one(self, auto):
        aid = auto.get("id")
        self._running.add(aid)
        try:
            started = datetime.now()
            final, tool_logs, error = run_automation(
                auto, self.client, self.model_id, self.plugins,
                self.enabled_plugins, self.enable_thinking, self.enable_tools,
                auto.get("max_rounds", self.max_rounds))
            finished = datetime.now()
            status = "error" if error else "ok"
            self._record(auto, started, finished, status, final, tool_logs, error)
            if self.on_log:
                try:
                    self.on_log(auto.get("name", aid), status)
                except Exception:
                    pass
        except Exception as e:
            auto["last_status"] = "error"
            auto["last_error"] = str(e)
            self._save()
        finally:
            self._running.discard(aid)

    def _record(self, auto, started, finished, status, final, tool_logs, error):
        aid = auto.get("id")
        auto["last_run"] = started.isoformat()
        auto["last_status"] = status
        auto["last_error"] = error
        # 写全文 md 日志
        ts = started.strftime("%Y%m%d_%H%M%S")
        md_path = os.path.join(LOG_DIR, f"{aid}_{ts}.md")
        try:
            lines = []
            lines.append(f"# 自动化任务执行记录\n")
            lines.append(f"- **任务**: {auto.get('name', aid)}")
            lines.append(f"- **时间**: {started.strftime('%Y-%m-%d %H:%M:%S')} → "
                        f"{finished.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"- **状态**: {status}")
            lines.append(f"- **耗时**: {round((finished - started).total_seconds(), 1)} 秒\n")
            lines.append("## 任务提示\n")
            lines.append(auto.get("prompt", "") + "\n")
            lines.append(f"## 工具调用（{len(tool_logs)} 次）\n")
            for i, (fname, fargs, fresult) in enumerate(tool_logs, 1):
                lines.append(f"### {i}. {fname}")
                lines.append(f"- 参数: `{fargs}`")
                lines.append(f"- 结果: {fresult}\n")
            lines.append("## 最终回复\n")
            lines.append(final + "\n")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            print(f"[Scheduler] 写日志失败: {e}")
            md_path = ""
        # 更新运行索引
        index = load_runs_index()
        index.append({
            "run_id": f"{aid}_{ts}",
            "automation_id": aid,
            "name": auto.get("name", aid),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "status": status,
            "error": error,
            "output_chars": len(final),
            "tool_calls": len(tool_logs),
            "log_file": os.path.basename(md_path) if md_path else "",
        })
        save_runs_index(index)
        self._save()

    def _save(self):
        save_automations(self.automations)

    # ── 任务增删改（供 UI 调用）──
    def add_automation(self, name, prompt, schedule, enabled=True, max_rounds=None):
        auto = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "prompt": prompt,
            "schedule": schedule,
            "enabled": enabled,
            "max_rounds": max_rounds if max_rounds else self.max_rounds,
            "created_at": datetime.now().isoformat(),
            "last_run": None,
            "last_status": None,
            "last_error": "",
        }
        self.automations.append(auto)
        self._save()
        return auto

    def update_automation(self, auto_id, **fields):
        auto = next((a for a in self.automations if a.get("id") == auto_id), None)
        if not auto:
            return False
        for k, v in fields.items():
            auto[k] = v
        self._save()
        return True

    def delete_automation(self, auto_id):
        self.automations = [a for a in self.automations if a.get("id") != auto_id]
        self._save()

    def set_enabled(self, auto_id, enabled):
        auto = next((a for a in self.automations if a.get("id") == auto_id), None)
        if auto:
            auto["enabled"] = enabled
            self._save()

    def list_runs(self, auto_id=None):
        index = load_runs_index()
        if auto_id:
            index = [r for r in index if r.get("automation_id") == auto_id]
        return sorted(index, key=lambda r: r.get("started_at", ""), reverse=True)

    def read_log(self, run_id):
        for r in load_runs_index():
            if r.get("run_id") == run_id and r.get("log_file"):
                p = os.path.join(LOG_DIR, r["log_file"])
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        return f.read()
        return None
