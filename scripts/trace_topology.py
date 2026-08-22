"""Shared topology-safe pixel adjacency for raster skeleton tracers.

An 8-connected skeleton needs diagonal edges for true diagonal strokes, but a
raw 8-neighbour graph also adds a diagonal across every rasterized right-angle
corner. That turns a simple bend into a three-edge cycle and can split one
smooth satin run into several independently capped fragments.
"""

from __future__ import annotations

from collections.abc import Collection


NB = (
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),            (0, 1),
    (1, -1),  (1, 0),   (1, 1),
)


def topology_neighbors(
    point: tuple[int, int],
    coords: Collection[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Return topology-preserving neighbours of ``point`` in ``coords``."""
    y, x = point
    out: list[tuple[int, int]] = []
    for dy, dx in NB:
        candidate = (y + dy, x + dx)
        if candidate not in coords:
            continue
        if dy and dx and ((y + dy, x) in coords or (y, x + dx) in coords):
            continue
        out.append(candidate)
    return out
