"""Shared board settings and independent SVG placements; no KiCad imports."""
from copy import deepcopy
import json
from .groups import rules_for_settings, validate_rules, validate_profile_space
from .inversion import settings_values as inversion_settings

PROPERTY = "SilkOverlayProject"
PLACEMENT_DEFAULTS = dict(source="", width=40., x=100., y=100., angle=0., side="Front", mirror=False)
SPACE_DEFAULTS = dict(edge_gap=.2, clip=True, minimum_width=0., minimum_width_enabled=False)
REQUIRED_SHARED = {"colour_rules", "matching_rules", "object_colours", "edge_gap", "clip", "minimum_width"}


class MissingSharedSettings(ValueError):
    pass


def placement_settings(settings):
    return {key: settings.get(key, value) for key, value in PLACEMENT_DEFAULTS.items()}


def shared_settings(settings):
    result = {key: settings.get(key, value) for key, value in SPACE_DEFAULTS.items()}
    result["minimum_width_enabled"] = settings.get("minimum_width_enabled", float(settings.get("minimum_width", 0.)) > 0)
    result.update(colour_rules=rules_for_settings(settings),
                  matching_rules=deepcopy(settings.get("matching_rules", [])),
                  object_colours=dict(settings.get("object_colours", {})))
    result.update(inversion_settings(settings))
    validate_rules(result["colour_rules"], result["matching_rules"])
    validate_profile_space(result)
    return result


def cleanup_width(settings):
    return float(settings.get("minimum_width", 0.)) if settings.get("minimum_width_enabled", True) else 0.


def recover_shared(properties, legacy):
    """Footprint-carried settings survive action undo/save/reopen.

    Every v3 footprint carries the SAME project settings, never a per-SVG rule
    override. Board properties are a fallback only for the old v2 format.
    """
    carried = []
    for label, info in legacy:
        if info.get("version") == 3:
            value = info.get("shared")
            if not isinstance(value, dict) or not REQUIRED_SHARED.issubset(value):
                raise MissingSharedSettings("Saved artwork is missing its shared rules. Load a saved profile in the composer; artwork has not been changed.")
            carried.append(shared_settings(value))
    if carried:
        if any(value != carried[0] for value in carried[1:]):
            raise MissingSharedSettings("Artwork contains conflicting shared settings. Load the intended profile in the composer before updating.")
        return carried[0]
    # Undo can restore old complete metadata while leaving a newer/stale board
    # property behind. That metadata is the configuration paired with the ink.
    for label, info in legacy:
        if "colour_rules" in info or "matching_rules" in info:
            return shared_settings(info)
    raw = properties.get(PROPERTY)
    if raw is not None:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("Unsupported SilkOverlayProject settings; no artwork changed.")
        if not REQUIRED_SHARED.issubset(value):
            raise MissingSharedSettings("The saved shared configuration is incomplete. Load a saved profile in the composer; artwork has not been changed.")
        return shared_settings(value)
    if legacy and any(info.get("version") in (2, 3) for _, info in legacy):
        raise MissingSharedSettings("Saved SVG placements were found, but their shared rules are missing. Load a saved profile in the composer; artwork has not been changed.")
    return shared_settings(legacy[0][1] if legacy else {})


def encode_shared(settings):
    return json.dumps({"version": 1, **shared_settings(settings)}, separators=(",", ":"))


def reset_settings():
    """User's explicit Reset preset; never silently applied to new projects."""
    result = shared_settings({"minimum_width": .15})
    for rule in result["colour_rules"].values():
        rule["allow_inside"] = False
        for layer in rule["layers"].values():
            layer["enabled"] = False
    result["colour_rules"]["blue"]["layers"]["silk"].update(enabled=True, clearance=.3)
    for name in ("exposed", "silk"):
        result["colour_rules"]["red"]["layers"][name].update(enabled=True, clearance=.15)
    result["matching_rules"] = [dict(scope="graphic", field="", pattern="", colour="blue"),
                                dict(scope="footprint", field="", pattern="", colour="red")]
    return result
