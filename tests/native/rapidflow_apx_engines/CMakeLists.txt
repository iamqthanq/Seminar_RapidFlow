cmake_minimum_required(VERSION 3.16)
project(rapidflow_apx_engines CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(RAPIDFLOW_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../baselines/rapidflow/source")
set(RAPIDFLOW_APX_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../extensions/rapidflow_apx")

include_directories(
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${RAPIDFLOW_APX_DIR}/include"
    "${RAPIDFLOW_SOURCE_DIR}"
    "${RAPIDFLOW_SOURCE_DIR}/graph"
    "${RAPIDFLOW_SOURCE_DIR}/utility"
    "${RAPIDFLOW_SOURCE_DIR}/configuration"
)

# Add foundation library subdirectory
add_subdirectory("${RAPIDFLOW_APX_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/rapidflow_apx_lib")

find_package(OpenSSL REQUIRED COMPONENTS Crypto)

add_executable(rapidflow_apx_native
    src/main.cpp
    src/harness_runner.cpp
    src/measurement_seam_tests.cpp
    src/p4b_tests.cpp
    src/residual_tests.cpp
    src/neighbor_buffer_tests.cpp
)

target_link_libraries(rapidflow_apx_native PRIVATE rapidflow_apx_foundation OpenSSL::Crypto)
target_compile_options(rapidflow_apx_native PRIVATE -O3 -Wall -Wextra)
target_compile_definitions(rapidflow_apx_native PRIVATE APX_TEST_ACCESS=1)

add_executable(rapidflow_apx_json_strings src/json_string_tests.cpp)
target_include_directories(rapidflow_apx_json_strings PRIVATE ../../../extensions/rapidflow_apx/include)
target_compile_options(rapidflow_apx_json_strings PRIVATE -Wall -Wextra)
