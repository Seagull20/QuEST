#include "quest/src/core/profiling.hpp"

#if QUEST_ENABLE_PROFILING_MARKERS
    #include <nvtx3/nvToolsExt.h>
#endif

extern "C" void quest_profile_range_push(const char* name) {
#if QUEST_ENABLE_PROFILING_MARKERS
    nvtxRangePushA(name);
#else
    (void) name;
#endif
}

extern "C" void quest_profile_range_pop(void) {
#if QUEST_ENABLE_PROFILING_MARKERS
    nvtxRangePop();
#endif
}
