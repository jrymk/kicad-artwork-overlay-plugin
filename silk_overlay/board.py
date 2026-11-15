"""Read native KiCad geometry; write only the plugin-owned footprint."""
import json
from datetime import datetime
from pathlib import Path

import pcbnew as pcb
from shapely.geometry import Polygon

from .geometry import polygons, union_polygonal, outline_band, TOLERANCE
from .groups import BoardObject, fill_enclosed, merge_footprint_layers, distribute_exposed
from .project import PROPERTY, encode_shared, placement_settings
from .recovery import backup_path, prune_backups

FIELD = "SilkOverlay"


def _ring(chain):
    return [(pcb.ToMM(chain.CPoint(i).x), pcb.ToMM(chain.CPoint(i).y))
            for i in range(chain.PointCount())]


def from_poly(poly):
    # Native outlines can be self-touching/crossed, especially filled copper.
    # Repair individual polygons before GEOS sees them together in a union.
    return union_polygonal(
        Polygon(_ring(poly.COutline(i)),
                [_ring(poly.CHole(i, j)) for j in range(poly.HoleCount(i))
                 if poly.CHole(i, j).PointCount() >= 3])
        for i in range(poly.OutlineCount()) if poly.COutline(i).PointCount() >= 3)


def metadata(fp):
    if not fp.HasField(FIELD):
        return None
    try:
        value = json.loads(fp.GetField(FIELD).GetText())
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) and value.get("version") in (1, 2, 3) else None


def current_settings(fp):
    """Recover placement even after normal PCB move/rotate/flip commands."""
    info = dict(metadata(fp))
    side = "Back" if fp.GetLayer() == pcb.B_Cu else "Front"
    flipped = side != info["side"]
    # KiCad footprint flips mirror local Y. Our UI mirrors local X, which
    # requires an extra 180 degrees when converting that representation.
    angle = fp.GetOrientationDegrees() + (180 if flipped else 0)
    info.update(x=pcb.ToMM(fp.GetPosition().x), y=pcb.ToMM(fp.GetPosition().y),
                angle=(angle+180) % 360-180, mirror=info["mirror"] ^ flipped, side=side)
    return info


def _layer_polys(board, layer, exclude, clearance=0):
    error = pcb.FromMM(TOLERANCE)
    poly = pcb.SHAPE_POLY_SET()
    zones = list(board.Zones())
    for fp in board.GetFootprints():
        if metadata(fp) or (exclude is not None and fp.m_Uuid == exclude.m_Uuid):
            continue
        fp.TransformPadsToPolySet(poly, layer, clearance, error, pcb.ERROR_OUTSIDE)
        fp.TransformFPShapesToPolySet(poly, layer, clearance, error, pcb.ERROR_OUTSIDE)
        zones.extend(fp.Zones())
    for item in list(board.GetDrawings()) + list(board.GetTracks()):
        if item.IsOnLayer(layer):
            if isinstance(item, pcb.PCB_DIMENSION_BASE):
                item.TransformShapeToPolygon(poly, layer, clearance, error, pcb.ERROR_OUTSIDE)
                item.TransformTextToPolySet(poly, clearance, error, pcb.ERROR_OUTSIDE)
                continue
            if isinstance(item, pcb.PCB_TEXT):
                item.TransformTextToPolySet(poly, clearance, error, pcb.ERROR_OUTSIDE)
                continue
            if isinstance(item, pcb.PCB_TEXTBOX):
                if item.IsBorderEnabled():
                    pcb.PCB_SHAPE.TransformShapeToPolygon(item, poly, layer, clearance, error, pcb.ERROR_OUTSIDE)
                item.TransformTextToPolySet(poly, clearance, error, pcb.ERROR_OUTSIDE)
                continue
            if isinstance(item, pcb.PCB_TABLE):
                table = pcb.SHAPE_POLY_SET()
                item.TransformGraphicItemsToPolySet(table, error, pcb.ERROR_OUTSIDE, None)
                if clearance:
                    table.Inflate(clearance, pcb.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, error)
                poly.Append(table)
                continue
            # Via's polygon routine uses copper diameter on mask layers.
            extra = item.GetSolderMaskExpansion() if isinstance(item, pcb.PCB_VIA) and layer in (pcb.F_Mask, pcb.B_Mask) else 0
            item.TransformShapeToPolySet(poly, layer, clearance+extra, error, pcb.ERROR_OUTSIDE)
    for zone in zones:
        if zone.IsOnLayer(layer) and not zone.GetIsRuleArea():
            area = pcb.SHAPE_POLY_SET()
            zone.TransformSolidAreasShapesToPolygon(layer, area)
            if clearance:
                area.Inflate(clearance, pcb.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, error)
            poly.Append(area)
    return poly


