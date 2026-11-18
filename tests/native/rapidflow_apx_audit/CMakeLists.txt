cmake_minimum_required(VERSION 3.10)
project(RapidFlow_APX_Audit CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -Wall -Wextra -pthread")

set(RAPIDFLOW_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../baselines/rapidflow/source")

include_directories(
    ${RAPIDFLOW_ROOT}
    ${RAPIDFLOW_ROOT}/utility
    ${RAPIDFLOW_ROOT}/configuration
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

# Native RapidFlow source files compiled directly in place
set(RAPIDFLOW_SRCS
    ${RAPIDFLOW_ROOT}/graph/graph.cpp
    ${RAPIDFLOW_ROOT}/utility/graphoperations.cpp
)

# Audit harness probe implementation
set(AUDIT_SRCS
    src/audit_probes.cpp
    src/main.cpp
)

add_executable(rapidflow_apx_audit
    ${RAPIDFLOW_SRCS}
    ${AUDIT_SRCS}
)
