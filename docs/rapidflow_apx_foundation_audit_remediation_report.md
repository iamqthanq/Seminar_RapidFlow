# Foundation Audit Remediation — Issue #38

**Proposed handoff:** FOUNDATION_AUDIT_REMEDIATION_READY_FOR_INDEPENDENT_REVIEW. Các kết quả dưới đây do Implementation Agent thực thi. Independent Implementation Review thuộc Technical Lead.

| Hạng mục | Revision hoặc identity |
|---|---|
| Historical audit terminal | be136f3d4d0ea0b50d51329dbc9a61dd714d719f |
| Feature start, P4b-1A R1.1 | 22e3735ce67d52acb7bfe05bcad8bf22bb9a3908 |
| Final implementation revision tested | 65b73d242124a2a9c1c6b243daa66a06910f7174 |
| Handoff revision | Docs-only child of the tested revision. The PR head identifies the handoff commit. |
| Native binary SHA-256 | 58380790badce7499989a224d276bf5ce914e5cca0a18681f18379a3fec6381e |
| JSON test binary SHA-256 | 1e91d2b1b34dcd431cfda21eacf96b037aaafe664fe8f00d539b88d1a5972297 |
| Pinned RapidFlow upstream | 2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1 |
| Feature and target | feature/rf38-p4b-optimization into QuangThang |

Project Coordinator đã phê duyệt targeted remediation trên feature hiện hữu. Audit findings tác động trực tiếp đến typed identity, transaction consistency và correctness gate của #38. Issue #38 và Draft PR #39 tiếp tục là issue và review gate của cùng work branch. [Issue #38](https://github.com/iamqthanq/Seminar_RapidFlow/issues/38) · [Draft PR #39](https://github.com/iamqthanq/Seminar_RapidFlow/pull/39)

## FAR-A — Unicode semantic integrity

Trên binary build từ 22e3735, cả ba cấu hình tái hiện hai witness. Parser cũ bỏ qua nội dung của Unicode escape và chèn dấu hỏi. Parser hiện tại đọc bốn hex digits, kết hợp surrogate pair hợp lệ, encode Unicode scalar sang UTF-8 và kiểm tra UTF-8 trực tiếp trước khi chấp nhận label. Các escape lỗi, control characters chưa escape, overlong sequences, surrogate encodings và code points ngoài Unicode đều bị từ chối. String identity là exact Unicode scalar sequence, giữ nguyên khác biệt giữa NFC/NFD, kiểu int và string. UTF-8 trực tiếp và escape hợp lệ của cùng một scalar sequence cho cùng string identity. Embedded NUL vẫn round-trip qua JSON output.

| Original witness | Before at 22e3735 (all three modes) | After at tested revision (all three modes) |
|---|---|---|
| typed-unicode-mismatch | INSERT ENTERED [[0,1]], inverse DELETE EXITED [[0,1]] | Hai event arrays rỗng ở cả hai update |
| typed-unicode-equivalent | Hai event arrays rỗng ở cả hai update | INSERT ENTERED [[0,1]], inverse DELETE EXITED [[0,1]] |

Matching tests đối chiếu tuple đầy đủ giữa oracle vét cạn dựng riêng trong test, Python M0, M1/NONE, M2/NONE và M2/STATIC. Triangle k=1 cô lập wrong Unicode edge label như đúng một missing edge, đồng thời kiểm tra equivalent initial label, INSERT/DELETE labels và DELETE+INSERT với label khác. Supplemental parser chạy 25 assertions cho UNI-01–18 và các byte/scalar boundaries. Input UTF-8/escape hai dạng ban đầu được sao chép byte-for-byte từ audit; SHA nằm trong thư mục evidence. [JSON parser](../extensions/rapidflow_apx/include/rapidflow_apx/json_mini.h) · [Parser tests](../tests/native/rapidflow_apx_engines/src/json_string_tests.cpp) · [Supplemental integration tests](../tests/test_rapidflow_apx_foundation_audit.py)

## FAR-B/C — Commit, delivery và fail-fast

Trên bản sao 22e3735, failpoint bad_alloc ngay trước deep graph diagnostics của A-static trả bước INSERT đầu NOT_COMMITTED dù đã giữ 2 ENTERED. Bước INSERT lặp bị từ chối và DELETE cuối trả 2 EXITED. Sau patch, cùng witness báo COMMITTED và DELIVERY_FAILED với RESULT_DELIVERY_ERROR. Hai bước còn lại UNATTEMPTED dù có continue-on-error. Một lỗi recoverable đã được ghi trước đó vẫn xuất hiện trong first_failing_step_index, first_failure_category và first_failure_message.

