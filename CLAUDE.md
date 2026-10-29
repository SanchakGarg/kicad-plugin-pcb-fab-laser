# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A KiCad 9/10 pcbnew ActionPlugin that exports laser-fabrication SVGs (and optional PNGs) from a loaded board, targeting xTool-style diode lasers ablating spray-painted copper-clad boards.

## Install / run

```bash
./install.sh    # Linux, macOS, and Windows-under-Git-Bash
# Native Windows PowerShell: powershell -ExecutionPolicy Bypass -File install.ps1
```

Both scripts symlink `pcb_fab_laser/` into every `<kicad_root>/<ver>/scripting/plugins/`
they find. Plugin root per OS: `~/.local/share/kicad` (Linux), `~/Documents/KiCad`
(macOS and Windows). Both fall back to a **copy** when symlink creation fails (Windows
without Developer Mode), and a copy is a snapshot — re-run the installer after every edit.

**Two installers cover the same ground and must stay in sync** (`install.sh` via
`uname` incl. `MINGW*|MSYS*|CYGWIN*`, `install.ps1` via `MyDocuments`). `README.md`
documents both plus a manual path table — update it when paths change.

Then in KiCad: **Tools → External Plugins → Refresh Plugins**, then run *PCB Fab Laser SVG Export*.

Because the install is a **symlink**, edits to the repo take effect after a Refresh Plugins (or a KiCad restart — pcbnew caches imported modules, so restart is the reliable way to pick up changes to already-imported modules).

There is no build, no test suite, and no linter configured. The only way to exercise the code is inside pcbnew — `import pcbnew` fails outside KiCad's Python. Plugin load errors surface in pcbnew's Python console, not stdout.

## Architecture

Three files, strict layering:

- `__init__.py` — KiCad's plugin loader imports this; it constructs and `.register()`s the plugin at import time.
- `action_plugin.py` — wx GUI only. `ExportDialog` gathers checkbox/DPI/outdir state into an `ExportOptions` dataclass, calls `LaserExporter.run()`, and reports the returned file list or the exception via `wx.MessageBox`. Contains no geometry logic.
- `exporter.py` — all geometry and file writing. Imports `pcbnew` but never `wx`, so it stays headless-testable in principle.

### Export model

Everything is flattened into `SHAPE_POLY_SET` (KiCad's polygon-set type) in internal units (IU, 1e6 per mm), then serialized to SVG by hand — no `pcbnew` plotter is used. Arcs are polygonized with a 5 µm chord error (`ARC_ERROR_IU`).

`ExportOptions.sides` is a tuple of `"front"`/`"back"`; `run()` calls `_export_side()` once per side, which maps the side to its layer pair via `SIDE_LAYERS` (F.Cu/F.SilkS/F.Mask or B.Cu/B.SilkS/B.Mask) and its mirror flag (`mirror_front` / `mirror_back`). Up to five files per side, named `<base>_<side>_<kind>.svg`:

| Kind | Content | Laser meaning |
|---|---|---|
| `copper` | `board_outline − Cu` (tracks, pads, zones, and any shapes/text drawn on the Cu layer) | black = burn/ablate paint, leaving traces masked |
| `silk` | `SilkS ∩ board_outline` | silkscreen artwork, separate job |
| `cut` | Edge.Cuts outline, `fill=none`, red 0.1 mm stroke | cut path |
| `drill` | one `<circle>`/`<ellipse>` per via and pad drill | drill targets |
| `pads` | (Cu pads ∪ *filled* `PCB_SHAPE`s drawn on Cu ∪ anything drawn on that side's Mask layer, board or footprint) ∩ board_outline | last pass on the finished board: strip the leftover etch mask off pads so they solder |

`cut` and `drill` are side-independent geometry but are still written per side, so each side's set carries that side's mirror and registers on its own. The dialog defaults `sides` from `copper_item_count` (tracks + SMD pads + zones; PTH pads are excluded because they are on every copper layer) and defaults mirror on for back only.

Key point: **copper is negative** (subtracted from the board outline) while **silkscreen and pads are positive**. Preserve this asymmetry — the laser removes paint everywhere black. Copper and silk are deliberately separate files (different power/speed); don't merge them back. The pads file is a *separate laser job run after etch/drill/cut*, not part of the mask artwork.

All files share the **same bbox**, taken from the board outline, so they overlay 1:1 in the laser software. Coordinates are emitted in mm with a `viewBox` in mm and `width`/`height` in `mm` units. Mirroring flips X about the shared width (`x_mm = w_mm - x_mm`); the per-side `mirror` flag is passed explicitly into `_write_svg`/`_poly_to_svg_path` and `_write_drill_svg`. If you add another writer, take and apply `mirror` the same way or layers will not register.

### KiCad API compatibility shims

`exporter.py` wraps several pcbnew calls because signatures differ across KiCad versions:

- `_bool_add` / `_bool_sub` / `_bool_inter` / `_simplify` pass `PM_FAST` only when the enum exists.
- `_board_outline_poly` passes `False` as the second arg (KiCad 10's `aInferOutlineIfNecessary` — `True` would silently invent an outline for a broken Edge.Cuts), and retries without it on `TypeError` (KiCad 9 and earlier, where that slot is an error-handler pointer).
- `_add_item_shape` retries `TransformShapeToPolygon` with a trailing `False` arg, then swallows failures.
- `_collect_drills` falls back from `GetDrillValue()` to `GetDrill()`.

When touching these, keep the try/except-narrow-then-broaden shape. Note the deliberate silent `except Exception: pass` in `_add_item_shape` and zone-fill collection — an unfillable zone or an unsupported item type must not abort a whole export.

### PNG rasterization

Out-of-process only. `_detect_rasterizer()` probes in order `inkscape`, `rsvg-convert`, `magick`, `convert`; `_rasterize()` holds a per-tool argv. Adding a tool means editing both. Missing tool → hard `RuntimeError` (not a silent skip) when PNG was requested.

Lookup goes through `_which()`, not `shutil.which()` directly: it falls back to the `_EXTRA_BIN_DIRS` probe (`/opt/homebrew/bin`, `/usr/local/bin`) and `_rasterize` substitutes the resolved absolute path into `cmd[0]`. Reason: a Finder/Dock-launched KiCad on macOS inherits a minimal `PATH` with no Homebrew. Keep new tool lookups on `_which()`.

## Known gaps

- `defaults()` sets `icon_file_name` to `icon.png`, which does not exist in the package.
- `metadata.json` is a PCM manifest but the repo is not packaged for PCM; the install scripts are the only install path, and the version there (`0.1.0`) is not referenced from code.
- `_pads_poly` emits the full pad copper shape, with no shrink/inset — a slightly hot pads pass can nibble the mask past the pad edge.
- `_collect_drills` does not deduplicate coincident holes and does not honor pad drill orientation (oval slots are emitted axis-aligned).
