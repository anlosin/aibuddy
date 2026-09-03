# 全量代码审查报告 — qwen (aibuddy)

> 审查日期：2026-09-03 ｜ 范围：main 分支工作树（含未提交改动）
> 覆盖：chat_window / session / worker / scheduler / workspace / config / plugin_manager / tools / automation_dialogs 及全部 9 个插件

## 一、高优先级问题（建议尽快修复）

### H1. 对话工作目录路径不确定 → 孤儿目录堆积 + 删除/打开错位

**位置**：`qwen_app/chat_window.py` L611-613

```python
ws_path = conv_workspace_path(self.current_conv_id or "default",
                              created_at=None)
```

**问题**：`created_at=None` 时 `conv_workspace_path` 内部用 `_ts()` 取**当前时间**拼目录名 `talk_<id>_<当前时间>`。也就是说：
- 每次发消息都会算出一个**不同的**工作目录（目录名含发送时刻）；
- 而 `session.delete_conversation` 和 `_open_storage_location` 用的是对话**真实 `created_at`**（固定值）计算的路径。

**后果**：
1. 同一对话发 N 条消息 → 产生最多 N 个孤儿目录（磁盘垃圾）；
2. 删除会话时清理的是"真实 created_at"对应的目录，实际发消息写入的是别的目录 → **删不干净**；
3. 「打开存储位置」打开的也是错误目录。

**修复建议**：发送时查当前对话元数据的真实 `created_at` 传入；若无则首次创建时落库一份，保证三处（发送/删除/打开）用同一时间戳。更简单的做法：目录名只用 `conv_id`（去掉时间戳），时间戳只用于新建对话那一刻。

### H2. `run_now` 不加 `_running` → 与定时触发可能双重执行

**位置**：`qwen_app/scheduler.py` L392-423

`run_now` 只**检查**了 `auto.get("id") in self._running`（L399），但从不把自己加入 `_running`。`_run_one`（定时触发路径）才负责 add/discard。若用户点「立即运行」的同时定时器也触发该任务，两条线程会**并发跑同一个任务**（工作目录、执行记录互相踩）。

**修复建议**：`run_now` 也用 `with self._lock: self._running.add(aid)` / `finally: discard`，与 `_run_one` 对齐。

### H3. 「立即运行」按钮可能永久卡死

**位置**：`qwen_app/automation_dialogs.py` L348-360

```python
def _worker():
    final, err = self.parent_window.scheduler.run_now(aid)
    self._run_done.emit(final, err)
threading.Thread(target=_worker, daemon=True).start()
```

`_worker` 无 try/except。若 `run_now` 在进入 `run_automation` 的 try 之前抛异常（如 `cron_workspace_path` 出错、`resolve_automation_client` 抛错），`_run_done` 信号永远不会发射 → 按钮永远禁用、状态永远显示"执行中…"，且无任何报错提示。

**修复建议**：`_worker` 包 try/except，异常时 `self._run_done.emit("", str(e))`。

## 二、中优先级问题

### M1. 知识库索引是全局单例，且 `kb_build` 可索引任意绝对路径

**位置**：`plugins/knowledge_base.py` L34、L128-129

- `INDEX_PATH` 固定在 `data/knowledge_base/index.json`，与新的"每对话独立工作目录"设计不匹配：A 对话构建的索引会覆盖 B 对话的，且重复 `kb_build` 直接覆盖旧索引（无增量）。
- `kb_build` 接受任意绝对路径（如 `C:/Users/...`），模型可将敏感目录建索引后经 `kb_search` 读出内容——本地桌面应用风险可控，但属于隐性数据外泄面。

**修复建议**：至少在索引里记录 `built_from` 并在 kb_status 提示覆盖行为（已做）；进一步可按工作目录隔离索引文件。

### M2. `kb_search` 每次查询全量重分词

**位置**：`plugins/knowledge_base.py` L227

