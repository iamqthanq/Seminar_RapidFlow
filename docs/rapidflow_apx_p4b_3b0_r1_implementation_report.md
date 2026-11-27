# RapidFlow-APX P4b-3B.0 R1 production evidence integrity

**Engineering handoff** P4B_3B_0_R1_READY_FOR_INDEPENDENT_REVIEW

**Phase authorization** P4B_3B_1_NOT_AUTHORIZED

Starting SHA f6489c629ccf60edbc6939aeaa551f5ab8adbf05 was verified in the
clean RF38 worktree and on the corresponding feature ref and draft PR.
Tested implementation fb856e249b5c988edc7e95c8596e6377fe166349. Evidence identifies the tested source.
A later documentation-only commit can have a different SHA.

## Findings and verification

| Finding | RED on the baseline | Boundary corrected and GREEN |
|---|---|---|
| B30-FIND-01 HIGH | PROVENANCE_ERROR returned COMPLETED | Supervisor disposition and post-execution binary/fixture digests gate the live and offline validator before raw parsing. Valid-looking, resource, partial, empty and absent output all remain INVALID on known drift. An actual controlled child removed its copied fixture after launch. |
| B30-FIND-02 HIGH | Streaming and graph init at 10× outer wall returned COMPLETED | Native macros, actual containing intervals, step profiles, aliases and cumulative totals are compared to independently reconciled supervisor boundaries. Both mutations return INVALID. Tests include numeric types, exact boundaries, overlapping timers and wait4 reconciliation. |
| B30-FIND-03 HIGH | Inconsistent initialized configurations and lifecycle returned CENSORED for RESOURCE_LIMIT_ERROR and CONFIGURATION_UNAVAILABLE | Exit and deadline facts, resource caps, raw schema, workload/configuration, lifecycle, prefix, timing and failure mechanism are checked before resource admission. Both mutations return INVALID. Verified timeout, structured pre-execution allocation failure, native fallback and DISC-07 retain the registered resource classifications. |
| B30-FIND-04 MEDIUM | DISC_07_K0 suffix step 11 was accepted with a wrong operation | Every emitted frozen record verifies step index, canonical operation, original endpoint order and typed label before lifecycle acceptance. The original mutation and additional duplicate, omitted, reordered, label, endpoint and commit-status mutations are rejected. |

The original RED logs precede source edits. The six paired RED/GREEN
mutations operate on copies with regenerated raw and envelope hashes.
The historical DISC-07 offline projection used a 1 ms supervisor
placeholder. The paired experiment uses a controlled envelope consistent
with the archived native timings on both revisions. The expected rejection
now identifies the wrong update operation. This prevents timing from
masking the suffix defect. The prior native work and outputs remain intact.

## Shared validation and authority

scripts/run_rapidflow_apx_p4b.py records wait4, monotonic wall, resource policy
and the post-launch identities before returning the supervisor envelope.
Metadata availability or drift can precede child schema checks. A reported
provenance failure is retained as PROVENANCE_ERROR and INVALID.

scripts/p4b_experiment_session.py session executes the native adapter,
persists child/supervisor bytes and calls validate_attempt before FINISH.
_read_journal checks the chain, original digests, recomputed validation and
terminal payload. Both resume and final analysis use that reader. Invalid
attempts halt and cannot become successful or resource-censored samples.
Only separately approved completed sessions can reach confirmatory analysis.

| Entrypoint | Supervisor and child evidence | Shared result and consumer |
|---|---|---|
| Live execution via production_command and session | NativeBackend plus wait4, invocation and persisted raw bytes | validate_attempt before journal FINISH |
| Offline session read and resume | Stored process.json, native-output.json and the locked reference | _read_journal recomputes validate_attempt and compares original FINISH |
| Reviewed analysis via production_command | Same independently reread journal | _analyze_reviewed_records accepts only the separately authorized completed session |
| Full R1 synthetic rehearsal | Test-only production-format adapter, separate synthetic identity | The same validate_attempt and session logic, followed by the existing synthetic statistical entrypoint |

scripts/p4b_native_evidence.py implements cross-clock bounds, profile aliases
and cumulative reconciliation, strict frozen update identity and parsing of
complete records in an externally interrupted writer prefix. Known native
profile and lifecycle contradictions fail closed. Unemitted bytes have
no assigned observations. The complete resource and timing map is in
timing-and-resource-map.md inside the evidence package.

The shared schema has opt-in flags for the native-specific pre-execution
allocation error and verified NLF fallback. The defaults on historical
correctness and preflight paths preserve their original behavior. A real
fallback can complete under BARE with exit 0, which proves the requested
optimizer was unavailable only if its fallback identity, counters/lifecycle,
scope and reference tuples all reconcile. Unattributed SIGKILL or a free
text allocation error does not establish a memory limit event.

