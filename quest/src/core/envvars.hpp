/** @file
 * Functions for loading environment variables, useful for
 * configuring QuEST ahead of calling initQuESTEnv(), after
 * compilation.
 * 
 * @author Tyson Jones
 */

#ifndef ENVVARS_HPP
#define ENVVARS_HPP

#include <string>


#ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE

#include <cstddef>

enum class GpuStagingMode {
    RAW,
    BULK_ASYNC,
    TILED_MATERIALIZE,
    TILED_FUSED
};

enum class GpuStagingMpiProgress {
    WAIT,
    TESTSOME,
    TESTANY
};

struct GpuStagingConfig {
    GpuStagingMode mode;
    std::size_t tileBytes;
    int slots;
    bool usePinnedMemory;
    GpuStagingMpiProgress mpiProgress;
    bool forceCpuStaging;
};

#endif


namespace envvar_names { 
    extern std::string PERMIT_NODES_TO_SHARE_GPU;
    extern std::string DEFAULT_VALIDATION_EPSILON;

    #ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
    extern std::string GPU_STAGING_MODE;
    extern std::string GPU_STAGING_TILE_MB;
    extern std::string GPU_STAGING_SLOTS;
    extern std::string GPU_STAGING_PINNED;
    extern std::string GPU_STAGING_MPI_PROGRESS;
    extern std::string FORCE_CPU_STAGING;
    #endif
}


/*
 * LOAD VARS
 */

void envvars_validateAndLoadEnvVars(const char* caller);


/*
 * GET VAR
 */

bool envvars_getWhetherGpuSharingIsPermitted();

qreal envvars_getDefaultValidationEpsilon();

#ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
const GpuStagingConfig& envvars_getGpuStagingConfig();
#endif


#endif // ENVVARS_HPP