Transaction result mang committed flag qua streaming result. Graph/NLF finalize, sink publication và success result handoff dùng các thao tác non-throwing sau commit. Search, staging và rollback vẫn thuộc precommit. Native harness đóng vòng update khi reporting của kết quả đã thực thi thất bại. Delivery record giữ execution_status, commit_status, delivery_stage và empty event arrays, với exit 9. Canonical Python runner từ chối dùng delivery-failed records làm complete evidence. Tại output serialization hoặc I/O failure, stderr báo best-effort RESULT_DELIVERY_ERROR và số step đã attempt/commit. I/O failure giữ exit 7. Phạm vi là in-memory transaction và harness reporting.

| Boundary | Test coverage | Outcome |
|---|---|---|
| BEFORE_MUTATION | INSERT và DELETE, 3 modes | NOT_COMMITTED và update kế tiếp thành công |
| ALLOCATION_FAILURE_MUTATION | INSERT và DELETE, 3 modes | NOT_COMMITTED, state đã rollback |
| DURING_CALLBACK | INSERT và DELETE, 3 modes | NOT_COMMITTED, staging abort |
| BEFORE_PUBLISH | INSERT và DELETE, 3 modes | NOT_COMMITTED, mutation đã rollback nếu áp dụng |
| REPORT_AFTER_COMMIT | INSERT và DELETE, 3 modes | COMMITTED, delivery failed, suffix UNATTEMPTED |
| REPORT_RESULT_JSON | INSERT và DELETE, 3 modes | COMMITTED, delivery failed, suffix UNATTEMPTED |
| REPORT_GRAPH_DIAGNOSTICS | INSERT và DELETE, 3 modes | COMMITTED, delivery failed, suffix UNATTEMPTED |
| REPORT_APPEND_STEP | INSERT và DELETE, 3 modes | COMMITTED, delivery failed, suffix UNATTEMPTED |
| Final output write | /dev/full | Exit 7, fatal stderr giữ committed count |

A-static thực hiện full-cell reconstruction tại 16 điểm failpoint của matrix, gồm cả precommit và postcommit, so NLF với actual graph. Các report faults là deterministic harness-only hooks. Lỗi resource thực có thể ngăn việc tạo đầy đủ JSON diagnostics.

Execution fields là additive. Attempted là bước đã vào xử lý. Committed là authoritative mutations đã commit. Completed là các bước thành công đã dựng step record đầy đủ. Delivered là attempted step records hoàn chỉnh trong workload output thành công, gồm cả record của lỗi recoverable. Failed gồm execution failures và fatal delivery failure. Unattempted là input suffix. Điều kiện completed + failed = attempted vẫn giữ nguyên. Historical artifacts giữ contract của phiên bản đã ghi.

Suffix builder luôn giữ step_index, UNATTEMPTED, NOT_COMMITTED và empty events. Metadata từng field chỉ được thêm khi đúng kiểu, op phải là alias nhận diện được. Original malformed suffix trả exit 8 và steps rỗng trước patch. Sau patch, exit 6 cho thiếu cạnh DELETE, giữ first failure ở index 0 và tạo đúng hai records. Supplemental tests kiểm tra wrong type ở u/v/op, unknown op, thiếu field, non-object, nhiều suffix rows, đúng alias và recovery khi schema error có thể bỏ qua. [Native harness](../tests/native/rapidflow_apx_engines/src/main.cpp) · [Transaction manager](../extensions/rapidflow_apx/src/transaction_manager.cpp) · [Streaming result](../extensions/rapidflow_apx/src/streaming_engine.cpp)

## FAR-D — Single configuration state machine

Shared validator áp dụng cùng consistency rules cho initialized success và initialized failure. Engine báo configuration_initialized ngay qua initialization boundary. Startup rejection chỉ có thể omit NLF metadata nếu outcome là startup error có exit phù hợp và lifecycle cho thấy chưa chạy update. Sau initialization, requested/effective modes, known configuration status, positive memory limit và logical bytes bắt buộc. Fallback effective none cần logical bytes bằng 0, reason được nhận diện và không đủ điều kiện Static. Metadata lỗi vẫn giữ native reported outcome trong runner output.

