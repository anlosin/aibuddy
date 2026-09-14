"""_DialogHost — PyQt5 对话框 ↔ ChatBridge(QObject) 的适配器。

背景（Day 20.2 修复）
--------------------
settings_dialog.py 里 show_settings / show_model_manager / show_plugin_manager 三个
对话框最初是为 chat_window.py 的 ChatWindow(QWidget) 写的。它们会：

1. ``QDialog(window)`` —— 需要 window 是 QWidget 才能当 parent。
2. 读 ``window.enable_thinking`` / ``window.theme`` / ``window.current_model_id``
   / ``window.base_url`` / ``window.plugins`` / ``window.plugin_infos`` 等十几个
   公开字段。
3. 调 ``window._save_settings()`` / ``window.apply_theme()`` /
   ``window.display_message(...)`` / ``window.setup_client()`` /
   ``window.switch_model(mid)`` 这五个方法。

Day 20.1 把这三个菜单入口（QtQuick 路径）接到 ``ChatBridge(QObject)`` 上后，
直接 ``show_model_manager(self)`` 在 QDialog 构造时就抛：
   ``argument 1 has unexpected type 'ChatBridge'``

即便绕过第一关，后面读 ``window.enable_thinking`` 也会 AttributeError。

本适配器的角色
--------------
``_DialogHost(QWidget)`` 把 ChatBridge 包一层，让它：
- 对外是 QWidget（QDialog 能当 parent）
- 暴露上述所有字段 / 方法（持久化到 ``data/model_config.json``，
  跟 PyQt5 路径同源；运行态字段代理给 bridge）
- ``display_message`` / ``apply_theme`` 走 ChatBridge 已有的信号（messageAdded
  / themeChanged），QML 端能正常渲染

PyQt5 路径（chat_window.py）继续用 ``self`` 当 host，行为不变。
"""
from datetime import datetime
from PyQt5.QtWidgets import QWidget

from . import config as _config


