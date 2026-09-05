# AI project context

This directory is concise, version-controlled context for AI-assisted work on
`nz-solar-potential` only. It complements source code and normal project
documentation; it is not a private profile, hidden memory store, or substitute
for checking the current implementation.

## What belongs here

- Verified architectural decisions and operational constraints that are easy
  to miss in individual source files.
- Stable facts about source data, coordinate systems, output contracts, and
  supported workflows.
- Clearly labelled open questions and hypotheses that need confirmation.

## What does not belong here

- Secrets, credentials, personal data, transient logs, or verbatim chat
  transcripts.
- Duplicated walkthroughs maintained elsewhere in `docs/`.
- Claims that are not traceable to code, configuration, a source-data record,
  an ADR, or reproducible validation.

## Maintenance protocol

When adding context, state the fact, its evidence, the date checked, and the
source location. Replace superseded statements rather than appending competing
versions. Remove duplicate notes. Keep uncertain items in an explicit `Open
questions` section or a dedicated file, and resolve or delete them when
evidence arrives.

Initial durable context is in [project-facts.md](project-facts.md). Update it
with the code change that alters a fact, constraint, or decision.