Các integration tests dùng output thật của CASE_HAND_21 và CASE_HAND_22. Chúng sửa một field sau subprocess hoặc dùng payload fallback có logic mâu thuẫn để kiểm validator. CASE_HAND_22 thất bại khi runner bị cung cấp static/SUCCESS/effective none, status unknown hoặc thiếu required metadata. Positive native run với memory limit bị đổi thành 0 bị từ chối và native SUCCESS vẫn được lưu trong runner outcome. Fallback mang nonzero logical bytes bị từ chối. [Shared validator](../scripts/run_rapidflow_apx_engines.py) · [Regression](../tests/test_rapidflow_apx_foundation_audit.py)

## FAR-E/F — Correctness gate và historical limitations

P4b design dùng registry canonical 25 required hand cases, native 56 streams/672 updates và 840 corpus cases từ hai seed files, mỗi file 420. Lệnh hiện hành dùng --stage và --nlf-mode static. Evidence tiers giữ independent M0, analytical, differential và censored. A được phép thay đổi counter values do pruning và resource outcomes. B cần sorted neighbor/traversal/counter parity tại những điểm giữ nguyên traversal và accounting.

P4a addendum ghi 180 historical records, 90 valid pairs, 30 workloads, 3 repeats và order 60/30, cùng bốn medians và candidate counts nguyên bản. Addendum giải thích đúng timing scope, historical attestation, nested repeats và cùng-binary correctness-only replay độc lập trước đây. Metrics và audit artifacts được giữ riêng với regression lần này. Bảy authority artifacts và binary lịch sử đã đối chiếu trước/sau; manifest không đổi. [Correctness design](rapidflow_apx_p4b_design.md) · [P4a audit addendum](rapidflow_apx_p4a_audit_addendum.md)

## Deferred P4b-3 preregistration

AUD-P4B-001 và AUD-P4B-002 là mandatory preconditions của P4b-3. Preregistration riêng cần khóa exact fixtures/hashes, generator/inventory, execution-order scheme, PROFILE_OFF macro boundary, diagnostics outside timing, workload aggregation, bootstrap unit/seed/algorithm, censoring/missing pairs, memory/resource eligibility, unique precedence, conditional A+B, evidence retention và peak RSS. Việc tái lập các nghiên cứu timing và triển khai variant tiếp theo cần gate riêng. Các kết quả regression bên dưới không được dùng làm benchmark performance.

## Regression thực thi ở implementation revision

Build pinned CMake 3.28.3 và GCC 13.3.0 trên Ubuntu x86-64, i5-8265U, Python 3.12.3. C++17, native engine -O3/-Wall/-Wextra, foundation library -fPIC. Xem compile-flags.log và environment.log cho toolchain thực tế. P1, P2 và native engine được clean-build. Bare và Static canonical runners dùng cùng binary SHA.

| Suite | Executed outcome | Elapsed / peak RSS / exit |
|---|---|---|
| P1 feasibility clean build | 6/6 probes | elapsed=8.14 peak_rss_kib=223148 exit=0 |
| P2 foundation clean build | 8/8 cases | elapsed=18.37 peak_rss_kib=251140 exit=0 |
| Canonical M1 and Bare M2 | 25/25 hand, 840/840 corpus and 672/672 stream steps per engine across 56 streams | elapsed=42.95 peak_rss_kib=312200 exit=0 |
| A-static on all canonical inventory | 25/25 hand, 840/840 corpus and 672/672 stream steps across 56 streams. The selected single-mode run is DIAGNOSTIC_SUBSET_PASS | elapsed=14.76 peak_rss_kib=29520 exit=0 |
| FAR supplemental | 10/10 tests, including 25 parser assertions and 48 transaction/reporting matrix executions | elapsed=0.93 peak_rss_kib=36380 exit=0 |
| P4b-1A focused | 9/9 tests, 78 generated Python streams and 780 updates | elapsed=35.62 peak_rss_kib=92076 exit=0 |
| Native reconstruction | 6 streams, 120 committed, 12 aborted, 144 full-cell checks | elapsed=0.00 peak_rss_kib=5560 exit=0 |
| ASan + UBSan parser target | 25/25 assertions | elapsed=0.00 peak_rss_kib=10012 exit=0 |
| UBSan native reconstruction | 6 streams, 120 committed, 12 aborted, 144 full-cell checks | elapsed=0.00 peak_rss_kib=7408 exit=0 |
| Full repository unittest discovery | Discovered 772. Passed 772. Skipped 0. Failures 0. Errors 0. Unittest wall 277.834 s. | elapsed=278.05 peak_rss_kib=92048 exit=0 |

