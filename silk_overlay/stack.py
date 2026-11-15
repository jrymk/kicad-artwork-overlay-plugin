"""Pure geometry stack calculation shared by composer and quick update."""
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from pathlib import Path
import math
from shapely.geometry import GeometryCollection
from .geometry import load_svg, final_artwork, union_polygonal, clearance_geometry
from . import placement
from .project import cleanup_width
from .inversion import active_sides


@dataclass(frozen=True)
class StackRequest:
    key: tuple
    entries: tuple  # (stable key, independent placement settings)
    shared: dict
    spaces: dict  # side -> (blockers, board outline)
    board_folder: str
    reload: bool = False
    cached_sources: dict = None
    inversions: dict = field(default_factory=dict)  # side -> native layer ink


@dataclass(frozen=True)
class StackResult:
    key: tuple
    geometries: dict
    sources: dict
    inversions: dict = field(default_factory=dict)  # side -> generated extra ink


@dataclass(frozen=True)
class UpdateRequest:
    entries: tuple
    shared: dict
    snapshots: dict
    board_folder: str
    inversions: dict = field(default_factory=dict)


def calculate_update(request, cancelled):
    from .jobs import PreviewCalculator
    spaces = {}
    calculator = PreviewCalculator()
    for side, (_, _, outline, objects) in request.snapshots.items():
        result = calculator((objects, request.shared["object_colours"], request.shared["matching_rules"],
                             request.shared["colour_rules"], False, side), cancelled)
        spaces[side] = (result[1], outline)
    return StackCalculator()(StackRequest(("update",), request.entries, request.shared, spaces,
                                         request.board_folder, reload=True, inversions=request.inversions), cancelled)


class StackCalculator:
    def __init__(self):
        self.sources = {}

    def __call__(self, request, cancelled):
        def check():
            if cancelled():
                raise CancelledError()
        shared = request.shared
        gap, width = float(shared["edge_gap"]), cleanup_width(shared)
        if any(not math.isfinite(v) or v < 0 for v in (gap, width)):
            raise ValueError("Edge clearance and minimum width must be finite and non-negative.")
        # Refresh each source once per update, even if placed multiple times.
        sources, originals, placed, by_side = {}, {}, {}, {}
        for key, settings in request.entries:
            check()
            if settings["side"] not in ("Front", "Back"):
                raise ValueError("Unknown artwork side.")
            if (any(not math.isfinite(float(settings[name])) for name in ("width", "x", "y", "angle"))
                    or not 0 < float(settings["width"]) <= 2000):
                raise ValueError("Invalid artwork placement or width.")
            path = Path(settings["source"])
            if not path.is_absolute():
                path = Path(request.board_folder) / path
            identity = str(path)
            if identity not in sources:
                if not request.reload and request.cached_sources and identity in request.cached_sources:
                    self.sources[identity] = request.cached_sources[identity]
                if request.reload or identity not in self.sources:
                    self.sources[identity] = load_svg(path, 2000.)
                sources[identity] = self.sources[identity]
            originals[key] = sources[identity]
            placed[key] = placement.materialize(originals[key], settings)
            by_side.setdefault(settings["side"], []).append(key)
        for side in active_sides(shared):
            if side in request.spaces:
                by_side.setdefault(side, [])
        results, extra_ink = {}, {}
        for side, keys in by_side.items():
            check()
            blockers, outline = request.spaces[side]
            _, boundary = clearance_geometry(GeometryCollection(), GeometryCollection(),
                                             outline if shared["clip"] else None, 0., 0., gap)
            # Clean the assembled ink, so overlapping SVGs can form a wider feature.
            assembled = union_polygonal(placed[key] for key in keys)
            inverted = side in active_sides(shared)
            ink = assembled
            if inverted:
                if side not in request.inversions:
                    raise ValueError(f"{side} inversion layer geometry was not loaded.")
                ink = union_polygonal([assembled.symmetric_difference(request.inversions[side])])
            check()
            cleaned = final_artwork(ink, blockers, boundary, width, cancelled)
            for key in keys:
                check()
                results[key] = cleaned.intersection(placed[key])
            if inverted:
                # Added ink has its own owner; SVG placement remains independent.
                extra_ink[side] = cleaned.difference(assembled)
        # Bound cache to sources in this project, not every SVG ever browsed.
        self.sources = sources
        return StackResult(request.key, results, originals, extra_ink)
