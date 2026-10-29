import os
import shutil
import subprocess
from dataclasses import dataclass
from xml.sax.saxutils import escape

import pcbnew


IU_PER_MM = pcbnew.IU_PER_MM if hasattr(pcbnew, "IU_PER_MM") else 1e6
ARC_ERROR_IU = int(0.005 * IU_PER_MM)  # 5 µm chord error for arc -> polygon

_PM_FAST = getattr(pcbnew.SHAPE_POLY_SET, "PM_FAST", None)


def _bool_add(target, other):
    if _PM_FAST is not None:
        target.BooleanAdd(other, _PM_FAST)
    else:
        target.BooleanAdd(other)


def _bool_sub(target, other):
    if _PM_FAST is not None:
        target.BooleanSubtract(other, _PM_FAST)
    else:
        target.BooleanSubtract(other)


def _bool_inter(target, other):
    if _PM_FAST is not None:
        target.BooleanIntersection(other, _PM_FAST)
    else:
        target.BooleanIntersection(other)


def _simplify(poly):
    if _PM_FAST is not None:
        poly.Simplify(_PM_FAST)
    else:
        poly.Simplify()


def _is_filled_shape(item):
    if item.Type() != pcbnew.PCB_SHAPE_T:
        return False
    try:
        return item.IsSolidFill()
    except AttributeError:
        return item.IsFilled()


SIDE_LAYERS = {
    "front": ("F_Cu", "F_SilkS", "F_Mask"),
    "back": ("B_Cu", "B_SilkS", "B_Mask"),
}


def copper_item_count(board, layer):
    """Tracks + SMD pads + zones on `layer`; used to guess which side is routed.
    Through-hole pads sit on every copper layer, so they would make a
    single-sided THT board look double-sided — skip them."""
    n = sum(1 for t in board.GetTracks() if t.IsOnLayer(layer))
    for fp in board.GetFootprints():
        n += sum(1 for p in fp.Pads()
                 if p.IsOnLayer(layer) and p.GetAttribute() != pcbnew.PAD_ATTRIB_PTH)
    for i in range(board.GetAreaCount()):
        if board.GetArea(i).IsOnLayer(layer):
            n += 1
    return n


@dataclass
class ExportOptions:
    sides: tuple  # subset of ("front", "back")
    include_copper: bool
    include_silk: bool
    include_edge: bool
    include_drill: bool
    include_pads: bool
    mirror_front: bool
    mirror_back: bool
    rasterize_png: bool
    dpi: int
    outdir: str


