"""Fast preview transforms; exact polygons are built for cleanup and Apply."""
import math
from shapely import affinity


def matrix(settings):
    scale = settings["width"] / 2000.
    angle = math.radians(-settings["angle"])
    c, s = math.cos(angle)*scale, math.sin(angle)*scale
    mirror = -1 if settings["mirror"] else 1
    return c*mirror, -s, s*mirror, c, settings["x"], settings["y"]


def bounds(source_bounds, settings):
    a, b, d, e, x, y = matrix(settings)
    x0, y0, x1, y1 = source_bounds
    points = [(a*u+b*v+x, d*u+e*v+y) for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def materialize(source, settings):
    return affinity.affine_transform(source, matrix(settings))
