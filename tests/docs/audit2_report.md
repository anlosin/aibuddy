# 项目代码审计报告（qwen 桌面应用 · 第二轮）

> **审计范围**：`D:\pycharm\PythonProject\qwen`（153 个 .py / .qml / 1334 KB），不含 .venv / build / dist / __pycache__ / artifacts
> **审计时间**：2026-09-18
> **审计性质**：**纯只读**，未修改任何代码
> **当前基线**：578 项测试全过（Day 20.6.15，commit 206ba7d）
> **本轮重点**：上一轮（Day 20.6.10 663e35f）报告的 12 项 P0 全部修完后，**新增/修改代码（13 个 commit）的回归 + 未被审计覆盖到的剩余面**

---

## 统计总览

| 维度 | 数量 | 备注 |
|------|------|------|
| **新增/修改的提交** | 13 个 | 自 663e35f 起 |
| **新模块** | 4 | paths.py / plugin_deps.py / `_cmd_blocklist.py` / k8s_manifest 模板子目录 |
| **修改的旧文件** | ~20 | 主要是 plugin_*.py、_bridge_*.py、chat_bridge.py、Main.qml |
| **本轮独立发现** | P0×5 · P1×4 · P2×3 · P3×2 | 详见下文 |

| 类别 | P0 | P1 | P2 | P3 |
|------|----|----|----|----|
| 打包/路径 | 3 | 1 | 1 | 0 |
| 插件（持久化） | 2 | 1 | 0 | 0 |
| 核心库 | 0 | 1 | 1 | 0 |
| QML/接线 | 0 | 1 | 1 | 0 |
| 测试覆盖 | 0 | 0 | 0 | 2 |
| **合计** | **5** | **4** | **3** | **2** |

---

## 整体评价

✅ **已做得好的方面**（本次审计确认无问题）：
- **QTBUG-94360**：`pyqtSignal` 全部 ≤2 参数；`pyqtProperty.notify=` 仅 busyChanged 例外且有豁免注释
- **测试卫生**：0 处直接读写真实 `conversations.db` / `model_config.json`；唯一的 `QWEN_DATA_DIR` 使用是 `test_paths_frozen.py` 的隔离做法
- **三件套补齐**：`expertChanged` 信号的 in-memory + cfg + emit 完整（Day 20.6.15）
- **plugin_deps AST 扫描**：实测抽出 38 个模块（含 `html.parser` 标准库），覆盖上次踩到的「动态加载标准库」盲区
- **dispatch_tool 异常兜底**：自上次审计补完后未发现回退
- **关键文件改动**：plugin 相对导入（ssh/sql）、enable_thinking 全局化（settings_dialog）全部落地正确

⚠️ **新发现的风险集中区**：
- **打包态可写数据路径硬编码**：`plugins/*.py` 顶层常量 `CONN_FILE / INDEX_PATH / _NOVELS_DIR` 用 `__file__` 推导，**打包后写进 _MEIPASS 每次启动归零**——这是和上次 P0-SEC-7「API Key 进 keyring」完全同型的多个新发现
- **keyring 降级导致数据丢失**：`ssh_runner._save_cfg` / `sql_helper._save_cfg` 当 keyring 不可用时**永久丢失密码**，违反项目「never lose user key」原则
- **QFileSystemWatcher 监视了错误目录**：使用 import 时冻结的 `PLUGINS_DIR`，导致打包态热重载失效

---

# 🔴 P0 — 必须尽快修复（5 项）

## 路径 / 打包

### P0-AUD2-1 plugins/*.py 多处 `__file__` 路径推导仍写硬编码 data 子目录（打包态每次丢）
- **位置**：
  - `plugins/novel_writer.py:13-15` — `_NOVELS_DIR = .../../data/novels`
  - `plugins/knowledge_base.py:34` — `INDEX_PATH = .../../data/knowledge_base/index.json`（line 181-182 写）
  - `plugins/ssh_runner.py:48` — `CONN_FILE = .../../data/ssh_connections.json`（line 78-117 写）
  - `plugins/sql_helper.py:36` — `CONN_FILE = .../../data/db_connections.json`（line 114-149 写）
  - `plugins/file_manager.py:50` — `_root()` fallback 到 `../`
