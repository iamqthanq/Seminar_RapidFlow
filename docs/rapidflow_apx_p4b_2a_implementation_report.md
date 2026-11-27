# RapidFlow-APX P4b-2A implementation and evidence

**Proposed handoff** `P4B_2A_READY_FOR_INDEPENDENT_REVIEW`. This report records implementation-agent evidence. Independent acceptance belongs to the reviewer.

| Attribute | Actual result |
|---|---|
| Starting HEAD and initial draft PR head | `910e542fdf4cfdf836356039793b88a0b2348b17` |
| Tested implementation commit | `d2108ec6fee083ce0716e2935e07750e56461a72` |
| Final HEAD and remote PR HEAD | Documentation-only child that introduces this report. Exact matching SHAs are in `final-head.txt` and `pr-final.json` in the evidence archive. |
| Reference binary SHA-256 | `dbaa10b9085aedbcd2f23e883a379a282309f8ce55399e6ba1f3e424c99e4c16` |
| Clean release binary SHA-256 | `d21575c1c70d1f7bc2380d8172ae4044dacfc3f6ec464d06884555c284d36b84` |
| Trace binary SHA-256 | `d0aa90eef9cd01c2ede726aed0acab010f08d9d5a15b7474869ce134449fc491` |
| Sanitizer binary SHA-256 | `6206d6373f1a7bc8968072558b1b692bc3d1ea3aa91034f2d6de9228098a8543` |
| Production changes | Caller-owned retrieval on the authoritative graph, M2 search-local scratch pool and mode, explicit pool invariant containment, capacity diagnostics and CLI/runner wiring |
| Neighbor API | `ApxAuthoritativeGraph::get_neighbors_into(u, out)` |
| Pool and frame bound | Fixed outer vector of vectors, depth indexed. $(q-2)+k+1$ slots. |
| Actual reuse | Diamond sibling at depth 1 returned 4 neighbors in capacity 4 with no growth. |
| Parent/child | Live parent contents, vector identity, storage pointer and capacity verified after child return. C4 deferred child uses a different depth slot with the same assigned count. |
| Exact traversal | 8,541 normalized rows per mode across eight native cases agreed. |
| Exact cross-mode counters | All 921 canonical M2 workloads compared field for field, including all 840 corpus cases. |
| Canonical correctness | All five modes ran 25 hand cases, 840 corpus cases, 56 streams and 672 steps. |
| Full regression | 791 discovered, 791 run, 791 passed, 0 skipped, 0 failures, 0 errors, exit 0, unittest elapsed 301.978 seconds. No skip reasons. |
| Remaining verification limitations | Deterministic injected buffer failures, targeted ASan/UBSan and standalone pool LeakSanitizer. Integrated leak checks disabled because of the documented upstream leak. |

