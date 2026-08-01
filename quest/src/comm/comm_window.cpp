/** @file
 * T-039's opt-in bulk_async shared-host-window transport.
 *
 * This file is compiled for every QuEST configuration.  The implementation
 * below is deliberately restricted to CUDA+MPI builds: the default CPU build
 * still links a no-op backend, so the unflagged path has no window lifetime or
 * transport side effects.
 */

#include "quest/include/config.h"
#include "quest/include/types.h"
#include "quest/include/qureg.h"

#include "quest/src/comm/comm_window.hpp"
#include "quest/src/comm/comm_config.hpp"
#include "quest/src/core/profiling.hpp"
#include "quest/src/gpu/gpu_config.hpp"
#include "quest/src/gpu/gpu_subroutines.hpp"

#include <cstdlib>
#include <cstdio>
#include <cstring>

#if COMPILE_MPI && COMPILE_CUDA && !COMPILE_HIP && defined(__CUDACC__)

    #include <mpi.h>
    #include <cuda_runtime.h>

    #include <algorithm>
    #include <cstdint>
    #include <limits>
    #include <memory>
    #include <unordered_map>
    #include <vector>

namespace {

constexpr std::uint32_t WINDOW_PROTOCOL_VERSION = 1;
constexpr std::uint32_t GATED_OFF_ENCODING = 0;
constexpr int MPI_CONTROL_TAG_COUNT = 3;

struct WindowDescriptor {
    std::uint32_t version;
    std::uint32_t encoding;
    std::uint64_t sequence;
    std::uint64_t producerWorldRank;
    std::uint64_t payloadBytes;
    std::uint64_t storedBytes;
    std::uint64_t byteOffset;
};

static_assert(sizeof(WindowDescriptor) <= std::numeric_limits<int>::max(),
    "window descriptor must fit in an MPI count");

struct WindowEntry {
    int worldRank = -1;
    int nodeRank = -1;
    void* slot = nullptr;
    bool registered = false;
};

struct PairDecision {
    bool resolved = false;
    bool eligible = false;
};

struct StagingStats {
    bool initialised = false;
    bool enabled = false;
    int rank = 0;

    std::uint64_t windowExchanges = 0;
    std::uint64_t rawFallbackExchanges = 0;
    std::uint64_t fallbackOffNode = 0;
    std::uint64_t fallbackRegistrationConsensus = 0;

