# P4a historical evidence audit addendum

The Independent Foundation Audit revalidated the frozen P4a ledger at SHA-256 34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5, 441,311 bytes, with 180 measurement records. The records form 90 valid M1/Bare M2 pairs across 30 workloads and three repetitions per workload. M1 ran first in 60 pairs, and M2 ran first in 30. Repetitions are nested within workloads. The audit did not establish elimination of execution-order bias.

| Historical timing scope | Computable pairs | Median M1 / Bare M2 |
|---|---:|---:|
| Search | 84 | 2.1268866741771673 |
| Native Step | 90 | 1.8021116934915766 |
| Streaming Loop | 90 | 1.1135918146212243 |
| Outer Wall | 90 | 1.04136318044999 |

M1 recorded 455,148 candidate evaluations and Bare M2 recorded 245,055. These are descriptive pilot results for the measured harness. Streaming Loop includes construction of deep graph diagnostics. The pilot used bounded workloads and does not support a production-scale NetFlow claim.

The raw ledger retains aggregate counters and historical M0 correctness attestations. The registered package does not contain the full per-step tuple payloads from the original executions. The audit separately replayed correctness on the same binary SHA with profiling off. It verified 60 runs, 552 steps and 24,768 ordered tuple events against an independent oracle. That replay provides supplemental correctness evidence. The original timings remain those in the frozen ledger.

The audited P4a workloads contain no non-ASCII labels. The Unicode counterexamples affect the native general label contract and do not invalidate the measured P4a workload results. The [registered evidence manifest](evidence/p4a_evidence_manifest.json) continues to identify the frozen authority files.
