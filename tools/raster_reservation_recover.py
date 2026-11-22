#!/usr/bin/env python3
"""Recover one orphaned experimental raster disk reservation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.raster_reservations import DiskReservationError, DiskReservations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, help="Reservation registry directory")
    parser.add_argument("reservation_id", help="32-character reservation ID from the record filename")
    parser.add_argument("--confirm-consumers-stopped", action="store_true",
                        help="Confirm the owner and all consuming child processes have stopped")
    args = parser.parse_args(argv)
    if not args.confirm_consumers_stopped:
        parser.error("inspect the owner and descendants, then confirm all consumers have stopped")
    try:
        DiskReservations(args.registry).recover_orphan(args.reservation_id, consumers_stopped=True)
    except (DiskReservationError, OSError, ValueError) as exc:
        print(f"Reservation recovery failed: {exc}", file=sys.stderr)
        return 2
    print(f"Recovered orphaned reservation {args.reservation_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