    std::uint64_t windowControlBytes = 0;
    std::uint64_t mpiPayloadBytes = 0;
    double windowControlSeconds = 0;
    double windowPayloadSeconds = 0;
    double payloadMpiSeconds = 0;
};

StagingStats& stagingStats() {
    static StagingStats stats;
    return stats;
}

bool envIsOne(const char* name) {
    const char* value = std::getenv(name);
    return value != nullptr && std::strcmp(value, "1") == 0;
}

bool statsRequested() {
    return envIsOne("QUEST_GPU_STAGING_STATS");
}

void printStagingStats() {
    StagingStats& stats = stagingStats();
    if (!stats.initialised || !stats.enabled)
        return;

    const char* mode = comm_isBulkAsyncEnabled()? "bulk_async" : "raw";
    std::fprintf(stderr,
        "[quest-staging-stats] rank=%d mode=%s "
        "window_exchanges=%llu raw_fallback_exchanges=%llu "
        "fallback_off_node=%llu fallback_registration_consensus=%llu "
        "window_control_seconds=%.9f window_payload_seconds=%.9f "
        "payload_mpi_seconds=%.9f window_control_bytes=%llu "
        "mpi_payload_bytes=%llu\n",
        stats.rank, mode,
        static_cast<unsigned long long>(stats.windowExchanges),
        static_cast<unsigned long long>(stats.rawFallbackExchanges),
        static_cast<unsigned long long>(stats.fallbackOffNode),
        static_cast<unsigned long long>(stats.fallbackRegistrationConsensus),
        stats.windowControlSeconds,
        stats.windowPayloadSeconds,
        stats.payloadMpiSeconds,
        static_cast<unsigned long long>(stats.windowControlBytes),
        static_cast<unsigned long long>(stats.mpiPayloadBytes));
}

void initialiseStats() {
    StagingStats& stats = stagingStats();
    if (stats.initialised)
        return;

    stats.initialised = true;
    stats.enabled = statsRequested();
    stats.rank = comm_getRank();
    if (stats.enabled)
        std::atexit(printStagingStats);
}

void noteRawPayload(std::size_t bytes, double seconds) {
    initialiseStats();
    StagingStats& stats = stagingStats();
    if (!stats.enabled)
        return;

    stats.mpiPayloadBytes += bytes;
    stats.payloadMpiSeconds += seconds;
}

void noteRawFallback() {
    initialiseStats();
    if (stagingStats().enabled)
        stagingStats().rawFallbackExchanges++;
}

void noteOffNodeFallback() {
    initialiseStats();
    if (stagingStats().enabled)
        stagingStats().fallbackOffNode++;
}

void noteRegistrationFallback() {
    initialiseStats();
    if (stagingStats().enabled)
        stagingStats().fallbackRegistrationConsensus++;
}

bool forceWindowFallbackForRank(int rank) {
    if (envIsOne("QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK"))
        return true;

    const char* value = std::getenv("QUEST_GPU_STAGING_FORCE_WINDOW_FALLBACK_RANK");
    if (value == nullptr || *value == '\0')
        return false;

    char* end = nullptr;
    long requestedRank = std::strtol(value, &end, 10);
    return end != value && *end == '\0' && requestedRank == rank;
}

[[noreturn]] void abortWindowProtocol(const char* reason) {
    std::fprintf(stderr, "[quest-staging] rank %d bulk_async protocol failure: %s\n",
        comm_getRank(), reason);
    MPI_Abort(MPI_COMM_WORLD, 739);
    std::abort();
}

class CommWindow {
  public:
    explicit CommWindow(Qureg qureg) {
        initialiseStats();
        if (qureg.numAmpsPerNode >
            std::numeric_limits<std::size_t>::max() / sizeof(qcomp))
            return;
        slotBytes_ = static_cast<std::size_t>(qureg.numAmpsPerNode) * sizeof(qcomp);
        setup();
    }

    ~CommWindow() {
        cleanup();
    }

