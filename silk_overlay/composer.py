"""wx popup composer; no board mutation until Apply."""
import math
import json
from uuid import uuid4
from copy import deepcopy
from pathlib import Path

import pcbnew as pcb
import wx
from shapely.geometry import GeometryCollection

from . import board as native
from .geometry import polygons, load_svg, clearance_geometry
from .groups import PALETTE, validate_rules, custom_layers
from .group_ui import GroupPanel
from .rule_ui import RulePanel
from .jobs import LatestJob, PreviewCalculator
from . import placement
from .project import placement_settings, cleanup_width
from .stack import StackRequest, StackResult
from .refresh import discover, UpdateDialog
from .branding import APP_NAME
from .inversion import layer_name as inversion_layer, settings_values as inversion_settings


class Canvas(wx.Panel):
    def __init__(self, parent, composer):
        super().__init__(parent)
        self.composer = composer
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.scale = 5.
        self.center = (100., 100.)
        self.drag = None
        self.path_cache = {}
        self.Bind(wx.EVT_PAINT, self.paint)
        self.Bind(wx.EVT_LEFT_DOWN, self.down)
        self.Bind(wx.EVT_LEFT_UP, self.up)
        self.Bind(wx.EVT_RIGHT_DOWN, self.down)
        self.Bind(wx.EVT_RIGHT_UP, self.up)
        self.Bind(wx.EVT_MOTION, self.motion)
        self.Bind(wx.EVT_MOUSEWHEEL, self.wheel)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, lambda event: setattr(self, "drag", None))
        self.Bind(wx.EVT_SIZE, lambda event: self.Refresh())

    def screen(self, x, y):
        w, h = self.GetClientSize()
        return ((x-self.center[0])*self.scale+w/2, (y-self.center[1])*self.scale+h/2)

    def world(self, x, y):
        w, h = self.GetClientSize()
        return ((x-w/2)/self.scale+self.center[0], (y-h/2)/self.scale+self.center[1])

    def fit(self, geometry):
        if geometry is None or geometry.is_empty:
            return
        x0, y0, x1, y1 = geometry.bounds
        w, h = self.GetClientSize()
        self.center = ((x0+x1)/2, (y0+y1)/2)
        self.scale = min(max(100, w-90)/max(1, x1-x0), max(100, h-90)/max(1, y1-y0))
        self.Refresh()

    def path_for(self, gc, geometry, pixel_scale=None):
        # World-coordinate paths survive panning. Quantised detail levels survive
        # small zoom changes. Bounded cache; never retain a path per mouse event.
        tolerance = .3 / max(.00001, pixel_scale if pixel_scale is not None else self.scale)
        lod = max(-16, min(16, math.floor(math.log2(tolerance))))
        key = (id(geometry), lod)
        cached = self.path_cache.get(key)
        if cached is None:
            path = gc.CreatePath()
            self.add_rings(path, geometry, 2.**lod)
            if len(self.path_cache) >= 128:
                self.path_cache.pop(next(iter(self.path_cache)))
            self.path_cache[key] = (geometry, path)
        else:
            path = cached[1]
        return path

    def draw_geometry(self, gc, geometry, fill, stroke=None, pixel_scale=None):
        if geometry is None or geometry.is_empty:
            return
        path = self.path_for(gc, geometry, pixel_scale)
        gc.SetBrush(wx.Brush(fill) if fill else wx.TRANSPARENT_BRUSH)
        gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(wx.Colour(stroke)).Width(1/self.scale)) if stroke else wx.TRANSPARENT_PEN)
        gc.DrawPath(path, wx.ODDEVEN_RULE)

    def add_rings(self, path, geometry, tolerance):
        # Screen-only simplification, never used in exported geometry.
        for poly in polygons(geometry.simplify(tolerance, preserve_topology=True)):
            for ring in [poly.exterior, *poly.interiors]:
                points = list(ring.coords)
                path.MoveToPoint(*points[0])
                for p in points[1:]:
                    path.AddLineToPoint(*p)
                path.CloseSubpath()

    def cover_outside(self, gc, boundary, colour):
        """Even-odd viewport stencil; no polygon boolean operation while dragging."""
        if boundary is None:
            return
        path = gc.CreatePath()
        w, h = self.GetClientSize()
        x, y = self.world(0, 0)
        path.AddRectangle(x, y, w/self.scale, h/self.scale)
        path.AddPath(self.path_for(gc, boundary))
        gc.SetBrush(wx.Brush(colour))
        gc.SetPen(wx.TRANSPARENT_PEN)
        gc.DrawPath(path, wx.ODDEVEN_RULE)

    def corners(self):
        c = self.composer
        if not c.preview_valid:
            return []
        x0, y0, x1, y1 = placement.bounds(c.original.bounds, c.settings)
        return [self.screen(x0, y0), self.screen(x1, y0), self.screen(x1, y1), self.screen(x0, y1)]

    def draw_scene(self, gc):
        c = self.composer
        gc.PushState()
        w, h = self.GetClientSize()
        gc.Translate(w/2, h/2)
        gc.Scale(self.scale, self.scale)
        gc.Translate(-self.center[0], -self.center[1])
        self.draw_geometry(gc, c.outline, "#203e39")
        inverted = bool(inversion_layer(c.settings, c.settings["side"]))
        for key, geometry in getattr(c, "stack_geometries", {}).items():
            if inverted or key != c.entries[c.active_target_index]["key"]:
                self.draw_geometry(gc, geometry, "#f1f6fb")
        if inverted:
            self.draw_geometry(gc, c.inversion_result, "#f1f6fb")
        if c.preview_valid and not inverted:
            if cleanup_width(c.settings) > 0 and c.artwork_ready:
                self.draw_geometry(gc, c.result, "#f1f6fb")
            else:
                gc.PushState()
                a, b, d, e, x, y = placement.matrix(c.settings)
                gc.ConcatTransform(gc.CreateMatrix(a, d, b, e, x, y))
                self.draw_geometry(gc, c.original, "#f1f6fb", pixel_scale=self.scale*c.settings["width"]/2000.)
                gc.PopState()
        if c.clearance_key is not None:
            blockers, boundary = c.cutouts
            self.draw_geometry(gc, blockers, "#203e39")
            if boundary is not None:
                self.cover_outside(gc, boundary, "#203e39")
        if c.show_colours.GetValue() and c.group_inks:
            for key, geometry in c.group_inks.items():
                self.draw_geometry(gc, geometry, PALETTE[key][1])
        if c.show_pads.GetValue() and c.pad_geometry is not None:
            self.draw_geometry(gc, c.pad_geometry, "#b98453")
        # Existing silk remains visible above its coloured exclusion area.
        self.draw_geometry(gc, c.silk, "#f1f6fb")
        if c.settings.get("clip") and c.outline is not None:
            self.cover_outside(gc, c.outline, "#151c25")
        self.draw_geometry(gc, c.outline, None, "#71998b")
        if c.selecting_objects:
            for item in c.board_objects:
                if item.key in c.group_panel.selected:
                    self.draw_geometry(gc, item.hit_geometry, "#FF00FF")
        gc.PopState()

    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush("#151c25"))
        dc.Clear()
        gc = wx.GraphicsContext.Create(dc)
        self.draw_scene(gc)
        c = self.composer
        corners = [] if c.selecting_objects else self.corners()
        if corners:
            gc.SetPen(wx.Pen("#60d7ed", 1))
            gc.SetBrush(wx.TRANSPARENT_BRUSH)
            gc.StrokeLines(corners+[corners[0]])
            gc.SetBrush(wx.Brush("#60d7ed"))
            for x, y in corners:
                gc.DrawRectangle(x-4, y-4, 8, 8)
        dc.SetTextForeground(wx.Colour("#b8c8d8"))
        hints = ("Alt drag: Box select graphics", "Shift: Add selection", "Shift+Ctrl: Remove selection") if c.selecting_objects else (
            "Drag artwork · corners resize · right-drag pans · wheel zooms",)
        for line, text in enumerate(hints):
            dc.DrawText(text, 16, 14 + line * 22)
        if self.drag and self.drag[0] == "select":
            _, start, end, _ = self.drag
            x0, y0 = self.screen(*start)
            x1, y1 = self.screen(*end)
            gc.SetBrush(wx.TRANSPARENT_BRUSH)
            gc.SetPen(wx.Pen("#ffffff", 1))
            gc.DrawRectangle(min(x0,x1), min(y0,y1), abs(x1-x0), abs(y1-y0))
        if c.settings.get("side") == "Back":
            dc.DrawText("Back layer shown from the front, matching PCB coordinates", 16, 14 + len(hints) * 22)

    def down(self, event):
        p = event.GetPosition()
        self.drag = None
        if event.RightDown():
            self.drag = ("pan", p.x, p.y, self.center)
        elif self.composer.selecting_objects:
            mode = "remove" if event.ShiftDown() and event.ControlDown() else "add" if event.ShiftDown() else "replace"
            if event.AltDown():
                start = self.world(p.x, p.y)
                self.drag = ("select", start, start, mode)
            else:
                # A click selects; movement past the click threshold pans.
                self.drag = ("pickpan", p.x, p.y, self.center, mode)
        elif self.composer.preview_valid:
            world = self.world(p.x, p.y)
            c = self.composer
            corners = self.corners()
            if any(math.hypot(p.x-x, p.y-y) < 12 for x, y in corners):
                radius = math.hypot(world[0]-c.settings["x"], world[1]-c.settings["y"])
                self.drag = ("resize", radius, c.settings["width"])
            else:
                x0, y0, x1, y1 = placement.bounds(c.original.bounds, c.settings)
                if x0 <= world[0] <= x1 and y0 <= world[1] <= y1:
                    self.drag = ("move", world, c.settings["x"], c.settings["y"])
        if self.drag is None:
            self.drag = ("pan", p.x, p.y, self.center)
        if self.drag and not self.HasCapture():
            self.CaptureMouse()

    def up(self, event):
        if self.drag and self.drag[0] == "select":
            _, start, _, mode = self.drag
            end = self.world(event.GetX(), event.GetY())
            self.composer.group_panel.select_canvas(start, end, mode, 6/self.scale, box_select=True)
        elif self.drag and self.drag[0] == "pickpan":
            _, x, y, center, mode = self.drag
            if math.hypot(event.GetX()-x, event.GetY()-y) < 6:
                point = self.world(event.GetX(), event.GetY())
                self.composer.group_panel.select_canvas(point, point, mode, 6/self.scale)
            else:
                self.center = (center[0]-(event.GetX()-x)/self.scale, center[1]-(event.GetY()-y)/self.scale)
        self.drag = None
        if self.HasCapture():
            self.ReleaseMouse()
        self.Refresh()

    def motion(self, event):
        if not self.drag:
            return
        p = event.GetPosition()
        kind, *args = self.drag
        c = self.composer
        if kind == "select":
            start, _, mode = args
            self.drag = ("select", start, self.world(p.x, p.y), mode)
            self.Refresh()
            return
        if kind == "pickpan":
            x, y, center, _ = args
            if math.hypot(p.x-x, p.y-y) < 6:
                return
            self.drag = ("pan", x, y, center)
            kind, args = "pan", (x, y, center)
        if kind == "pan":
            x, y, center = args
            self.center = (center[0]-(p.x-x)/self.scale, center[1]-(p.y-y)/self.scale)
            self.Refresh()
            return
        x, y = self.world(p.x, p.y)
        if kind == "move":
            initial, old_x, old_y = args
            c.set_numeric("x", old_x+x-initial[0])
            c.set_numeric("y", old_y+y-initial[1])
        else:
            radius, width = args
            ratio = math.hypot(x-c.settings["x"], y-c.settings["y"])/max(radius, .001)
            c.set_numeric("width", max(.01, min(2000., width*ratio)))
        # Coalesce motion to one update per display interval.
        c.schedule_motion()

    def wheel(self, event):
        p = event.GetPosition()
        before = self.world(p.x, p.y)
        self.scale = max(.05, min(3000., self.scale * 1.2**(event.GetWheelRotation()/event.GetWheelDelta())))
        after = self.world(p.x, p.y)
        self.center = (self.center[0]+before[0]-after[0], self.center[1]+before[1]-after[1])
        self.Refresh()


