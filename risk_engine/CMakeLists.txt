cmake_minimum_required(VERSION 3.16)
project(rock_risk LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# 跨平台警告开关：不依赖任何平台专属 API
if(MSVC)
  set(RR_WARNINGS /W4 /permissive-)
else()
  set(RR_WARNINGS -Wall -Wextra -Wpedantic)
endif()

add_library(rock_risk_lib STATIC
  src/types.cpp
  src/policy.cpp
  src/assessor.cpp
  src/json_io.cpp
)
target_include_directories(rock_risk_lib
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party
)
target_compile_options(rock_risk_lib PRIVATE ${RR_WARNINGS})

add_executable(rock_risk_cli cli/main.cpp)
target_link_libraries(rock_risk_cli PRIVATE rock_risk_lib)
target_compile_options(rock_risk_cli PRIVATE ${RR_WARNINGS})

enable_testing()
add_executable(rock_risk_tests
  tests/test_main.cpp
  tests/test_assessor.cpp
  tests/test_json_io.cpp
)
target_include_directories(rock_risk_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
target_link_libraries(rock_risk_tests PRIVATE rock_risk_lib)
target_compile_options(rock_risk_tests PRIVATE ${RR_WARNINGS})
add_test(NAME rock_risk_unit COMMAND rock_risk_tests)
