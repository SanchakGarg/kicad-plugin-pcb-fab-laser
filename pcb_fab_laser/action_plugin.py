import os
import pcbnew
import wx

from .exporter import LaserExporter, ExportOptions, copper_item_count

SIDE_FRONT, SIDE_BACK, SIDE_BOTH = range(3)
SIDES_BY_CHOICE = {
    SIDE_FRONT: ("front",),
    SIDE_BACK: ("back",),
    SIDE_BOTH: ("front", "back"),
}


class PcbFabLaserPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "PCB Fab Laser SVG Export"
        self.category = "Fabrication"
        self.description = (
            "Export SVG layers (copper negative, silkscreen, Edge.Cuts) "
            "for xTool laser PCB fabrication."
        )
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), "icon.png")

    def Run(self):
        board = pcbnew.GetBoard()
        if board is None:
            wx.MessageBox("No board loaded.", "PCB Fab Laser", wx.OK | wx.ICON_ERROR)
            return

        dlg = ExportDialog(None, board)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()


class ExportDialog(wx.Dialog):
    def __init__(self, parent, board):
        super().__init__(parent, title="PCB Fab Laser SVG Export", size=(520, 600))
        self.board = board

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.rb_side = wx.RadioBox(
            panel, label="Sides to export",
            choices=["Front (F.Cu / F.SilkS)", "Back (B.Cu / B.SilkS)", "Both"])
        # Default to whichever side(s) actually carry the routing.
        n_front = copper_item_count(board, pcbnew.F_Cu)
        n_back = copper_item_count(board, pcbnew.B_Cu)
        if n_front and n_back:
            self.rb_side.SetSelection(SIDE_BOTH)
        else:
            self.rb_side.SetSelection(SIDE_FRONT if n_front > n_back else SIDE_BACK)
        sizer.Add(self.rb_side, flag=wx.EXPAND | wx.ALL, border=8)

        sizer.Add(wx.StaticText(panel, label="Files per side:"),
                  flag=wx.ALL, border=8)
        self.cb_copper = wx.CheckBox(
            panel, label="Copper (negative — keep traces → copper.svg)")
        self.cb_silk = wx.CheckBox(panel, label="Silkscreen (positive → silk.svg)")
        self.cb_copper.SetValue(True)
        self.cb_silk.SetValue(True)
        sizer.Add(self.cb_copper, flag=wx.LEFT | wx.RIGHT, border=16)
        sizer.Add(self.cb_silk, flag=wx.LEFT | wx.RIGHT, border=16)

        self.cb_edge = wx.CheckBox(panel, label="Edge.Cuts (board outline → cut.svg)")
        self.cb_edge.SetValue(True)
        sizer.Add(self.cb_edge, flag=wx.LEFT | wx.RIGHT, border=16)
        self.cb_drill = wx.CheckBox(panel, label="Drill holes (vias + pad drills → drill.svg)")
        self.cb_drill.SetValue(True)
        sizer.Add(self.cb_drill, flag=wx.LEFT | wx.RIGHT, border=16)
        self.cb_pads = wx.CheckBox(
            panel, label="Pads (strip paint off pads for soldering → pads.svg)")
        self.cb_pads.SetValue(True)
        sizer.Add(self.cb_pads, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=16)

        sizer.Add(wx.StaticLine(panel), flag=wx.EXPAND | wx.ALL, border=4)

        # Back-side artwork is seen through the board, so it is mirrored by default.
        self.cb_mirror_front = wx.CheckBox(panel, label="Mirror front-side files")
        self.cb_mirror_back = wx.CheckBox(panel, label="Mirror back-side files")
        self.cb_mirror_front.SetValue(False)
        self.cb_mirror_back.SetValue(True)
        sizer.Add(self.cb_mirror_front, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=8)
        sizer.Add(self.cb_mirror_back, flag=wx.ALL, border=8)

        png_box = wx.BoxSizer(wx.HORIZONTAL)
        self.cb_png = wx.CheckBox(panel, label="Also rasterize PNG @ DPI:")
        self.cb_png.SetValue(True)
        png_box.Add(self.cb_png, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        self.txt_dpi = wx.TextCtrl(panel, value="1000", size=(70, -1))
        png_box.Add(self.txt_dpi, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(png_box, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        out_box = wx.BoxSizer(wx.HORIZONTAL)
        out_box.Add(wx.StaticText(panel, label="Output dir:"),
                    flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        project_dir = os.path.dirname(board.GetFileName()) or os.path.expanduser("~")
        default_dir = os.path.join(project_dir, "export_artifacts")
        self.txt_outdir = wx.TextCtrl(panel, value=default_dir)
        out_box.Add(self.txt_outdir, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=4)
        btn_browse = wx.Button(panel, label="...")
        btn_browse.Bind(wx.EVT_BUTTON, self.on_browse)
        out_box.Add(btn_browse)
        sizer.Add(out_box, flag=wx.EXPAND | wx.ALL, border=8)

        btn_box = wx.BoxSizer(wx.HORIZONTAL)
        btn_export = wx.Button(panel, label="Export")
        btn_export.Bind(wx.EVT_BUTTON, self.on_export)
        btn_cancel = wx.Button(panel, id=wx.ID_CANCEL, label="Cancel")
        btn_box.AddStretchSpacer()
        btn_box.Add(btn_export, flag=wx.RIGHT, border=8)
        btn_box.Add(btn_cancel)
        sizer.Add(btn_box, flag=wx.EXPAND | wx.ALL, border=8)

        panel.SetSizer(sizer)

    def on_browse(self, _evt):
        dlg = wx.DirDialog(self, "Choose output directory",
                           defaultPath=self.txt_outdir.GetValue())
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_outdir.SetValue(dlg.GetPath())
        dlg.Destroy()

    def on_export(self, _evt):
        outdir = self.txt_outdir.GetValue().strip()
        if not outdir:
            wx.MessageBox("Output directory empty.", "Error",
                          wx.OK | wx.ICON_ERROR)
            return
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as e:
            wx.MessageBox(f"Cannot create {outdir}:\n{e}", "Error",
                          wx.OK | wx.ICON_ERROR)
            return

        try:
            dpi = int(self.txt_dpi.GetValue())
            if dpi <= 0:
                raise ValueError
        except ValueError:
            wx.MessageBox("DPI must be positive integer.", "Error",
                          wx.OK | wx.ICON_ERROR)
            return

        opts = ExportOptions(
            sides=SIDES_BY_CHOICE[self.rb_side.GetSelection()],
            include_copper=self.cb_copper.GetValue(),
            include_silk=self.cb_silk.GetValue(),
            include_edge=self.cb_edge.GetValue(),
            include_drill=self.cb_drill.GetValue(),
            include_pads=self.cb_pads.GetValue(),
            mirror_front=self.cb_mirror_front.GetValue(),
            mirror_back=self.cb_mirror_back.GetValue(),
            rasterize_png=self.cb_png.GetValue(),
            dpi=dpi,
            outdir=outdir,
        )

        try:
            exporter = LaserExporter(self.board, opts)
            written = exporter.run()
        except Exception as e:
            wx.MessageBox(f"Export failed:\n{e}", "Error",
                          wx.OK | wx.ICON_ERROR)
            return

        wx.MessageBox(
            "Exported:\n" + "\n".join(written) if written else "Nothing exported.",
            "PCB Fab Laser",
            wx.OK | wx.ICON_INFORMATION,
        )
        self.EndModal(wx.ID_OK)
