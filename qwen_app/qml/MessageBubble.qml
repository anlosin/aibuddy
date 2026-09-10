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
}