def read_layers(board, side, exclude=None):
    """Native board geometry, including mask expansion, tenting and mask webs."""
    mask_layer, silk_layer = ((pcb.F_Mask, pcb.F_SilkS) if side == "Front" else (pcb.B_Mask, pcb.B_SilkS))
    mask = _layer_polys(board, mask_layer, exclude)
    inflate = board.GetDesignSettings().m_SolderMaskMinWidth // 2 - 1
    if inflate > 0:
        # Match KiCad's mask-web closing: inflated native item shapes, union,
        # chamfered deflation, then restore the exact original openings.
        merged = _layer_polys(board, mask_layer, exclude, inflate)
        merged.Simplify()
        merged.Deflate(inflate, pcb.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, pcb.FromMM(TOLERANCE))
        merged.BooleanAdd(mask)
        mask = merged
    silk = _layer_polys(board, silk_layer, exclude)
    outline = pcb.SHAPE_POLY_SET()
    valid = board.GetBoardPolygonOutlines(outline, False)
    return (from_poly(mask), from_poly(silk), from_poly(outline) if valid and outline.OutlineCount() else None)


def _silk_item(item, layer):
    """Preserve text strokes, including fields; never use text bounding boxes."""
    poly = pcb.SHAPE_POLY_SET()
    error = pcb.FromMM(TOLERANCE)
    if not item.IsOnLayer(layer):
        return from_poly(poly)
    if isinstance(item, pcb.PCB_DIMENSION_BASE):
        item.TransformShapeToPolygon(poly, layer, 0, error, pcb.ERROR_OUTSIDE)
        item.TransformTextToPolySet(poly, 0, error, pcb.ERROR_OUTSIDE)
    elif isinstance(item, pcb.PCB_TEXT):
        if item.IsVisible():
            item.TransformTextToPolySet(poly, 0, error, pcb.ERROR_OUTSIDE)
    elif isinstance(item, pcb.PCB_TEXTBOX):
        if item.IsBorderEnabled():
            pcb.PCB_SHAPE.TransformShapeToPolygon(item, poly, layer, 0, error, pcb.ERROR_OUTSIDE)
        item.TransformTextToPolySet(poly, 0, error, pcb.ERROR_OUTSIDE)
    elif isinstance(item, pcb.PCB_TABLE):
        item.TransformGraphicItemsToPolySet(poly, error, pcb.ERROR_OUTSIDE, None)
    else:
        item.TransformShapeToPolySet(poly, layer, 0, error, pcb.ERROR_OUTSIDE)
    return from_poly(poly)


def read_exposed(board, side, mask, exclude=None):
    copper = from_poly(_layer_polys(board, pcb.F_Cu if side == "Front" else pcb.B_Cu, exclude))
    return copper.intersection(mask)


def read_inversion(board, name):
    """Use actual filled shapes, text strokes and zones; exclude generated ink."""
    layer = board.GetLayerID(name)
    if layer < 0 or layer >= pcb.PCB_LAYER_ID_COUNT or not board.IsLayerEnabled(layer):
        raise ValueError(f"Inversion layer {name} is not enabled on this board.")
    return from_poly(_layer_polys(board, layer, None))