The full discovery produced zero skips.

Exact commands and exits are in commands.log. Canonical result JSON files include attempted cases, provenance and binary hashes. The supplemental trace stores each input byte sequence, SHA-256, output JSON, argv, exit, elapsed and peak RSS. Full regression contains the repository's bounded correctness, containment and observer smoke tests. It supplied no new P4a/P4b benchmark results.

The full evidence bundle is local to the audit host at /tmp/rapidflow-far-elw8hm/review-evidence.tar.gz. Historical raw artifacts remain in D_INT03. Provenance checks compared both worktrees and the upstream pin. The old Independent Audit witness directory was read as input and preserved.

## Acceptance gates for independent review

PASS below denotes a developer verification result for the stated check.

| Gate | Status | Evidence |
|---|---|---|
| FAR-A01 | PASS | Original mismatch returns empty arrays across three modes. |
| FAR-A02 | PASS | Original equivalent witness returns [[0,1]] ENTERED and inverse EXITED in three modes. |
| FAR-A03 | PASS | Typed scalar tests and Triangle k=1 Unicode edge/update integration against both oracles. |
| FAR-A04 | PASS | Parser UNI-10–17 and three-mode malformed-input fail-before-update tests. |
| FAR-A05 | PASS | UNI-02–05, UNI-07, UNI-18, scalar boundaries and sanitizer runs. |
| FAR-B01 | PASS | Precommit fault matrix and successful following operations, P2 canonical and P4b focused tests. |
| FAR-B02 | PASS | COMMITTED survives all four reporting fault types on INSERT and DELETE. |
| FAR-B03 | PASS | Original postcommit witness and recovery-then-fatal test have exact UNATTEMPTED suffix. |
| FAR-B04 | PASS | A-static full-cell reconstruction at injected fault sites and canonical P4b reconstruction. |
| FAR-C01 | PASS | Original missing-edge DELETE retains first error category/index and exit 6. |
| FAR-C02 | PASS | Malformed, missing and non-object suffix cases retain complete step records. |
| FAR-C03 | PASS | Exact attempted/unattempted accounting and empty events in suffix tests. |
| FAR-D01 | PASS | Real startup and initialized negative runs, plus modified-output hand-case verification. |
| FAR-D02 | PASS | Initialized missing/unknown/contradictory payloads rejected. Native outcome retained. |
| FAR-D03 | PASS | Positive zero memory limit rejected at the subprocess boundary. |
| FAR-E01 | PASS | 25 hand, 56 streams/672 updates and 420 + 420 corpus, checked against unchanged registries. |
| FAR-E02 | PASS | Current CLI help and canonical matrix runs verify the documented selectors. |
| FAR-E03 | PASS | §8.3 specifies tuple/state/duplicates, A pruning counters and B traversal/counter parity. |
| FAR-F01 | PASS | Seven frozen files and historical binary unchanged, historical values in addendum. |
| FAR-F02 | PASS | Addendum records order 60/30, nested repeats, diagnostic timing and bounded interpretation. |
| FAR-F03 | PASS | Addendum distinguishes historical aggregates/attestation from same-binary replay. |
| FAR-G01 | PASS | Canonical runner JSON files passed every requested stage. |
| FAR-G02 | PASS | 78/780 Python and 6/120/12/144 native reconstruction inventory confirmed. |
| FAR-G03 | PASS | Full regression log and discovered/pass/skip/failure/error accounting above. |
| FAR-G04 | PASS | Test revision, exact clean-build binaries and toolchain bound in runner provenance. |
| FAR-G05 | PASS | Historical before/after equality and pristine pinned upstream. |
| FAR-G06 | PASS | git diff --check, clean tested source and remote implementation SHA synchronized through the approved push script. |
| FAR-G07 | PASS | Mandatory P4b-3 preregistration in §10 of the design. Existing decision thresholds retained. |

Independent review starts at Draft PR #39. Static features and foundation correctness are the scope of this handoff.
