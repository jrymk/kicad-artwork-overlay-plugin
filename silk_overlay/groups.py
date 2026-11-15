"""Colour rule groups; no KiCad or GUI imports."""
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import cached_property
from fnmatch import fnmatchcase
import json
import math
from concurrent.futures import CancelledError

from shapely.geometry import Polygon, GeometryCollection, Point, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .geometry import polygons, outline_band, TOLERANCE

PALETTE = {
    "red": ("Red", "#ef7373"), "blue": ("Blue", "#759fff"),
    "green": ("Green", "#72d79b"), "yellow": ("Yellow", "#e7cc66"),
    "cyan": ("Cyan", "#68d5dc"), "magenta": ("Magenta", "#da87db"),
    "orange": ("Orange", "#f3a34c"), "purple": ("Purple", "#a793ec"),
}
LAYERS = {"exposed": "Cu ∩ Mask (exposed copper)", "silk": "Silkscreen", "courtyard": "Courtyard",
          "mask": "Mask", "custom": "Custom layer 1", "custom2": "Custom layer 2"}
CUSTOM_SLOTS = ("custom", "custom2")
SIDES = ("Both", "Front", "Back")
TABLE_COLORS = {"red": "#cf3838", "blue": "#3268d8", "green": "#21864a",
                "yellow": "#a37a00", "cyan": "#008899", "magenta": "#b137b9",
                "orange": "#b96300", "purple": "#744ad0"}


def color_label(rules, key):
    return rules[key].get("label", "").strip() or PALETTE[key][0]


def applies_to_side(layer_rule, side):
    return side is None or layer_rule.get("side", "Both") in ("Both", side)


@dataclass
class BoardObject:
    key: str
    label: str
    kind: str
    ink: object
    region: object
    parent: str = ""
    region_source: str = "enclosed contours"
    reference: str = ""
    footprint: str = ""
    value: str = ""
    text: str = ""
    openings: object = None
    layers: object = None

    @cached_property
    def hit_geometry(self):
        if self.kind == "footprint":
            return unary_union([self.region, self.ink, self.openings if self.openings is not None else GeometryCollection(),
                                *(self.layers or {}).values()])
        return fill_enclosed(self.ink)


def default_rules(silk_gap=.2, mask_gap=.2):
    return {key: {"label": "", "allow_inside": key in ("cyan", "magenta"), "layers": {
        layer: {"enabled": layer not in ("courtyard", *CUSTOM_SLOTS) or (layer == "courtyard" and key == "red"),
                "clearance": silk_gap if layer == "silk" else .3 if key in ("red", "yellow") else mask_gap,
                **({"name": "", "side": "Both"} if layer in CUSTOM_SLOTS else {})} for layer in LAYERS}} for key in PALETTE}


def rules_for_settings(settings):
    """Migrate earlier single-clearance/pad-mode settings to explicit layers."""
    rules = default_rules(settings.get("silk_gap", .2), settings.get("mask_gap", .2))
    if "colour_rules" not in settings and settings.get("version") == 1:
        rules["red"]["allow_inside"] = True
        for layer in ("exposed", "courtyard"):
            rules["red"]["layers"][layer]["enabled"] = False
        rules["red"]["layers"]["mask"]["clearance"] = settings.get("mask_gap", .2)
        rules["green"]["allow_inside"] = True
    for key, value in settings.get("colour_rules", {}).items():
        if key in rules:
            if "layers" in value:
                rules[key].update({name: deepcopy(item) for name, item in value.items() if name != "layers"})
                for layer, layer_rule in value["layers"].items():
                    if layer in rules[key]["layers"]:
                        rules[key]["layers"][layer].update(deepcopy(layer_rule))
            else:
                silk_gap = value.get("silk_clearance", value.get("clearance", settings.get("silk_gap", .2)))
                mask_gap = value.get("mask_clearance", max(float(value.get("clearance", 0)), settings.get("mask_gap", .2)))
                mode = value.get("footprint_mode", "pads")
                rules[key]["allow_inside"] = value.get("allow_inside", False)
                for layer, layer_rule in rules[key]["layers"].items():
                    layer_rule["clearance"] = silk_gap if layer == "silk" else mask_gap
                    if layer == "exposed":
                        layer_rule["enabled"] = mode != "mask_only"
                    if layer == "courtyard":
                        layer_rule["enabled"] = mode == "courtyard"
    return deepcopy(rules)


