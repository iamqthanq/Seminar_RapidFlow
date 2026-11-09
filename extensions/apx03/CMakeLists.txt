cmake_minimum_required(VERSION 3.10)
project(RapidFlow_APX03_Baseline CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3 -Wall -Wextra -pthread")

set(RAPIDFLOW_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../baselines/rapidflow/source")

include_directories(
    ${RAPIDFLOW_ROOT}
    ${RAPIDFLOW_ROOT}/utility
    ${RAPIDFLOW_ROOT}/configuration
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# RapidFlow native C++ source files (strictly compiled in place, read-only origin)
set(RAPIDFLOW_SRCS
    ${RAPIDFLOW_ROOT}/graph/graph.cpp
    ${RAPIDFLOW_ROOT}/utility/graphoperations.cpp
)

# APX-03 newly authored baseline C++ components
set(APX03_SRCS
    src/apx03_graph.cpp
    src/apx03_matcher.cpp
    src/apx03_dynamic_engine.cpp
    src/main.cpp
)

add_executable(rapidflow_apx03_baseline
    ${RAPIDFLOW_SRCS}
    ${APX03_SRCS}
)

# Output binary placed in build root
set_target_properties(rapidflow_apx03_baseline PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
)
