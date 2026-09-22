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
    // Day 20.4.4 修复 "Binding loop detected for property implicitHeight"：
    //   标题 Text 用了 Layout.fillWidth + wrapMode.Wrap → 它的 implicitWidth
    //   依赖 Text.width ← contentRow.width ← bubble.width ← bubble.implicitWidth。
    //   原来写 implicitWidth: contentRow.implicitWidth + 28 时，这条链首尾相接
    //   成 implicitWidth 自环；Qt 会把环就近归到 implicitHeight 上报警，于是
    //   每创建一个气泡就往 stderr 打一条 binding loop 警告（用户容易误当报错）。
    //   实测：把 implicitWidth 解耦成常量后警告消失（stderr 干净）。
    //   气泡实际宽度由调用方的 Layout.preferredWidth 决定，这里只提供稳定的
    //   implicit 尺寸，因此用常量属性而不是从 contentRow 反推最稳妥。
    property int preferredWidth: 680
    implicitWidth: preferredWidth
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
            // 标题 / 正文（AI 回复 / 用户消息 / 工具名）
            // Day 20.6.22 修复 "无法选中复制一小部分对话内容"：原来用 Text
            // （只读控件，selectable 永远 false），用户拖蓝 / 双击 / Ctrl+C
            // 全部失效 —— 只能整条气泡「复制」（走三点菜单的 copy_to_clipboard）。
            // 改为 TextEdit + readOnly + selectByMouse + selectByKeyboard +
            // persistentSelection：行为等价于可选中不可编辑，鼠标拖蓝高亮、
            // Ctrl+C 复制都正常。TextEdit 的 contentHeight 自动撑出 implicitHeight
            // 给 ColumnLayout 用，不会破布局。
            // 三点菜单的「复制」项仍走整条 copy_to_clipboard（不冲突）。
            TextEdit {
                id: bodyText
                visible: bubble.text.length > 0
                text: bubble.text
                color: bubble.textColor
                font.pixelSize: bubble.isTool ? 14 : 15
                font.family: "Microsoft YaHei"
                font.bold: bubble.isTool
                wrapMode: TextEdit.Wrap
                Layout.fillWidth: true
                textFormat: bubble.isTool ? TextEdit.PlainText : TextEdit.RichText
                readOnly: true
                selectByMouse: true
                selectByKeyboard: true
                persistentSelection: true
                // 不显示光标闪烁（已读的角色用 TextEdit 只想借它的可选中能力）
                cursorVisible: false
                // 不参与 Tab 焦点链（避免误触）
                activeFocusOnTab: false
            }
            // 参数 / 结果（code 字段）
            Rectangle {
                visible: bubble.hasCode
                Layout.fillWidth: true
                radius: 8
                color: bubble.codeBg
                implicitHeight: codeText.implicitHeight + 16
                TextEdit {
                    id: codeText
                    anchors.fill: parent
                    anchors.margins: 8
                    text: bubble.code
                    color: bubble.codeFg
                    font.family: bubble.isTool ? "Consolas, Courier New, monospace" : "Microsoft YaHei"
                    font.pixelSize: bubble.isTool ? 12 : 13
                    wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    persistentSelection: true
                    cursorVisible: false
                    activeFocusOnTab: false
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
                // Day 20.4.4 定位（跟侧边栏 sessionMenu 同一套规则）：
                //   1) parent 必须是 Overlay —— 否则 Qt 会自己重排位置
                //   2) 顺序必须「先 popup() 再设 x/y」—— popup() 内部会覆盖
                //      我们事先设好的 x/y（这是之前一直定位失败的真因）
                //   3) 菜单右边缘对齐按钮右边缘，上边缘在按钮下方 2px，再 clamp 进窗口
                var ov = Overlay.overlay
                var p = moreBtn.mapToItem(ov, 0, moreBtn.height + 2)
                var mx = Math.max(4, Math.min(p.x + moreBtn.width - bubbleMenu.width,
                                              ov.width - bubbleMenu.width - 4))
                var my = Math.max(4, Math.min(p.y, ov.height - bubbleMenu.height - 4))
                bubbleMenu.popup()
                bubbleMenu.x = mx
                bubbleMenu.y = my
            }
        }
    }

    // 三点菜单（按 who 类型自适应）
    // Day 20.3: 所有数据通过 Menu 自身的 currentMsgXxx 属性传递，MenuItem 不再
    // 直接引用 bubble.*（跨 Popup window scope 不可靠）。
    Menu {
        id: bubbleMenu
        // Day 20.4.4: parent 必须是 Overlay（普通 Item 做 parent 时 Qt 会自己重排位置，
        // 覆盖掉我们设的 x/y）。定位细节见 moreMa.onClicked。
        parent: Overlay.overlay
        property string currentMsgText: ""
        property string currentMsgCode: ""
        property string currentMsgWho: ""
        property int currentMsgIndex: -1
        property bool currentMsgHasCode: false

        // Day 20.6 修复「菜单项之间出现莫名空行」：
        // Qt 5.15 Controls 2 的 Menu 内容项是 QQuickListView 布局，ListView
        // **不会跳过 visible:false 的 delegate** —— 隐藏的 MenuItem/MenuSeparator
        // 仍按 implicitHeight 占一行（实测：隐藏 MenuItem 仍占 40px）。
        // 所以每个条件隐藏项都必须显式把 height 绑定为「可见才取 implicitHeight，
        // 隐藏取 0」，否则「复制代码/引用回复/编辑/重新生成」等隐藏项会在菜单里
        // 留下成片的空白行。

        // 普通复制（user / ai / tool_* 都给）
        MenuItem {
            text: qsTr("复制")
            onTriggered: if (bridge) bridge.copy_to_clipboard(bubbleMenu.currentMsgText)
        }
        // 复制代码（仅当 hasCode）
        MenuItem {
            text: qsTr("复制代码")
            visible: bubbleMenu.currentMsgHasCode
            height: visible ? implicitHeight : 0
            onTriggered: if (bridge) bridge.copy_code(bubbleMenu.currentMsgCode)
        }
        MenuSeparator {
            visible: bubbleMenu.currentMsgHasCode
            height: visible ? implicitHeight : 0
        }
        // 引用回复（user / ai；tool_* 不引用，太冗余）
        MenuItem {
            text: qsTr("引用回复")
            visible: bubbleMenu.currentMsgWho !== "tool_call"
                 && bubbleMenu.currentMsgWho !== "tool_result"
            height: visible ? implicitHeight : 0
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
            height: visible ? implicitHeight : 0
            onTriggered: if (bridge) bridge.speak_text(bubbleMenu.currentMsgText)
        }
        MenuSeparator {
            visible: bubbleMenu.currentMsgWho === "user"
                  || bubbleMenu.currentMsgWho === "ai"
            height: visible ? implicitHeight : 0
        }
        // 编辑（仅 user）
        MenuItem {
            text: qsTr("编辑...")
            visible: bubbleMenu.currentMsgWho === "user"
            height: visible ? implicitHeight : 0
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
            height: visible ? implicitHeight : 0
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
