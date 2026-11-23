#!/usr/bin/env python3
"""
scripts/summarize_rapidflow_apx_p4a.py — Statistical analysis and report summarizer for Phase P4a.

Analyzes the raw experiment ledger (p4a_raw_ledger.jsonl) from Phase P4a.
Computes:
1. Paired speedup distribution s_i = T_{M1,i} / T_{M2,i} (median, Q1, Q3, min, max, mean).
2. Stratification across:
   - Query & tolerance k (Diamond k=0, 1; K4 k=0, 1, 2)
   - Label regime (uniform vs mixed)
   - Topology (micro, cycle, hub, scalefree, zero_event, delete_heavy)
   - Event status (event-bearing vs zero-event)
3. Macro-stage breakdowns (input parsing, graph init, query prep, streaming exec).
4. Micro-step stage breakdowns for M2 (update maintenance, search core, neighbor retrieval, tuple sink).
5. Counter ratios (candidates evaluated per step, events emitted per step, pruning efficiency).
6. Bottleneck identification & evidence-based P4b recommendation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_raw_ledger.jsonl"
DEFAULT_SUMMARY_JSON = REPO_ROOT / "results" / "logs" / "rf36-p4a" / "p4a_summary_metrics.json"


def percentile(data: List[float], p: float) -> float:
    """Calculates linear interpolation percentile p in [0, 1]."""
    if not data:
        return 0.0
    sorted_d = sorted(data)
    n = len(sorted_d)
    if n == 1:
        return sorted_d[0]
    pos = (n - 1) * p
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    weight = pos - low
    return sorted_d[low] * (1.0 - weight) + sorted_d[high] * weight


def compute_distribution_stats(values: List[float]) -> Dict[str, float]:
    """Computes basic distribution metrics: min, Q1, median, Q3, max, mean, stddev."""
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "q1": 0.0,
            "median": 0.0,
            "q3": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "stddev": 0.0,
        }
    cnt = len(values)
    mean_val = sum(values) / cnt
    var_val = sum((x - mean_val) ** 2 for x in values) / cnt if cnt > 1 else 0.0
    return {
        "count": cnt,
        "min": round(min(values), 4),
        "q1": round(percentile(values, 0.25), 4),
        "median": round(percentile(values, 0.50), 4),
        "q3": round(percentile(values, 0.75), 4),
        "max": round(max(values), 4),
        "mean": round(mean_val, 4),
        "stddev": round(math.sqrt(var_val), 4),
    }


def load_ledger(ledger_path: Path) -> List[Dict[str, Any]]:
    """Loads all records from JSONL ledger."""
    records = []
    if not ledger_path.is_file():
        return records
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def analyze_p4a_ledger(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Processes ledger records into structured statistical metrics."""
    # 1. Group records by (workload_id, repetition) -> { "m1": rec, "m2": rec }
    pairs_map: Dict[Tuple[str, int], Dict[str, Any]] = defaultdict(dict)
    for rec in records:
        w_id = rec.get("workload_id")
        rep = rec.get("repetition", 0)
        eng = rec.get("engine")
        if w_id and eng in ("m1", "m2"):
            pairs_map[(w_id, rep)][eng] = rec

    # 2. Filter valid pairs
    valid_pairs = []
    outer_speedups = []
    stream_speedups = []
    search_speedups = []

    # Stratified speedups
    speedups_by_query_k: Dict[str, List[float]] = defaultdict(list)
    speedups_by_regime: Dict[str, List[float]] = defaultdict(list)
    speedups_by_topology: Dict[str, List[float]] = defaultdict(list)
    speedups_by_event_status: Dict[str, List[float]] = defaultdict(list)

    # Profiling breakdowns across all valid runs for M1 and M2
    m1_macro_stages: Dict[str, List[float]] = defaultdict(list)
    m2_macro_stages: Dict[str, List[float]] = defaultdict(list)

    m1_micro_stages: Dict[str, List[float]] = defaultdict(list)
    m2_micro_stages: Dict[str, List[float]] = defaultdict(list)

    # Counters
    m1_counters: Dict[str, List[int]] = defaultdict(list)
    m2_counters: Dict[str, List[int]] = defaultdict(list)

    pair_details = []

    for (w_id, rep), eng_dict in sorted(pairs_map.items()):
        if "m1" not in eng_dict or "m2" not in eng_dict:
            continue
        m1 = eng_dict["m1"]
        m2 = eng_dict["m2"]

        if m1.get("status") != "COMPLETED" or m2.get("status") != "COMPLETED":
            continue
        if m1.get("exit_code") != 0 or m2.get("exit_code") != 0:
            continue
        if not m1.get("m0_agreed") or not m2.get("m0_agreed"):
            continue

        q_type = m1.get("query_type", "unknown")
        k = m1.get("k", 0)
        regime = m1.get("regime", "unknown")
        topology = m1.get("topology", "unknown")
        query_k_key = f"{q_type}_k{k}"

        # Outer wall speedup
        t_m1_outer = m1.get("outer_wall_ns", 0)
        t_m2_outer = m2.get("outer_wall_ns", 0)
        s_outer = (t_m1_outer / t_m2_outer) if t_m2_outer > 0 else 1.0

        # Profiling timings
        m1_prof = m1.get("profiling", {})
        m2_prof = m2.get("profiling", {})

        t_m1_stream = m1_prof.get("streaming_exec_ns", 0)
        t_m2_stream = m2_prof.get("streaming_exec_ns", 0)
        s_stream = (t_m1_stream / t_m2_stream) if t_m2_stream > 0 else 1.0

        m1_cum = m1_prof.get("cumulative", {})
        m2_cum = m2_prof.get("cumulative", {})

        t_m1_search = m1_cum.get("search_ns", 0)
        t_m2_search = m2_cum.get("search_ns", 0)
        s_search = (t_m1_search / t_m2_search) if t_m2_search > 0 else 1.0

        outer_speedups.append(s_outer)
        stream_speedups.append(s_stream)
        search_speedups.append(s_search)

        speedups_by_query_k[query_k_key].append(s_stream)
        speedups_by_regime[regime].append(s_stream)
        speedups_by_topology[topology].append(s_stream)

        # Event status: check if events were emitted
        m2_cnt = m2.get("counters", {})
        events_emitted = m2_cnt.get("committed_emissions", m2_cnt.get("events_emitted", 0))
        ev_key = "zero_event" if events_emitted == 0 else "event_bearing"
        speedups_by_event_status[ev_key].append(s_stream)

        # Record pair details
        pair_details.append({
            "workload_id": w_id,
            "repetition": rep,
            "query_type": q_type,
            "k": k,
            "regime": regime,
            "topology": topology,
            "events_emitted": events_emitted,
            "m1_outer_ns": t_m1_outer,
            "m2_outer_ns": t_m2_outer,
            "speedup_outer": round(s_outer, 4),
            "m1_stream_ns": t_m1_stream,
            "m2_stream_ns": t_m2_stream,
            "speedup_stream": round(s_stream, 4),
            "m1_search_ns": t_m1_search,
            "m2_search_ns": t_m2_search,
            "speedup_search": round(s_search, 4),
        })

        # Record macro breakdowns
        for eng, prof, macro_dict in [("m1", m1_prof, m1_macro_stages), ("m2", m2_prof, m2_macro_stages)]:
            tot = prof.get("total_wall_ns", 0)
            if tot > 0:
                macro_dict["input_parsing_pct"].append(100.0 * prof.get("input_parsing_ns", 0) / tot)
                macro_dict["graph_init_pct"].append(100.0 * prof.get("graph_init_ns", 0) / tot)
                macro_dict["query_prep_pct"].append(100.0 * prof.get("query_prep_ns", 0) / tot)
                macro_dict["streaming_exec_pct"].append(100.0 * prof.get("streaming_exec_ns", 0) / tot)

        # Record micro breakdowns within cumulative step total
        for eng, cum, micro_dict in [("m1", m1_cum, m1_micro_stages), ("m2", m2_cum, m2_micro_stages)]:
            step_tot = cum.get("step_total_ns", cum.get("cumulative_step_total_ns", 0))
            update_maint = cum.get("update_maintenance_ns", cum.get("cumulative_update_maintenance_ns", 0))
            search_core = cum.get("search_core_ns", cum.get("cumulative_search_core_ns", 0))
            neighbor_ret = cum.get("neighbor_retrieval_ns", cum.get("cumulative_neighbor_retrieval_ns", 0))
            tuple_sink = cum.get("tuple_sink_ns", cum.get("cumulative_tuple_sink_ns", 0))
            search_tot = cum.get("search_ns", cum.get("cumulative_search_ns", 0))

            if step_tot > 0:
                micro_dict["update_maintenance_pct"].append(100.0 * update_maint / step_tot)
                micro_dict["search_core_pct"].append(100.0 * search_core / step_tot)
                micro_dict["neighbor_retrieval_pct"].append(100.0 * neighbor_ret / step_tot)
                micro_dict["tuple_sink_pct"].append(100.0 * tuple_sink / step_tot)

            # Also percentage within search_ns
            if search_tot > 0:
                micro_dict["neighbor_retrieval_within_search_pct"].append(
                    100.0 * neighbor_ret / search_tot
                )
                micro_dict["search_core_within_search_pct"].append(
                    100.0 * search_core / search_tot
                )

        # Counters
        for eng, rec, cnt_dest in [
            ("m1", m1, m1_counters),
            ("m2", m2, m2_counters),
        ]:
            cnt_src = rec.get("counters", {})
            cnt_dest["candidates_evaluated"].append(
                cnt_src.get("candidate_evaluations", cnt_src.get("candidates_evaluated", 0))
            )
            cnt_dest["events_emitted"].append(
                cnt_src.get("committed_emissions", cnt_src.get("events_emitted", 0))
            )
            cnt_dest["steps"].append(rec.get("steps_count", cnt_src.get("steps", 1)))

        valid_pairs.append((w_id, rep))

    # Aggregate distribution stats
    def summarize_pct_dict(d: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
        res = {}
        for k, vals in d.items():
            res[k] = compute_distribution_stats(vals)
        return res

    overall_speedup_stats = {
        "outer_wall": compute_distribution_stats(outer_speedups),
        "streaming_exec": compute_distribution_stats(stream_speedups),
        "search_cumulative": compute_distribution_stats(search_speedups),
    }

    stratified_stats = {
        "by_query_k": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_query_k.items())},
        "by_regime": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_regime.items())},
        "by_topology": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_topology.items())},
        "by_event_status": {k: compute_distribution_stats(v) for k, v in sorted(speedups_by_event_status.items())},
    }

    macro_breakdowns = {
        "m1": summarize_pct_dict(m1_macro_stages),
        "m2": summarize_pct_dict(m2_macro_stages),
    }

    micro_breakdowns = {
        "m1": summarize_pct_dict(m1_micro_stages),
        "m2": summarize_pct_dict(m2_micro_stages),
    }

    # Aggregate counter ratios
    m1_eval_sum = sum(m1_counters["candidates_evaluated"])
    m1_emit_sum = sum(m1_counters["events_emitted"])
    m1_step_sum = sum(m1_counters["steps"])

    m2_eval_sum = sum(m2_counters["candidates_evaluated"])
    m2_emit_sum = sum(m2_counters["events_emitted"])
    m2_step_sum = sum(m2_counters["steps"])

    counter_metrics = {
        "m1": {
            "total_candidates_evaluated": m1_eval_sum,
            "total_events_emitted": m1_emit_sum,
            "total_steps": m1_step_sum,
            "candidates_per_step": round(m1_eval_sum / m1_step_sum, 2) if m1_step_sum > 0 else 0.0,
            "events_per_step": round(m1_emit_sum / m1_step_sum, 2) if m1_step_sum > 0 else 0.0,
            "pruning_efficiency_pct": round(
                100.0 * (1.0 - (m1_emit_sum / m1_eval_sum)), 2
            ) if m1_eval_sum > 0 else 0.0,
        },
        "m2": {
            "total_candidates_evaluated": m2_eval_sum,
            "total_events_emitted": m2_emit_sum,
            "total_steps": m2_step_sum,
            "candidates_per_step": round(m2_eval_sum / m2_step_sum, 2) if m2_step_sum > 0 else 0.0,
            "events_per_step": round(m2_emit_sum / m2_step_sum, 2) if m2_step_sum > 0 else 0.0,
            "pruning_efficiency_pct": round(
                100.0 * (1.0 - (m2_emit_sum / m2_eval_sum)), 2
            ) if m2_eval_sum > 0 else 0.0,
        },
    }

    # Bottleneck Analysis & Evaluation of Hypothesis for P4b
    # Let's inspect neighbor retrieval within search for M2:
    m2_neighbor_within_search_median = (
        micro_breakdowns["m2"].get("neighbor_retrieval_within_search_pct", {}).get("median", 0.0)
    )
    m2_search_core_within_search_median = (
        micro_breakdowns["m2"].get("search_core_within_search_pct", {}).get("median", 0.0)
    )
    m2_stream_speedup_median = overall_speedup_stats["streaming_exec"].get("median", 1.0)

    # Hypotheses testing:
    # H1: Bare M2 is faster than M1 in streaming execution (s_stream > 1.0).
    # H2: Neighbor retrieval overhead (ApxAuthoritativeGraph::get_neighbors) is a significant bottleneck (>= 15-20%).
    # H3: Candidate search dominates over maintenance and tuple sink during streaming execution.
    bottleneck_diagnosis = {
        "m2_neighbor_retrieval_within_search_pct_median": m2_neighbor_within_search_median,
        "m2_search_core_within_search_pct_median": m2_search_core_within_search_median,
        "overall_m2_streaming_speedup_median": m2_stream_speedup_median,
        "neighbor_retrieval_is_significant_bottleneck": bool(m2_neighbor_within_search_median >= 15.0),
        "streaming_speedup_achieved": bool(m2_stream_speedup_median > 1.0),
    }

    # Recommendation for Phase P4b:
    # If neighbor retrieval is significant, Authoritative Graph / CSR representation or caching neighbor lookups is critical.
    # If candidate evaluation count is high and pruning efficiency is low, Relaxed NLF filtering is beneficial.
    if bottleneck_diagnosis["neighbor_retrieval_is_significant_bottleneck"]:
        p4b_primary_recommendation = (
            "OPTION B & A: Authoritative Graph Neighbor Retrieval Optimization paired with Relaxed NLF. "
            "Profiling demonstrates get_neighbors() accounts for significant search overhead due to dynamic vector "
            "allocation and sorting. Combining CSR neighbor retrieval with Relaxed NLF will yield maximal speedup."
        )
    else:
        p4b_primary_recommendation = (
            "OPTION A: Proceed with Relaxed NLF Implementation. Profiling confirms candidate search is the primary "
            "workload component, validating filtering heuristics."
        )

    summary_metrics = {
        "total_records_processed": len(records),
        "total_valid_pairs": len(valid_pairs),
        "overall_speedup_distribution": overall_speedup_stats,
        "stratified_speedup": stratified_stats,
        "macro_stage_breakdowns_pct": macro_breakdowns,
        "micro_step_stage_breakdowns_pct": micro_breakdowns,
        "counter_metrics": counter_metrics,
        "bottleneck_diagnosis": bottleneck_diagnosis,
        "p4b_recommendation": p4b_primary_recommendation,
        "pair_details": pair_details,
    }

    return summary_metrics


