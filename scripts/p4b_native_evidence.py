"""Timing and frozen-record checks shared by live and persisted P4b evidence."""

import json

from scripts.run_rapidflow_apx_p4b import LedgerIntegrityError, load_json_bytes

NATIVE_METRICS = ("streaming_loop_wall_ns", "input_parsing_ns", "initialization_wall_ns",
                  "graph_init_ns", "query_prep_ns", "engine_and_nlf_init_ns", "reporting_wall_ns")
PROFILING_METRICS = ("update_maintenance_ns", "search_ns", "neighbor_retrieval_ns",
                     "search_core_ns", "tuple_commit_ns")
STEP_METRICS = ("step_total_ns", *PROFILING_METRICS, "tuple_sink_ns")
PHASES = ("input_parsing_ns", "initialization_wall_ns", "streaming_loop_wall_ns", "reporting_wall_ns")
INIT = ("graph_init_ns", "query_prep_ns", "engine_and_nlf_init_ns")
UPDATE_OPS = {"+": "+", "INSERT": "+", "INS": "+", "-": "-", "DELETE": "-", "DEL": "-"}


def require(condition, reason):
    if not condition:
        raise LedgerIntegrityError(reason)


def uint(value, field, *, positive=False):
    require(type(value) is int and int(positive) <= value <= (1 << 63) - 1,
            f"INVALID_NUMERIC_EVIDENCE: {field}")
    return value


def timings(native, outer, *, complete=True):
    macro = native.get("macro_timing")
    if macro is None and not complete and "macro_timing" not in native:
        return {}
    require(isinstance(macro, dict) and macro.get("clock_source") == "std::chrono::steady_clock",
            "TIMING_CLOCK_OR_SCHEMA")
    values = {key: uint(macro.get(key), f"macro_timing.{key}") for key in NATIVE_METRICS}
    for key, value in values.items():
        require(value <= outer, f"TIMING_BOUNDARY: {key} exceeds outer_wall_ns")
    require(sum(values[key] for key in PHASES) <= outer, "TIMING_BOUNDARY: disjoint native phases exceed outer_wall_ns")
    require(sum(values[key] for key in INIT) <= values["initialization_wall_ns"],
            "TIMING_BOUNDARY: initialization subphases exceed initialization_wall_ns")
    return values


def profile_timings(native, macro, outer, *, has_complete_steps=True):
    root = native.get("profiling")
    if root is None and "profiling" not in native:
        return
    require(isinstance(root, dict) and root.get("enabled") is True, "TIMING_PROFILE_SCHEMA")
    for key in ("input_parsing_ns", "graph_init_ns", "query_prep_ns", "streaming_loop_wall_ns", "streaming_exec_ns"):
        field = "streaming_loop_wall_ns" if key == "streaming_exec_ns" else key
        value = uint(root.get(key), f"profiling.{key}")
        require(value <= outer and (field not in macro or value == macro[field]), f"TIMING_PROFILE_ALIAS: {key}")
    pre_output = uint(root.get("pre_output_wall_ns"), "profiling.pre_output_wall_ns")
    require(root.get("total_wall_ns") == pre_output and type(root["total_wall_ns"]) is int and pre_output <= outer,
            "TIMING_BOUNDARY: profiling.pre_output_wall_ns")
    require(root["streaming_exec_ns"] <= pre_output, "TIMING_BOUNDARY: streaming_exec_ns exceeds pre_output_wall_ns")
    sums = dict.fromkeys(STEP_METRICS, 0)
    for index, step in enumerate(native.get("steps", ())):
        if "profiling" not in step and (step["status"] == "UNATTEMPTED" or
                                         step["error_category"] in ("RESOURCE_LIMIT_ERROR", "RESULT_DELIVERY_ERROR")):
            continue
        profile = step.get("profiling")
        require(isinstance(profile, dict), f"TIMING_STEP_SCHEMA: {index}")
        values = {key: uint(profile.get(key), f"steps[{index}].profiling.{key}") for key in STEP_METRICS}
        for key in STEP_METRICS[1:]:
            require(values[key] <= values["step_total_ns"], f"TIMING_BOUNDARY: step {index} {key} exceeds step_total_ns")
        for key in ("neighbor_retrieval_ns", "search_core_ns"):
            require(values[key] <= values["search_ns"], f"TIMING_BOUNDARY: step {index} {key} exceeds search_ns")
        require(values["tuple_commit_ns"] == values["tuple_sink_ns"], f"TIMING_PROFILE_ALIAS: step {index} tuple_sink_ns")
        for key, value in values.items():
            sums[key] += value
    cumulative = root.get("cumulative")
    require(isinstance(cumulative, dict), "TIMING_CUMULATIVE_SCHEMA")
    for key in STEP_METRICS:
        value = uint(cumulative.get(key), f"profiling.cumulative.{key}")
        alias = uint(root.get(f"cumulative_{key}"), f"profiling.cumulative_{key}")
        require(value == alias, f"TIMING_PROFILE_ALIAS: cumulative {key}")
        require(value >= sums[key] and (not has_complete_steps or value == sums[key]), f"TIMING_CUMULATIVE_MISMATCH: {key}")
        require(value <= root["streaming_exec_ns"], f"TIMING_BOUNDARY: cumulative {key} exceeds streaming_exec_ns")
    require(uint(root.get("native_step_total_ns"), "profiling.native_step_total_ns") == cumulative["step_total_ns"],
            "TIMING_PROFILE_ALIAS: native_step_total_ns")


