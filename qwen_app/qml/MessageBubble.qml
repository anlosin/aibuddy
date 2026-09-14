import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// 消息气泡组件 — 支持 user / ai / tool_call / tool_result 四种类型
Rectangle {
    id: bubble
    property string who: "ai"          // "user" | "ai" | "tool_call" | "tool_result"
    property string text: ""
    property bool hasCode: false
    property string code: ""
    // Day 20: 气泡在 messageModel 中的索引（Main.qml delegate 通过 ListView.index 注入）
    // 三点菜单的操作需要这个索引来定位要改哪条 history
    property int msgIndex: -1

    readonly property bool isUser: who === "user"
    readonly property bool isToolCall: who === "tool_call"
    readonly property bool isToolResult: who === "tool_result"
    readonly property bool isTool: isToolCall || isToolResult

    readonly property color fillColor: isUser
        ? "#4F8AF7"
        : (isToolCall ? "#FFF7E6" : (isToolResult ? "#F0F9F4" : "#FFFFFF"))
    readonly property color borderColor: isUser
        ? "transparent"
        : (isToolCall ? "#F5C36F" : (isToolResult ? "#7DC998" : "#E5E7EB"))
    readonly property color textColor: isUser ? "#FFFFFF" : "#1F1F1F"
    readonly property color codeBg: isUser ? Qt.rgba(1, 1, 1, 0.18) : "#F2F3F5"
    readonly property color codeFg: isUser ? "#FFFFFF" : "#1F1F1F"
    readonly property color avatarBg: isUser
        ? "#23B36B"
        : (isToolCall ? "#F5A623" : (isToolResult ? "#23B36B" : "#4E6EF2"))
    readonly property string avatarIcon: isUser
        ? "\ud83e\uddd1"
        : (isToolCall ? "\ud83d\udd27" : (isToolResult ? "\ud83d\udcca" : "\ud83e\udd16"))
    readonly property int radiusVal: 16

    color: fillColor
    radius: radiusVal
    border.color: borderColor
    border.width: isUser ? 0 : 1
    implicitWidth: contentRow.implicitWidth + 28
    implicitHeight: contentRow.implicitHeight + 24

    // 真阴影（叠层 Rectangle 模拟，GPU 渲染柔和）
    Rectangle {
        anchors.fill: parent
        anchors.topMargin: 1
        anchors.bottomMargin: -3
        anchors.leftMargin: 0
        anchors.rightMargin: 0
        z: -1
        radius: bubble.radiusVal + 1
        color: "transparent"
        border.color: bubble.isUser
            ? Qt.rgba(0.31, 0.54, 0.95, 0.18)
            : Qt.rgba(0, 0, 0, 0.08)
        border.width: 1
    }

    RowLayout {
        id: contentRow
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        // 头像
        Rectangle {
            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            radius: 16
            color: bubble.avatarBg
            Text {
                anchors.centerIn: parent
                text: bubble.avatarIcon
                font.pixelSize: 16
            }
        }

        // 文本列
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            // 标题（工具名 / 角色）
            Text {
                visible: bubble.text.length > 0
                text: bubble.text
                color: bubble.textColor
                font.pixelSize: bubble.isTool ? 14 : 15
                font.family: "Microsoft YaHei"
                font.bold: bubble.isTool
                wrapMode: Text.Wrap
                Layout.fillWidth: true
                textFormat: bubble.isTool ? Text.PlainText : Text.RichText
            }
            // 参数 / 结果（code 字段）
            Rectangle {
                visible: bubble.hasCode
                Layout.fillWidth: true
                radius: 8
                color: bubble.codeBg
                implicitHeight: codeText.implicitHeight + 16
                Text {
                    id: codeText
                    anchors.fill: parent
                    anchors.margins: 8
                    text: bubble.code
                    color: bubble.codeFg
                    font.family: bubble.isTool ? "Consolas, Courier New, monospace" : "Microsoft YaHei"
                    font.pixelSize: bubble.isTool ? 12 : 13
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }
            }
        }
    }

    // ===== Day 20: 三点按钮 + 操作菜单 =====
    // 位置：user 气泡左上角 / 其他气泡右上角（避开头像）
    // Day 20.3 修复（user 反馈"点击无反应"）：
    //   1) opacity 永久 1.0（原 0.7 太淡，hover 才 1.0，用户看不见）
    //   2) MenuItem 跨 Popup 窗口 scope 失效：直接引用 bubble.text / bubble.msgIndex
    //      在 Menu 弹出后可能为 undefined（Menu 是独立 Popup window）。改为
    //      把气泡数据先缓存到 Menu 的 currentMsgXxx 属性，MenuItem 只读这些属性。
    //   3) popup() 用 mapToItem(null, ...) 映射到屏幕绝对坐标，避免 delegate
    //      本地坐标让 Menu 弹到不可见位置
    Rectangle {
        id: moreBtn
        width: 22
        height: 22
        radius: 11
        anchors.top: parent.top
        anchors.right: bubble.isUser ? undefined : parent.right
        anchors.left: bubble.isUser ? parent.left : undefined
        anchors.topMargin: 4
        anchors.rightMargin: 4
        anchors.leftMargin: 4
        // Day 20.3: 全部气泡都显示三点（user 气泡头像在左侧不冲突，位置改左上）
        visible: true
        // 永远 opacity 1.0；hover 时背景变深做视觉反馈
        opacity: 1.0
        color: moreMa.containsMouse || bubbleMenu.opened
            ? Qt.rgba(0, 0, 0, 0.15)
            : Qt.rgba(0, 0, 0, 0.06)
        z: 5
        Text {
            anchors.centerIn: parent
            text: "\u22ef"  // ⋯
            color: bubble.textColor
            opacity: 0.8
            font.pixelSize: 16
            font.bold: true
        }
        MouseArea {
            id: moreMa
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                // Day 20.3: 先把气泡数据缓存到 Menu 属性，避开跨 Popup window
                // scope 引用 bubble.* 失效的问题
                bubbleMenu.currentMsgText = bubble.text || ""
                bubbleMenu.currentMsgCode = bubble.code || ""
                bubbleMenu.currentMsgWho = bubble.who
                bubbleMenu.currentMsgIndex = bubble.msgIndex
                bubbleMenu.currentMsgHasCode = bubble.hasCode
                // 屏幕绝对坐标：moreBtn 右下角对齐菜单右边缘，避免菜单超出可见区
                var pt = moreBtn.mapToItem(null, moreBtn.width, moreBtn.height + 2)
                bubbleMenu.x = pt.x - bubbleMenu.width + moreBtn.width
                bubbleMenu.y = pt.y
                bubbleMenu.popup()
            }
        }
    }

    // 三点菜单（按 who 类型自适应）
    // Day 20.3: 所有数据通过 Menu 自身的 currentMsgXxx 属性传递，MenuItem 不再
    // 直接引用 bubble.*（跨 Popup window scope 不可靠）。
    Menu {
        id: bubbleMenu
        property string currentMsgText: ""
        property string currentMsgCode: ""
        property string currentMsgWho: ""
        property int currentMsgIndex: -1
        property bool currentMsgHasCode: false

        // 普通复制（user / ai / tool_* 都给）
        MenuItem {
            text: qsTr("复制")
            onTriggered: if (bridge) bridge.copy_to_clipboard(bubbleMenu.currentMsgText)
        }
        // 复制代码（仅当 hasCode）
        MenuItem {
            text: qsTr("复制代码")
            visible: bubbleMenu.currentMsgHasCode
            onTriggered: if (bridge) bridge.copy_code(bubbleMenu.currentMsgCode)
        }
        MenuSeparator { visible: bubbleMenu.currentMsgHasCode }
        // 引用回复（user / ai；tool_* 不引用，太冗余）
        MenuItem {
            text: qsTr("引用回复")
            visible: bubbleMenu.currentMsgWho !== "tool_call"
                 && bubbleMenu.currentMsgWho !== "tool_result"
            onTriggered: {
                if (!bridge) return
                var q = bubbleMenu.currentMsgText.substring(0, 80)
                if (bubbleMenu.currentMsgText.length > 80) q += "..."
                bridge.quote_reply("", q)
            }
        }
        // 朗读（user / ai；tool_* 不朗读）
        MenuItem {
            text: qsTr("朗读")
            visible: bubbleMenu.currentMsgWho !== "tool_call"
                 && bubbleMenu.currentMsgWho !== "tool_result"
            onTriggered: if (bridge) bridge.speak_text(bubbleMenu.currentMsgText)
        }
        MenuSeparator {
            visible: bubbleMenu.currentMsgWho === "user"
                  || bubbleMenu.currentMsgWho === "ai"
        }
        // 编辑（仅 user）
        MenuItem {
            text: qsTr("编辑...")
            visible: bubbleMenu.currentMsgWho === "user"
            onTriggered: {
                if (!bridge) return
                editDialog.currentText = bubbleMenu.currentMsgText
                editDialog.targetIndex = bubbleMenu.currentMsgIndex
                editDialog.open()
            }
        }
        // 重新生成（仅 ai）
        MenuItem {
            text: qsTr("重新生成")
            visible: bubbleMenu.currentMsgWho === "ai"
            enabled: bridge !== undefined && bridge !== null && bridge.isBusy === false
            onTriggered: if (bridge) bridge.regenerate_ai_response(bubbleMenu.currentMsgIndex)
        }
        MenuSeparator {}
        // 分享导出（所有类型）
        MenuItem {
            text: qsTr("分享导出")
            onTriggered: if (bridge) bridge.share_bubble(bubbleMenu.currentMsgIndex)
        }
        // 删除（所有类型，但 tool_* 很少单独删，统一给）
        MenuItem {
            text: qsTr("删除")
            onTriggered: if (bridge) bridge.delete_bubble(bubbleMenu.currentMsgIndex)
        }
    }

    // ===== Day 20: 编辑用户消息的输入弹窗 =====
    Dialog {
        id: editDialog
        property string currentText: ""
        property int targetIndex: -1
        title: qsTr("编辑消息")
        anchors.centerIn: Overlay.overlay
        modal: true
        width: 480
        height: 240
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: {
            if (bridge && targetIndex >= 0) {
                bridge.edit_user_message(targetIndex, editArea.text)
            }
            targetIndex = -1
        }
        onRejected: {
            targetIndex = -1
        }
        contentItem: ColumnLayout {
            spacing: 8
            Text {
                text: "修改消息内容（保存后其后的 AI 回复会被丢弃）："
                font.pixelSize: 12
                color: "#666"
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 6
                border.color: "#E5E6EB"
                border.width: 1
                color: "white"
                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 4
                    TextArea {
                        id: editArea
                        text: editDialog.currentText
                        wrapMode: TextArea.Wrap
                        selectByMouse: true
                    }
                }
            }
        }
    }
}
