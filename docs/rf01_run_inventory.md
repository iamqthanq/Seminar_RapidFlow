# RF-01: Run Inventory & Experimental History

Tài liệu này ghi nhận đầy đủ, minh bạch và có hệ thống toàn bộ lịch sử các lần chạy thực nghiệm trong khuôn khổ Issue #2 (RF-01 - Gate G0), nhằm giải quyết yêu cầu **R1** và các điều chỉnh sau đợt review độc lập.

---

## 1. Phân loại các đợt chạy (Run Categorization)

Theo đúng quy chuẩn nghiên cứu khoa học và protocol dự án:
- **Preliminary Runs (Chạy sơ bộ):** Các lần chạy thăm dò kỹ thuật, kiểm tra tích hợp harness (`scripts/run_case.py`), kiểm tra định dạng tài nguyên/logs trước khi kích hoạt bộ test tự động.
- **Official Smoke Suite - Batch 1 (Bộ smoke chính thức đợt 1):** Bộ 6 ca kiểm thử chạy tự động qua `scripts/rf01_smoke_runner.py` tại commit `d358082`.
- **Independent Reviewer Replays (Chạy đối chứng độc lập):** Các lượt chạy độc lập do Reviewer (ChatGPT) tự thực hiện trực tiếp từ dòng lệnh (không qua runner của agent) trên môi trường Linux (đợt 1: 2 cases; đợt 2: đủ 6 cases).
- **Post-Fix Revalidation Suite - Batch 2:** Bộ 6 ca kiểm thử chạy lại qua `scripts/rf01_smoke_runner.py` sau đợt sửa lỗi R1–R5 (commit `47faf49`).
- **Final Revalidation Suite - Batch 3 (Sau F1 & F2):** Bộ 6 ca kiểm thử chạy qua runner nâng cấp với kiểm tra reference fail-closed (SHA-256 + format) và đánh giá `evidence_status = COMPLETE_METRICS`.

> [!NOTE]
> Các lần chạy này phục vụ kiểm định kỹ thuật (smoke test / build sanity) và kiểm tra tính vững chắc của runner. Chúng **không phải** là benchmark lặp lại nhiều lần (repeated benchmark) hay thử nghiệm đo lường hiệu năng của Gate G2/G3.

---

## 2. Bảng kê chi tiết toàn bộ các lượt chạy

