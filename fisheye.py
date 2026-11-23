"""Bounded focus-and-context projection for a two-dimensional icon grid."""


def axis(value, focus, length, strength=2.0):
    """Magnify around focus, compress distance, and preserve both boundaries."""
    if length <= 0:
        return 0.0
    value = min(length, max(0.0, value))
    focus = min(length, max(0.0, focus))
    delta = value - focus
    if not delta:
        return focus
    span = length - focus if delta > 0 else focus
    ratio = abs(delta) / span
    offset = span * (strength + 1.0) * ratio / (strength * ratio + 1.0)
    return focus + offset if delta > 0 else focus - offset


def inverse_axis(value, focus, length, strength=2.0):
    """Map a projected pointer back to the original grid without hit-test drift."""
    if length <= 0:
        return 0.0
    value = min(length, max(0.0, value))
    focus = min(length, max(0.0, focus))
    delta = value - focus
    if not delta:
        return focus
    span = length - focus if delta > 0 else focus
    ratio = abs(delta) / span
    offset = span * ratio / (strength + 1.0 - strength * ratio)
    return focus + offset if delta > 0 else focus - offset
