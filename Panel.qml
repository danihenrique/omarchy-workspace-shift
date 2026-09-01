import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Workspace list, labels, neighbor swap, and shortcut settings.
Panel {
  id: root
  moduleName: "io.github.danihenrique.workspace-shift"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color secondaryForeground: Util.alpha(contentForeground, 0.54)

  readonly property string pluginDir: Quickshell.env("HOME") + "/.config/omarchy/plugins/io.github.danihenrique.workspace-shift"
  readonly property string shiftScript: pluginDir + "/scripts/workspace-shift"
  readonly property string applyScript: pluginDir + "/scripts/apply-binds"
  readonly property string configPath: Quickshell.env("HOME") + "/.config/omarchy/workspace-shift.json"

  property var labels: ({})
  property string shortcutLeft: "SUPER + SHIFT + comma"
  property string shortcutRight: "SUPER + SHIFT + period"
  property var counts: ({})
  property var swapQueue: []
  property bool writingConfig: false
  property bool labelFocused: false
  property bool recordingLeft: false
  property bool recordingRight: false
  property string status: ""
  property bool statusIsError: false

  property int dragFrom: -1
  property real dragOriginY: 0
  property real dragDelta: 0
  readonly property real rowHeight: Math.max(Style.space(34), Style.font.body + Style.space(16))
  readonly property bool busy: swapProc.running || applyProc.running || refreshProc.running || root.swapQueue.length > 0
  readonly property bool fieldsFocused: root.labelFocused || root.recordingLeft || root.recordingRight

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  onOpenedChanged: {
    if (!root.opened) {
      root.dragFrom = -1
      root.stopRecording()
      return
    }
    root.status = ""
    root.stopRecording()
    root.refresh()
  }

  function applyConfigText(text) {
    var cfg = Model.parseConfig(text)
    root.labels = cfg.labels
    root.shortcutLeft = cfg.shortcutLeft
    root.shortcutRight = cfg.shortcutRight
    if (root.opened) root.stopRecording()
    root.applySnapshot()
  }

  function stopRecording() {
    root.recordingLeft = false
    root.recordingRight = false
  }

  function beginRecording(side) {
    root.status = ""
    root.statusIsError = false
    if (side === "right") {
      root.recordingLeft = false
      root.recordingRight = true
    } else {
      root.recordingRight = false
      root.recordingLeft = true
    }
  }

  function handleCaptureKey(event, side) {
    if (Model.isModifierKey(event.key)) {
      event.accepted = true
      return
    }

    var primary = Model.superModifierMask() | Qt.ControlModifier | Qt.AltModifier
    if (event.key === Qt.Key_Escape && !(event.modifiers & primary)) {
      root.stopRecording()
      keyCatcher.forceActiveFocus()
      event.accepted = true
      return
    }

    var bind = Model.bindStringFromKeyEvent(event.key, event.modifiers, event.text)
    if (bind) {
      if (side === "right") root.shortcutRight = bind
      else root.shortcutLeft = bind
      root.stopRecording()
      root.applyBinds()
      keyCatcher.forceActiveFocus()
      event.accepted = true
      return
    }

    if (!Model.hasPrimaryModifier(event.modifiers)) {
      root.status = "Add Super/Ctrl/Alt"
      root.statusIsError = true
    }
    event.accepted = true
  }

  function saveConfig() {
    root.writingConfig = true
    configFile.setText(Model.serializeConfig({
      labels: root.labels,
      shortcutLeft: root.shortcutLeft,
      shortcutRight: root.shortcutRight
    }))
    Qt.callLater(function() { root.writingConfig = false })
  }

  property string workspacesText: "[]"

  function indexOfWs(wsId) {
    for (var i = 0; i < rows.count; i++) {
      if (rows.get(i).wsId === wsId) return i
    }
    return -1
  }

  function applySnapshot() {
    var list = Model.snapshot(root.labels, root.counts, root.workspacesText)
    if (rows.count !== list.length) {
      rows.clear()
      for (var i = 0; i < list.length; i++)
        rows.append(list[i])
      return
    }
    for (var j = 0; j < list.length; j++) {
      rows.setProperty(j, "wsId", list[j].wsId)
      rows.setProperty(j, "label", list[j].label)
      rows.setProperty(j, "windows", list[j].windows)
    }
  }

  function applyListState(text) {
    var state = Model.parseListState(text)
    root.counts = state.counts
    root.workspacesText = state.workspacesText
    root.applySnapshot()
  }

  function refresh() {
    if (refreshProc.running || swapProc.running || root.swapQueue.length > 0) return
    if (root.fieldsFocused) return
    refreshProc.running = true
  }

  function commitLabel(wsId, text) {
    root.labels = Model.setLabel(root.labels, wsId, text)
    var idx = root.indexOfWs(wsId)
    if (idx >= 0)
      rows.setProperty(idx, "label", Model.labelOf(root.labels, wsId))
    root.saveConfig()
  }

  function swapNeighbor(a, b) {
    if (a < 1 || b < 1 || a > 10 || b > 10 || a === b) return
    // Optimistic UI only — scripts/workspace-shift persists label swap with windows.
    root.labels = Model.swapLabels(root.labels, a, b)
    var ia = root.indexOfWs(a)
    var ib = root.indexOfWs(b)
    if (ia >= 0 && ib >= 0) {
      var la = rows.get(ia).label
      var lb = rows.get(ib).label
      var wa = rows.get(ia).windows
      var wb = rows.get(ib).windows
      rows.setProperty(ia, "label", lb)
      rows.setProperty(ib, "label", la)
      rows.setProperty(ia, "windows", wb)
      rows.setProperty(ib, "windows", wa)
    }
    root.enqueueSwap(a, b)
  }

  function moveWorkspace(wsId, direction) {
    var idx = root.indexOfWs(wsId)
    if (idx < 0) return
    var other = idx + direction
    if (other < 0 || other >= rows.count) return
    root.swapNeighbor(wsId, rows.get(other).wsId)
  }

  function shiftRow(index, steps) {
    if (index < 0 || index >= rows.count) return
    var id = rows.get(index).wsId
    if (steps > 0) {
      for (var i = 0; i < steps; i++) {
        var cur = root.indexOfWs(id)
        if (cur < 0 || cur >= rows.count - 1) break
        var nextId = rows.get(cur + 1).wsId
        root.swapNeighbor(id, nextId)
      }
    } else if (steps < 0) {
      for (var j = 0; j < -steps; j++) {
        var cur2 = root.indexOfWs(id)
        if (cur2 <= 0) break
        var prevId = rows.get(cur2 - 1).wsId
        root.swapNeighbor(id, prevId)
      }
    }
  }

  function enqueueSwap(src, dest) {
    root.swapQueue = root.swapQueue.concat([{ src: src, dest: dest }])
    root.drainSwap()
  }

  function drainSwap() {
    if (swapProc.running) return
    if (root.swapQueue.length === 0) {
      root.refresh()
      return
    }
    var job = root.swapQueue[0]
    var rest = []
    for (var i = 1; i < root.swapQueue.length; i++)
      rest.push(root.swapQueue[i])
    root.swapQueue = rest
    root.status = "Swapping " + job.src + " \u2194 " + job.dest
    root.statusIsError = false
    swapProc.command = [root.shiftScript, "swap", String(job.src), String(job.dest)]
    swapProc.running = true
  }

  function applyBinds() {
    root.saveConfig()
    root.status = "Applying shortcuts\u2026"
    root.statusIsError = false
    applyProc.command = [root.applyScript, "--left", root.shortcutLeft, "--right", root.shortcutRight]
    applyProc.running = true
  }

  ListModel { id: rows }

  FileView {
    id: configFile
    path: root.configPath
    watchChanges: true
    atomicWrites: true
    printErrors: false
    onLoaded: {
      if (root.writingConfig) return
      root.applyConfigText(text())
    }
    onLoadFailed: {
      if (root.writingConfig) return
      root.applyConfigText("")
    }
    onFileChanged: reload()
    Component.onCompleted: reload()
  }

  Process {
    id: refreshProc
    command: [root.pluginDir + "/scripts/list-state"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyListState(text)
    }
  }

  Process {
    id: swapProc
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.status = "Swap failed"
        root.statusIsError = true
        root.swapQueue = []
        configFile.reload()
        refreshProc.running = true
        return
      }
      root.status = ""
      configFile.reload()
      root.drainSwap()
    }
  }

  Process {
    id: applyProc
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.status = "Could not apply shortcuts"
        root.statusIsError = true
        return
      }
      root.status = "Shortcuts applied"
      root.statusIsError = false
    }
  }

  Timer {
    interval: 2000
    running: root.opened && !root.busy && !root.fieldsFocused
    repeat: true
    onTriggered: root.refresh()
  }


  component ShortcutCapture: Row {
    id: capture
    required property string label
    required property string side
    required property string bindValue
    required property bool recording
    signal startRecording()

    width: parent.width
    spacing: Style.space(8)

    Text {
      width: Style.space(40)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: capture.label
      color: root.secondaryForeground
      font.family: root.contentFontFamily
      font.pixelSize: Style.font.caption
    }

    Item {
      id: captureFocus
      width: parent.width - Style.space(48)
      height: bindBtn.implicitHeight
      anchors.verticalCenter: parent.verticalCenter
      activeFocusOnTab: false
      focus: capture.recording

      Keys.enabled: capture.recording
      Keys.priority: Keys.BeforeItem
      Keys.onPressed: function(event) {
        root.handleCaptureKey(event, capture.side)
      }

      onActiveFocusChanged: {
        if (capture.recording && !activeFocus)
          root.stopRecording()
      }

      Button {
        id: bindBtn
        anchors.fill: parent
        text: capture.recording ? "Press shortcut\u2026" : capture.bindValue
        bordered: true
        leftAlign: true
        active: capture.recording
        focusable: false
        foreground: root.contentForeground
        fontFamily: root.contentFontFamily
        fontSize: Style.font.bodySmall
        onClicked: {
          if (!capture.recording)
            capture.startRecording()
        }
      }
    }

    onRecordingChanged: {
      if (recording)
        Qt.callLater(function() { captureFocus.forceActiveFocus() })
      else if (captureFocus.activeFocus)
        keyCatcher.forceActiveFocus()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(
      header.implicitHeight + listColumn.implicitHeight + settings.implicitHeight + Style.space(24),
      Style.space(640)
    )

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.fieldsFocused
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: header
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(6)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: "Workspaces"
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }

        Text {
          width: parent.width
          visible: root.status !== ""
          textFormat: Text.PlainText
          text: root.status
          color: root.statusIsError ? Color.urgent : root.secondaryForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
        }

        PanelSeparator { foreground: root.contentForeground }
      }

      Column {
        id: listColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.topMargin: Style.space(8)
        spacing: Style.space(2)

        Repeater {
          model: rows

          delegate: Item {
            id: row
            required property int index
            required property int wsId
            required property string label
            required property int windows

            width: listColumn.width
            height: root.rowHeight
            opacity: root.dragFrom === index ? 0.72 : 1
            onLabelChanged: if (labelField && !labelField.activeFocus) labelField.text = label

            Row {
              id: rowBody
              anchors.fill: parent
              spacing: Style.space(6)

              Text {
                width: Style.space(22)
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: String(row.wsId)
                color: root.contentForeground
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.body
                font.bold: true
                horizontalAlignment: Text.AlignRight
              }

              TextField {
                id: labelField
                width: Math.max(Style.space(140), rowBody.width - Style.space(210))
                anchors.verticalCenter: parent.verticalCenter
                placeholderText: "Label"
                foreground: root.contentForeground
                placeholderTextColor: root.secondaryForeground
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.bodySmall
                verticalPadding: Style.space(4)
                Component.onCompleted: text = row.label
                onActiveFocusChanged: root.labelFocused = activeFocus
                onEditingFinished: root.commitLabel(row.wsId, text)
                onAccepted: root.commitLabel(row.wsId, text)
                Keys.onEscapePressed: function(event) {
                  text = row.label
                  keyCatcher.forceActiveFocus()
                  event.accepted = true
                }
              }

              Text {
                width: Style.space(72)
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: Model.windowCountText(row.windows)
                color: root.secondaryForeground
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }

              PanelActionButton {
                anchors.verticalCenter: parent.verticalCenter
                iconText: "\uDB80\uDD43"
                tooltipText: "Swap with workspace above"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                enabled: index > 0 && !root.busy
                onClicked: root.moveWorkspace(row.wsId, -1)
              }

              PanelActionButton {
                anchors.verticalCenter: parent.verticalCenter
                iconText: "\uDB80\uDD40"
                tooltipText: "Swap with workspace below"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                enabled: index < rows.count - 1 && !root.busy
                onClicked: root.moveWorkspace(row.wsId, 1)
              }

              Item {
                id: grip
                width: Style.space(22)
                height: parent.height
                enabled: !root.busy

                Text {
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: "\uDB80\uDDDB"
                  color: root.secondaryForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.icon
                }

                MouseArea {
                  anchors.fill: parent
                  enabled: !root.busy
                  cursorShape: Qt.SizeVerCursor
                  onPressed: function(mouse) {
                    var p = mapToItem(listColumn, mouse.x, mouse.y)
                    root.dragFrom = row.index
                    root.dragOriginY = p.y
                    root.dragDelta = 0
                  }
                  onPositionChanged: function(mouse) {
                    if (root.dragFrom !== row.index) return
                    var p = mapToItem(listColumn, mouse.x, mouse.y)
                    root.dragDelta = p.y - root.dragOriginY
                  }
                  onReleased: {
                    if (root.dragFrom !== row.index) return
                    var delta = root.dragDelta
                    root.dragFrom = -1
                    root.dragDelta = 0
                    var steps = Math.round(delta / root.rowHeight)
                    if (steps === 0 && Math.abs(delta) >= root.rowHeight * 0.35)
                      steps = delta > 0 ? 1 : -1
                    if (steps !== 0) root.shiftRow(row.index, steps)
                  }
                  onCanceled: {
                    root.dragFrom = -1
                    root.dragDelta = 0
                  }
                }
              }
            }
          }
        }
      }

      Column {
        id: settings
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: listColumn.bottom
        anchors.topMargin: Style.space(10)
        spacing: Style.space(8)

        PanelSeparator { foreground: root.contentForeground }

        PanelSectionHeader {
          text: "Shortcuts"
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
        }

        ShortcutCapture {
          label: "Left"
          side: "left"
          bindValue: root.shortcutLeft
          recording: root.recordingLeft
          onStartRecording: root.beginRecording("left")
        }

        ShortcutCapture {
          label: "Right"
          side: "right"
          bindValue: root.shortcutRight
          recording: root.recordingRight
          onStartRecording: root.beginRecording("right")
        }

        Button {
          text: applyProc.running ? "Applying\u2026" : "Apply"
          bordered: true
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
          enabled: !applyProc.running
          onClicked: root.applyBinds()
        }

        Text {
          width: parent.width
          textFormat: Text.PlainText
          wrapMode: Text.WordWrap
          text: "Click a shortcut, then press the combination to record it."
          color: root.secondaryForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