def frozen_updates(steps, updates, *, complete=True):
    require(isinstance(steps, (tuple, list)) and len(steps) <= len(updates)
            and (not complete or len(steps) == len(updates)), "FROZEN_UPDATE_INVENTORY")
    for index, (step, update) in enumerate(zip(steps, updates)):
        require(isinstance(step, dict), f"FROZEN_UPDATE_IDENTITY: step {index}")
        wanted = {"step_index": index, "op": UPDATE_OPS[update["op"]],
                  "u": update["u"], "v": update["v"], "label": update["label"]}
        for key, expected in wanted.items():
            value = step.get(key)
            require(type(value) is type(expected) and value == expected, f"FROZEN_UPDATE_IDENTITY: step {index} {key}")


def interrupted_output(raw):
    """Read complete fields/records from the native writer's streamed JSON prefix.

    A killed writer may stop anywhere. A completely emitted field is validated
    with the same strict JSON reader as an ordinary result.
    """
    if not raw:
        return {}, "ABSENT"
    try:
        value = load_json_bytes(raw)
    except (ValueError, UnicodeError):
        pass
    else:
        require(isinstance(value, dict), "INTERRUPTED_OUTPUT_SCHEMA")
        return value, "COMPLETE"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        require(error.reason == "unexpected end of data" and error.end == len(raw), "INTERRUPTED_OUTPUT_SCHEMA")
        text = raw[:error.start].decode("utf-8")
    decoder = json.JSONDecoder()

    def decode(offset):
        try:
            value, end = decoder.raw_decode(text, offset)
        except json.JSONDecodeError as error:
            # Incomplete final literals, strings and containers may end before
            # raw_decode reaches a closing token. Reject known syntax errors.
            tail = text[error.pos:].strip()
            require(error.msg.startswith("Unterminated string") or error.pos == len(text)
                    or (tail and any(token.startswith(tail) for token in ("true", "false", "null")))
                    or tail == "-",
                    "INTERRUPTED_OUTPUT_SCHEMA")
            return None, None
        return load_json_bytes(text[offset:end].encode()), end

    def space(offset):
        while offset < len(text) and text[offset] in " \t\r\n":
            offset += 1
        return offset

    i = space(0)
    require(i < len(text) and text[i] == "{", "INTERRUPTED_OUTPUT_SCHEMA")
    native = {}
    i = space(i + 1)
    while i < len(text):
        key, end = decode(i)
        if end is None:
            break
        require(isinstance(key, str) and key not in native, "INTERRUPTED_OUTPUT_SCHEMA")
        i = space(end)
        if i == len(text):
            break
        require(text[i] == ":", "INTERRUPTED_OUTPUT_SCHEMA")
        i = space(i + 1)
        if i == len(text):
            break
        if key == "steps":
            require(text[i] == "[", "INTERRUPTED_OUTPUT_SCHEMA")
            steps = native["steps"] = []
            i = space(i + 1)
            while i < len(text) and text[i] != "]":
                step, end = decode(i)
                if end is None:
                    return native, "PARTIAL"
                require(isinstance(step, dict), "INTERRUPTED_OUTPUT_SCHEMA")
                steps.append(step)
                i = space(end)
                if i == len(text):
                    return native, "PARTIAL"
                if text[i] == "]":
                    break
                require(text[i] == ",", "INTERRUPTED_OUTPUT_SCHEMA")
                i = space(i + 1)
            require(i == len(text) or not text[i + 1:].strip(), "INTERRUPTED_OUTPUT_SCHEMA")
            break
        value, end = decode(i)
        if end is None:
            break
        i = space(end)
        # Without a separator a trailing number might have been cut short.
        if i == len(text) and type(value) in (int, float):
            break
        native[key] = value
        if i == len(text):
            break
        require(text[i] == ",", "INTERRUPTED_OUTPUT_SCHEMA")
        i = space(i + 1)
    return native, "PARTIAL"
