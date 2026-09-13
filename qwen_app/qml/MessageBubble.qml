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

    // ===== Day 20: 三点按钮 + 操作菜单（hover 时显示）=====
    // 位置：user 气泡左上角 / 其他气泡右上角（避开头像）
    Rectangle {
        id: moreBtn
        width: 22
        height: 22
        radius: 11
        // user 气泡左上角，其他右上角（user 头像已在左侧不会冲突）
        anchors.top: parent.top
        anchors.right: bubble.isUser ? undefined : parent.right
        anchors.left: bubble.isUser ? parent.left : undefined
        anchors.topMargin: 4
        anchors.rightMargin: 4
        anchors.leftMargin: 4
        // 头像不挡时永远显示；user 气泡头像挡则隐藏
        visible: bubble.isUser ? false : true
        // Day 19.1 修复（类比 drop bug 教训）：默认 opacity=1.0 永远可见。
        // hover/opened 时变深（仍然有视觉反馈），但不再依赖 hover 才出现。
        opacity: moreMa.containsMouse || bubbleMenu.opened ? 1.0 : 0.7
        Behavior on opacity { NumberAnimation { duration: 150 } }
        color: moreMa.containsMouse ? Qt.rgba(0, 0, 0, 0.10) : Qt.rgba(0, 0, 0, 0.05)
        z: 5
        Text {
            anchors.centerIn: parent
            text: "\u22ef"  // ⋯
            color: bubble.textColor
            opacity: 0.7
            font.pixelSize: 16
            font.bold: true
        }
        MouseArea {
            id: moreMa
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            // 防止冒泡触发 ListView 点击行为
            onClicked: {
                bubbleMenu.popup()
                // 阻止事件传到外层 MouseArea
            }
        }
    }

    // 三点菜单（按 who 类型自适应）
    Menu {
        id: bubbleMenu
        // 弹出位置：三点按钮正下方（避免菜单覆盖气泡）
        y: moreBtn.y + moreBtn.height + 2
        x: bubble.isUser ? moreBtn.x : (moreBtn.x + moreBtn.width - width)
        // 普通复制（user / ai / tool_* 都给）
        MenuItem {
            text: qsTr("复制")
            onTriggered: if (bridge) bridge.copy_to_clipboard(bubble.text || "")
        }
        // 复制代码（仅当 hasCode）
        MenuItem {
            text: qsTr("复制代码")
            visible: bubble.hasCode
            onTriggered: if (bridge) bridge.copy_code(bubble.code || "")
        }
        MenuSeparator { visible: bubble.hasCode }
        // 引用回复（user / ai；tool_* 不引用，太冗余）
        MenuItem {
            text: qsTr("引用回复")
            visible: !bubble.isTool
            onTriggered: {
                if (!bridge) return
                // 取 bubble.text 的前 80 字（防过长）
                var q = (bubble.text || "").substring(0, 80)
                if ((bubble.text || "").length > 80) q += "..."
                bridge.quote_reply("", q)
            }
        }
        // 朗读（user / ai；tool_* 不朗读）
        MenuItem {
            text: qsTr("朗读")
            visible: !bubble.isTool
            onTriggered: if (bridge) bridge.speak_text(bubble.text || "")
        }
        MenuSeparator { visible: bubble.isUser || bubble.who === "ai" }
        // 编辑（仅 user）
        MenuItem {
            text: qsTr("编辑...")
            visible: bubble.isUser
            onTriggered: {
                if (!bridge) return
                // 简化：用 MessageDialog 输入新内容
                editDialog.currentText = bubble.text || ""
                editDialog.targetIndex = bubble.msgIndex
                editDialog.open()
            }
        }
        // 重新生成（仅 ai）
        MenuItem {
            text: qsTr("重新生成")
            visible: bubble.who === "ai"
            enabled: bridge !== undefined && bridge !== null && bridge.isBusy === false
            onTriggered: {
                if (!bridge) return
                bridge.regenerate_ai_response(bubble.msgIndex)
            }
        }
        MenuSeparator {}
        // 分享导出（所有类型）
        MenuItem {
            text: qsTr("分享导出")
            onTriggered: if (bridge) bridge.share_bubble(bubble.msgIndex)
        }
        // 删除（所有类型，但 tool_* 很少单独删，统一给）
        MenuItem {
            text: qsTr("删除")
            onTriggered: if (bridge) bridge.delete_bubble(bubble.msgIndex)
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
