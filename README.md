# reg-platform

The projection layer of REG. It reads `reg-conformance` at one pinned commit
and writes one file, `projection/projection-v1.json`, that the presentation
layer (`reg-site`) reads and nothing else.

```
reg-conformance (catalogue, vectors, evidence)   ← source of truth
        │  source.lock.json pins one commit
        ▼
reg-platform  →  projection/projection-v1.json   ← contract: contracts/projection-v1.schema.json
        │        badges/*.svg
        │  reg-site pins content_sha256
        ▼
reg-site (static pages)
```

The layers are linked only by the versioned contract. `reg-site` never reads
`reg-conformance`; `reg-platform` never renders a page.

## Run

```sh
pip install -r requirements.txt
./run_all.sh                       # clones reg-conformance into .cache/
REG_CONFORMANCE=../reg-conformance ./run_all.sh   # or use a local checkout
```

`run_all.sh` checks out the pinned commit, builds the projection, validates
it against the schema, renders the badges and runs the guard tests. Any
failure stops it.

## Guards

The projection is fail-closed. It records every finding; any `error` sets
`guards.passed` to false, and the badge renderer and the site build refuse
such a projection.

| Code | Level | What it catches |
|---|---|---|
| `SRC-DIRTY` | error | the reg-conformance checkout has uncommitted changes |
| `SRC-COMMIT` | error | HEAD is not the pinned commit |
| `CAT-MISSING`, `CAT-COUNT` | error | the catalogue does not list every axis combination |
| `CAT-ADMISSIBILITY` | error | the catalogue's `admissible_structural` disagrees with the rules recomputed here |
| `CAT-REF` | error | a configuration cites a guarantee record that does not exist |
| `GUARD-UNBOUND-CLAIM` | error | a configuration is marked demonstrated or partial, but a record it cites has no bound evidence |
| `VEC-DUPLICATE` | error | two vectors share an id |
| `CAT-RULES-VERSION` | warning | the catalogue and the rules file declare different rule versions |
| `REC-PERIMETER` | warning | a guarantee record declares a perimeter the rules reject |
| `VEC-DIST-STALE` | warning | `vectors/dist/vectors.json` differs from the YAML sources |

**Bound evidence.** A guarantee record is bound only when its
`test_results_hash` is the sha256 of a file tracked in reg-conformance at the
pinned commit, its `demonstration_scope` is set, and its validation flag is
true. A hash that nothing in the repository reproduces is a declaration, not
evidence.

**States.** `demonstrated` (every cited record bound, production-equivalent),
`partial` (bound, loopback-test-only), `not_evaluated`, `refuted`,
`inadmissible`. A state is never inferred from structural admissibility.

**No claims.** `claims.conformance_claims_allowed` is false and
`claims.subjects` is empty in contract v1.0. Results of the runner's native
mode are projected as `reference_kernel_result`: they describe the
repository's reference kernel, never a product. Subject runs arrive in
contract v1.1.

## State at the pinned commit (b943f06)

42 of 144 configurations are admissible; all 42 are `not_evaluated`. None of
the 13 guarantee records is bound. 60 vector ids.

Two warnings are open in reg-conformance and are reported, not fixed, here:
`valid-configurations-v1.1.0.json` declares rules 2.0.0 while
`compatibility-rules-v1.yaml` declares 1.0.0; `raft-consensus-replication`
declares a mono-host + distributed-consensus perimeter that rules R1b and R2a
reject, and its results hash matches no file in the repository.

## Updating the data

1. Change `commit` in `source.lock.json`.
2. Run `./run_all.sh`.
3. Commit `source.lock.json`, `projection/` and `badges/` together.
4. In reg-site, run `npm run pin` to take the new `content_sha256`.

CI fails if the committed projection does not match what the pinned commit
produces.

License: Apache-2.0.