class LaserExporter:
    def __init__(self, board, opts: ExportOptions):
        self.board = board
        self.opts = opts

    # ---------- public ----------

    def run(self):
        board_outline = self._board_outline_poly()
        if board_outline.OutlineCount() == 0:
            raise RuntimeError("No closed Edge.Cuts outline found.")

        bbox = self._poly_bbox_iu(board_outline)
        base = os.path.splitext(os.path.basename(self.board.GetFileName()))[0] or "board"

        svg_paths = []
        for side in self.opts.sides:
            svg_paths.extend(self._export_side(side, base, board_outline, bbox))

        written = list(svg_paths)

        if self.opts.rasterize_png and svg_paths:
            tool = _detect_rasterizer()
            if tool is None:
                raise RuntimeError(
                    "PNG requested but no rasterizer found. "
                    "Install one of: inkscape, rsvg-convert, imagemagick."
                )
            for svg in svg_paths:
                png = os.path.splitext(svg)[0] + ".png"
                _rasterize(tool, svg, png, self.opts.dpi)
                written.append(png)

        return written

    def _export_side(self, side, base, board_outline, bbox):
        """Write every requested file for one side. Cut and drill are written per
        side too, so each side's job set registers under its own mirror setting."""
        cu_layer, silk_layer, mask_layer = (getattr(pcbnew, n) for n in SIDE_LAYERS[side])
        mirror = self.opts.mirror_front if side == "front" else self.opts.mirror_back

        def out(kind):
            return os.path.join(self.opts.outdir, f"{base}_{side}_{kind}.svg")

        svg_paths = []

        if self.opts.include_copper:
            copper = self._copper_poly(cu_layer)
            negative = pcbnew.SHAPE_POLY_SET(board_outline)
            _bool_sub(negative, copper)
            _simplify(negative)
            path = out("copper")
            self._write_svg(path, [(negative, "black")], bbox, mirror)
            svg_paths.append(path)

        if self.opts.include_silk:
            silk = self._silk_poly(silk_layer)
            _bool_inter(silk, board_outline)
            _simplify(silk)
            if silk.OutlineCount():
                path = out("silk")
                self._write_svg(path, [(silk, "black")], bbox, mirror)
                svg_paths.append(path)

        if self.opts.include_edge:
            path = out("cut")
            self._write_svg(
                path,
                [(board_outline, "none")],
                bbox,
                mirror,
                stroke="red",
                stroke_width_mm=0.1,
                # Laser software imports the background rect as a shape and
                # would cut it; the outline alone already spans the bbox.
                background=False,
            )
            svg_paths.append(path)

        if self.opts.include_drill:
            holes = self._collect_drills()
            if holes:
                path = out("drill")
                self._write_drill_svg(path, holes, bbox, mirror)
                svg_paths.append(path)

        if self.opts.include_pads:
            pads = self._pads_poly(cu_layer, mask_layer)
            _bool_inter(pads, board_outline)
            _simplify(pads)
            if pads.OutlineCount():
                path = out("pads")
                self._write_svg(path, [(pads, "black")], bbox, mirror)
                svg_paths.append(path)

        return svg_paths

    # ---------- geometry collection ----------

    def _board_outline_poly(self):
        outline = pcbnew.SHAPE_POLY_SET()
        try:
            # KiCad 10+: 2nd arg is aInferOutlineIfNecessary. Must be False, or a
            # broken Edge.Cuts silently becomes an invented outline.
            self.board.GetBoardPolygonOutlines(outline, False)
        except TypeError:
            self.board.GetBoardPolygonOutlines(outline)
        return outline

    def _copper_poly(self, layer):
        poly = pcbnew.SHAPE_POLY_SET()

        for track in self.board.GetTracks():
            if not track.IsOnLayer(layer):
                continue
            self._add_item_shape(track, layer, poly)

        # Graphic shapes/text drawn directly on the copper layer (e.g. a filled
        # rectangle used as a hand-made pad) are copper too.
        for d in self.board.GetDrawings():
            if d.IsOnLayer(layer):
                self._add_item_shape(d, layer, poly)

        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                if pad.IsOnLayer(layer):
                    self._add_item_shape(pad, layer, poly)
            for item in fp.GraphicalItems():
                if item.IsOnLayer(layer):
                    self._add_item_shape(item, layer, poly)

        for i in range(self.board.GetAreaCount()):
            zone = self.board.GetArea(i)
            if not zone.IsOnLayer(layer):
                continue
            try:
                fill = zone.GetFilledPolysList(layer)
                if fill is not None:
                    _bool_add(poly, fill)
            except Exception:
                # zone not filled or API mismatch
                pass

        _simplify(poly)
        return poly

    def _silk_poly(self, layer):
        poly = pcbnew.SHAPE_POLY_SET()

        for d in self.board.GetDrawings():
            if d.IsOnLayer(layer):
                self._add_item_shape(d, layer, poly)

        for fp in self.board.GetFootprints():
            for item in fp.GraphicalItems():
                if item.IsOnLayer(layer):
                    self._add_item_shape(item, layer, poly)
            ref = fp.Reference()
            if ref and ref.IsOnLayer(layer) and ref.IsVisible():
                self._add_item_shape(ref, layer, poly)
            val = fp.Value()
            if val and val.IsOnLayer(layer) and val.IsVisible():
                self._add_item_shape(val, layer, poly)

        _simplify(poly)
        return poly

    def _pads_poly(self, layer, mask_layer):
        """Pad copper on `layer`, for burning the paint back off the pads once
        traces, holes and outline are done — so they can be soldered.
        Filled shapes drawn on the copper layer count as hand-made pads; unfilled
        lines/outlines and text do not. Anything drawn on `mask_layer` is a
        solder-mask opening, so it is included as-is."""
        poly = pcbnew.SHAPE_POLY_SET()

        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                if pad.IsOnLayer(layer):
                    self._add_item_shape(pad, layer, poly)
            for item in fp.GraphicalItems():
                if item.IsOnLayer(layer) and _is_filled_shape(item):
                    self._add_item_shape(item, layer, poly)
                elif item.IsOnLayer(mask_layer):
                    self._add_item_shape(item, mask_layer, poly)

        for d in self.board.GetDrawings():
            if d.IsOnLayer(layer) and _is_filled_shape(d):
                self._add_item_shape(d, layer, poly)
            elif d.IsOnLayer(mask_layer):
                self._add_item_shape(d, mask_layer, poly)

        _simplify(poly)
        return poly

    def _collect_drills(self):
        """Return list of (cx_iu, cy_iu, dx_iu, dy_iu) for each drill.
        dx == dy for round drills; dy > dx (or vice versa) for oval slots."""
        holes = []

        for track in self.board.GetTracks():
            if track.Type() != pcbnew.PCB_VIA_T:
                continue
            via = track
            try:
                d = via.GetDrillValue()
            except Exception:
                d = via.GetDrill()
            if d <= 0:
                continue
            pos = via.GetPosition()
            holes.append((pos.x, pos.y, d, d))

        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                drill = pad.GetDrillSize()
                dx, dy = drill.x, drill.y
                if dx <= 0 or dy <= 0:
                    continue
                pos = pad.GetPosition()
                holes.append((pos.x, pos.y, dx, dy))

        return holes

    def _write_drill_svg(self, path, holes, bbox_iu, mirror):
        x0, y0, x1, y1 = bbox_iu
        w_mm = (x1 - x0) / IU_PER_MM
        h_mm = (y1 - y0) / IU_PER_MM

        body = []
        for cx, cy, dx, dy in holes:
            cx_mm = (cx - x0) / IU_PER_MM
            cy_mm = (cy - y0) / IU_PER_MM
            if mirror:
                cx_mm = w_mm - cx_mm
            rx_mm = dx / IU_PER_MM / 2.0
            ry_mm = dy / IU_PER_MM / 2.0
            if abs(rx_mm - ry_mm) < 1e-6:
                body.append(
                    f'  <circle cx="{cx_mm:.4f}" cy="{cy_mm:.4f}" '
                    f'r="{rx_mm:.4f}" fill="black" stroke="none"/>'
                )
            else:
                body.append(
                    f'  <ellipse cx="{cx_mm:.4f}" cy="{cy_mm:.4f}" '
                    f'rx="{rx_mm:.4f}" ry="{ry_mm:.4f}" '
                    f'fill="black" stroke="none"/>'
                )

        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{w_mm}mm" height="{h_mm}mm" '
            f'viewBox="0 0 {w_mm} {h_mm}">\n'
            f'  <rect x="0" y="0" width="{w_mm}" height="{h_mm}" fill="white"/>\n'
            + "\n".join(body) + "\n"
            "</svg>\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

    def _add_item_shape(self, item, layer, poly):
        try:
            item.TransformShapeToPolygon(
                poly, layer, 0, ARC_ERROR_IU, pcbnew.ERROR_INSIDE
            )
        except TypeError:
            try:
                item.TransformShapeToPolygon(
                    poly, layer, 0, ARC_ERROR_IU, pcbnew.ERROR_INSIDE, False
                )
            except Exception:
                pass
        except Exception:
            pass

    # ---------- bbox / svg ----------

    def _poly_bbox_iu(self, poly):
        bbox = poly.BBox()
        return (bbox.GetLeft(), bbox.GetTop(),
                bbox.GetRight(), bbox.GetBottom())

    def _write_svg(self, path, layers, bbox_iu, mirror, stroke="none", stroke_width_mm=0.0,
                   background=True):
        x0, y0, x1, y1 = bbox_iu
        w_mm = (x1 - x0) / IU_PER_MM
        h_mm = (y1 - y0) / IU_PER_MM

        body = []
        for poly, fill in layers:
            d = self._poly_to_svg_path(poly, x0, y0, w_mm, mirror)
            if not d:
                continue
            attrs = [
                f'd="{d}"',
                f'fill="{escape(fill)}"',
                'fill-rule="evenodd"',
                f'stroke="{escape(stroke)}"',
            ]
            if stroke_width_mm > 0:
                attrs.append(f'stroke-width="{stroke_width_mm}"')
            body.append(f'  <path {" ".join(attrs)}/>')

        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{w_mm}mm" height="{h_mm}mm" '
            f'viewBox="0 0 {w_mm} {h_mm}">\n'
            + (f'  <rect x="0" y="0" width="{w_mm}" height="{h_mm}" fill="white"/>\n'
               if background else "")
            + "\n".join(body) + "\n"
            "</svg>\n"
        )

        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

    def _poly_to_svg_path(self, poly_set, x0_iu, y0_iu, w_mm, mirror):
        parts = []

        def chain_to_d(chain):
            n = chain.PointCount()
            if n == 0:
                return ""
            pts = []
            for i in range(n):
                p = chain.CPoint(i)
                x_mm = (p.x - x0_iu) / IU_PER_MM
                y_mm = (p.y - y0_iu) / IU_PER_MM
                if mirror:
                    x_mm = w_mm - x_mm
                pts.append((x_mm, y_mm))
            head = f"M {pts[0][0]:.4f} {pts[0][1]:.4f}"
            tail = " ".join(f"L {x:.4f} {y:.4f}" for x, y in pts[1:])
            return f"{head} {tail} Z"

        for i in range(poly_set.OutlineCount()):
            outline = poly_set.Outline(i)
            d = chain_to_d(outline)
            if d:
                parts.append(d)
            for h in range(poly_set.HoleCount(i)):
                hole = poly_set.Hole(i, h)
                dh = chain_to_d(hole)
                if dh:
                    parts.append(dh)

        return " ".join(parts)