class Composer(wx.Dialog):
    def __init__(self, parent, board):
        super().__init__(parent, title=APP_NAME, size=(1600, 1100),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        display = wx.Display.GetFromPoint(wx.GetMousePosition())
        work_area = wx.Display(display if display != wx.NOT_FOUND else 0).GetClientArea()
        preferred = self.FromDIP((1600, 1100))
        width = min(preferred.width, int(work_area.width * .94))
        height = min(preferred.height, int(work_area.height * .94))
        self.SetSize((width, height))
        self.SetMinSize((min(self.FromDIP(1000), width), min(self.FromDIP(700), height)))
        self.Move((work_area.x + (work_area.width - width) // 2,
                   work_area.y + (work_area.height - height) // 2))
        self.board = board
        self.previous = None
        self.active_target_index = 0
        self.mask = self.silk = self.outline = self.placed = self.result = None
        self.original = None
        self.preview_valid = False
        self.artwork_key = self.artwork_request_key = self.cleaned_key = None
        self.loaded_source = None
        self.clearance_key = None
        self.board_objects = []
        self.group_inks = {}
        self.group_bodies = {}
        self.group_blockers = GeometryCollection()
        self.rules_error = None
        self.rules_revision = 0
        self.geometry_dirty = True
        self.pending_apply = False
        self.closed = False
        self.pad_geometry = None
        self.settings = {"source": "", "width": 40., "x": 100., "y": 100., "angle": 0.,
                         "side": "Front", "mirror": False, "mask_gap": .2,
                         "silk_gap": .2, "edge_gap": .2, "clip": True, "minimum_width": 0.}
        self.entries, shared, self.overlays = discover(board, allow_missing=True)
        self.configuration_error = shared.pop("recovery_required", None)
        self.settings.update(shared)
        self.colour_rules = shared["colour_rules"]
        self.assignments = shared["object_colours"]
        self.matching_rules = shared["matching_rules"]
        self.source_cache, self.snapshot_cache, self.space_cache = {}, {}, {}
        self.inversion_cache = {}
        self.inversion_geometry = GeometryCollection()
        self.inversion_result = GeometryCollection()
        self.source_revision = 0
        self.stack_geometries = {}
        if not self.entries:
            self.entries.append(dict(key=str(uuid4()), settings=placement_settings(self.settings), previous=None))
        selected = [i for i, entry in enumerate(self.entries) if entry["previous"] is not None and entry["previous"].IsSelected()]
        self.active_target_index = selected[0] if selected else 0
        self.settings.update(self.entries[self.active_target_index]["settings"])
        self.previous = self.entries[self.active_target_index]["previous"]
        self.resolved_colours = {}
        self.loaded_custom_layers = set()
        self.custom_choices = [board.GetLayerName(layer) for layer in range(pcb.PCB_LAYER_ID_COUNT)
                               if board.IsLayerEnabled(layer)]
        self.controls = {}
        outer = wx.BoxSizer(wx.VERTICAL)
        self.splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        self.canvas = Canvas(self.splitter, self)
        self.tabs = wx.Notebook(self.splitter)
        sidebar_width = min(self.FromDIP(600), int(width * .46))
        minimum_pane = min(self.FromDIP(340), max(1, (width - self.FromDIP(40)) // 2))
        self.tabs.SetMinSize((minimum_pane, -1))
        self.splitter.SetMinimumPaneSize(minimum_pane)
        self.splitter.SetSashGravity(1.)
        panel = wx.ScrolledWindow(self.tabs)
        panel.SetScrollRate(0, 12)
        self.tabs.AddPage(panel, "Artworks")
        form = wx.BoxSizer(wx.VERTICAL)
        self.targets = wx.ListBox(panel, size=(-1, self.FromDIP(100)))
        self.refresh_targets()
        self.targets.Bind(wx.EVT_LISTBOX, self.select_target)
        form.Add(wx.StaticText(panel, label="SVGs"), 0, wx.TOP, 8)
        form.Add(self.targets, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 6)
        entry_buttons = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (("Add SVG", self.add_artwork), ("Remove SVG", self.remove_artwork)):
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            entry_buttons.Add(button, 1, wx.RIGHT, 4)
        form.Add(entry_buttons, 0, wx.EXPAND | wx.BOTTOM, 6)
        self.file = wx.FilePickerCtrl(panel, wildcard="SVG artwork (*.svg)|*.svg", style=wx.FLP_OPEN | wx.FLP_FILE_MUST_EXIST | wx.FLP_USE_TEXTCTRL)
        self.file.Bind(wx.EVT_FILEPICKER_CHANGED, self.source_changed)
        self.file.SetPath(self.settings["source"])
        form.Add(self.file, 0, wx.EXPAND | wx.BOTTOM, 10)
        grid = wx.FlexGridSizer(cols=2, vgap=7, hgap=10)
        grid.AddGrowableCol(1)
        for key, label in [("width", "Width (mm)"), ("angle", "Rotation (° CCW)"),
                           ("x", "Centre X (mm)"), ("y", "Centre Y (mm)")]:
            grid.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            control = wx.TextCtrl(panel, value=str(self.settings[key]), style=wx.TE_PROCESS_ENTER)
            control.Bind(wx.EVT_TEXT, self.schedule_update)
            control.Bind(wx.EVT_TEXT_ENTER, lambda event: self.update_preview())
            self.controls[key] = control
            grid.Add(control, 1, wx.EXPAND)
        form.Add(grid, 0, wx.EXPAND | wx.BOTTOM, 12)
        self.side = wx.Choice(panel, choices=["Front", "Back"])
        self.side.SetStringSelection(self.settings["side"])
        self.side.Bind(wx.EVT_CHOICE, self.side_changed)
        form.Add(self.side, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.mirror = wx.CheckBox(panel, label="Mirror artwork horizontally")
        self.mirror.SetValue(self.settings["mirror"])
        self.mirror.Bind(wx.EVT_CHECKBOX, lambda event: self.update_preview())
        form.Add(self.mirror, 0, wx.BOTTOM, 8)
        fit_board = wx.Button(panel, label="Fit board in view")
        fit_board.Bind(wx.EVT_BUTTON, lambda event: self.canvas.fit(self.outline if self.outline is not None else self.mask))
        form.Add(fit_board, 0, wx.EXPAND | wx.BOTTOM, 6)
        fit_art = wx.Button(panel, label="Fit artwork in view")
        fit_art.Bind(wx.EVT_BUTTON, self.fit_artwork)
        form.Add(fit_art, 0, wx.EXPAND | wx.BOTTOM, 12)
        form.AddStretchSpacer()
        self.status = wx.StaticText(panel, label="Choose an SVG to start.")
        form.Add(self.status, 0, wx.EXPAND | wx.TOP, 12)
        panel.SetSizer(form)
        rules_page = wx.ScrolledWindow(self.tabs)
        rules_page.SetScrollRate(0, 12)
        rules_form = wx.BoxSizer(wx.VERTICAL)
        self.rule_panel = RulePanel(rules_page, self)
        rules_form.Add(self.rule_panel, 1, wx.EXPAND | wx.ALL, 6)
        self.group_panel = GroupPanel(rules_page, self)
        rules_form.Add(self.group_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        rules_page.SetSizer(rules_form)
        self.tabs.AddPage(rules_page, "Rules")
        self.tabs.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, lambda event: self.canvas.Refresh())
        self.splitter.SplitVertically(self.canvas, self.tabs, -sidebar_width)
        outer.Add(self.splitter, 1, wx.EXPAND | wx.ALL, 10)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.show_pads = wx.CheckBox(self, label="Show pads")
        self.show_pads.SetValue(True)
        self.show_pads.Bind(wx.EVT_CHECKBOX, lambda event: self.pads_changed())
        buttons.Add(self.show_pads, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        self.show_colours = wx.CheckBox(self, label="Show rule colors")
        self.show_colours.SetValue(True)
        self.show_colours.Bind(wx.EVT_CHECKBOX, lambda event: self.canvas.Refresh())
        buttons.Add(self.show_colours, 0, wx.ALIGN_CENTER_VERTICAL)
        buttons.AddStretchSpacer()
        cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
        cancel.Bind(wx.EVT_BUTTON, self.cancel)
        self.apply_button = wx.Button(self, label="Apply")
        self.apply_button.Enable(False)
        self.apply_button.Bind(wx.EVT_BUTTON, self.apply)
        buttons.Add(cancel, 0, wx.RIGHT, 8)
        buttons.Add(self.apply_button)
        outer.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        self.SetSizer(outer)
        self.Layout()
        self.splitter.SetSashPosition(-sidebar_width)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda event: self.update_preview(), self.timer)
        self.cleanup_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda event: self.request_artwork(), self.cleanup_timer)
        self.group_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda event: self.request_preview(), self.group_timer)
        self.worker = LatestJob(PreviewCalculator())
        self.poll_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.poll_geometry, self.poll_timer)
        self.poll_timer.Start(80)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        try:
            self.load_layers()
        except Exception:
            self.shutdown()
            super().Destroy()
            raise
        if not self.settings["source"] and self.outline is not None and not self.outline.is_empty:
            x0, y0, x1, y1 = self.outline.bounds
            self.set_numeric("x", (x0+x1)/2)
            self.set_numeric("y", (y0+y1)/2)
            self.set_numeric("width", max(1., (x1-x0)*.7))
        self.update_preview()
        wx.CallAfter(self.canvas.fit, self.outline if self.outline is not None else self.mask)

    def on_close(self, event):
        self.shutdown()
        event.Skip()

    def cancel(self, event):
        self.shutdown()
        self.EndModal(wx.ID_CANCEL)

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.pending_apply = False
        for name in ("timer", "group_timer", "poll_timer", "cleanup_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.Stop()
        worker = getattr(self, "worker", None)
        if worker is not None:
            worker.close()

    def Destroy(self):
        self.shutdown()
        return super().Destroy()

    @property
    def selecting_objects(self):
        return hasattr(self, "tabs") and self.tabs.GetSelection() >= 1

    def fit_artwork(self, event):
        if self.preview_valid:
            from shapely.geometry import box
            self.canvas.fit(box(*placement.bounds(self.original.bounds, self.settings)))

    def schedule_motion(self):
        self.invalidate_artwork()
        if not self.timer.IsRunning():
            self.timer.StartOnce(16)

    @property
    def artwork_ready(self):
        return (not self.geometry_dirty and self.artwork_key is not None
                and self.cleaned_key == self.artwork_key and self.result is not None)

    def invalidate_artwork(self):
        self.cleanup_timer.Stop()
        self.artwork_request_key = self.cleaned_key = None
        self.apply_button.Enable(False)
        # A rules job owns the same worker while geometry_dirty is true.
        # Do not cancel it just because artwork placement changed.
        if not self.geometry_dirty:
            self.worker.invalidate()

    def request_artwork(self):
        self.cleanup_timer.Stop()
        if (self.closed or self.artwork_key is None or self.geometry_dirty or
                self.artwork_ready or self.artwork_request_key == self.artwork_key):
            return
        self.artwork_request_key = self.artwork_key
        self.rules_error = None
        side = self.settings["side"]
        entries = tuple((entry["key"], dict(entry["settings"])) for entry in self.entries
                        if entry["settings"]["source"] and entry["settings"]["side"] == side)
        self.worker.request(StackRequest(self.artwork_key, entries, self.shared_settings(),
                            {side: (self.group_blockers, self.outline)},
                            str(Path(self.board.GetFileName()).parent), cached_sources=dict(self.source_cache),
                            inversions={side: self.inversion_geometry}))

    def artwork_status(self):
        s = self.settings
        error = self.configuration_error or self.rules_error
        if error:
            self.status.SetLabel("Cannot apply: " + error)
            self.status.Wrap(350)
            return
        if not self.preview_valid:
            if inversion_layer(s, s["side"]):
                self.status.SetLabel("Inversion ready." if self.artwork_ready else "Updating inversion…")
            else:
                self.status.SetLabel("Choose an SVG for this row. Rules apply to the entire stack.")
            return
        bounds = self.original.bounds
        height = (bounds[3]-bounds[1])*s["width"]/2000.
        text = f"{s['width']:.2f} × {height:.2f} mm artwork"
        if inversion_layer(s, s["side"]):
            text += "\nInversion ready." if self.artwork_ready else "\nUpdating inversion; showing previous result."
        elif cleanup_width(s) > 0:
            text += ("\nSliver removal ready." if self.artwork_ready else
                     "\nSliver removal pending; showing uncleaned preview.")
        if self.artwork_ready and self.result.is_empty and self.inversion_result.is_empty:
            text += "\nNo ink remains. Reduce cleanup or adjust cutouts."
        if self.outline is None:
            text += "\nNo valid board outline: clipping off."
        self.status.SetLabel(text)

    def request_preview(self, check_layers=True):
        if self.closed:
            return
        self.group_timer.Stop()
        self.invalidate_artwork()
        self.geometry_dirty = True
        self.apply_button.Enable(False)
        self.worker.invalidate()
        try:
            validate_rules(self.colour_rules, self.matching_rules)
            self.load_inversion()
            if check_layers and custom_layers(self.colour_rules, self.settings["side"]) != self.loaded_custom_layers:
                self.load_layers()
                return
            self.rules_error = None
            payload = (list(self.board_objects), dict(self.assignments), deepcopy(self.matching_rules),
                       deepcopy(self.colour_rules), self.show_pads.GetValue(), self.settings["side"])
            self.worker.request(payload)
            self.rule_panel.set_status("Checking PCB geometry before Apply…" if self.pending_apply else
                                       "Updating exclusions… You can keep editing.")
        except Exception as error:
            self.rules_error = str(error)
            self.rule_panel.set_status(self.rules_error)
            self.pending_apply = False
            self.tabs.Enable(True)
            self.canvas.Enable(True)
        self.artwork_status()
        self.canvas.Refresh()

    def groups_changed(self, defer=False):
        self.invalidate_artwork()
        self.geometry_dirty = True
        self.apply_button.Enable(False)
        self.worker.invalidate()
        self.group_timer.Stop()
        self.group_timer.StartOnce(300 if defer else 1)
        self.rule_panel.set_status("Preview updating…")

    def poll_geometry(self, event):
        completed = self.worker.poll()
        if completed is None or self.closed:
            return
        _, result, error = completed
        if error is not None:
            self.artwork_request_key = None
            self.rules_error = str(error)
            self.rule_panel.set_status(self.rules_error)
            self.artwork_status()
            self.apply_button.Enable(False)
            self.pending_apply = False
            self.tabs.Enable(True)
            self.canvas.Enable(True)
            return
        if isinstance(result, StackResult):
            if result.key != self.artwork_key or self.geometry_dirty:
                return
            self.stack_geometries = result.geometries
            self.inversion_result = result.inversions.get(self.settings["side"], GeometryCollection())
            self.result = result.geometries.get(self.entries[self.active_target_index]["key"], GeometryCollection())
            self.cleaned_key = result.key
            for entry in self.entries:
                if entry["key"] in result.sources:
                    self.source_cache[str(self.resolve_source(entry["settings"]["source"]))] = result.sources[entry["key"]]
            # Paths are bounded and keyed by geometry identity. Keep cached
            # board paths when only the artwork cleanup result changed.
            self.artwork_status()
            self.apply_button.Enable(not self.pending_apply and not self.configuration_error)
            self.canvas.Refresh()
            return
        (self.resolved_colours, self.group_blockers, self.group_inks,
         self.group_bodies, self.pad_geometry, elapsed) = result
        self.geometry_dirty = False
        self.rules_error = None
        self.rules_revision += 1
        self.space_cache[self.space_key()] = result
        while len(self.space_cache) > 4:
            self.space_cache.pop(next(iter(self.space_cache)))
        self.clearance_key = None
        self.canvas.path_cache.clear()
        count = sum(colour is not None for colour in self.resolved_colours.values())
        self.rule_panel.set_status(f"{count} objects matched · preview ready ({elapsed:.2f} s)")
        self.update_preview()

    def pads_changed(self):
        if self.show_pads.GetValue() and self.pad_geometry is None:
            self.request_preview()
        self.canvas.Refresh()

    def set_numeric(self, key, value):
        self.settings[key] = value
        self.controls[key].ChangeValue(f"{value:.4f}".rstrip("0").rstrip("."))

    def schedule_update(self, event):
        if hasattr(self, "timer"):
            self.invalidate_artwork()
            self.timer.StartOnce(180)

    def shared_settings(self):
        return dict(colour_rules=deepcopy(self.colour_rules), object_colours=dict(self.assignments),
                    matching_rules=deepcopy(self.matching_rules), edge_gap=self.settings["edge_gap"],
                    clip=self.settings["clip"], minimum_width=self.settings["minimum_width"],
                    minimum_width_enabled=self.settings["minimum_width_enabled"], **inversion_settings(self.settings))

    def load_inversion(self):
        name = inversion_layer(self.settings, self.settings["side"])
        if name and name not in self.inversion_cache:
            self.inversion_cache[name] = native.read_inversion(self.board, name)
            while len(self.inversion_cache) > 2:
                self.inversion_cache.pop(next(iter(self.inversion_cache)))
        self.inversion_geometry = self.inversion_cache[name] if name else GeometryCollection()

    def inversion_changed(self):
        self.stack_geometries = {}
        self.inversion_result = GeometryCollection()
        self.invalidate_artwork()
        self.request_preview()

    def space_key(self):
        return (self.settings["side"], tuple(sorted(custom_layers(self.colour_rules, self.settings["side"]))),
                json.dumps([self.colour_rules, self.assignments, self.matching_rules,
                            self.show_pads.GetValue()], sort_keys=True))

    def load_layers(self):
        self.invalidate_artwork()
        self.geometry_dirty = True
        self.worker.invalidate()
        self.group_timer.Stop()
        self.clearance_key = None
        self.stack_geometries = {}
        self.inversion_result = GeometryCollection()
        self.group_blockers = GeometryCollection()
        self.group_inks = {}
        required_custom = custom_layers(self.colour_rules, self.settings["side"])
        key = (self.settings["side"], tuple(sorted(required_custom)))
        if key not in self.snapshot_cache:
            with wx.BusyCursor():
                self.snapshot_cache[key] = native.read_snapshot(self.board, self.settings["side"], required_custom)
            while len(self.snapshot_cache) > 4:
                self.snapshot_cache.pop(next(iter(self.snapshot_cache)))
            # Completed rule geometry refers to snapshot objects; discard entries
            # from other custom-layer sets so evicted snapshots can be released.
            self.space_cache = {k: value for k, value in self.space_cache.items()
                                if k[:2] in self.snapshot_cache}
        self.mask, self.silk, self.outline, self.board_objects = self.snapshot_cache[key]
        self.loaded_custom_layers = required_custom
        self.group_panel.refresh_objects()
        self.clip.Enable(self.outline is not None)
        # Preserve the shared clipping setting even if this side has no outline.
        cached = self.space_cache.get(self.space_key())
        if cached is not None:
            (self.resolved_colours, self.group_blockers, self.group_inks,
             self.group_bodies, self.pad_geometry, _) = cached
            self.geometry_dirty = False
            self.rules_error = None
            self.rules_revision += 1
            self.rule_panel.set_status("Cached available space ready.")
            self.update_preview()
        else:
            self.pad_geometry = None
            self.request_preview(check_layers=False)

    def resolve_source(self, value):
        source = Path(value)
        return source if source.is_absolute() else Path(self.board.GetFileName()).parent / source

    def source_path(self):
        return self.resolve_source(self.file.GetPath())

    def source_changed(self, event):
        self.source_revision += 1
        self.source_cache.pop(str(self.source_path()), None)
        self.loaded_source = None
        self.update_preview()
        self.refresh_targets()

    def side_changed(self, event):
        side = self.side.GetStringSelection()
        if side == self.settings["side"]:
            return
        self.settings["side"] = side
        self.stash_current()
        self.load_layers()
        self.update_preview()
        self.refresh_targets()

    def stash_current(self):
        labels = {"width": "Artwork width", "angle": "Rotation", "x": "Centre X", "y": "Centre Y",
                  "minimum_width": "Minimum feature width", "edge_gap": "Edge clearance"}
        for key, control in self.controls.items():
            try:
                value = float(control.GetValue())
            except (ValueError, TypeError):
                raise ValueError(f"{labels.get(key, key)}: enter a number.") from None
            if not math.isfinite(value):
                raise ValueError(f"{labels.get(key, key)}: enter a finite number.")
            self.settings[key] = value
        if not 0 < self.settings["width"] <= 2000 or self.settings["minimum_width"] < 0 or self.settings["edge_gap"] < 0:
            raise ValueError("Width must be 0–2000 mm; clearances must be non-negative.")
        self.settings.update(source=self.file.GetPath(), side=self.side.GetStringSelection(),
                             mirror=self.mirror.GetValue(), clip=self.clip.GetValue(),
                             minimum_width_enabled=self.minimum_enabled.GetValue())
        self.entries[self.active_target_index]["settings"] = placement_settings(self.settings)

    def refresh_targets(self):
        self.targets.Set([f"{Path(entry['settings']['source']).name or 'Choose an SVG…'} · {entry['settings']['side']}"
                          for entry in self.entries])
        self.targets.SetSelection(self.active_target_index)

    def show_entry(self, index):
        old_side = self.settings["side"]
        self.active_target_index = index
        entry = self.entries[index]
        self.previous = entry["previous"]
        self.settings.update(entry["settings"])
        self.file.SetPath(self.settings["source"])
        for key in self.controls:
            self.set_numeric(key, self.settings[key])
        self.side.SetStringSelection(self.settings["side"])
        self.mirror.SetValue(self.settings["mirror"])
        self.loaded_source = None
        if old_side != self.settings["side"]:
            self.load_layers()
        self.refresh_targets()
        self.update_preview()

    def select_target(self, event):
        index = self.targets.GetSelection()
        if index == self.active_target_index or index < 0:
            return
        try:
            self.stash_current()
        except ValueError as error:
            self.targets.SetSelection(self.active_target_index)
            self.status.SetLabel(str(error))
            return
        self.show_entry(index)

    def add_artwork(self, event):
        try:
            self.stash_current()
        except ValueError as error:
            self.status.SetLabel(str(error))
            return
        if not self.entries[self.active_target_index]["settings"]["source"]:
            return  # Already have a blank row ready for the file picker.
        settings = placement_settings(self.settings)
        settings["source"] = ""
        self.entries.append(dict(key=str(uuid4()), settings=settings, previous=None))
        self.show_entry(len(self.entries)-1)

    def remove_artwork(self, event):
        self.entries.pop(self.active_target_index)
        if not self.entries:
            self.entries.append(dict(key=str(uuid4()), settings=placement_settings({}), previous=None))
        self.stack_geometries = {}
        self.show_entry(min(self.active_target_index, len(self.entries)-1))

    def update_preview(self):
        self.timer.Stop()
        self.apply_button.Enable(False)
        try:
            self.stash_current()
            s = self.settings
            self.load_inversion()
            wanted = {str(self.resolve_source(entry["settings"]["source"])) for entry in self.entries
                      if entry["settings"]["source"]}
            self.source_cache = {key: value for key, value in self.source_cache.items() if key in wanted}
            self.preview_valid = bool(self.file.GetPath())
            if self.preview_valid:
                identity = str(self.source_path())
                if identity != self.loaded_source:
                    if identity not in self.source_cache:
                        self.source_cache[identity] = load_svg(self.source_path(), 2000.)
                    self.original = self.source_cache[identity]
                    self.loaded_source = identity
            else:
                self.original = None
                self.loaded_source = None
            key = (s["clip"], s["edge_gap"], self.rules_revision)
            if key != self.clearance_key:
                _, boundary = clearance_geometry(GeometryCollection(), GeometryCollection(),
                                      self.outline if s["clip"] else None, 0., 0., s["edge_gap"])
                self.cutouts = (self.group_blockers, boundary)
                self.clearance_key = key
            entries_key = tuple((entry["key"], json.dumps(entry["settings"], sort_keys=True))
                                for entry in self.entries if entry["settings"]["side"] == s["side"])
            artwork_key = (entries_key, self.clearance_key, cleanup_width(s), self.source_revision,
                           inversion_layer(s, s["side"]))
            if artwork_key != self.artwork_key:
                self.invalidate_artwork()
                self.artwork_key = artwork_key
            if self.artwork_ready:
                self.result = self.stack_geometries.get(self.entries[self.active_target_index]["key"], GeometryCollection())
            if not self.geometry_dirty and not self.artwork_ready and self.artwork_request_key != self.artwork_key:
                self.cleanup_timer.StartOnce(180)
            self.artwork_status()
            self.apply_button.Enable(not self.geometry_dirty and not self.rules_error and not self.pending_apply and not self.configuration_error
                                     and ((cleanup_width(s) == 0 and not inversion_layer(s, s["side"])) or self.artwork_ready))
            self.canvas.Refresh()
            return True
        except Exception as error:
            self.invalidate_artwork()
            self.result = self.placed = None
            self.preview_valid = False
            self.status.SetLabel(str(error))
            self.status.Wrap(350)
            self.canvas.Refresh()
            return False

    def apply(self, event):
        if self.geometry_dirty or self.pending_apply or self.rules_error or self.configuration_error:
            return
        try:
            self.stash_current()
            entries = [dict(entry, settings=dict(entry["settings"])) for entry in self.entries
                       if entry["settings"]["source"]]
            for entry in entries:
                path = self.resolve_source(entry["settings"]["source"])
                try:
                    entry["settings"]["source"] = str(path.relative_to(Path(self.board.GetFileName()).parent))
                except ValueError:
                    entry["settings"]["source"] = str(path)
            self.worker.invalidate()
            self.timer.Stop()
            self.group_timer.Stop()
            self.cleanup_timer.Stop()
            self.poll_timer.Stop()
            self.pending_apply = True
            dialog = UpdateDialog(self, self.board, entries, self.shared_settings(), self.overlays, worker=self.worker)
            try:
                if dialog.ShowModal() == wx.ID_OK:
                    self.shutdown()
                    self.EndModal(wx.ID_OK)
            finally:
                dialog.Destroy()
                self.pending_apply = False
            if not self.closed:
                self.poll_timer.Start(80)
                self.invalidate_artwork()
                self.update_preview()
        except Exception as error:
            self.pending_apply = False
            if not self.closed:
                self.poll_timer.Start(80)
            wx.MessageBox(str(error), APP_NAME, wx.OK | wx.ICON_ERROR, self)
