import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// 消息气泡组件 — 含头像、正文、代码段
Rectangle {
    id: bubble
    property string who: "ai"          // "user" | "ai"
    property string text: ""
    property bool hasCode: false
    property string code: ""

    readonly property color fillColor: who === "user" ? "#4F8AF7" : "#FFFFFF"
    readonly property color borderColor: who === "user" ? "transparent" : "#E5E7EB"
    readonly property color textColor: who === "user" ? "#FFFFFF" : "#1F1F1F"
    readonly property color codeBg: who === "user" ? Qt.rgba(1, 1, 1, 0.18) : "#F2F3F5"
    readonly property color codeFg: who === "user" ? "#FFFFFF" : "#1F1F1F"
    readonly property color avatarBg: who === "user" ? "#23B36B" : "#4E6EF2"
    readonly property string avatarIcon: who === "user" ? "🧑" : "🤖"
    readonly property int radiusVal: 16

    color: fillColor
    radius: radiusVal
    border.color: borderColor
    border.width: who === "user" ? 0 : 1
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
        border.color: bubble.who === "user"
            ? Qt.rgba(0.31, 0.54, 0.95, 0.18)
            : Qt.rgba(0, 0, 0, 0.08)
        border.width: 1
    }

    RowLayout {
        id: contentRow
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        // 头像（用户/AI 都显示）
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

        // 文本列（正文 + 可选代码段）
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                visible: bubble.text.length > 0
                text: bubble.text
                color:bubble.textColor
                font.pixelSize: 15
                font.family: "Microsoft YaHei"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
                textFormat: Text.RichText
            }
            // 代码段
            Rectangle {
                visible: bubble.hasCode
                Layout.fillWidth: true
                radius: 8
                color:bubble.codeBg
                implicitHeight: codeText.implicitHeight + 16
                Text {
                    id: codeText
                    anchors.fill: parent
                    anchors.margins: 8
                    text: bubble.code
                    color:bubble.codeFg
                    font.family: "Consolas, Courier New, monospace"
                    font.pixelSize: 13
                    wrapMode: Text.Wrap
                    textFormat: Text.RichText
                }
            }
        }
    }
}
