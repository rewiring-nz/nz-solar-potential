"""Resolve imagery files for the current pipeline run."""

import os


def selected_imagery_paths(paths):
    """Return only imagery inputs allowed by this run's source selection."""
    if os.environ.get("SOLAR_INCLUDE_IMAGERY", "1") == "0":
        return None, None
    imagery = paths["imagery"] if paths["imagery"].exists() else None
    reference = paths["reference_imagery"]
    photo = reference if reference.exists() else imagery
    return imagery, photo