def read_objects(board, side, exclude=None, custom_names=()):
    """Selectable records with stable UUIDs. No colours are written to PCB items."""
    front = side == "Front"
    silk_layer = pcb.F_SilkS if front else pcb.B_SilkS
    copper_layer = pcb.F_Cu if front else pcb.B_Cu
    court_layer = pcb.F_CrtYd if front else pcb.B_CrtYd
    mask_layer = pcb.F_Mask if front else pcb.B_Mask
    records = []
    custom_ids = {name: board.GetLayerID(name) for name in custom_names}
    for name, layer in custom_ids.items():
        if layer < 0 or layer >= pcb.PCB_LAYER_ID_COUNT or not board.IsLayerEnabled(layer):
            raise ValueError(f"Custom layer {name} is not enabled on this board.")
    def add_silk(item, parent="", prefix=""):
        ink = _silk_item(item, silk_layer)
        if ink.is_empty:
            return
        key = item.m_Uuid.AsString()
        text = item.GetText() if hasattr(item, "GetText") else type(item).__name__.removeprefix("PCB_").lower()
        label = prefix + str(text).replace("\n", " ")[:70]
        kind = "silk_text" if isinstance(item, (pcb.PCB_TEXT, pcb.PCB_TEXTBOX, pcb.PCB_TABLE, pcb.PCB_DIMENSION_BASE)) else "silk_graphic"
        records.append(BoardObject(key, label + " · " + key[:8], kind, ink, fill_enclosed(ink),
                                   parent, text=str(text) if hasattr(item, "GetText") else "", layers={"silk": ink}))
    def add_zone(zone, parent="", prefix=""):
        if zone.IsOnLayer(silk_layer) and not zone.GetIsRuleArea():
            poly = pcb.SHAPE_POLY_SET()
            zone.TransformSolidAreasShapesToPolygon(silk_layer, poly)
            ink = from_poly(poly)
            if not ink.is_empty:
                records.append(BoardObject(zone.m_Uuid.AsString(), prefix+"filled zone", "silk_graphic",
                                           ink, fill_enclosed(ink), parent, layers={"silk": ink}))
    def add_opening(item, parent="", prefix=""):
        if not item.IsOnLayer(mask_layer):
            return
        if isinstance(item, pcb.PCB_VIA):
            poly = pcb.SHAPE_POLY_SET()
            item.TransformShapeToPolySet(poly, mask_layer, item.GetSolderMaskExpansion(),
                                         pcb.FromMM(TOLERANCE), pcb.ERROR_OUTSIDE)
            ink = from_poly(poly)
        else:
            ink = _silk_item(item, mask_layer)
        if not ink.is_empty:
            records.append(BoardObject(item.m_Uuid.AsString(), prefix+"mask opening", "opening",
                                       ink, fill_enclosed(ink), parent, layers={"mask": ink}))
    for fp in board.GetFootprints():
        if metadata(fp) or (exclude is not None and fp.m_Uuid == exclude.m_Uuid):
            continue
        parent = fp.m_Uuid.AsString()
        pads = pcb.SHAPE_POLY_SET()
        fp.TransformPadsToPolySet(pads, copper_layer, 0, pcb.FromMM(TOLERANCE), pcb.ERROR_OUTSIDE)
        pad_geometry = from_poly(pads)
        openings = pcb.SHAPE_POLY_SET()
        fp.TransformPadsToPolySet(openings, mask_layer, 0, pcb.FromMM(TOLERANCE), pcb.ERROR_OUTSIDE)
        mask_geometry = from_poly(openings)
        court = from_poly(fp.GetCourtyard(court_layer))
        source = "courtyard"
        if court.is_empty:
            # Conservative, explicit fallback; this is never used for text.
            court = from_poly(fp.GetBoundingHull(copper_layer))
            source = "pad/graphic hull (no courtyard)"
        court_lines = pcb.SHAPE_POLY_SET()
        fp.TransformFPShapesToPolySet(court_lines, court_layer, 0, pcb.FromMM(TOLERANCE), pcb.ERROR_OUTSIDE)
        court_ink = from_poly(court_lines)
        if court_ink.is_empty or source != "courtyard":
            court_ink = outline_band(court, .001)
            if court.is_empty:
                source = "no courtyard or pad/graphic hull"
        layers = {"mask": mask_geometry, "courtyard": court_ink}
        for name, layer in custom_ids.items():
            poly = pcb.SHAPE_POLY_SET()
            fp.TransformPadsToPolySet(poly, layer, 0, pcb.FromMM(TOLERANCE), pcb.ERROR_OUTSIDE)
            fp.TransformFPShapesToPolySet(poly, layer, 0, pcb.FromMM(TOLERANCE), pcb.ERROR_OUTSIDE)
            for zone in fp.Zones():
                if zone.IsOnLayer(layer) and not zone.GetIsRuleArea():
                    zone.TransformSolidAreasShapesToPolygon(layer, poly)
            layers["custom:"+name] = from_poly(poly)
        records.append(BoardObject(parent, fp.GetReference()+" · "+fp.GetValue(), "footprint",
                                   pad_geometry, court, region_source=source, reference=fp.GetReference(),
                                   footprint=fp.GetFPIDAsString(), value=fp.GetValue(), openings=mask_geometry, layers=layers))
        if not fp.GetPrivateLayers().Contains(silk_layer):
            for item in list(fp.GetFields()) + list(fp.GraphicalItems()):
                add_silk(item, parent, fp.GetReference()+" / ")
        for zone in fp.Zones():
            add_zone(zone, parent, fp.GetReference()+" / ")
        if not fp.GetPrivateLayers().Contains(mask_layer):
            for item in list(fp.GetFields()) + list(fp.GraphicalItems()):
                add_opening(item, parent, fp.GetReference()+" / ")
    for item in board.GetDrawings():
        add_silk(item)
        add_opening(item)
    for item in board.GetTracks():
        add_opening(item)
    for zone in board.Zones():
        add_zone(zone)
    by_key = {item.key: item for item in records}
    for name, layer in custom_ids.items():
        for item in list(board.GetDrawings()) + list(board.GetTracks()) + list(board.Zones()):
            if not item.IsOnLayer(layer):
                continue
            if isinstance(item, pcb.ZONE):
                if item.GetIsRuleArea():
                    continue
                poly = pcb.SHAPE_POLY_SET()
                item.TransformSolidAreasShapesToPolygon(layer, poly)
                ink = from_poly(poly)
            elif isinstance(item, pcb.PCB_VIA) and layer in (pcb.F_Mask, pcb.B_Mask):
                poly = pcb.SHAPE_POLY_SET()
                item.TransformShapeToPolySet(poly, layer, item.GetSolderMaskExpansion(),
                                             pcb.FromMM(TOLERANCE), pcb.ERROR_OUTSIDE)
                ink = from_poly(poly)
            else:
                ink = _silk_item(item, layer)
            if ink.is_empty:
                continue
            key = item.m_Uuid.AsString()
            if key in by_key:
                by_key[key].layers["custom:"+name] = ink
            else:
                record = BoardObject(key, name+" / "+key[:8], "graphic", ink, fill_enclosed(ink),
                                     text=str(item.GetText()) if hasattr(item, "GetText") else "",
                                     layers={"custom:"+name: ink})
                records.append(record)
                by_key[key] = record
    return [item for item in merge_footprint_layers(records) if not item.hit_geometry.is_empty]


