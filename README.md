# Seminar_RapidFlow — NetFlow / CSM Research Workspace

Workspace for **reproducible research on RapidFlow (PVLDB 2022)** using **NetFlow**. This repo is independent from the team repository `seminar-csm`.

**Current Research Direction — Direction D (2026-11-18, Issue #28):**
The project is executing **Direction D: RapidFlow-APX Native Integration**, extending the original RapidFlow C++ codebase to support Approximate Continuous Subgraph Matching (APX CSM) under **Edge Recall** and **S2 connectivity** ($0 \le k < \lambda(Q)$).
- **Mandatory Native Integration:** The final deliverable requires genuine reuse of original RapidFlow components (Graph representation, Global Views, memory-efficient relation layouts, streaming pipelines) combined with an integrated APX update and search kernel. Standalone Python engines, uncoupled C++ ports, or count-only wrappers do not fulfill this objective.
- **Current Phase:** P0–P1 (Issue #28: contract audit, RapidFlow source audit, typed boundary/NLF probes, and Decision R1). Native production engine implementation begins in P2 upon R1 approval.
- **Foundational Assets (DSTAR-01–03, Completed):** DSTAR-01 established the working contract ([docs/dstar_problem_contract_v1.md](docs/dstar_problem_contract_v1.md)) and independent M0 Oracle ([scripts/dstar_oracle.py](scripts/dstar_oracle.py)). DSTAR-02 verified M1-enum-Exact-k ([scripts/dstar_m1_enum.py](scripts/dstar_m1_enum.py)) and bare M2-Event-k ([scripts/dstar_m2_event.py](scripts/dstar_m2_event.py)) across 840 corpus cases and 56 stream configurations ([docs/dstar02_validation_report.md](docs/dstar02_validation_report.md)). DSTAR-03 completed the 65-workload paired pilot (390 runs, [docs/dstar03_pilot_report.md](docs/dstar03_pilot_report.md)) confirming 100% M0 correctness.
- **Methodological Clarifications:** The pilot metric `budget_units` is an internal engine safety counter, not an invariant theoretical work metric; observed budget ratios ($W_{\text{M1}}/W_{\text{M2}} \approx 1.52$ in synthetic matrix) reflect disaggregated operation counting rather than proven general search work reduction. Zero events on tiny NetFlow probes ($|S| \le 8$) reflect localized event sparsity in tiny induced subgraphs, not a definitive proof that $|S| \ge 64$ is universally required. Legacy Model C work (APX-01–04) remains preserved as historical evidence. Advisor approval for Gate 0A remains pending.

## Bàn giao cho AI ở tài khoản khác

**Đọc [AI_PROJECT_HANDOFF.md](AI_PROJECT_HANDOFF.md)** để hiểu đầy đủ bài toán CSM, trạng thái nghiên cứu, phân công AI quản lý/AI Coding Agent/AI phản biện, quy trình Issue–Draft PR, bằng chứng cần nộp và quy tắc GitHub. File này là bản snapshot có thể tải rồi gửi cho một AI/tài khoản khác, không tự cấp quyền truy cập GitHub hoặc Linux. Đọc các Issue/PR mới nhất trước khi hành động.

## Map

| Directory | Purpose |
|---|---|
| `baselines/rapidflow/` | upstream source reference, immutable commit and build record |
| `external/` | local third-party source checkout(s), ignored by Git |
| `data/netflow/` | original dataset files (ignored); manifest and provenance |
| `data/queries/` | versioned queries and workload descriptions |
| `data/streams/` | update streams (ignored); reproducibility notes |
| `configs/` | experiment manifest templates |
| `scripts/` | inventory, execution wrapper, result capture |
| `tests/` | correctness integration and test-case notes |
| `results/raw/` | logs and time measurements (ignored) |
| `results/summary/` | reviewed summaries, never unverified measurements |
| `research/hypotheses/` | measurable bottleneck hypotheses |
| `research/prior_art/` | related work and novelty checks |
| `research/decisions/` | decisions and rejected approaches |
| `docs/` | experimental protocol, Codex instructions |

## Git push policy

**Branch policy v2 (2026-11-04):** Work only inside `iamqthanq/Seminar_RapidFlow`. Each approved Issue uses a new `feature/rfNN-*` branch based on `QuangThang`. Gemini can push **only** the active feature branch via `bash scripts/push_issue_branch.sh`, then create one Draft PR **into `QuangThang`** with `bash scripts/open_issue_pr.sh <ISSUE_NUMBER>`. Additional commits update the same PR. Agent must never push directly to `QuangThang` or `main` or merge any PR; user authorizes merges. See `AGENTS.md`, `GEMINI.md`, `docs/BRANCH_POLICY.md`. RF-01 was pushed directly before policy v2 and is reviewed via its existing commit.

## Workflow

1. Inventory existing local source and NetFlow before downloading or changing anything.
2. Pin original RapidFlow commit, record flags and exact dataset/query/update stream.
3. Verify correctness on small graphs (compare **actual mapping sets**, not just counts).
4. Run baseline with bounded resources and preserve runtime, RSS, logs, exit status.
5. Profile: separate index maintenance, search, enumeration, and output costs. Identify unavoidable output explosion.
6. Record a falsifiable hypothesis and check prior art before changing the algorithm.
7. Submit code, tests, configuration and results in a PR; reviewer decides, never self-merge.

## Commands

```bash
python3 scripts/inventory.py
python3 scripts/run_case.py --help
```

Run wrapper only once a real executable and workload exist. **The runner does not assume RapidFlow's CLI flags; provide an explicitly reviewed command.** See `docs/RUN_PROTOCOL.md`.

No source, dataset, benchmark numbers or research claim is generated by this scaffold.
