#!/usr/bin/env python3
"""Reproduce the frozen PRE-R09 correctness matrix and retain lossless raw evidence."""

import argparse
import gzip
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import run_rapidflow_apx_p4b as p4b
from scripts.canonical_output_comparator import diff_canonical_structures, verify_neighbor_partition_invariants
from scripts.dstar_oracle import DStarDynamicOracle, DStarGraph, DStarUpdate, ResourceLimitError, apply_update
from scripts.run_rapidflow_apx_engines import (
    ALL_NEIGHBOR_COUNTER_FIELDS, validate_codec_state, validate_graph_state,
    validate_native_output_schema, validate_nlf_configuration,
)

MEASUREMENT_PATHS = {
    ("macro_timing", field) for field in (
        "input_parsing_ns", "initialization_wall_ns", "graph_init_ns", "query_prep_ns",
        "engine_and_nlf_init_ns", "streaming_loop_wall_ns", "reporting_wall_ns",
        "child_vm_hwm_kib", "child_vm_hwm_bytes",
    )
}
EVENT_FIELDS = ("entered_tuples", "exited_tuples")


def ordered_events(step, field, width):
    tuples = step[field]
    if not isinstance(tuples, list):
        raise ValueError(f"{field} is not a tuple array")
    ordered = []
    for mapping in tuples:
        if (not isinstance(mapping, list) or len(mapping) != width
                or any(type(vertex) is not int for vertex in mapping)
                or len(set(mapping)) != width):
            raise ValueError(f"Invalid ordered mapping in {field}: {mapping!r}")
        ordered.append(tuple(mapping))
    if len(set(ordered)) != len(ordered):
        raise ValueError(f"Duplicate mapping in {field}")
    return ordered


def graph_from_dict(spec):
    graph = DStarGraph()
    for vertex in spec["vertices"]:
        graph.add_vertex(vertex["id"], vertex["label"])
    for edge in spec["edges"]:
        graph.add_edge(edge["u"], edge["v"], edge["label"])
    return graph


def compare_option_b(bare, other):
    # Only measurement observations vary between otherwise identical runs. The
    # shared comparator retains exact native tuple emission order and legacy work.
    return diff_canonical_structures(
        bare, other, excluded_set=MEASUREMENT_PATHS | {
            ("requested_neighbor_mode",), ("effective_neighbor_mode",),
        } | {("counters", field) for field in ALL_NEIGHBOR_COUNTER_FIELDS},
    )


def oracle_and_snapshots(workload, entry):
    query = graph_from_dict(workload["query"])
    graph = graph_from_dict(workload["initial_graph"])
    # These are the frozen generator tier limits. The generic oracle defaults
    # to eight data vertices and would incorrectly exclude registered C1 inputs.
    expected_c1 = entry["correctness_tier"] == "C1_EXHAUSTIVE_M0_ORACLE"
    try:
        oracle = DStarDynamicOracle(query, graph, workload["k"], max_candidates=100_000,
                                    max_data_vertices=64, max_query_vertices=6)
    except ResourceLimitError as error:
        if expected_c1:
            raise ValueError("Frozen C1 workload could not initialize M0") from error
        oracle = None
        status = {"availability": "ORACLE_UNAVAILABLE_WITHIN_REGISTERED_BUDGET", "reason": str(error)}
    else:
        if not expected_c1:
            raise ValueError("Frozen C3 tier disagrees with actual M0 availability")
        status = {"availability": "AVAILABLE", "steps": []}

    v_labels = set(query.vertices.values()) | set(graph.vertices.values())
    e_labels = set(query.edges.values()) | set(graph.edges.values())
    snapshots = [graph]
    codecs = [{"query_vertices_count": len(query.vertices), "data_vertices_count": len(graph.vertices),
               "vertex_labels_count": len(v_labels), "edge_labels_count": len(e_labels)}]
    for update in workload["updates"]:
        change = DStarUpdate({"+": "INSERT", "-": "DELETE"}[update["op"]],
                             update["u"], update["v"], update["label"])
        if oracle is not None:
            result = oracle.apply_update(change)
            status["steps"].append({"step": result.step, "entered_tuples": result.entered,
                                    "exited_tuples": result.exited})
        graph = graph.clone()
        apply_update(graph, change)
        e_labels.add(change.label)
        snapshots.append(graph)
        codecs.append(dict(codecs[-1], edge_labels_count=len(e_labels)))
    return status, snapshots, codecs