def colour_of(item, assignments):
    key = assignments.get(item.parent if item.parent else item.key)
    return key if key in PALETTE else None


def merge_footprint_layers(items):
    """A footprint owns all its silk/mask graphics; children are not rule objects."""
    owners = {item.key: item for item in items if item.kind == "footprint"}
    layers = {key: {name: [geometry] for name, geometry in (item.layers or {}).items()}
              for key, item in owners.items()}
    for item in items:
        if item.parent in owners:
            for name, geometry in (item.layers or {}).items():
                layers[item.parent].setdefault(name, []).append(geometry)
    return [replace(item, layers={name: unary_union(parts) for name, parts in layers[item.key].items()})
            if item.kind == "footprint" else item for item in items if item.parent not in owners]


SCOPES = ("footprint", "silk_text", "silk_graphic", "silk", "opening", "graphic", "all")
FIELDS = ("", "footprint", "reference", "value", "text", "uuid")


def default_matching_rules():
    return []


def matches(item, rule):
    if item.parent:
        return False  # Footprint-owned shapes only participate through their owner.
    scope = rule["scope"]
    if scope != "all" and scope != item.kind and not (scope == "silk" and item.kind.startswith("silk")) and not (scope == "graphic" and item.kind != "footprint"):
        return False
    if not rule["field"]:
        return True
    if rule["field"] == "uuid" and "ids" in rule:
        return item.key in rule["ids"]
    if rule["field"] == "text" and not item.text:
        return False
    value = item.key if rule["field"] == "uuid" else getattr(item, rule["field"])
    return fnmatchcase(value.casefold(), rule["pattern"].casefold())


def resolve_colours(items, assignments, matching_rules):
    """Last matching rule wins; each footprint and all its layers share one colour."""
    direct = {}
    for item in items:
        if item.parent:
            continue
        if assignments.get(item.key) in PALETTE:
            direct[item.key] = assignments[item.key]
        else:
            for rule in reversed(matching_rules):
                if matches(item, rule):
                    direct[item.key] = rule["colour"]
                    break
    return {item.key: direct.get(item.parent if item.parent else item.key) for item in items}


def validate_rules(rules, matching_rules):
    for key in PALETTE:
        rule = rules[key]
        if not isinstance(rule.get("label", ""), str):
            raise ValueError("Color labels must be text.")
        for layer in LAYERS:
            layer_rule = rule["layers"][layer]
            if not isinstance(layer_rule["enabled"], bool):
                raise ValueError("Layer enabled must be true or false.")
            # Disabled sources never use their clearance. Keep their draft value
            # so disabling a partially edited field does not prevent Apply.
            if layer_rule["enabled"]:
                label = f"{color_label(rules, key)} ({PALETTE[key][0]})" if rule.get("label", "").strip() else PALETTE[key][0]
                field = f"{label} / {LAYERS[layer]} clearance"
                try:
                    gap = float(layer_rule["clearance"])
                except (ValueError, TypeError):
                    raise ValueError(f"{field}: enter a number in mm, or disable this layer.") from None
                if not math.isfinite(gap) or gap < 0:
                    raise ValueError(f"{field} must be finite and non-negative.")
            if layer in CUSTOM_SLOTS and layer_rule.get("side", "Both") not in SIDES:
                raise ValueError("Custom layer side must be Front, Back or Both.")
            if layer in CUSTOM_SLOTS and layer_rule["enabled"] and not layer_rule.get("name"):
                raise ValueError(f"Choose a custom board layer for {PALETTE[key][0]}.")
        if not isinstance(rule["allow_inside"], bool):
            raise ValueError("Invalid colour group settings.")
    for rule in matching_rules:
        ids = rule.get("ids")
        selection = rule["field"] == "uuid" and isinstance(ids, list) and bool(ids) and all(isinstance(key, str) and key for key in ids)
        if (rule["scope"] not in SCOPES or rule["field"] not in FIELDS or
                rule["colour"] not in PALETTE or not isinstance(rule["pattern"], str) or
                (rule["field"] and not rule["pattern"] and not selection) or (ids is not None and not selection)):
            raise ValueError("Invalid matching rule.")


