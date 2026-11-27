# Coordinator decision packet for P4b-3B.1

Independent Technical Lead acceptance is pending. Every decision below is a proposal.

| Decision | State | Proposal or question |
|---|---|---|
| 1. Workload freeze | PROPOSED | Retain 50 frozen workloads and exact identities. |
| 2. DISC-07 | PROPOSED | Retain both cases and work cap 500,000. Accept zero spare DISC coverage margin. |
| 3. C3 controls | PENDING APPROVAL | Allow scoped differential evidence and limited BARE controls without treating either as exhaustive. |
| 4. Schedule | PROPOSED | Preserve 200 warm-up, 1,000 primary, 250 descriptive diagnostic slots, R=5 and Williams sequence. Diagnostic order is not position balanced. |
| 5. Resources | PROPOSED | 60s, 512 MiB RLIMIT_AS, 500,000 work units per step, 256 MiB NLF admission. Review the same-category repeated failure rule. |
| 6. Environment | PENDING APPROVAL | Review Ubuntu 24.04.5 / i5-8265U snapshot, powersave governor, high swap occupancy at preflight, load, libraries, affinity, power and free disk. Recheck immediately before activation. |
| 7. Primary inference | PROPOSED | Preserve paired workload medians, complete DISC blocks and fixed coverage denominators. |
| 8. Strata | PENDING APPROVAL | Choose registered R1 or conservative 10 / 5 / 90% candidate. Use synthetic tests to inspect priority 6 behavior. |
| 9. Multiplicity and dependence | PENDING APPROVAL | Retain unadjusted primary intervals, separately labeled Holm p-values and Bonferroni intervals, and dependence limitations. Review any additional family sensitivity separately. |
| 10. Execution | PENDING APPROVAL | After independent acceptance approve exact envelope, host, release, registry, source and permitted phases through the reviewed anchor. |