def read_snapshot(board, side, custom_names=()):
    """GUI-thread read of one side. Every generated overlay is excluded."""
    mask, silk, outline = read_layers(board, side)
    items = read_objects(board, side, custom_names=custom_names)
    for layer, geometry, key, label, kind in (
            ("silk", silk, "unindexed-silk", "Other silk geometry", "silk_graphic"),
            ("mask", mask, "mask-webs", "Mask webs / other openings", "opening")):
        represented = union_polygonal(item.layers[layer] for item in items if layer in item.layers)
        residual = geometry.difference(represented)
        if not residual.is_empty:
            items.append(BoardObject(key, label, kind, residual, residual, layers={layer: residual}))
    residual = distribute_exposed(items, read_exposed(board, side, mask))
    if not residual.is_empty:
        items.append(BoardObject("exposed-residual", "Other exposed copper", "opening",
                                 residual, residual, layers={"exposed": residual}))
    return mask, silk, outline, items


def _poly(shape):
    result = pcb.SHAPE_POLY_SET()
    result.NewOutline()
    for x, y in list(shape.exterior.coords)[:-1]:
        result.Append(pcb.FromMM(x), pcb.FromMM(y))
    for ring in shape.interiors:
        hole = result.NewHole()
        for x, y in list(ring.coords)[:-1]:
            result.Append(pcb.FromMM(x), pcb.FromMM(y), 0, hole)
    # KiCad graphic polygons serialize outlines, not separate hole rings.
    result.Fracture()
    return result


