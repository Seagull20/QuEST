/** @file
 * Experimental GPU CPU-staging mode selection.
 *
 * T-035 intentionally maps every requested mode to the existing raw path. Later
 * phases can add paths behind this selector without changing default QuEST
 * behaviour.
 */

#ifndef COMM_STAGING_HPP
#define COMM_STAGING_HPP

#ifndef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
    #error "comm_staging.hpp is available only when QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE is enabled."
#endif


enum class GpuStagingPath {
    RAW
};


GpuStagingPath comm_staging_selectPath();

bool comm_staging_isCpuStagingForced();


#endif // COMM_STAGING_HPP
