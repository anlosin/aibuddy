"""破坏性命令黑名单 —— shell_runner 与 ssh_runner 共用（单一事实来源）

背景（Day 20.6.12，来源 tests/artifacts/audit_verification.md P0-SEC-1/5）：
  此前 ssh_runner.py 自带一份 17 条的 BLOCKED_PATTERNS，注释却写着
  「与 shell_runner 保持一致」—— 实际缺 shell_runner Day 18 (H7) 的全部加固
  （python -c / cmd /c / powershell -Command / xargs / for-do-done）。
  **注释说谎比缺陷本身更危险**：后人会以为两边已对齐而不再检查。
  故把黑名单抽到本模块，两边 import 同一份，从结构上消除再次漂移的可能。

设计立场（复核结论，勿轻易推翻）：
  黑名单是「尽力而为的护栏」，**不是沙箱**。本插件的产品定位就是让 LLM 能执行
  shell 真干活，因此**不做白名单化重构** —— 白名单会让 git / pip / docker /
  kubectl 全部不可用，等于废掉产品核心能力。本模块的目标只有两个：
    ① 覆盖已知的**具体**绕过写法；② 对正常命令零误伤。
  真正的边界手段是 workspace 路径约束 + 超时 + 用户对自动化任务的知情。

误伤红线（改正则前请先跑 tests/test_day20_6_12_cmd_guard.py 与
tests/test_shell_runner_blocklist.py）：
  `ls -la` / `git status` / `pip install requests` / `python script.py` /
  `python -c "print(1)"` / `cat /etc/hosts` / `rm -rf build/` / `rm -rf ./dist`
  都必须放行 —— 前者是日常命令，后两者是工作区内正常的构建产物清理。

Day 20.6.12 相比原 shell_runner 版本补的具体漏点：
  - `rm -rf ..` / `rm -rf C:\\` （原正则只认 `/`、`~`、`$HOME` 三种目标）
  - `rm -rf .` / `rm -rf ./`   （等于清空整个工作区）
  - `dd of=/dev/sda`           （原正则写死 `dd if=.*of=`，无 if= 就绕过）
  - `: > /etc/passwd`          （原正则只拦 `> /dev/sd`）
  - `chmod 000 x`              （原正则只拦 `chmod -R 0`）
  - `chown -R root /`          （原黑名单完全没有）
  - `mv x /etc/hosts`          （原黑名单完全没有）
"""

