# P4b-3B detached activation candidate and runbook

State `PREREGISTRATION_CANDIDATE`. The current reviewed envelope map is empty.
The executing checkout reads the committed map at
`configs/rapidflow_apx_p4b_activation_anchors.json`. A local approval flag,
file or digest cannot issue authorization.

## Trust and envelope

After independent acceptance and Coordinator approval, a separate reviewed
commit may pin the exact SHA-256 of a detached envelope and its two external
approval references. The envelope schema `p4b-detached-activation-v1` binds
experiment ID, exact absolute session path, allowed phases, research scope,
protocol, workload manifest, schedule, correctness registry, PRE-R09 archive
and manifest, native source and release hash, implementation commit and
source digests, stable host digest, resource policy, statistical amendment
hash, C3 policy and DISC-07 disposition. Review the exact source fields in
`p4b_activation.py::authorize`.

The detached envelope binds the earlier reviewed logic commit. The subsequent
anchor may therefore pin the envelope without a circular digest. Only anchor
and documentation changes may follow the earlier commit. Recheck the envelope
before each production launch. The threat boundary is the reviewed checkout,
OS, binary and supervisor. Hashes detect stale and substituted untrusted
evidence but cannot attest against a privileged party replacing all roots.

## For P4b-3B.1 only

**DO NOT EXECUTE DURING P4b-3B.0.**

1. Record both approvals and preserve exact envelope bytes. Verify committed
   source, clean worktree, pristine upstream, release SHA-256 and approved
   session path.
2. Verify the raw references with
   `python3 scripts/p4b_correctness_registry.py --archive ARCHIVE --verify`.
   Check stable host identity, background load, memory, free disk, libraries
   and expected caps. Current admission requires 768 MiB available RAM and
   12 GiB free space. Preserve the full repetition count.
3. Execute the warm-up and primary phase first. Use the reviewed absolute
   path and the same inputs for every resume.

   ```bash
   python3 scripts/run_rapidflow_apx_p4b.py --execute-primary \
     --activation APPROVED_ENVELOPE --archive ARCHIVED_PRE_R09 \
     --binary LOCKED_RELEASE --session APPROVED_ABSOLUTE_SESSION
   ```

4. Substitute `--execute-diagnostic` only if explicitly approved, after
   valid primary completion, at the identical session directory.
5. Resume at a terminal boundary by rerunning that phase. Completed
   sessions return accounting without launching a child. After a
   semantic failure or evidence discrepancy preserve all files and halt.
   A START without FINISH requires independent review even if no PID
   remains visible. Never edit the journal or assume a safe retry.
6. Substitute `--analyze-session` to independently reload persisted evidence.
   Partial sessions have accounting only. Only a complete reviewed session
   can issue confirmatory estimates. Diagnostic attribution is descriptive.
7. Package activation, runtime environment, artifact digests, journal,
   censors and analysis. A+B and any optimization selection remain separate
   decisions.
