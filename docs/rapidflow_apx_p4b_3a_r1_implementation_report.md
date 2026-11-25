# RapidFlow-APX P4b-3A R1 independent review handoff

Issue #38 and existing Draft PR #39. The phase remains a preregistration candidate.
This addendum records R1 executions separately from the historical P4b-3A report.

## Provenance and cleanup

The implementation started at `9d2b2f18b40f6a89324c58e0e4a354b59200d5e7`.
Both local refs and the PR head agreed with that revision. The clean worktree
was switched back to `feature/rf38-p4b-optimization`. The local restart branch
pointed to the same revision, had no distinct commits and was unoccupied before
deletion. Exactly the two confirmed prior R1 scratch directories were removed
after inventory. No prior patch was used. No stash was present. New evidence
lives under `/tmp/rapidflow-p4b3a-fresh-r1-Oikvdr`.

| Identity | Value |
|---|---|
| Accepted P4b-2B source | `a4e9d41defa78a3cc1b34f27bff43aa98cf858db` |
| Accepted P4b-2B binary SHA-256 | `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78` |
| Locked native source | `97d8636b9567d53d3e083c48d61b46feb40ae540` |
| Locked native SHA-256 | `f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75` (941,216 bytes) |
| PRE-R09 and canonical source | `dce3cce8f7c2093caccdb83ee82e471f371f1507` |
| Final tested implementation | `a4262e7ba9c51ed70aaf0936425ef3b9c41c1097` |
| Pinned upstream | `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1` |
| Protocol SHA-256 | `70836770663c32a1a53e52e048be1dd8268fd2e3f02f66859737eef308c4acbb` |
| Workload manifest SHA-256 | `09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869` |
| Schedule SHA-256 | `824d4b09f31432fde355597aee877f38cae3f267b66dd658d8fc82c3cac05e6f` |
| Historical P4b-3A archive SHA-256 | `cf7f1cfb6beed9cc6821deddaa0b3694727e8550d7ef2823bff40d6fb3f8b671` |

Verified 50 fixture hashes, seven P4a artifacts and 33 historical P4b-3A manifest
entries, including registered byte sizes. The matching engine, native harness,
shared recursive comparator, fixtures, generator, protocol and schedule
are unchanged. R1's Python replay command, match predicates and native runner
paths exercised by PRE-R09 are identical to the final tested implementation.

## Changed files

| Path | Responsibility |
|---|---|
| `scripts/run_rapidflow_apx_p4b.py` | Frozen plan and native identity, resource outcomes, strict attempt resolution, evidence binding and persistent synthetic rehearsal. |
| `scripts/summarize_rapidflow_apx_p4b.py` | Synthetic isolation, computed eligibility, coverage/censoring, primary and stratum estimates, bootstrap and decision precedence. |
| `scripts/replay_rapidflow_apx_p4b.py` | Actual correctness replay, M0 and graph reconstruction, full tuple comparison and raw evidence. |
| `tests/test_rapidflow_apx_p4b_measurement.py` | Attempt histories and existing native isolation checks. |
| `tests/test_rapidflow_apx_p4b_statistics.py` | Full synthetic ledger identities, corrected boundary and provenance expectations. |
| `tests/test_rapidflow_apx_p4b_r1.py` | Mutation tests, crossed evidence and raw tuple checks, slot resume and native fault classification. |

## Findings matrix

Baseline counterexamples come from the approved Git blobs loaded in memory
by `reproduce-baseline.py`, recorded in `baseline-reproduction.json`.