`docs = [tokenize(c["text"]) for c in index["chunks"]]` —— 每次检索都对**全部 chunk** 重新 jieba 分词。索引到几千 chunk 时单次查询会明显变慢（分词是主要开销）。BM25 的 `df` 已持久化，但每文档词频没有。

**修复建议**：构建索引时把每个 chunk 的词频（或分词结果）一并持久化，查询时直接读。

## 三、低优先级问题

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| L1 | `worker.py` L12-13 | `START_TAGS` 含 `"\u8fea\u58eb"`（迪士）、`END_TAGS` 含 `"iever"`，疑为历史编码损坏残留的死条目，永远匹配不到真实输出 | 直接删除这两个死条目 |
| L2 | `worker.py` vs `scheduler.py` | `API_TIMEOUT=60` 与定时任务 `timeout=120` 不一致，同样"等模型响应"行为不同 | 统一为一个常量或都进 config |
| L3 | `plugins/file_manager.py` L51-58 | `_resolve` 连续调用两次 `_root()`（各做一次 realpath） | 提取一次复用 |
| L4 | `plugins/shell_runner.py` | `shell=True` + 正则黑名单，无法穷尽绕过（已知权衡） | 维持现状，文档标注即可 |
| L5 | `plugins/web_search.py` L111-128 | RelatedTopics 嵌套循环里内层 `break` 后外层继续空转（`max_results - count` 可能为 0），小边界浪费 | 外层加 `if count >= max_results: break` |
| L6 | `workspace.py` | `resolve_workspace()` 回退用 `default_base()`，而 `conv_workspace_path` 用 `resolve_base()`；legacy `workspace_root` 场景下两者可能指向不同 base | 统一走 `resolve_base()` |
| L7 | `scheduler.py` L129-137 | weekly 分支 8 次迭代后的 `return cand` 理论上可能返回过期时间（实际因 days 默认全周几乎不可达） | 死代码，可简化 |

## 四、误报修正（实测验证，非问题）

| 此前怀疑 | 验证结果 |
|----------|----------|
| `once` 任务 `datetime.fromisoformat("yyyy-MM-dd HH:mm")` 解析失败导致永不触发 | ✅ **误报**。Python 3.7+ 的 `fromisoformat` 原生支持空格分隔格式，实测 `datetime.fromisoformat('2026-09-03 14:30')` 正常解析 |
| `run_now` 同步执行会冻结 GUI | ✅ **误报**。`AutomationManagerDialog._run_now` 已在后台 `threading.Thread` 中调用，结果经信号回 GUI 线程，模式正确（但有 H3 的卡死隐患） |

## 五、其余确认无恙的模块

- **worker.py 线程模型**：协作式停止（event + wait(3000)，不用 terminate）、线程局部 workspace set/clear 配对正确。
- **config.py**：SQLite 线程局部连接 + WAL + `user_version` 存 current_id（8 位 hex 在 int 范围内），无问题。
- **plugin_manager/tools**：`_normalize_schema` 递归补 `additionalProperties=False`，深拷贝隔离，无问题。
- **web_fetch**：SSRF 防护（内网/回环/保留地址校验）完整。
- **code_runner**：独立临时目录 + rmtree 清理 + 超时处理，无问题。
- **write_file/file_manager**：核心文件写保护、`PROTECTED_DIRS` 删除保护到位。

## 六、待提交改动提醒

工作树有 3 个已修改未提交文件（上轮功能遗留，已验证）：
- `qwen_app/workspace.py`（删除会话/定时任务时自动清理工作区）
- `qwen_app/session.py`（自动清理 + `transparent_ss` 样式表修复）
- `qwen_app/scheduler.py`（`delete_automation` 调用 `delete_cron_workspace`）

## 七、修复优先级建议

1. **H1**（工作目录不一致）— 影响数据完整性，且用户已感知（"打开存储位置仍然是老的位置"）
2. **H2 + H3**（run_now 并发/卡死）— 小改动，一并修
3. L1/L2（worker 死条目 + 超时统一）— 顺手清理
4. M1/M2（知识库）— 视使用频率决定
