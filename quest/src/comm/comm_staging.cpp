/** @file
 * Experimental GPU CPU-staging mode selection.
 */

#include "quest/include/types.h"

#include "quest/src/comm/comm_staging.hpp"
#include "quest/src/core/envvars.hpp"


GpuStagingPath comm_staging_selectPath() {

    GpuStagingMode mode = envvars_getGpuStagingConfig().mode;

    // Scaffolding only: later phases replace individual cases while raw remains
    // the conservative fallback for every unsupported or incomplete path.
    switch (mode) {
    case GpuStagingMode::RAW:
    case GpuStagingMode::BULK_ASYNC:
    case GpuStagingMode::TILED_MATERIALIZE:
    case GpuStagingMode::TILED_FUSED:
        return GpuStagingPath::RAW;
    }

    return GpuStagingPath::RAW;
}


bool comm_staging_isCpuStagingForced() {

    return envvars_getGpuStagingConfig().forceCpuStaging;
}