def validate_profile_space(data):
    from .inversion import settings_values
    result = {}
    for key in ("minimum_width", "edge_gap"):
        if key in data:
            value = float(data[key])
            if not math.isfinite(value) or value < 0:
                raise ValueError("Profile clearances and minimum width must be finite and non-negative.")
            result[key] = value
    for key in ("clip", "minimum_width_enabled"):
        if key in data:
            if not isinstance(data[key], bool):
                raise ValueError("Profile enable settings must be true or false.")
            result[key] = data[key]
    result.update({key: value for key, value in settings_values(data).items() if key in data})
    return result


def profile_json(rules, matching_rules, space=None):
    validate_rules(rules, matching_rules)
    return json.dumps({"format": "silk-overlay-profile", "version": 1, **(validate_profile_space(space) if space else {}),
                       "colour_rules": rules, "matching_rules": matching_rules}, indent=2) + "\n"


def parse_profile(text):
    data = json.loads(text)
    if data.get("format") != "silk-overlay-profile" or data.get("version") != 1:
        raise ValueError("Not a supported Silk Overlay profile.")
    rules = rules_for_settings(data)
    matching = data["matching_rules"]
    validate_rules(rules, matching)
    validate_profile_space(data)
    return rules, matching


def fill_enclosed(geometry):
    """Fill closed interiors, retaining exterior contours, never a text bbox."""
    return unary_union([Polygon(p.exterior) for p in polygons(geometry)])


def custom_layers(rules, side=None):
    return {slot["name"] for rule in rules.values() for key in CUSTOM_SLOTS
            for slot in [rule["layers"].get(key, {})]
            if slot.get("enabled") and slot.get("name") and applies_to_side(slot, side)}


def distribute_exposed(items, exposed):
    """Intersect each object's mask with actual board copper, using a spatial index."""
    parts = list(polygons(exposed))
    tree = STRtree(parts)
    represented = []
    for item in items:
        mask = item.layers.get("mask") if item.layers is not None else None
        if mask is None or mask.is_empty:
            continue
        near = [parts[int(i)] for i in tree.query(mask)]
        geometry = mask.intersection(unary_union(near))
        item.layers["exposed"] = geometry
        represented.append(geometry)
    return exposed.difference(unary_union(represented))


def layer_geometry(item, layer, layer_rule):
    key = "custom:" + layer_rule.get("name", "") if layer in CUSTOM_SLOTS else layer
    if item.layers is not None:
        return item.layers.get(key, GeometryCollection())
    # Offline synthetic objects and old callers without the native layer map.
    if item.kind == "footprint":
        if layer == "exposed":
            return item.ink if item.openings is None else item.ink.intersection(item.openings)
        if layer == "mask":
            return item.openings if item.openings is not None else GeometryCollection()
        if layer == "courtyard":
            return outline_band(item.region, .001)
    elif (layer == "silk" and item.kind.startswith("silk")) or (layer == "mask" and item.kind == "opening"):
        return item.ink
    return GeometryCollection()


