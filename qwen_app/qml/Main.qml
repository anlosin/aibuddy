import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1100
    height: 760
    title: "aibuddy"

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
    //
    // Day 20.6: MenuBar/Menu/MenuItem 默认走系统 Fusion 风格 —— Windows
    // 上始终浅色背景 + 黑色文字，暗黑模式下菜单栏「不变」。统一 background
    // 绑色板（topbarBg/textPrimary），MenuItem hover/selected 也走 sidebarHover
    // / sidebarSel，避免系统风格盖住我们的暗色调。
    menuBar: MenuBar {
        background: Rectangle {
            color: root.pal.topbarBg
            border.color: root.pal.topbarBorder
            border.width: 0
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: root.pal.topbarBorder
            }
        }
        delegate: MenuBarItem {
            id: mbi
            // Day 20.6.2: **只覆盖 background，不动 contentItem 也不覆盖
            // palette 角色**。前两版分别踩坑：
            //   ① 上一版（Day 20.6）override contentItem: Text → Text 不处理
            //      '&' 助记符，菜单前多个 & 符号
            //   ② Day 20.6.1 加 palette.text/highlight/highlightedText → palette
            //      角色覆盖把 MenuBarItem 的 hover/press 状态打乱了，整个菜单
            //      栏点不动
            //
            // 现在仅覆盖 background 绑色板（默认 contentItem + 默认 palette
            // 完整保留）。暗色模式下 Fusion 的 MenuBarItem 默认文字色仍是
            // 黑色（系统 palette），但**菜单栏能点**是基本要求 —— 文字不可见
            // 是次要问题，可通过给 QApplication 设暗色 palette（main.py）解决。
            background: Rectangle {
                color: mbi.highlighted ? root.pal.sidebarHover : "transparent"
            }
        }
        // Day 20.6: 所有下拉子菜单（Popup）也要绑色板，否则 MenuItem 还是
        // 系统 Fusion 浅色（白底黑字），暗黑模式下一片惨白刺眼。Menu 设
        // background，MenuItem 用 default delegate 覆盖 hover/selected 色。
        Menu {
            id: fileMenu
            title: qsTr("&文件")
            background: Rectangle { color: root.pal.topbarBg; border.color: root.pal.topbarBorder }
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
            id: editMenu
            title: qsTr("&编辑")
            background: Rectangle { color: root.pal.topbarBg; border.color: root.pal.topbarBorder }
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
            id: viewMenu
            title: qsTr("&视图")
            background: Rectangle { color: root.pal.topbarBg; border.color: root.pal.topbarBorder }
            MenuItem {
                text: qsTr("切换主题\tCtrl+T")
                enabled: bridge !== undefined && bridge !== null
                onTriggered: if (bridge) bridge.set_theme(root.themeName === "dark" ? "light" : "dark")
            }
        }
        Menu {
            id: toolsMenu
            title: qsTr("&工具")
            background: Rectangle { color: root.pal.topbarBg; border.color: root.pal.topbarBorder }
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
            id: helpMenu
            title: qsTr("&帮助")
            background: Rectangle { color: root.pal.topbarBg; border.color: root.pal.topbarBorder }
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
    // Day 20.4.5：**不能无脑 clear()+append()**。原因有两条：
    //   ① 全量重建会给 ListView 一次 model reset，容易把用户的滚动位置搞乱
    //      （用户报"点了另一个会话后左侧列表自己跳回最顶"）；
    //   ② 点会话只是「选中态」变了，没必要重建 185 个 delegate。
    // 所以：**会话集合没变（同一批 id、同一顺序）时只就地更新 sel/name/time**；
    // 只有真的新增/删除会话才重建，并在重建后恢复原来的滚动位置。
    function refreshConvList() {
        if (!bridge) return
        const sessions = bridge.list_sessions()
        var sameShape = (convModel.count === sessions.length)
        if (sameShape) {
            for (var k = 0; k < sessions.length; k++) {
                if (convModel.get(k).convId !== sessions[k].id) {
                    sameShape = false
                    break
                }
            }
        }
        if (sameShape) {
            // 就地更新：不 clear → 不触发 model reset → 滚动位置稳稳不动
            for (var i = 0; i < sessions.length; i++) {
                const s = sessions[i]
                convModel.set(i, { "name": s.name, "time": s.time, "sel": s.sel })
            }
            return
        }
        // 集合变了（新建 / 删除 / 顺序变化）→ 必须重建，但要找回滚动位置
        var keepY = convListView.contentY
        convModel.clear()
        for (var j = 0; j < sessions.length; j++) {
            const sj = sessions[j]
            convModel.append({
                "convId": sj.id,
                "name": sj.name,
                "time": sj.time,
                "sel": sj.sel
            })
        }
        // 内容高度要等一帧才更新，所以延后恢复并夹到合法范围
        Qt.callLater(function() {
            var maxY = Math.max(0, convListView.contentHeight - convListView.height)
            convListView.contentY = Math.max(0, Math.min(keepY, maxY))
        })
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
                    // Day 20.6: 暗黑模式「白字 + transparent 底」不显眼，背景色绑
                    // inputBorder 让亮/暗模式都能看见，hover 提亮为 sendBtn
                    Rectangle {
                        width: 32; height: 32; radius: 16
                        color: themeMa.containsMouse ? root.pal.sendBtn : root.pal.inputBg
                        border.color: themeMa.containsMouse ? root.pal.sendBtnHover : root.pal.inputBorder
                        border.width: 1
                        Text {
                            anchors.centerIn: parent
                            text: root.themeName === "dark" ? "☀️" : "🌙"
                            font.pixelSize: 14
                            // 亮模式底浅+字深色；暗模式 hover 蓝底用白字
                            color: themeMa.containsMouse
                                ? "#FFFFFF"
                                : root.pal.textPrimary
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
                                // Day 20.4.4 修复：原来 visible: rowMa.containsMouse || model.sel
                                // 是个「抖动陷阱」——鼠标从行体移到 ⋯ 上时，hover 从 rowMa
                                // 切给 moreMa，rowMa.containsMouse 立刻变 false → 按钮变不可见
                                // → 鼠标底下没东西了 → hover 回到 rowMa → 按钮又出现……
                                // 闪烁循环，用户永远点不中（点到的都是 rowMa → load_session）。
                                // 跟 MessageBubble 里 moreBtn 的结论一致（Day 20.3 已改为常显）。
                                visible: true
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
                                        // 3) 定位（实测总结，别再改回去）：
                                        //    a) parent 必须是 Overlay —— 否则 Qt 会自己重排
                                        //       把 x/y 覆盖掉（实测设 46,62 → 888,448）。
                                        //    b) **必须从按钮自身 mapToItem 取坐标**，
                                        //       不要再用 `index * rowStride - contentY` 反推！
                                        //       Day 20.4.5 实测：ListView 在深层滚动后
                                        //       delegate 的 `index` 不可靠 —— 同一个
                                        //       delegate.y=11036（=178 行）却报 index=3，
                                        //       `indexAt()` 也返回 3 → 反推出来的 rowTop
                                        //       是 -10534（大负数）→ 被钳到 y=0，
                                        //       表现就是"菜单跑到列表最顶上"。
                                        //       而 `moreBtn.mapToItem(ov, ...)` 在同一时刻
                                        //       给出的坐标是正确的（实测 viewport 155 →
                                        //       scene 334，与按钮实际位置完全一致）。
                                        //    c) 点击处理是同步的（popup + 设坐标在同一帧内
                                        //       完成），所以不存在"delegate 被回收导致
                                        //       瞬时位移"的窗口。
                                        var ov = Overlay.overlay
                                        var bl = moreBtn.mapToItem(ov, 0, moreBtn.height + 2)
                                        var mx = Math.max(4, Math.min(
                                            bl.x + moreBtn.width - sessionMenu.width,
                                            ov.width - sessionMenu.width - 4))
                                        var my = Math.max(4, Math.min(
                                            bl.y, ov.height - sessionMenu.height - 4))
                                        // popup() 内部会按自己的规则重排一次位置，把我们
                                        // 事先设好的 x/y 覆盖掉；所以顺序必须是
                                        // 「先 popup()，再 x/y」。
                                        sessionMenu.popup()
                                        sessionMenu.x = mx
                                        sessionMenu.y = my
                                    }
                                }
                            }
                        }
                        // Day 20.4.4 关键修复：rowMa 虽然源码里写在 RowLayout 之后，
                        // 但 QML 命中测试按「绘制顺序的逆序」——同一父项内 z 相同时
                        // **后声明者在上**。rowMa 覆盖整个 delegate 且最后声明，
                        // 于是永远先拿到 click，把 RowLayout（内含 moreBtn）的点击吞掉
                        // → 三点按钮永远点不到（点了没反应 / 没有菜单）。
                        // moreBtn 上的 z:10 只在 RowLayout **内部**相对兄弟生效，
                        // 对 rowMa 完全无效。
                        // 修复：把 rowMa 压到 RowLayout 之下（z:-1 < RowLayout 的 z:0），
                        // 让 RowLayout 子树先参与命中测试；文本区不是 MouseArea，
                        // 事件会自然落回 rowMa。
                        MouseArea {
                            id: rowMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            z: -1
                            onClicked: if (bridge) bridge.load_session(model.convId)
                        }
                    }
                }
                // Day 20.4: 侧边栏会话三点菜单（重命名 / 清空消息 / 删除）
                // 跟 MessageBubble.qml 的 bubbleMenu 一样，跨 Popup window scope
                // 不可靠 → 数据通过 Menu 自身的 currentConvXxx 属性传递。
                Menu {
                    id: sessionMenu
                    // Day 20.4.4: 只有 parent = Overlay 时 Qt 才「尊重」我们设的 x/y。
                    // parent 是普通 Item（行 / delegate / ListView）时 Qt 会自己重排位置，
                    // 把 x/y 覆盖掉（实测设 46,62 → 实际 888,448，弹到窗口右下角）。
                    // 所以 parent 固定为 Overlay，x/y 用 Overlay 坐标（见 onClicked）。
                    parent: Overlay.overlay
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
                                // Day 20.4.4 关键修复：必须写 model.who / model.text！
                                // MessageBubble **自身**有 who / text / hasCode 属性，
                                // 写 `who: who` 时 RHS 会被 QML 解析成 MessageBubble
                                // 自己的属性（作用域遮蔽：对象自身属性优先于 delegate
                                // 的 model role）→ 变成自绑定 → 永远停在默认值
                                // who="ai" / text="" / hasCode=false。
                                // 现象：所有气泡都渲染成 AI 白气泡，且正文一个字都没有。
                                // 对照：`text: ts` 没事，因为 Text 没有 ts 属性。
                                MessageBubble {
                                    who: model.who
                                    text: model.text
                                    hasCode: model.hasCode
                                    code: model.hasCode ? model.code : ""
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
