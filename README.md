# PCB Fab Laser SVG Export

A KiCad 9/10 `pcbnew` plugin that exports laser-fabrication artwork from a loaded board.
Built for xTool-style diode lasers ablating spray-painted copper-clad board: the laser
burns the paint away wherever the artwork is **black**, and the paint that survives
masks the copper for etching.

## Output

Every file shares one bounding box, taken from the board outline, so they overlay 1:1
in the laser software. Coordinates are millimetres, 1:1 scale.

Files are written per side, as `<board>_<side>_<kind>.svg` with `<side>` = `front`
or `back`:

| Kind | Content | Laser job |
|---|---|---|
| `copper` | board outline − Cu (including shapes drawn on the copper layer) | burn paint off everything that is *not* a trace, so the traces stay masked for etching |
| `silk` | SilkS, clipped to the outline | silkscreen artwork, as its own job |
| `cut` | Edge.Cuts outline, unfilled, red 0.1 mm stroke | cut the board out |
| `drill` | one dot per via and pad drill | drill targets |
| `pads` | Cu pads, filled shapes drawn on copper, and anything drawn on the solder mask layer (mask openings), clipped to the outline | burn the remaining paint off the pads so they can be soldered |

Copper and silkscreen are separate files so each can get its own power/speed.
`cut` and `drill` are written for each side, mirrored to match that side's files, so
every side's job set registers on its own.

Suggested order per side: **copper → etch → drill → cut → pads**. The pads pass runs last,
on the finished board, and strips the leftover paint (the etch mask, acting as a
solder mask) off just the pads.

Each layer is an independent file, so you can set its own laser power/speed per job
and skip any you don't want. Anything you uncheck in the dialog isn't written.

Optional PNG rasterization at a chosen DPI, for laser software that prefers rasters.
Needs one of `inkscape`, `rsvg-convert`, or ImageMagick (`magick` / `convert`) on
`PATH` — the export fails loudly rather than silently skipping if PNG is requested
and none is found. On macOS, `/opt/homebrew/bin` and `/usr/local/bin` are also
probed directly, since a Dock-launched KiCad inherits a minimal `PATH` that omits
Homebrew.

**Sides to export** is Front (`F.Cu` / `F.SilkS`), Back (`B.Cu` / `B.SilkS`) or Both.
It defaults to the side(s) with tracks, SMD pads or zones. Through-hole pads are
ignored for this guess, since they sit on both layers.

**Mirror** is set per side: back-side files are mirrored by default (the artwork is
seen through the board), front-side files are not.

## Install

The plugin is a plain Python package — installing means getting the `pcb_fab_laser/`
directory into KiCad's user scripting plugins folder.

### Linux / macOS

```bash
git clone https://github.com/saikhurana98/kicad-plugin-pcb-fab-laser.git
cd kicad-plugin-pcb-fab-laser
./install.sh
```

It symlinks the package into every KiCad version it finds under
`~/.local/share/kicad/<ver>/scripting/plugins/` (Linux) or
`~/Documents/KiCad/<ver>/scripting/plugins/` (macOS).

### Windows

```powershell
git clone https://github.com/saikhurana98/kicad-plugin-pcb-fab-laser.git
cd kicad-plugin-pcb-fab-laser
powershell -ExecutionPolicy Bypass -File install.ps1
```

Target is `%USERPROFILE%\Documents\KiCad\<ver>\scripting\plugins\`. The script
symlinks when it can (needs Developer Mode or an elevated shell) and otherwise
copies — a copy is a snapshot, so re-run the script after each update.

If you have Git Bash / MSYS / Cygwin, `./install.sh` works there too and does the
same thing.

### Manual (any OS, or if the scripts find nothing)

Copy or symlink `pcb_fab_laser/` into the user plugins folder for your KiCad version:

| OS | Path |
|---|---|
| Linux | `~/.local/share/kicad/<ver>/scripting/plugins/` |
| macOS | `~/Documents/KiCad/<ver>/scripting/plugins/` |
| Windows | `%USERPROFILE%\Documents\KiCad\<ver>\scripting\plugins\` |

`<ver>` is the major.minor KiCad version from **Help → About KiCad** (e.g. `9.0`).
KiCad creates the version folder on first run; if `scripting/plugins` isn't in it
yet, create those subfolders yourself.

### Then

In pcbnew: **Tools → External Plugins → Refresh Plugins**, then run
**PCB Fab Laser SVG Export**.

If the plugin doesn't appear, open **Tools → Scripting Console** — plugin import
errors are reported there, not on stdout.

## Development

Symlink installs are live: after editing, **Refresh Plugins** picks up new files, but
pcbnew caches already-imported modules, so **restart KiCad** to reliably load changes
to `exporter.py` / `action_plugin.py`.

There is no build step and no test suite — `import pcbnew` only works inside KiCad's
own Python, so the code can only be exercised from pcbnew.

## Requirements

- KiCad 9 or 10 with Python scripting (the stock builds have it)
- A board with a closed `Edge.Cuts` outline — the export aborts without one, since
  every file's bounding box comes from it
- Optional: `inkscape`, `rsvg-convert`, or ImageMagick for PNG output

## License

MIT
