cmake_minimum_required(VERSION 3.16)
project(latency_lab VERSION 0.1.0 LANGUAGES CXX)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR "Latency Lab requires Linux perf_event_open")
endif()
add_library(latency_metrics src/metrics.cpp)
target_include_directories(latency_metrics PUBLIC include)
target_compile_features(latency_metrics PUBLIC cxx_std_17)
target_compile_options(latency_metrics PRIVATE -Wall -Wextra -Wpedantic)
add_executable(metrics_probe src/probe.cpp)
target_link_libraries(metrics_probe PRIVATE latency_metrics)
target_compile_options(metrics_probe PRIVATE -Wall -Wextra -Wpedantic)
add_executable(runner
  src/runner.cpp
  src/runner/options.cpp
  src/runner/capture.cpp
  src/runner/process.cpp
  src/runner/json.cpp
)
target_link_libraries(runner PRIVATE latency_metrics)
target_compile_options(runner PRIVATE -Wall -Wextra -Wpedantic)
add_executable(demo_target src/demo_target.cpp)
target_compile_features(demo_target PRIVATE cxx_std_17)
target_compile_options(demo_target PRIVATE -Wall -Wextra -Wpedantic)
include(CTest)
if(BUILD_TESTING)
  add_executable(collector_lifecycle tests/collector_lifecycle.cpp)
  target_link_libraries(collector_lifecycle PRIVATE latency_metrics)
  add_test(NAME collector_lifecycle COMMAND collector_lifecycle)
  find_package(Python3 COMPONENTS Interpreter REQUIRED)
  add_test(NAME probe_contract COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/probe_contract.py $<TARGET_FILE:metrics_probe>)
  add_test(NAME runner_integration COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/runner_integration.py $<TARGET_FILE:runner>)
  set_tests_properties(runner_integration PROPERTIES TIMEOUT 40)
endif()