def make_footprint(board, geometry, settings):
    """Prepare completely before touching the live board."""
    fp = pcb.FOOTPRINT(board)
    used = {item.GetReference() for item in board.GetFootprints()}
    number = 1
    while f"ART{number}" in used:
        number += 1
    fp.SetReference(f"ART{number}")
    fp.SetValue("Silk inversion" if settings.get("kind") == "inversion" else "Silk overlay")
    fp.SetAttributes(pcb.FP_BOARD_ONLY | pcb.FP_EXCLUDE_FROM_BOM | pcb.FP_EXCLUDE_FROM_POS_FILES)
    fp.SetLayer(pcb.F_Cu if settings["side"] == "Front" else pcb.B_Cu)
    fp.SetPosition(pcb.VECTOR2I(pcb.FromMM(settings["x"]), pcb.FromMM(settings["y"])))
    fp.SetOrientationDegrees(settings["angle"])
    layer = pcb.F_SilkS if settings["side"] == "Front" else pcb.B_SilkS
    for part in polygons(geometry):
        shape = pcb.PCB_SHAPE(fp)
        shape.SetShape(pcb.SHAPE_T_POLY)
        shape.SetLayer(layer)
        shape.SetFilled(True)
        shape.SetWidth(0)
        shape.SetPolyShape(_poly(part))
        fp.Add(shape)
    fp.SetField(FIELD, json.dumps({"version": 1, **settings}, separators=(",", ":")))
    for field in fp.GetFields():
        field.SetPosition(fp.GetPosition())
        field.SetVisible(False)
    return fp


def apply(board, geometry, settings, previous=None):
    if geometry.is_empty:
        raise ValueError("All artwork is cut away. Adjust its position, size or clearances.")
    fp = make_footprint(board, geometry, settings)
    # Snapshot the current in-memory board, including unsaved user edits.
    path = Path(board.GetFileName())
    if not board.GetFileName():
        raise ValueError("Save the board once before applying artwork.")
    backup = backup_path(path)
    try:
        if not pcb.SaveBoard(str(backup), board, True):
            raise RuntimeError("Could not create the board snapshot; artwork was not changed.")
    finally:
        # SaveBoard may change the board filename; always restore it.
        board.SetFileName(str(path))
    if previous is not None:
        fp.SetUuidDirect(previous.m_Uuid)
        fp.SetReference(previous.GetReference())
        fp.SetLocked(previous.IsLocked())
    if previous is not None:
        board.Remove(previous)
    try:
        board.Add(fp)
    except Exception:
        if previous is not None:
            board.Add(previous)
        raise
    prune_backups(backup)
    return fp, backup


def apply_stack(board, entries, shared, previous):
    """Prepare the entire stack, take one backup, then replace with rollback.

    entries = [(geometry, placement settings, old footprint or None)]. Empty
    geometry is allowed: retaining its source/placement lets it reappear after
    components move. Never leave obsolete ink behind just because a cut is empty.
    """
    if not board.GetFileName():
        raise ValueError("Save the board once before applying artwork.")
    old_properties = pcb.MAP_STRING_STRING()
    for key, value in board.GetProperties().items():
        old_properties[key] = value
    properties = pcb.MAP_STRING_STRING()
    for key, value in old_properties.items():
        properties[key] = value
    properties[PROPERTY] = encode_shared(shared)
    prepared, used = [], {fp.GetReference() for fp in board.GetFootprints()}
    for geometry, settings, old in entries:
        # Keep a synchronized recovery copy on every footprint. These objects
        # participate in KiCad's action-plugin undo; board properties alone do
        # not reliably follow the generated footprint across undo/reopen.
        info = {"version": 3, **placement_settings(settings), "shared": json.loads(properties[PROPERTY])}
        if settings.get("kind") == "inversion":
            info["kind"] = "inversion"
        fp = make_footprint(board, geometry, info)
        if old is not None:
            fp.SetUuidDirect(old.m_Uuid)
            fp.SetReference(old.GetReference())
            fp.SetLocked(old.IsLocked())
        else:
            number = 1
            while f"ART{number}" in used:
                number += 1
            fp.SetReference(f"ART{number}")
        used.add(fp.GetReference())
        prepared.append(fp)
    path = Path(board.GetFileName())
    backup = backup_path(path)
    try:
        if not pcb.SaveBoard(str(backup), board, True):
            raise RuntimeError("Could not create the board snapshot; artwork was not changed.")
    finally:
        board.SetFileName(str(path))
    removed, added = [], []
    try:
        for fp in previous:
            board.Remove(fp)
            removed.append(fp)
        for fp in prepared:
            board.Add(fp)
            added.append(fp)
        board.SetProperties(properties)
    except Exception:
        for fp in reversed(added):
            board.Remove(fp)
        for fp in removed:
            board.Add(fp)
        board.SetProperties(old_properties)
        raise
    prune_backups(backup)
    return prepared, backup