    bool tryExchange(Qureg qureg, qcomp* gpuSend, qcomp* gpuRecv, qindex numAmps, int pairRank) {
        // T-069 scopes this transport to the full-amplitude exchange.  The
        // existing partial/packed routes therefore continue to use raw MPI.
        if (numAmps != qureg.numAmpsPerNode || slotBytes_ == 0)
            return false;

        const std::size_t payloadBytes = static_cast<std::size_t>(numAmps) * sizeof(qcomp);
        if (payloadBytes > slotBytes_ || payloadBytes > std::numeric_limits<std::uint64_t>::max())
            return false;

        if (!resolvePair(pairRank))
            return false;

        const WindowEntry* peer = findWorldRank(pairRank);
        if (peer == nullptr || peer->slot == nullptr)
            abortWindowProtocol("peer registration table entry disappeared");

        ++sequence_;

        // Default-stream work and, when enabled, cuQuantum work must be made
        // visible to the dedicated D2H stream without a device-wide sync.
        gpu_waitForPriorWorkOnStream(reinterpret_cast<void*>(d2hStream_));

        double payloadStart = MPI_Wtime();
        {
            QuestProfileRange d2hRange("quest.communication.d2h");
            cudaError_t result = cudaMemcpyAsync(
                mySlot_, gpuSend, payloadBytes, cudaMemcpyDeviceToHost, d2hStream_);
            if (result != cudaSuccess)
                abortWindowProtocol("cudaMemcpyAsync D2H failed");

            result = cudaStreamSynchronize(d2hStream_);
            if (result != cudaSuccess)
                abortWindowProtocol("D2H stream synchronization failed");
        }

        const double d2hFinished = MPI_Wtime();
        double controlStart = MPI_Wtime();
        if (MPI_Win_sync(window_) != MPI_SUCCESS)
            abortWindowProtocol("MPI_Win_sync before token failed");

        WindowDescriptor descriptor {
            WINDOW_PROTOCOL_VERSION,
            GATED_OFF_ENCODING,
            sequence_,
            static_cast<std::uint64_t>(worldRank_),
            static_cast<std::uint64_t>(payloadBytes),
            static_cast<std::uint64_t>(payloadBytes),
            0
        };
        WindowDescriptor peerDescriptor {};

        // Only this fixed-size descriptor crosses MPI; the qcomp payload is
        // read directly from peer->slot after the second Win_sync.
        {
            QuestProfileRange mpiRange("quest.communication.mpi");
            int result = MPI_Sendrecv(
                &descriptor, static_cast<int>(sizeof(descriptor)), MPI_BYTE,
                peer->nodeRank, tagData_,
                &peerDescriptor, static_cast<int>(sizeof(peerDescriptor)), MPI_BYTE,
                peer->nodeRank, tagData_, control_, MPI_STATUS_IGNORE);
            if (result != MPI_SUCCESS)
                abortWindowProtocol("pairwise data token exchange failed");
        }

        if (MPI_Win_sync(window_) != MPI_SUCCESS)
            abortWindowProtocol("MPI_Win_sync after token failed");

        if (peerDescriptor.version != WINDOW_PROTOCOL_VERSION ||
            peerDescriptor.encoding != GATED_OFF_ENCODING ||
            peerDescriptor.payloadBytes != payloadBytes ||
            peerDescriptor.storedBytes != payloadBytes ||
            peerDescriptor.byteOffset != 0 ||
            peerDescriptor.producerWorldRank != static_cast<std::uint64_t>(pairRank))
            abortWindowProtocol("peer token does not describe the requested payload");

        const double peerReady = MPI_Wtime();
        {
            QuestProfileRange h2dRange("quest.communication.h2d");
            cudaError_t result = cudaMemcpyAsync(
                gpuRecv, peer->slot, payloadBytes, cudaMemcpyHostToDevice, h2dStream_);
            if (result != cudaSuccess)
                abortWindowProtocol("cudaMemcpyAsync H2D failed");

            result = cudaStreamSynchronize(h2dStream_);
            if (result != cudaSuccess)
                abortWindowProtocol("H2D stream synchronization failed");
        }
        const double h2dFinished = MPI_Wtime();

        unsigned char closeToken = 1;
        unsigned char peerCloseToken = 0;
        {
            QuestProfileRange mpiRange("quest.communication.mpi");
            int result = MPI_Sendrecv(
                &closeToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                &peerCloseToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                control_, MPI_STATUS_IGNORE);
            if (result != MPI_SUCCESS || peerCloseToken != 1)
                abortWindowProtocol("pairwise close failed");
        }
        double controlFinished = MPI_Wtime();

        StagingStats& stats = stagingStats();
        if (stats.enabled) {
            stats.windowExchanges++;
            stats.windowControlBytes += 2 * (sizeof(descriptor) + sizeof(closeToken));
            stats.windowControlSeconds += (peerReady - controlStart) +
                (controlFinished - h2dFinished);
            stats.windowPayloadSeconds += (d2hFinished - payloadStart) +
                (h2dFinished - peerReady);
        }

        return true;
    }

  private:
    MPI_Comm node_ = MPI_COMM_NULL;
    MPI_Comm control_ = MPI_COMM_NULL;
    MPI_Win window_ = MPI_WIN_NULL;

    int worldRank_ = 0;
    int myNodeRank_ = -1;
    std::size_t slotBytes_ = 0;
    void* mySlot_ = nullptr;
    bool windowLocked_ = false;
    bool setupReady_ = false;
    int tagNegotiate_ = -1;
    int tagData_ = -1;
    int tagClose_ = -1;
    cudaStream_t d2hStream_ = nullptr;
    cudaStream_t h2dStream_ = nullptr;
    std::uint64_t sequence_ = 0;