def build_group_geometry(items, assignments, rules, cache=None, cancelled=None, side="Front"):
    """Cache independent color groups. Join silk and openings before filling voids.

    Footprint-owned geometry is filled per footprint, so unrelated components
    cannot form a giant board-wide closed loop. Standalone silk borders may join.
    """
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise CancelledError()

    validate_rules(rules, [])
    members = {key: [] for key in PALETTE}
    for item in items:
        key = colour_of(item, assignments)
        if key is not None:
            members[key].append(item)
    cache = {} if cache is None else cache
    rendered, outlines, blockers, closed_owners = {}, {}, [], {}
    for key, objects in members.items():
        check_cancelled()
        rule = rules[key]
        signature = (tuple(id(item) for item in objects), json.dumps({k: v for k, v in rule.items() if k != "label"}, sort_keys=True), side)
        saved = cache.get(key)
        if saved is not None and saved[0] == signature:
            _, _, protected, ink, body, owned = saved
        else:
            owners = {}
            for item in objects:
                check_cancelled()
                owner = item.parent or (item.key if item.kind in ("footprint", "opening") else "standalone-silk")
                pieces = owners.setdefault(owner, [])
                for layer, layer_rule in rule["layers"].items():
                    if layer_rule["enabled"] and (layer not in CUSTOM_SLOTS or applies_to_side(layer_rule, side)):
                        geometry = layer_geometry(item, layer, layer_rule)
                        if not geometry.is_empty:
                            pieces.append(geometry.buffer(float(layer_rule["clearance"]) + TOLERANCE, quad_segs=32))
            protected_parts, owned = [], {}
            for owner, pieces in owners.items():
                check_cancelled()
                expanded = unary_union(pieces)
                protected_parts.append(expanded if rule["allow_inside"] else fill_enclosed(expanded))
                if not rule["allow_inside"]:
                    owned[owner] = protected_parts[-1]
            protected = unary_union(protected_parts)
            ink, body = protected, GeometryCollection()
            # Keep objects alive with their id signature; retain only one result per colour.
            cache[key] = (signature, objects, protected, ink, body, owned)
        rendered[key], outlines[key] = ink, body
        blockers.append(protected)
        for owner, geometry in owned.items():
            closed_owners.setdefault(owner, []).append((key, geometry))
    # Different colours can still close one component's interior, provided all
    # contributing geometry disallows inside. No merging across footprint owners.
    additions = {key: [] for key in PALETTE}
    for owned_regions in closed_owners.values():
        check_cancelled()
        if len(owned_regions) > 1:
            filled = fill_enclosed(unary_union([g for _, g in owned_regions]))
            blockers.append(filled)
            key = owned_regions[0][0]
            additions[key].append(filled)
    # One union per colour, rather than repeatedly merging the entire board for
    # every footprint that has differently coloured silk. The old loop was quadratic.
    for key, extra in additions.items():
        if extra:
            check_cancelled()
            rendered[key] = unary_union([rendered[key], *extra])
    check_cancelled()
    return unary_union(blockers), rendered, outlines


def hit_test(items, x, y, tolerance, kind=None):
    point = Point(x, y)
    candidates = [item for item in items if (kind is None or item.kind == kind)
                  and item.hit_geometry.distance(point) <= tolerance]
    return min(candidates, key=lambda item: (not item.kind.startswith("silk"),
                                            item.hit_geometry.area), default=None)


class ObjectIndex:
    """Spatial search, built only when the object/search filter changes."""
    def __init__(self, items):
        self.items = list(items)
        self.tree = STRtree([item.hit_geometry for item in self.items])

    def click(self, x, y, tolerance):
        indices = self.tree.query(box(x-tolerance, y-tolerance, x+tolerance, y+tolerance))
        return hit_test([self.items[int(i)] for i in indices], x, y, tolerance)

    def rectangle(self, area):
        return {self.items[int(i)].key for i in self.tree.query(area, predicate="intersects")}
