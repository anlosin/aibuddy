import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1100
    height: 760
    title: "aibuddy (QtQuick Day 1-2 Demo)"

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

    // Day 13: 主题切换全局颜色过渡动画
    Behavior on color {
        ColorAnimation { duration: 220; easing.type: Easing.OutCubic }
    }

    // ===== 消息列表（动态，bridge 信号驱动）=====
    ListModel {
        id: messageModel
    }

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

    // ===== 监听 bridge的信号 → 推入messageModel =====
    Connections {
        target: bridge
        enabled:bridge !== null
        // 完整新消息
        function onMessageAdded(who, text, ts, hasCode, code) {
            messageModel.append({
                "who": who,
                "text": text,
                "ts": ts,
                "hasCode": hasCode,
                "code": code
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
    }

    // ============ Day 12: 全局快捷键 ============
    Shortcut {
        sequence: "Esc"
        enabled: bridge !== undefined && bridge !== null && bridge.isBusy
        onActivated: if (bridge) bridge.stop_chat()
    }
    Shortcut {
        sequence: "Ctrl+L"
        onActivated: messageModel.clear()
    }
    Shortcut {
        sequence: "Ctrl+T"
        onActivated: if (bridge) bridge.set_theme(root.themeName === "dark" ? "light" : "dark")
    }
    Shortcut {
        sequence: "Ctrl+N"
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
                            Rectangle {
                                Layout.preferredWidth: 24
                                Layout.preferredHeight: 24
                                radius: 4
                                color: moreMa.containsMouse ? root.pal.sidebarBorder : "transparent"
                                visible: rowMa.containsMouse || model.sel
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
                                    onClicked: if (bridge) bridge.delete_session(model.convId)
                                }
                            }
                        }
                        MouseArea {
                            id: rowMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: if (bridge) bridge.load_session(model.convId)
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
                            visible: fileDrop.attachments.length > 0
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
                                    model: fileDrop.attachments
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
                                                    const arr = fileDrop.attachments.slice()
                                                    arr.splice(index, 1)
                                                    fileDrop.attachments = arr
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
                            DropArea {
                                id: fileDrop
                                anchors.fill: parent
                                property var attachments: []
                                onDropped: {
                                    if (drop.hasUrls && drop.urls.length > 0) {
                                        // Day 15: 用 toLocalFile 正确处理 URL 编码（空格 → %20）
                                        const filePath = drop.urls[0].toLocalFile()
                                                || drop.urls[0].toString().replace("file:///", "").replace("file://", "")
                                        const fileUrl = drop.urls[0].toString()
                                        const lower = filePath.toLowerCase()
                                        const isImage = lower.endsWith(".png") || lower.endsWith(".jpg")
                                            || lower.endsWith(".jpeg") || lower.endsWith(".gif")
                                            || lower.endsWith(".bmp") || lower.endsWith(".webp")
                                        if (isImage) {
                                            const newAtts = fileDrop.attachments.slice()
                                            newAtts.push({path: filePath, url: fileUrl, name: filePath.split(/[\\\\\\/]/).pop()})
                                            fileDrop.attachments = newAtts
                                            inputField.text += (inputField.text ? "\n" : "") + "[\u56fe\u7247] " + filePath
                                        } else if (bridge) {
                                            const textExts = [".txt", ".md", ".py", ".js", ".ts", ".json", ".log",
                                                               ".html", ".css", ".sh", ".yml", ".yaml", ".xml",
                                                               ".sql", ".java", ".go", ".rs", ".cpp", ".c", ".h"]
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
                                                const paths = fileDrop.attachments.map(function(a) { return a.path })
                                                bridge.send_message(text, false, paths)
                                            }
                                            text = ""
                                            fileDrop.attachments = []
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
                                        const paths = fileDrop.attachments.map(function(a) { return a.path })
                                        bridge.send_message(inputField.text, false, paths)
                                        inputField.text = ""
                                        fileDrop.attachments = []
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