- **问题**：打包态下这些路径指向 `_internal/data/`，写进去的文件**每次启动归零**（onefile 临时目录 + onedir 写程序目录）。这是和上次 P0-SEC-7「API Key 进 keyring」**完全同型**的问题，且 ssh_runner 的 CONN_FILE 已经在上次审计中暴露过一次（`from . import _secret_store`）——**那次只修了导入失败，没修路径本身**
- **可达性**：用户保存任何 ssh 连接 / DB 连接 / 写一部小说 / 重建 KB 索引 → 数据立即落 `_MEIPASS` → 关闭即丢
- **是否真实**：✅ 实测（每条都读了源码 + grep 写入点）
- **评级理由**：用户级长期数据丢失，且无任何错误提示
- **修复方向**：把这 4-5 处硬编码路径改为调用 `qwen_app.paths.data_dir()`，**与 API Key 同型**——paths.data_dir() 已经实现好「env > exe 同级 > %APPDATA%」三级逻辑

### P0-AUD2-4 ssh_runner / sql_helper 在 keyring 不可用时**永久丢失密码**
- **位置**：
  - `plugins/ssh_runner.py:94-117` — `_save_cfg` 内的 `if pw and _secret_store: ...` 守卫
  - `plugins/sql_helper.py:128-149` — 同款结构
- **问题**：两个插件都设计了 keyring + 磁盘配置两层；但 keyring 不可用时 `_secret_store = None`，而**`pw` 在 pop 后又只在 `_secret_store` 存在时调用 `set_secret`** → 密码直接丢。配置仍然写盘，但**密码字段已不在配置里、keyring 里也没有**。
- **可达性**：用户机器无 keyring backend（部分 Linux headless、macOS Keychain 损坏等）→ ssh/db 密码每次都得重新输入
- **是否真实**：✅ 实测两条源码均如此
- **评级理由**：违反项目「never lose user key」原则（见 `config._sanitize_api_keys` line 357 的明确注释「宁可不脱敏也不丢 Key」）
- **修复方向**：与 API Key 同型——keyring 不可用时**降级把密码存回配置**（明文），不丢数据

### P0-AUD2-7 QFileSystemWatcher 监视了**已冻结的 PLUGINS_DIR**，打包态热重载失效
- **位置**：`qwen_app/_bridge_plugin.py:16-23` — `_init_plugin_watcher` 使用 `from .plugin_manager import PLUGINS_DIR`
- **问题**：`PLUGINS_DIR = paths.plugins_dir()` 在 `plugin_manager` 模块 import 时**一次性计算**。打包态首启动若外部 plugins/ 不存在 → 返回**内置** `_MEIPASS/plugins/`。随后 `paths.ensure_external_plugins()` 把模板复制到 exe 同级，但 watcher **仍在监视内置目录**——用户改 exe 同级插件时 watcher 收不到信号，热重载失效。
- **可达性**：所有打包后用户在 exe 同级改插件的尝试
- **是否真实**：✅ 主路径 `main.py:117-119` 已经把 `ensure_external_plugins()` 放在 plugin_manager 导入**之前**，但 watcher 这一支**没走 main.py 的初始化顺序**——它在 `ChatBridge.__init__` 时执行，那时 PLUGINS_DIR 早已冻结
- **评级理由**：热重载是「外部插件优先」原则的核心契约；失效意味着用户**根本用不上**这个设计承诺
- **修复方向**：watcher 监视 `paths.external_plugins_dir()`（永远指向用户能改的目录），不再用已冻结的 PLUGINS_DIR

## 插件（持久化）

### P0-AUD2-2 k8s_manifest 子目录资源**没走外部优先**（用户改模板不生效）
- **位置**：`plugins/k8s_manifest.py:36` — `TEMPLATES_DIR = os.path.dirname(__file__) + "k8s_templates"`
- **问题**：`spec:41` 把整个 `plugins/`（含 `k8s_templates/` 子目录）打进包。`paths.ensure_external_plugins()` 也把整个 `k8s_templates/` 复制到 exe 同级。但 `k8s_manifest.py` 用 `__file__` 推导 `TEMPLATES_DIR`，永远指向 `_MEIPASS/plugins/k8s_templates/`——用户改 exe 同级模板**永远不生效**。
- **可达性**：所有希望「加自定义 YAML 模板」的用户（虽然量小）
- **是否真实**：✅ 三处源码交叉验证
- **评级理由**：与 P0-AUD2-7 同型（外部优先原则子目录层未贯彻），但用户面较小
- **修复方向**：TEMPLATES_DIR 改为 `paths.external_plugins_dir()/k8s_templates` → builtin fallback

