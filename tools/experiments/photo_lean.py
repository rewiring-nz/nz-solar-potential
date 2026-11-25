"""The lean module lives in src/photo_lean.py; this name is kept for the
experiment scripts beside it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src import photo_lean as _pl
from src.photo_lean import *          # noqa: F401,F403
from src.photo_lean import line_samples, photo_lean, leaned, absorb_slivers  # noqa: F401


def __getattr__(name):
    return getattr(_pl, name)