def validate_replay(record, variant, workload, snapshots, codecs, oracle, control=None):
    output = record["output_data"]
    if not isinstance(output, dict):
        raise ValueError(f"Missing or invalid native output: {record['censoring_reason']}")
    code = record["exit_code"]
    censored = code == 4 and record["censoring_reason"] == "WORK_BUDGET_EXCEEDED"
    if code != 0 and not censored:
        raise ValueError(f"Unexpected native exit {code}: {record['censoring_reason']}")
    if (output.get("execution_purpose") != "correctness"
            or output.get("profiling_enabled") is not False
            or output.get("deep_diagnostics_enabled") is not True):
        raise ValueError("Wrong execution purpose or correctness diagnostics unavailable")
    ok, reason = validate_native_output_schema(
        output, "m2", workload["case_id"], workload=workload, is_success=not censored,
        expected_raw_input_sha256=record["fixture_sha256"],
    )
    if not ok:
        raise ValueError(reason)
    cfg = p4b.ALL_VARIANTS[variant]
    ok, reason, _ = validate_nlf_configuration(
        "m2", cfg["nlf_mode"], output, not censored,
        requested_neighbor_mode=cfg["neighbor_mode"], native_exit_code=code,
    )
    if not ok:
        raise ValueError(reason)
    issues = verify_neighbor_partition_invariants(output, cfg["neighbor_mode"], is_negative=censored)
    if issues:
        raise ValueError(issues[0])
    if output["work_budget"]["effective_limit"] != p4b.CANONICAL_MAX_WORK_CAP:
        raise ValueError("Frozen work cap was not applied")
    if len(output["steps"]) != len(workload["updates"]):
        raise ValueError("Native output does not cover the declared step inventory")
    width = len(workload["query"]["vertices"])
    completed_prefix = 0
    for i, step in enumerate(output["steps"]):
        committed = step["status"] == "SUCCESS"
        if committed:
            completed_prefix += 1
        for field in EVENT_FIELDS:
            actual = ordered_events(step, field, width)
            if not committed and actual:
                raise ValueError(f"Noncommitted step {i} publishes events")
            if committed and oracle["availability"] == "AVAILABLE":
                expected = {tuple(mapping) for mapping in oracle["steps"][i][field]}
                if set(actual) != expected:
                    raise ValueError(f"M0 mismatch at step {i} {field}")
            if control is not None and committed and control["steps"][i]["status"] == "SUCCESS":
                expected = ordered_events(control["steps"][i], field, width)
                if (actual != expected if variant in ("B_BUFFER", "B_MERGE") else set(actual) != set(expected)):
                    raise ValueError(f"Bare mismatch at step {i} {field}")
        if step["status"] == "UNATTEMPTED":
            continue
        after_update = i + int(committed)
        for validator, field, expected in (
            (validate_graph_state, "graph_state", snapshots[after_update]),
            (validate_codec_state, "codec_state", codecs[after_update]),
        ):
            ok, reason = validator(step.get(field), expected, workload["case_id"], variant, i)
            if not ok:
                raise ValueError(reason)
    if control is not None and variant in ("B_BUFFER", "B_MERGE"):
        differences = compare_option_b(control, output)
        if differences:
            raise ValueError(differences[0])
    if censored:
        return "C3_BUDGET_UNAVAILABLE" if oracle["availability"] != "AVAILABLE" else "CENSORED", completed_prefix
    if oracle["availability"] == "AVAILABLE":
        return "M0_VERIFIED", completed_prefix
    if control is not None and control["status"] == "SUCCESS":
        return "DIFFERENTIAL_M2_VERIFIED", completed_prefix
    return "CORRECTNESS_NOT_FULLY_CERTIFIED", completed_prefix


def git_identity(*, expected_native_commit=p4b.NATIVE_SOURCE_COMMIT, allow_candidate=False):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=p4b.REPO_ROOT, text=True).strip()

    if not allow_candidate and git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("PRE-R09 requires a clean evidence revision")
    if not allow_candidate and git("diff", "--name-only", expected_native_commit, "HEAD", "--", "extensions", "tests/native"):
        raise ValueError("Native source differs from the locked binary build revision")
    upstream = "baselines/rapidflow/source"
    if (git("-C", upstream, "rev-parse", "HEAD") != p4b.EXPECTED_UPSTREAM_COMMIT
            or git("-C", upstream, "status", "--porcelain")):
        raise ValueError("Pinned upstream is not pristine")
    return git("rev-parse", "HEAD")


