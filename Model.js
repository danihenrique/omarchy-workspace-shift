// Pure helpers for workspace rows and the labels/shortcuts config file.
// Active ids match omarchy.workspaces: always 1–5, plus any other live 1–10.

function defaultConfig() {
  return {
    labels: {},
    shortcutLeft: "SUPER + SHIFT + comma",
    shortcutRight: "SUPER + SHIFT + period"
  }
}

function parseConfig(text) {
  var cfg = defaultConfig()
  if (!text) return cfg
  try {
    var data = JSON.parse(text)
    if (!data || typeof data !== "object") return cfg
    if (data.labels && typeof data.labels === "object")
      cfg.labels = copyMap(data.labels)
    if (typeof data.shortcutLeft === "string" && data.shortcutLeft.trim())
      cfg.shortcutLeft = data.shortcutLeft.trim()
    if (typeof data.shortcutRight === "string" && data.shortcutRight.trim())
      cfg.shortcutRight = data.shortcutRight.trim()
  } catch (e) {}
  return cfg
}

function serializeConfig(cfg) {
  return JSON.stringify({
    labels: (cfg && cfg.labels) ? cfg.labels : {},
    shortcutLeft: (cfg && cfg.shortcutLeft) ? cfg.shortcutLeft : defaultConfig().shortcutLeft,
    shortcutRight: (cfg && cfg.shortcutRight) ? cfg.shortcutRight : defaultConfig().shortcutRight
  }, null, 2) + "\n"
}

function copyMap(src) {
  var out = {}
  if (!src) return out
  var keys = Object.keys(src)
  for (var i = 0; i < keys.length; i++)
    out[keys[i]] = src[keys[i]]
  return out
}

function labelOf(labels, wsId) {
  if (!labels) return ""
  var v = labels[String(wsId)]
  return v ? String(v) : ""
}

function setLabel(labels, wsId, text) {
  var next = copyMap(labels)
  var t = String(text || "").trim()
  var k = String(wsId)
  if (t) next[k] = t
  else delete next[k]
  return next
}

function swapLabels(labels, a, b) {
  var next = copyMap(labels)
  var ka = String(a)
  var kb = String(b)
  var la = next[ka] || ""
  var lb = next[kb] || ""
  if (lb) next[ka] = lb
  else delete next[ka]
  if (la) next[kb] = la
  else delete next[kb]
  return next
}

function countsFromClients(text) {
  var counts = {}
  try {
    var clients = JSON.parse(text)
    if (!Array.isArray(clients)) return counts
    for (var c = 0; c < clients.length; c++) {
      var cl = clients[c]
      if (!cl || cl.mapped !== true) continue
      if (cl.pinned === true) continue
      var id = cl.workspace ? cl.workspace.id : 0
      if (id >= 1 && id <= 10)
        counts[id] = (counts[id] || 0) + 1
    }
  } catch (e) {}
  return counts
}

// Same rule as omarchy.workspaces workspaceIds(): base 1–5, then any
// other Hyprland workspace id in 1–10 (occupied or empty-but-present).
function activeWorkspaceIds(workspacesText, counts) {
  var ids = [1, 2, 3, 4, 5]
  function add(id) {
    if (id > 0 && id <= 10 && ids.indexOf(id) === -1)
      ids.push(id)
  }
  try {
    var workspaces = JSON.parse(workspacesText || "[]")
    if (Array.isArray(workspaces)) {
      for (var i = 0; i < workspaces.length; i++) {
        var ws = workspaces[i]
        if (ws && typeof ws.id === "number") add(ws.id)
      }
    }
  } catch (e) {}
  if (counts) {
    var keys = Object.keys(counts)
    for (var k = 0; k < keys.length; k++) {
      var n = parseInt(keys[k], 10)
      if (!isNaN(n) && counts[n] > 0) add(n)
    }
  }
  ids.sort(function(a, b) { return a - b })
  return ids
}

function snapshot(labels, counts, workspacesText) {
  var ids = activeWorkspaceIds(workspacesText, counts)
  var rows = []
  for (var i = 0; i < ids.length; i++) {
    var id = ids[i]
    rows.push({
      wsId: id,
      label: labelOf(labels, id),
      windows: (counts && counts[id]) ? counts[id] : 0
    })
  }
  return rows
}