    std::vector<WindowEntry> entries_;
    std::unordered_map<int, PairDecision> pairDecisions_;

    WindowEntry* findWorldRank(int worldRank) {
        for (WindowEntry& entry : entries_)
            if (entry.worldRank == worldRank)
                return &entry;
        return nullptr;
    }

    const WindowEntry* findWorldRank(int worldRank) const {
        for (const WindowEntry& entry : entries_)
            if (entry.worldRank == worldRank)
                return &entry;
        return nullptr;
    }

    bool queryTagUpperBound(MPI_Comm communicator, int& upperBound) {
        int isSet = 0;
        int* upperBoundPtr = nullptr;
        if (MPI_Comm_get_attr(communicator, MPI_TAG_UB, &upperBoundPtr, &isSet) != MPI_SUCCESS)
            return false;
        if (!isSet || upperBoundPtr == nullptr)
            return false;
        upperBound = *upperBoundPtr;
        return true;
    }

    void setup() {
        worldRank_ = comm_getRank();

        if (MPI_Comm_split_type(
                MPI_COMM_WORLD, MPI_COMM_TYPE_SHARED, 0, MPI_INFO_NULL, &node_) != MPI_SUCCESS)
            return;

        MPI_Comm_set_errhandler(node_, MPI_ERRORS_RETURN);
        if (MPI_Comm_rank(node_, &myNodeRank_) != MPI_SUCCESS)
            return;

        int nodeSize = 0;
        if (MPI_Comm_size(node_, &nodeSize) != MPI_SUCCESS)
            return;

        std::vector<int> worldRanks(nodeSize, -1);
        if (MPI_Allgather(
                &worldRank_, 1, MPI_INT, worldRanks.data(), 1, MPI_INT, node_) != MPI_SUCCESS)
            return;

        if (MPI_Comm_dup(node_, &control_) != MPI_SUCCESS)
            return;
        MPI_Comm_set_errhandler(control_, MPI_ERRORS_RETURN);

        int tagUpperBound = 0;
        if (!queryTagUpperBound(control_, tagUpperBound) &&
            !queryTagUpperBound(MPI_COMM_WORLD, tagUpperBound))
            return;
        if (tagUpperBound < MPI_CONTROL_TAG_COUNT - 1)
            return;

        tagNegotiate_ = tagUpperBound - (MPI_CONTROL_TAG_COUNT - 1);
        tagData_ = tagUpperBound - (MPI_CONTROL_TAG_COUNT - 2);
        tagClose_ = tagUpperBound;

        entries_.resize(nodeSize);
        for (int nodeRank = 0; nodeRank < nodeSize; nodeRank++) {
            entries_[nodeRank].worldRank = worldRanks[nodeRank];
            entries_[nodeRank].nodeRank = nodeRank;
        }

        MPI_Info info = MPI_INFO_NULL;
        if (MPI_Info_create(&info) != MPI_SUCCESS)
            return;
        // This is an optimisation hint only; correctness does not depend on
        // the implementation placing the ranks in disjoint local segments.
        MPI_Info_set(
            info,
            const_cast<char*>("alloc_shared_noncontig"),
            const_cast<char*>("true"));

        if (slotBytes_ > static_cast<std::size_t>(std::numeric_limits<MPI_Aint>::max()))
            return;
        MPI_Aint allocationBytes = static_cast<MPI_Aint>(slotBytes_);
        MPI_Win candidate = MPI_WIN_NULL;
        void* localSlot = nullptr;
        int allocationResult = MPI_Win_allocate_shared(
            allocationBytes, 1, info, node_, &localSlot, &candidate);
        MPI_Info_free(&info);

        int localAllocationOk = allocationResult == MPI_SUCCESS &&
            candidate != MPI_WIN_NULL && localSlot != nullptr;
        int allAllocationOk = 0;
        MPI_Allreduce(&localAllocationOk, &allAllocationOk, 1, MPI_INT, MPI_MIN, node_);
        if (!allAllocationOk) {
            int candidatePresent = candidate != MPI_WIN_NULL ? 1 : 0;
            int candidateMin = 0;
            int candidateMax = 0;
            MPI_Allreduce(&candidatePresent, &candidateMin, 1, MPI_INT, MPI_MIN, node_);
            MPI_Allreduce(&candidatePresent, &candidateMax, 1, MPI_INT, MPI_MAX, node_);
            if (candidateMin != candidateMax)
                abortWindowProtocol("asymmetric MPI shared-window allocation state");
            if (candidatePresent)
                MPI_Win_free(&candidate);
            return;
        }

        window_ = candidate;
        mySlot_ = localSlot;

        MPI_Win_set_errhandler(window_, MPI_ERRORS_RETURN);
        int lockResult = MPI_Win_lock_all(MPI_MODE_NOCHECK, window_);
        int localLockOk = lockResult == MPI_SUCCESS ? 1 : 0;
        int allLockOk = 0;
        MPI_Allreduce(&localLockOk, &allLockOk, 1, MPI_INT, MPI_MIN, node_);
        if (!allLockOk) {
            if (localLockOk)
                MPI_Win_unlock_all(window_);
            MPI_Win_free(&window_);
            mySlot_ = nullptr;
            return;
        }
        windowLocked_ = true;

        for (WindowEntry& entry : entries_) {
            MPI_Aint queryBytes = 0;
            int queryDisp = 0;
            void* querySlot = nullptr;
            int queryResult = MPI_Win_shared_query(
                window_, entry.nodeRank, &queryBytes, &queryDisp, &querySlot);
            if (queryResult != MPI_SUCCESS || queryBytes < static_cast<MPI_Aint>(slotBytes_) ||
                queryDisp != 1 || querySlot == nullptr)
                continue;

            entry.slot = querySlot;
            cudaError_t registerResult = cudaHostRegister(
                entry.slot, slotBytes_, cudaHostRegisterDefault);
            if (registerResult == cudaSuccess) {
                entry.registered = true;
            } else {
                // Clear a registration error before the raw fallback path is
                // allowed to continue on this rank.
                cudaGetLastError();
            }
        }

        cudaError_t d2hResult = cudaStreamCreateWithFlags(&d2hStream_, cudaStreamNonBlocking);
        cudaError_t h2dResult = cudaStreamCreateWithFlags(&h2dStream_, cudaStreamNonBlocking);
        setupReady_ = d2hResult == cudaSuccess && h2dResult == cudaSuccess;
        if (!setupReady_) {
            if (d2hResult != cudaSuccess)
                cudaGetLastError();
            if (h2dResult != cudaSuccess)
                cudaGetLastError();
        }
    }

