cmake_minimum_required(VERSION 3.21)
project(quest_compression_toy LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)

option(QUEST_COMPRESSION_ENABLE_NVTX "Emit NVTX ranges from compression_exchange." OFF)
set(QUEST_NVTX_INCLUDE_DIR "" CACHE PATH "Optional include directory containing nvtx3/nvToolsExt.h")

find_package(MPI REQUIRED COMPONENTS CXX)
find_package(CUDAToolkit REQUIRED)

set(NVCOMP_HINTS)
if(DEFINED ENV{NVCOMP_ROOT})
  list(APPEND NVCOMP_HINTS "$ENV{NVCOMP_ROOT}")
endif()
if(DEFINED NVCOMP_ROOT)
  list(APPEND NVCOMP_HINTS "${NVCOMP_ROOT}")
endif()

find_path(NVCOMP_INCLUDE_DIR
  NAMES nvcomp.hpp
  HINTS ${NVCOMP_HINTS}
  PATH_SUFFIXES include)
find_library(NVCOMP_LIBRARY
  NAMES nvcomp
  HINTS ${NVCOMP_HINTS}
  PATH_SUFFIXES lib lib64)

if(NOT NVCOMP_INCLUDE_DIR OR NOT NVCOMP_LIBRARY)
  message(FATAL_ERROR "nvCOMP not found. Set NVCOMP_ROOT to an installation containing include/nvcomp.hpp and lib/libnvcomp.*")
endif()

include(CheckIncludeFileCXX)
set(CMAKE_REQUIRED_INCLUDES "${NVCOMP_INCLUDE_DIR};${CUDAToolkit_INCLUDE_DIRS}")
check_include_file_cxx("nvcomp/lz4.hpp" QUEST_COMPRESSION_HAVE_NVCOMP_LZ4)
check_include_file_cxx("nvcomp/gdeflate.hpp" QUEST_COMPRESSION_HAVE_NVCOMP_GDEFLATE)
check_include_file_cxx("nvcomp/bitcomp.hpp" QUEST_COMPRESSION_HAVE_NVCOMP_BITCOMP)

add_executable(compression_exchange src/compression_exchange.cu)
target_include_directories(compression_exchange PRIVATE "${NVCOMP_INCLUDE_DIR}")
target_link_libraries(compression_exchange PRIVATE MPI::MPI_CXX CUDA::cudart "${NVCOMP_LIBRARY}")
target_compile_definitions(compression_exchange PRIVATE
  QUEST_COMPRESSION_ENABLE_NVTX=$<BOOL:${QUEST_COMPRESSION_ENABLE_NVTX}>
  QUEST_COMPRESSION_HAVE_NVCOMP_LZ4=$<BOOL:${QUEST_COMPRESSION_HAVE_NVCOMP_LZ4}>
  QUEST_COMPRESSION_HAVE_NVCOMP_GDEFLATE=$<BOOL:${QUEST_COMPRESSION_HAVE_NVCOMP_GDEFLATE}>
  QUEST_COMPRESSION_HAVE_NVCOMP_BITCOMP=$<BOOL:${QUEST_COMPRESSION_HAVE_NVCOMP_BITCOMP}>)

if(QUEST_COMPRESSION_ENABLE_NVTX)
  find_path(QUEST_COMPRESSION_NVTX_INCLUDE_DIR
    NAMES nvtx3/nvToolsExt.h
    HINTS
      "${QUEST_NVTX_INCLUDE_DIR}"
      "$ENV{QUEST_NVTX_INCLUDE_DIR}"
      ${CUDAToolkit_INCLUDE_DIRS}
    PATH_SUFFIXES include)
  if(NOT QUEST_COMPRESSION_NVTX_INCLUDE_DIR)
    message(FATAL_ERROR "QUEST_COMPRESSION_ENABLE_NVTX=ON but nvtx3/nvToolsExt.h was not found. Set QUEST_NVTX_INCLUDE_DIR.")
  endif()
  target_include_directories(compression_exchange PRIVATE "${QUEST_COMPRESSION_NVTX_INCLUDE_DIR}")
  message(STATUS "compression_exchange NVTX enabled with headers: ${QUEST_COMPRESSION_NVTX_INCLUDE_DIR}")
else()
  message(STATUS "compression_exchange NVTX disabled")
endif()

set_target_properties(compression_exchange PROPERTIES
  CUDA_SEPARABLE_COMPILATION OFF
  CUDA_RUNTIME_LIBRARY Shared)
