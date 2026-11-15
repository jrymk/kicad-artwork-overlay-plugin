"""Consistent, explicit selection markers for the two color button rows."""
import wx
from .groups import color_label


def refresh_buttons(buttons, rules, selected):
    for key, button in buttons.items():
        active = key == selected
        label = color_label(rules, key)
        button.SetValue(active)
        button.SetLabel(("✓ " if active else "") + label)
        button.SetToolTip(label)
        font = button.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD if active else wx.FONTWEIGHT_NORMAL)
        button.SetFont(font)
