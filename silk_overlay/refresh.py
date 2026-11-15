"""Update every saved SVG in one cancellable operation, without the editor."""
from copy import deepcopy
from pathlib import Path
import wx
from . import board as native
from .groups import custom_layers, validate_rules
from .project import recover_shared, placement_settings, shared_settings, MissingSharedSettings
from .jobs import LatestJob, PreviewCalculator
from .stack import UpdateRequest
from .branding import APP_NAME, UPDATE_NAME
from .inversion import active_sides, layer_name


def discover(board, allow_missing=False):
    footprints = sorted((fp for fp in board.GetFootprints() if native.metadata(fp)),
                        key=lambda fp: (fp.GetReference(), fp.m_Uuid.AsString()))
    entries = [dict(key=fp.m_Uuid.AsString(), settings=placement_settings(native.current_settings(fp)), previous=fp)
               for fp in footprints if native.metadata(fp).get("kind") != "inversion"]
    try:
        shared = recover_shared(dict(board.GetProperties()),
                                [(fp.GetReference(), native.metadata(fp)) for fp in footprints])
    except MissingSharedSettings as error:
        if not allow_missing:
            raise
        shared = shared_settings({})
        shared["recovery_required"] = str(error)
    return entries, shared, footprints


class UpdateDialog(wx.Dialog):
    def __init__(self, parent, board, entries, shared, previous, worker=None):
        super().__init__(parent, title=UPDATE_NAME, size=(640, 170))
        self.board, self.entries, self.shared, self.previous = board, entries, deepcopy(shared), previous
        self.closed, self.started, self.error = False, False, None
        self.owns_worker = worker is None
        self.worker = worker if worker is not None else LatestJob(PreviewCalculator())
        form = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(self, label="Reading board geometry…")
        form.Add(self.status, 0, wx.ALL | wx.EXPAND, 16)
        self.gauge = wx.Gauge(self)
        form.Add(self.gauge, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 16)
        cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
        cancel.Bind(wx.EVT_BUTTON, self.cancel)
        form.Add(cancel, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizer(form)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.poll, self.timer)
        self.Bind(wx.EVT_CLOSE, self.cancel)
        self.timer.Start(80)
        self.CentreOnParent()

    def cancel(self, event):
        self.stop()
        self.EndModal(wx.ID_CANCEL)

    def stop(self):
        self.closed = True
        self.timer.Stop()
        if self.owns_worker:
            self.worker.close()
        else:
            self.worker.invalidate()

    def Destroy(self):
        self.stop()
        return super().Destroy()

    def poll(self, event):
        if self.closed:
            return
        try:
            self.gauge.Pulse()
            if not self.started:
                self.started = True
                validate_rules(self.shared["colour_rules"], self.shared["matching_rules"])
                if not self.board.GetFileName():
                    raise ValueError("Save the PCB once before updating artwork.")
                sides = {entry["settings"]["side"] for entry in self.entries} | active_sides(self.shared)
                # KiCad native objects must stay on the GUI thread.
                snapshots = {side: native.read_snapshot(self.board, side, custom_layers(self.shared["colour_rules"], side))
                             for side in sides}
                layers = {layer_name(self.shared, side) for side in active_sides(self.shared)}
                inversion_layers = {name: native.read_inversion(self.board, name) for name in layers}
                inversions = {side: inversion_layers[layer_name(self.shared, side)] for side in active_sides(self.shared)}
                self.worker.request(UpdateRequest(tuple((entry["key"], dict(entry["settings"])) for entry in self.entries),
                                     self.shared, snapshots, str(Path(self.board.GetFileName()).parent), inversions))
                self.status.SetLabel("Updating artwork, inversion and clearances…")
                return
            completed = self.worker.poll()
            if completed is None:
                return
            _, result, error = completed
            if error:
                raise error
            prepared = [(result.geometries[entry["key"]], entry["settings"], entry["previous"])
                        for entry in self.entries]
            old_inversions = {native.metadata(fp)["side"]: fp for fp in self.previous
                              if native.metadata(fp).get("kind") == "inversion"}
            for side, geometry in result.inversions.items():
                settings = dict(placement_settings({"side": side, "x": 0., "y": 0.}), kind="inversion")
                prepared.append((geometry, settings, old_inversions.get(side)))
            native.apply_stack(self.board, prepared, self.shared, self.previous)
            self.stop()
            self.EndModal(wx.ID_OK)
        except Exception as error:
            self.error = str(error)
            self.stop()
            wx.MessageBox(self.error, UPDATE_NAME, wx.OK | wx.ICON_ERROR, self)
            self.EndModal(wx.ID_CANCEL)


def update_existing(parent, board):
    entries, shared, previous = discover(board)
    if not entries and not active_sides(shared):
        wx.MessageBox(f"No saved silk overlay artwork on this PCB. Add SVGs with {APP_NAME} first.",
                      UPDATE_NAME, wx.OK | wx.ICON_INFORMATION, parent)
        return
    if not shared["matching_rules"] and not shared["object_colours"] and not active_sides(shared):
        raise MissingSharedSettings(f"No exclusion rules are saved. Open {APP_NAME} and load a profile or configure rules before updating. Existing artwork has not been changed.")
    dialog = UpdateDialog(parent, board, entries, shared, previous)
    try:
        return dialog.ShowModal()
    finally:
        dialog.Destroy()
