// Pure helpers for workspace rows and the labels/shortcuts config file.

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
  for (var i = 1; i <= 10; i++) counts[i] = 0
  try {
    var clients = JSON.parse(text)
    if (!Array.isArray(clients)) return counts
    for (var c = 0; c < clients.length; c++) {
      var cl = clients[c]
      if (!cl || cl.mapped !== true) continue
      if (cl.pinned === true) continue
      var id = cl.workspace ? cl.workspace.id : 0
      if (id >= 1 && id <= 10) counts[id]++
    }
  } catch (e) {}
  return counts
}

function snapshot(labels, counts) {
  var rows = []
  for (var i = 1; i <= 10; i++) {
    rows.push({
      wsId: i,
      label: labelOf(labels, i),
      windows: (counts && counts[i]) ? counts[i] : 0
    })
  }
  return rows
}

function windowCountText(n) {
  if (!n) return "empty"
  if (n === 1) return "1 window"
  return n + " windows"
}
