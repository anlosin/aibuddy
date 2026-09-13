"""Day 19 (新加): _safe_path 工具 — 把路径安全检查从 chat_bridge 抽出来 DRY。

背景：chat_bridge.read_text_file 与 _build_user_content 都各自实现
"拒绝对路径 + POSIX 风格前缀检查"，代码重复。现在集中到本模块。

设计原则：
- 单一职责：只判断「这个路径字符串是不是相对路径」
- 不做文件存在性 / 大小检查（那是调用方的责任）
- 不做规范化（os.path.normpath 等）—— 调用方拿原样字符串去 read / open，
  失败的话 OS 会自然报错；规范化反而可能改变路径语义

跨平台要点：
- Windows: os.path.isabs("C:\\Users\\...") == True
- POSIX: os.path.isabs("/etc/passwd") == True
- Qt 拖入文件用 file:/// URL，转 toLocalFile 后 Windows 上仍是
  "C:\\Users\\..."（绝对）；但 unit test / 编程传字符串时可能给
  "/etc/passwd" 这种 POSIX 风格 —— isabs 在 Windows 上返回 False，
  必须额外 path.startswith("/") 拦截
"""
import os


def is_safe_relative(path: str) -> bool:
    """判断路径字符串是否是「安全相对路径」。

    安全条件（全部满足）：
    1. 非空（None / "" 拒）
    2. 不是 OS 绝对路径（os.path.isabs）
    3. 不以 "/" 开头（POSIX 风格绝对路径的兜底拦截，
       解决 Windows 上 os.path.isabs 不认 /etc/passwd 的问题）

    返回 True 表示「可以传给 read_* / open 之类函数」；
    返回 False 表示「应拒绝并提示用户」。
    """
    if not path:
        return False
    if os.path.isabs(path):
        return False
    if path.startswith("/"):
        return False
    return True
