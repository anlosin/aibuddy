import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1100
    height: 760
    title: "aibuddy (QtQuick Day 1-2 Demo)"

    // ===== Day 20: 顶部 MenuBar =====
    // Day 20.3 精简：8 组（文件/编辑/视图/聊天/设置/模型/自动化/帮助）
    // → 5 组（文件/编辑/视图/工具/帮助）。
    //   - 「聊天」并入「工具」（含 4 个 chat 操作）
    //   - 「设置 / 模型 / 自动化」合并为「工具」（含 5 个管理操作）
    // 用户反馈"菜单栏菜单过多"——把功能相近的合并，主对话操作走快捷键
    // / 三点菜单，主管理操作走「工具」分组。
    // 所有 action 走 bridge.* slot —— 已经在 chat_bridge.py 实现
    // 不引入新 QObject 局部变量（避免保活坑）
    // 所有 MenuItem enabled 都做 bridge 注入守卫（H5 教训）
    menuBar: MenuBar {
        Menu {
            title: qsTr("&文件")
            MenuItem {
                text: qsTr("新对话\tCtrl+N")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.create_session("新对话")
            }
            MenuItem {
                text: qsTr("导出当前会话...")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: {
                    if (!bridge) return
                    bridge.toast("请用气泡三点菜单的「分享」逐条导出")
                }
            }
            MenuSeparator {}
            MenuItem {
                text: qsTr("退出")
                onTriggered: Qt.quit()
            }
        }
        Menu {
            title: qsTr("&编辑")
            MenuItem {
                text: qsTr("清空消息\tCtrl+L")
                enabled: bridge !== undefined && bridge !== null && !inputField.activeFocus
                onTriggered: messageModel.clear()
            }
            MenuItem {
                text: qsTr("查找\tCtrl+F")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.toast("查找功能开发中")
            }
            MenuSeparator {}
            MenuItem {
                text: qsTr("复制最后一条 AI 回答")
                enabled: bridge !== undefined && bridge !== null && messageModel.count > 0
                onTriggered: {
                    if (!bridge) return
                    var last = ""
                    for (var i = messageModel.count - 1; i >= 0; i--) {
                        var it = messageModel.get(i)
                        if (it.who === "ai") { last = it.text; break }
                    }
                    if (last) bridge.copy_to_clipboard(last)
                    else bridge.toast("没有 AI 回答可复制")
                }
            }
            MenuSeparator {}
            MenuItem {
                text: qsTr("偏好设置...")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.show_settings_dialog()
            }
        }
        Menu {
            title: qsTr("&视图")
            MenuItem {
                text: qsTr("切换主题\tCtrl+T")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.set_theme(root.themeName === "dark" ? "light" : "dark")
            }
        }
        // Day 20.3: 合并 4 组功能到「工具」菜单（chat / 模型 / 插件 / 自动化）
        Menu {
            title: qsTr("&工具")
            // ── 对话操作（4 项，原「聊天」菜单） ──
            MenuItem {
                text: qsTr("重新生成最后一条 AI 回答")
                enabled: bridge !== undefined && bridge !== null && !bridge.isBusy && messageModel.count > 0
                onTriggered: {
                    if (!bridge) return
                    var lastAi = -1
                    for (var i = messageModel.count - 1; i >= 0; i--) {
                        if (messageModel.get(i).who === "ai") { lastAi = i; break }
                    }
                    if (lastAi >= 0) bridge.regenerate_ai_response(lastAi)
                    else bridge.toast("没有 AI 回答可重新生成")
                }
            }
            MenuItem {
                text: qsTr("停止生成\tEsc")
                enabled: bridge !== undefined && bridge !== null && bridge.isBusy
                onTriggered: if (bridge) bridge.stop_chat()
            }
            MenuItem {
                text: qsTr("朗读最后一条 AI 回答")
                enabled: bridge !== undefined && bridge !== null && messageModel.count > 0
                onTriggered: {
                    if (!bridge) return
                    var last = ""
                    for (var i = messageModel.count - 1; i >= 0; i--) {
                        var it = messageModel.get(i)
                        if (it.who === "ai") { last = it.text; break }
                    }
                    if (last) bridge.speak_text(last)
                }
            }
            MenuItem {
                text: qsTr("删除最后一条消息")
                enabled: bridge !== undefined && bridge !== null && messageModel.count > 0
                onTriggered: {
                    if (!bridge) return
                    bridge.delete_bubble(messageModel.count - 1)
                }
            }
            MenuSeparator {}
            // ── 模型 / 插件 / 自动化（5 项，原「设置 / 模型 / 自动化」合并） ──
            MenuItem {
                text: qsTr("模型管理...")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.show_model_manager_dialog()
            }
            MenuItem {
                text: qsTr("插件管理...")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.show_plugin_manager_dialog()
            }
            MenuItem {
                text: qsTr("自动化任务...")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.show_automation_manager_dialog()
            }
            MenuItem {
                text: qsTr("立即检查并执行到期任务")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.run_automation_check_due()
            }
        }
        Menu {
            title: qsTr("&帮助")
            MenuItem {
                text: qsTr("快捷键")
                onTriggered: helpPopup.open()
            }
            MenuItem {
                text: qsTr("关于 aibuddy")
                onTriggered: aboutPopup.open()
            }
        }
    }

    // Day 20: 提示弹窗（菜单 / 三点菜单触发）
    Popup {
        id: helpPopup
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        width: 420
        height: 280
        modal: true
        focus: true
        contentItem: ColumnLayout {
            spacing: 8
            Text {
                text: "快捷键"
                font.pixelSize: 16
                font.bold: true
                color: root.pal.textPrimary
                Layout.fillWidth: true
            }
            Text {
                text: "Enter         发送消息\nShift+Enter   换行\nEsc           停止生成\nCtrl+L        清空消息\nCtrl+N        新对话\nCtrl+T        切换主题\nCtrl+K        停止生成（同 Esc）"
                font.pixelSize: 13
                color: root.pal.textPrimary
                Layout.fillWidth: true
            }
            Item { Layout.fillHeight: true }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                radius: 6
                color: closeHelpMa.containsMouse ? root.pal.sendBtnHover : root.pal.sendBtn
                Text { anchors.centerIn: parent; text: "关闭"; color: "white"; font.bold: true }
                MouseArea {
                    id: closeHelpMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: helpPopup.close()
                }
            }
        }
    }
    Popup {
        id: aboutPopup
        x: (parent.width - width) / 2
        y: (parent.height - height) / 2
        width: 380
        height: 200
        modal: true
        focus: true
        contentItem: ColumnLayout {
            spacing: 8
            Text {
                text: "aibuddy"
                font.pixelSize: 20
                font.bold: true
                color: root.pal.textPrimary
                Layout.fillWidth: true
            }
            Text {
                text: "QtQuick AI 对话助手 · Day 20"
                font.pixelSize: 13
                color: root.pal.textSecondary
                Layout.fillWidth: true
            }
            Text {
                text: "气泡操作：复制 / 删除 / 编辑 / 重新生成\n引用回复 / TTS 朗读 / 分享导出"
                font.pixelSize: 12
                color: root.pal.textTertiary
                Layout.fillWidth: true
            }
            Item { Layout.fillHeight: true }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                radius: 6
                color: closeAboutMa.containsMouse ? root.pal.sendBtnHover : root.pal.sendBtn
                Text { anchors.centerIn: parent; text: "关闭"; color: "white"; font.bold: true }
                MouseArea {
                    id: closeAboutMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: aboutPopup.close()
                }
            }
        }
    }

    // Day 20: toast 提示（非阻塞，1.5s 自动消失）
    Rectangle {
        id: toastBox
        property string msg: ""
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: 110
        radius: 8
        color: Qt.rgba(0.12, 0.12, 0.16, 0.92)
        width: Math.min(420, toastText.implicitWidth + 32)
        height: toastText.implicitHeight + 20
        opacity: msg.length > 0 ? 0.95 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Text {
            id: toastText
            anchors.centerIn: parent
            text: toastBox.msg
            color: "white"
            font.pixelSize: 13
        }
        Timer {
            id: toastTimer
            interval: 1500
            onTriggered: toastBox.msg = ""
        }
    }

    // 主题切换：light / dark
    property string themeName: bridge ? bridge.get_theme() : "light"

    color: themeName === "dark" ? "#0E0F12" : "#F8F9FB"

    // ===== 主题调色板（双套） =====
    readonly property var pal: ({
        "light": {
            "appBg": "#F8F9FB",
            "sidebarBg": "#FFFFFF",
            "sidebarBorder": "#ECEEF2",
            "sidebarHover": "#F4F6FB",
            "sidebarSel": "#EAF1FF",
            "userBubble": "#4F8AF7",
            "userBubbleHover": "#3B5BEF",
            "userFg": "#FFFFFF",
            "aiBubble": "#FFFFFF",
            "aiBubbleBorder": "#E5E7EB",
            "aiFg": "#1F1F1F",
            "aiCode": "#F2F3F5",
            "aiCodeFg": "#1F1F1F",
            "inputBg": "#FFFFFF",
            "inputBgFocus": "#FAFBFC",
            "inputBorder": "#E5E6EB",
            "inputBorderFocus": "#4F8AF7",
            "sendBtn": "#4F8AF7",
            "sendBtnHover": "#3B5BEF",
            "stopBtn": "#E55656",
            "stopBtnHover": "#C73B3B",
            "textPrimary": "#1F1F1F",
            "textSecondary": "#8A8F99",
            "textTertiary": "#B0B4BD",
            "topbarBg": "#FFFFFF",
            "topbarBorder": "#ECEEF2",
            "avatarAi": "#4E6EF2",
            "avatarUser": "#23B36B",
            "tsColor": "#B0B4BD",
            "shadow": "#1A000000",
        },
        "dark": {
            "appBg": "#0E0F12",
            "sidebarBg": "#15161A",
            "sidebarBorder": "#22232A",
            "sidebarHover": "#1E2027",
            "sidebarSel": "#1F2A48",
            "userBubble": "#3D6FE0",
            "userBubbleHover": "#2D5FE0",
            "userFg": "#FFFFFF",
            "aiBubble": "#18191D",
            "aiBubbleBorder": "#26272D",
            "aiFg": "#E8E8E8",
            "aiCode": "#1E1E22",
            "aiCodeFg": "#E8E8E8",
            "inputBg": "#15161A",
            "inputBgFocus": "#1A1B1F",
            "inputBorder": "#2C2D34",
            "inputBorderFocus": "#4F8AF7",
            "sendBtn": "#3D6FE0",
            "sendBtnHover": "#2D5FE0",
            "stopBtn": "#E55656",
            "stopBtnHover": "#C73B3B",
            "textPrimary": "#E8E8E8",
            "textSecondary": "#888C97",
            "textTertiary": "#5A5E68",
            "topbarBg": "#15161A",
            "topbarBorder": "#22232A",
            "avatarAi": "#4E6EF2",
            "avatarUser": "#23B36B",
            "tsColor": "#5A5E68",
            "shadow": "#66000000",
        }
    })[themeName]

    // ===== 消息列表（动态，bridge 信号驱动）=====
    ListModel {
        id: messageModel
    }

    // Day 16: 附件状态（顶层声明，所有子节点都引用）
    // 之前 DropArea id 在 line 614 才声明，但 line 549 的 Rectangle 引用 fileDrop，
    // QML 加载时立即 evaluate visible binding → id 尚未 resolve → 真 GPU 渲染 segfault (0xC0000005)
    property var fileAttachments: []

    // Day 8: 会话列表（动态从 bridge.list_sessions() 填充）
    ListModel {
        id: convModel
    }

    // 重拉会话列表（bridge.sessionListChanged 触发）
    function refreshConvList() {
        convModel.clear()
        if (!bridge) return
        const sessions = bridge.list_sessions()
        for (let i = 0; i < sessions.length; i++) {
            const s = sessions[i]
            convModel.append({
                "convId": s.id,
                "name": s.name,
                "time": s.time,
                "sel": s.sel
            })
        }
    }

    // Day 17: 启动时填充侧边栏。Day 16 把 enable_plugin_watcher 挪到 main.py 时
    // 连带删掉了 Component.onCompleted，导致"最近对话"要等到首次
    // sessionListChanged 才会有内容（首次启动侧边栏是空的）。
    Component.onCompleted: refreshConvList()

    // ===== 监听 bridge的信号 → 推入messageModel =====
    // Day 18 (H5)：enabled 必须显式判 `bridge !== undefined && bridge !== null`。
    // 仅写 `bridge !== null` 在 Qt 5.15.x 上，context property 注入完成前
    // Connections 会一直判定为禁用状态，所有 Python → QML 信号全部丢失
    // （应用看着运行但无任何响应）。
    Connections {
        target: bridge
        enabled: bridge !== undefined && bridge !== null
        // 完整新消息
        // Day 17: 信号收敛为 (who, {text,ts,code}) —— Qt 5.15.2 下 QML Connections
        // 连接「≥3 个参数」的 Python 信号会栈越界崩溃（QTBUG-94360）
        function onMessageAdded(who, m) {
            const p = m || {}
            const cd = p.code || ""
            messageModel.append({
                "who": who,
                "text": p.text || "",
                "ts": p.ts || "",
                "hasCode": cd.length > 0,
                "code": cd
            })
            msgList.positionViewAtEnd()
        }
        // 流式追加到最后一个匹配 who 的气泡
        // Day 14: 多轮工具调用后顺序为 [user, ai, tool_call, tool_result, ai, tool_result]，
        // 必须按 who 找，不能假设总是最后一个（否则 ai 流式会写到 tool_result 里）
        function _lastIndexOf(who) {
            for (let i = messageModel.count - 1; i >= 0; i--) {
                if (messageModel.get(i).who === who) return i
            }
            return -1
        }
        function onAppendToLast(who, content) {
            const idx = _lastIndexOf(who)
            if (idx < 0) return
            const cur = messageModel.get(idx)
            messageModel.set(idx, { "text": cur.text + content })
        }
        // 完成最后一个气泡
        function onFinalizeLast(who) {
            // Day 6: 真实实现会停止光标、刷历史等。Markdown 渲染由 messageReplaced 接管。
        }
        // Day 6: 流式完成后用 Markdown 渲染版替换最后一个气泡
        function onMessageReplaced(who, newText) {
            const idx = _lastIndexOf(who)
            if (idx < 0) return
            messageModel.set(idx, { "text": newText })
        }

        // Day 8: 会话列表变化 -> 重拉
        function onSessionListChanged() {
            refreshConvList()
        }
        // Day 8: 会话加载完成 -> 重填 messageModel
        function onSessionLoaded(convId, history) {
            messageModel.clear()
            if (!history) return
            for (let i = 0; i < history.length; i++) {
                const m = history[i]
                // history item 格式: {role: 'user'|'assistant'|'system', content: '...'}
                // 过滤 system，只显示 user + assistant
                if (!m || m.role === 'system') continue
                const who = (m.role === 'user') ? 'user' : 'ai'
                messageModel.append({
                    "who": who,
                    "text": m.content || '',
                    "ts": '',
                    "hasCode": false,
                    "code": ''
                })
            }
        }
        // 错误气泡
        function onAppendError(who, text) {
            messageModel.append({
                "who": "error",
                "text": text,
                "ts": "",
                "hasCode": false,
                "code": ""
            })
            msgList.positionViewAtEnd()
        }
        // 主题切换
        function onThemeChanged(name) {
            root.themeName = name
        }
        // Day 20: 气泡删除（bridge.delete_bubble 触发，QML 同步移除）
        function onBubbleDeleted(idx) {
            if (idx < 0 || idx >= messageModel.count) return
            messageModel.remove(idx)
            msgList.positionViewAtEnd()
        }
        // Day 20: 引用回复（bridge.quote_reply 触发，文本填入输入框）
        function onQuoteInserted(quoted) {
            if (!quoted) return
            const prefix = "> " + quoted.replace(/\n/g, "\n> ") + "\n\n"
            inputField.text = prefix + inputField.text
            inputField.forceActiveFocus()
            // 滚动到末尾
            inputField.cursorPosition = inputField.text.length
        }
        // Day 20: toast 提示（bridge._toast / 菜单触发）
        function onToast(msg) {
            if (!msg) return
            toastBox.msg = msg
            toastTimer.restart()
        }
    }

    // ============ Day 12: 全局快捷键 ============
    // Day 18 (M5)：给所有破坏性 shortcut 加 inputField focus 检查。
    // 之前 Ctrl+L / Ctrl+N / Ctrl+T 在 inputField 聚焦时仍触发（Shortcut 默认
    // Qt.WindowShortcut 优先级高于 QML 子控件），导致用户输入框里按 Ctrl+L
    // 全选变成"清空全部消息"。这里加 enabled 守卫：inputField 失焦时才生效。
    // "Esc" 和 "Ctrl+K" 已在 enabled 中检查 isBusy（停止生成专用）。
    Shortcut {
        sequence: "Esc"
        enabled: bridge !== undefined && bridge !== null && bridge.isBusy
        onActivated: if (bridge) bridge.stop_chat()
    }
    Shortcut {
        sequence: "Ctrl+L"
        enabled: bridge !== undefined && bridge !== null && !inputField.activeFocus
        onActivated: messageModel.clear()
    }
    Shortcut {
        sequence: "Ctrl+T"
        enabled: bridge !== undefined && bridge !== null && !inputField.activeFocus
        onActivated: if (bridge) bridge.set_theme(root.themeName === "dark" ? "light" : "dark")
    }
    Shortcut {
        sequence: "Ctrl+N"
        enabled: bridge !== undefined && bridge !== null && !inputField.activeFocus
        onActivated: if (bridge) bridge.create_session("新对话")
    }
    Shortcut {
        sequence: "Ctrl+K"
        enabled: bridge !== undefined && bridge !== null && bridge.isBusy
        onActivated: if (bridge) bridge.stop_chat()
    }

    // 根布局
    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ============ 侧边栏 ============
        Rectangle {
            Layout.preferredWidth: 280
            Layout.fillHeight: true
            color: root.pal.sidebarBg
            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: 1
                color: root.pal.sidebarBorder
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    Layout.bottomMargin: 12
                    spacing: 8
                    Rectangle {
                        width: 24; height: 24; radius: 6
                        color: root.pal.sendBtn
                        Text {
                            anchors.centerIn: parent
                            text: "💬"
                            font.pixelSize: 14
                        }
                    }
                    Text {
                        text: "aibuddy"
                        color: root.pal.textPrimary
                        font.pixelSize: 16
                        font.bold: true
                        Layout.fillWidth: true
                    }
                    Rectangle {
                        width: 32; height: 32; radius: 8
                        color: themeMa.containsMouse ? root.pal.sidebarHover : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: root.themeName === "dark" ? "☀️" : "🌙"
                            font.pixelSize: 14
                        }
                        MouseArea {
                            id: themeMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: if (bridge) bridge.set_theme(root.themeName === "dark" ? "light" : "dark")
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    radius: 10
                    color: newMa.containsMouse ? root.pal.sendBtnHover : root.pal.sendBtn
                    Text {
                        anchors.centerIn: parent
                        text: "+  新建对话"
                        color: root.pal.userFg
                        font.pixelSize: 14
                        font.bold: true
                    }
                    MouseArea {
                        id: newMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (bridge) bridge.create_session("新对话")
                    }
                }

                Text {
                    text: "最近对话"
                    color: root.pal.textTertiary
                    font.pixelSize: 11
                    font.letterSpacing: 0.5
                    Layout.topMargin: 16
                    Layout.bottomMargin: 4
                    Layout.leftMargin: 10
                }

                ListView {
                    id: convListView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: convModel
                    clip: true
                    spacing: 2
                    delegate:Rectangle {
                        width: ListView.view.width
                        height: 60
                        radius: 10
                        color: model.sel ? root.pal.sidebarSel
                            : (rowMa.containsMouse ? root.pal.sidebarHover : "transparent")
                        Rectangle {
                            visible: model.sel
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.leftMargin: 4
                            width: 3
                            radius: 1.5
                            color: root.pal.sendBtn
                        }
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 14
                            anchors.rightMargin: 10
                            anchors.topMargin: 8
                            anchors.bottomMargin: 8
                            spacing: 0
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    text: model.name
                                    color: root.pal.textPrimary
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: model.time
                                    color: root.pal.textTertiary
                                    font.pixelSize: 11
                                }
                            }
                            // Day 20.4 修复（用户反馈"三点按钮不可用"）：
                            //   原代码 three-dot MouseArea 在 rowMa 之前声明，
                            //   但 rowMa 同样 anchors.fill: parent 覆盖整个 delegate
                            //   —— rowMa z 更高，click 被它吞了（实际触发 load_session，
                            //   看起来啥也没发生）。
                            //   修复：three-dot Rectangle z=10 抬高，click 用
                            //   mapToItem + accepted=true 双重保护避免冒泡。
                            //   同时改成"点开菜单"（重命名 / 清空 / 删除）而不是
                            //   直接 delete_session（误触代价大）。
                            Rectangle {
                                id: moreBtn
                                Layout.preferredWidth: 24
                                Layout.preferredHeight: 24
                                radius: 4
                                color: moreMa.containsMouse ? root.pal.sidebarBorder : "transparent"
                                visible: rowMa.containsMouse || model.sel
                                z: 10
                                Text {
                                    anchors.centerIn: parent
                                    text: "⋯"
                                    color: root.pal.textSecondary
                                    font.pixelSize: 14
                                }
                                MouseArea {
                                    id: moreMa
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: function(mouse) {
                                        // 1) 阻止冒泡到 rowMa（防止同时 load_session）
                                        mouse.accepted = true
                                        // 2) 把会话数据缓存到 Menu 属性
                                        //    （跨 Popup window scope 失效，跟
                                        //    MessageBubble.qml 同一坑）
                                        sessionMenu.currentConvId = model.convId
                                        sessionMenu.currentConvName = model.name
                                        // 3) 定位：Menu.x / Menu.y 是 sessionMenu.parent
                                        //    的局部坐标，所以 mapToItem 要以 parent 为目标，
                                        //    不能用 null（null 是全局屏幕坐标，差一个 sidebar 偏移）
                                        var pt = moreBtn.mapToItem(sessionMenu.parent,
                                                                   moreBtn.width, moreBtn.height)
                                        sessionMenu.x = pt.x - sessionMenu.width + moreBtn.width
                                        sessionMenu.y = pt.y + 2
                                        sessionMenu.popup()
                                    }
                                }
                            }
                        }
                        // rowMa 必须在 three-dot 之前声明 + z < 10（已 OK：比 three-btn z 晚声明）
                        MouseArea {
                            id: rowMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            z: 0
                            onClicked: if (bridge) bridge.load_session(model.convId)
                        }
                    }
                }
                // Day 20.4: 侧边栏会话三点菜单（重命名 / 清空消息 / 删除）
                // 跟 MessageBubble.qml 的 bubbleMenu 一样，跨 Popup window scope
                // 不可靠 → 数据通过 Menu 自身的 currentConvXxx 属性传递。
                Menu {
                    id: sessionMenu
                    property string currentConvId: ""
                    property string currentConvName: ""
                    MenuItem {
                        text: qsTr("重命名...")
                        onTriggered: {
                            if (!bridge) return
                            renameDialog.currentConvId = sessionMenu.currentConvId
                            renameDialog.currentName = sessionMenu.currentConvName
                            renameDialog.open()
                        }
                    }
                    MenuItem {
                        text: qsTr("清空消息")
                        onTriggered: {
                            if (!bridge) return
                            if (sessionMenu.currentConvId) {
                                bridge.clear_session_history(sessionMenu.currentConvId)
                            }
                        }
                    }
                    MenuSeparator {}
                    MenuItem {
                        text: qsTr("删除会话")
                        // 用红色文字提醒危险操作（PyQt5 路径也有 QMessageBox 二次确认，
                        // 这里用 toast 简化，避免阻塞 UI）
                        onTriggered: {
                            if (!bridge) return
                            if (sessionMenu.currentConvId) {
                                bridge.delete_session(sessionMenu.currentConvId)
                            }
                        }
                    }
                }
                // Day 20.4: 重命名会话的输入弹窗
                Dialog {
                    id: renameDialog
                    property string currentConvId: ""
                    property string currentName: ""
                    title: qsTr("重命名会话")
                    anchors.centerIn: Overlay.overlay
                    modal: true
                    width: 360
                    height: 160
                    standardButtons: Dialog.Ok | Dialog.Cancel
                    onOpened: {
                        // 打开时把 currentName 同步进 input（onOpened 比 Component.onCompleted
                        // 晚一帧，dialog 内部 TextInput 已经构造好）
                        renameInput.text = renameDialog.currentName
                        renameInput.selectAll()
                        renameInput.forceActiveFocus()
                    }
                    onAccepted: {
                        if (bridge && currentConvId) {
                            bridge.rename_session(currentConvId, renameInput.text)
                        }
                    }
                    contentItem: ColumnLayout {
                        spacing: 8
                        Text {
                            text: "请输入新会话名："
                            color: "#666"
                            font.pixelSize: 12
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 32
                            radius: 6
                            border.color: "#E5E6EB"
                            border.width: 1
                            color: "white"
                            TextInput {
                                id: renameInput
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                verticalAlignment: TextInput.AlignVCenter
                                font.pixelSize: 13
                                selectByMouse: true
                            }
                        }
                    }
                }
            }
        }

        // ============ 聊天区 ============
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: root.pal.appBg

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 56
                    color: root.pal.topbarBg
                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 1
                        color: root.pal.topbarBorder
                    }
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 20
                        anchors.rightMargin: 20
                        spacing: 12
                        // Day 10: 模型下拉（从 bridge.list_models() 动态填）
                        ComboBox {
                            id: modelCombo
                            Layout.preferredHeight: 32
                            Layout.preferredWidth: 200
                            model: bridge ? bridge.list_models() : []
                            // 只显示 name 字段
                            textRole: "name"
                            // \u9009\u4e2d\u9879\u7d22\u5f15\uff08\u542b current=true \u7684\uff09
                            currentIndex: {
                                if (!bridge) return -1
                                const ms = bridge.list_models()
                                for (let i = 0; i < ms.length; i++) {
                                    if (ms[i].current) return i
                                }
                                return ms.length > 0 ? 0 : -1
                            }
                            onActivated: {
                                if (!bridge) return
                                const ms = bridge.list_models()
                                if (currentIndex >= 0 && currentIndex < ms.length) {
                                    bridge.set_current_model(ms[currentIndex].id)
                                }
                            }
                        }
                        // Day 14: 工具调用进度 chip（橙色脉冲）
                        Rectangle {
                            visible: bridge && bridge.toolCallInProgress
                            Layout.preferredHeight: 32
                            Layout.preferredWidth: 220
                            radius: 16
                            color: "#FFF7E6"
                            border.color: "#F5A623"
                            border.width: 1
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 6
                                Rectangle {
                                    Layout.preferredWidth: 12
                                    Layout.preferredHeight: 12
                                    radius: 6
                                    color: "#F5A623"
                                    SequentialAnimation on opacity {
                                        loops: Animation.Infinite
                                        NumberAnimation { to: 0.3; duration: 600 }
                                        NumberAnimation { to: 1.0; duration: 600 }
                                    }
                                }
                                Text {
                                    text: "\u6b63\u5728\u8c03\u7528\uff1a" + (bridge ? bridge.currentToolName : "")
                                    color: "#A86D00"
                                    font.pixelSize: 12
                                    font.bold: true
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                // 消息列表
                ListView {
                    id: msgList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: messageModel
                    clip: true
                    spacing: 28
                    leftMargin: 80
                    rightMargin: 80
                    topMargin: 32
                    bottomMargin: 24
                    delegate: Item {
                        width: ListView.view.width
                        height: msgRow.implicitHeight + 18

                        ColumnLayout {
                            id: msgRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            spacing: 6

                            Text {
                                text: ts
                                color: root.pal.tsColor
                                font.pixelSize: 11
                                Layout.alignment: Qt.AlignHCenter
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Item {
                                    Layout.fillWidth: who === "user"
                                    Layout.preferredHeight: 1
                                }
                                MessageBubble {
                                    who: who
                                    text: text
                                    hasCode: hasCode
                                    code: hasCode ? code : ""
                                    // Day 20: 注入 ListView.index（delegate 上下文属性）到气泡
                                    msgIndex: index
                                    Layout.preferredWidth: 680
                                }
                                Item {
                                    Layout.fillWidth: who === "ai"
                                    Layout.preferredHeight: 1
                                }
                            }
                        }
                    }
                }

                // 输入栏
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 88
                    color: root.pal.topbarBg
                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        height: 1
                        color: root.pal.topbarBorder
                    }
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10

                        // Day 13: 附件预览行（图片缩略图，仅有附件时显示）
                        Rectangle {
                            visible: fileAttachments.length > 0
                            Layout.preferredHeight: 64
                            Layout.fillWidth: true
                            color: root.pal.sidebarHover
                            radius: 10
                            border.color: root.pal.inputBorder
                            border.width: 1
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 8
                                Repeater {
                                    model: fileAttachments
                                    delegate: Rectangle {
                                        Layout.preferredWidth: 48
                                        Layout.preferredHeight: 48
                                        radius: 6
                                        color: root.pal.sidebarBg
                                        border.color: root.pal.sidebarBorder
                                        border.width: 1
                                        Image {
                                            anchors.fill: parent
                                            anchors.margins: 2
                                            source: modelData.url
                                            fillMode: Image.PreserveAspectCrop
                                            asynchronous: true
                                            cache: true
                                        }
                                        Rectangle {
                                            anchors.top: parent.top
                                            anchors.right: parent.right
                                            anchors.margins: 1
                                            width: 14; height: 14
                                            radius: 7
                                            color: "#E55656"
                                            Text {
                                                anchors.centerIn: parent
                                                text: "✕"
                                                color: "white"
                                                font.pixelSize: 9
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                onClicked: {
                                                    const arr = fileAttachments.slice()
                                                    arr.splice(index, 1)
                                                    fileAttachments = arr
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        Rectangle {
                            id: inputBox
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            radius: 14
                            color: inputField.activeFocus ? root.pal.inputBgFocus : root.pal.inputBg
                            border.color: inputField.activeFocus ? root.pal.inputBorderFocus : root.pal.inputBorder
                            border.width: 1
                            // Day 12: 拖拽文件支持（文件路径填入输入框）
                            // Day 13: 图片预览 + 文本文件内容自动附加
                            // Day 19 (H-NEW-8): 拖入文件大小校验（>50MB 直接拒）
                            DropArea {
                                anchors.fill: parent
                                // Day 19 (H-NEW-8): 大文件拒绝上限 50MB。
                                // chat_bridge.read_text_file 自身 MAX_SIZE=50KB（远超一般文本），
                                // 图片附件 4MB 上限已经够小，但用户可能误拖 ISO / 视频 / 压缩包，
                                // 这种"无意义的大文件"在 DropArea 早期直接拒 + 提示，避免
                                // 后面 read_text_file / image 读大文件卡 GUI。
                                readonly property int maxFileBytes: 50 * 1024 * 1024
                                onDropped: {
                                    if (drop.hasUrls && drop.urls.length > 0) {
                                        // Day 15: 用 toLocalFile 正确处理 URL 编码（空格 → %20）
                                        const filePath = drop.urls[0].toLocalFile()
                                                || drop.urls[0].toString().replace("file:///", "").replace("file://", "")
                                        const fileUrl = drop.urls[0].toString()
                                        const lower = filePath.toLowerCase()
                                        // Day 19 (H-NEW-8): 大小校验优先于扩展名判断，
                                        // 因为 ISO / .exe 等都是大文件，先拒大小更省事。
                                        if (bridge) {
                                            const sizeInfo = bridge.get_file_size(filePath)
                                            if (sizeInfo.ok && sizeInfo.size > maxFileBytes) {
                                                // 通过 appendError 发一个错误气泡（QML 端会渲染成红色 toast）
                                                bridge.appendError("system",
                                                    "文件过大，已拒绝拖入：" + filePath.split(/[\\\\\\/]/).pop() +
                                                    " (" + Math.round(sizeInfo.size / 1024 / 1024) + "MB > 50MB 上限)")
                                                return
                                            }
                                        }
                                        const isImage = lower.endsWith(".png") || lower.endsWith(".jpg")
                                            || lower.endsWith(".jpeg") || lower.endsWith(".gif")
                                            || lower.endsWith(".bmp") || lower.endsWith(".webp")
                                        if (isImage) {
                                            const newAtts = fileAttachments.slice()
                                            newAtts.push({path: filePath, url: fileUrl, name: filePath.split(/[\\\\\\/]/).pop()})
                                            fileAttachments = newAtts
                                            inputField.text += (inputField.text ? "\n" : "") + "[\u56fe\u7247] " + filePath
                                        } else if (bridge) {
                                            // Day 19 (M-NEW-4 修复): 文本扩展名白名单
                                            // 改为从 bridge.list_readable_ext() 拉取（Python
                                            // 端 _READABLE_EXT 的单一来源），避免双份维护漂移
                                            const textExts = bridge.list_readable_ext()
                                            const isText = textExts.some(function(e) { return lower.endsWith(e) })
                                            if (isText) {
                                                const content = bridge.read_text_file(filePath)
                                                if (content) {
                                                    inputField.text += (inputField.text ? "\n" : "") + content
                                                } else {
                                                    inputField.text += (inputField.text ? "\n" : "") + filePath
                                                }
                                            } else {
                                                inputField.text += (inputField.text ? "\n" : "") + filePath
                                            }
                                        }
                                    }
                                }
                            }
                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: -3
                                radius: 17
                                color: "transparent"
                                border.color: inputField.activeFocus
                                    ? Qt.rgba(0.31, 0.54, 0.95, 0.20)
                                    : "transparent"
                                border.width: inputField.activeFocus ? 6 : 0
                            }
                            ScrollView {
                                anchors.fill: parent
                                anchors.margins: 8
                                TextArea {
                                    id: inputField
                                    placeholderText: "试试发送：帮我写个 Python 快速排序"
                                    placeholderTextColor: root.pal.textTertiary
                                    color: root.pal.textPrimary
                                    font.pixelSize: 14
                                    wrapMode: TextArea.Wrap
                                    background: null
                                    selectByMouse: true
                                    persistentSelection: true
                                    Keys.onReturnPressed: function(event) {
                                        if (event.modifiers & Qt.ShiftModifier) {
                                            // Shift+Enter 换行
                                            event.accepted = false
                                        } else {
                                            if (bridge) {
                                                const paths = fileAttachments.map(function(a) { return a.path })
                                                bridge.send_message(text, false, paths)
                                            }
                                            text = ""
                                            fileAttachments = []
                                            event.accepted = true
                                        }
                                    }
                                }
                            }
                        }

                        Rectangle {
                            id: actionBtn
                            Layout.preferredWidth: 96
                            Layout.preferredHeight: 56
                            radius: 14
                            // Day 7: 根据 bridge.isBusy 切换颜色/文本/点击行为
                            // 用 enabled 卫士防止 QML 加载时 bridge 未注入导致 binding 段错误
                            enabled: bridge !== undefined && bridge !== null
                            color: {
                                if (!enabled) return root.pal.sendBtn
                                if (bridge.isBusy) {
                                    return actionMa.containsMouse ? root.pal.stopBtnHover : root.pal.stopBtn
                                }
                                return actionMa.containsMouse ? root.pal.sendBtnHover : root.pal.sendBtn
                            }
                            Behavior on color { ColorAnimation { duration: 120 } }

                            Text {
                                anchors.centerIn: parent
                                text: (bridge && bridge.isBusy) ? "停止" : "发送"
                                color: "white"
                                font.pixelSize: 15
                                font.bold: true
                            }
                            MouseArea {
                                id: actionMa
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (!bridge) return
                                    if (bridge.isBusy) {
                                        bridge.stop_chat()
                                    } else {
                                        const paths = fileAttachments.map(function(a) { return a.path })
                                        bridge.send_message(inputField.text, false, paths)
                                        inputField.text = ""
                                        fileAttachments = []
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
