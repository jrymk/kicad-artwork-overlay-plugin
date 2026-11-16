"""Colour tabs and canvas selection; no board-object table."""
import wx
from shapely.geometry import box
from .groups import PALETTE, LAYERS, CUSTOM_SLOTS, SIDES, ObjectIndex, COURTYARD_INSET_FIELD
from .color_ui import refresh_buttons


class GroupPanel(wx.Panel):
    def __init__(self, parent, composer):
        super().__init__(parent)
        self.composer = composer
        self.selected = set()
        self.active_colour = "red"
        self.index_key = None
        self.index = ObjectIndex([])
        form = wx.BoxSizer(wx.VERTICAL)
        form.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 8)
        form.Add(wx.StaticText(self, label="Color settings"), 0, wx.TOP | wx.BOTTOM, 5)
        tabs = wx.GridSizer(cols=4, vgap=self.FromDIP(3), hgap=self.FromDIP(3))
        self.tabs = {}
        for key, (name, colour) in PALETTE.items():
            button = wx.ToggleButton(self, label=name, style=wx.BU_EXACTFIT)
            button.SetMinSize(self.FromDIP((0, 28)))
            button.SetBackgroundColour(colour)
            button.SetForegroundColour("#18202b")
            button.Bind(wx.EVT_TOGGLEBUTTON, lambda event, key=key: self.select_colour(key))
            self.tabs[key] = button
            tabs.Add(button, 0, wx.EXPAND)
        form.Add(tabs, 0, wx.EXPAND | wx.BOTTOM, 7)
        label_row = wx.BoxSizer(wx.HORIZONTAL)
        label_row.Add(wx.StaticText(self, label="Label"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.label = wx.TextCtrl(self)
        self.label.SetHint("Color name when empty")
        self.label.Bind(wx.EVT_TEXT, self.label_changed)
        label_row.Add(self.label, 1, wx.EXPAND)
        form.Add(label_row, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.layer_controls = {}
        self.custom_controls = {}
        for field, label in LAYERS.items():
            row = wx.BoxSizer(wx.HORIZONTAL)
            enabled = wx.CheckBox(self, label=label)
            enabled.Bind(wx.EVT_CHECKBOX, self.rule_changed)
            row.Add(enabled, 1, wx.ALIGN_CENTER_VERTICAL)
            control = wx.TextCtrl(self, size=self.FromDIP((75, -1)))
            control.Bind(wx.EVT_TEXT, self.rule_changed)
            self.layer_controls[field] = (enabled, control)
            row.Add(control, 0, wx.LEFT, 5)
            row.Add(wx.StaticText(self, label="mm"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
            form.Add(row, 0, wx.EXPAND | wx.BOTTOM, 4)
            if field == "courtyard":
                control.SetToolTip("Outward clearance after the courtyard inset is applied.")
                row = wx.BoxSizer(wx.HORIZONTAL)
                self.courtyard_solid = wx.CheckBox(self, label="Solid courtyard")
                self.courtyard_solid.SetToolTip(
                    "Exclude the courtyard's enclosed area, even when Allow artwork to flow inside object is on. "
                    "Older profiles retain outline behavior until this is enabled.")
                self.courtyard_solid.Bind(wx.EVT_CHECKBOX, self.rule_changed)
                row.Add(self.courtyard_solid, 1, wx.ALIGN_CENTER_VERTICAL)
                row.Add(wx.StaticText(self, label="Inset"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
                self.courtyard_inset = wx.TextCtrl(self, size=self.FromDIP((75, -1)))
                self.courtyard_inset.SetToolTip(
                    "Shrink the solid courtyard inward by this distance before applying its clearance. "
                    f"Footprint property {COURTYARD_INSET_FIELD} overrides only this inset (mm); blank inherits it. "
                    "If the region shrinks away, the other exclusion layers still apply.")
                self.courtyard_inset.Bind(wx.EVT_TEXT, self.rule_changed)
                row.Add(self.courtyard_inset, 0, wx.LEFT, 5)
                row.Add(wx.StaticText(self, label="mm"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
                form.Add(row, 0, wx.EXPAND | wx.LEFT | wx.BOTTOM, 8)
            if field in CUSTOM_SLOTS:
                row = wx.BoxSizer(wx.HORIZONTAL)
                layer = wx.Choice(self, choices=["Choose a board layer…"] + composer.custom_choices)
                layer.SetMinSize((0, -1))
                layer.SetSelection(0)
                layer.Bind(wx.EVT_CHOICE, self.rule_changed)
                side = wx.Choice(self, choices=["Apply to artworks on layer…", *SIDES])
                side.SetMinSize(self.FromDIP((90, -1)))
                side.SetSelection(1)
                side.SetToolTip("Apply to artworks on layer…\nFront: F.SilkS\nBack: B.SilkS\nBoth: F.SilkS and B.SilkS")
                side.Bind(wx.EVT_CHOICE, lambda event, slot=field: self.side_changed(slot, event))
                self.custom_controls[field] = (layer, side)
                row.Add(layer, 1, wx.EXPAND | wx.RIGHT, 5)
                row.Add(side, 0, wx.EXPAND)
                form.Add(row, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.allow = wx.CheckBox(self, label="Allow artwork to flow inside object")
        self.allow.SetToolTip("Allow unblocked interior spaces. Enabled solid courtyard regions remain excluded.")
        self.allow.Bind(wx.EVT_CHECKBOX, self.rule_changed)
        form.Add(self.allow, 0, wx.BOTTOM, 5)
        self.description = wx.StaticText(self, label="")
        self.description.SetMinSize((0, -1))
        self.description_text = self.description.GetLabel()
        form.Add(self.description, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.SetSizer(form)
        self.SetMinSize((0, -1))
        self.Bind(wx.EVT_SIZE, self.resized)
        self.select_colour("red")

    def select_colour(self, key):
        self.active_colour = key
        self.load_rule()

    def load_rule(self):
        rule = self.composer.colour_rules[self.active_colour]
        self.label.ChangeValue(rule.get("label", ""))
        refresh_buttons(self.tabs, self.composer.colour_rules, self.active_colour)
        for layer, (enabled, control) in self.layer_controls.items():
            enabled.SetValue(rule["layers"][layer]["enabled"])
            control.ChangeValue(str(rule["layers"][layer]["clearance"]))
            control.Enable(enabled.GetValue())
        self.allow.SetValue(rule["allow_inside"])
        courtyard = rule["layers"]["courtyard"]
        self.courtyard_solid.SetValue(courtyard.get("solid", False))
        self.courtyard_solid.Enable(courtyard["enabled"])
        self.courtyard_inset.ChangeValue(str(courtyard.get("inset", 0)))
        self.courtyard_inset.Enable(courtyard["enabled"] and self.courtyard_solid.GetValue())
        for slot, (layer, side) in self.custom_controls.items():
            settings = rule["layers"][slot]
            name = settings.get("name", "")
            if name and layer.FindString(name) == wx.NOT_FOUND:
                layer.Append(name)
            layer.SetSelection(layer.FindString(name) if name else 0)
            side.SetSelection(SIDES.index(settings.get("side", "Both")) + 1)
            layer.Enable(settings["enabled"])
            side.Enable(settings["enabled"])

    def label_changed(self, event):
        self.composer.colour_rules[self.active_colour]["label"] = self.label.GetValue()
        refresh_buttons(self.tabs, self.composer.colour_rules, self.active_colour)
        self.composer.rule_panel.refresh_labels()

    def side_changed(self, slot, event):
        side = self.custom_controls[slot][1]
        if side.GetSelection() == 0:
            value = self.composer.colour_rules[self.active_colour]["layers"][slot].get("side", "Both")
            side.SetSelection(SIDES.index(value) + 1)
            return
        self.rule_changed(event)

    def refresh_objects(self):
        key = tuple(id(item) for item in self.composer.board_objects)
        if key != self.index_key:
            self.index_key = key
            self.index = ObjectIndex(self.composer.board_objects)
        self.selected.intersection_update(item.key for item in self.composer.board_objects)
        self.describe_selection()

    def describe_selection(self):
        items = [item for item in self.composer.board_objects if item.key in self.selected]
        text = items[0].label if len(items) == 1 else f"{len(items)} objects selected"
        if not items:
            text = ""
        elif len(items) == 1 and items[0].courtyard_inset.strip():
            text += f" · Courtyard inset override: {items[0].courtyard_inset}"
        self.description_text = text
        self.description.SetLabel(text)
        self.description.Wrap(max(self.FromDIP(180), self.GetClientSize().width))
        self.GetParent().Layout()
        self.GetParent().FitInside()

    def resized(self, event):
        width = self.GetClientSize().width
        if width > self.FromDIP(180):
            self.description.SetLabel(self.description_text)
            self.description.Wrap(width)
        event.Skip()

    def select_canvas(self, start, end, mode, tolerance, box_select=False):
        if box_select:
            area = box(min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1]))
            graphics = {item.key for item in self.composer.board_objects if item.kind != "footprint" and not item.parent}
            keys = self.index.rectangle(area) & graphics
        else:
            picked = self.index.click(*end, tolerance)
            keys = {picked.key} if picked else set()
        if mode == "add":
            self.selected |= keys
        elif mode == "remove":
            self.selected -= keys
        else:
            self.selected = keys
        self.describe_selection()
        items = [item for item in self.composer.board_objects if item.key in self.selected]
        self.composer.rule_panel.pick_objects(items)
        self.composer.canvas.Refresh()

    def rule_changed(self, event):
        layers = {}
        for layer, (enabled, control) in self.layer_controls.items():
            control.Enable(enabled.GetValue())
            layers[layer] = {"enabled": enabled.GetValue(), "clearance": control.GetValue()}
        courtyard = layers["courtyard"]
        courtyard.update(solid=self.courtyard_solid.GetValue(), inset=self.courtyard_inset.GetValue())
        self.courtyard_solid.Enable(courtyard["enabled"])
        self.courtyard_inset.Enable(courtyard["enabled"] and courtyard["solid"])
        for slot, (layer, side) in self.custom_controls.items():
            layers[slot]["name"] = layer.GetStringSelection() if layer.GetSelection() > 0 else ""
            layers[slot]["side"] = side.GetStringSelection()
            layer.Enable(layers[slot]["enabled"])
            side.Enable(layers[slot]["enabled"])
        self.composer.colour_rules[self.active_colour] = {
            "label": self.label.GetValue(), "layers": layers, "allow_inside": self.allow.GetValue()}
        self.composer.groups_changed(defer=True)
