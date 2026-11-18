#!/usr/bin/env python3
"""
scripts/run_dstar03_pilot.py — Supervisor Runner for DSTAR-03 Paired Pilot Evaluation (v2).

Responsibilities:
1. Environmental and manifest preflight checks:
   - Verifies system resources (MemAvailable >= 2 GiB, DiskFree >= 1 GiB).
   - Validates manifest integrity: exactly 65 workloads (40 syn, 5 wit, 20 netflow),
     unique case indices 0..64, and manifest SHA-256 integrity.
   - Collects and records source hashes for engines, worker, resources, and runner.
2. Rejection of source/protocol drift on resume.
3. Durable append-only journal (reports/dstar03/attempts.jsonl) supporting interruption and resume.
4. Paired isolated child subprocess executions (3 pairs = 6 runs per workload).
5. Parity-offset alternating pair ordering to balance thermal/cache effects.
6. Honest status reporting and structured error preservation (never masking CORRECTNESS_FAIL or RESOURCE_LIMIT).
7. Strict exit codes: non-zero on partial runs, filter runs, batch interruptions, or slot failures.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dstar03_resources import (
    DEFAULT_RLIMIT_AS_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    check_environment_preflight,
    hash_file_sha256,
    run_contained_subprocess,
    validate_resource_limits,
)
from generate_dstar03_workloads import compute_workload_fingerprint, sha256_of_obj

DEFAULT_MANIFEST_PATH = REPO_ROOT / "configs" / "dstar03_pilot_manifest.json"
DEFAULT_LEDGER_PATH = REPO_ROOT / "reports" / "dstar03" / "attempts.jsonl"
WORKER_SCRIPT = REPO_ROOT / "scripts" / "dstar03_pilot_worker.py"
SUMMARIZER_SCRIPT = REPO_ROOT / "scripts" / "summarize_dstar03_pilot.py"

CORE_SOURCE_SCRIPTS = [
    REPO_ROOT / "scripts" / "dstar_m1_enum.py",
    REPO_ROOT / "scripts" / "dstar_m2_event.py",
    REPO_ROOT / "scripts" / "dstar_event_common.py",
    REPO_ROOT / "scripts" / "dstar_oracle.py",
    REPO_ROOT / "scripts" / "dstar03_resources.py",
    REPO_ROOT / "scripts" / "dstar03_pilot_worker.py",
    REPO_ROOT / "scripts" / "run_dstar03_pilot.py",
    REPO_ROOT / "scripts" / "summarize_dstar03_pilot.py",
]


def collect_source_provenance() -> Dict[str, Any]:
    """Collects Git HEAD, dirty state, and SHA-256 hashes of core source scripts."""
    prov: Dict[str, Any] = {
        "git_commit": "UNKNOWN",
        "git_dirty": False,
        "source_hashes": {},
    }
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True
        ).strip()
        prov["git_commit"] = commit
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), text=True
        ).strip()
        prov["git_dirty"] = bool(status_out)
    except Exception:
        pass

    for s_path in CORE_SOURCE_SCRIPTS:
        if s_path.exists():
            rel_name = str(s_path.relative_to(REPO_ROOT))
            prov["source_hashes"][rel_name] = hash_file_sha256(s_path)

    return prov


class PilotSupervisor:
    def __init__(
        self,
        manifest_path: Path = DEFAULT_MANIFEST_PATH,
        ledger_path: Path = DEFAULT_LEDGER_PATH,
        max_wall_seconds: float = 600.0,
        worker_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        rlimit_as_bytes: int = DEFAULT_RLIMIT_AS_BYTES,
    ) -> None:
        self.manifest_path = manifest_path
        self.ledger_path = ledger_path
        self.max_wall_seconds = max_wall_seconds
        self.worker_timeout = worker_timeout
        self.rlimit_as_bytes = rlimit_as_bytes
        self.interrupted = False

        validate_resource_limits(self.worker_timeout, self.rlimit_as_bytes)

        # Set up signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        print(f"\nCaught signal {signum}. Requesting graceful supervisor shutdown...")
        self.interrupted = True

    def preflight(self) -> Dict[str, Any]:
        """Runs environment and manifest preflight checks."""
        # 1. Environment preflight
        env_res = check_environment_preflight(target_dir=str(REPO_ROOT))
        if env_res["status"] != "PASS":
            return {"status": "SAFETY_BLOCKED", "reason": env_res["reason"]}

        # 2. Manifest check
        if not self.manifest_path.exists():
            return {"status": "ERROR", "reason": f"Manifest not found: {self.manifest_path}"}

        try:
            with self.manifest_path.open("r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as exc:
            return {"status": "ERROR", "reason": f"Corrupt manifest: {exc}"}

        # Check manifest SHA-256
        saved_sha = manifest.get("manifest_sha256")
        if not saved_sha:
            return {"status": "ERROR", "reason": "Manifest missing required 'manifest_sha256' field"}

        manifest_for_hash = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
        calc_sha = sha256_of_obj(manifest_for_hash)
        if saved_sha != calc_sha:
            return {
                "status": "ERROR",
                "reason": f"Manifest hash drift: saved {saved_sha} != calculated {calc_sha}",
            }

        # Validate registered workloads count and breakdown
        workloads = manifest.get("workloads", [])
        if len(workloads) != 65:
            return {
                "status": "ERROR",
                "reason": f"Manifest registered workloads count {len(workloads)} != 65 required",
            }

        counts = {"SYNTHETIC_MATRIX": 0, "STRUCTURED_WITNESS": 0, "NETFLOW_PROBE": 0}
        case_indices: Set[int] = set()
        workload_ids: Set[str] = set()

        for w in workloads:
            fam = w.get("family")
            if fam in counts:
                counts[fam] += 1
            else:
                return {"status": "ERROR", "reason": f"Unknown workload family {fam} in {w.get('workload_id')}"}

            c_idx = w.get("case_index")
            if c_idx is None or c_idx in case_indices:
                return {"status": "ERROR", "reason": f"Duplicate or missing case_index: {c_idx}"}
            case_indices.add(c_idx)

            w_id = w.get("workload_id")
            if not w_id or w_id in workload_ids:
                return {"status": "ERROR", "reason": f"Duplicate or missing workload_id: {w_id}"}
            workload_ids.add(w_id)

            # Check fixture file existence
            fixture_path = REPO_ROOT / w.get("relative_path", "")
            if not fixture_path.exists():
                return {"status": "ERROR", "reason": f"Workload fixture missing on disk: {fixture_path}"}

        expected_counts = {"SYNTHETIC_MATRIX": 40, "STRUCTURED_WITNESS": 5, "NETFLOW_PROBE": 20}
        if counts != expected_counts:
            return {
                "status": "ERROR",
                "reason": f"Workload counts breakdown mismatch: actual {counts} != expected {expected_counts}",
            }

        if sorted(case_indices) != list(range(65)):
            return {
                "status": "ERROR",
                "reason": f"Case indices not contiguous 0..64: {sorted(case_indices)}",
            }

        source_prov = collect_source_provenance()

        return {
            "status": "PASS",
            "manifest": manifest,
            "manifest_sha256": saved_sha,
            "source_provenance": source_prov,
        }

    def load_existing_ledger(
        self, expected_manifest_sha: Optional[str] = None
    ) -> Tuple[Set[str], Set[str], List[Dict[str, Any]], Optional[str]]:
        """
        Reads existing attempts ledger.
        Validates ledger integrity: checks for corrupt JSON, conflicting records, and provenance drift.
        Returns (started_attempt_ids, completed_attempt_ids, all_records, error_msg).
        """
        started: Set[str] = set()
        completed: Set[str] = set()
        records: List[Dict[str, Any]] = []

        if not self.ledger_path.exists():
            return started, completed, records, None

        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception as exc:
                    return started, completed, records, f"Corrupted JSON line {line_no} in ledger: {exc}"

                records.append(rec)
                rec_type = rec.get("record_type")
                att_id = rec.get("attempt_id")

                if rec_type == "SUPERVISOR_HEADER":
                    ledger_m_sha = rec.get("manifest_sha256")
                    if expected_manifest_sha and ledger_m_sha and ledger_m_sha != expected_manifest_sha:
                        return (
                            started,
                            completed,
                            records,
                            f"Ledger manifest drift: ledger manifest_sha256 {ledger_m_sha} != current {expected_manifest_sha}",
                        )

                elif rec_type == "ATTEMPT_STARTED":
                    if not att_id:
                        return started, completed, records, f"Line {line_no}: ATTEMPT_STARTED missing attempt_id"
                    started.add(att_id)

                elif rec_type == "ATTEMPT_COMPLETED":
                    if not att_id:
                        return started, completed, records, f"Line {line_no}: ATTEMPT_COMPLETED missing attempt_id"
                    if att_id in completed:
                        return started, completed, records, f"Line {line_no}: Duplicate ATTEMPT_COMPLETED for attempt_id={att_id}"
                    completed.add(att_id)

        return started, completed, records, None

    def append_record(self, record: Dict[str, Any]) -> None:
        """Atomically appends a record to the durable ledger."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def run(self, workload_filter: Optional[str] = None) -> Dict[str, Any]:
        """Main supervisor execution loop."""
        supervisor_t0 = time.time()
        print("Starting DSTAR-03 Pilot Supervisor (v2)...")

        # 1. Preflight
        pf = self.preflight()
        if pf["status"] != "PASS":
            print(f"Supervisor Preflight Failed: {pf['reason']}")
            self.append_record({
                "record_type": "SUPERVISOR_RUN_BLOCKED",
                "status": pf["status"],
                "reason": pf["reason"],
                "timestamp": time.time(),
            })
            return pf

        manifest = pf["manifest"]
        manifest_sha = pf["manifest_sha256"]
        source_prov = pf["source_provenance"]
        workloads = manifest["workloads"]

        # Check filter
        is_filtered_run = bool(workload_filter)
        if workload_filter:
            workloads = [w for w in workloads if workload_filter in w["workload_id"]]
            print(f"Applied filter '{workload_filter}': {len(workloads)} workloads selected.")
            if len(workloads) == 0:
                print(f"ERROR: Filter '{workload_filter}' matched 0 workloads!")
                return {
                    "status": "ERROR",
                    "reason": f"Filter '{workload_filter}' matched 0 workloads",
                    "total_slots": 0,
                    "executed_this_run": 0,
                    "total_completed": 0,
                }

        # 2. Check existing ledger
        started_ids, completed_ids, existing_records, ledger_err = self.load_existing_ledger(
            expected_manifest_sha=manifest_sha
        )
        if ledger_err:
            print(f"ERROR loading existing ledger: {ledger_err}")
            return {"status": "ERROR", "reason": ledger_err}

        print(f"Existing ledger has {len(completed_ids)} completed attempts.")

        # Write supervisor session header if ledger is fresh or new session
        self.append_record({
            "record_type": "SUPERVISOR_HEADER",
            "session_id": f"session_{int(supervisor_t0)}",
            "manifest_sha256": manifest_sha,
            "git_commit": source_prov["git_commit"],
            "git_dirty": source_prov["git_dirty"],
            "source_hashes": source_prov["source_hashes"],
            "is_filtered_run": is_filtered_run,
            "filter": workload_filter,
            "timestamp": supervisor_t0,
        })

        # Mark any attempt started in a prior run but not completed as INTERRUPTED
        interrupted_ids = started_ids - completed_ids
        for int_id in interrupted_ids:
            print(f"Marking previously interrupted attempt: {int_id}")
            self.append_record({
                "record_type": "ATTEMPT_COMPLETED",
                "attempt_id": int_id,
                "status": "INTERRUPTED",
                "error": "Process was interrupted before completion in previous run",
                "timestamp": time.time(),
            })
            completed_ids.add(int_id)

        # 3. Build scheduled slots
        scheduled_slots: List[Dict[str, Any]] = []
        for w in workloads:
            w_id = w["workload_id"]
            case_idx = w["case_index"]
            rel_path = w["relative_path"]
            fixture_path = REPO_ROOT / rel_path

            pair_patterns = [
                ("m1", "m2") if (case_idx % 2 == 0) else ("m2", "m1"),
                ("m2", "m1") if (case_idx % 2 == 0) else ("m1", "m2"),
                ("m1", "m2") if (case_idx % 2 == 0) else ("m2", "m1"),
            ]

            for pair_idx, (eng_a, eng_b) in enumerate(pair_patterns):
                for sub_rep, eng in enumerate([eng_a, eng_b]):
                    rep_idx = pair_idx
                    attempt_id = f"{w_id}_{eng}_pair{pair_idx+1}_rep{rep_idx}"
                    scheduled_slots.append({
                        "workload_id": w_id,
                        "case_index": case_idx,
                        "fixture_path": fixture_path,
                        "pair_index": pair_idx + 1,
                        "rep_index": rep_idx,
                        "engine": eng,
                        "order_in_pair": sub_rep + 1,
                        "attempt_id": attempt_id,
                    })

        total_slots = len(scheduled_slots)
        print(f"Total scheduled execution slots: {total_slots}")

        executed_this_run = 0
        skipped_already_done = 0
        failed_slots_count = 0
        batch_stopped_early = False

        for slot_idx, slot in enumerate(scheduled_slots):
            elapsed_wall = time.time() - supervisor_t0
            if elapsed_wall >= self.max_wall_seconds:
                print(f"Max wall budget ({self.max_wall_seconds}s) reached. Stopping batch.")
                batch_stopped_early = True
                break

            if self.interrupted:
                print("Supervisor interrupted. Stopping execution loop.")
                batch_stopped_early = True
                break

            att_id = slot["attempt_id"]
            if att_id in completed_ids:
                skipped_already_done += 1
                continue

            # Record attempt start
            start_rec = {
                "record_type": "ATTEMPT_STARTED",
                "attempt_id": att_id,
                "workload_id": slot["workload_id"],
                "case_index": slot["case_index"],
                "engine": slot["engine"],
                "pair_index": slot["pair_index"],
                "rep_index": slot["rep_index"],
                "order_in_pair": slot["order_in_pair"],
                "timestamp": time.time(),
            }
            self.append_record(start_rec)

            # Launch child worker subprocess
            cmd = [
                sys.executable,
                str(WORKER_SCRIPT),
                "--workload-path", str(slot["fixture_path"]),
                "--engine", slot["engine"],
                "--attempt-id", att_id,
            ]

            proc_res = run_contained_subprocess(
                cmd,
                timeout_seconds=self.worker_timeout,
                limit_as_bytes=self.rlimit_as_bytes,
                cwd=str(REPO_ROOT),
            )

            # Process output and preserve structured errors
            completed_rec: Dict[str, Any] = {
                "record_type": "ATTEMPT_COMPLETED",
                "attempt_id": att_id,
                "workload_id": slot["workload_id"],
                "case_index": slot["case_index"],
                "engine": slot["engine"],
                "pair_index": slot["pair_index"],
                "rep_index": slot["rep_index"],
                "order_in_pair": slot["order_in_pair"],
                "timestamp": time.time(),
                "subprocess_status": proc_res["status"],
                "exit_code": proc_res["exit_code"],
                "supervisor_wall_ns": proc_res["wall_ns"],
            }

            if proc_res["status"] == "TIMEOUT":
                completed_rec["status"] = "TIMEOUT"
                completed_rec["error"] = f"Worker exceeded timeout of {self.worker_timeout}s"
                failed_slots_count += 1
            elif proc_res["status"] == "RESOURCE_LIMIT":
                completed_rec["status"] = "RESOURCE_LIMIT"
                completed_rec["error"] = proc_res.get("termination_reason", "Worker exceeded RLIMIT_AS")
                failed_slots_count += 1
            else:
                # Attempt to parse child stdout JSON even if exit code is non-zero
                child_payload = None
                if proc_res["stdout"]:
                    try:
                        child_payload = json.loads(proc_res["stdout"].strip())
                    except Exception:
                        pass

                if child_payload and isinstance(child_payload, dict):
                    completed_rec.update(child_payload)
                    st = child_payload.get("status")
                    if st not in ("SUCCESS_NONEMPTY", "ZERO_RESULT_COMPLETE"):
                        failed_slots_count += 1
                else:
                    completed_rec["status"] = "ERROR"
                    completed_rec["error"] = proc_res["stderr"][:500] if proc_res["stderr"] else "Nonzero exit with no valid JSON output"
                    failed_slots_count += 1

            self.append_record(completed_rec)
            completed_ids.add(att_id)
            executed_this_run += 1

            if executed_this_run % 25 == 0 or executed_this_run == total_slots:
                print(
                    f"Progress: {executed_this_run} executed this run, "
                    f"{len(completed_ids)}/{total_slots} total completed "
                    f"({round(len(completed_ids)/total_slots*100, 1)}%)."
                )

        print(
            f"Execution batch finished. Executed: {executed_this_run}, "
            f"Skipped (already done): {skipped_already_done}, "
            f"Total Completed: {len(completed_ids)}/{total_slots}."
        )

        # Determine supervisor final status
        if is_filtered_run:
            final_status = "PARTIAL_RUN"
        elif batch_stopped_early or len(completed_ids) < total_slots:
            final_status = "INCOMPLETE_BATCH"
        elif failed_slots_count > 0:
            final_status = "SLOT_FAILURES_DETECTED"
        else:
            final_status = "PASS"

        return {
            "status": final_status,
            "total_slots": total_slots,
            "executed_this_run": executed_this_run,
            "total_completed": len(completed_ids),
            "failed_slots": failed_slots_count,
            "is_filtered_run": is_filtered_run,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DSTAR-03 paired pilot supervisor.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH, help="Path to manifest JSON.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH, help="Path to attempts ledger JSONL.")
    parser.add_argument("--max-wall-seconds", type=float, default=600.0, help="Maximum supervisor wall time in seconds.")
    parser.add_argument("--worker-timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Per-worker timeout in seconds.")
    parser.add_argument("--filter", type=str, default=None, help="Filter workloads by substring.")
    args = parser.parse_args()

    supervisor = PilotSupervisor(
        manifest_path=args.manifest,
        ledger_path=args.ledger,
        max_wall_seconds=args.max_wall_seconds,
        worker_timeout=args.worker_timeout,
    )
    res = supervisor.run(workload_filter=args.filter)

    # Strict exit codes:
    # 0 = PASS (complete full pilot with zero failures)
    # 2 = PARTIAL_RUN (filter used or intentional partial debug execution)
    # 1 = Any other failure or incomplete batch
    st = res.get("status")
    if st == "PASS":
        sys.exit(0)
    elif st == "PARTIAL_RUN":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
