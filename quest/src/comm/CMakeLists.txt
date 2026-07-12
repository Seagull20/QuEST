# @author Oliver Thomson Brown

target_sources(QuEST
  PRIVATE
  comm_config.cpp
  comm_routines.cpp
)

if (QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE)
  target_sources(QuEST PRIVATE comm_staging.cpp)
endif()
