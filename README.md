# Workspace Shift

![Workspace Shift panel](preview.png)

Reorder Omarchy workspaces by swapping their windows. Hyprland cannot renumber workspaces, and the built-in `omarchy.workspaces` widget stays numeric — this plugin exchanges the mapped, non-pinned windows (and your labels) between two ids instead. The panel lists the same active range as the bar (always 1–5, plus any higher live workspace up to 10), including empty slots in the middle.

Click the bar icon to rename workspaces, move a row up or down, or drag the handle. Default Super+Shift+, and Super+Shift+. swap the current workspace with its neighbor; click a shortcut in the panel and press a combo to change it.

## Install

```sh
omarchy plugin add https://github.com/danihenrique/omarchy-workspace-shift.git --enable
```

Place it in the bar:

```sh
omarchy bar move io.github.danihenrique.workspace-shift --section left|center|right
```

On first enable, the plugin writes a managed bind block into `~/.config/hypr/bindings.lua` and reloads Hyprland. You can also apply binds from the panel, or:

```sh
~/.config/omarchy/plugins/io.github.danihenrique.workspace-shift/scripts/apply-binds
```

## Super+Shift+comma

On US QWERTY, `<` is Shift+comma. The panel records the combo you press (e.g. `SUPER + SHIFT + comma`) rather than free-typed bind strings. Apply still also registers extra `SUPER + less` / `SUPER + greater` binds, because Hyprland will register `SUPER + less` but that key does not fire.

## Usage

- **Bar icon** — open the Workspaces panel
- **Labels** — edit a row and it saves immediately to `~/.config/omarchy/workspace-shift.json`
- **Up / down / drag / Super+Shift+,.** — swap that workspace's windows *and* label with the neighbor (labels travel with the window set). Workspace numbers stay numeric; empty slots in the active range stay visible.
- **Shortcuts** — click Left or Right, then press a combination that includes Super, Ctrl, or Alt. The control records Omarchy `o.bind` syntax and applies immediately. Apply re-applies the current binds.
- **CLI** — `scripts/workspace-shift left|right` or `scripts/workspace-shift swap SRC DEST`

This plugin does **not** replace `omarchy.workspaces`.

## Uninstall

Strip the Hypr binds *before* removing the plugin (the helper lives in the plugin folder):

```sh
~/.config/omarchy/plugins/io.github.danihenrique.workspace-shift/scripts/apply-binds --remove
omarchy plugin remove io.github.danihenrique.workspace-shift
```

`apply-binds --remove` deletes the block between `-- BEGIN io.github.danihenrique.workspace-shift` and `-- END io.github.danihenrique.workspace-shift` in `~/.config/hypr/bindings.lua`. If you already removed the plugin, delete that block by hand and run `hyprctl reload`.
