"""Shared inversion settings; no KiCad or UI dependencies."""
KEYS = {"Front": "inversion_front_layer", "Back": "inversion_back_layer"}


def layer_name(settings, side):
    return settings.get(KEYS[side], "")


def active_sides(settings):
    return {side for side in KEYS if layer_name(settings, side)}


def settings_values(settings):
    result = {key: settings.get(key, "") for key in KEYS.values()}
    if any(not isinstance(value, str) for value in result.values()):
        raise ValueError("Inversion layers must be board layer names or empty.")
    return result
