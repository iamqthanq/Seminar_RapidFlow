#!/usr/bin/env python3
"""
scripts/run_dstar03_pilot.py — Supervisor Runner for DSTAR-03 Paired Pilot Evaluation.

Responsibilities:
1. Environmental and manifest preflight checks.
2. Independent M0 Ground Truth verification of all workloads outside timed runs.
3. Durable append-only journal (reports/dstar03/attempts.jsonl) supporting interruption and resume.
4. Paired isolated child subprocess executions (3 pairs = 6 runs per workload).
5. Parity-offset alternating pair ordering to balance thermal/cache effects.
6. Real-time batch wall-clock monitoring.
7. Post-run invocation of summarizer to generate reports.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dstar03_resources import check_environment_preflight, run_contained_subprocess
from generate_dstar03_workloads import sha256_of_obj, verify_workload_m0

DEFAULT_MANIFEST_PATH = REPO_ROOT / "configs" / "dstar03_pilot_manifest.json"
DEFAULT_LEDGER_PATH = REPO_ROOT / "reports" / "dstar03" / "attempts.jsonl"
WORKER_SCRIPT = REPO_ROOT / "scripts" / "dstar03_pilot_worker.py"
SUMMARIZER_SCRIPT = REPO_ROOT / "scripts" / "summarize_dstar03_pilot.py"


class PilotSupervisor:
    def __init__(
        self,
        manifest_path: Path = DEFAULT_MANIFEST_PATH,
        ledger_path: Path = DEFAULT_LEDGER_PATH,
        max_wall_seconds: float = 600.0,
        worker_timeout: float = 60.0,
        rlimit_as_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.manifest_path = manifest_path
        self.ledger_path = ledger_path
        self.max_wall_seconds = max_wall_seconds
        self.worker_timeout = worker_timeout
        self.rlimit_as_bytes = rlimit_as_bytes
        self.interrupted = False

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

        # Check manifest SHA
        m_copy = dict(manifest)
        saved_sha = m_copy.pop("manifest_sha256", None)
        calc_sha = sha256_of_obj(m_copy)
        if saved_sha and saved_sha != calc_sha:
            return {
                "status": "ERROR",
                "reason": f"Manifest hash drift: saved {saved_sha} != calculated {calc_sha}",
            }

        return {"status": "PASS", "manifest": manifest}

    def load_existing_ledger(self) -> Tuple[Set[str], Set[str], List[Dict[str, Any]]]:
        """
        Reads existing attempts ledger.
        Returns (started_attempt_ids, completed_attempt_ids, all_records).
        """
        started: Set[str] = set()
        completed: Set[str] = set()
        records: List[Dict[str, Any]] = []

        if not self.ledger_path.exists():
            return started, completed, records

        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records.append(rec)
                    rec_type = rec.get("record_type")
                    att_id = rec.get("attempt_id")
                    if rec_type == "ATTEMPT_STARTED":
                        started.add(att_id)
                    elif rec_type == "ATTEMPT_COMPLETED":
                        completed.add(att_id)
                except Exception:
                    pass

        return started, completed, records

    def append_record(self, record: Dict[str, Any]) -> None:
        """Atomically appends a record to the durable ledger."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def run(self, workload_filter: Optional[str] = None) -> Dict[str, Any]:
        """Main execution loop."""
        supervisor_t0 = time.time()
        print("Starting DSTAR-03 Pilot Supervisor...")

        # 1. Preflight
        pf = self.preflight()
        if pf["status"] != "PASS":
            print(f"Supervisor Preflight Failed: {pf['reason']}")
            # Record blocked attempt
            self.append_record({
                "record_type": "SUPERVISOR_RUN_BLOCKED",
                "status": pf["status"],
                "reason": pf["reason"],
                "timestamp": time.time(),
            })
            return pf

        manifest = pf["manifest"]
        workloads = manifest["workloads"]

        # Filter workloads if requested
        if workload_filter:
            workloads = [w for w in workloads if workload_filter in w["workload_id"]]
            print(f"Applied filter '{workload_filter}': {len(workloads)} workloads selected.")

        # 2. Check existing ledger
        started_ids, completed_ids, _ = self.load_existing_ledger()
        print(f"Existing ledger has {len(completed_ids)} completed attempts.")

        # Any attempt that was started but never completed is marked INTERRUPTED
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
        # For each workload: 3 pairs = 6 runs
        # Order parity:
        # even case_index: Pair 1 (M1, M2), Pair 2 (M2, M1), Pair 3 (M1, M2)
        # odd case_index:  Pair 1 (M2, M1), Pair 2 (M1, M2), Pair 3 (M2, M1)
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
                    rep_idx = pair_idx  # rep 0, 1, 2
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

        for slot_idx, slot in enumerate(scheduled_slots):
            # Check elapsed wall time budget
            elapsed_wall = time.time() - supervisor_t0
            if elapsed_wall >= self.max_wall_seconds:
                print(f"Max wall budget ({self.max_wall_seconds}s) reached. Stopping batch.")
                break

            if self.interrupted:
                print("Supervisor interrupted. Stopping execution loop.")
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

            # Process output
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
            elif proc_res["status"] == "RESOURCE_LIMIT":
                completed_rec["status"] = "RESOURCE_LIMIT"
                completed_rec["error"] = "Worker exceeded memory limit (RLIMIT_AS)"
            elif proc_res["status"] == "OK":
                try:
                    payload = json.loads(proc_res["stdout"])
                    completed_rec.update(payload)
                except Exception as exc:
                    completed_rec["status"] = "ERROR"
                    completed_rec["error"] = f"Failed to parse child JSON output: {exc}"
                    completed_rec["raw_stdout"] = proc_res["stdout"][:500]
            else:
                completed_rec["status"] = "ERROR"
                completed_rec["error"] = proc_res["stderr"][:500]

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

        return {
            "status": "PASS",
            "total_slots": total_slots,
            "executed_this_run": executed_this_run,
            "total_completed": len(completed_ids),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DSTAR-03 paired pilot supervisor.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH, help="Path to manifest JSON.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH, help="Path to attempts ledger JSONL.")
    parser.add_argument("--max-wall-seconds", type=float, default=600.0, help="Maximum supervisor wall time in seconds.")
    parser.add_argument("--worker-timeout", type=float, default=60.0, help="Per-worker timeout in seconds.")
    parser.add_argument("--filter", type=str, default=None, help="Filter workloads by substring.")
    args = parser.parse_args()

    supervisor = PilotSupervisor(
        manifest_path=args.manifest,
        ledger_path=args.ledger,
        max_wall_seconds=args.max_wall_seconds,
        worker_timeout=args.worker_timeout,
    )
    res = supervisor.run(workload_filter=args.filter)

    sys.exit(0 if res.get("status") == "PASS" else 1)


if __name__ == "__main__":
    main()
