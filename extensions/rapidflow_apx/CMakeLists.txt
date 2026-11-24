cmake_minimum_required(VERSION 3.16)
project(rapidflow_apx_foundation CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(RAPIDFLOW_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../baselines/rapidflow/source")

if(NOT EXISTS "${RAPIDFLOW_SOURCE_DIR}/graph/graph.h")
    message(FATAL_ERROR "RapidFlow source not found at ${RAPIDFLOW_SOURCE_DIR}")
endif()

include_directories(
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${RAPIDFLOW_SOURCE_DIR}"
    "${RAPIDFLOW_SOURCE_DIR}/graph"
    "${RAPIDFLOW_SOURCE_DIR}/utility"
    "${RAPIDFLOW_SOURCE_DIR}/configuration"
)

set(FOUNDATION_SRCS
    src/typed_codec.cpp
    src/authoritative_graph.cpp
    src/tuple_sink.cpp
    src/transaction_manager.cpp
    src/micro_matcher.cpp
    src/query_plan.cpp
    src/nlf_signatures.cpp
    src/nlf_index.cpp
    src/m1_enum_engine.cpp
    src/m2_event_engine.cpp
    src/streaming_engine.cpp
    "${RAPIDFLOW_SOURCE_DIR}/graph/graph.cpp"
    "${RAPIDFLOW_SOURCE_DIR}/utility/graphoperations.cpp"
)

add_library(rapidflow_apx_foundation STATIC ${FOUNDATION_SRCS})

target_compile_options(rapidflow_apx_foundation PRIVATE -O3 -Wall -Wextra)
