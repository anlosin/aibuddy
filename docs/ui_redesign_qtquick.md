# UI 现代化重设计方案（QtQuick / QML）

> **状态**：评估文档，未实施。
> **基线版本**：`0504595`（PyQt5 主题微调，已推远端）
> **目标**：从 PyQt5 原生控件迁移到 QtQuick 2.15 + QML 声明式 UI，获得现代 web 风格的视觉与交互

---

## 1. 为什么 PyQt5 达不到现代极简

| 框架层限制 | 表现 |
|---|---|
| QSS 不支持子选择器 | `.parent:hover .child` 联动写不出 |
| 控件非 GPU 渲染 | 阴影/模糊/动画全靠 QSS 模拟，PyQt5 支持度差 |
| Win32 原生依赖 | 圆角/边框在 Windows 渲染硬，看起来"应用感"重 |
| QSS 不支持渐变 | `background: linear-gradient(...)` 无效 |
| 字体/间距写死 | 13px / 8px 这种紧凑感与现代 web 14-16px / 12-16px 有差距 |

**结论**：用 QSS 调到 0504595 的程度已经是 PyQt5 的天花板。

---

## 2. QtQuick 优势（为什么能解决）

| QtQuick 能力 | 对应现代 UI 需求 |
|---|---|
| QML 声明式 + JavaScript 逻辑 | 像写 HTML+CSS，复杂样式 1 行 |
| GPU 加速渲染（OpenGL / Direct3D） | 阴影 / 渐变 / 模糊 / 圆角 全部原生支持 |
| `BorderImage` / `ShaderEffect` | 真·毛玻璃、渐变、动画 |
| `MouseArea` + `states` 过渡 | 流畅 hover/press 过渡（120ms ease） |
| `Material` / `Universal` 内置风格 | 起点就是 Material 3 / Fluent 风格，再自定 1 周即可出 Linear 感 |
| `Repeater` + `ListView` | 列表项 60px 高 + 选中态 2px 蓝条 = 几行代码 |
| 与 Python 集成（QObject） | 后端逻辑（chat / scheduler / plugin）零改动 |

---

## 3. 实施路径

### 阶段 1：技术验证（1-2 天）
- **目标**：确认 QtQuick 在本项目能跑起来
- **任务**：
  - 装 PyQt5 的 QML 绑定（其实 PyQt5 自带 `PyQt5.QtQml`，不需新依赖）
  - 写一个最小 demo：`main.py` 启动 `QQuickView` 加载一个 `Main.qml` 显示一个按钮
  - 验证 Python → QML 双向通信（`Property` / `Signal` / `Slot`）
- **风险**：QtQuick 文档老旧、PyQt5 + Qt 5.15 的 QML 行为可能跟 6.x 不同
- **退出条件**：能跑通 hello world + 一个简单交互

### 阶段 2：QML 重构核心窗口（5-7 天）
- **目标**：`chat_window.py` 的核心功能用 QML 重写
- **范围**：
  - 主窗口骨架（侧边栏 240px + 聊天区 + 输入栏）
  - 会话列表项（QML `ListView` + 60px 高 + 选中态蓝条）
  - 消息气泡（QML 自定义 `Rectangle` 组件）
  - 输入框 + 发送按钮（QML 内置 `TextField` + 自定义按钮）
  - 顶栏（专家选择 / 模型选择 / 状态）
- **保持不动**：`worker.py`（业务流）/ `plugin_manager.py` / `scheduler.py`
- **Python 暴露给 QML**：
  ```python
  class ChatBridge(QObject):
    @Slot(str, result=str)
    def send_message(self, text): ...
  ```
- **风险**：
  - 消息气泡的 Markdown 渲染（QML `Text` 组件支持有限，可能要分段用多个 Text）
  - 流式响应（WebSocket 风格推送）—— QML 用 `Q_PROPERTY(NOTIFY signal)` 绑定
  - 代码块、复制按钮、停止生成等交互细节多

