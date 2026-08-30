# ADR 0001: Containerisation strategy

Date: 2026-08-30

## Status

Proposed for evaluation. Docker is not currently required for development or
data maintenance.

## Context

The pipeline runs locally and depends on Python geospatial packages, large
local rasters and point clouds, LINZ credentials, and long-running regional
builds. Most current development is on macOS, while instructions must also
serve Ubuntu and Windows users. Cloud execution is a future maturity stage.

Containers could make Python and native geospatial dependency versions more
repeatable and create a bridge to future batch infrastructure. They also add
complexity around large bind mounts, file ownership, disk and volume
performance, Apple Silicon compatibility, local debugging, and credential
handling. Those costs are material for high-volume geospatial processing.

## Decision

Keep the `.venv` local workflow as the supported baseline. Do not add Docker,
Docker Compose, or a cloud runtime until an evaluation demonstrates a concrete
benefit for this project.

## Adoption criteria

Consider a container proof of concept when one or more of these conditions is
met:

- Reproducible setup repeatedly fails across supported platforms.
- Native dependency drift causes meaningful contributor or release delays.
- A cloud batch target requires a portable execution artifact.
- CI needs a consistent geospatial runtime that cannot be maintained reliably
  with ordinary dependency installation.

The proof of concept must build one representative area, run the existing
validation checks, preserve access to host data without copying it into images,
keep `LINZ_API_KEY` outside the image and repository, and measure setup time,
runtime, memory use, disk use, and macOS/Ubuntu performance against the native
workflow.

## Consequences

Current setup remains simple and local-first. Reproducibility depends on the
documented Python environment and supported package wheels. Any later adoption
must update the local setup and operations guides, record measured trade-offs,
and retain a workable path for developers who do not use containers.