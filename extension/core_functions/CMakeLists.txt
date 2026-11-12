cmake_minimum_required(VERSION 3.5...3.29)

project(CoreFunctionsExtension)

# Before any subdirectory or target, which would otherwise inherit the
# directory-wide definition
set_extension_version_sources(core_functions core_functions_extension.cpp)

include_directories(include)
add_subdirectory(aggregate)
add_subdirectory(scalar)

set(CORE_FUNCTION_FILES ${CORE_FUNCTION_FILES} core_functions_extension.cpp
                        function_list.cpp lambda_functions.cpp)

build_extension_library(core_functions ${CORE_FUNCTION_FILES})
duckdb_link_loadable_bundled_libraries(core_functions duckdb_skiplistlib)

install(
  TARGETS core_functions_extension
  EXPORT "${DUCKDB_EXPORT_SET}"
  LIBRARY DESTINATION "${INSTALL_LIB_DIR}"
  ARCHIVE DESTINATION "${INSTALL_LIB_DIR}")
