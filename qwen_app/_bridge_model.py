"""ChatBridge 功能切片 —— ModelMixin（模型 / 偏好 / 专家 / 文件读取）。

拆分约定（施工图 §2.3 硬规则）：
- mixin 继承普通 ``object``，不写 ``__init__``，不继承 QObject；
- 信号 / pyqtProperty / ``__init__`` / 类常量全部留在 ``chat_bridge.py`` 主类体；
- 本模块**不准** import ``chat_bridge``（防成环）；
- 方法体从 chat_bridge.py 逐字搬入（含装饰器与注释），逻辑零改动。
"""
import os

from PyQt5.QtCore import pyqtSlot

from . import config as _config
from ._safe_path import is_safe_to_read  # Day 19.1: 路径安全检查（DRY）


class ModelMixin(object):
    """模型切换 / 偏好读写 / 专家切换 / 文件大小与文本读取 slot。"""

    @pyqtSlot(result='QVariantList')
    def list_models(self):
        """Day 10: 返回可选模型列表 [{id, name, current}, ...]"""
        try:
            models, current_id = _config.load_models()
        except Exception:
            return []
        out = []
        for m in models:
            out.append({
                "id": m.get("id", ""),
                "name": m.get("name") or m.get("model_id") or "?",
                "current": m.get("id") == current_id,
            })
        return out

    @pyqtSlot(str, result=bool)
    def set_current_model(self, model_id: str) -> bool:
        """Day 10: 切换当前模型。成功返回 True。"""
        if not model_id:
            return False
        try:
            models, _ = _config.load_models()
            if not any(m.get("id") == model_id for m in models):
                return False
            _config.save_models(models, model_id)
            # 刷新 _last_model_name，后续 start_real_chat 会用新模型
            new_model = next((m for m in models if m.get("id") == model_id), None)
            if new_model:
                self._last_model_name = new_model.get("name") or new_model.get("model_id", "?")
                self.currentModelNameChanged.emit(self._last_model_name)
            return True
        except Exception as e:
            print(f"[chat_bridge] set_current_model 失败: {e}")
            return False

    @pyqtSlot(result=str)
    def get_current_model_name(self) -> str:
        """Day 10: 返回当前模型名称（QML 顶栏显示用）"""
        return self._last_model_name or "(未选择)"

    @pyqtSlot(bool, bool, bool, result=bool)
    def set_preferences(self, enable_thinking: bool, enable_tools: bool,
                        enable_context: bool) -> bool:
        """Day 19 (M-NEW-5 修复): 写全局偏好到 cfg 顶层 + 立即生效。
        Day 20.6.22: 增加 enable_context 参数 —— 「上下文记忆」开关。

        QML 端「偏好设置」对话框（Main.qml）保存时调此 slot。
        成功返回 True，失败返回 False。
        """
        try:
            cfg = _config.load_config()
            cfg["enable_thinking"] = bool(enable_thinking)
            cfg["enable_tools"] = bool(enable_tools)
            cfg["enable_context"] = bool(enable_context)
            _config.save_config(cfg)
            # 立即更新 self，下次 start_real_chat 生效
            self._enable_thinking = bool(enable_thinking)
            self._enable_tools = bool(enable_tools)
            self._enable_context = bool(enable_context)
            return True
        except Exception as e:
            print(f"[chat_bridge] set_preferences 失败: {e}")
            return False

    @pyqtSlot(result='QVariantMap')
    def get_preferences(self):
        """返回当前全局偏好（QML 端读后渲染设置对话框的勾选状态）。

        Day 20.6.22: 新增 enable_context 字段；旧 QtQuick 调用方可能未传，
        QML 端 get_preferences() 总能读到完整三项。
        """
        return {
            "enable_thinking": getattr(self, "_enable_thinking", False),
            "enable_tools": getattr(self, "_enable_tools", True),
            "enable_context": getattr(self, "_enable_context", True),
        }

    @pyqtSlot(bool, result=bool)
    def set_enable_context(self, enable: bool) -> bool:
        """Day 20.6.22: 单独切换上下文记忆（独立 slot，便于菜单快捷切换）。

        QML 端偏好设置 / 工具栏按钮可单独调本方法，不必走三参 set_preferences。
        持久化到 cfg["enable_context"]；QML 端 get_preferences() 刷新读到最新值。
        """
        try:
            cfg = _config.load_config()
            cfg["enable_context"] = bool(enable)
            _config.save_config(cfg)
            self._enable_context = bool(enable)
            return True
        except Exception as e:
            print(f"[chat_bridge] set_enable_context 失败: {e}")
            return False

    def _apply_expert(self, expert_id: str) -> bool:
        """Day 20.6.15: 专家切换的**统一入口**（选中态三件套）：

        1. in-memory（self._current_expert_id，本次对话立即生效）
        2. 持久化（cfg["current_expert"]，与 chat_window._save_current_expert
           共用同一个键 —— 重启后 / PyQt5 备用窗口读到的是同一个值）
        3. emit expertChanged（QML 专家下拉框跟随，覆盖 /dev 前缀路由场景：
           用户没碰下拉框但当前专家变了，UI 必须同步）

        此前 set_expert 只做第 1 件 —— 切换不落盘、QML 也收不到通知。
        """
        experts = self._load_experts()
        if expert_id not in experts:
            return False
        self._current_expert_id = expert_id
        try:
            cfg = _config.load_config()
            cfg["current_expert"] = expert_id
            _config.save_config(cfg)
        except Exception as e:
            print(f"[chat_bridge] 持久化 current_expert 失败: {e}")
        try:
            self.expertChanged.emit(expert_id)
        except Exception:
            pass
        return True

    @pyqtSlot(str, result=bool)
    def set_expert(self, expert_id: str) -> bool:
        """Day 19 (C-NEW-3 修复): 切到指定专家。成功返回 True。

        QML 端下拉框选专家时调此 slot；/dev 前缀路由也会调。
        Day 20.6.15: 改走 _apply_expert —— 补持久化 + expertChanged 信号。
        """
        return self._apply_expert(expert_id)

    @pyqtSlot(result='QVariantList')
    def list_experts(self):
        """Day 19 (C-NEW-3 修复): 返回所有专家的元信息（QML 端下拉框用）。

        格式: [{id, name, description, current}, ...]
        """
        experts = self._load_experts()
        out = []
        cur = getattr(self, "_current_expert_id", "general")
        for eid, e in experts.items():
            out.append({
                "id": eid,
                "name": e.get("name", eid),
                "description": e.get("description", ""),
                "current": eid == cur,
            })
        return out

    @pyqtSlot(str, result='QVariantMap')
    def get_file_size(self, file_path: str):
        """Day 19 (H-NEW-8): 查文件大小（字节）。

        QML 端 DropArea 拖入文件时调本 slot 判断是否超过 50MB 上限。
        超过直接拒绝 + 显示提示气泡，避免无意义的大文件（ISO / 视频 / 压缩包）
        进 read_text_file / image 附件把 GUI 卡死。

        返回结构: {"ok": bool, "size": int, "error": str}
        - ok=True:  size 是文件字节数
        - ok=False: error 是失败原因（不存在 / 无权限 / 路径为空）
        """
        out = {"ok": False, "size": 0, "error": ""}
        if not file_path:
            out["error"] = "路径为空"
            return out
        # Day 19 (H-NEW-8): 路径字符串过滤（与 read_text_file 一致；
        # Day 19.1 修订：不再拒绝绝对路径，GUI 拖入永远绝对）
        if not is_safe_to_read(file_path):
            out["error"] = "路径字符串不合法（空或含控制字符）"
            return out
        try:
            out["size"] = os.path.getsize(file_path)
            out["ok"] = True
        except FileNotFoundError:
            out["error"] = "文件不存在"
        except PermissionError:
            out["error"] = "无权限访问"
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    @pyqtSlot(result='QVariantList')
    def list_readable_ext(self):
        """Day 19 (M-NEW-4): 暴露 _READABLE_EXT 白名单给 QML。

        之前 QML 端 DropArea.onDropped 自己维护了一份 textExts 数组，与
        Python _READABLE_EXT 重复；任何一处扩展白名单都得手动同步两边，
        容易漂移。现在统一以 Python 为单一来源，QML 通过本 slot 拉取。
        返回纯字符串列表（含点前缀），QML 端用 endsWith(ext) 判断。
        """
        return list(self._READABLE_EXT)

    @pyqtSlot(str, result=str)
    def read_text_file(self, file_path: str) -> str:
        """Day 13: 读文本文件内容（拖入文本文件时附加到输入框用）

        返回格式: "[文件: 路径]\n```lang\n内容\n```"
        - 限制 50KB（更大截断 + 提示）
        - 只读文本类：扩展名白名单 + 绝对路径拒绝（防敏感文件泄露）
        - 出错返回 "[读文件失败: ...]"

        安全（Day 18 修复 C3）：
        - 拒绝绝对路径（仅接受拖入工作区内的相对路径；之前任意路径都接受，
          导致 ~/.ssh/id_rsa、/etc/passwd 等可被读 + 走 LLM 泄露）
        - 扩展名白名单（见 _READABLE_EXT）；二进制/可执行/压缩包直接拒绝
        """
        if not file_path:
            return ""
        # 路径字符串过滤（Day 19.1：不再拒绝绝对路径；GUI 拖入永远绝对）
        if not is_safe_to_read(file_path):
            return "[读文件失败: 路径不合法（空或含控制字符）]"
        # C3 修复：扩展名白名单
        lower = file_path.lower()
        if not any(lower.endswith(ext) for ext in self._READABLE_EXT):
            return ("[读文件失败: 不支持的文件类型（仅文本/代码/配置/文档类）]")
        # 文件大小检查（50KB 限制）
        MAX_SIZE = 50 * 1024
        try:
            size = os.path.getsize(file_path)
        except Exception as e:
            return f"[读文件失败: {e}]"
        if size > MAX_SIZE:
            truncated_msg = f"(文件过大，已截断到 {MAX_SIZE // 1024}KB)"
        else:
            truncated_msg = None
        # 按扩展名选语言标签
        lang = "text"
        for ext, l in ((".py", "python"), (".js", "javascript"), (".ts", "typescript"),
                        (".json", "json"), (".md", "markdown"), (".html", "html"),
                        (".css", "css"), (".sh", "bash"), (".yml", "yaml"),
                        (".yaml", "yaml"), (".xml", "xml"), (".sql", "sql"),
                        (".java", "java"), (".go", "go"), (".rs", "rust"),
                        (".cpp", "cpp"), (".c", "c"), (".h", "c"), (".txt", "text"),
                        (".log", "text"), (".ini", "ini"), (".cfg", "ini"),
                        (".toml", "ini"), (".env", "ini"), (".csv", "text")):
            if lower.endswith(ext):
                lang = l
                break
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_SIZE)
        except Exception as e:
            return f"[读文件失败: {e}]"
        file_name = os.path.basename(file_path)
        out = f"[文件: {file_name}]\n```{lang}\n{content}\n```"
        if truncated_msg:
            out += f"\n{truncated_msg}"
        return out
