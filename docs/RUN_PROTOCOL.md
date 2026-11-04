# Experimental protocol — RapidFlow / NetFlow

## Stage 0 — inventory (read-only)
- Locate RapidFlow source and NetFlow on this device; inspect first.
- Record source URL, commit hash, license, compiler and build flags.
- Record graph format, vertex/edge labels, directedness and update semantics.
- Record exact graph/stream paths, sizes and SHA-256 hashes.
- Do not silently modify data or assume dataset equivalence based on filename.

## Stage 1 — smoke and correctness
- Compile original RapidFlow in its own checkout/build directory.
- Verify insert and delete behavior on small graphs.
- Define whether output is counts, explicit embeddings, or both. Record any limit/truncation.
- Compare delta embedding sets with an independent oracle (BL-02 from team repo can be reused by reference).
- Audit duplicate embeddings, automorphism handling, injective/non-induced interpretation, labels, update ordering.
- Record pass/fail/blocked separately for each test.

## Stage 2 — baseline
- Fix one graph + query + stream + executable commit + parameters for every case.
- Run one case at a time first. Explicit timeout; capture exit code, wall time, max RSS, stdout/stderr.
- For comparison, separate counting vs full enumeration and avoid conflating enumeration with writing matches.
- Report both complete and timed-out runs; never present timeout's partial counts as final outputs.
- Run repeats only after correctness and reproducibility pass.

## Stage 3 — profiling
- Instrument cautiously in a separate branch, with minimal changes and check against uninstrumented results.
- Attribute time to: initial index building, per-update index work, candidate generation, backtracking/enumeration, output.
- Record search states, candidates, number of *valid* matches, labels, query topology and degrees when accessible.
- Distinguish redundant search from unavoidable output cost.
- No novelty claim without prior-art matrix and ablation test.

## Running safely
```bash
python3 scripts/run_case.py \
  --case-id smoke-insert-01 \
  --dataset-path /absolute/path/to/data.graph \
  --query-path /absolute/path/to/Q_0 \
  --stream-path /absolute/path/to/insertion.graph \
  --timeout 60 \
  -- \
  /absolute/path/to/rapidflow <ACTUAL_FLAGS_VERIFIED_FROM_UPSTREAM>
```
The angle-bracket placeholder is explanatory, **not an executable example**. Inspect the upstream CLI first. The script records hashes only if paths exist. Captured raw files are ignored by Git. A successful exit is not correctness proof.
