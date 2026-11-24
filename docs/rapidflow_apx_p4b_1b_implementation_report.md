# RapidFlow-APX P4b-1B implementation and evidence

**Proposed handoff** `P4B_1B_READY_FOR_INDEPENDENT_REVIEW`. The implementation agent executed the checks below. Independent acceptance belongs to the reviewer.

| Attribute | Result |
|---|---|
| Starting HEAD and starting PR head | `a4d8aba28850a0e70552cd79e833ac1e9f2061b4` |
| Tested source | `8ab7324b90fcfef8c7aba735e06c1163d684f2b7` |
| Final HEAD and remote PR HEAD | The documentation-only child that introduces this report. The exact matching SHAs are recorded after push in `final-head.txt` and `pr-final.json` in the evidence archive. |
| Preserved reference native binary SHA-256 | `58380790badce7499989a224d276bf5ce914e5cca0a18681f18379a3fec6381e` |
| Clean release native binary SHA-256 | `dbaa10b9085aedbcd2f23e883a379a282309f8ce55399e6ba1f3e424c99e4c16` |
| Trace native binary SHA-256 | `34cd53bac8489c0ebaa97a7e7a81d1f0193e42f5008e920434ba1711580bc913` |
| Sanitizer native binary SHA-256 | `df28c7c43521bb674e99cbfeaf73b343c333c8790daedc5cad23cdbd37f84edc` |
| Production changes | Search-local compact state, M2 search, existing streaming/index setup, residual invariant classification, counters and build wiring |
| Verification changes | Native CLI and trace tests, strict Python runner, seeded differential and foundation audit regressions, byte-preserved audit fixtures |
| Canonical correctness | 25 hand cases, 840 corpus cases, 56 registered streams and 672 steps each in M1, Bare M2, Static and Residual |
| Frozen controls | 87/87 before/after workloads for each of NONE and STATIC in M2. 31/31 targeted M1 workloads. |
| Full repository regression | 782 discovered, 782 run, 782 passed, 0 skipped, 0 failures, 0 errors. Runner 305.032 s, exit 0. No skip reasons. |
| Sanitizer scope | ASan and UBSan passed focused numeric and DFS trace with integration leak detection disabled. Isolated residual lifecycle passed with leak detection enabled. The integrated leak-enabled numeric run reported the existing upstream allocation leak. |

