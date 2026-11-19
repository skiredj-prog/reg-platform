# reg-projection contract

## 1.1.0 (19 November 2026)

First published version. It replaces the 1.0.0 draft, which was never
published.

- The content depends on `source.lock.json` only. `source.describe` is
  removed: it came from the tags the clone happened to hold, so two honest
  builds of the same commit could differ. `source.exact_tag` is now the tag
  named in the lock, verified to point to the commit (`SRC-TAG` otherwise).
- `source.files`: every file of reg-conformance the projection read, with its
  sha256 at the commit. The site links each figure to these files.
- Vectors: `registry` separates the runner's registered vectors (`vector`)
  from the proposals it does not register (`candidate`, `non_validated`).
  `summary.vectors_registered` is the repository's own count (56 at
  `b943f06`); `summary.vectors_total` still counts every id.
- Vectors carry their row of `VECTORS.md` (`index`: status IMPLEMENTED,
  DECLARED or RESERVED, family, normative property). That status is the one a
  quote keeps (CLAIMS.md). Also `input`, `intended`, `observed`,
  `reference`, `superseded_by`, `file` and `line`.
- `vector_index`: the prose of `VECTORS.md`, its status definitions, and the
  rows that no registered vector exercises (`rows_without_vector`).
- `summary.reference_kernel_pass`: passes of the reference kernel by index
  status, and how many rest on a superseded vector.
- `rules`: the compatibility clauses, so a reader can see what rejects a
  configuration.
- `claims_policy`: `CLAIMS.md` verbatim, section by section, with the status
  the file gives each section (`in_force`, `partly_in_force`, `draft`,
  `unknown`). `present: false` before reg-conformance v0.7.0.
- `reference_kernel_replay`: reg-platform runs `runner.py --mode native` at
  the pinned commit and compares every registered vector with the
  registry's `native` value. A difference, a missing or an extra id fails
  the guards (`VEC-REPLAY-*`). Each vector carries its replayed status.
- Guarantee records carry `source_sha256` and `perimeter_rejected_by`.
  Invariants carry `scope`, `applies_to` and `contract_ref`.

No field lets a reader infer conformance. `claims.subjects` stays empty;
subject runs need a later version of the contract.