# ── 破坏性命令黑名单：命中直接拒绝执行 ──
# 说明：run_command 保留 shell=True 以支持管道/重定向/内建命令（这是本插件
# "干活"能力的核心），因此安全边界压缩到黑名单广度。黑名单覆盖越权重命名、
# 参数变体(-rf/-fr/-r -f)、家目录/敏感绝对路径删除、Windows PowerShell/CMD
# 递归删除、远程下载执行器等，尽力收缩绕过空间。
BLOCKED_PATTERNS = [
    # ── rm 递归删除：越界目标（根 / 家目录 / 上级目录 / 盘符根）──
    r"\brm\b[^\n|&;]*?(?:-\w*[rR]+\w*|--recursive)[^\n|&;]*?[\s'\"]+"
    r"(?:/|~|\$\{?HOME\}?|[a-zA-Z]:[\\/]|\.\.(?:[\\/]|\s|$))",
    # ── rm 递归删除当前目录本身（. 或 ./）—— 等于清空整个工作区 ──
    r"\brm\b[^\n|&;]*?(?:-\w*[rR]+\w*|--recursive)[^\n|&;]*?[\s'\"]+\./?\s*(?:$|[|&;])",
    # ── Windows 递归/强制删除 ──
    r"\bRemove-Item\b[^|\n]*-(Recurse|Force|r)\b.*-Force\b",   # PowerShell 递归强制
    r"\brd\s+/[sq]\b",                                        # rd /s /q
    r"\bdel\s+/[sqf]\b",                                      # del /s /q /f
    # ── 危险单条命令 / 格式化 / 写设备 ──
    r"\bformat\s+[a-z]:",         # format C:
    r"\bmkfs",                    # mkfs*
    r"\bdd\b[^\n|&;]*\bof=\s*/dev/",            # dd 写设备（不要求先有 if=）
    r"\bdd\b[^\n|&;]*\bof=\s*[a-zA-Z]:[\\/]",   # dd 写整个盘符
    r">\s*/dev/(sd|nvme|hd|disk|mmcblk)",       # 重定向覆盖块设备
    r"(?:^|[^0-9])>\s*/(etc|bin|sbin|usr|boot|lib|var|sys|proc|root)/",  # 覆盖系统目录文件
    r"\btruncate\s+-s\s*0\s+/",   # truncate 设备/系统文件
    # ── fork bomb / 关机 / 分区 / 权限破坏 ──
    r":\(\).*\{\s*:\|:",          # fork bomb
    r"\bshutdown\b",              # shutdown
    r"\breboot\b",                # reboot
    r"\bhalt\b",                  # halt
    r"\bpoweroff\b",              # poweroff
    r"\bfdisk\b",                 # fdisk
    r"\bparted\b",                # parted
    r"\bchmod\s+(?:-\w+\s+)*0+\b",                        # chmod 000 / chmod -R 000
    r"\bchmod\s+(?:-\w+\s+)*[0-7]{3,4}\s+/(?:\s|$|[|&;])",  # chmod 777 /
    r"\b(chown|chgrp)\s+(?:-\w+\s+)*\S+\s+/(?:\s|$|[|&;])",  # chown -R root /
    r"\bmv\b[^\n|&;]*\s/(?:etc|bin|sbin|usr|boot|lib|var|sys|proc|root)/",  # mv 到系统目录
    r"\bDISM\b",                  # DISM
    r"\bmkfs\.",                  # mkfs.ext4 等
    # ── 远程下载并执行（curl|wget ... | sh/bash / xargs sh）──
    r"\b(curl|wget)\b[^|\n]*\|\s*(sh|bash|zsh|cmd|powershell)\b",
    r"\bxargs\b[^|\n]*\s(sh|bash|zsh)\b",
    # ── Day 18 (H7)：拦截嵌套执行器 + 后台并行删除 ──
    r"\bpython[0-9.]*\s+-c\b[^|\n]*\b(os\.system|subprocess|Popen|__import__|exec|eval)\b",
    r"\bpython[0-9.]*\s+-c\b[^|\n]*['\"]([^'\"]*\brm\b|.*\bshutdown\b|.*\bmkfs\b|.*\bformat\b)",
    r"\bnode\s+(-e|--eval)\b",                              # node eval
    r"\bruby\s+-e\b",                                       # ruby eval
    r"\bperl\s+-e\b",                                       # perl eval
    r"\b(powershell|pwsh)\s+(-Command|-C|-EncodedCommand|-E)\b",
    # cmd /c 与 cmd /k（都会执行任意命令）。
    # Day 20.6.12 修正：原写法 `\bcmd\s*\.?exe?\s+/c\b` 里的 `exe?` 要求字面
    # "ex"，实际**匹配不到** `cmd /c` —— 也就是说 Day 18 这条加固从未生效，
    # 只因当时的测试用例是 `cmd /c "rd /s /q C:\\Windows"`，被 `rd\s+/[sq]`
    # 另一条拦住，才显得"通过"。现改为 `cmd(?:\.exe)?\s*/[ck]`。
    r"\bcmd(?:\.exe)?\s*/[ck]\b",
    # for/do/done 并行删除（多线程 rm 一个目录）—— 模型常用于"加速"清理
    r"\bfor\b[^\n]*?\bdo\b[^\n]*?\brm\b[^\n]*?\&\s*(done|$)",
    r"\bwhile\b[^\n]*?\bdo\b[^\n]*?\brm\b",
    # xargs -P 并行执行 rm
    r"\bxargs\b[^\n]*-P[^\n]*\brm\b",
]


def matches(command):
    """返回命中的正则 pattern 字符串，未命中返回 None"""
    text = command or ""
    for p in BLOCKED_PATTERNS:
        if _search(p, text):
            return p
    return None


# 预编译缓存（首次使用时构建一次）
_COMPILED = {}


def _search(pattern, text):
    import re
    r = _COMPILED.get(pattern)
    if r is None:
        r = _COMPILED[pattern] = re.compile(pattern, re.IGNORECASE)
    return r.search(text)


def refuse_if_blocked(command):
    """命中黑名单则返回拒绝说明（str），否则返回 None

    调用方（shell_runner / ssh_runner / code_runner）应把返回值直接作为
    工具结果返回给模型，让模型知道边界并改写命令，而不是静默失败。
    """
    hit = matches(command)
    if hit:
        return ("⛔ 出于安全考虑，该命令被拒绝执行（命中破坏性操作黑名单：%s）。"
                "如需执行，请改用更安全的等价写法，或联系管理员调整策略。"
                % hit)
    return None
