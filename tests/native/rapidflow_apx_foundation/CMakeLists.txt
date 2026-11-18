cmake_minimum_required(VERSION 3.16)
project(rapidflow_apx_foundation_harness CXX)

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

add_executable(rapidflow_apx_foundation_harness
    src/main.cpp
    src/harness_cases.cpp
)

target_link_libraries(rapidflow_apx_foundation_harness PRIVATE rapidflow_apx_foundation)
target_compile_options(rapidflow_apx_foundation_harness PRIVATE -O3 -Wall -Wextra)
