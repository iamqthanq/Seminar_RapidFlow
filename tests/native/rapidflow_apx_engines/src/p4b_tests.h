#ifndef RAPIDFLOW_APX_P4B_TESTS_H
#define RAPIDFLOW_APX_P4B_TESTS_H

namespace rapidflow_apx {

class ApxAuthoritativeGraph;
class ApxQuerySignatures;
class ApxNlfIndex;

bool verify_index_reconstruction(
    const ApxAuthoritativeGraph& graph,
    const ApxQuerySignatures& signatures,
    const ApxNlfIndex& index
);

int run_residual_native_tests(const char* suite_name);
int run_neighbor_buffer_tests(const char* suite_name);
int run_p4b_native_tests(const char* suite_name = "all");

} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_P4B_TESTS_H
