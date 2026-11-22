"""Validation for explicit processing-grid choices."""

import math


def validate_processing(res: float | None, resampling: str) -> None:
    if res is not None and (isinstance(res, bool) or not isinstance(res, (int, float))
                            or not math.isfinite(res) or res <= 0):
        raise ValueError("Resolution must be a positive finite number")
    if resampling not in ("nearest", "bilinear", "cubic", "average"):
        raise ValueError("Unsupported raster resampling method")