# ---------- rasterizer ----------

# GUI apps on macOS launched from Finder/Dock inherit a minimal PATH that
# excludes Homebrew dirs, so shutil.which() misses tools installed there.
# Probe these explicitly as a fallback.
_EXTRA_BIN_DIRS = (
    "/opt/homebrew/bin",  # Homebrew on Apple Silicon
    "/usr/local/bin",     # Homebrew on Intel macOS / common Linux
)


def _which(name):
    """Full path to `name` on PATH, or in known extra bin dirs, else None."""
    path = shutil.which(name)
    if path:
        return path
    for d in _EXTRA_BIN_DIRS:
        cand = os.path.join(d, name)
        if os.access(cand, os.X_OK):
            return cand
    return None


def _detect_rasterizer():
    for name in ("inkscape", "rsvg-convert", "magick", "convert"):
        if _which(name):
            return name
    return None


def _rasterize(tool, svg_path, png_path, dpi):
    if tool == "inkscape":
        cmd = [
            "inkscape",
            svg_path,
            f"--export-type=png",
            f"--export-dpi={dpi}",
            f"--export-filename={png_path}",
            "--export-background=white",
            "--export-background-opacity=1.0",
        ]
    elif tool == "rsvg-convert":
        cmd = [
            "rsvg-convert",
            "-d", str(dpi),
            "-p", str(dpi),
            "-b", "white",
            "-o", png_path,
            svg_path,
        ]
    elif tool in ("magick", "convert"):
        cmd = [
            tool,
            "-density", str(dpi),
            "-background", "white",
            "-flatten",
            svg_path,
            png_path,
        ]
    else:
        raise RuntimeError(f"Unknown rasterizer: {tool}")

    # Resolve to a full path so subprocess finds it even under the minimal PATH
    # a Finder/Dock-launched macOS app inherits.
    resolved = _which(tool)
    if resolved:
        cmd[0] = resolved

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{tool} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
