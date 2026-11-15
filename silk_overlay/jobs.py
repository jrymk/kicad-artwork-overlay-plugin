"""One bounded geometry worker; no wx or KiCad imports or callbacks."""
from concurrent.futures import ThreadPoolExecutor, CancelledError
from dataclasses import dataclass
from threading import Event
from time import perf_counter
import json
from shapely.ops import unary_union
from .groups import resolve_colours, build_group_geometry
from .geometry import final_artwork
from . import placement
from .stack import StackRequest, StackCalculator, UpdateRequest, calculate_update
from .project import cleanup_width


@dataclass(frozen=True)
class ArtworkRequest:
    key: tuple
    source: object
    settings: dict
    blockers: object
    boundary: object


@dataclass(frozen=True)
class ArtworkResult:
    key: tuple
    geometry: object


class PreviewCalculator:
    """Cache belongs exclusively to the single worker; inputs are snapshots."""
    def __init__(self):
        self.cache = {}
        self.items_key = None
        self.items = None
        self.pads = None
        self.result_key = None
        self.result = None
        self.stack = StackCalculator()

    def __call__(self, payload, cancelled):
        if isinstance(payload, UpdateRequest):
            return calculate_update(payload, cancelled)
        if isinstance(payload, StackRequest):
            return self.stack(payload, cancelled)
        if isinstance(payload, ArtworkRequest):
            if cancelled():
                raise CancelledError()
            art = placement.materialize(payload.source, payload.settings)
            result = final_artwork(art, payload.blockers, payload.boundary,
                                   cleanup_width(payload.settings), cancelled)
            if cancelled():
                raise CancelledError()
            return ArtworkResult(payload.key, result)
        items, assignments, matching, rules, show_pads = payload[:5]
        side = payload[5] if len(payload) > 5 else "Front"
        started = perf_counter()
        key = tuple(id(item) for item in items)
        if key != self.items_key:
            self.cache.clear()
            self.pads = None
            self.items_key = key
            self.items = items  # Prevent recycled object ids from matching an old snapshot.
        result_key = (key, json.dumps([assignments, matching, rules], sort_keys=True), side)
        if result_key != self.result_key:
            colours = resolve_colours(items, assignments, matching)
            blockers, areas, outlines = build_group_geometry(items, colours, rules, self.cache, cancelled, side)
            if cancelled():
                raise CancelledError()
            self.result = colours, blockers, areas, outlines
            self.result_key = result_key
        colours, blockers, areas, outlines = self.result
        if cancelled():
            raise CancelledError()
        if show_pads and self.pads is None:
            self.pads = unary_union([item.ink for item in items if item.kind == "footprint"])
        return colours, blockers, areas, outlines, self.pads if show_pads else None, perf_counter()-started


class LatestJob:
    def __init__(self, calculate):
        self.calculate = calculate
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="silk-preview")
        self.future = None
        self.pending = None
        self.cancel_event = None
        self.revision = 0
        self.running_revision = 0
        self.closed = False

    def invalidate(self):
        self.revision += 1
        self.pending = None
        if self.cancel_event is not None:
            self.cancel_event.set()

    def request(self, payload):
        if self.closed:
            return
        self.invalidate()
        self.pending = (self.revision, payload)
        self._start()

    def _start(self):
        if self.closed or self.future is not None or self.pending is None:
            return
        self.running_revision, payload = self.pending
        self.pending = None
        self.cancel_event = Event()
        self.future = self.executor.submit(self.calculate, payload, self.cancel_event.is_set)

    def poll(self):
        """Return (revision, result, error) only for the latest requested result."""
        if self.closed or self.future is None or not self.future.done():
            return None
        result, error = None, None
        try:
            result = self.future.result()
        except CancelledError:
            pass
        except Exception as exc:
            error = exc
        current = self.running_revision == self.revision and not self.cancel_event.is_set()
        revision = self.running_revision
        self.future = None
        self._start()
        return (revision, result, error) if current else None

    def close(self):
        self.closed = True
        self.invalidate()
        self.executor.shutdown(wait=False, cancel_futures=True)