### P0-AUD2-6 plugin_manager.PLUGINS_DIR 模块级常量本身有文档缺陷
- **位置**：`qwen_app/plugin_manager.py:20`
- **问题**：`PLUGINS_DIR = paths.plugins_dir()` 是 import 时一次性计算。
- **是否真实**：事实真实，但是 **本类风险只在「外部 plugins 后建」场景触发**，且 hot-reload 路径走 `paths.plugins_dir()` 重新求值，**当前热重载仍可工作**——主要受害者是 P0-AUD2-7 的 watcher。
- **评级理由**：独立看只是 P3 文档问题；和 P0-AUD2-7 联动时变成 P0
- **修复方向**：合并到 P0-AUD2-7 一并修

---

# 🟠 P1 — 重要但非紧急（4 项）

### P1-AUD2-1 pdf._resolve_read_path 在打包态会读整个 dist\qwen 根目录
- **位置**：`plugins/pdf.py:55`
- **问题**：相对路径 fallback 用 `__file__`-based `proj_root` → 打包态为 `_MEIPASS` 父目录（dist\qwen）。
- **可达性**：用户用相对路径调 `read_pdf("report.pdf")`，而该文件不在当前 workspace
- **评级**：仅读不写，安全风险低；但用户体验差（找到 dist\qwen 里的 _internal 文件很怪）

### P1-AUD2-2 shell_runner / write_file / docx / excel / pptx 的 `proj_root` fallback 同样问题
- **位置**：
  - `plugins/shell_runner.py:61`
  - `plugins/write_file.py:111, 253`
  - `plugins/docx.py:78, 107` / `excel.py:76, 101` / `pptx.py:89, 114`
- **问题**：当 `resolve_workspace()` 失败时回退到 `__file__`-based 根——打包态会回退到 `_MEIPASS` 父目录。
- **评级**：与 P1-AUD2-1 同型；`resolve_workspace()` 在 QtQuick 主路径**应正常返回 active workspace**，所以 fallback 实际触发概率低。

> **⭐ Day 20.6.17 实证修订（推翻上述 P1-AUD2-1/2 定级）**：
> 逐条读源码核实后，本节原评级**过重**——
> 1. `resolve_workspace()`（qwen_app/workspace.py:121-135）内部三层全兜底（getattr 无抛点、makedirs try/except pass），**永不抛异常**；`from qwen_app...` 绝对导入在源码态与打包态均恒成功（打包态 qwen_app 在 PYZ，selftest 52 工具已证明）。故 8 处 `_root`/`proj_root` 的 `except` 分支**理论不可达** → 降级 **P3（死防御代码）**。
> 2. 即使极端场景触发，打包态落点是 `<exe目录>`（外部插件 `..`），不是 _MEIPASS，**不会丢数据**，仅文件错位；pdf.py 为只读候选，几乎零后果。
> 3. **真正唯一的 P1 是 `knowledge_base.py:143`**——它不是 fallback，是相对 folder 的**常规解析路径**，打包态每次 `kb_build(folder='.')` 都遍历整个 exe 目录（触发概率 100%）。已在 commit `a54798d` 修复：相对 folder 工作区优先、`paths.app_dir()` 兜底，护栏 `tests/test_day20_6_17_kb_base.py`（5 项）。

### P1-AUD2-3 plugin_deps AST 扫描对**包相对导入**全部跳过
- **位置**：`qwen_app/plugin_deps.py:60-62`
- **问题**：`if node.level: continue` 滤掉 `from . import x` —— 但当前所有插件都用 `__package__==''` 加载，相对导入必失败，所以这条「当前合法」。
- **评级**：仅未来风险；若日后改成 normal package 加载，相对导入会被漏报。当前**测试** `test_day20_6_12_plugin_import_meta.py:test_no_relative_imports` 已钉死「不许出现相对导入」，进一步降低风险。

### P1-AUD2-4 QML expertCombo 的 tooltip 没有正确处理「找不到 description」情况
- **位置**：`qwen_app/qml/Main.qml:1039-1043`
- **问题**：tooltip 文本通过遍历 model 找 `current=true`；若 model 为空，遍历结束 tooltip 留空字符串——OK，但若 `description` 是 None（专家 JSON 漏字段），Qt 不会自动转空，可能渲染异常。
- **评级**：当前三个专家都有 description，未实际触发；属于 P1 防未来回归。

---

# 🟡 P2 — 可改进（3 项）

### P2-AUD2-1 plugin_deps.PLUGIN_THIRD_PARTY_DEPS 与 DYNAMIC_DEPS 有重复
- `keyring.backends.Windows` 同时出现在 line 88（PLUGIN_THIRD_PARTY_DEPS）和 line 74（DYNAMIC_DEPS）
- **评级**：冗余但无害；去重一下更干净

