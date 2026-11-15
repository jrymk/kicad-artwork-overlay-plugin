"""Small registration layer; load geometry dependencies only on invocation."""
from pathlib import Path
import traceback
import pcbnew
import wx
from .branding import APP_NAME, UPDATE_NAME


class SilkOverlayAction(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = APP_NAME
        self.category = "Graphics"
        self.description = "Compose PCB silkscreen artwork with clearance rules, inversion and feature cleanup"
        self.show_toolbar_button = True
        self.icon_file_name = str(Path(__file__).with_name("icon.png"))

    def Run(self):
        try:
            from .composer import Composer
            board = pcbnew.GetBoard()
            if board is None:
                raise ValueError("Open a PCB first.")
            dialog = Composer(None, board)
            try:
                dialog.ShowModal()
            finally:
                dialog.Destroy()
            pcbnew.Refresh()
        except Exception:
            wx.MessageBox(traceback.format_exc(), APP_NAME, wx.OK | wx.ICON_ERROR)


class SilkOverlayUpdateAction(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = UPDATE_NAME
        self.category = "Graphics"
        self.description = "Update saved SVG artwork and inversion layers with current PCB clearances"
        self.show_toolbar_button = True
        self.icon_file_name = str(Path(__file__).with_name("icon-update.png"))

    def Run(self):
        try:
            from .refresh import update_existing
            board = pcbnew.GetBoard()
            if board is None:
                raise ValueError("Open a PCB first.")
            update_existing(None, board)
            pcbnew.Refresh()
        except Exception:
            wx.MessageBox(traceback.format_exc(), UPDATE_NAME, wx.OK | wx.ICON_ERROR)
