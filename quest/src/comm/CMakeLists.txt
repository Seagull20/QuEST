# @author Oliver Thomson Brown

target_sources(QuEST
  PRIVATE
  comm_config.cpp
  comm_routines.cpp
  comm_window.cpp
)

if (ENABLE_CUDA)
  set_source_files_properties(
    comm_window.cpp
    TARGET_DIRECTORY QuEST
    PROPERTIES
    LANGUAGE CUDA
  )
endif()