| ID | Baseline source and cause | R1 change and test | Result |
|---|---|---|---|
| PRE-F01 | `summarize_rapidflow_apx_p4b.py:355` let the default path label fabricated dictionaries as confirmatory | Explicit synthetic origin, experiment identity and native-launch authorization gate. Forged approval and cross-origin mutations rejected in `test_f01`. Persisted synthetic records rejected by default in `test_r10_synthetic_journal_full_plan_and_resume`. | PASS |
| PRE-F02 | `is_slot_eligible` accepted an M1 record with a wrong protocol, no graph steps and an M2 slot identity | Plan-derived seal fields bind experiment, configuration, source, binary, seed, session, slot and repeat. Strict types, update count, raw hashes and native reference binding verified with identity mutations and a bounded native smoke in `test_f02` and `test_r10_raw_measurement_requires_exact_same_variant_correctness`. | PASS |
| PRE-F03 | `classify_variant_decision` threw on missing RSS when there were zero complete pairs | Absent estimates are null. Censored observations remain counted, never imputed. Priority 3 and 4 distinguish resource regression from no common sample after integrity checks. `test_f03` drives every registered censor category and checks zero-pair decisions. | PASS |
| PRE-F04 | The analyzer never built or passed strata to the classifier, so priority 6 was unreachable through it | Derive all 15 strata from the locked registry, check expected counts, resample by the declared units and respect descriptive-only strata. `test_f04` reaches priority 6 through the full pipeline and rejects mutated membership. | PASS |
| PRE-F05 | A singleton DISC workload entered the point estimate but was absent from bootstrap. Fixed Bonferroni intervals were mislabeled as Holm. | Point and bootstrap use the same workloads. Unmatched DISC is descriptive and remains in resource/censor tables. True supplemental Holm step-down adjustment of centered-log-bootstrap p-values, separately labeled Bonferroni intervals, exact RSS gate, unadjusted primary 95% CI. `test_f04`, `test_f05` and the classifier numeric boundary suite. | PASS |
| PRE-F06 | `resolve_p4b_ledger_records` picked a later eligible row even after an earlier mismatch | Ordered attempt IDs and parent references. Only a verified prelaunch cancellation can precede evaluation. No retry of a launched, completed, censored, failed or ambiguously interrupted slot is registered. Resolver and durable-resume mutations exercise duplicates, missing parents, failure history, incomplete rows and retry after completion. | PASS |
| PRE-F07 | `validate_schedule` admitted a cross-workload position swap that preserved global counts | Byte locks and exact deterministic schedule regeneration verify all slot assignments, counts, positions and purposes, including warmups and diagnostics. `test_f07` mutates each phase while retaining counts. | PASS |
| PRE-R08 | Historical source and binary were preserved. Canonical evidence had to be re-executed | Six configurations ran all hand, corpus and stream stages. Executed frozen binary controls and five sanitizer selectors. | PASS |
| PRE-R09 | Full hash-bound correctness output was required. Native emission and M0 list orders differ. | Separate correctness-only runner, state reconstruction, raw full tuples, scoped comparison and independent artifact verification. All 250 rows and 50 oracle files validated. | PASS |
| PRE-R10 | Baseline CLI printed a gate message, with no durable schedule rehearsal or crash state | Full synthetic append/resume path, fsync, explicit START/FINISH, output binding and interrupted-session handling. Bounded native smokes exercise performance/diagnostic isolation, timeout, address-space cap, signal, missing/stale output and wrong raw/reference evidence. Confirmatory CLI remains locked. | PASS (R1 scope) |
| PRE-R11 | Measured scope, cohort denominators and historical evidence required reconciliation | This addendum, hash-linked raw outputs, historical byte checks, exact command log, ASan/UBSan evidence and clean revision test logs. | PASS |

## Correctness certification

| Unit | M0 verified | Differentially verified | Censored | Failed |
|---|---:|---:|---:|---:|
| 50 frozen workloads | 32 | 16 | 2 | 0 |
| 250 native executions | 160 | 64 variant vs BARE | 10 | 0 |

The run row excludes 16 C3 BARE reference runs from the certification columns.
They completed and were independently checked for graph state, codec and stream
invariants, but cannot be differential certificates for themselves. M0
was unavailable on 18 workloads under the existing generator tier envelope
(100,000 candidate injections, 64 data vertices, 6 query vertices).
`DISC_02_K0` exceeded it at 863,040 and both `DISC_07` workloads at
53,981,544,960. Their fixture bytes and limits were unchanged.
`DISC_07_K0` committed 10 steps and left 69 unattempted. `DISC_07_K1`
committed zero and left 79 unattempted. Both have one attempted failed step,
exit 4 and native `WORK_BUDGET_EXCEEDED`, across all five modes. The complete
emitted event/transaction prefix was retained.

The replay saved native JSON losslessly in gzip, both compressed and original
SHA-256, raw input SHA-256, native exit, step records including ordered tuples
and counters, native source, replay source, command, and oracle output. An
independent artifact reader validated 250 raw outputs, 50 oracle artifacts
and 7,170 committed prefix steps. It recomputed 3,400 duplicate-free M0
event collection comparisons. In 820 of those, external list order differed.
The contract orders coordinates by canonical query ID. The oracle sorts the
set delta and the native sink retains traversal order. Replay checks exact
coordinate sequences and duplicates before comparing M0 event sets.
Independent cross-mode verification covered 100 Option B pairs across every
step record (3,168 records) and 100 Option A pairs across 5,736 committed event collections.
Option B retains native list order, graph and codec states, lifecycle and legacy
counters in the recursive comparison. Its scoped exclusions are the existing
mode/counter allowlist plus explicitly named numeric timing and RSS fields.
Unknown fields in the timing object still compare. A mutation with reordered
tuples and a recomputed raw hash is rejected.

| Canonical stage | M1, BARE, STATIC, RESIDUAL, BUFFER, MERGE |
|---|---|
| Hand | 25 each |
| Corpus | 840 each |
| Dynamic streams | 56 and 672 steps each |

