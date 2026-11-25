# R1 measurement boundary and resource register

Native wall timers use C++ steady_clock. The Python supervisor uses
perf_counter_ns before Popen and after wait4 observation. Both use the Linux
monotonic clock basis on the locked host. Durations are integer nanoseconds.
Floor rounding of nested intervals cannot increase their sum past a
containing interval. No tolerance is used.

| Timer or invariant | Source and boundaries | Relationship and scope |
|---|---|---|
| Supervisor outer wall | run_rapidflow_apx_p4b.py execute_p4b_native_slot, before Popen through wait4 observation | Exact reaped minus started. Includes polling and reap overhead. |
| Input parsing | harness_runner.cpp run_native_harness parse_t0 to parse_t1 | Inside outer wall. |
| Initialization | init_wall_t0 to init_wall_t1 | The disjoint graph init, query prep and engine/NLF init intervals fit inside initialization. |
| Streaming | stream_t0 to stream_t1 in the correctness and performance/diagnostic paths | Disjoint from parsing, initialization and reporting. Sum of the four macro phases fits inside outer wall. |
| Reporting | report_t0 to report_t1 | JSON construction outside streaming. Final streamed file write occurs later. |
| Profiling wall | run_wall_t0 through pre-output snapshot | Matches total_wall_ns alias, encloses streaming and fits inside outer wall. May overlap reporting. |
| Step timers | ApxStreamingEngine::execute_step before transaction work through tx callback return | Each diagnostic timer fits inside step_total_ns. Search contains retrieval and search core. Commit and sink are aliases. The subtimers may overlap, so they are checked separately. |
| Per-field cumulative | CumulativeProfiling::accumulate and harness_runner.cpp serializer | Each field equals the sum of corresponding completed step observations and independently fits inside streaming. A torn result may retain only a prefix of the step observations. |
| CPU and RSS | wait4 user/system/maxrss and child /proc/self/status VmHWM | Reconcile representations independently. CPU is not bounded by wall. Peak child KiB and bytes agree exactly. wait4 RSS is an abnormal-exit fallback and does not establish memory exhaustion. |

Complete structured outcomes require timers and frozen update identity.
External termination may leave no output or only a native writer prefix.
Every complete available record is validated. Missing fields are preserved
as missing. Diagnostic profiles are validated whenever present.

| Supervisor / native outcome | Evidence required | Final use |
|---|---|---|
| Post-launch identity drift or unreadable binary/input | Supervisor disposition or mismatching post-launch digests | INVALID / PROVENANCE_ERROR, including without output. |
| Exit 0, requested configuration succeeds | Full identity, complete frozen lifecycle, timers/RSS and same-variant reference | COMPLETED. Real admission still requires activation. |
| Exit 4 at logical cap | Complete frozen updates, valid committed prefix, first CALLBACK_ABORTED at cap + 1, suffix unattempted, outcome consistency and reference | CENSORED / WORK_BUDGET_EXCEEDED. |
| Exit 5 with structured allocation failure | Matching configuration, identified ERROR before engine counters exist, std::bad_alloc across first-failure/root/step, prefix and complete suffix | Registered resource censor. Does not establish an RSS of 512 MiB. |
| Exit 5 with no serialized result | Exit 5 plus native startup containment diagnostic, matched invocation/caps and wait4 RSS | RESOURCE_UNAVAILABLE. Native fails before it parses a workload in this path. |
| NLF configuration unavailable | Requested static/residual, effective none, allowed fallback reason, valid exit and full lifecycle, BARE reference tuples | CONFIGURATION_UNAVAILABLE. Native can complete via fallback at exit 0 without measuring the requested variant. |
| Supervisor deadline or native SIGALRM at registered deadline | Matching wait4 status and elapsed boundary. Absent or torn output allowed. Available records remain bound to frozen input. | TIMEOUT. |
| Other signal, unknown error, missing or contradictory evidence | No established resource event | Interrupted, failed or invalid. Generic kill or bad_alloc text does not prove memory censoring. |
