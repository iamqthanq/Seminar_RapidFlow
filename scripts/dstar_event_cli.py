#!/usr/bin/env python3
"""
scripts/dstar_event_cli.py — CLI Interface for D* Event Enumeration Engines (M1 & M2).

Usage:
    python3 scripts/dstar_event_cli.py \
        --engine m1|m2 \
        --query PATH \
        --graph PATH \
        --stream PATH \
        --k K \
        [--max-work N] \
        [--max-stream-steps N] \
        [--output PATH]

Exit codes:
    0: Execution completed successfully (status: "SUCCESS")
    1: Invalid input schema, domain violation (k >= lambda), invariant violation (status: "ERROR")
    2: Resource limit exceeded (status: "RESOURCE_LIMIT")
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from dstar_event_common import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_DATA_VERTICES,
    DEFAULT_MAX_QUERY_VERTICES,
    DEFAULT_MAX_STREAM_STEPS,
    DEFAULT_MAX_WORK,
    DStarEventError,
    DStarEventGraph,
    DStarEventUpdate,
    EventCounters,
    InvalidGraphError,
    InvalidThresholdError,
    InvalidUpdateError,
    InvariantViolationError,
    ResourceLimitError,
    UnknownVertexError,
    compute_edge_connectivity,
    validate_resource_caps,
    validate_run_contract,
)
from dstar_m1_enum import M1EnumEngine
from dstar_m2_event import M2EventEngine


def load_stream(stream_path: str) -> List[DStarEventUpdate]:
    """Loads update stream from either a JSON array file or JSONL file."""
    if not os.path.exists(stream_path):
        raise FileNotFoundError(f"Stream file does not exist: {stream_path}")

    with open(stream_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        return []

    # Attempt parsing as whole JSON first
    if content.startswith("[") or content.startswith("{"):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return [DStarEventUpdate.from_dict(item) for item in parsed]
            elif isinstance(parsed, dict) and "updates" in parsed:
                return [DStarEventUpdate.from_dict(item) for item in parsed["updates"]]
            elif isinstance(parsed, dict) and ("operation" in parsed or "kind" in parsed):
                return [DStarEventUpdate.from_dict(parsed)]
        except json.JSONDecodeError:
            pass

    # Fallback to JSONL
    updates: List[DStarEventUpdate] = []
    for line_idx, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise InvalidUpdateError(f"Malformed JSON on line {line_idx} of stream: {e}")
        updates.append(DStarEventUpdate.from_dict(data))
    return updates


def run_cli() -> int:
    parser = argparse.ArgumentParser(description="D* Event Enumeration CLI (M1 / M2)")
    parser.add_argument("--engine", required=True, choices=["m1", "m2"], help="Engine: m1 or m2")
    parser.add_argument("--query", required=True, help="Path to query graph JSON")
    parser.add_argument("--graph", required=True, help="Path to initial data graph JSON")
    parser.add_argument("--stream", required=True, help="Path to update stream JSON/JSONL")
    parser.add_argument("--k", required=True, type=int, help="Missing edge budget k")
    parser.add_argument("--max-work", type=int, default=DEFAULT_MAX_WORK, help="Maximum work operations")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES, help="Candidate space limit")
    parser.add_argument("--max-data-vertices", type=int, default=DEFAULT_MAX_DATA_VERTICES, help="Data vertex cap")
    parser.add_argument("--max-query-vertices", type=int, default=DEFAULT_MAX_QUERY_VERTICES, help="Query vertex cap")
    parser.add_argument("--max-stream-steps", type=int, default=DEFAULT_MAX_STREAM_STEPS, help="Stream steps cap")
    parser.add_argument("--output", default=None, help="Optional output JSON path")

    args = parser.parse_args()

    limits = {
        "max_work": args.max_work,
        "max_candidates": args.max_candidates,
        "max_data_vertices": args.max_data_vertices,
        "max_query_vertices": args.max_query_vertices,
        "max_stream_steps": args.max_stream_steps,
    }

    output_doc: Dict[str, Any] = {
        "semantics_version": "dstar-v1.0",
        "engine": args.engine,
        "engine_version": f"dstar-{args.engine}-v1.0",
        "k": args.k,
        "status": "SUCCESS",
    }

    try:
        # Load graphs and stream
        query = DStarEventGraph.from_file(args.query)
        output_doc["query_vertex_order"] = sorted(query.vertices.keys())

        curr_graph = DStarEventGraph.from_file(args.graph)
        updates = load_stream(args.stream)

        # F02.2 & F03.1 & F03.3: Validate run-level contract BEFORE executing stream
        lambda_q = validate_run_contract(
            query=query,
            graph=curr_graph,
            k=args.k,
            limits=limits,
            stream_length=len(updates),
        )
        output_doc["lambda"] = lambda_q

        engine_cls = M1EnumEngine if args.engine == "m1" else M2EventEngine
        engine = engine_cls(limits)

        completed_steps: List[Dict[str, Any]] = []
        total_entered = 0
        total_exited = 0
        aggregate_counters = EventCounters()

        for step_idx, update in enumerate(updates):
            try:
                result, curr_graph = engine.process_update(query, curr_graph, update, args.k, limits)
            except Exception as e:
                # F05: Preserve progressive progress when error occurs mid-stream
                output_doc["status"] = "RESOURCE_LIMIT" if isinstance(e, ResourceLimitError) else "ERROR"
                output_doc["error_type"] = type(e).__name__
                output_doc["error_message"] = str(e)
                output_doc["failing_step_index"] = step_idx
                output_doc["failing_update"] = update.to_dict()
                output_doc["attempted_steps_count"] = step_idx + 1
                output_doc["completed_steps_count"] = len(completed_steps)
                output_doc["completed_steps"] = completed_steps
                output_doc["steps"] = completed_steps
                output_doc["total_entered"] = total_entered
                output_doc["total_exited"] = total_exited
                output_doc["counters"] = aggregate_counters.to_dict()

                serialized = json.dumps(output_doc, indent=2)
                if args.output:
                    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(serialized + "\n")
                print(serialized, file=sys.stderr)
                return 2 if isinstance(e, ResourceLimitError) else 1

            total_entered += len(result.entered)
            total_exited += len(result.exited)

            # Aggregate counters
            aggregate_counters.candidate_evaluations += result.counters.candidate_evaluations
            aggregate_counters.candidate_extensions += result.counters.candidate_extensions
            aggregate_counters.anchors_started += result.counters.anchors_started
            aggregate_counters.edge_lookups += result.counters.edge_lookups
            aggregate_counters.vertex_label_checks += result.counters.vertex_label_checks
            aggregate_counters.emitted_tuples += result.counters.emitted_tuples
            aggregate_counters.variants_started += result.counters.variants_started
            aggregate_counters.raw_exact_results += result.counters.raw_exact_results
            aggregate_counters.edge_decisions += result.counters.edge_decisions
            aggregate_counters.missing_branches += result.counters.missing_branches
            aggregate_counters.commitment_checks += result.counters.commitment_checks

            step_record = {
                "step_index": step_idx,
                "operation": result.operation,
                "edge": [result.edge[0], result.edge[1]],
                "label": result.label,
                "entered": [list(t) for t in result.entered],
                "exited": [list(t) for t in result.exited],
                "counters": result.counters.to_dict(),
            }
            completed_steps.append(step_record)

        output_doc["steps"] = completed_steps
        output_doc["total_entered"] = total_entered
        output_doc["total_exited"] = total_exited
        output_doc["counters"] = aggregate_counters.to_dict()
        output_doc["completed_steps_count"] = len(completed_steps)
        output_doc["attempted_steps_count"] = len(updates)
        output_doc["status"] = "SUCCESS"

        serialized = json.dumps(output_doc, indent=2)
        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(serialized + "\n")
        else:
            print(serialized)
        return 0

    except ResourceLimitError as e:
        output_doc["status"] = "RESOURCE_LIMIT"
        output_doc["error_type"] = type(e).__name__
        output_doc["error_message"] = str(e)
        serialized = json.dumps(output_doc, indent=2)
        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(serialized + "\n")
        print(serialized, file=sys.stderr)
        return 2

    except (DStarEventError, ValueError, KeyError, FileNotFoundError) as e:
        output_doc["status"] = "ERROR"
        output_doc["error_type"] = type(e).__name__
        output_doc["error_message"] = str(e)
        serialized = json.dumps(output_doc, indent=2)
        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(serialized + "\n")
        print(serialized, file=sys.stderr)
        return 1

    except Exception as e:
        output_doc["status"] = "ERROR"
        output_doc["error_type"] = type(e).__name__
        output_doc["error_message"] = f"Unexpected runtime error: {e}"
        serialized = json.dumps(output_doc, indent=2)
        if args.output:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(serialized + "\n")
        print(serialized, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run_cli())
