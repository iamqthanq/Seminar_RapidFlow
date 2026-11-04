# RapidFlow (PVLDB 2022) — pristine upstream source

- Original repository: https://github.com/shixuansun/RapidFlow
- Pinned upstream HEAD: `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
- Upstream date: 2022-06-30
- License: MIT (see `source/LICENSE`).
- Local checkout: `baselines/rapidflow/source/` (ignored by Git; its own Git working tree is clean).
- Original author test datasets are included at `source/test/insert/` and `source/test/delete/`, NOT NetFlow.
- Build instructions from upstream: `cmake -S source -B build && cmake --build build`; upstream builds `RapidFlow.out`.
- This device currently has `g++` and `make`, but `cmake` was NOT found as of 2026-11-04; no build has been attempted.
- Upstream CLI: `RapidFlow.out -d <graph> -q <query> -u <update> -num MAX -time_limit <seconds>`. NOTE: `MAX` removes the configured enumeration cap; independently verify whether the implementation prints explicit embeddings or only aggregate counters.

DO NOT edit upstream source in place for a proposed improvement. Preserve this pinned version for fair comparison.