### 阶段 3：迁移对话框（3-4 天）
- **目标**：`automation_dialogs.py` / `settings_dialog.py` 全部用 QML `Dialog` 组件
- **范围**：
  - 自动化任务管理表格（QML `TableView` / `ListView`）
  - 编辑表单（QML 自带 `TextField` / `ComboBox` / `SpinBox`）
  - 偏好设置（QML `Switch` / `Slider`）
- **Python 暴露给 QML**：每个对话框一个 `XxxDialogBridge` 暴露 `load()` / `save()` 方法

### 阶段 4：保留 QWidget 的过渡方案（可选）
- **不迁移的弹窗**（如 `QMessageBox` 错误提示 / `QFileDialog` 文件选择）继续用 QtWidgets
- 混用 `QQuickWidget` 嵌入 QML 到现有 QWidget 窗口，作为渐进迁移
- 等所有重要 UI 都迁完再彻底切到 `QQuickView`

---

## 4. 工作量估算

| 阶段 | 工作量 | 风险 | 是否能回滚 |
|---|---|---|---|
| 1. 技术验证 | 1-2 天 | 低 | 完全回滚（demo 不入主分支） |
| 2. 主窗口重写 | 5-7 天 | **中** | 难（主入口改动大） |
| 3. 对话框迁移 | 3-4 天 | 低 | 容易（一个文件一个文件迁） |
| 4. 收尾打磨 | 2-3 天 | 低 | 容易 |
| **合计** | **2-3 周** | 中 | 阶段 1 不入主分支，后续可分 PR |

### 投入产出

| 项 | 价值 |
|---|---|
| 视觉 | 真正达到 Linear / Notion / ChatGPT 那种 web 感 |
| 交互 | 流式响应、过渡动画、键盘快捷键（QML 友好） |
| 维护 | QML 比 QWidget 代码量小 30-50%（声明式） |
| 跨平台 | macOS / Linux 视觉跟 Windows 一致（当前 macOS 上肯定跟 Win 不一样） |

---

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| PyQt5 5.15 是 EOL（Qt 公司 2023 起只维护商业版） | 阶段 1 评估时同时验证 PyQt6 升级路径（语法差异约 5%） |
| QML 学习曲线 | 当前开发者已熟 QWidget，QML 主要新概念是绑定语法 + 状态机 |
| Markdown 渲染细节 | 阶段 2 用 QML 写一个 `MarkdownText.qml` 组件，测试 20+ 真实回答样本 |
| 性能 | QtQuick 是 GPU 加速，预期比当前 QTextEdit + 嵌入 HTML 还快 |
| 用户适应 | 视觉差异大，可能需 1-2 周用户反馈调整 |

---

## 6. 替代方案对照

| 方案 | 工作量 | 效果 | 风险 |
|---|---|---|---|
| **A. 维持 PyQt5**（当前 0504595） | 0 | 中（PyQt5 天花板） | 无 |
| **B. 继续用 QSS 抠细节** | 1-2 天 | 中+ | 高（可能再改也看不出） |
| **C. QtQuick 重写**（本文档） | 2-3 周 | 高（达到 web 级） | 中 |
| **D. 换 Web 技术栈**（Electron / Tauri） | 4-6 周 | 最高 | 极高（项目本质是 Python+AI 客户端） |

---

## 7. 下一步建议

1. **先做阶段 1 验证**（1-2 天投入，验证 QtQuick 真的能跑、效果确实比 QSS 好）
2. 验证通过 → 开新分支 `feature/qtquick-ui`，阶段 2 起开始重写主窗口
3. 阶段 2 中间给用户看 demo 截图，确认方向对
4. 主窗口稳定后再决定要不要继续做阶段 3-4

**建议在阶段 1 之后给一个 checkpoint**：用户验收 demo 满意 → 继续；不满意 → 停在 0504595，不亏。