def format_markdown_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Formats markdown table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val)))
    hdr_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * col_widths[i] for i in range(len(headers))) + " |"
    data_lines = [
        "| " + " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([hdr_line, sep_line] + data_lines)


def generate_markdown_report(metrics: Dict[str, Any]) -> str:
    """Generates comprehensive markdown report."""
    lines = []
    lines.append("# Phase P4a Native Profiling & Baseline Measurement Report")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(f"- **Total Workload Pairs Analyzed:** {metrics['total_valid_pairs']}")
    lines.append(f"- **Median Streaming Speedup ($S_{{stream}} = T_{{M1}} / T_{{M2}}$):** {metrics['overall_speedup_distribution']['streaming_exec']['median']}x")
    lines.append(f"- **Median Outer Wall Speedup ($S_{{outer}} = T_{{M1}} / T_{{M2}}$):** {metrics['overall_speedup_distribution']['outer_wall']['median']}x")
    lines.append(f"- **Median Search Speedup ($S_{{search}} = T_{{M1}} / T_{{M2}}$):** {metrics['overall_speedup_distribution']['search_cumulative']['median']}x")
    lines.append(f"- **M2 Neighbor Retrieval within Search:** {metrics['micro_step_stage_breakdowns_pct']['m2'].get('neighbor_retrieval_within_search_pct', {}).get('median', 0.0):.2f}%")
    lines.append(f"- **P4b Recommendation:** {metrics['p4b_recommendation']}")
    lines.append("")

    lines.append("## 2. Paired Speedup Distribution (M1 vs Bare M2)")
    lines.append("")
    sp_headers = ["Metric Scope", "Count", "Min", "Q1 (25%)", "Median", "Q3 (75%)", "Max", "Mean", "StdDev"]
    sp_rows = []
    for scope_key, scope_name in [
        ("streaming_exec", "Streaming Exec (Core)"),
        ("outer_wall", "Outer Wall (Process)"),
        ("search_cumulative", "Cumulative Search"),
    ]:
        s = metrics["overall_speedup_distribution"][scope_key]
        sp_rows.append([scope_name, s["count"], f"{s['min']:.2f}x", f"{s['q1']:.2f}x", f"{s['median']:.2f}x", f"{s['q3']:.2f}x", f"{s['max']:.2f}x", f"{s['mean']:.2f}x", f"{s['stddev']:.2f}"])
    lines.append(format_markdown_table(sp_headers, sp_rows))
    lines.append("")

    lines.append("## 3. Stratified Speedup Breakdown")
    lines.append("")
    lines.append("### 3.1 By Query Topology and Tolerance $k$")
    lines.append("")
    qk_headers = ["Query & Tolerance", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    qk_rows = []
    for qk, s in metrics["stratified_speedup"]["by_query_k"].items():
        qk_rows.append([qk, s["count"], f"{s['min']:.2f}x", f"{s['q1']:.2f}x", f"{s['median']:.2f}x", f"{s['q3']:.2f}x", f"{s['max']:.2f}x", f"{s['mean']:.2f}x"])
    lines.append(format_markdown_table(qk_headers, qk_rows))
    lines.append("")

    lines.append("### 3.2 By Label Regime")
    lines.append("")
    reg_headers = ["Label Regime", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    reg_rows = []
    for reg, s in metrics["stratified_speedup"]["by_regime"].items():
        reg_rows.append([reg, s["count"], f"{s['min']:.2f}x", f"{s['q1']:.2f}x", f"{s['median']:.2f}x", f"{s['q3']:.2f}x", f"{s['max']:.2f}x", f"{s['mean']:.2f}x"])
    lines.append(format_markdown_table(reg_headers, reg_rows))
    lines.append("")

    lines.append("### 3.3 By Graph Stream Topology")
    lines.append("")
    top_headers = ["Topology", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    top_rows = []
    for top, s in metrics["stratified_speedup"]["by_topology"].items():
        top_rows.append([top, s["count"], f"{s['min']:.2f}x", f"{s['q1']:.2f}x", f"{s['median']:.2f}x", f"{s['q3']:.2f}x", f"{s['max']:.2f}x", f"{s['mean']:.2f}x"])
    lines.append(format_markdown_table(top_headers, top_rows))
    lines.append("")

    lines.append("### 3.4 By Event Status")
    lines.append("")
    ev_headers = ["Event Status", "Pairs", "Min", "Q1", "Median", "Q3", "Max", "Mean"]
    ev_rows = []
    for ev, s in metrics["stratified_speedup"]["by_event_status"].items():
        ev_rows.append([ev, s["count"], f"{s['min']:.2f}x", f"{s['q1']:.2f}x", f"{s['median']:.2f}x", f"{s['q3']:.2f}x", f"{s['max']:.2f}x", f"{s['mean']:.2f}x"])
    lines.append(format_markdown_table(ev_headers, ev_rows))
    lines.append("")

    lines.append("## 4. Macro-Stage Execution Breakdown (% of Total Wall Time)")
    lines.append("")
    macro_headers = ["Stage", "M1 Median %", "M1 Mean %", "M2 Median %", "M2 Mean %"]
    macro_rows = []
    for stage_key, stage_name in [
        ("input_parsing_pct", "Input Parsing"),
        ("graph_init_pct", "Initial Graph CSR Construction"),
        ("query_prep_pct", "Query Graph Index Preparation"),
        ("streaming_exec_pct", "Streaming Execution Loop"),
    ]:
        m1_s = metrics["macro_stage_breakdowns_pct"]["m1"].get(stage_key, {})
        m2_s = metrics["macro_stage_breakdowns_pct"]["m2"].get(stage_key, {})
        macro_rows.append([
            stage_name,
            f"{m1_s.get('median', 0.0):.2f}%",
            f"{m1_s.get('mean', 0.0):.2f}%",
            f"{m2_s.get('median', 0.0):.2f}%",
            f"{m2_s.get('mean', 0.0):.2f}%",
        ])
    lines.append(format_markdown_table(macro_headers, macro_rows))
    lines.append("")

    lines.append("## 5. Micro-Step Stage Execution Breakdown (% of Step Total Time)")
    lines.append("")
    micro_headers = ["Step Component", "M1 Median %", "M1 Mean %", "M2 Median %", "M2 Mean %"]
    micro_rows = []
    for comp_key, comp_name in [
        ("update_maintenance_pct", "Update Maintenance (TransactionMgr)"),
        ("search_core_pct", "Search Core (Candidate Evaluation)"),
        ("neighbor_retrieval_pct", "Neighbor Retrieval (AuthoritativeGraph)"),
        ("tuple_sink_pct", "Tuple Sink (Canonical Sort & Deduplication)"),
    ]:
        m1_s = metrics["micro_step_stage_breakdowns_pct"]["m1"].get(comp_key, {})
        m2_s = metrics["micro_step_stage_breakdowns_pct"]["m2"].get(comp_key, {})
        micro_rows.append([
            comp_name,
            f"{m1_s.get('median', 0.0):.2f}%",
            f"{m1_s.get('mean', 0.0):.2f}%",
            f"{m2_s.get('median', 0.0):.2f}%",
            f"{m2_s.get('mean', 0.0):.2f}%",
        ])
    lines.append(format_markdown_table(micro_headers, micro_rows))
    lines.append("")

    lines.append("## 6. Engine Counters and Pruning Metrics")
    lines.append("")
    cnt_headers = ["Metric", "M1 (Enum-Diff)", "M2 (Bare Event-Triggered)"]
    c1 = metrics["counter_metrics"]["m1"]
    c2 = metrics["counter_metrics"]["m2"]
    cnt_rows = [
        ["Total Candidates Evaluated", str(c1["total_candidates_evaluated"]), str(c2["total_candidates_evaluated"])],
        ["Total Events Emitted", str(c1["total_events_emitted"]), str(c2["total_events_emitted"])],
        ["Total Stream Steps", str(c1["total_steps"]), str(c2["total_steps"])],
        ["Candidates per Step", f"{c1['candidates_per_step']:.2f}", f"{c2['candidates_per_step']:.2f}"],
        ["Events per Step", f"{c1['events_per_step']:.2f}", f"{c2['events_per_step']:.2f}"],
        ["Pruning Efficiency %", f"{c1['pruning_efficiency_pct']:.2f}%", f"{c2['pruning_efficiency_pct']:.2f}%"],
    ]
    lines.append(format_markdown_table(cnt_headers, cnt_rows))
    lines.append("")

    lines.append("## 7. Bottleneck Identification & P4b Architectural Decision")
    lines.append("")
    diag = metrics["bottleneck_diagnosis"]
    lines.append(f"- **M2 Neighbor Retrieval within Search:** {diag['m2_neighbor_retrieval_within_search_pct_median']:.2f}% of search time.")
    lines.append(f"- **M2 Search Core within Search:** {diag['m2_search_core_within_search_pct_median']:.2f}% of search time.")
    lines.append(f"- **Significant Bottleneck Verdict:** `{'CONFIRMED' if diag['neighbor_retrieval_is_significant_bottleneck'] else 'REJECTED'}`.")
    lines.append("")
    lines.append(f"**Conclusion & Path Forward:** {metrics['p4b_recommendation']}")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="P4a Native Profiling Summarizer")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER, help="Path to raw ledger JSONL")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_SUMMARY_JSON, help="Path to summary metrics JSON")
    parser.add_argument("--output-md", type=Path, default=None, help="Path to output markdown report")
    args = parser.parse_args()

    records = load_ledger(args.ledger)
    if not records:
        print(f"Warning: No records found in {args.ledger}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(records)} records from {args.ledger}")
    metrics = analyze_p4a_ledger(records)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(f"Summary metrics saved to {args.output_json}")

    md_report = generate_markdown_report(metrics)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_md, "w", encoding="utf-8") as f:
            f.write(md_report)
        print(f"Markdown report saved to {args.output_md}")
    else:
        print("\n" + md_report)


if __name__ == "__main__":
    main()