The P4b-2B locked binary and P4b-3A binary also produced exactly equal JSON
and exit codes on 150 hand-case/configuration pairs (25 × 6). This is
a bounded historical comparison. The complete canonical suites ran against
the current locked binary.

## Statistical and execution scope

The frozen primary inferential set is the union of eligible complete Continuity
workloads and complete matched DISC groups. Five successful repetitions on
both sides are required to form a workload ratio. Reported cohort IDs make
the point, distribution, bootstrap, coverage and jitter denominators auditable.
All complete workload ratios are separately retained for descriptive and
worst-workload RSS reporting. A synthetic 49-pair fixture with a single
extreme unmatched DISC observation uses 48 workload units for both the
primary point estimate and its 10,000-resample interval. The primary
bootstrap consumes seed 380302 and keeps matched pairs together. Holm
adjusted p-values are an approximate sensitivity check and do not control
the decision classifier. No independence claim is made for related
Continuity source families or for populations outside this fixed sample.

The primary Williams assignment is locked to the registered sequences and
repetition order. The auxiliary diagnostic rotations are also locked and
their empirical positions are irregular (for example BARE has counts
5/14/14/11/6). Attributions are descriptive. Diagnostic observations
and B_BUFFER are outside the confirmatory sample.

A synthetic rehearsal persisted 217 then 1,450 scheduled records via resume,
then used the recovered records for analysis of synthetic observations.
No native subprocess was invoked by that rehearsal. START without FINISH
is retained and blocks continuation at that slot. An interrupted run
does not authorize a replacement. Folder and file sync, exclusive raw
output, attempt identity and independent payload identity avoid a stale
or cross-slot artifact being mistaken for completion. Native resource
classification and raw measurement binding were tested separately.

### Measurement boundaries

`tests/native/rapidflow_apx_engines/src/harness_runner.cpp` times parsing
and initialization separately. For PERFORMANCE and DIAGNOSTIC the retained
vector's initial reserve precedes the timed region. Between native
`PRIMARY_TIMER_START` and `PRIMARY_TIMER_STOP`, the update executes and
the code retains `RetainedStepOutcome` and full tuples. Dedicated
reporting constructs step JSON outside the primary interval.
`reporting_wall_ns` stops before the final streamed file write.
The child samples Linux `VmHWM` before that write and reports the original
KiB and exact byte conversion. This can omit a later write-stage peak.
Python's outer interval covers launch through `wait4` observation with
roughly 2 ms polling. The fallback `ru_maxrss` is per wait and can include
pre-exec inherited parent RSS. Fallback values cannot establish eligible
success RSS. A bounded child/parent bloat witness checks that distinction.
Logical allocation-free behavior is not claimed.

## Regression and evidence

| Check | Result |
|---|---|
| Clean-revision full repository regression | 856/856 passed in 396.760s. 0 failures, 0 errors, 0 skips, exit 0. Targeted correction suite 29/29, exit 0. |
| Targeted sanitizer replay of unchanged binary | 5/5 selectors exit 0 with ASan+UBSan. Leak detection disabled for known upstream `Graph::buildEdgeIndex` sparse hash map leakage. |
| Historical integrity | Seven P4a files and 33 historical P4b-3A files verified. |
| Evidence archive | `/tmp/rapidflow-p4b3a-fresh-r1-Oikvdr/review-evidence-p4b3a-r1.tar.gz (22b5245d24d47b00fec6f3ab7b0960a6d6a27636759cc9c7c1259a343882cfe9)` |
| SHA-256 manifest | `cdd7ae9e7063649e8945a6e7a086d16e54410b43c313623f935b2bc9309a1898 (1,811 hashed artifacts)` |

The archive contains the clean revision logs, baseline probes, canonical
results, PRE-R09 raw gzip and oracle files, synthetic journal, sanitizer
results, commands, environment, and verification records. Release binary
and historical archive remain separate, content-identified artifacts.
The two execution flags return exit 2 even with caller-supplied approval
fields. No actual primary, warm-up or diagnostic schedule, comparative
timing result, ranking, winner selection or P4b-3B work occurred.
The mandated repository suite includes the existing bounded P4a Diamond
smoke with three calls per profiling mode. It checks successful child
execution and positive timing. Its results were excluded from R1 inference
and no historical observer-overhead evidence was regenerated.

## Limits and review gate

No actual authorized measurement or real confirmatory ingestion was exercised.
A separate reviewed activation must provide experiment authorization and
a trusted reference registry. The R1 CLI deliberately blocks both primary
and diagnostic execution and the analyzer blocks confirmatory result issuance.
Infrastructure readiness is established by the synthetic schedule and
bounded native smokes within the R1 authorization. DISC-07 remains censored
and all C3 results remain differential. Review is independent and the
experiment still requires coordinator authorization.

`P4B_3A_R1_READY_FOR_INDEPENDENT_REVIEW`
