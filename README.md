# ai-workbench

一个可在内网运行的 PyQt5 桌面 AI 助手。不止于对话，还能通过插件"真正干活"——
读写文件、执行命令、操作数据库、连接 SSH 管理远程 Linux、生成 PPT/Word/Excel、做知识库检索等。
支持 OpenAI 兼容接口（DeepSeek / Qwen 等），内置思考模式、工具调用、定时自动化与代理设置。

## 功能特性

- 桌面端：PyQt5 图形界面，支持流式对话、思考过程展示。
- 模型兼容：任意 OpenAI 兼容端点（配置 `base_url` / `api_key` / `model_id`）。
- 思考模式：透传 `enable_thinking`，区分模型的"思考"与"正式回复"。
- 工具调用：模型可自主调用插件完成任务（function calling）。
- 插件体系：计算、时钟、代码审查/解释/优化、文件管理、知识库、小说写作、
  PDF、PPT、Word、Excel、Shell、SQL、SSH、翻译、天气、网页抓取/搜索、工作流等。
- 自动化：类 WorkBuddy 的定时任务，支持 interval / daily / weekly / once 四种周期，
  每次执行结果按次记录为 Markdown 日志 + 索引。
- 代理设置：仅作用于模型连接地址，不影响插件 / SSH 等其它网络连接。
- 独立模式：无需 GUI，可作为 24/7 常驻服务运行（适合内网服务器）。

## 安装

需 Python 3.10+。

```bash
pip install -r requirements.txt
```

> Linux 需先安装系统 Qt 库，例如 `sudo apt-get install -y python3-pyqt5`；
> macOS 需 `brew install qt@5` 后再安装 PyQt5。

## 配置

复制示例配置并填入你自己的信息：

```bash
cp model_config.example.json data/model_config.json
```

`data/model_config.json` 字段说明：

| 字段 | 说明 |
| --- | --- |
| `model_id` | 模型标识 |
| `api_key` | API 密钥（**请勿提交到仓库**） |
| `base_url` | OpenAI 兼容接口地址 |
| `enable_thinking` | 是否开启思考模式 |
| `enable_tools` | 是否允许模型调用工具 |
| `workspace_root` | 工作区根目录（插件文件操作范围） |
| `agent_mode` | 是否开启自主 Agent 多轮模式 |
| `max_agent_rounds` | Agent 最大轮数 |
| `proxy` | 模型连接代理地址（如 `http://127.0.0.1:7890` 或 `socks5://127.0.0.1:1080`），留空不代理 |

## 使用

启动图形界面：

```bash
python main.py
```

启动自动化常驻服务（无 GUI，每 30 秒检查到期任务）：

```bash
python -m qwen_app.scheduler_run
# 或交给系统 cron / 计划任务，每次只检查一次就退出：
python -m qwen_app.scheduler_run --once
```

## 打包为 Windows exe（免 Python 环境）

```bash
build_exe.bat
# 等价于：
#   pip install pyinstaller
#   python -m PyInstaller qwen.spec --noconfirm
```

产物：`dist/qwen/qwen.exe`。分发时打包**整个 `dist/qwen` 文件夹**（onedir 形态）。

> ⚠️ **只运行 `dist\qwen\qwen.exe`。**
> 构建过程中 PyInstaller 还会在 `build\qwen\` 里留下一个同名 `qwen.exe`，那是
> bootloader 的**半成品**，旁边没有 `_internal\`，双击必然报
> `Failed to load Python DLL ...\build\qwen\_internal\python313.dll`。
> **看报错里的路径前缀即可判定**：写着 `build\` 就是点错了文件，包本身没问题。
> `build\` 只是构建缓存（可随时整个删掉），`build_exe.bat` 现在会在构建后
> 自动删除那个半成品。

首次运行会在 exe 同级自动生成：

| 位置 | 说明 |
| --- | --- |
| `data/` | 配置、会话库、自动化任务、工作区、日志。**便携** —— 整个文件夹拷走即带走全部数据 |
| `plugins/` | 插件目录，可自由增删改；替换 `.py` 后热重载生效 |

若 exe 所在目录不可写（例如装在 `C:\Program Files\`），数据自动回退 `%APPDATA%\qwen`；
也可用环境变量 `QWEN_DATA_DIR` 显式指定数据目录。

自检打包是否**完整**（逐个真实 import 插件第三方依赖 + 加载全部插件）：

```bash
dist\qwen\qwen.exe --selftest
type dist\qwen\data\logs\selftest.log
```

> 打包要点：插件由 `importlib` **动态加载**，PyInstaller 的静态分析看不到它们内部
> 的第三方 `import`（如 `paramiko` / `pdfplumber` / `jieba`），漏一个就是「某个功能在 exe 里
> ImportError」。因此依赖清单统一维护在 `qwen_app/plugin_deps.py`，由 `qwen.spec`
> （打进包）与 `--selftest`（打包后校验）**共用同一份**，避免两边漂移。
>
> 路径也做了 frozen 适配（`qwen_app/paths.py`）：只读资源走 `_MEIPASS`，可写数据走
> exe 同级 —— 否则数据会被写进解包临时目录，进程退出即丢。

## 目录结构

- `main.py` — 入口启动器（项目根目录，导入 `qwen_app` 包）
- `start.bat` — Windows 一键启动脚本
- `build_exe.bat` / `qwen.spec` — Windows 打包脚本与 PyInstaller 配方
- `model_config.example.json` — 配置模板（复制为 `data/model_config.json`）
- `sp_*.txt` — 系统提示词模板（`sp_analyst` / `sp_developer` / `sp_general`）
- `qwen_app/` — 核心应用包
  - `chat_window.py` — 主窗口与界面逻辑
  - `worker.py` — 对话与工具调用工作线程
  - `config.py` / `tools.py` — 配置、对话持久化与默认值
  - `paths.py` — 统一路径解析（源码态 / 打包态；只读资源 vs 可写数据）
  - `plugin_deps.py` — 插件第三方依赖清单（打包与自检共用的单一事实源）
  - `plugin_manager.py` — 插件发现与分发
  - `scheduler.py` / `scheduler_run.py` — 定时自动化核心与独立运行器
  - `expert_router.py` — 专家路由（声明式专家）
  - `compressor.py` / `sanitizer.py` — 对话压缩与输入清洗
  - `experts/` — 专家声明（`*.json`）
- `plugins/` — 各功能插件（顶层包，运行时数据经 `..` 指向 `data/`）
- `scripts/` — 辅助脚本（`push.py` 推送、`migrate_conversations.py` 迁移）
- `tests/` — 测试与压测脚本（结果产物输出到 `tests/output/`）
- `data/` — 运行时数据（均被 `.gitignore` 排除，保持根目录干净）：
  `model_config.json`、`conversations/`、`automations.json`、`automation_runs.json`、
  `db_connections.json`、`automation_logs/`、`knowledge_base/`、`novels/`、
  `wf_out/`、`sub/`、`backups/` 等

## 安全说明

本项目面向内网使用。`data/model_config.json`（含密钥）、数据库连接、SSH 连接、
对话记录、自动化日志等均收纳于 `data/` 并通过 `.gitignore` 排除，不会进入版本库。
部署到公网前请自行评估网络安全与访问控制。
