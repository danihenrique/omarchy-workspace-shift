import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar entry for Workspace Shift: one layers icon that opens the workspace
// list, labels, and shortcut settings.
//
// All the work happens in Panel.qml. This file owns the bar slot and the
// open/close contract the bar routes summon/hide/toggle through.
BarWidget {
  id: root
  moduleName: "io.github.danihenrique.workspace-shift"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function toggle() {
    if (panelLoader.item) panelLoader.item.toggle()
  }

  function togglePanel() {
    root.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "io.github.danihenrique.workspace-shift"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // nf-md-layers (U+F0328). Surrogate pair so the source survives editors
    // that mangle private-use codepoints.
    text: "\uDB80\uDF28"
    tooltipText: "Workspaces"

    onPressed: function(b) {
      if (b === Qt.LeftButton) root.toggle()
    }
  }
}
