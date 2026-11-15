"""Ordered rules above the colour editor; canvas picks populate rule conditions."""
from pathlib import Path
import json
import wx
from .groups import PALETTE, TABLE_COLORS, color_label, profile_json, parse_profile, validate_profile_space
from .color_ui import refresh_buttons
from .project import reset_settings
from .inversion import KEYS as INVERSION_KEYS, settings_values as inversion_settings

TYPES = ("all", "footprint", "graphic")
OPTIONS = {
    "all": [("", "No conditions (root)"), ("uuid", "Selected objects")],
    "footprint": [("", "No conditions"), ("footprint", "Footprint name"),
                  ("reference", "Reference"), ("value", "Value"), ("uuid", "Selected footprints")],
    "graphic": [("", "All graphics"), ("text", "Text"), ("uuid", "Selected graphics")],
}


class RulePanel(wx.Panel):
    def __init__(self, parent, composer):
        super().__init__(parent)
        self.composer = composer
        self.active_colour = "red"
        self.current_scope = "footprint"
        self.remembered_field = {"all": "", "footprint": "footprint", "graphic": ""}
        self.picked = None
        self.exact_key = ""
        self.selected_ids = []
        self.legacy_scope = None
        self.legacy_condition = None
        self.refreshing = False
        form = wx.BoxSizer(wx.VERTICAL)
        profile_row = wx.WrapSizer(wx.HORIZONTAL)
        for label, handler in (("Save profile…", self.save_profile), ("Load profile…", self.load_profile),
                               ("Load basic defaults…", self.reset)):
            button = wx.Button(self, label=label, style=wx.BU_EXACTFIT)
            button.Bind(wx.EVT_BUTTON, handler)
            profile_row.Add(button, 0, wx.RIGHT | wx.BOTTOM, 4)
        form.Add(profile_row, 0, wx.EXPAND | wx.BOTTOM, 6)
        shared_grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=8)
        shared_grid.AddGrowableCol(1)
        for key, label in (("minimum_width", "Minimum feature width (mm)"), ("edge_gap", "Edge clearance (mm)")):
            enabled = wx.CheckBox(self, label=label)
            flag = "minimum_width_enabled" if key == "minimum_width" else "clip"
            enabled.SetValue(composer.settings[flag])
            setattr(composer, "minimum_enabled" if key == "minimum_width" else "clip", enabled)
            enabled.Bind(wx.EVT_CHECKBOX, self.space_toggled)
            shared_grid.Add(enabled, 0, wx.ALIGN_CENTER_VERTICAL)
            control = wx.TextCtrl(self, value=str(composer.settings[key]))
            control.Bind(wx.EVT_TEXT, composer.schedule_update)
            control.Enable(enabled.GetValue())
            composer.controls[key] = control
            if key == "minimum_width":
                enabled.SetToolTip("Remove narrow features after clearance cutouts. Uncheck to skip cleanup.")
            else:
                enabled.SetToolTip("Clip artwork to the board outline and cutouts with this clearance. Uncheck to skip clipping.")
            shared_grid.Add(control, 0, wx.EXPAND)
        form.Add(shared_grid, 0, wx.EXPAND | wx.BOTTOM, 6)
        inversion_help = ("Graphics on this layer become silkscreen after processing. "
                          "Wherever they overlap the SVG artwork, both are cut away. "
                          "Existing exclusion rules, edge clearance, and minimum-feature cleanup still apply.")
        inversion_label = wx.StaticText(self, label="Artwork Inversion Silkscreen Layer")
        inversion_label.SetToolTip(inversion_help)
        form.Add(inversion_label, 0, wx.TOP | wx.BOTTOM, 5)
        inversion_grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=8)
        inversion_grid.AddGrowableCol(1)
        self.inversion_controls = {}
        for side, key in INVERSION_KEYS.items():
            inversion_grid.Add(wx.StaticText(self, label=side), 0, wx.ALIGN_CENTER_VERTICAL)
            control = wx.Choice(self, choices=["None"] + composer.custom_choices)
            control.SetMinSize((0, -1))
            control.SetToolTip(inversion_help)
            control.Bind(wx.EVT_CHOICE, self.inversion_changed)
            self.inversion_controls[key] = control
            inversion_grid.Add(control, 1, wx.EXPAND)
        self.sync_inversion_controls()
        form.Add(inversion_grid, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.rows = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.rows.SetToolTip("Last matching rule wins. Unmatched objects are untouched.")
        self.rows.SetMinSize(self.FromDIP((0, 220)))
        for label, width in (("Object", 100), ("Condition", 195), ("Color", 80)):
            self.rows.InsertColumn(self.rows.GetColumnCount(), label, width=self.FromDIP(width))
        self.rows.Bind(wx.EVT_LIST_ITEM_SELECTED, self.select_rule)
        self.rows.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.deselect_rule)
        self.rows.Bind(wx.EVT_LEFT_DOWN, self.table_clicked)
        form.Add(self.rows, 1, wx.EXPAND | wx.BOTTOM, 5)
        self.scope = wx.Choice(self, choices=["Any object", "Footprint", "Graphic"])
        self.scope.SetMinSize((0, -1))
        self.scope.SetSelection(1)
        self.scope.Bind(wx.EVT_CHOICE, self.scope_changed)
        self.field = wx.Choice(self)
        self.field.SetMinSize((0, -1))
        self.field.Bind(wx.EVT_CHOICE, self.field_changed)
        form.Add(self.scope, 0, wx.EXPAND | wx.BOTTOM, 5)
        form.Add(self.field, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.pattern = wx.TextCtrl(self)
        self.pattern.SetHint("Pick an object or enter a wildcard, e.g. *QFN*")
        form.Add(self.pattern, 0, wx.EXPAND | wx.BOTTOM, 5)
        colours = wx.GridSizer(cols=4, vgap=self.FromDIP(3), hgap=self.FromDIP(3))
        self.colour_buttons = {}
        for key, (name, colour) in PALETTE.items():
            button = wx.ToggleButton(self, label=name, style=wx.BU_EXACTFIT)
            button.SetMinSize(self.FromDIP((0, 28)))
            button.SetBackgroundColour(colour)
            button.SetForegroundColour("#18202b")
            button.Bind(wx.EVT_TOGGLEBUTTON, lambda event, key=key: self.choose_colour(key))
            self.colour_buttons[key] = button
            colours.Add(button, 0, wx.EXPAND)
        form.Add(colours, 0, wx.EXPAND | wx.BOTTOM, 5)
        row = wx.GridSizer(cols=5, vgap=0, hgap=self.FromDIP(3))
        self.edit_buttons = {}
        for label, handler in (("Add", self.add), ("Update", self.update), ("Remove", self.remove),
                               ("↑", lambda e: self.move(-1)), ("↓", lambda e: self.move(1))):
            button = wx.Button(self, label=label, style=wx.BU_EXACTFIT)
            button.SetMinSize(self.FromDIP((52, -1)))
            button.Bind(wx.EVT_BUTTON, handler)
            self.edit_buttons[label] = button
            row.Add(button, 0, wx.EXPAND)
        form.Add(row, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.clear_manual = wx.Button(self, label="Clear manual overrides")
        self.clear_manual.SetToolTip("Remove older manual object color assignments so matching rules take effect.")
        self.clear_manual.Bind(wx.EVT_BUTTON, self.clear_overrides)
        form.Add(self.clear_manual, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.status = wx.StaticText(self, label="No exclusions until you add a rule.")
        self.status.SetMinSize((0, -1))
        self.status_text = self.status.GetLabel()
        form.Add(self.status, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.SetSizer(form)
        self.SetMinSize((0, -1))
        self.Bind(wx.EVT_SIZE, self.resized)
        self.configure_fields("footprint")
        self.choose_colour("red", reveal=False)
        self.refresh_rules()

    def sync_space_controls(self):
        c = self.composer
        c.minimum_enabled.SetValue(c.settings["minimum_width_enabled"])
        c.clip.SetValue(c.settings["clip"])
        c.controls["minimum_width"].Enable(c.minimum_enabled.GetValue())
        c.controls["edge_gap"].Enable(c.clip.GetValue())

    def space_toggled(self, event):
        c = self.composer
        c.controls["minimum_width"].Enable(c.minimum_enabled.GetValue())
        c.controls["edge_gap"].Enable(c.clip.GetValue())
        c.update_preview()

    def sync_inversion_controls(self):
        for key, control in self.inversion_controls.items():
            name = self.composer.settings.get(key, "")
            if name and control.FindString(name) == wx.NOT_FOUND:
                control.Append(name)
            control.SetSelection(control.FindString(name) if name else 0)

    def inversion_changed(self, event):
        for key, control in self.inversion_controls.items():
            self.composer.settings[key] = control.GetStringSelection() if control.GetSelection() > 0 else ""
        self.composer.inversion_changed()

    def set_status(self, text):
        text = self.composer.configuration_error or text
        self.status_text = text
        self.status.SetLabel(text)
        self.status.Wrap(max(self.FromDIP(180), self.GetClientSize().width))
        self.Layout()
        self.GetParent().Layout()
        self.GetParent().FitInside()

    def resized(self, event):
        width = self.GetClientSize().width
        if width > self.FromDIP(180):
            self.status.SetLabel(self.status_text)
            self.status.Wrap(width)
            available = self.rows.GetClientSize().width - self.FromDIP(8)
            color_width = max(self.FromDIP(80), min(self.FromDIP(160), available // 4))
            self.rows.SetColumnWidth(2, color_width)
            self.rows.SetColumnWidth(1, max(self.FromDIP(60), available - self.FromDIP(100) - color_width))
        event.Skip()

    def configure_fields(self, scope, field=None):
        self.current_scope = scope
        self.scope.SetSelection(TYPES.index(scope))
        choices = OPTIONS[scope]
        self.field.SetItems([label for _, label in choices])
        wanted = self.remembered_field[scope] if field is None else field
        index = next((i for i, (key, _) in enumerate(choices) if key == wanted), 0)
        self.field.SetSelection(index)
        self.remembered_field[scope] = choices[index][0]
        self.show_condition()

    def show_condition(self):
        field = self.remembered_field[self.current_scope]
        self.field.Show(self.current_scope != "all" or field != "")
        self.pattern.Show(field not in ("", "uuid"))
        self.Layout()
        self.GetParent().Layout()
        self.GetParent().FitInside()

    def scope_changed(self, event):
        self.legacy_scope = None
        self.legacy_condition = None
        scope = TYPES[self.scope.GetSelection()]
        if scope == "all":
            self.remembered_field["all"] = ""
        self.configure_fields(scope)

    def field_changed(self, event):
        self.legacy_condition = None
        self.remembered_field[self.current_scope] = OPTIONS[self.current_scope][self.field.GetSelection()][0]
        self.show_condition()
        if self.picked is not None:
            self.populate_pattern(self.picked)

    def populate_pattern(self, item):
        field = self.remembered_field[self.current_scope]
        self.exact_key = item.key
        if field and field != "uuid":
            self.pattern.ChangeValue(getattr(item, field, ""))

    def pick_object(self, item):
        self.pick_objects([item])

    def pick_objects(self, items):
        self.selected_ids = [item.key for item in items]
        self.picked = items[0] if len(items) == 1 else None
        self.legacy_scope = None
        self.legacy_condition = None
        self.exact_key = ""
        if not items:
            self.set_status("No objects selected.")
            return
        kinds = {"footprint" if item.kind == "footprint" else "graphic" for item in items}
        if len(kinds) == 1:
            self.configure_fields(kinds.pop())
        else:
            self.configure_fields("all", "uuid")
        field = self.remembered_field[self.current_scope]
        values = {getattr(item, field, "") for item in items} if field not in ("", "uuid") else set()
        if len(values) == 1:
            self.pattern.ChangeValue(values.pop())
        elif len(values) > 1:
            self.pattern.ChangeValue("")
        self.set_status(("Picked " + items[0].label if len(items) == 1 else f"{len(items)} objects selected") +
                        ". Edit the condition, then Add or Update.")

    def choose_colour(self, colour, reveal=True):
        self.active_colour = colour
        refresh_buttons(self.colour_buttons, self.composer.colour_rules, colour)
        if reveal and hasattr(self.composer, "group_panel"):
            self.composer.group_panel.select_colour(colour)

    def refresh_labels(self):
        refresh_buttons(self.colour_buttons, self.composer.colour_rules, self.active_colour)
        for index, rule in enumerate(self.composer.matching_rules):
            self.rows.SetItem(index, 2, color_label(self.composer.colour_rules, rule["colour"]))

    def update_selection_ui(self):
        if self.refreshing:
            return
        index = self.rows.GetFirstSelected()
        count = len(self.composer.matching_rules)
        for name in ("Update", "Remove"):
            self.edit_buttons[name].Enable(index >= 0)
        self.edit_buttons["↑"].Enable(index > 0)
        self.edit_buttons["↓"].Enable(0 <= index < count - 1)
        for row in range(count):
            self.rows.SetItemBackgroundColour(row, "#dce9f8" if row == index else "#ffffff")

    def deselect_rule(self, event):
        if not self.refreshing:
            wx.CallAfter(self.update_selection_ui)
        event.Skip()

    def table_clicked(self, event):
        index, flags = self.rows.HitTest(event.GetPosition())
        if index == wx.NOT_FOUND:
            selected = self.rows.GetFirstSelected()
            if selected >= 0:
                self.rows.SetItemState(selected, 0, wx.LIST_STATE_SELECTED)
            self.update_selection_ui()
        event.Skip()

    def refresh_rules(self, selected=-1):
        self.refreshing = True
        try:
            self.rows.DeleteAllItems()
            for rule in self.composer.matching_rules:
                kind = "Any object" if rule["scope"] == "all" else "Footprint" if rule["scope"] == "footprint" else "Graphic"
                condition = "No conditions" if not rule["field"] else rule["field"] + ": " + rule["pattern"]
                if rule["field"] == "uuid":
                    ids = rule.get("ids", [rule["pattern"]])
                    item = next((item for item in self.composer.board_objects if item.key in ids), None)
                    condition = f"{len(ids)} selected objects" if len(ids) > 1 else "Selected: " + item.label if item else "Selected object"
                index = self.rows.InsertItem(self.rows.GetItemCount(), kind)
                self.rows.SetItem(index, 1, condition)
                self.rows.SetItem(index, 2, color_label(self.composer.colour_rules, rule["colour"]))
                self.rows.SetItemTextColour(index, TABLE_COLORS[rule["colour"]])
            if 0 <= selected < self.rows.GetItemCount():
                self.rows.SetItemState(selected, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
        finally:
            self.refreshing = False
        self.update_selection_ui()
        self.clear_manual.Show(bool(self.composer.assignments))
        self.refresh_labels()
        if 0 <= selected < len(self.composer.matching_rules):
            self.load_selected_rule(selected)
        self.Layout()
        self.GetParent().Layout()
        self.GetParent().FitInside()

    def select_rule(self, event):
        if self.refreshing:
            return
        self.load_selected_rule(event.GetIndex())
        self.update_selection_ui()

    def load_selected_rule(self, index):
        rule = self.composer.matching_rules[index]
        scope = rule["scope"] if rule["scope"] in TYPES else "graphic"
        self.legacy_scope = rule["scope"] if rule["scope"] != scope else None
        self.legacy_condition = dict(rule) if rule["field"] not in [key for key, _ in OPTIONS[scope]] else None
        self.configure_fields(scope, rule["field"])
        self.pattern.ChangeValue(rule["pattern"])
        self.exact_key = rule["pattern"] if rule["field"] == "uuid" else ""
        self.selected_ids = list(rule.get("ids", []))
        self.choose_colour(rule["colour"])

    def editor_rule(self):
        if self.legacy_condition is not None:
            return {**self.legacy_condition, "colour": self.active_colour}
        field = self.remembered_field[self.current_scope]
        if field == "uuid" and self.selected_ids:
            return dict(scope=self.legacy_scope or self.current_scope, field="uuid", pattern="",
                        ids=list(self.selected_ids), colour=self.active_colour)
        pattern = self.exact_key if field == "uuid" else self.pattern.GetValue().strip() if field else ""
        if field and not pattern:
            self.set_status("Pick an object or enter a condition first.")
            return None
        return dict(scope=self.legacy_scope or self.current_scope, field=field, pattern=pattern, colour=self.active_colour)

    def changed(self, index=-1):
        self.refresh_rules(index)
        self.composer.groups_changed(defer=True)

    def add(self, event):
        rule = self.editor_rule()
        if rule:
            self.composer.matching_rules.append(rule)
            self.changed(len(self.composer.matching_rules)-1)

    def update(self, event):
        index, rule = self.rows.GetFirstSelected(), self.editor_rule()
        if index >= 0 and rule:
            self.composer.matching_rules[index] = rule
            self.changed(index)

    def remove(self, event):
        index = self.rows.GetFirstSelected()
        if index >= 0:
            self.composer.matching_rules.pop(index)
            self.changed(min(index, len(self.composer.matching_rules)-1))

    def move(self, direction):
        index = self.rows.GetFirstSelected()
        target = index + direction
        rules = self.composer.matching_rules
        if index >= 0 and 0 <= target < len(rules):
            rules[index], rules[target] = rules[target], rules[index]
            self.changed(target)

    def clear_overrides(self, event):
        self.composer.assignments.clear()
        self.changed()

    def save_profile(self, event):
        try:
            self.composer.stash_current()
            content = profile_json(self.composer.colour_rules, self.composer.matching_rules, self.composer.settings)
            with wx.FileDialog(self, "Save rule profile", defaultFile="silk-overlay-profile.json",
                               wildcard="JSON profile (*.json)|*.json", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return
                Path(dialog.GetPath()).write_text(content, encoding="utf-8")
            self.set_status("Profile saved.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.set_status(str(error))

    def load_profile(self, event):
        try:
            with wx.FileDialog(self, "Load rule profile", wildcard="JSON profile (*.json)|*.json",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return
                text = Path(dialog.GetPath()).read_text(encoding="utf-8-sig")
                rules, matching = parse_profile(text)
                space = validate_profile_space(json.loads(text))
                profile_name = Path(dialog.GetPath()).name
            if not self.confirm_replacement(
                    "Load profile?", "Load profile",
                    f"Load {profile_name}?\n\n"
                    "This replaces the current rules, color settings and inversion layers, "
                    "clears manual overrides, and loads any edge-clearance and cleanup settings in the profile. "
                    "SVG placements stay unchanged."):
                return
            self.composer.colour_rules, self.composer.matching_rules = rules, matching
            self.composer.configuration_error = None
            self.composer.assignments.clear()
            self.composer.settings.update(inversion_settings(space))
            self.sync_inversion_controls()
            for key in ("minimum_width", "edge_gap"):
                if key in space:
                    self.composer.set_numeric(key, space[key])
            if "clip" in space:
                self.composer.settings["clip"] = space["clip"]
            if "minimum_width_enabled" in space or "minimum_width" in space:
                self.composer.settings["minimum_width_enabled"] = space.get(
                    "minimum_width_enabled", space.get("minimum_width", 0) > 0)
            self.sync_space_controls()
            self.composer.group_panel.load_rule()
            self.changed()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.set_status(str(error))

    def confirm_replacement(self, title, action, message):
        message += "\n\nSave a profile first if you want to keep the current settings. The PCB changes only when you click Apply."
        with wx.MessageDialog(self, message, title, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING) as dialog:
            dialog.SetYesNoLabels(action, "Cancel")
            return dialog.ShowModal() == wx.ID_YES

    def reset(self, event):
        if not self.confirm_replacement(
                "Load basic defaults?", "Load basic defaults",
                "This replaces the current rules and color settings with the built-in basic preset, "
                "clears manual overrides, turns inversion off, and enables minimum feature width at 0.15 mm.\n\n"
                "These are the tool's basic defaults, not a saved personal profile. "
                "Edge clearance and SVG placements stay unchanged."):
            return
        settings = reset_settings()
        self.composer.colour_rules = settings["colour_rules"]
        self.composer.matching_rules = settings["matching_rules"]
        self.composer.configuration_error = None
        self.composer.assignments.clear()
        self.composer.set_numeric("minimum_width", .15)
        self.composer.settings["minimum_width_enabled"] = True
        self.composer.settings.update(inversion_settings(settings))
        self.sync_inversion_controls()
        self.sync_space_controls()
        self.composer.group_panel.load_rule()
        self.changed()