| STT | Đợt chạy (Category) | Run ID / Thư mục log | Case ID | Stream | Query | Exit Code | Result Count | Expected | Match | Wall Time (s) | Peak RSS (KB) | Evidence Status | Ghi chú |
|:---:|---|---|---|:---:|:---:|:---:|---:|---:|:---:|---:|---:|:---:|---|
| 1 | **Preliminary Dry-Run** | `rf01_smoke_insert_Q0/20261104T093452Z_7ec174bf` | `rf01_smoke_insert_Q0` | Insert | Q_0 | 0 | 587,884 | 587,884 | True | 3.52 | 265,048 | COMPLETE_METRICS | Chạy đơn lẻ kiểm tra CLI `run_case.py` & đo tài nguyên |
| 2 | **Official Suite (Batch 1)** | `rf01_smoke_insert_Q0/20261104T093638Z_eb172ff5` | `rf01_smoke_insert_Q0` | Insert | Q_0 | 0 | 587,884 | 587,884 | True | 3.32 | 265,288 | COMPLETE_METRICS | Chạy tự động đợt 1 |
| 3 | **Official Suite (Batch 1)** | `rf01_smoke_insert_Q1/20261104T093642Z_5a3ab215` | `rf01_smoke_insert_Q1` | Insert | Q_1 | 0 | 12,866,379 | 12,866,379 | True | 3.82 | 277,940 | COMPLETE_METRICS | Chạy tự động đợt 1 |
| 4 | **Official Suite (Batch 1)** | `rf01_smoke_insert_Q2/20261104T093646Z_42c1e6b6` | `rf01_smoke_insert_Q2` | Insert | Q_2 | 0 | 1,139,364 | 1,139,364 | True | 3.07 | 255,428 | COMPLETE_METRICS | Chạy tự động đợt 1 |
| 5 | **Official Suite (Batch 1)** | `rf01_smoke_delete_Q0/20261104T093649Z_88cade3d` | `rf01_smoke_delete_Q0` | Delete | Q_0 | 0 | 587,984 | 587,984 | True | 3.82 | 332,752 | COMPLETE_METRICS | Chạy tự động đợt 1 |
| 6 | **Official Suite (Batch 1)** | `rf01_smoke_delete_Q1/20261104T093653Z_3841f022` | `rf01_smoke_delete_Q1` | Delete | Q_1 | 0 | 12,864,072 | 12,864,072 | True | 4.28 | 341,940 | COMPLETE_METRICS | Chạy tự động đợt 1 |
| 7 | **Official Suite (Batch 1)** | `rf01_smoke_delete_Q2/20261104T093658Z_26cb8eab` | `rf01_smoke_delete_Q2` | Delete | Q_2 | 0 | 1,140,656 | 1,140,656 | True | 3.57 | 321,820 | COMPLETE_METRICS | Chạy tự động đợt 1 |
| 8 | **Reviewer Direct Replay (1)**| `/tmp/rf01_independent_review__n0myf25` | `manual_insert_Q0` | Insert | Q_0 | 0 | 587,884 | 587,884 | True | 3.42 | - | - | Reviewer ChatGPT chạy trực tiếp binary |
| 9 | **Reviewer Direct Replay (1)**| `/tmp/rf01_independent_review__n0myf25` | `manual_delete_Q0` | Delete | Q_0 | 0 | 587,984 | 587,984 | True | 3.88 | - | - | Reviewer ChatGPT chạy trực tiếp binary |
| 10 | **Post-Fix Suite (Batch 2)** | `rf01_smoke_insert_Q0/20261104T104252Z_5f2f0e17` | `rf01_smoke_insert_Q0` | Insert | Q_0 | 0 | 587,884 | 587,884 | True | 3.52 | 265,368 | COMPLETE_METRICS | Chạy lại sau khi sửa R4, verdict: `PASS_VERIFIED` |
| 11 | **Post-Fix Suite (Batch 2)** | `rf01_smoke_insert_Q1/20261104T104255Z_2a4a7a9e` | `rf01_smoke_insert_Q1` | Insert | Q_1 | 0 | 12,866,379 | 12,866,379 | True | 4.12 | 277,924 | COMPLETE_METRICS | Chạy lại sau khi sửa R4, verdict: `PASS_VERIFIED` |
| 12 | **Post-Fix Suite (Batch 2)** | `rf01_smoke_insert_Q2/20261104T104300Z_16ba471d` | `rf01_smoke_insert_Q2` | Insert | Q_2 | 0 | 1,139,364 | 1,139,364 | True | 3.07 | 255,536 | COMPLETE_METRICS | Chạy lại sau khi sửa R4, verdict: `PASS_VERIFIED` |
| 13 | **Post-Fix Suite (Batch 2)** | `rf01_smoke_delete_Q0/20261104T104303Z_59000853` | `rf01_smoke_delete_Q0` | Delete | Q_0 | 0 | 587,984 | 587,984 | True | 3.92 | 332,652 | COMPLETE_METRICS | Chạy lại sau khi sửa R4, verdict: `PASS_VERIFIED` |
| 14 | **Post-Fix Suite (Batch 2)** | `rf01_smoke_delete_Q1/20261104T104307Z_d0d1ddb9` | `rf01_smoke_delete_Q1` | Delete | Q_1 | 0 | 12,864,072 | 12,864,072 | True | 4.48 | 341,848 | COMPLETE_METRICS | Chạy lại sau khi sửa R4, verdict: `PASS_VERIFIED` |
| 15 | **Post-Fix Suite (Batch 2)** | `rf01_smoke_delete_Q2/20261104T104312Z_1ea1c9c4` | `rf01_smoke_delete_Q2` | Delete | Q_2 | 0 | 1,140,656 | 1,140,656 | True | 3.62 | 321,908 | COMPLETE_METRICS | Chạy lại sau khi sửa R4, verdict: `PASS_VERIFIED` |
| 16 | **Reviewer Direct Replay (2)**| `/tmp/rf01_pr6_independent_replay_vqlh0qnv` | `manual_insert_Q0` | Insert | Q_0 | 0 | 587,884 | 587,884 | True | 3.42 | - | - | Reviewer ChatGPT chạy trực tiếp 6 cases |
| 17 | **Reviewer Direct Replay (2)**| `/tmp/rf01_pr6_independent_replay_vqlh0qnv` | `manual_insert_Q1` | Insert | Q_1 | 0 | 12,866,379 | 12,866,379 | True | 3.97 | - | - | Reviewer ChatGPT chạy trực tiếp 6 cases |
| 18 | **Reviewer Direct Replay (2)**| `/tmp/rf01_pr6_independent_replay_vqlh0qnv` | `manual_insert_Q2` | Insert | Q_2 | 0 | 1,139,364 | 1,139,364 | True | 3.12 | - | - | Reviewer ChatGPT chạy trực tiếp 6 cases |
| 19 | **Reviewer Direct Replay (2)**| `/tmp/rf01_pr6_independent_replay_vqlh0qnv` | `manual_delete_Q0` | Delete | Q_0 | 0 | 587,984 | 587,984 | True | 3.87 | - | - | Reviewer ChatGPT chạy trực tiếp 6 cases |
| 20 | **Reviewer Direct Replay (2)**| `/tmp/rf01_pr6_independent_replay_vqlh0qnv` | `manual_delete_Q1` | Delete | Q_1 | 0 | 12,864,072 | 12,864,072 | True | 4.37 | - | - | Reviewer ChatGPT chạy trực tiếp 6 cases |
| 21 | **Reviewer Direct Replay (2)**| `/tmp/rf01_pr6_independent_replay_vqlh0qnv` | `manual_delete_Q2` | Delete | Q_2 | 0 | 1,140,656 | 1,140,656 | True | 3.67 | - | - | Reviewer ChatGPT chạy trực tiếp 6 cases |
| 22 | **Final Suite (Batch 3)** | `rf01_smoke_insert_Q0/20261104T111300Z_6522e2fd` | `rf01_smoke_insert_Q0` | Insert | Q_0 | 0 | 587,884 | 587,884 | True | 3.57 | 265,192 | COMPLETE_METRICS | Sau sửa F1/F2: `PASS_VERIFIED` + `COMPLETE_METRICS` |
| 23 | **Final Suite (Batch 3)** | `rf01_smoke_insert_Q1/20261104T111303Z_64ec6e80` | `rf01_smoke_insert_Q1` | Insert | Q_1 | 0 | 12,866,379 | 12,866,379 | True | 4.22 | 277,968 | COMPLETE_METRICS | Sau sửa F1/F2: `PASS_VERIFIED` + `COMPLETE_METRICS` |
| 24 | **Final Suite (Batch 3)** | `rf01_smoke_insert_Q2/20261104T111308Z_3f2686e9` | `rf01_smoke_insert_Q2` | Insert | Q_2 | 0 | 1,139,364 | 1,139,364 | True | 3.32 | 255,664 | COMPLETE_METRICS | Sau sửa F1/F2: `PASS_VERIFIED` + `COMPLETE_METRICS` |
| 25 | **Final Suite (Batch 3)** | `rf01_smoke_delete_Q0/20261104T111311Z_8ce85b8e` | `rf01_smoke_delete_Q0` | Delete | Q_0 | 0 | 587,984 | 587,984 | True | 4.02 | 332,516 | COMPLETE_METRICS | Sau sửa F1/F2: `PASS_VERIFIED` + `COMPLETE_METRICS` |
| 26 | **Final Suite (Batch 3)** | `rf01_smoke_delete_Q1/20261104T111316Z_675dc058` | `rf01_smoke_delete_Q1` | Delete | Q_1 | 0 | 12,864,072 | 12,864,072 | True | 4.53 | 341,836 | COMPLETE_METRICS | Sau sửa F1/F2: `PASS_VERIFIED` + `COMPLETE_METRICS` |
| 27 | **Final Suite (Batch 3)** | `rf01_smoke_delete_Q2/20261104T111320Z_2ff4a6a7` | `rf01_smoke_delete_Q2` | Delete | Q_2 | 0 | 1,140,656 | 1,140,656 | True | 3.82 | 321,868 | COMPLETE_METRICS | Sau sửa F1/F2: `PASS_VERIFIED` + `COMPLETE_METRICS` |

---

## 3. Tính toàn vẹn của bằng chứng
1. **Không xóa bất kỳ log nào:** Toàn bộ 19 thư mục con trong `results/raw/` được giữ nguyên vẹn 100%.
2. **Khớp kết quả 100%:** Cả 27 lượt chạy (preliminary, batch 1, reviewer replays đợt 1 và đợt 2, batch 2, batch 3) đều cho ra mã thoát `0`, không có stderr, và `Result count` trùng khớp tuyệt đối với file đáp án tham chiếu của tác giả thượng nguồn.
3. **Phân định rõ ràng:** Đã phân loại minh bạch giữa Preliminary dry-run, Official batches, và Independent Reviewer replays.
