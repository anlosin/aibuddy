import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1100
    height: 760
    title: "aibuddy (QtQuick Demo)"

    // 主题切换：light / dark
    property string themeName: "light"

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
            "textPrimary": "#1F1F1F",
            "textSecondary": "#8A8F99",
            "textTertiary": "#B0B4BD",
            "topbarBg": "#FFFFFF",
            "topbarBorder": "#ECEEF2",
            "avatarAi": "#4E6EF2",
            "avatarUser": "#23B36B",
            "tsColor": "#B0B4BD",
            "shadow": "#1A000000",       // ARGB 10% 黑
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
            "textPrimary": "#E8E8E8",
            "textSecondary": "#888C97",
            "textTertiary": "#5A5E68",
            "topbarBg": "#15161A",
            "topbarBorder": "#22232A",
            "avatarAi": "#4E6EF2",
            "avatarUser": "#23B36B",
            "tsColor": "#5A5E68",
            "shadow": "#66000000",       // ARGB 40% 黑
        }
    })[themeName]

    // mock 数据：会话列表
    ListModel {
        id: convModel
        ListElement { name: "Python 快速排序"; time: "10:01"; sel: true }
        ListElement { name: "SQL 联表查询"; time: "09:30" }
        ListElement { name: "Code Review: scheduler.py"; time: "昨天" }
        ListElement { name: "Git 撤销 push"; time: "周一" }
        ListElement { name: "PyQt5 vs QtQuick 调研"; time: "上周" }
        ListElement { name: "keyring 凭据方案"; time: "上周" }
        ListElement { name: "项目部署到 Kubernetes"; time: "08-21" }
    }

    // mock 数据：消息列表（包含代码段、用户/AI 混排）
    ListModel {
        id: messageModel
        ListElement { who: "user"; text: "帮我写个 Python 快速排序"; ts: "10:00"; hasCode: false }
        ListElement {
            who: "ai"
            text: "好的，这是标准实现，时间复杂度 O(n log n)："
            ts: "10:00"
            hasCode: true
            code: "def quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[0]\n    left = [x for x in arr[1:] if x < pivot]\n    right = [x for x in arr[1:] if x >= pivot]\n    return quicksort(left) + [pivot] + quicksort(right)"
        }
        ListElement { who: "user"; text: "能用一行实现吗？"; ts: "10:01"; hasCode: false }
        ListElement {
            who: "ai"
            text: "可以，但可读性会差："
            ts: "10:01"
            hasCode: true
            code: "qs = lambda a: a and (qs([x for x in a[1:] if x < a[0]]) + [a[0]] + qs([x for x in a[1:] if x >= a[0]])) or []"
        }
        ListElement { who: "user"; text: "如果是浮点数且要稳定排序呢？"; ts: "10:02"; hasCode: false }
        ListElement {
            who: "ai"
            text: "稳定排序建议用归并排序，或者在快排基础上加索引："
            ts: "10:02"
            hasCode: true
            code: "# 给每个元素附加原始下标，归并后再丢掉\ndata = [(v, i) for i, v in enumerate(arr)]\ndata.sort(key=lambda x: x[0])  # Python sort 是稳定的\nreturn [v for v, _ in data]"
        }
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

            // 右侧 1px 分隔线
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

                // 顶部 logo + 主题切换
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
                    // 主题切换
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
                            onClicked: root.themeName = root.themeName === "dark" ? "light" : "dark"
                        }
                    }
                }

                // "新建对话" 按钮
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
                    }
                }

                // 标题
                Text {
                    text: "最近对话"
                    color: root.pal.textTertiary
                    font.pixelSize: 11
                    font.letterSpacing: 0.5
                    Layout.topMargin: 16
                    Layout.bottomMargin: 4
                    Layout.leftMargin: 10
                }

                // 会话列表
                ListView {
                    id: convList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: convModel
                    clip: true
                    spacing: 2
                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 60
                        radius: 10
                        color: model.sel ? root.pal.sidebarSel
                            : (rowMa.containsMouse ? root.pal.sidebarHover : "transparent")

                        // 选中态：左侧 3px 蓝色指示条
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
                            // 更多操作按钮
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
                                }
                            }
                        }
                        MouseArea {
                            id: rowMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
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

                // 顶栏
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
                        // 模型/专家切换
                        Rectangle {
                            Layout.preferredHeight: 32
                            Layout.preferredWidth: 200
                            radius: 8
                            color: modelMa.containsMouse ? root.pal.sidebarHover : "transparent"
                            border.color: root.pal.inputBorder
                            border.width: 1
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 6
                                Text {
                                    text: "🤖"
                                    font.pixelSize: 14
                                }
                                Text {
                                    text: "Qwen-Max"
                                    color: root.pal.textPrimary
                                    font.pixelSize: 13
                                    font.bold: true
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: "▾"
                                    color: root.pal.textTertiary
                                    font.pixelSize: 10
                                }
                            }
                            MouseArea {
                                id: modelMa
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                        Item { Layout.fillWidth: true }
                        // 工具按钮组
                        Row {
                            spacing: 4
                            Repeater {
                                model: [
                                    {icon: "🔧", tip: "插件管理"},
                                    {icon: "⏰", tip: "自动化任务"},
                                    {icon: "⚙", tip: "偏好设置"}
                                ]
                                delegate: Rectangle {
                                    width: 32; height: 32; radius: 8
                                    color: toolMa.containsMouse ? root.pal.sidebarHover : "transparent"
                                    Text {
                                        anchors.centerIn: parent
                                        text: modelData.icon
                                        font.pixelSize: 14
                                    }
                                    MouseArea {
                                        id: toolMa
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        ToolTip.text: modelData.tip
                                        ToolTip.visible: toolMa.containsMouse
                                        ToolTip.delay: 500
                                    }
                                }
                            }
                        }
                    }
                }

                // 消息列表
                ListView {
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

                            // 时间戳
                            Text {
                                text: ts
                                color: root.pal.tsColor
                                font.pixelSize: 11
                                Layout.alignment: Qt.AlignHCenter
                            }

                            // 气泡行
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                // 用户消息：右推
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
                                // AI 消息：左推
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

                        // 输入框
                        Rectangle {
                            id: inputBox
                            Layout.fillWidth: true
                            Layout.preferredHeight: 56
                            radius: 14
                            color: inputField.activeFocus ? root.pal.inputBgFocus : root.pal.inputBg
                            border.color: inputField.activeFocus ? root.pal.inputBorderFocus : root.pal.inputBorder
                            border.width: 1
                            // 焦点态 6px 蓝色光晕
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
                            // 多行输入
                            ScrollView {
                                anchors.fill: parent
                                anchors.margins: 8
                                TextArea {
                                    id: inputField
                                    placeholderText: "发消息…（Shift+Enter 换行）"
                                    placeholderTextColor: root.pal.textTertiary
                                    color: root.pal.textPrimary
                                    font.pixelSize: 14
                                    wrapMode: TextArea.Wrap
                                    background: null
                                    selectByMouse: true
                                    persistentSelection: true
                                }
                            }
                        }

                        // 发送按钮
                        Rectangle {
                            Layout.preferredWidth: 96
                            Layout.preferredHeight: 56
                            radius: 14
                            color: sendMa.containsMouse ? root.pal.sendBtnHover : root.pal.sendBtn
                            Text {
                                anchors.centerIn: parent
                                text: "发送"
                                color: "white"
                                font.pixelSize: 15
                                font.bold: true
                            }
                            MouseArea {
                                id: sendMa
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                            }
                        }
                    }
                }
            }
        }
    }
}
