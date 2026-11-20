import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import Quickshell.Io
import qs.Common
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    property var windows: []
    property string statusText: "Loading usage…"
    readonly property string codexPath: (pluginData?.codexPath || "").trim()
    readonly property string proxyUrl: (pluginData?.proxyUrl || "").trim()

    property var approvals: []
    property bool isApprovalConnected: false
    property string approvalError: "Connecting to Codex App Server…"
    readonly property bool enableApprovals: pluginData?.enableApprovals === true
    readonly property string approvalSocket: (pluginData?.approvalSocket || "").trim()
    readonly property var currentApproval: approvals.length > 0 ? approvals[0] : null
    readonly property string approvalLabel: approvals.length > 1
        ? "Approval required (" + approvals.length + ")" : "Approval required"
    readonly property string quotaLabel: windows.length > 0
        ? "Codex · " + windows.map(window => window.label + " " + window.remaining + "%").join(" · ")
        : "Codex · —"
    readonly property string barLabel: currentApproval
        ? approvalLabel + (windows.length > 0
            ? " · " + windows.map(window => window.remaining + "%").join(" · ") : "")
        : quotaLabel

    onEnableApprovalsChanged: if (!enableApprovals) approvalProcess.running = false
    onCodexPathChanged: pathRefresh.restart()
    onProxyUrlChanged: pathRefresh.restart()
    onApprovalSocketChanged: {
        approvals = []
        isApprovalConnected = false
        approvalError = "Connecting to Codex App Server…"
        approvalProcess.running = false
    }
    readonly property string scriptPath: decodeURIComponent(
        Qt.resolvedUrl("rate_limits.py").toString().replace(/^file:\/\//, "")
    )
    readonly property string approvalScriptPath: decodeURIComponent(
        Qt.resolvedUrl("approvals.py").toString().replace(/^file:\/\//, "")
    )
    function updateFromJson(output) {
        try {
            const result = JSON.parse(output)
            windows = result.windows
            statusText = result.error || ""
        } catch (error) {
            windows = []
            statusText = "Codex returned an invalid usage response."
        }
    }

    Timer {
        interval: 300000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: if (!quotaProcess.running) quotaProcess.running = true
    }

    Timer {
        id: pathRefresh

        interval: 300
        onTriggered: {
            if (quotaProcess.running)
                restart()
            else
                quotaProcess.running = true
        }
    }

    Process {
        id: quotaProcess

        command: ["python3", root.scriptPath, root.codexPath, root.proxyUrl]
        stdout: StdioCollector {
            onStreamFinished: root.updateFromJson(text)
        }

        onExited: (exitCode, exitStatus) => {
            if (exitCode !== 0 || exitStatus !== 0) {
                root.windows = []
                root.statusText = "Could not start the usage check."
            }
        }
    }

    Timer {
        interval: 4000
        running: root.enableApprovals
        repeat: true
        triggeredOnStart: true
        onTriggered: if (!approvalProcess.running) approvalProcess.running = true
    }

    Process {
        id: approvalProcess

        command: ["/usr/bin/python3", root.approvalScriptPath, root.approvalSocket]
        stdinEnabled: true
        stdout: SplitParser {
            onRead: line => {
                try {
                    const update = JSON.parse(line)
                    root.isApprovalConnected = update.connected === true
                    root.approvals = update.approvals || []
                    root.approvalError = update.error || "Disconnected from Codex App Server."
                } catch (error) {
                    root.approvals = []
                    root.isApprovalConnected = false
                    root.approvalError = "Codex App Server returned an invalid response."
                }
            }
        }

        onExited: {
            root.approvals = []
            root.isApprovalConnected = false
        }
    }

    function answerApproval(token, decision) {
        if (approvalProcess.running && currentApproval?.token === token) {
            approvalProcess.write(JSON.stringify({ token: token, decision: decision }) + "\n")
        }
    }

    horizontalBarPill: Component {
        BarPill { }
    }

    verticalBarPill: Component {
        BarPill { vertical: true }
    }

    component BarPill: Item {
        id: pill

        property bool vertical: false
        readonly property real padding: Theme.spacingM
        readonly property real primarySize: Math.ceil(label.implicitWidth + padding * 2)

        implicitWidth: vertical ? root.widgetThickness : primarySize
        implicitHeight: vertical ? primarySize : root.widgetThickness
        width: implicitWidth
        height: implicitHeight
        clip: true

        StyledText {
            id: label

            anchors.centerIn: parent
            width: Math.max(0, (pill.vertical ? pill.height : pill.width) - pill.padding * 2)
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            rotation: pill.vertical ? -90 : 0
            text: root.barLabel
            color: root.currentApproval ? Theme.primary : Theme.surfaceText
            font.pixelSize: Theme.fontSizeMedium - 2
        }
    }

    component ReviewButton: Controls.Button {
        id: button

        property bool isPrimaryAction: false
        property string pressedToken: ""
        font.pixelSize: Theme.fontSizeSmall
        font.weight: Font.DemiBold
        hoverEnabled: true
        leftPadding: Theme.spacingM
        rightPadding: Theme.spacingM
        implicitHeight: 34
        opacity: enabled ? 1 : 0.48

        contentItem: StyledText {
            text: button.text
            font: button.font
            color: button.isPrimaryAction ? Theme.primaryText : Theme.surfaceText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: Math.min(Theme.cornerRadius, 8)
            color: button.isPrimaryAction ? Theme.primary : Theme.surfaceContainerHigh
            opacity: button.down ? 0.76 : button.hovered ? 0.9 : 1
            border.width: button.visualFocus ? 2 : 0
            border.color: button.isPrimaryAction ? Theme.primaryText : Theme.primary
        }

        onPressed: pressedToken = root.currentApproval?.token || ""
    }

    popoutWidth: currentApproval ? 460 : 340
    popoutHeight: currentApproval ? 325 : enableApprovals && !isApprovalConnected ? 180 : 140

    popoutContent: Component {
        PopoutComponent {
            headerText: root.currentApproval ? "Approval required" : "Codex usage"

            ColumnLayout {
                width: parent.width
                spacing: Theme.spacingS
                visible: !root.currentApproval

                Repeater {
                    model: root.windows

                    delegate: RowLayout {
                        required property var modelData

                        Layout.fillWidth: true
                        spacing: Theme.spacingM

                        StyledText {
                            Layout.fillWidth: true
                            text: modelData.label + " window"
                            color: Theme.surfaceVariantText
                            font.pixelSize: Theme.fontSizeSmall
                            elide: Text.ElideRight
                        }

                        StyledText {
                            text: modelData.remaining + "%"
                            color: Theme.surfaceText
                            font.pixelSize: Theme.fontSizeMedium
                            font.weight: Font.DemiBold
                        }

                        StyledText {
                            Layout.preferredWidth: 130
                            horizontalAlignment: Text.AlignRight
                            text: modelData.resetsAt === null ? "Reset unavailable"
                                : "Resets " + Qt.formatDateTime(new Date(modelData.resetsAt * 1000), "MM-dd hh:mm")
                            color: Theme.surfaceVariantText
                            font.pixelSize: Theme.fontSizeSmall
                            elide: Text.ElideRight
                        }
                    }
                }

                StyledText {
                    Layout.fillWidth: true
                    visible: root.windows.length === 0
                    text: root.statusText
                    color: Theme.surfaceVariantText
                    wrapMode: Text.WordWrap
                }

                StyledText {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingS
                    visible: root.enableApprovals && !root.isApprovalConnected
                    text: root.approvalError
                    textFormat: Text.PlainText
                    color: Theme.surfaceVariantText
                    font.pixelSize: Theme.fontSizeSmall
                    wrapMode: Text.Wrap
                }
            }

            ColumnLayout {
                width: parent.width
                visible: !!root.currentApproval
                spacing: Theme.spacingM

                RowLayout {
                    Layout.fillWidth: true

                    StyledText {
                        Layout.fillWidth: true
                        text: root.currentApproval?.title || ""
                        color: Theme.surfaceText
                        font.pixelSize: Theme.fontSizeMedium
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }

                    StyledText {
                        visible: root.approvals.length > 1
                        text: root.approvals.length + " pending"
                        color: Theme.surfaceVariantText
                        font.pixelSize: Theme.fontSizeSmall
                    }
                }

                Controls.ScrollView {
                    id: requestPreview

                    Layout.fillWidth: true
                    Layout.preferredHeight: 166
                    clip: true
                    contentWidth: availableWidth
                    contentHeight: requestBody.implicitHeight

                    Column {
                        id: requestBody

                        width: requestPreview.availableWidth
                        spacing: Theme.spacingM

                        TextEdit {
                            width: parent.width
                            text: root.currentApproval?.detail || ""
                            readOnly: true
                            selectByMouse: true
                            textFormat: TextEdit.PlainText
                            color: Theme.surfaceText
                            selectionColor: Theme.primary
                            selectedTextColor: Theme.primaryText
                            font.family: "monospace"
                            font.pixelSize: Theme.fontSizeSmall
                            wrapMode: TextEdit.Wrap
                            Accessible.name: "Requested action"
                        }

                        StyledText {
                            width: parent.width
                            visible: text.length > 0
                            text: root.currentApproval?.context || ""
                            textFormat: Text.PlainText
                            color: Theme.surfaceVariantText
                            font.pixelSize: Theme.fontSizeSmall
                            wrapMode: Text.Wrap
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingS

                    StyledText {
                        Layout.fillWidth: true
                        text: root.currentApproval?.isSending ? "Sending decision…" : ""
                        color: Theme.surfaceVariantText
                        font.pixelSize: Theme.fontSizeSmall
                        elide: Text.ElideRight
                    }

                    ReviewButton {
                        text: root.currentApproval?.negativeDecision === "cancel" ? "Cancel turn" : "Deny"
                        enabled: !!root.currentApproval?.negativeDecision
                            && !root.currentApproval?.isSending
                        onClicked: root.answerApproval(pressedToken, root.currentApproval?.negativeDecision)
                    }

                    ReviewButton {
                        text: "Allow once"
                        isPrimaryAction: true
                        enabled: (root.currentApproval?.canAccept ?? false)
                            && !root.currentApproval?.isSending
                        onClicked: root.answerApproval(pressedToken, "accept")
                    }
                }
            }
        }
    }
}