function windowCountText(n) {
  if (!n) return "empty"
  if (n === 1) return "1 window"
  return n + " windows"
}

function parseListState(text) {
  var empty = { counts: {}, workspacesText: "[]" }
  if (!text) return empty
  try {
    var data = JSON.parse(text)
    if (!data || typeof data !== "object") return empty
    return {
      counts: data.counts && typeof data.counts === "object" ? data.counts : {},
      workspacesText: JSON.stringify(data.workspaces || [])
    }
  } catch (e) {
    return empty
  }
}

function isModifierKey(key) {
  return key === Qt.Key_Shift
    || key === Qt.Key_Control
    || key === Qt.Key_Alt
    || key === Qt.Key_Meta
    || key === Qt.Key_AltGr
    || key === Qt.Key_Super_L
    || key === Qt.Key_Super_R
    || key === Qt.Key_Hyper_L
    || key === Qt.Key_Hyper_R
}

function superModifierMask() {
  var mask = Qt.MetaModifier
  if (typeof Qt.SuperModifier === "number")
    mask = mask | Qt.SuperModifier
  return mask
}

function hasPrimaryModifier(modifiers) {
  var mods = modifiers || 0
  return !!(mods & (superModifierMask() | Qt.ControlModifier | Qt.AltModifier))
}

function keyTokenFromEvent(key, modifiers, text) {
  if (key >= Qt.Key_A && key <= Qt.Key_Z)
    return String.fromCharCode(key)
  if (key >= Qt.Key_0 && key <= Qt.Key_9)
    return String.fromCharCode(key)
  if (key === Qt.Key_Comma) return "comma"
  if (key === Qt.Key_Period) return "period"
  if (key === Qt.Key_Less)
    return (modifiers & Qt.ShiftModifier) ? "comma" : "less"
  if (key === Qt.Key_Greater)
    return (modifiers & Qt.ShiftModifier) ? "period" : "greater"
  if (key === Qt.Key_Left) return "LEFT"
  if (key === Qt.Key_Right) return "RIGHT"
  if (key === Qt.Key_Up) return "UP"
  if (key === Qt.Key_Down) return "DOWN"
  if (key === Qt.Key_Space) return "SPACE"
  if (key === Qt.Key_Return || key === Qt.Key_Enter) return "RETURN"
  if (key === Qt.Key_Tab) return "TAB"
  if (key === Qt.Key_Escape) return "ESCAPE"
  if (key === Qt.Key_Slash) return "slash"
  if (key === Qt.Key_Backslash) return "backslash"
  if (key === Qt.Key_Minus) return "minus"
  if (key === Qt.Key_Equal) return "equal"
  if (key === Qt.Key_BracketLeft) return "bracketleft"
  if (key === Qt.Key_BracketRight) return "bracketright"
  if (key === Qt.Key_Semicolon) return "semicolon"
  if (key === Qt.Key_Apostrophe) return "apostrophe"

  if (typeof text === "string" && text.length === 1) {
    var code = text.charCodeAt(0)
    if (code >= 32 && code < 127) {
      if ((code >= 65 && code <= 90) || (code >= 97 && code <= 122))
        return text.toUpperCase()
      if (code >= 48 && code <= 57)
        return text
      return text.toLowerCase()
    }
  }
  return null
}

// Qt key + modifiers → Omarchy o.bind string, or null if not a complete shortcut.
function bindStringFromKeyEvent(key, modifiers, text) {
  if (isModifierKey(key)) return null
  var token = keyTokenFromEvent(key, modifiers, text)
  if (!token) return null
  if (!hasPrimaryModifier(modifiers)) return null
  var parts = []
  if (modifiers & superModifierMask()) parts.push("SUPER")
  if (modifiers & Qt.ControlModifier) parts.push("CTRL")
  if (modifiers & Qt.AltModifier) parts.push("ALT")
  if (modifiers & Qt.ShiftModifier) parts.push("SHIFT")
  parts.push(token)
  return parts.join(" + ")
}
