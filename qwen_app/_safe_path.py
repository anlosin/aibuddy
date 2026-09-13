"""Day 19 (新加) + Day 19.1 (修订): _safe_path 工具 — 路径安全检查。

Day 19.1 修订：
- 原 is_safe_relative 拒绝所有绝对路径。
- QML DropArea.toLocalFile() 返回的永远是绝对路径（C:/Users/...），
  原实现导致所有 GUI 拖入的文本/图片都被误拒（回归 bug）。
- 设计意图变更：read_text_file / get_file_size / _build_user_content
  的输入路径来源只有 QML DropArea（@pyqtSlot 不可被 LLM 直接调），
  绝对路径是合法且唯一的输入形式。"拒绝绝对路径"是死代码 + 阻断
  合法功能，必须删除。

新设计：
- is_safe_to_read(path)：路径可读（仅过滤 None/空/控制字符/路径过长）
- 不再检查绝对/相对 —— GUI 拖入永远合法
- 调用方各自负责扩展名白名单、大小限制（chat_bridge._READABLE_EXT / _MAX_IMAGE_BYTES）
"""
import os


def is_safe_to_read(path: str) -> bool:
    """路径字符串可作为文件读路径（基础安全过滤）。

    安全条件：
    1. 非空（None / "" 拒）
    2. 没有控制字符（防注入）
    3. 长度 < 4096（防恶意超长路径）

    注意：
    - 不检查路径是否存在（调用方 OSError 自然处理）
    - 不检查绝对/相对（GUI 拖入永远合法）
    - 不检查扩展名（调用方白名单处理）
    - 不检查 ../ 路径遍历（GUI 拖入的路径是用户主动选择的，
      OS open 按 CWD 解析；如果要防遍历，由调用方 normpath + 根边界检查）

    返回 True 表示「字符串形式可接受，可尝试 open」。
    """
    if not path:
        return False
    if len(path) > 4096:
        return False
    # 控制字符：\x00-\x1f（除路径合法字符外）
    # 路径里实际不会含这些字符（即使含也不能打开）
    for ch in path:
        if ord(ch) < 0x20:
            return False
    return True


def is_safe_relative(path: str) -> bool:
    """Day 19 兼容：保留旧 API 但标记为 deprecated。

    旧实现拒绝绝对路径，已被证明是错误的设计（GUI 拖入永远是绝对路径）。
    现在返回 True 让调用方继续工作；新代码请用 is_safe_to_read()。

    返回 True：兼容历史（GUI 拖入不被拒）
    """
    # 兼容路径：永远返回 True（不再拒绝绝对路径）
    return bool(path)
