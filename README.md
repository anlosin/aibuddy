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
cp model_config.example.json model_config.json
```

`model_config.json` 字段说明：

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

## 目录结构

- `main.py` — 入口启动器（项目根目录，导入 `qwen_app` 包）
- `qwen_app/` — 核心应用包
  - `chat_window.py` — 主窗口与界面逻辑
  - `worker.py` — 对话与工具调用工作线程
  - `config.py` / `tools.py` — 配置、对话持久化与默认值
  - `plugin_manager.py` — 插件发现与分发
  - `scheduler.py` / `scheduler_run.py` — 定时自动化核心与独立运行器
  - `expert_router.py` — 专家路由（声明式专家）
  - `compressor.py` / `sanitizer.py` — 对话压缩与输入清洗
  - `experts/` — 专家声明（`*.json`）
  - `worker_system/` — 线程池 / 异步任务工具
- `plugins/` — 各功能插件（顶层包，运行时数据经 `..` 指向项目根目录）
- `scripts/` — 辅助脚本（`push.py` 推送、`migrate_conversations.py` 迁移）
- `tests/` — 测试与压测脚本
- `backups/` — 备份文件（`.bak`）
- 运行时数据（均被 `.gitignore` 排除）：`model_config.json`、`conversations/`、
  `automations.json`、`automation_logs/`、`knowledge_base/`、`novels/` 等

## 安全说明

本项目面向内网使用。`model_config.json`（含密钥）、数据库连接、SSH 连接、
对话记录、自动化日志等均已通过 `.gitignore` 排除，不会进入版本库。
部署到公网前请自行评估网络安全与访问控制。