def replay(binary, output_dir, *, candidate_mode=False):
    protocol, manifest, _ = p4b.load_frozen_plan()
    actual_binary_sha = p4b.compute_sha256(binary)
    if candidate_mode:
        if not binary.is_file() or actual_binary_sha == p4b.HISTORICAL_R1_NATIVE_SHA256:
            raise ValueError("Candidate replay requires the rebuilt candidate native binary")
        expected_binary_sha = actual_binary_sha
        revision = git_identity(allow_candidate=True)
        native_source_commit = p4b.NATIVE_SOURCE_COMMIT if actual_binary_sha == p4b.NATIVE_SHA256 else revision
    else:
        expected_binary_sha = p4b.NATIVE_SHA256
        if actual_binary_sha != expected_binary_sha:
            raise ValueError("PRE-R09 binary hash does not match the frozen build")
        revision = git_identity()
        native_source_commit = p4b.NATIVE_SOURCE_COMMIT
    output_dir.mkdir()
    rows = []
    workload_rows = []
    for entry in manifest["workloads"]:
        workload_id = entry["workload_id"]
        folder = output_dir / workload_id
        folder.mkdir()
        fixture = p4b.REPO_ROOT / entry["fixture_path"]
        workload = p4b.load_json_bytes(fixture.read_bytes())
        oracle, snapshots, codecs = oracle_and_snapshots(workload, entry)
        oracle_bytes = p4b.canonical_json_bytes(oracle)
        (folder / "oracle.json").write_bytes(oracle_bytes)
        oracle_sha = p4b.sha256_bytes(oracle_bytes)
        control = None
        workload_results = []
        for variant in p4b.DIAG_VARIANTS:
            path = folder / f"{variant}.json"
            record = p4b.execute_p4b_native_slot(
                binary, fixture, variant, "correctness", path,
                timeout_s=protocol["resource_policy"]["wall_timeout_seconds"],
                as_limit_mib=protocol["resource_policy"]["address_space_limit_mib"],
                max_work=protocol["resource_policy"]["logical_work_cap"],
                max_nlf_memory_mib=protocol["resource_policy"]["max_nlf_memory_mib"],
                expected_binary_sha256=expected_binary_sha, expected_fixture_sha256=entry["fixture_sha256"],
            )
            try:
                if variant != "BARE" and control is None:
                    raise ValueError("BARE control did not validate")
                result, prefix = validate_replay(record, variant, workload, snapshots, codecs, oracle, control)
            except (ValueError, KeyError, TypeError) as error:
                result, prefix, discrepancy = "MISMATCH", None, str(error)
            else:
                discrepancy = None
                if variant == "BARE":
                    control = record["output_data"]
            raw = path.read_bytes()
            compressed = gzip.compress(raw, mtime=0)
            compressed_path = path.with_suffix(".json.gz")
            compressed_path.write_bytes(compressed)
            path.unlink()
            row = {k: v for k, v in record.items() if k not in {"output_data", "output_path"}}
            row.update(
                evidence_kind="REAL_CORRECTNESS", attempt_id=f"PRE-R09/{workload_id}/{variant}/1",
                workload_id=workload_id, native_source_commit=native_source_commit,
                replay_source_commit=revision, protocol_sha256=p4b.PROTOCOL_SHA256,
                manifest_sha256=protocol["manifest_sha256"], schedule_sha256=protocol["schedule_sha256"],
                oracle_sha256=oracle_sha, oracle_availability=oracle["availability"],
                raw_output=str(compressed_path.relative_to(output_dir)),
                compressed_output_sha256=p4b.sha256_bytes(compressed), result=result,
                discrepancy=discrepancy, verified_prefix_steps=prefix,
                native_status=(record["output_data"] or {}).get("status"),
                error_category=(record["output_data"] or {}).get("error_category"),
                step_record_count=len((record["output_data"] or {}).get("steps", [])),
            )
            with (output_dir / "replay.jsonl").open("ab") as log:
                log.write((json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode())
            workload_results.append(result)
            rows.append(row)
        if "MISMATCH" in workload_results:
            classification = "FAILED"
        elif any(s in ("CENSORED", "C3_BUDGET_UNAVAILABLE") for s in workload_results):
            classification = "CENSORED"
        elif all(s == "M0_VERIFIED" for s in workload_results):
            classification = "M0_VERIFIED"
        elif control and control["status"] == "SUCCESS" and all(
            s == "DIFFERENTIAL_M2_VERIFIED" for s in workload_results[1:]
        ):
            classification = "DIFFERENTIAL_M2_VERIFIED"
        else:
            classification = "CORRECTNESS_NOT_FULLY_CERTIFIED"
        workload_rows.append({"workload_id": workload_id, "result": classification,
                              "oracle_availability": oracle["availability"]})
        print(workload_id, classification, flush=True)
    if git_identity(allow_candidate=candidate_mode) != revision or p4b.compute_sha256(binary) != expected_binary_sha:
        raise ValueError("Source or binary changed during the replay")
    summary = {"workload_count": len(workload_rows), "native_executions": len(rows),
               "candidate_mode": candidate_mode,
               "binary_sha256": expected_binary_sha,
               "native_source_commit": native_source_commit,
               "replay_source_commit": revision,
               "workloads_by_result": dict(Counter(r["result"] for r in workload_rows)),
               "runs_by_result": dict(Counter(r["result"] for r in rows)),
               "oracle_unavailable_workloads": sum(r["oracle_availability"] != "AVAILABLE" for r in workload_rows),
               "workloads": workload_rows}
    (output_dir / "summary.json").write_bytes(p4b.canonical_json_bytes(summary))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=p4b.DEFAULT_BINARY_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate", action="store_true", help="Certify an isolated candidate binary without altering historical R1 locks")
    args = parser.parse_args()
    outcome = replay(args.binary.resolve(), args.output_dir.resolve(), candidate_mode=args.candidate)
    if outcome["workloads_by_result"].get("FAILED"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