The approved worktree was `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT04B` on `feature/rf38-p4b-optimization`. Issue §7 authorized P4b-2A and recorded P4b-1B acceptance with documented limitations. The initial HEAD matched the open Draft PR targeting `QuangThang`. [Issue #38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38) · [Draft PR #39](https://github.com/iamqthanq/Seminar_RapidFlow/pull/39)

## Retrieval and snapshot correspondence

The new API clears the supplied vector before checking `has_vertex(u)`, so invalid and isolated vertices both give an empty result. Valid vertices scan the same RapidFlow CSR offsets as the legacy implementation. Base neighbors masked by either tombstones or overlay labels are skipped. The overlay pointer/count pair is read from the same `GlobalEdgeView` and its entries are appended. Sorting and unique removal run on the complete collection. Indexed loops consume zero elements when the count is zero, with no APX pointer-range arithmetic or persistent borrowed adjacency. This is the same ordered result
$\operatorname{sortunique}((B(u)\setminus(T(u)\cup P(u)))\cup O(u))$.
The RapidFlow in-memory loader allocates the CSR neighbor array even for zero elements, and the overlay accessor may return null with count zero. The public method can throw on vector growth. Any partially collected list belongs to the aborted search. [Authoritative graph](../extensions/rapidflow_apx/src/authoritative_graph.cpp) · [API declaration](../extensions/rapidflow_apx/include/rapidflow_apx/authoritative_graph.h)

The independent dictionary reconstructs every effective edge, label, degree and ordered neighbor list at its observable snapshots. Tests include tentative INSERT, base deletion and labeled replacement, and rollback of a DELETE. DELETE search runs before mutation. The staged deletion of an overlay edge is an internal transaction state and is not used for neighbor verification or candidate retrieval.

| Existing `get_neighbors()` path | Purpose | P4b-2A integration |
|---|---|---|
| `m2_event_engine.cpp` candidate loop | Hot M2 search path | BUFFER fills the chosen depth slot; LEGACY calls the original API. |
| `m1_enum_engine.cpp` and `micro_matcher.cpp` | M1 and exact search helpers | Original API. |
| `nlf_index.cpp` | Initial signature construction | Original API. |
| `authoritative_graph.cpp::get_degree` | Derived degree | Original API. |
| Native engine and foundation harnesses | Graph diagnostics, reconstruction or validation | Original API; direct tests call both. |
| Native P4b and residual tests | Independent test reconstruction | Original API; source inventory in `legacy-calls.log`. |

The original `get_neighbors()` implementation remains the comparison control. The new graph API is used by normal M2 only in buffer mode. Both paths use the pinned interfaces in `graph/graph.h::getVertexNeighbors` and `utility/relation/global_edge_view.h::get_neighbor`.

## Recursion theorem and actual lifetimes

| DFS call site | Parent context | Child assigned count | Child missing count | Depth |
|---|---|---:|---|---|
| `search_event` anchor root | Two endpoints and anchor PRESENT | 2 | 0 | 0 |
| `dfs_m2` PRESENT child | Tentative injective assignment, incident resolution accepted | Parent + 1 | Parent plus actual incident misses | Parent + 1 |
| `dfs_m2` deferred MISSING child | Frontier marked MISSING and updated count within budget | Parent | Parent + 1 | Parent + 1 |

These are the only call sites. Along a descent, PRESENT can increase the number of mapped vertices at most $q-2$ times. A deferred descent consumes one unit of the budget, at most $k$ times. Incident resolution may consume more budget in an existing frame. Depth is bounded by $(q-2)+k$ and includes one root slot in the pool size. The constructor uses `size_t`, checks $q\ge2$, overflow and outer `max_size()`, and sizes the pool once. `at(depth)` checks every used frame including leaf entries. Once recursion starts, the outer vector cannot be resized or relocated through the pool API. Each child picks the next depth, so no child mutates the active parent slot. On a normal return a sibling can refill that slot. Each anchor and each update retrieves current adjacency afresh. The successful inverse C4 DELETE also started a new search with zero root capacity. [Pool](../extensions/rapidflow_apx/include/rapidflow_apx/neighbor_buffer_pool.h) · [DFS wiring](../extensions/rapidflow_apx/src/m2_event_engine.cpp)

A frame uses the same preexisting frontier and ordered candidate checks in either mode. Given identical anchor state and identical neighbor lists, each candidate decision and recursive child state is identical, so the induction applies through both PRESENT and MISSING calls. This preserves tuple emission order and logical work, subject to resource failures specific to a storage mode. The trace records anchor and orientation, depth, mapping, commitment state, candidate sequence, actual candidate extensions, both recursive branch transitions and raw emission mappings. `normalized-traces.txt` retains both traces. A second bounded native comparison drives the complete registered correctness inventory against the same binaries and identical input bytes.

### Capacity and parent/child observations

| Trace state | Depth | Result size | Capacity before | Capacity after | Interpretation |
|---|---:|---:|---:|---:|---|
| Direct high-degree first fill | — | 65,536 | 3 | 98,304 | Grew |
| Direct high-degree refill | — | 65,536 | 98,304 | 98,304 | Nonempty reuse |
| Direct smaller result | — | 1 | 98,304 | 98,304 | Nonempty reuse |
| Direct isolated vertex | — | 0 | 98,304 | 98,304 | Capacity retained |
| Diamond PRESENT child | 1 | 4 | 0 | 4 | Grew while nonempty parent remained live |
| Diamond later sibling | 1 | 4 | 4 | 4 | Nonempty reuse |
| C4 deferred child | 1 | 2 | 0 | 2 | Grew in a distinct slot |
| C4 later sibling | 1 | 1 | 2 | 2 | Nonempty reuse |

The Diamond parent at depth 0 had candidates `[1,2,3]`, assigned count 2 and missing count 0. Its PRESENT child at depth 1 assigned a third vertex, kept missing count 0, and retrieved `[0,2,3,4]` while growing. The parent was checked on return and its candidate iteration continued. Diamond had 46 nonempty sibling reuses and 27 cross-anchor nonempty reuses. The C4 deferred witness starts with parent candidates `[1]`, assigned count 2 and missing count 0. The child at depth 1 keeps 2 assignments, increases missing count to 1, and fills a different slot with `[0,3]`. Parent identity, contents and storage were preserved after returning. K2 observed two root entries, depth 0 and zero retrievals.

| Trace case | Frame bound | Actual max depth | Slots with nonzero capacity | Legacy and buffer trace rows each |
|---|---:|---:|---|---:|
| K2 | 1 | 0 | 0 | 8 |
| Triangle-k0 | 2 | 1 | 1 | 66 |
| Triangle-k1 | 3 | 2 | 2 | 96 |
| C4 | 4 | 3 | 3 | 204 |
| C4-budget | 4 | 2 | 3 | 15 |
| Diamond | 4 | 3 | 3 | 942 |
| K4-k2 | 5 | 4 | 4 | 1025 |
| Prism-k2 | 7 | 5 | 6 | 6185 |

The native direct tests exercised 7 deterministic graph snapshots, 49 vertex comparisons and the 65,536-neighbor witness. Three generated streams at seeds 1, 7 and 42 attempted 216 operations, committing 154 and aborting 62. The independent dictionary verified 319 observable snapshots and 2,233 vertex comparisons, with 2,152 nonempty no-growth observations and zero exact-vector or duplicate discrepancies. The snapshots include initial, legacy-observable tentative INSERT, committed and restored states. [Direct and trace source](../tests/native/rapidflow_apx_engines/src/neighbor_buffer_tests.cpp)

### Diagnostics, memory and timing placement

`neighbor_buffer_retrieval_calls` counts successful fills from M2 candidate retrieval. `neighbor_buffer_growth_calls` counts those whose vector capacity increased across the fill. `neighbor_buffer_no_growth_calls` counts the remainder, including empty results. Equality of completed calls and their two categories is checked on step and cumulative data. Counts aggregate by addition and stay zero under LEGACY. The pool optional is disengaged under LEGACY. Logical work charging is unaffected. A capacity comparison is an observation about one retrieval and does not count allocator calls.

On this x86-64 build `sizeof(std::vector<uint32_t>) == 24`. Approximate logical storage is $24F + 4\sum_d c_d$ bytes for $F=(q-2)+k+1$ vector objects and retained inner capacities $c_d$. The Diamond observation `[4,8,8,0]` gives about 176 bytes of that storage. Measured vector-object size and reported capacities exclude allocator metadata, unused outer allocated capacity, stack objects, graph data, and process RSS. Initialization is $O(F)$ with lazy inner growth. The operation retains base/overlay scans with the same expected hash-lookup costs, sorting in $O(s\log s)$ for collected size $s$, and linear duplicate removal. Buffer reuse can avoid future capacity growth at the same depth while retaining more memory until search exit. Pool construction is within search and outside the neighbor timer. The actual fill, including sorting, stays inside the neighbor timer in both modes. Capacity classification is outside that timer.

## Configurations, failure handling and correctness

LEGACY remains the default. BUFFER is accepted only for M2/NONE. The same native predicate governs CLI validation and streaming initialization. Python also rejects ambiguous all-engine BUFFER and combined modes. `neighbor_configuration_version=1` identifies the additive native metadata. After initialization the harness supplies requested and effective neighbor modes. The existing configuration status and fallback reason continue to describe the NLF index. Live runner calls require current neighbor identity on all initialized outputs, including negatives. Early CLI, input or domain failures can omit it when existing lifecycle and exit-code checks establish that execution did not start. Historical native schema v1.1.0 records can still be validated by the historical path without new fields. Four real subprocess substitutions exercised both directions on success and initialized failure. The raw native error and step outcome survive a runner-side rejection. [Native harness](../tests/native/rapidflow_apx_engines/src/main.cpp) · [Runner](../scripts/run_rapidflow_apx_engines.py)

| Fault or lifecycle verification | Observed outcome |
|---|---|
| Existing graph and search hooks | Thirteen applicable BUFFER executions covered both operations, including INSERT-only forward/reverse interruption. Precommit failures restored state, emitted no committed tuples and allowed a valid retry. |
| Buffer failure hooks | Eight deterministic `bad_alloc` injections for INSERT and DELETE at pre-pool, first fill, late fill after one staged emission, and child fill. They are injected boundary failures. The tests do not exercise a failing system allocator. Search-local buffers were discarded, the graph restored and the retry used a fresh pool and matched all eight exact event tuples. |
| Depth guard and containment | Numeric tests exercise invalid query/size/depth dimensions. The native callback test invokes that same out-of-range guard on a one-slot test pool from a real non-root callback. The transaction rolled back and subsequent execution was blocked by session poisoning. Natural valid D-R traversal stayed within the proved bound. |
| Logical work cap | Both modes agreed on exits, counters, lifecycle and graph/codec for caps 1, 3, 9, 27 and 1,000. The cap-9 trace reached depth 2 before work abort. |
| Codec checkpoint | An injected failure before publication discarded a newly interned label. Matching resumed at the same codec size. A later committed INSERT added the label. |
| Unicode and typed labels | Raw/escaped audit witnesses, typed relabel and malformed input checks ran with existing modes and BUFFER. Distinct-label witness had zero events. Equivalent-label witness had one exact event per direction. |
| FAR delivery and fail-fast | Four reporting hooks for INSERT and DELETE preserved COMMITTED and made the remaining step UNATTEMPTED. The malformed suffix witness preserved its first failure and prevented any commit. |

A search unwind destroys the pool before control resumes at transaction containment. No buffer is stored in the transaction, graph, codec, or persistent stream state. Pool failure follows rollback with no mode downgrade. Trace and injectable hooks compile out of release. [Failure and mode regressions](../tests/test_rapidflow_apx_neighbor_buffer.py) · [FAR regressions](../tests/test_rapidflow_apx_foundation_audit.py)

| Verification | M1 / LEGACY | Bare M2 | STATIC / LEGACY | RESIDUAL / LEGACY | BUFFER |
|---|---:|---:|---:|---:|---:|
| Hand cases | 25/25 | 25/25 | 25/25 | 25/25 | 25/25 |
| Corpus agreement and zero duplicates | 840/840 | 840/840 | 840/840 | 840/840 | 840/840 |
| Registered streams | 56/56 | 56/56 | 56/56 | 56/56 | 56/56 |
| Stream tuple, graph, codec and reconstruction agreement | 672/672 | 672/672 | 672/672 | 672/672 | 672/672 |

The combined legacy runner classified its result as `P3_ENGINES_VERIFIED`. The three explicitly selected M2 runs covered their whole workloads and retain the runner classification `DIAGNOSTIC_SUBSET_PASS`. The oracle recomputed 8,002 corpus events across 330 cases with events, 239 at $k\ge1$. It recomputed 3,024 ENTERED and 1,780 EXITED stream tuples. The independent paired comparison checked process exits and complete parsed output, including exposed tuple sequence, statuses, commit, graph, codec, execution totals and every legacy/NLF counter. It excludes only the additive neighbor metadata and its three diagnostics. It compared 25 hand + 840 corpus + 56 stream workloads with no differences. All comparison runs were correctness executions.

| Observed logical total | LEGACY | BUFFER |
|---|---:|---:|
| Paired workloads | 921 | 921 |
| Attempted steps | 1,538 | 1,538 |
| Candidate evaluations | 118,624 | 118,624 |
| Candidate extensions | 56,317 | 56,317 |
| DFS entries | 55,123 | 55,123 |
| Logical work units | 203,695 | 203,695 |
| Raw emissions | 13,100 | 13,100 |
| Committed emissions | 13,100 | 13,100 |
| Discarded emissions | 0 | 0 |

Frozen starting-to-final controls agreed on 87/87 workloads each in M2 NONE, STATIC and RESIDUAL, and 31/31 targeted M1 workloads. This covers all 25 hand cases, 56 streams in each M2 control, both original Unicode and residual audit fixtures, and comparable success and work-budget failures. The full frozen corpus is outside that comparison. The workload bytes and limits match and all prior output fields and both counter levels are compared. The accepted 78 PBT streams and 780 updates were extended to BUFFER. The pool recorded 13,880 completed fills and 1,484 fills with growth. There were 27 event-bearing streams, 51 zero-event streams and 1,196 oracle events. Residual recorded 13,185 checks and 4,154 prunes. Across the four M2 variants the original M0 tuple, graph, codec and duplicate checks ran, with exact exposed sequence and legacy counters also compared for Bare M2 versus BUFFER.

P1 feasibility passed 6/6 and P2 foundation passed 8/8. The native Static groups passed. Residual numeric and reconstruction passed with 55,142 entries, 27,732 incident commitments, 42,118 deferred commitments, 114,190 restorations, 1,008 anchor restorations and zero discrepancies. The D-R empty-frontier invariant test passed. No natural no-frontier early return with unmapped vertices and valid D-R state is claimed, because the kernel guards that state.

## Sanitizers, provenance and preservation

Build toolchain and exact flags are in `environment.log`, `release-flags.log` and `instrumented-flags.log`. Release uses the repository runner build, C++17 and `-O3 -Wall -Wextra`. The instrumented build adds `-fsanitize=address,undefined -fno-omit-frame-pointer -g -fno-pie` and links `-fsanitize=address,undefined -no-pie`. The standalone pool test ran 1,000 lifetimes with LeakSanitizer enabled. Integrated graph and trace verification covered empty/invalid adjacency, large vector growth, parent/child lifetime, deferred and nested recursion, and injected unwind with LeakSanitizer disabled due to the documented upstream `Graph::buildEdgeIndex` leak. ASan/UBSan stayed enabled. Native unit-test dispatch precedes the workload CLI address-space cap. This is targeted sanitizer evidence, including no integrated leak assessment.

Evidence is retained at `/tmp/rapidflow-p4b2a-4TsBiw/`. The archive includes exact commands, raw input/output captures with hashes, full test logs, ordered traces, observed dispositions, runtime and peak RSS for test reproducibility, frozen and paired comparison summaries, and source and binary identities. Runtime values are test metadata.

All seven historical P4a artifacts and the historical binary were verified before and after by byte size and SHA-256. The raw ledger remains 441,311 bytes with SHA-256 `34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5`. The historical P4a binary SHA-256 is `6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b`. Shared upstream is pristine at `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`. [Historical manifest](evidence/p4a_evidence_manifest.json) · [Historical limitations](rapidflow_apx_p4a_audit_addendum.md)

BUFFER runtime advantage has not been established. P4b-2B sorted merge and combined A+B have not been implemented. NetFlow scalability and final optimization selection remain open. P4b-3 preregistration is still mandatory under AUD-P4B-001/002, including exact fixture bytes and generator, balanced order, PROFILE_OFF timing with deep diagnostics excluded, workload-level aggregation and bootstrap, censoring, memory eligibility, unique decision precedence, conditional A+B and evidence retention.


## Verification register

Native execution rows refer to `d2108ec6fee083ce0716e2935e07750e56461a72` and the binaries above. The command ledger and raw records retain exact argv, affected workloads, outcome and exit status. The revision for the start and handoff checks is identified in its row.

| ID | Verification and method | Expected | Observed | Exit and result | Evidence | Limitation |
|---|---|---|---|---|---|---|
| V-BA-01 | Repository preflight. `git status/rev-parse/worktree list; gh issue 38 and pr 39` | Approved clean branch, upstream and PR head match | Verified 910e542, authorization §7 and OPEN/DRAFT | 0, PASS | `preflight.log; issue-before.json; pr-before.json` | Starting state |
| V-BA-02 | Reference binary identity. `sha256sum baseline-native; independent P4b-1B report` | Expected frozen accepted binary identity | Preserved dbaa10b9… before rebuild | 0, PASS | `reference-binary.log; frozen-summary.json` | Starting state |
| V-BA-03 | Legacy retrieval call sites. `rg get_neighbors and review CSR, overlay and callers` | Enumerate all read paths, replace M2 seam only | Inventory reviewed; legacy implementation retained | 0, PASS | `legacy-calls.log; source diff` | Start and final |
| V-BA-04 | Output replacement. `native --test-p4b-neighbor graph` | Caller vector cleared on all invocations | Independent expected vectors equal, prepopulated invalid calls clear all entries | 0, PASS | `direct-graph.log` | — |
| V-BA-05 | Base/overlay/tombstones. `native graph suite` | Effective ordered and typed replacements | Seven directed snapshots including tentative INSERT and rollback | 0, PASS | `direct-graph.log` | Observable snapshots |
| V-BA-06 | Independent graph reconstruction. `native graph suite with canonical edge dictionary` | All effective edges and vertex lists agree | 49 directed and 2,233 generated vertex comparisons, exact | 0, PASS | `direct-graph.log` | Bounded snapshots |
| V-BA-07 | Invalid and empty cases. `native graph suite and sanitizer graph` | Any invalid or isolated vertex gives empty output | Three invalid IDs checked, including UINT32_MAX; isolated and removed adjacency verified | 0, PASS | `direct-graph.log; sanitizer-graph.log` | — |
| V-BA-08 | High-degree retrieval. `native graph suite` | Full ordered high-degree output | 65,536 neighbor IDs without truncation, repeated exact output | 0, PASS | `direct-graph.log` | Bounded high-degree star |
| V-BA-09 | Direct capacity growth and reuse. `native graph suite` | Large fill grows, repeated/smaller/empty calls retain | Observed 3 → 98,304 initial growth and retained nonempty capacity | 0, PASS | `direct-graph.log` | Capacity observations |
| V-BA-10 | Recursion theorem. `review source call sites; native --test-p4b-neighbor pool and trace` | Check F=q-2+k+1 and depth guards | Root plus both descents, arithmetic guards and all observed depths match | 0, PASS | `recursive-calls.log; direct-pool.log; trace.log` | Proof gives upper bound |
| V-BA-11 | Root and K2. `native trace suite` | Root depth 0, two assignments, no K2 retrieval | K2 1 theoretical frame, 2 anchor visits, 0 fills | 0, PASS | `trace.log` | — |
| V-BA-12 | PRESENT child ownership. `native trace suite` | Child advances depth and assignment by one, distinct slot | All transitions checked, Diamond live growth witness | 0, PASS | `trace.log; normalized-traces.txt` | Bounded |
| V-BA-13 | Deferred child ownership. `native trace suite` | Same assignment, depth and missing both increase | C4 actual distinct depth-1 slot and retained parent | 0, PASS | `trace.log; normalized-traces.txt` | Bounded |
| V-BA-14 | Parent preservation during growth. `native trace suite` | Parent storage and contents preserved after child growth | Diamond and C4 witnesses passed, all live parent returns checked | 0, PASS | `trace.log` | — |
| V-BA-15 | Sibling capacity reuse. `native trace suite` | Nonempty repeated same-depth fill with no growth | 46 Diamond siblings reused; sample depth 1 size/capacity 4 | 0, PASS | `trace.log` | — |
| V-BA-16 | Cross-anchor reuse. `native trace suite` | Same pool, fresh retrieval per anchor and fresh pool next update | 27 Diamond cross-anchor reuses; inverse C4 starts from empty capacity | 0, PASS | `trace.log` | — |
| V-BA-17 | Exact traversal traces. `native trace suite with APX_NEIGHBOR_TRACE_FILE` | Identical normalized search decisions and raw emissions | Eight cases, 8,541 rows per mode equal | 0, PASS | `trace.log; normalized-traces.txt` | — |
| V-BA-18 | Exact legacy counter parity. `capture_correctness.py, paired by raw input SHA-256` | Whole output equal apart from additive buffer fields | 921/921 pairs, no differences including all step and cumulative counters | 0, PASS | `canonical-exact-parity.json; canonical-replays.jsonl.gz` | — |
| V-BA-19 | Logical work-budget parity. `test_work_budget_and_exposed_order and C4-budget trace` | Exact success/abort and counters at multiple caps | Five caps checked; actual depth-2 abort at cap 9 | 0, PASS | `full-regression.log; trace.log` | Mode-specific allocation failure classified separately |
| V-BA-20 | Existing search fault rollback. `test_search_and_graph_fault_recovery` | Restore state then exact oracle-matched retry | 13 INSERT/DELETE combinations passed | 0, PASS | `full-regression.log; focused-dev.log` | Forward/reverse hook is INSERT only |
| V-BA-21 | Buffer-specific failures. `native trace suite with injected boundary failure` | Abort pre-commit and classify depth guard | 8 injected bad_alloc boundaries and 2 actual guard failures via callback | 0, PASS | `trace.log; sanitizer-trace.log` | Injected boundary failures, no allocator fault asserted |
| V-BA-22 | Search-local cleanup and recovery. `native trace suite and codec checkpoint test` | Discard pool, restore graph/codec/sink, fresh retry | All recoverable retries succeed; poison blocks unsafe continuation | 0, PASS | `trace.log; full-regression.log` | — |
| V-BA-23 | Configuration lifecycle. `test_cli_matrix and test_configuration_metadata_matrix` | Default, explicit mode, reject A+B/M1/all and wrong metadata | Matrix, early failure and historical parsing verified | 0, PASS | `full-regression.log; focused-dev.log` | — |
| V-BA-24 | Actual wrong-mode substitution. `test_actual_mode_substitution_and_negative_identity` | Reject both directions on success and initialized failure | 4 actual subprocess substitutions and native outcome preserved | 0, PASS | `full-regression.log; focused-dev.log` | — |
| V-BA-25 | Typed and Unicode. `FAR original witnesses and relabel tests` | Exact typed identity and tuples | BUFFER included; Unicode witnesses 0/0 and 1/1 as expected | 0, PASS | `full-regression.log; far-evidence/` | — |
| V-BA-26 | FAR postcommit/fail-fast. `FAR fault matrix and original malformed suffix` | Committed outcome persists, fatal delivery stops, preserve first failure | Four report boundaries each operation, plus raw suffix witness | 0, PASS | `full-regression.log; far-evidence/` | — |
| V-BA-27 | Generated graph reconstruction. `native graph suite, seeds 1,7,42` | Independent comparison on committed/restored states | 216 attempts, 154 committed, 62 aborted, 319 snapshots, 0 discrepancies | 0, PASS | `direct-graph.log` | — |
| V-BA-28 | Matching PBT. `test_06_property_based_differential` | Original 78/780 workloads and oracle, plus buffer exact counters | All modes agreed. The pool recorded 13,880 completed fills and 1,484 fills with growth. There were 27 event-bearing streams, 51 zero-event streams and 1,196 oracle events. Residual recorded 13,185 checks and 4,154 prunes. | 0, PASS | `full-regression.log; canonical-replays.jsonl.gz` | — |
| V-BA-29 | Frozen M1 and NONE. `frozen_compare.py` | Before/after exact output and exits | 31/31 M1 and 87/87 Bare M2 | 0, PASS | `frozen-summary.json; frozen-raw.jsonl.gz` | Bounded frozen inventory |
| V-BA-30 | Frozen STATIC. `frozen_compare.py` | Legacy Static preserves every prior counter | 87/87 exact comparisons | 0, PASS | `frozen-summary.json; frozen-raw.jsonl.gz` | Bounded frozen inventory |
| V-BA-31 | Frozen RESIDUAL. `frozen_compare.py` | Legacy Residual preserves every prior counter | 87/87 exact comparisons | 0, PASS | `frozen-summary.json; frozen-raw.jsonl.gz` | Bounded frozen inventory |
| V-BA-32 | Canonical controls. `runner --engine all --nlf-mode none and --engine m2 static/residual, --stage all` | 25/840/56/672 in each mode | All M1, Bare M2, Static and Residual passed | 0, PASS | `canonical-legacy.json; canonical-static.json; canonical-residual.json` | — |
| V-BA-33 | Canonical BUFFER. `runner --engine m2 --nlf-mode none --neighbor-mode buffer --stage all` | 25/840/56/672, effective BUFFER | Full inventory, DIAGNOSTIC_SUBSET_PASS | 0, PASS | `canonical-buffer.json; canonical-replays.jsonl.gz` | — |
| V-BA-34 | Residual native reconstruction. `native --test-p4b-residual numeric and trace` | Preserve actual residual commitments and restorations | 55,142 DFS entries and 0 reconstruction discrepancies | 0, PASS | `residual-numeric.log; residual-trace.log` | — |
| V-BA-35 | Sanitizers. `sanitized native --test-p4b-neighbor pool/graph/trace` | Targeted lifetime/UB checks | All three exited 0. Pool leak checks on. Integrated checks with leaks off. | 0, PARTIAL | `sanitizer-*.log; instrumented-flags.log` | Known upstream leak and unit-test dispatch before CLI address cap |
| V-BA-36 | Full regression. `python3 -m unittest discover -s tests -v` | All discovered tests accounted | 791 discovered, 791 run, 791 passed, 0 skipped, 0 failures, 0 errors, exit 0, unittest elapsed 301.978 seconds. No skip reasons. | 0, PASS | `full-regression.log; full-regression.time; discovered.log` | — |
| V-BA-37 | Historical preservation. `verify_preservation.py and exact before/after diff` | Seven artifact sizes/hashes and historical binary unchanged | Confirmed at both boundaries, pristine upstream | 0, PASS | `history-before.log; history-after.log; final-upstream.log` | Historical P4a |
| V-BA-38 | Final local and remote state. `approved push script; git diff/status; gh pr and issue view` | Exact matching SHAs, correct branch lifecycle | Recorded publish and exact match, prior protected heads preserved | 0, PASS | `final-head.txt; pr-final.json; issue-final.json; remote-final.log; final-diff.log` | Docs-only report child |

## Acceptance matrix

| Gate | Actual requirement | Evidence | Status | Limitation |
|---|---|---|---|---|
| BA-A01 | Approved starting revision | V-BA-01 | PASS | — |
| BA-A02 | Issue #38 authorization | V-BA-01 | PASS | — |
| BA-A03 | Existing branch and Draft PR | V-BA-01, V-BA-38 | PASS | — |
| BA-A04 | Upstream unchanged | V-BA-37 | PASS | — |
| BA-A05 | No sorted merge or empty-overlay performance fast path | V-BA-03, V-BA-05 | PASS | Reviewed graph source |
| BA-A06 | No A+B | V-BA-23 | PASS | — |
| BA-A07 | No benchmark or NetFlow execution | V-BA-28, V-BA-32, V-BA-33, V-BA-36 | PASS | Command ledger |
| BA-A08 | No unrelated redesign | V-BA-03, V-BA-10, V-BA-38 | PASS | Reviewed source diff |
| BA-B01 | Caller-owned API | V-BA-03, V-BA-04 | PASS | — |
| BA-B02 | Clear previous contents | V-BA-04 | PASS | — |
| BA-B03 | Invalid and isolated-vertex semantics | V-BA-07 | PASS | — |
| BA-B04 | Exact base CSR collection | V-BA-05, V-BA-06 | PASS | — |
| BA-B05 | Exact overlay collection | V-BA-05, V-BA-06 | PASS | — |
| BA-B06 | Tombstone and replacement preservation | V-BA-05, V-BA-06 | PASS | — |
| BA-B07 | Sorted unique output | V-BA-04, V-BA-06, V-BA-08 | PASS | — |
| BA-B08 | Independent reference | V-BA-06, V-BA-27 | PASS | — |
| BA-B09 | Pointer/count handling | V-BA-03, V-BA-07, V-BA-35 | PASS | — |
| BA-B10 | Direct nonempty capacity reuse | V-BA-08, V-BA-09 | PASS | — |
| BA-C01 | Search-local ownership | V-BA-10, V-BA-16, V-BA-22 | PASS | — |
| BA-C02 | Depth theorem and call sites | V-BA-10, V-BA-12, V-BA-13 | PASS | — |
| BA-C03 | Checked frame arithmetic | V-BA-10 | PASS | — |
| BA-C04 | Fixed outer vector | V-BA-10, V-BA-14 | PASS | — |
| BA-C05 | Root depth | V-BA-11 | PASS | — |
| BA-C06 | PRESENT child depth | V-BA-12 | PASS | — |
| BA-C07 | Deferred child depth | V-BA-13 | PASS | — |
| BA-C08 | Parent/child storage isolation | V-BA-12, V-BA-13, V-BA-14 | PASS | — |
| BA-C09 | Parent survives child growth | V-BA-14 | PASS | — |
| BA-C10 | Actual sibling reuse | V-BA-15 | PASS | — |
| BA-C11 | Fresh retrieval across anchors | V-BA-16 | PASS | — |
| BA-C12 | Abort lifetime | V-BA-21, V-BA-22, V-BA-35 | PASS | Injected resource boundaries |
| BA-D01 | LEGACY stays default | V-BA-23, V-BA-29, V-BA-32 | PASS | — |
| BA-D02 | Explicit BUFFER | V-BA-23, V-BA-33 | PASS | — |
| BA-D03 | M1 BUFFER rejected | V-BA-23 | PASS | — |
| BA-D04 | A+B rejected | V-BA-23 | PASS | — |
| BA-D05 | all BUFFER rejected | V-BA-23 | PASS | — |
| BA-D06 | Requested/effective identity validated | V-BA-23, V-BA-24 | PASS | — |
| BA-D07 | Actual wrong-mode substitution | V-BA-24 | PASS | — |
| BA-D08 | Initialized negative identity | V-BA-23, V-BA-24 | PASS | — |
| BA-D09 | Capacity diagnostics | V-BA-09, V-BA-15, V-BA-17, V-BA-18 | PASS | Counts of completed fills and capacity growth |
| BA-D10 | Disabled mode no allocations or operations | V-BA-10, V-BA-17, V-BA-29, V-BA-30, V-BA-31 | PASS | Disengaged pool and zero diagnostics |
| BA-E01 | Exact ordered neighbor equality | V-BA-04, V-BA-05, V-BA-06, V-BA-27 | PASS | — |
| BA-E02 | Native traversal trace equality | V-BA-17 | PASS | — |
| BA-E03 | Exact legacy counter parity | V-BA-18, V-BA-28 | PASS | — |
| BA-E04 | Comparable work-budget parity | V-BA-19 | PASS | — |
| BA-E05 | Native search fault rollback | V-BA-20 | PASS | — |
| BA-E06 | Buffer-specific failure and recovery | V-BA-21, V-BA-22 | PASS | Injected, no allocator claim |
| BA-E07 | Internal depth guard containment | V-BA-10, V-BA-21, V-BA-22 | PASS | Guard exercised on one-slot test pool inside actual callback |
| BA-E08 | Unicode and typed labels | V-BA-25 | PASS | — |
| BA-E09 | FAR postcommit/fail-fast | V-BA-26 | PASS | — |
| BA-E10 | Property-based tuple parity | V-BA-28 | PASS | — |
| BA-E11 | Full canonical inventory | V-BA-32, V-BA-33 | PASS | — |
| BA-E12 | Frozen controls preserved | V-BA-29, V-BA-30, V-BA-31 | PASS | Bounded frozen inventory |
| BA-F01 | Full regression accounted | V-BA-36 | PASS | — |
| BA-F02 | Targeted sanitizers and limits recorded | V-BA-35 | PASS | LeakSanitizer only on standalone pool |
| BA-F03 | Cost and memory model | V-BA-09, V-BA-10, V-BA-14 | PASS | Logical vector elements and capacities |
| BA-F04 | Seven artifacts and binary preserved | V-BA-37 | PASS | — |
| BA-F05 | Exact tested source and binary | V-BA-02, V-BA-32, V-BA-33, V-BA-38 | PASS | — |
| BA-F06 | Report and evidence package | V-BA-17, V-BA-18, V-BA-36, V-BA-38 | PASS | — |
| BA-F07 | Clean diff and remote sync | V-BA-38 | PASS | — |
| BA-F08 | Issue OPEN, PR DRAFT, stop at review gate | V-BA-38 | PASS | — |

Handoff stops at the Independent Implementation Review Gate on Draft PR #39.
