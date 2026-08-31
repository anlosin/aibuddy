"""自动化调度器 — 独立（无界面）运行模式

让 qwen 项目像 WorkBuddy 的「自动化」一样，在后台定时执行任务，
并**按次记录每一次执行结果**。

本脚本不依赖 PyQt5，适合在内网服务器上 24/7 常驻：
    python -m qwen_app.scheduler_run
或用系统计划任务 / systemd 定时拉起（配合 --once 单次检查后退出）。

它会：
1. 读取 model_config.json 的 API 配置，构建 OpenAI 兼容 client
2. 发现插件、加载启用列表
3. 每 30 秒检查 automations.json 中到期的任务并后台执行
4. 每次执行结果写入 automation_logs/<id>_<时间戳>.md 全文 + automation_runs.json 索引

用法：
    python -m qwen_app.scheduler_run              # 常驻，每 30s 检查
    python -m qwen_app.scheduler_run --once     # 只检查一次到期任务就退出（配合外部 cron）
    python -m qwen_app.scheduler_run --check     # 同 --once
"""
import os
import sys
import time
import argparse

# 让脚本可直接以 `python qwen_app/scheduler_run.py` 运行：
# 把项目根目录加入 sys.path，使 qwen_app 包可导入。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from datetime import datetime

import qwen_app.config as config
from qwen_app.plugin_manager import discover_plugins
import qwen_app.scheduler as scheduler


def build_scheduler():
    # 主模型跟随多模型注册表的「当前激活模型」(current_model)，
    # 与桌面应用保持一致；旧扁平配置会被自动迁移为默认模型。
    cur = config.get_current_model() or {}
    client = config.make_openai_client(
        cur.get("api_key", ""),
        cur.get("base_url", ""),
        cur.get("proxy", ""),
    )
    plugins, _ = discover_plugins()
    enabled = config.load_plugin_state()
    enabled = [p for p in enabled if p in plugins]
    sch = scheduler.Scheduler(
        client=client,
        model_id=cur.get("model_id", ""),
        plugins=plugins,
        enabled_plugins=enabled,
        enable_thinking=cur.get("enable_thinking", False),
        enable_tools=cur.get("enable_tools", True),
        max_rounds=config.load_config().get("max_agent_rounds", 12),
    )
    return sch


def main():
    parser = argparse.ArgumentParser(description="自动化任务调度器（独立模式）")
    parser.add_argument("--once", action="store_true",
                        help="只检查一次到期任务就退出（配合外部 cron/计划任务）")
    parser.add_argument("--check", action="store_true",
                        help="同 --once")
    parser.add_argument("--interval", type=int, default=30,
                        help="常驻模式下两次检查间隔秒数（默认 30）")
    args = parser.parse_args()

    sch = build_scheduler()
    n = len(sch.automations)
    print(f"[Scheduler] 已加载 {n} 个自动化任务；启用 "
          f"{sum(1 for a in sch.automations if a.get('enabled', True))} 个")

    if args.once or args.check:
        due = [a for a in sch.automations
               if scheduler.is_due(a, datetime.now())]
        print(f"[Scheduler] 本次检查到期任务：{len(due)} 个")
        sch.check_due()
        # 等待在后台线程中跑完（最多等 10 分钟）
        waited = 0
        while sch._running and waited < 600:
            time.sleep(5)
            waited += 5
        print("[Scheduler] 执行完毕，退出。")
        return

    print(f"[Scheduler] 常驻模式启动，每 {args.interval} 秒检查一次。"
          f"按 Ctrl+C 退出。\n")
    try:
        while True:
            sch.check_due()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[Scheduler] 收到中断信号，已停止。")


if __name__ == "__main__":
    main()
