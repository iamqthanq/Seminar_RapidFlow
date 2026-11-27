# RapidFlow-APX P4b-3B.0 R2 Candidate Binary Recertification

- **Native C++ revision (`NATIVE_SOURCE_COMMIT`):** `115ce2844d91f6a6588fe0ac50b1179bc3b38012`
- **Candidate Binary SHA-256:** `3fa35111acd7e7527a80bae6b0caa6d51d1e283d9848747a62c2517d696d1876` (`970,632` bytes)
- **Preserved Historical R1 Binary SHA-256:** `f02e6ff99c707407d88fa16ebd370e9c43d4359b8192247e394b95cd607d3d75` (`941,216` bytes, untouched archive `22b5245d...`)

## 1. P3 Recertification Matrix (`build-r2-candidate/rapidflow_apx_native`)

| Matrix Configuration | Hand Cases | Corpus 840 | 56 Streams (`672` Updates) | Outcome |
|---|---|---|---|---|
| `all_none_legacy` (`M1` + `M2`) | `25 / 25` | `M1=840`, `M2=840` | `M1=672`, `M2=672` | `PASS` |
| `m2_static_legacy` | `25 / 25` | `M2=840` | `M2=672` | `PASS` |
| `m2_residual_legacy` | `25 / 25` | `M2=840` | `M2=672` | `PASS` |
| `m2_none_buffer` | `25 / 25` | `M2=840` | `M2=672` | `PASS` |
| `m2_none_merge` | `25 / 25` | `M2=840` | `M2=672` | `PASS` |

## 2. 250-Run Candidate Correctness Replay & Candidate Registry

Command executed against committed C++ source `115ce2844d91f6a6588fe0ac50b1179bc3b38012`:
- `python3 scripts/replay_rapidflow_apx_p4b.py --candidate --binary tests/native/rapidflow_apx_engines/build-r2-candidate/rapidflow_apx_native --output <run_dir>`
- Output artifact in repo: `configs/rapidflow_apx_p4b_r2_candidate_registry.json` (`sha256 = 90fc2f3ee1e8c4bcf210603caeb218190a294b168377858cd4be276ebb662118`).

| Metric / Tier | Historical R1 (`f02e6ff9...`) | R2 Candidate (`3fa35111...`) |
|---|---|---|
| Total native runs (`50` workloads × `5` variants) | `250` | `250` |
| `M0_VERIFIED` | `160` | `160` |
| `DIFFERENTIAL_M2_VERIFIED` | `64` | `64` |
| `CORRECTNESS_NOT_FULLY_CERTIFIED` | `16` | `16` |
| `C3_BUDGET_UNAVAILABLE` | `10` | `10` |
| Oracle-unavailable workloads | `18` | `18` |

## 3. Native ASan + UBSan Cleanliness

- Built at `tests/native/rapidflow_apx_engines/build-r2-asan` with `-fsanitize=address,undefined -fno-omit-frame-pointer -O1` and run with `ASAN_OPTIONS=detect_leaks=0`.
- All 6 native verification suites (`--test-empty-frontier`, `--test-p4b all`, `--test-p4b-residual numeric`, `--test-p4b-neighbor graph`, `--test-p4b-measurement`, `rapidflow_apx_json_strings`) passed cleanly (`SANITIZERS_PASS`).