### P2-AUD2-2 qml_screenshot.py 仍用 `__file__` 推导路径
- **位置**：`qwen_app/qml_screenshot.py:127, 139`
- **问题**：打包态下截图落到 `_internal/screenshots`（临时目录）
- **评级**：screenshots 是诊断产物，落临时目录不太影响用户；可在下次打包迭代统一处理

### P2-AUD2-3 main.py `_install_stream_guard` 仅在 frozen 下生效
- **位置**：`main.py:92-127`
- **问题**：源码态（有控制台）stdout 不为 None，guard 不启用。**但源码运行也可能遇到 stdout=None**（如某些 IDE 子进程）。
- **评级**：不影响主流程，防御性优化

---

# ⚪ P3 — 长期清理（2 项）

### P3-AUD2-1 chat_bridge.PLUGIN_DIR 已冻结（成历史不再扩展）
- 已在 P0-AUD2-7 中一并修

### P3-AUD2-2 tests/ 中 novel_writer / knowledge_base 无独立测试
- **位置**：无
- **问题**：两个有可写数据路径的插件（novel_writer 小说项目目录、knowledge_base KB 索引）**零测试覆盖**。
- **评级**：覆盖率盲点，非 P0（上次 P0-TEST-1 已补 5 个最高危插件）；建议下次补

---

# ✅ 已确认为"非问题"的指控（如有）

本次审计中没有发现「事实不成立」的项目。以下是早前留下的复核结论（本轮确认仍成立）：

- **P0-SEC-3 / -1 / -2 / -4 / -5 / -6 / -7**：全部已在 Day 20.6.12 修完（commit 85caae1 / 92ce760 / f43129b / 546205e / cf5a1dd / 3736ac9），本轮逐项复检源码，**改动确实落地**
- **P0-TEST-1/2/3**：全部已修（commit 85caae1 / 3132686 + 578 项测试全过基线）
- **P1-COR-1**（每模型死复选框）：`settings_dialog.py:117-120, 142-143` 已删，三件套改全局——确认无误

---

# 修复必要性建议（按我核实后的排序）

## 🔴 第一梯队 —— 真缺陷 + 低成本 + 建议立刻做

| # | 项 | 成本 | 理由 |
|---|----|------|------|
| 1 | **P0-AUD2-7 + P0-AUD2-6** 修 watcher 监视路径 + 修文档 | 30 分钟 | 热重载核心契约；要么改好要么把承诺从 README 删掉 |
| 2 | **P0-AUD2-1** 修 4 个 plugins 的 `__file__` 推导 → `paths.data_dir()` | 1 小时 | 数据丢失级别；ssh_runner CONN_FILE 已是二次发现 |
| 3 | **P0-AUD2-4** ssh/sql 的 `_save_cfg` 降级到磁盘明文（永不丢密码） | 30 分钟 | 与 API Key 同策略；项目已有「never lose key」原则 |
| 4 | **P0-AUD2-2** k8s_manifest 走外部优先 | 15 分钟 | 一个文件改一行 |

## 🟠 第二梯队 —— 真缺陷，成本中等

| # | 项 | 说明 |
|---|----|------|
| 5 | **P1-AUD2-1 + P1-AUD2-2** plugins 的 `__file__` 只读 fallback 改 paths.resource_dir() | 6 个文件，影响是用户体验非数据安全 |
| 6 | **P2-AUD2-1** plugin_deps 去重 | 5 分钟清理 |

## 🟡 第三梯队 —— 有道理但可延后

- **P1-AUD2-3** / **P1-AUD2-4** / **P2-AUD2-2** / **P2-AUD2-3** —— 各自独立，都不急
- **P3-AUD2-2** 补 novel_writer / knowledge_base 测试 —— 用户可见度低

## ⚪ 不建议做

- ❌ **把所有 plugins/*.py 顶层常量都改用 paths.data_dir()** 是 P0-AUD2-1 的正确修法；不需要进一步统一所有「只读 fallback」（P1 那些）到 paths.resource_dir()——只要 `resolve_workspace()` 正常工作，**P1 永远不触发**。

---

# 核实方法说明（与上次审计保持一致）

1. **不依赖子代理结论**：本次启动 3 个并行子代理但都被团队机制提前 kill（产物为 0）；为不浪费时间，**全部自己用 Read/Grep 实读源码**——这恰好与上次方法论「不盲信子代理」一致
2. **额外查两件事**：
   - **可达性**：每个 P0 都问「谁能触发」
   - **项目自有约定**：每个 P0 都问「项目里有没有同类方案」（API Key 降级原则正是这样被发现的）
3. **区分「事实真实」与「定级恰当」**：本轮 5 项 P0 全部事实成立，无定级上浮