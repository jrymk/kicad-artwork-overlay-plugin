"""SVG fills/strokes and clearance operations, in PCB millimetres."""
import math
from concurrent.futures import CancelledError
import xml.etree.ElementTree as ET
from pathlib import Path

from shapely import affinity, make_valid
from shapely.geometry import GeometryCollection, LineString, Polygon
from shapely.ops import polygonize, unary_union
from svgelements import SVG, Path as SVGPath, Shape, Move, Close, Line

TOLERANCE = 0.005


def polygons(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygons(part)


def polygonal(geometry):
    return union_polygonal((geometry,))


def union_polygonal(geometries):
    """Repair each input before union; a failed union cannot be repaired afterwards.

    Keep all polygon pieces from make_valid, including both sides of a crossed
    outline. Collapsed lines/points carry no filled area and are not exported.
    """
    parts = []
    for geometry in geometries:
        valid = geometry if geometry.is_valid else make_valid(geometry)
        parts.extend(polygons(valid))
    return unary_union(parts)


def outline_band(geometry, width):
    """Stroke polygon contours, including holes; empty regions stay empty.

    An empty native poly set becomes a GeometryCollection, whose Shapely
    boundary is None. Extract polygon boundaries instead of calling it directly.
    """
    boundaries = [part.boundary for part in polygons(geometry) if not part.is_empty]
    return unary_union(boundaries).buffer(width) if boundaries else GeometryCollection()


def _flatten(segment, tolerance, depth=0, a=0., b=1.):
    start, end = segment.point(a), segment.point(b)
    chord = LineString([(start.x, start.y), (end.x, end.y)])
    mids = [segment.point(a + (b-a)*f) for f in (.25, .5, .75)]
    from shapely.geometry import Point
    error = max(chord.distance(Point(p.x, p.y)) for p in mids)
    if error <= tolerance:
        return [(end.x, end.y)]
    if depth >= 20:
        raise ValueError("SVG curve is too complex at this size.")
    mid = (a+b)/2
    return (_flatten(segment, tolerance, depth+1, a, mid)
            + _flatten(segment, tolerance, depth+1, mid, b))


def _subpaths(path, tolerance):
    points = []
    closed = False
    for segment in path:
        if isinstance(segment, Move):
            if len(points) > 1:
                yield points, closed
            points = [(segment.end.x, segment.end.y)]
            closed = False
        else:
            if not points and segment.start is not None:
                points = [(segment.start.x, segment.start.y)]
            if isinstance(segment, (Line, Close)):
                points.append((segment.end.x, segment.end.y))
            else:
                points.extend(_flatten(segment, tolerance))
            closed = isinstance(segment, Close)
    if len(points) > 1:
        yield points, closed


def _winding(x, y, ring):
    winding = 0
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        cross = (bx-ax)*(y-ay) - (x-ax)*(by-ay)
        if ay <= y < by and cross > 0:
            winding += 1
        elif by <= y < ay and cross < 0:
            winding -= 1
    return winding


def _fill(rings, rule):
    if not rings:
        return GeometryCollection()
    lines = [LineString(r + [r[0]]) for r in rings if len(r) >= 3]
    faces = polygonize(unary_union(lines))
    result = []
    for face in faces:
        p = face.representative_point()
        winding = sum(_winding(p.x, p.y, r) for r in rings)
        if (winding % 2 != 0) if rule == "evenodd" else (winding != 0):
            result.append(face)
    return unary_union(result)


def load_svg(filename, width_mm):
    """Convert visible vector paths to centered, monochrome geometry.

    Reject features which cannot faithfully become physical silk rather than
    silently dropping them. Colours all mean ink; white is not a cutout.
    """
    if not math.isfinite(width_mm) or width_mm <= 0:
        raise ValueError("Artwork width must be positive.")
    data = Path(filename).read_bytes()
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("SVG document types/entities are not supported.")
    root = ET.fromstring(data)
    rejected = {"text", "image", "clipPath", "mask", "filter", "pattern", "style",
                "linearGradient", "radialGradient", "foreignObject", "use"}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in rejected:
            raise ValueError(f"SVG contains {tag}. Convert text/strokes/effects to plain paths first.")
        attrs = dict(element.attrib)
        attrs.update({k.strip(): v.strip() for k, v in
                      (part.split(":", 1) for part in attrs.get("style", "").split(";") if ":" in part)})
        for key in ("clip-path", "mask", "filter", "stroke-dasharray", "vector-effect"):
            if attrs.get(key, "none").strip() not in ("none", ""):
                raise ValueError(f"SVG {key} must be converted to paths first.")
        for key in ("opacity", "fill-opacity", "stroke-opacity"):
            if key in attrs and float(attrs[key]) not in (0., 1.):
                raise ValueError("Partial SVG opacity cannot be represented in silkscreen.")
    document = SVG.parse(str(filename), reify=True, ppi=96)
    shapes = [e for e in document.elements() if isinstance(e, Shape)]
    boxes = [e.bbox() for e in shapes if e.bbox() is not None]
    if not boxes:
        raise ValueError("SVG has no supported vector shapes.")
    span = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    if span <= 0:
        raise ValueError("SVG has zero width.")
    tolerance = TOLERANCE * span / width_mm
    pieces = []
    for shape in shapes:
        values = shape.values
        if values.get("visibility") in ("hidden", "collapse") or float(values.get("opacity", 1)) == 0:
            continue
        paths = list(_subpaths(SVGPath(shape), tolerance))
        if shape.fill.value is not None and float(values.get("fill-opacity", 1)) != 0:
            pieces.append(_fill([p for p, _ in paths], values.get("fill-rule", "nonzero")))
        if shape.stroke.value is not None and shape.stroke_width > 0 and float(values.get("stroke-opacity", 1)) != 0:
            cap = {"round": 1, "butt": 2, "square": 3}.get(values.get("stroke-linecap", "butt"), 2)
            join = {"round": 1, "miter": 2, "bevel": 3}.get(values.get("stroke-linejoin", "miter"), 2)
            for points, closed in paths:
                if closed and points[-1] != points[0]:
                    points.append(points[0])
                pieces.append(LineString(points).buffer(float(shape.stroke_width)/2,
                              cap_style=cap, join_style=join, quad_segs=32,
                              mitre_limit=float(values.get("stroke-miterlimit", 4))))
    art = polygonal(unary_union(pieces))
    if art.is_empty:
        raise ValueError("SVG contains no visible ink.")
    x0, y0, x1, y1 = art.bounds
    art = affinity.translate(art, -(x0+x1)/2, -(y0+y1)/2)
    return affinity.scale(art, width_mm/(x1-x0), width_mm/(x1-x0), origin=(0, 0))


def place(art, x, y, angle, mirror=False):
    if mirror:
        art = affinity.scale(art, -1, 1, origin=(0, 0))
    # PCB y increases downwards; positive KiCad angle is counter-clockwise.
    art = affinity.rotate(art, -angle, origin=(0, 0))
    return affinity.translate(art, x, y)


def clearance_geometry(mask, silk, outline, mask_gap, silk_gap, edge_gap):
    if any(not math.isfinite(v) or v < 0 for v in (mask_gap, silk_gap, edge_gap)):
        raise ValueError("Clearances must be finite and non-negative.")
    # Native board polygons approximate arcs; retain a small conservative margin.
    blockers = unary_union([mask.buffer(mask_gap + TOLERANCE, quad_segs=32),
                            silk.buffer(silk_gap + TOLERANCE, quad_segs=32)])
    return blockers, outline.buffer(-edge_gap) if outline is not None else None


def subtract_clearances(art, blockers, boundary):
    result = art.difference(blockers)
    if boundary is not None:
        result = result.intersection(boundary)
    return polygonal(result)


def remove_slivers(geometry, minimum_width=0., cancelled=None):
    """Round morphological opening, in mm, constrained to the original ink.

    A disk of diameter minimum_width must fit to retain a feature. This removes
    thin islands/tips and rounds sharp corners; it is not a fabrication DRC.
    Intersection after dilation prevents numeric/curve approximation from
    growing ink back into any clearance or board cutout.
    """
    width = float(minimum_width)
    if not math.isfinite(width) or width < 0:
        raise ValueError("Minimum feature width must be finite and non-negative.")
    if width == 0 or geometry.is_empty:
        return geometry
    def check():
        if cancelled is not None and cancelled():
            raise CancelledError()
    check()
    core = geometry.buffer(-width / 2, quad_segs=32, join_style=1)
    check()
    if core.is_empty:
        return core
    restored = core.buffer(width / 2, quad_segs=32, join_style=1)
    check()
    return polygonal(restored.intersection(geometry))


def final_artwork(art, blockers, boundary, minimum_width=0., cancelled=None):
    """Shared preview/export pipeline: clearance cuts first, sliver removal last."""
    return remove_slivers(subtract_clearances(art, blockers, boundary), minimum_width, cancelled)


def cut_artwork(art, mask, silk, outline, mask_gap, silk_gap, edge_gap):
    return subtract_clearances(art, *clearance_geometry(mask, silk, outline, mask_gap, silk_gap, edge_gap))