    bool resolvePair(int pairRank) {
        PairDecision& decision = pairDecisions_[pairRank];
        if (decision.resolved)
            return decision.eligible;

        const WindowEntry* peer = findWorldRank(pairRank);
        if (peer == nullptr || peer->worldRank == worldRank_) {
            decision.resolved = true;
            decision.eligible = false;
            noteOffNodeFallback();
            return false;
        }

        bool localEligible = setupReady_ &&
            !forceWindowFallbackForRank(worldRank_) &&
            !forceWindowFallbackForRank(pairRank) &&
            findWorldRank(worldRank_) != nullptr &&
            findWorldRank(worldRank_)->registered &&
            peer->registered;
        int localToken = localEligible ? 1 : 0;
        int peerToken = 0;
        const double controlStart = MPI_Wtime();
        int result = MPI_Sendrecv(
            &localToken, 1, MPI_INT, peer->nodeRank, tagNegotiate_,
            &peerToken, 1, MPI_INT, peer->nodeRank, tagNegotiate_,
            control_, MPI_STATUS_IGNORE);
        if (result != MPI_SUCCESS)
            abortWindowProtocol("pairwise eligibility negotiation failed");
        const double controlFinished = MPI_Wtime();

        StagingStats& stats = stagingStats();
        if (stats.enabled) {
            stats.windowControlBytes += 2 * sizeof(localToken);
            stats.windowControlSeconds += controlFinished - controlStart;
        }

        decision.resolved = true;
        decision.eligible = localToken == 1 && peerToken == 1;
        if (!decision.eligible)
            noteRegistrationFallback();
        return decision.eligible;
    }

