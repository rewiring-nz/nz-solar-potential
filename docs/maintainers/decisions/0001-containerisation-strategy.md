# ADR 0001: Containerisation

Date: 2026-08-30

## Status

Proposed for evaluation. Docker is not required.

## Context

The pipeline needs Python geospatial packages, native GDAL tools, large
rasters and point clouds, LINZ credentials and long builds. Most work is on
macOS; the guides also cover Ubuntu and Windows.

Containers could pin dependency versions and help with batch jobs. They also
add cost: large bind mounts, file ownership, disk performance, Apple Silicon
support, debugging and credential handling.

## Decision

The local `.venv` workflow stays the baseline. Add no Docker, Compose or
container runtime until an evaluation shows a real benefit.

## When to reconsider

Try a container proof of concept if:

- setup keeps failing across supported platforms;
- native dependency drift delays contributors or releases;
- a batch target needs a portable artefact; or
- CI needs a geospatial runtime that ordinary installs cannot keep stable.

The proof of concept must build one area, pass the existing checks, read
host data without copying it into the image, keep `LINZ_API_KEY` out of the
image and repo, and measure setup time, runtime, memory and disk against the
native workflow on macOS and Ubuntu.

## Consequences

Setup stays simple and local. Reproducibility rests on the documented Python
environment. Any adoption must update the setup guides, record the measured
trade-offs, and keep a path for developers who do not use containers.