The synthetic C3 BARE admission is narrow and requires the non-confirmatory
origin, the bounded reference tier, no activation, and measurement_eligible
false. Python AST comparison verified identity of the rest of the statistical
analysis source. Confirmatory admission, decision precedence, thresholds,
bootstrap, Holm and statistical families retain the frozen behavior.

## Production-format rehearsal

Completed 1450 distinct slots with 1450 START and 1450 FINISH events. No missing or duplicate slots and no native matching subprocesses. The non-confirmatory analyzer consumed the exact reread journal. Verified native-format outcomes were 1392 COMPLETED and 58 CENSORED.

The test-only adapter supplies child files and supervisor facts and invokes
the real scheduler, journal writer, shared validators and final analyzer.
Certifications are drawn from the 250 locked registry entries. B_BUFFER
appears only in its 50 diagnostic slots. Valid DISC-07 failures follow
their archived reference and are not missing coverage records.

The five-repetition isolation controls preserve five actual validated
synthetic BARE observations and replace two variant observations with
independently validated alternative attempts. The alternatives have
separate journals because production halts at the first invalid attempt.
The negative control counted zero invalid variants as successful or
resource-censored and concluded INCONCLUSIVE at priority 2. The valid
control retains priority 3 NO_GO / RESOURCE_FEASIBILITY_REGRESSION.
Both results are verification of the frozen rule using synthetic data.

Fault tests exercise pre-START, between START and launch, after controlled
execution/raw persistence, before and after FINISH, and on reopen of a
diagnostic session. A pending attempt cannot be relaunched. The actual CLI
rejects primary, diagnostic and analysis without activation at exit 2
and creates no session. Frozen production trust anchors remain empty.

## Preservation and regression

Full unittest discovery passed 879/879 in 810.676 seconds at fb856e249b5c988edc7e95c8596e6377fe166349. Baseline reported 870 tests. There are 9 new tests, zero failures, errors, skips, expected failures and unexpected successes. Exit status 0.

All groups A–L are itemized with SHA-256 records in inventory-before.json,
inventory-after.json and preservation-result.json. They cover pinned
pristine upstream, native sources and release binaries, all historical
archives and archived trust manifests, seven P4a files, 50 fixtures and
the frozen workload manifest, correctness registry, Williams schedule,
protocol, decision policy and activation anchors. The only source change
in the analyzer is the synthetic admission described above.

The native measurements used for validation are the bounded witnesses
from the locked release, the bounded tests in repository regression and
the controlled reference envelopes. The published historical native hash,
sampling scheme, matching implementation and optimization semantics
remain unchanged. A deliberate child-process provenance probe used a
temporary fixture and a controlled script.

## Reproduce independently

1. Verify the detached evidence archive with SHA256SUMS and run
   python3 package-r1.py verify ARCHIVE_DIRECTORY. This extracts regular
   artifacts to a new temporary directory and compares the manifest.
2. Reconstruct the required baseline with git archive at the starting SHA
   into an isolated directory. Use the commands in evidence-commands.txt
   to run the RED reproducer there and the GREEN reproducer on the tested
   revision. The archived RED/GREEN copies retain raw input and envelopes.
3. Run unittest for tests.test_rapidflow_apx_p4b_3b0_r1 and the complete
   unittest discovery on a clean checkout. Inspect the exact test inventory
   in full-regression.log.
4. Run run-production-format-simulation.py with the archived prior R1
   correctness evidence and an empty evidence workspace. Inspect the
   configuration coverage matrix, ledger, final analysis and separate
   statistical-control journals. Check the three unauthorized CLI outcomes.
5. Review the actual source in harness_runner.cpp and streaming_engine.cpp
   against timing-and-resource-map.md. Check preservation-result.json and
   the original and R1 engineering matrices.

## Review boundary

Trials of real production performance remain unauthorized. Controlled
supervisor observations test resource classification without forcing failures
in the historical release. If a structured allocation failure occurs before
the native child emits sufficient frozen step identity, R1 cannot admit it as
a validated native censor. A separate startup limit-install failure has its
own supported exit and diagnostic. An external timeout can retain complete records
or a writer prefix, and only emitted fields can be verified. The bounded
predecessor envelope format lacks explicit post-launch digests and may
verify historical functional evidence using its recorded disposition. New
production and simulated production-format records require the digests.
No conflict with the frozen experimental hypotheses was introduced.
Independent re-review and any later activation decision remain external.

## Evidence and engineering gates

The independent extraction verified all 10427 artifacts. Archive SHA-256 725e4ea162032fa2a7bc8311d40d9bf23546b396db080b8620afd33dec4e40e6. Manifest SHA-256 77b243c925943f8ed4c7d1a929d4591cfb72a5e28aa566f39fea1aab3c6337d5. The R1 reconciliation covers all 78 engineering gates and retains the two external approval gates as pending. These results record implementation checks. Independent acceptance remains pending.