Worktree `/home/iamqthanq/Desktop/Seminar_RapidFlow_D_INT04B`, feature `feature/rf38-p4b-optimization`. Issue §7 explicitly authorized P4b-1B. The repository was clean and the initially OPEN/DRAFT PR targeted `QuangThang`. [Issue #38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38) · [Draft PR #39](https://github.com/iamqthanq/Seminar_RapidFlow/pull/39)

## Mathematical correspondence and ownership

```text
Immutable ApxQuerySignatures ── canonical edge endpoint IDs ──┐
                                                              v
                                      ApxResidualMissingState (one search)
                                              ^               |
                                         DFS status         r(u,s)
                                                              v
Authoritative snapshot ── existing transactional ApxNlfIndex ──> candidate bound
```

`ApxResidualMissingState` builds offsets by canonical query vertex and uses the sorted positive requirement list to resolve each canonical edge's two slots. The slots are distinct even when both endpoint signatures share a global ID. Maximum vector dimensions are checked before allocation and before computing an offset sum. Registry and endpoint identities are checked at setup. Commitment and undo validate both slots and both counts before either write. The compact state exists only inside a RESIDUAL `search_event`. A search abort unwinds the entire invocation and discards the state. Its allocation failure follows the established callback transaction rollback path. Detected residual invariants instead roll back, poison the session and stop the residual harness. [Residual state](../extensions/rapidflow_apx/src/residual_nlf_state.cpp) · [M2 implementation](../extensions/rapidflow_apx/src/m2_event_engine.cpp) · [Transaction catch](../extensions/rapidflow_apx/src/transaction_manager.cpp)

At every DFS entry, the by-value `missing_count` counts the edges whose status is MISSING. Each such edge contributes once at its two distinct endpoint rows, including an unmapped endpoint. For candidate $u\mapsto v$ and a completion $f$ respecting the commitments, each still-required edge of signature $\sigma$ at $u$ would consume a distinct compatible neighbor of $v$ if present, by injectivity. At most $N_G(v,\sigma)$ such edges can be present. Therefore the per-signature deficit after deducting $r(u,\sigma)$ lower-bounds additional misses of that signature. Buckets at one vertex are disjoint, and all exclude committed misses. The summed deficit plus the number already committed is a lower bound on $|R_G(f)|$. The actual code computes guarded `required - committed` and decrements the remaining budget by each deficit only after comparing it. It reads full authoritative availability. The data-side index sees the same INSERT-after and DELETE-before snapshots as search.

For a fixed candidate and commitment state, $\max(0,N_Q-N_G)\le r+\max(0,N_Q-r-N_G)$. Summing over just that candidate's signatures gives $\delta_{static}\le m_{cur}+\delta_{res}$. When $k=0$, both filters see zero committed misses at candidate evaluation and apply the same bound. The code runs residual at the existing preassignment seam and uses one check per eligible endpoint at anchoring. The query contract, canonical frontier, both anchor orientations, typed labels, injectivity checks and exact-$k$ leaf remain governed by the accepted M2 path. [Contract](rapidflow_apx_contract.md) · [Design §5 and §8](rapidflow_apx_p4b_design.md)

| M2 status site | Transition | Global count | Query-side effect and undo |
|---|---|---|---|
| Anchor entry and cleanup | UNKNOWN → PRESENT → UNKNOWN | Starts at zero | No residual changes. Each completed anchor restores its zero state. |
| Candidate frontier | UNKNOWN → PRESENT → UNKNOWN | Unchanged | Established tentative mapping and existing reverse undo. |
| Two-endpoint resolution, already committed | MISSING or PRESENT → same | Unchanged | Checks graph consistency; deferred MISSING is counted once. |
| Two-endpoint resolution, compatible | UNKNOWN → PRESENT | Unchanged | Existing status undo; no residual cell changes. |
| Two-endpoint resolution, absent or wrong label | UNKNOWN → MISSING | +1 | Both endpoint cells increment after the status undo entry is stored. |
| Candidate unwind | MISSING or PRESENT → UNKNOWN | Restore caller count | Reverse status undo restores both cells for each MISSING write before resetting mapping and used set. |
| Deferred branch and unwind | UNKNOWN → MISSING → UNKNOWN | Child gets +1 | Both cells increment before any child, including the unmapped endpoint. Undo both on normal return. The over-budget transient is accounted for but never passed into a candidate filter. The existing fault hook still observes its original boundary. |

On normal completion, missing operations balance. On an exceptional exit a suffix of the search-local mutations may be discarded by destruction. No artificial undo is reported. INSERT rollback of the persistent index, codec, graph and staged tuples remains coordinated by the existing transaction scope. [DFS transition test](../tests/native/rapidflow_apx_engines/src/residual_tests.cpp)

## Actual witnesses

The paired audit fixtures are byte-for-byte copies. Canonical C4 edge IDs 0–3 are $01,02,13,23$. Both traces below were observed at actual DFS candidate filtering in both INSERT $(0,1,0)$ and its inverse DELETE, with anchor 0 in the forward orientation.

| Quantity | Strict, initial edge 13 | Deferred, initial edges 13 and 23 |
|---|---|---|
| Partial mapping in canonical order | `[0,1,_,_]` | `[0,1,_,3]` |
| Committed MISSING | Edge ID 1 ($02$) | Edge ID 1 ($02$), endpoint 2 unmapped |
| Candidate | $q_3\mapsto g_3$ | $q_2\mapsto g_2$ |
| $k$, $m_{cur}$, remaining budget | 1, 1, 0 | 1, 1, 0 |
| Signature $(\ell_E,\ell_V)$ | $(0,0)$ | $(0,0)$ |
| Required, committed local, available | 2, 0, 1 | 2, 1, 1 |
| Static and residual deficits | 1 and 1 | 1 and 0 |
| Static and residual decisions | KEEP / **PRUNE** | KEEP / **KEEP** |
| INSERT ENTERED and inverse DELETE EXITED | Both empty | Both are the eight tuples below |

```text
[[0, 1, 2, 3], [0, 2, 1, 3], [1, 0, 3, 2], [1, 3, 0, 2], [2, 0, 3, 1], [2, 3, 0, 1], [3, 1, 2, 0], [3, 2, 1, 0]]
```

The separate exhaustive Python oracle checks typed labels, matched connectivity and exact update deltas by permutations. Both it and M0 agreed with M1, Bare M2, Static and Residual on the two witnesses and both directions. The A1 Diamond-to-C4 witness produced 8/8 with the identity tuple and chord insertion produced 16/16. The A2 Diamond local arithmetic was tested against the production helper, with its reachable double-counting counterpart verified in deferred C4. The numerical Diamond helper case by itself makes no native reachability claim. For A3 the helper kept the full compatible neighbor capacity, and the independent trace replay verified actual candidate decisions against authoritative counts. A5 exercised Unicode and integer/string label replacement with nonempty event sets. A6 compared every registered $k=0$ stream (24 streams, 288 steps) including legacy step and cumulative traversal counters and decisions as reflected in Static and Residual check/prune counts. A7 covered checked count, slot and budget boundaries. [Native tests](../tests/native/rapidflow_apx_engines/src/residual_tests.cpp) · [Exact tuple and CLI regressions](../tests/test_rapidflow_apx_residual_nlf.py)

### Independent reconstruction in the trace build

The observer reconstructs slots from actual edge status, canonical endpoints, labels and opposite labels, independently of residual slot mutation. It checks global missing count, incidence conservation, every requirement slot, injective mapping and used-vertex flags. Stacked parent snapshots check actual candidate/deferred unwind and sibling restoration. Reconstruction runs at anchor start/cleanup, DFS entry, candidate checks, both MISSING transitions, after incident resolution, child return and after both unwind paths. The hook is compiled out of the release build.

| Quantity | Observed |
|---|---:|
| Generated two-update K5 streams across seeds 1, 7, 42 plus two C4 trace witnesses | 36 + 2 |
| DFS entries reconstructed | 55,142 |
| Incident MISSING commitments reconstructed | 27,732 |
| Deferred MISSING commitments reconstructed | 42,118 |
| Candidate/deferred restorations checked | 114,190 |
| Anchor restorations checked | 1,008 |
| Incident resolution with multiple new MISSING commitments | 4,246 |
| Candidate rejections after at least one new MISSING commitment | 8,616 |
| DFS entries with nested MISSING count at least two | 35,758 |
| Out-of-budget transient observations | 39,000 |
| Compatible C4 completions independently enumerated for bound checks | 48 |
| Residual-state discrepancies | 0 |

## Cost model and diagnostics

For $q$ query vertices, $m$ query edges and $R$ positive vertex/signature requirements, $R\le2m$. On the recorded x86-64 build the logical vector elements occupy $8(q+1)$ bytes of offsets, $4R$ of missing counts, $4R$ of capacities and $16m$ of endpoint slot pairs. The allocator, vector objects, existing DFS undo and other engine memory are outside this accounting. The strict/deferred C4 has $q=m=R=4$, or 136 bytes of logical residual vector elements. Search construction costs $O(q+R+m\log(1+\max_u s(u)))$ with sorted requirement searches, where $s(u)$ is the number of requirements on $u$. Post-construction MISSING commitment and undo touch two cells in constant time. One filter evaluation is $O(s(u))$ with possible budget early exit or skipped zero demand. There is no allocation in the filter. The persistent data-side index remains one $O(|V_G||\Sigma_Q|)$ table shared by the existing enabled-mode architecture with the existing 256 MiB default admission budget. A separate residual allocation can fail the current precommit step. No midstream downgrade is authorized. The worst-case combinatorial search class is unchanged.

- `nlf_residual_checks` and `nlf_residual_pruned` count actual evaluations and rejection decisions, including anchors.
- `nlf_residual_anchor_checks` and `nlf_residual_anchor_pruned` are the anchor subsets. A first-endpoint prune short-circuits the second check.
- `nlf_residual_signature_lookups` counts actual data-index reads. Zero residual demand skips a read. Generic `nlf_signature_lookups` and `nlf_anchor_*` also include the corresponding residual operations.
- `nlf_residual_missing_commits` and `nlf_residual_missing_undos` count actual operations, each touching two cells, including out-of-budget transients. They are cumulative and can differ after an abort. Neither is the current branch's global missing count.
- STATIC-specific counters remain STATIC-only. NONE and STATIC emit zero residual counters and allocate no residual state. Legacy work charging is unchanged. The runner validates mode-specific diagnostics and reconciles step totals without imposing fields retroactively on historical output.

## Correctness, fault and reproduction evidence

| Actual verification | M1 / NONE | M2 / NONE | M2 / STATIC | M2 / RESIDUAL |
|---|---:|---:|---:|---:|
| Required hand cases, including domain negatives | 25/25 | 25/25 | 25/25 | 25/25 |
| Corpus exact tuple sets and zero duplicates | 840/840 | 840/840 | 840/840 | 840/840 |
| Registered streams | 56/56 | 56/56 | 56/56 | 56/56 |
| Stream exact tuples, graph, codec, counters and active set | 672/672 | 672/672 | 672/672 | 672/672 |

The clean combined NONE invocation reported `P3_ENGINES_VERIFIED`. Single-mode Static and Residual invocations reported the runner's `DIAGNOSTIC_SUBSET_PASS` for their selected mode across the full registered inventory. M0 generated 8,002 corpus truth events across 330 event-bearing cases, 239 at $k\ge1$. Streams agreed on 3,024 ENTERED and 1,780 EXITED. The clean release reproduced all NLF modes with the same SHA-256. P1 feasibility passed 6/6 and P2 foundation passed 8/8.

The unchanged PBT workloads included 78 streams and 780 updates at seeds 1, 7 and 42. All three M2 modes matched M0 per step and passed graph, codec, exact schema and duplicate checks. The K2, Triangle, C4, Diamond, K4 and triangular prism workloads covered valid $k$ up to 2 and the original uniform and mixed typed regimes. There were 27 event-bearing streams, 51 zero-event streams, 1,196 truth events, 13,185 residual checks and 4,154 residual prunes. Supplemental fixtures covered raw/escaped Unicode, malformed Unicode and typed edge-label changes. The existing native Static suite preserved 6 reconstruction streams, 120 committed updates, 12 aborted updates and 144 complete index reconstructions with no mismatch.

| Failure and identity check | Actual evidence |
|---|---|
| Search fault and NLF transaction boundaries | 14 actual INSERT/DELETE combinations across AFTER_VERTEX_ASSIGNMENT, AFTER_MISSING_COMMITMENT, AFTER_NLF_PREPARE, AFTER_GRAPH_BEFORE_NLF, AFTER_FIRST_NLF_CELL, DURING_CALLBACK and BEFORE_PUBLISH. They rolled back graph and data index, published no tuples and recovered on the next equivalent update. |
| Codec checkpoint for a newly interned label | Three further INSERT fault paths rolled back a new label, completed a valid matching search, then accepted the label on a later successful INSERT. |
| Genuine logical work cap | INSERT and DELETE exceeded cap 9 at 10 work units, reported WORK_BUDGET_EXCEEDED, rolled back and allowed an irrelevant-label update at the same cap. |
| Residual invariant | Test-only observer injection produced INVARIANT_VIOLATION, restored index and graph, and prevented another commit on the poisoned session. |
| FAR reporting faults | Four reporting points after commit on both INSERT and DELETE preserved COMMITTED. The next update remained UNATTEMPTED with continue-on-error. Full-cell reconstruction matched the committed graph. |
| Configuration identity | Shared validator tested positive and initialized-negative metadata. Six real substituted subprocesses exercised all three directed mode mismatches with positive and failing updates. M1 and all reject optimization selection. |
| Real fallback | A 30,000-vertex workload exceeded 1 MiB logical index admission. Requested RESIDUAL reported effective NONE, zero logical index bytes and zero residual counters. The runner excluded it from residual certification. |
| Frozen bounds | Starting and final binaries received identical input bytes and argv limits. Full JSON comparison excludes only new residual diagnostic fields. Frozen NONE and STATIC each covered all 25 hand and 56 streams, two Unicode and two C4 audit witnesses, and two work caps. M1 covered the hand and supplemental cases. All agreed. Cap 1,000 completed and cap 9 reported the same work-budget exit and state on both revisions in all three configurations. |

The native toolchain was Ubuntu GNU C++ 13.3.0 with CMake 3.28.3, C++17, `-O3 -Wall -Wextra` and `-fPIC` on the foundation library. The trace and sanitized builds used separate build directories. The sanitizer build added `-fsanitize=address,undefined -fno-omit-frame-pointer -g -fno-pie` and linked with `-fsanitize=address,undefined -no-pie`. ASan/UBSan covered direct numeric tests and actual native DFS reconstruction and residual invariant rollback. Leak detection was enabled and passed on a standalone 1,000-lifetime residual state check that did not load the upstream graph. Integrated numeric with leak detection enabled failed with 208 bytes across 8 allocations in upstream `Graph::buildEdgeIndex`. Integrated numeric and trace replays passed with leak detection disabled. Sanitizer scope is targeted and does not certify whole-system leak freedom or the production CLI address-space path.

The evidence directory is `/tmp/rapidflow-p4b1b-m4IOqJ/`. The final archive records commands, exits, elapsed time and peak RSS, raw inputs/outputs, hashes, trace, summary JSON, test logs and preservation checks. Per-case raw captures from a second correctness execution use the same clean release binary and are reproducibility evidence. Runtime/RSS in this report describe test execution only. They are not a comparative benchmark.

## Preserved history and remaining research limits

The seven entries in the historical manifest were checked before and after by byte size and SHA-256. The historical raw ledger remains 441,311 bytes at `34452fb4eeccd812882a5c2c47cca0c49b070bf8b48e5ea86d8ac68c8cc521d5`. The historical P4a binary remains at `6e4537d03a356dd9c603e452f86848cbdb1ea2cd0fcbdf31199d0e9afbf9b21b`. The shared upstream remained pristine at `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`. Frozen baseline parity in this phase has the bounded scope above. Prior historical partial work-budget evidence retains its original status. [P4a limitations](rapidflow_apx_p4a_audit_addendum.md) · [P4a manifest](evidence/p4a_evidence_manifest.json)

Residual NLF performance advantage has not been established. Option B has not been implemented. Combined A+B has not been evaluated. NetFlow scalability has not been established. R4b optimization selection has not been performed. P4b-3 preregistration remains mandatory for AUD-P4B-001 and AUD-P4B-002, including exact frozen fixture bytes and generator, execution order, PROFILE_OFF timing with diagnostic exclusion, workload-level aggregation and bootstrap, censoring, memory eligibility, decision precedence and the conditional A+B gate.


## Verification register

The tested source for native execution rows is `8ab7324b90fcfef8c7aba735e06c1163d684f2b7`. Non-execution checks use the revision described in their row. Expected-failure workload exits are retained in the raw records and are distinct from the successful verification command. The executed external command log expands the abbreviated methods below.

| ID | Verification and command or method | Expected | Actual | Exit and status | Evidence | Limitation |
|---|---|---|---|---|---|---|
| V-RES-01 | Repository preflight. `git status / rev-parse / worktree list; gh issue view 38 and gh pr view 39` | Clean approved local HEAD, authorized Issue, matching draft PR | Verified approval §7 and matching a4d8aba | Various, recorded per check, PASS | `pr-before.json, issue-38-before.log, tested-status.txt` | Starting revision a4d8aba |
| V-RES-02 | Canonical signature slot mapping. `Native numeric CLI and trace reconstruction` | Both canonical endpoints map to distinct correct typed slots | Equal/asymmetric identities, shared requirements, reversed input edge order and invalid registry checked | 0, PASS | `trace-numeric.log, trace-final.log` | — |
| V-RES-03 | Residual arithmetic. `Native numeric CLI` | Full budget/requirement boundaries, no wrap | Zero demand skips reads; equality and one-over tests; uint32 maximum and invalid counts | 0, PASS | `trace-numeric.log` | — |
| V-RES-04 | Commit and undo. `Native numeric CLI` | Checked two-cell commit/undo | Second-endpoint rejection left both counts unchanged; reverse undo restored zero | 0, PASS | `trace-numeric.log` | — |
| V-RES-05 | Deferred MISSING. `Native trace CLI` | Both incident requirements updated while endpoint unmapped | Deferred C4 candidate q2 uses already-committed count 1; 42,118 traced deferred commits | 0, PASS | `trace-final.log` | — |
| V-RES-06 | Incident resolution rollback. `Native trace CLI` | Multiple incident commits roll back exactly | 4,246 multi-MISSING resolutions and 8,616 rejects after changes, with restoration checks | 0, PASS | `trace-final.log` | — |
| V-RES-07 | Sibling and anchor isolation. `Native trace CLI` | State matches parent at sibling and anchor boundaries | 114,190 candidate/deferred restorations and 1,008 anchor cleanups | 0, PASS | `trace-final.log` | — |
| V-RES-08 | Independent DFS reconstruction. `Native trace CLI; reference scan by edge labels, endpoints and status` | Per-slot and global state match reference | 55,142 DFS entries, 0 discrepancies; 48 compatible completions checked | 0, PASS | `trace-final.log` | Bounded C4 and seeded K5 workloads |
| V-RES-09 | A1 original. `test_exact_tuples_for_a1_chord_and_audit_witnesses` | Diamond to C4 yields 8 ENTERED and 8 EXITED | Exact tuples agreed with both oracles and all four native configurations | 0, PASS | `residual-focused.log, residual-focused-raw.jsonl.gz` | — |
| V-RES-10 | Chord insertion. `test_exact_tuples_for_a1_chord_and_audit_witnesses` | Chord yields 16 ENTERED and 16 EXITED | Exact tuples agreed with both oracles and all four native configurations | 0, PASS | `residual-focused.log, residual-focused-raw.jsonl.gz` | — |
| V-RES-11 | A2 double-counting. `Native numeric and deferred trace` | A2 helper keeps at exact budget; actual deferred state avoids double count | Original local 3/2/1 helper kept, reachable C4 retained | 0, PASS | `trace-numeric.log, trace-final.log` | Original Diamond state tested at helper level |
| V-RES-12 | A3 used-neighbor trap. `Native numeric helper and observer's authoritative counts` | Full neighbor capacity, including used neighbors | Independent recomputation agreed with every traced candidate decision | 0, PASS | `trace-numeric.log, trace-final.log` | — |
| V-RES-13 | Strict reachable witness. `Native trace and paired audit fixture` | STATIC keep, RESIDUAL prune | Observed both snapshots; tuples empty | 0, PASS | `trace-final.log, canonical-replays.jsonl.gz, residual-focused-raw.jsonl.gz` | — |
| V-RES-14 | Deferred reachable witness. `Native trace and paired audit fixture` | RESIDUAL keep valid partial state | Observed both snapshots; exact 8 ENTERED and 8 EXITED | 0, PASS | `trace-final.log, canonical-replays.jsonl.gz, residual-focused-raw.jsonl.gz` | — |
| V-RES-15 | k=0 equivalence. `test_exact_mode_legacy_traversal` | Static/Residual exact tuple, legacy work and decision count equality | 24 registered k=0 streams and 288 updates agreed | 0, PASS | `residual-focused.log, residual-focused-raw.jsonl.gz` | — |
| V-RES-16 | k=2 correctness. `Selected canonical streams and unchanged PBT` | K4 and prism event/graph/codec parity | M0 parity on all registered completed k=2 workloads | 0, PASS | `canonical-residual.json, full-regression.log` | — |
| V-RES-17 | Typed and Unicode labels. `FAR extended tests and A5 typed-label witness` | Original Unicode 0/0 and 1/1, typed edge/vertex parity | Exact oracles matched; invalid encodings fail closed; relabel churn matched | 0, PASS | `far-evidence/, residual-focused-raw.jsonl.gz` | — |
| V-RES-18 | Native configuration identity. `Native CLI and test_actual_native_substitution_rejected` | Reject wrong modes, invalid CLI selections, initialized negatives with mismatched identity | Six native mode substitutions rejected plus full metadata matrix | 0, PASS | `residual-focused.log, residual-focused-raw.jsonl.gz` | — |
| V-RES-19 | Actual fallback. `test_real_memory_fallback via native CLI at 1 MiB` | Requested RESIDUAL unavailable, fallback NONE excluded | Real index admission fallback, zero index bytes and residual diagnostics | 0, PASS | `residual-focused.log, residual-focused-raw.jsonl.gz` | — |
| V-RES-20 | Search abort and rollback. `test_residual_search_faults_and_recovery; native trace poison check` | Precommit integrity, recovery, fail closed for invariants | 14 combinations, 3 new-label checkpoint faults and poisoned-session control | 0, PASS | `residual-focused.log, trace-final.log` | — |
| V-RES-21 | Actual work-budget abort. `test_actual_residual_work_budget_abort via native CLI at 9 units` | Abort and roll back for INSERT and DELETE; later permitted operation succeeds | Both abort at 10 units and retain reconstructed state | 0, PASS | `residual-focused.log, residual-focused-raw.jsonl.gz` | — |
| V-RES-22 | Postcommit delivery. `test_precommit_and_delivery_fault_matrix, four reporting hooks` | COMMITTED, distinct delivery failure, suffix unattempted | 8 RESIDUAL cases, both operations, with full-cell checks | 0, PASS | `full-regression.log, far-evidence/` | — |
| V-RES-23 | Property-based differential. `unittest test_06_property_based_differential` | Preserved 78/780, compare exact M0 and three native M2 modes | 27 event streams, 1,196 truth events and zero discrepancies | 0, PASS | `full-regression.log, capture-correctness.log, canonical-replays.jsonl.gz` | — |
| V-RES-24 | Frozen NONE. `frozen_compare.py with starting and clean release binary` | Compare full output and capped behavior on identical bytes | 87/87 M2 and 31/31 targeted M1 | 0, PASS | `frozen-summary.json, frozen-raw.jsonl.gz` | No full frozen corpus claimed |
| V-RES-25 | Frozen STATIC. `frozen_compare.py with starting and clean release binary` | Compare full output including Static counters and capped behavior | 87/87 M2 Static | 0, PASS | `frozen-summary.json, frozen-raw.jsonl.gz` | No full frozen corpus claimed |
| V-RES-26 | Canonical M1 and Bare M2. `runner --clean-build --engine all --nlf-mode none --stage all` | Exact 25/840/56/672 per selection | P3_ENGINES_VERIFIED | 0, PASS | `canonical-none.json, canonical-replays.jsonl.gz` | — |
| V-RES-27 | Canonical Static. `runner --engine m2 --nlf-mode static --stage all` | Exact 25/840/56/672 as effective STATIC | DIAGNOSTIC_SUBSET_PASS on full inventory | 0, PASS | `canonical-static.json, canonical-replays.jsonl.gz` | Single-mode runner classification |
| V-RES-28 | Canonical Residual. `runner --engine m2 --nlf-mode residual --stage all` | Exact 25/840/56/672 as effective RESIDUAL | DIAGNOSTIC_SUBSET_PASS on full inventory | 0, PASS | `canonical-residual.json, canonical-replays.jsonl.gz` | Single-mode runner classification |
| V-RES-29 | Sanitizers. `ASan+UBSan numeric, trace and isolated helper` | No introduced sanitizer findings in targeted coverage | Numeric and trace exit 0 with leaks off, standalone helper exit 0 with leaks on; upstream leak on integrated leak-enabled run | 0 and 1 as stated, PARTIAL | `sanitizer-*.log, instrumented-flags.log` | Integrated leak-enabled run exit 1; 208 B in upstream buildEdgeIndex |
| V-RES-30 | Full repository regression. `python3 -m unittest discover -s tests -v` | Discovery and passed/skipped/failures/errors accounted | 782 discovered, 782 run, 782 passed, 0 skipped, 0 failures, 0 errors. Runner 305.032 s, exit 0. No skip reasons. | 0, PASS | `full-regression.log, full-regression.time, discovered.log` | — |
| V-RES-31 | Historical artifacts and binary. `verify_preservation.py; compare pre/post records and upstream git status` | 7/7 artifacts and historical binary byte/hash identical; upstream pinned and pristine | All verified on both boundaries | Various, recorded per check, PASS | `history-before.log, history-after.log, final-upstream.log` | — |
| V-RES-32 | Final repository and remote state. `Approved push script; git diff/status/HEAD; gh pr view and gh issue view` | Local/remote PR head match, clean tree, protected heads unchanged, OPEN/DRAFT | Synchronization rechecked at both pushes, exact handoff outputs in archive | Various, recorded per check, PASS | `final-head.txt, pr-final.json, issue-final.json, remote-final.log, final-diff.log` | Report commit is documentation-only |

## Acceptance matrix

| Gate | Actual requirement | Evidence reference | Status | Limitation |
|---|---|---|---|---|
| R-A01 | Approved starting revision | V-RES-01 | PASS | — |
| R-A02 | Issue #38 authorization verified | V-RES-01 | PASS | — |
| R-A03 | Existing branch and PR preserved | V-RES-01, V-RES-32 | PASS | — |
| R-A04 | Upstream pristine | V-RES-31 | PASS | — |
| R-A05 | No unrelated algorithm redesign | V-RES-02, V-RES-04, V-RES-32 | PASS | Reviewed source diff |
| R-A06 | No Option B implementation | V-RES-32 | PASS | Reviewed source diff |
| R-A07 | No benchmark or NetFlow execution | V-RES-26, V-RES-27, V-RES-28, V-RES-30 | PASS | Command ledger |
| R-A08 | No unauthorized future-phase work | V-RES-32 | PASS | Reviewed source diff and retained preregistration |
| R-B01 | D-R contract preserved | V-RES-09, V-RES-10, V-RES-26, V-RES-28 | PASS | Contract and unchanged leaf/domain checks |
| R-B02 | Residual theorem correctly implemented | V-RES-03, V-RES-08, V-RES-13, V-RES-14 | PASS | Proof and source correspondence |
| R-B03 | Global committed missing count correct | V-RES-05, V-RES-06, V-RES-08 | PASS | — |
| R-B04 | Signature-specific missing deduction correct | V-RES-02, V-RES-08, V-RES-14 | PASS | — |
| R-B05 | No static-deficit double-counting | V-RES-11, V-RES-14 | PASS | Original exact A2 state tested at helper level |
| R-B06 | No used-neighbor subtraction | V-RES-12 | PASS | — |
| R-B07 | No cross-vertex aggregation | V-RES-03, V-RES-08 | PASS | Single-candidate source seam |
| R-B08 | Exact-k leaf preserved | V-RES-09, V-RES-10, V-RES-26, V-RES-28 | PASS | — |
| R-B09 | Actual reachable residual-only prune | V-RES-13 | PASS | — |
| R-B10 | k=0 equivalence verified | V-RES-15 | PASS | Registered k=0 streams |
| R-C01 | Correct query-local ownership | V-RES-04, V-RES-08, V-RES-20 | PASS | Source lifetime and abort recovery |
| R-C02 | Deterministic canonical edge/slot mapping | V-RES-02, V-RES-08 | PASS | — |
| R-C03 | Both endpoints counted | V-RES-02, V-RES-04, V-RES-08 | PASS | — |
| R-C04 | Deferred MISSING edges counted | V-RES-05, V-RES-14 | PASS | — |
| R-C05 | UNKNOWN-to-MISSING transition correct | V-RES-04, V-RES-05, V-RES-06 | PASS | — |
| R-C06 | Existing MISSING not counted again | V-RES-05, V-RES-08, V-RES-14 | PASS | — |
| R-C07 | PRESENT leaves missing state unchanged | V-RES-08, V-RES-15 | PASS | — |
| R-C08 | Multi-edge resolution undone | V-RES-06 | PASS | — |
| R-C09 | Normal backtracking correct | V-RES-07, V-RES-08 | PASS | — |
| R-C10 | Sibling and anchor isolation | V-RES-07, V-RES-08 | PASS | — |
| R-C11 | Exception and abort lifetime safe | V-RES-20, V-RES-21, V-RES-29 | PASS | Search-local destruction on unwind |
| R-C12 | Independent residual-state reconstruction | V-RES-08 | PASS | Bounded native trace |
| R-D01 | Checked count increment and capacity | V-RES-03, V-RES-04 | PASS | — |
| R-D02 | Safe decrement and undo | V-RES-04, V-RES-06 | PASS | — |
| R-D03 | Safe residual-demand subtraction | V-RES-03 | PASS | — |
| R-D04 | Safe remaining budget arithmetic | V-RES-03, V-RES-15 | PASS | — |
| R-D05 | Checked state allocation and indexing | V-RES-02, V-RES-03, V-RES-29 | PASS | Allocation capacity guards reviewed; no physical OOM test |
| R-D06 | NONE remains default | V-RES-18, V-RES-24, V-RES-26 | PASS | — |
| R-D07 | Native residual CLI and metadata | V-RES-18, V-RES-28 | PASS | — |
| R-D08 | Wrong-mode execution rejected | V-RES-18 | PASS | Six actual wrong-mode subprocesses |
| R-D09 | Memory fallback ineligible | V-RES-19 | PASS | — |
| R-D10 | FAR configuration and Unicode preserved | V-RES-17, V-RES-18, V-RES-22 | PASS | — |
| R-E01 | A1 original and chord verified | V-RES-09, V-RES-10 | PASS | — |
| R-E02 | A2–A7 reconciled | V-RES-03, V-RES-05, V-RES-11, V-RES-12, V-RES-15, V-RES-17 | PASS | — |
| R-E03 | Strict and deferred Codex witnesses | V-RES-13, V-RES-14 | PASS | — |
| R-E04 | Residual rollback and actual work abort | V-RES-20, V-RES-21 | PASS | — |
| R-E05 | Postcommit delivery integrity preserved | V-RES-22 | PASS | — |
| R-E06 | PBT exact tuple parity | V-RES-23 | PASS | — |
| R-E07 | Canonical 25/840/56×12 | V-RES-26, V-RES-27, V-RES-28 | PASS | — |
| R-E08 | Frozen NONE/STATIC comparison | V-RES-24, V-RES-25 | PASS | Bounded agreed comparisons |
| R-E09 | Counter definitions and accounting preserved | V-RES-08, V-RES-15, V-RES-24, V-RES-25 | PASS | — |
| R-E10 | Full regression and sanitizer evidence accurately reported | V-RES-29, V-RES-30 | PASS | Targeted ASan/UBSan. Integrated upstream leak qualified above |
| R-F01 | Source-backed report | V-RES-02, V-RES-08, V-RES-32 | PASS | Source references and transition inventory |
| R-F02 | Complexity and memory accounting | V-RES-02, V-RES-03 | PASS | Logical element bytes only |
| R-F03 | Actual commands and provenance | V-RES-01, V-RES-26, V-RES-28, V-RES-29, V-RES-30 | PASS | Command log, manifests and captures |
| R-F04 | Historical P4a artifacts and binary unchanged | V-RES-31 | PASS | — |
| R-F05 | Exact tested source and binary hash | V-RES-26, V-RES-28, V-RES-32 | PASS | Tested source and independent build identities above |
| R-F06 | Git diff and working tree clean | V-RES-32 | PASS | Both accepted-base diff checks |
| R-F07 | Final commit pushed and remote synchronized | V-RES-32 | PASS | Exact remote SHA in handoff evidence |
| R-F08 | Issue OPEN, PR DRAFT, stop at review gate | V-RES-32 | PASS | No external lifecycle changes |

Handoff stops at the Independent Implementation Review Gate on Draft PR #39.
