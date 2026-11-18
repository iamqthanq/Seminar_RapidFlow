#ifndef HARNESS_CASES_H
#define HARNESS_CASES_H

#include <string>
#include "json_mini.h"

namespace rapidflow_apx_harness {

mini_json::Value run_codec_boundaries(const mini_json::Value& fixture);
mini_json::Value run_lifecycle_stream(const mini_json::Value& fixture);
mini_json::Value run_transaction_lifecycle(const mini_json::Value& fixture);
mini_json::Value run_tuple_sink_invariants(const mini_json::Value& fixture);
mini_json::Value run_k4_exact_enumeration(const mini_json::Value& fixture);
mini_json::Value run_event_transition_stream(const mini_json::Value& fixture);
mini_json::Value run_diamond_k1_fixed(const mini_json::Value& fixture);
mini_json::Value run_irrelevant_update_stream(const mini_json::Value& fixture);

} // namespace rapidflow_apx_harness

#endif // HARNESS_CASES_H