    void cleanup() {
        bool hasRegisteredMapping = false;
        for (const WindowEntry& entry : entries_)
            hasRegisteredMapping = hasRegisteredMapping || entry.registered;

        // The window slots may still be used by outstanding copies.  This
        // device-wide sync is teardown-only and never appears in the timed
        // exchange path.
        if (window_ != MPI_WIN_NULL || hasRegisteredMapping)
            gpu_sync();

        if (d2hStream_ != nullptr) {
            cudaStreamDestroy(d2hStream_);
            d2hStream_ = nullptr;
        }
        if (h2dStream_ != nullptr) {
            cudaStreamDestroy(h2dStream_);
            h2dStream_ = nullptr;
        }

        for (WindowEntry& entry : entries_) {
            if (entry.registered) {
                cudaHostUnregister(entry.slot);
                entry.registered = false;
            }
        }

        if (window_ != MPI_WIN_NULL) {
            if (windowLocked_)
                MPI_Win_unlock_all(window_);
            MPI_Win_free(&window_);
            windowLocked_ = false;
        }

        if (control_ != MPI_COMM_NULL)
            MPI_Comm_free(&control_);
        if (node_ != MPI_COMM_NULL)
            MPI_Comm_free(&node_);
    }
};

std::unordered_map<qcomp*, std::unique_ptr<CommWindow>>& windowsByCpuBuffer() {
    static std::unordered_map<qcomp*, std::unique_ptr<CommWindow>> windows;
    return windows;
}

} // namespace


void comm_window_initForQureg(Qureg qureg) {
    if (!comm_isBulkAsyncEnabled() || !qureg.isDistributed || !qureg.isGpuAccelerated)
        return;

    // T-069 deliberately retains cpuCommBuffer for raw fallback.  The
    // shared window adds one full-payload host slot per rank, so opted-in
    // Quregs have a transitional +1 payload host-memory footprint; VRAM is
    // unchanged.  This replaces the old (and false here) "no footprint
    // change" statement.
    windowsByCpuBuffer().emplace(qureg.cpuCommBuffer,
        std::make_unique<CommWindow>(qureg));
}


void comm_window_destroyForQureg(Qureg qureg) {
    auto& windows = windowsByCpuBuffer();
    auto it = windows.find(qureg.cpuCommBuffer);
    if (it == windows.end())
        return;
    windows.erase(it);
}


bool comm_window_tryExchange(
    Qureg qureg, qcomp* gpuSend, qcomp* gpuRecv, qindex numAmps, int pairRank) {
    auto& windows = windowsByCpuBuffer();
    auto it = windows.find(qureg.cpuCommBuffer);
    if (it == windows.end())
        return false;

    bool exchanged = it->second->tryExchange(qureg, gpuSend, gpuRecv, numAmps, pairRank);
    if (!exchanged && numAmps == qureg.numAmpsPerNode)
        noteRawFallback();
    return exchanged;
}


bool comm_window_isAvailable() {
    return true;
}


bool comm_window_statsEnabled() {
    initialiseStats();
    return stagingStats().enabled;
}


void comm_window_recordPayloadMpi(std::size_t bytes, double seconds) {
    noteRawPayload(bytes, seconds);
}

#else

void comm_window_initForQureg(Qureg) {}
void comm_window_destroyForQureg(Qureg) {}

bool comm_window_tryExchange(Qureg, qcomp*, qcomp*, qindex, int) {
    return false;
}

bool comm_window_isAvailable() {
    return false;
}

bool comm_window_statsEnabled() {
    return false;
}

void comm_window_recordPayloadMpi(std::size_t, double) {}

#endif