class _DialogHost(QWidget):
    """QWidget 适配器：把 ChatBridge 暴露成 ChatWindow 风格的接口。"""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self._b = bridge
        # 纯 dialog host —— 不显示、不进任务栏，避免用户看到「额外的窗口」
        self.setVisible(False)
        # 不显示窗口标题（Win 上没有标题栏会显示成"python"）
        self.setWindowTitle("")

    # ────── 内部工具 ──────

    def _cfg(self):
        return _config.load_config()

    def _save_cfg(self, cfg):
        _config.save_config(cfg)

    def _cur_model(self):
        return _config.get_current_model() or {}

    def _update_cur_model(self, fields):
        """更新当前激活模型字典里的若干字段并写盘"""
        models, cur = _config.load_models()
        for m in models:
            if m.get("id") == cur:
                m.update(fields)
                break
        _config.save_models(models, cur)

    # ────── 全局偏好（cfg 顶层字段） ──────

    @property
    def enable_thinking(self):
        return getattr(self._b, "_enable_thinking", False)

    @enable_thinking.setter
    def enable_thinking(self, v):
        self._b._enable_thinking = bool(v)

    @property
    def enable_tools(self):
        return getattr(self._b, "_enable_tools", True)

    @enable_tools.setter
    def enable_tools(self, v):
        self._b._enable_tools = bool(v)

    @property
    def agent_mode(self):
        return bool(self._cfg().get("agent_mode", False))

    @agent_mode.setter
    def agent_mode(self, v):
        cfg = self._cfg()
        cfg["agent_mode"] = bool(v)
        self._save_cfg(cfg)

    @property
    def max_agent_rounds(self):
        return int(self._cfg().get("max_agent_rounds", 3))

    @max_agent_rounds.setter
    def max_agent_rounds(self, v):
        cfg = self._cfg()
        cfg["max_agent_rounds"] = int(v)
        self._save_cfg(cfg)

    @property
    def theme(self):
        getter = getattr(self._b, "get_theme", None)
        return getter() if getter else self._b._theme

    @theme.setter
    def theme(self, v):
        # ChatBridge.set_theme 内部会 emit themeChanged(name)，QML 自动重渲
        setter = getattr(self._b, "set_theme", None)
        if setter:
            setter(v)
        else:
            self._b._theme = v

    # ────── 当前模型 ──────

    @property
    def current_model_id(self):
        return _config.load_models()[1]

    @current_model_id.setter
    def current_model_id(self, v):
        models, _ = _config.load_models()
        _config.save_models(models, v)

    @property
    def base_url(self):
        return self._cur_model().get("base_url", "")

    @base_url.setter
    def base_url(self, v):
        self._update_cur_model({"base_url": v})

    @property
    def api_key(self):
        return self._cur_model().get("api_key", "")

    @api_key.setter
    def api_key(self, v):
        self._update_cur_model({"api_key": v})

    @property
    def model_id(self):
        return self._cur_model().get("model_id", "")

    @model_id.setter
    def model_id(self, v):
        self._update_cur_model({"model_id": v})

    @property
    def proxy(self):
        return self._cur_model().get("proxy", "")

    @proxy.setter
    def proxy(self, v):
        self._update_cur_model({"proxy": v})

    # ────── 插件列表（chat_window 同字段） ──────
    # ChatBridge 没有持久 plugins / plugin_infos 实例字段（每次按需
    # _discover_plugins()），但 show_plugin_manager 会读 window.plugins
    # / window.plugin_infos 还要写回（reload 后赋新值）。缓存到 bridge
    # 上避免每次扫描磁盘。

    @property
    def plugins(self):
        cached = getattr(self._b, "_plugins_cache", None)
        if cached is None:
            from .plugin_manager import discover_plugins
            plugins, infos = discover_plugins()
            self._b._plugins_cache = plugins
            self._b._plugin_infos_cache = infos
            cached = plugins
        return cached

    @plugins.setter
    def plugins(self, v):
        self._b._plugins_cache = v

    @property
    def plugin_infos(self):
        cached = getattr(self._b, "_plugin_infos_cache", None)
        if cached is None:
            from .plugin_manager import discover_plugins
            plugins, infos = discover_plugins()
            self._b._plugins_cache = plugins
            self._b._plugin_infos_cache = infos
            cached = infos
        return cached

    @plugin_infos.setter
    def plugin_infos(self, v):
        self._b._plugin_infos_cache = v

    @property
    def enabled_plugins(self):
        return list(self._cfg().get("enabled_plugins", []))

    @enabled_plugins.setter
    def enabled_plugins(self, v):
        cfg = self._cfg()
        cfg["enabled_plugins"] = list(v)
        self._save_cfg(cfg)

    # ────── 回调方法（PyQt5 路径 ChatWindow 上的同名方法） ──────

    def _save_settings(self):
        """把所有「全局偏好」+ 当前模型字段统一写回 cfg。"""
        cfg = self._cfg()
        cfg["enable_thinking"] = self.enable_thinking
        cfg["enable_tools"] = self.enable_tools
        cfg["agent_mode"] = self.agent_mode
        cfg["max_agent_rounds"] = self.max_agent_rounds
        cfg["ui_theme"] = self.theme
        # 当前模型字段（PyQt5 路径 _save_settings 同步写回，这里照搬以保持
        # 两条路径下文件结构一致 —— 已迁移到 models 注册表的字段可忽略）
        m = self._cur_model()
        if m.get("id"):
            cfg["model_id"] = m.get("model_id", "")
            cfg["api_key"] = m.get("api_key", "")
            cfg["base_url"] = m.get("base_url", "")
            cfg["proxy"] = m.get("proxy", "")
        self._save_cfg(cfg)

    def apply_theme(self, rerender_chat=True):
        """应用主题。QtQuick 路径下 set_theme 已 emit themeChanged，
        QML Connections.onThemeChanged 会自动重渲。rerender_chat 仅 PyQt5
        路径使用（重画 QTextEdit），这里忽略。"""
        self.theme = self.theme  # 触发 setter，走 set_theme 信号

    def display_message(self, sender, message, tag=None, time_str=None):
        """显示一条系统消息 —— QtQuick 路径 emit messageAdded 信号。"""
        if not time_str:
            time_str = datetime.now().strftime("%H:%M")
        # Day 17 QTBUG-94360：messageAdded 严格 2 参数（str, QVariantMap）
        self._b.messageAdded.emit(sender, {
            "text": message,
            "ts": time_str,
            "code": tag or "system",
        })

    def setup_client(self):
        """重建 OpenAI client。QtQuick 路径不需要立即动作：bridge 在下次
        start_real_chat 会按 _config.get_current_model() 读最新字段，模型
        名字通过 set_current_model 已更新到 _last_model_name 并 emit
        currentModelNameChanged。这里只需要触发一次刷新即可。"""
        m = self._cur_model()
        if m:
            new_name = m.get("name") or m.get("model_id", "?")
            if getattr(self._b, "_last_model_name", None) != new_name:
                self._b._last_model_name = new_name
                try:
                    self._b.currentModelNameChanged.emit(new_name)
                except Exception:
                    pass

    def switch_model(self, model_entry_id):
        """切换当前激活模型 —— 委托给 bridge.set_current_model()。"""
        ok = self._b.set_current_model(model_entry_id)
        if ok:
            m = self._cur_model()
            label = m.get("name") or m.get("model_id", "?")
            self.display_message(
                "系统",
                f"已切换模型 → {label}（{m.get('model_id', '')} @ {m.get('base_url', '')}）",
                "system",
            )
