# RapidFlow-APX P4b-3B.0 activation infrastructure

**Verdict** `P4B_3B_0_READY_FOR_INDEPENDENT_ACTIVATION_REVIEW`. Independent review and Coordinator authorization
remain pending. The frozen protocol remains `PREREGISTRATION_CANDIDATE` with
`execution_authorized=false`. Zero official warm-up, primary or diagnostic
slots ran. No optimization winner or combined A+B was implemented.

## Provenance

- Starting local, remote and PR head `d864f11d08812009c540cf68d3abc134ceaa0ee7`.
  Final tested source `ef64048d1789acef70f8444ce9f5f064b1ad0fdf`.
- Pristine pinned upstream `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`.
  Preserved native release `f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75`
  (941,216 bytes). Accepted P4b-2B binary
  `4b7902be22c5429b57be09aa84f99e1b8e13af072e932ff4e40774b5aa0dbf78`
  (854,160 bytes). No native source, release build or comparator changes.
- 50/50 fixture hashes verified. Protocol
  `70836770663c32a1a53e52e048be1dd8268fd2e3f02f66859737eef308c4acbb`.
  Manifest `09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869`.
  Schedule `824d4b09f31432fde355597aee877f38cae3f267b66dd658d8fc82c3cac05e6f`.
- Seven historical P4a artifacts verified. P4b-3A archive
  `cf7f1cfb6beed9cc6821deddaa0b3694727e8550d7ef2823bff40d6fb3f8b671`.
  R1 archive `22b5245d24d47b00fec6f3ab7b0960a6d6a27636759cc9c7c1259a343882cfe9`.
  Embedded manifest `cdd7ae9e7063649e8945a6e7a086d16e54410b43c313623f935b2bc9309a1898`
  and all 1,812 members verified. Historical R1 canonical and 856-test evidence
  is kept separately. R1 covered M1 and five M2 configurations with 25
  hand cases, 840 corpus cases, and 56 streams / 672 updates per
  configuration. Its 150 exact frozen-release comparisons cover hand
  cases only. P4b-3B.0 verified the archived inventory and reran current
  repository regression and bounded native checks. The five-selector R1
  ASan/UBSan scope is inherited. Leak detection was disabled in R1 for the
  upstream sparse hash map leak.

## Registry, execution and ingestion

`p4b_correctness_registry.py::validate_registry` rebuilds deterministic
registry bytes from the pinned archive and recomputes M0 and graph states.
Safe tar parsing validates each member, then compares lifecycle, same-variant
identity, tuples, counters and certification. The 250 entries contain 160
independent C1, 64 differential C3 optimized, 16 limited C3 BARE and 10
censored references. The registry digest is
`a122454b3cdd5b00c221a3f3d847aea372c7e1f9889c4fb1314017c16d4e2117`.

`p4b_activation.py::authorize` verifies detached approval against the
committed anchor. `run_rapidflow_apx_p4b.py::main` invokes
`p4b_experiment_session.py::production_command`. It verifies authorization
before opening the session and verifies the pinned reference registry.
The low-level adapter accepts direct performance or diagnostic calls only
for the fixed historical smoke fixture and its registered variant/purpose
pairs. Production calls must bind the active attempt's fixture digest,
binary path, purpose, variant, output path and caps before child creation.
`session` shares scheduling, lock, attempt lifecycle, raw evidence and
independent revalidation across synthetic and native adapters. START
precedes launch and FINISH follows durable raw evidence and independent
validation. Exclusive publication and fsync protect the append chain.
An unresolved START blocks; a semantic, delivery or integrity incident
halts. The new scheduler issues first attempts only. The unchanged R1
resolver still permits only verified prelaunch lineage. No ambiguous slot
is retried.

`validate_attempt` recomputes the terminal outcome from raw output,
supervisor wait status, caps, metrics and a same-variant reference. The
journal reader recomputes independently and treats changed artifacts,
lifecycle or metric provenance as integrity failures. The real CLI only
calls the shared estimator after complete reviewed evidence. A partial
session returns accounting and incident evidence. Version 2 session journals
are a separate transport and do not silently ingest R1 journal files.

## Measurement and interpretation

Streaming retains full tuples and counters. Output writing is outside the
streaming timer. Reporting and VmHWM end before final streamed output, so
later memory peaks may be missed. RSS success needs child VmHWM in KiB
converted exactly to bytes. Wait4 RSS may include inherited parent memory
and only describes failure when used as a fallback. Outer wall includes
wait4 polling. Diagnostic timers can nest and each slot reports its
denominator. There is no observer calibration. Resource records retain
exit code, signal, actual stderr and native categories. A bad_alloc
does not establish the amount of memory that would have been needed.

The estimator and classifier preserve the R1 matched cohort and thresholds.
Unapproved statistical choices remain candidates. Amendment hash
`100467a2ac3592166134c47c4fb9107a0e7265f71bf23e0cf2fdfba65a441878`. Continuity dependence and diagnostic position imbalance
limit interpretation. Host environment was observed rather than changed.
The candidate requires at least 12 GiB free disk and 768 MiB available
memory. The historical raw size analogue across planned slots is 2.37 GB,
or 4.75 GB with a twofold margin. This is a storage estimate, not a benchmark
measurement. Free storage and available memory at preflight were 112.4 GB
and 4.81 GB. The preflight snapshot showed about 3.8 GiB of 4 GiB swap
occupied and a powersave governor on every logical CPU. These observations
need review before activation. They were left as observed. The locked release
cache records GCC 13.3.0 and CMake 3.28.3. CMake is installed in the
repository tool directory and was invoked by absolute path for verification.

## Executed verification

| Check | Observed |
|---|---|
| Production routing | One slot through the production CLI, scheduler and adapter with test-private trust and a fake child. Persisted START and FINISH, reran the CLI without relaunch, and verified that partial analysis supplied no confirmatory result. |
| Synthetic schedule | 200 warm-up, 1,000 primary, 250 diagnostic. 1,450 unique terminal slots, 2,900 events, 0 duplicate/missing, 0 native subprocesses. |
| Pause/resume | positions 0, 4, 21, 600, 1,200, 1,223 and 1,450 plus completed reentry. |
| Faults | Persistence-boundary interruption, torn append, altered digest, prior semantic halt and Linux lock contention and release after child exit. Ambiguous PID marker is retained for review. |
| Bounded witness | P4B_CONT_01 / BARE in correctness, performance and diagnostic. Three retained native outputs. Mutations check exact tuple order, modes, lifecycle and RSS. Smoke data are excluded from confirmatory eligibility. |
| DISC-07 | Offline projection of archived correctness bytes with a synthetic supervisor. Both exact work-budget prefixes validated. Zero production subprocesses. |
| Statistics | 24 synthetic scenarios, the original classifier cases and independent 10,000-draw recomputation on a varied workload fixture. |
| Regression | 870/870 passed. 0 failures, 0 errors, 0 skips, 0 expected failures, 0 unexpected successes. Exit 0. 577.607 seconds. |

## Review boundary

The required regression includes the unchanged bounded historical P4a
profiling smoke. It yielded no P4b measurements or interpretation.
The production activation test and full rehearsal created zero official
slots. No approved production session or real confirmatory inference was run.
Tests use a fake subprocess adapter for the production route and real
bounded witnesses for native binding. Host outages were simulated at
named persistence boundaries rather than induced on this host. Hash
binding assumes a trusted review checkout and supervisor. C3 and censored
references remain limited. The acceptance matrix records any unverified
engineering gates independently of the two pending approval gates.

## Engineering and approval gates

The machine-readable matrix records 78 of 78 engineering
gates as PASS. The two approval gates remain pending. The evidence package
contains the actual log, binary identity, tested Git revision, evidence
hashes and a post-push Git snapshot.

## Implementation and test commits

- `954c8ca656210314d3e8fb01cc970e643f8048bd` feat(p4b): bind reviewed activation, correctness registry and experiment sessions (#38)
- `17a653a5bbeff8fa1cb9edb52fcb12d89c40b071` test(p4b): isolate synthetic scenarios across readiness checks (#38)
- `346c4c740190b8d78921d4183d0cdfaef161c2ce` fix(p4b): contain monitored child on supervisor death (#38)
- `3eb0b22725cf9254333a60b3d5e9571ae9d74976` fix(p4b): return structured denial from the direct activation CLI (#38)
- `b8832aa305d9ce5e715b5b1c1ad1cd37fa3c44bf` fix(p4b): guard the low-level native measurement launch (#38)
- `ef64048d1789acef70f8444ce9f5f064b1ad0fdf` test(p4b): exercise launch rejection in the direct-adapter test (#38)

Issue #38 records the 3B.0 implementation scope. PR #39 is the existing
draft into QuangThang. Activation requires the two external approvals
described in the candidate and Coordinator packet.

[Acceptance matrix](rapidflow_apx_p4b_3b0_acceptance_matrix.json) ·
[Activation candidate and runbook](rapidflow_apx_p4b_3b_activation_candidate.md) ·
[Coordinator packet](rapidflow_apx_p4b_3b0_coordinator_packet.md) ·
[Preregistration amendments](rapidflow_apx_p4b_preregistration_amendments.md)
