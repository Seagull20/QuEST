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
#include "quest/src/comm/comm_compression.hpp"
#include "quest/src/core/profiling.hpp"
#include "quest/src/gpu/gpu_config.hpp"
#include "quest/src/gpu/gpu_subroutines.hpp"

#include <cstdlib>
#include <cerrno>
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

constexpr std::size_t NO_PRIOR_UNIT = std::numeric_limits<std::size_t>::max();

constexpr std::uint32_t WINDOW_PROTOCOL_VERSION = 1;
constexpr std::uint32_t GATED_OFF_ENCODING = 0;
constexpr std::uint32_t BITCOMP_ENCODING = 1;
// T-079: the sink contract's third per-unit state — "the codec was attempted
// and did not shrink this unit", as against GATED_OFF's "never attempted".
// Both mean stored == raw and a plain H2D at the receiver; the distinction is
// provenance.  Only the fused path ever writes it, so the descriptor LAYOUT is
// unchanged and every other path's validation stays exactly as it was.
constexpr std::uint32_t RAW_FALLBACK_ENCODING = 2;
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

    // T-079: set once the fused codec-in-pipeline path has actually run, so
    // the reported mode names what executed rather than what was requested.
    bool fusedCodec = false;

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

    const char* mode = stats.fusedCodec? "tiled_materialize_codec" :
        (comm_isBulkAsyncEnabled()? "bulk_async" :
        (comm_isTiledMaterializeEnabled()? "tiled_materialize" : "raw"));
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
        if (!configureTileGeometry())
            abortWindowProtocol("invalid QUEST_GPU_STAGING_TILE_BYTES/MB");
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

#ifdef COMPILE_NVCOMP
        // T-040 A3 (codec-over-window): the vote below is gated on the
        // rank-uniform candidacy check, so compression-off runs never pay for
        // (or even see) the extra control message and A1 stays byte-identical
        // to its measured form. The vote itself carries per-rank context
        // health, which is NOT uniform: a one-sided nvcomp init failure makes
        // the pair agree on the raw protocol instead of desynchronising.
        //
        // T-079: the tiled branch used to be taken BEFORE this vote, so a
        // configuration asking for both ran tiled staging with the codec
        // inactive. The vote now runs first and dispatches to the fused
        // pipeline. Every other combination reaches exactly the branch it
        // reached before: compression-off runs fail the candidacy check and
        // never see this control message, a bulk run votes and dispatches as
        // it always did, and a failed vote falls through to the tiled or raw
        // branch below.
        if (comm_compression_windowCodecCandidate(static_cast<qindex>(numAmps))) {
            const bool fused = comm_isTiledMaterializeEnabled();
            // The fused arm's streams, managers, device staging and events are
            // built HERE, not at window set-up: only past resolvePair is the
            // pair known to be on-node, registered and eligible, so a run that
            // never reaches the fused path never pays for it — and never risks
            // its memory abort.  The attempt is made once and cached.
            if (fused)
                ensureFusedPipeline();
            std::uint8_t localCodec = (fused?
                (fusedReady_ && comm_compression_windowPipelineUsable(
                    static_cast<qindex>(numAmps))) :
                comm_compression_windowCodecUsable(
                    static_cast<qindex>(numAmps))) ? 1 : 0;
            std::uint8_t peerCodec = 0;
            if (MPI_Sendrecv(
                    &localCodec, 1, MPI_BYTE, peer->nodeRank, tagNegotiate_,
                    &peerCodec, 1, MPI_BYTE, peer->nodeRank, tagNegotiate_,
                    control_, MPI_STATUS_IGNORE) != MPI_SUCCESS)
                abortWindowProtocol("codec vote exchange failed");
            comm_compression_recordControl(sizeof(localCodec));
            if (localCodec && peerCodec)
                return fused?
                    tryEncodedTiledExchange(gpuSend, gpuRecv, payloadBytes, pairRank, peer) :
                    tryEncodedBulkExchange(gpuSend, gpuRecv, payloadBytes, pairRank, peer);
        }
#endif

        if (comm_isTiledMaterializeEnabled())
            return tryTiledExchange(qureg, gpuSend, gpuRecv, numAmps, pairRank, peer);

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

#ifdef COMPILE_NVCOMP
    // T-040 A3: chunked codec-over-window bulk exchange. Mirrors the MPI
    // codec path's 64 MiB chunk loop, but every payload byte crosses through
    // the shared-host slot: compressed chunks land at their raw chunk offset
    // (regions are disjoint since stored <= raw), each chunk publishes one
    // descriptor whose encoding/storedBytes fields carry what the old MPI
    // size handshake carried, and ONE close pair ends the exchange. The two
    // directions decide encoding independently — the window protocol has no
    // Sendrecv symmetry constraint, so an incompressible chunk on one side
    // gates only that side off (GATED_OFF through the slot, never MPI).
    // mpi_payload_bytes therefore stays 0 on this path by construction.
    bool tryEncodedBulkExchange(qcomp* gpuSend, qcomp* gpuRecv,
            std::size_t payloadBytes, int pairRank, const WindowEntry* peer) {

        const std::size_t chunkBytes = comm_compression_chunkBytes();
        if (chunkBytes == 0)
            abortWindowProtocol("codec chunk size is zero after a successful vote");

        ++sequence_;

        // The codec runs on its own stream; make all prior gate work visible
        // to it the same way the MPI codec path does. This is the evidence
        // arm, priced for correctness first (same sync the A2 arm pays).
        cudaError_t syncResult = cudaDeviceSynchronize();
        if (syncResult != cudaSuccess)
            abortWindowProtocol("device synchronize before encoded exchange failed");

        auto* src = reinterpret_cast<const std::uint8_t*>(gpuSend);
        auto* dst = reinterpret_cast<std::uint8_t*>(gpuRecv);
        auto* slotBase = static_cast<std::uint8_t*>(mySlot_);

        StagingStats& stats = stagingStats();
        double controlSeconds = 0;
        double payloadSeconds = 0;
        std::size_t controlBytes = 0;

        for (std::size_t offset = 0; offset < payloadBytes; offset += chunkBytes) {
            const std::size_t bytes = std::min(chunkBytes, payloadBytes - offset);

            // encode (or gate off) this direction's chunk
            const double encodeStart = MPI_Wtime();
            const std::size_t compSize = comm_compression_compressChunk(src + offset, bytes);
            const bool encoded = compSize > 0;
            const std::size_t storedBytes = encoded ? compSize : bytes;
            const void* d2hSource = encoded ?
                comm_compression_deviceCompressedSend() :
                static_cast<const void*>(src + offset);

            {
                QuestProfileRange d2hRange("quest.communication.d2h");
                cudaError_t result = cudaMemcpyAsync(slotBase + offset, d2hSource,
                    storedBytes, cudaMemcpyDeviceToHost, d2hStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("encoded D2H into window slot failed");
                result = cudaStreamSynchronize(d2hStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("encoded D2H stream synchronization failed");
            }
            const double d2hFinished = MPI_Wtime();
            payloadSeconds += d2hFinished - encodeStart;

            const double controlStart = MPI_Wtime();
            if (MPI_Win_sync(window_) != MPI_SUCCESS)
                abortWindowProtocol("MPI_Win_sync before encoded token failed");

            WindowDescriptor descriptor {
                WINDOW_PROTOCOL_VERSION,
                encoded ? BITCOMP_ENCODING : GATED_OFF_ENCODING,
                sequence_,
                static_cast<std::uint64_t>(worldRank_),
                static_cast<std::uint64_t>(bytes),
                static_cast<std::uint64_t>(storedBytes),
                static_cast<std::uint64_t>(offset)
            };
            WindowDescriptor peerDescriptor {};
            {
                QuestProfileRange mpiRange("quest.communication.mpi");
                int result = MPI_Sendrecv(
                    &descriptor, static_cast<int>(sizeof(descriptor)), MPI_BYTE,
                    peer->nodeRank, tagData_,
                    &peerDescriptor, static_cast<int>(sizeof(peerDescriptor)), MPI_BYTE,
                    peer->nodeRank, tagData_, control_, MPI_STATUS_IGNORE);
                if (result != MPI_SUCCESS)
                    abortWindowProtocol("encoded chunk token exchange failed");
            }

            if (MPI_Win_sync(window_) != MPI_SUCCESS)
                abortWindowProtocol("MPI_Win_sync after encoded token failed");

            const bool peerEncoded = peerDescriptor.encoding == BITCOMP_ENCODING;
            // NOTE: sequence_ is a per-rank counter and pairs change with the
            // distributed target, so the two sides of a pair legitimately
            // disagree on it — the raw bulk path omits the check for the same
            // reason. Do not validate it.
            if (peerDescriptor.version != WINDOW_PROTOCOL_VERSION ||
                (!peerEncoded && peerDescriptor.encoding != GATED_OFF_ENCODING) ||
                peerDescriptor.producerWorldRank != static_cast<std::uint64_t>(pairRank) ||
                peerDescriptor.payloadBytes != bytes ||
                peerDescriptor.byteOffset != offset ||
                peerDescriptor.storedBytes == 0 ||
                peerDescriptor.storedBytes > bytes ||
                (!peerEncoded && peerDescriptor.storedBytes != bytes))
                abortWindowProtocol("peer token does not describe the requested encoded chunk");

            const double peerReady = MPI_Wtime();
            controlSeconds += peerReady - controlStart;
            controlBytes += 2 * sizeof(descriptor);

            const std::size_t peerStored = static_cast<std::size_t>(peerDescriptor.storedBytes);
            const std::uint8_t* peerChunk = static_cast<const std::uint8_t*>(peer->slot) + offset;
            {
                QuestProfileRange h2dRange("quest.communication.h2d");
                void* h2dTarget = peerEncoded ?
                    comm_compression_deviceCompressedRecv() :
                    static_cast<void*>(dst + offset);
                cudaError_t result = cudaMemcpyAsync(h2dTarget, peerChunk,
                    peerStored, cudaMemcpyHostToDevice, h2dStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("encoded H2D from peer slot failed");
                result = cudaStreamSynchronize(h2dStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("encoded H2D stream synchronization failed");
            }
            if (peerEncoded)
                comm_compression_decompressChunk(dst + offset, bytes);
            payloadSeconds += MPI_Wtime() - peerReady;

            // this rank's send accounting (peer records its own side)
            comm_compression_recordChunk(bytes, storedBytes, !encoded);
        }

        // one close pair for the whole exchange: chunk regions are disjoint,
        // so the producer never rewrites a slot region the peer still reads;
        // the close only guards the NEXT exchange's slot reuse.
        const double closeStart = MPI_Wtime();
        unsigned char closeToken = 1;
        unsigned char peerCloseToken = 0;
        {
            QuestProfileRange mpiRange("quest.communication.mpi");
            int result = MPI_Sendrecv(
                &closeToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                &peerCloseToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                control_, MPI_STATUS_IGNORE);
            if (result != MPI_SUCCESS || peerCloseToken != 1)
                abortWindowProtocol("encoded exchange close failed");
        }
        controlSeconds += MPI_Wtime() - closeStart;
        controlBytes += 2 * sizeof(closeToken);

        if (stats.enabled) {
            stats.windowExchanges++;
            stats.windowControlBytes += controlBytes;
            stats.windowControlSeconds += controlSeconds;
            stats.windowPayloadSeconds += payloadSeconds;
        }

        return true;
    }

    // T-079 spec A3 (win/on/tiled), D-021's committed core: the codec runs
    // INSIDE the tiled staging pipeline instead of alongside it. The unit is
    // the tile — D-032 merges the tile and chunk axes, so
    // QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES is not consulted here and the
    // unit actually in force is printed once by the codec's start-up line.
    // Per unit: encode on the codec's encode stream, stage the encoded bytes
    // into this rank's slot at the unit's RAW offset (regions stay disjoint
    // because stored <= raw), publish the unchanged T-069 descriptor, read the
    // peer's bytes back one iteration later, and decode them on a second codec
    // stream. An incompressible unit falls back to raw inside its own slot
    // region with encoding gated off, exactly as the bulk codec path does per
    // chunk, so mpi_payload_bytes stays 0 on this path too.
    //
    // The ordering constraint that survives the pipelining: the encoded length
    // is known only once the encode kernel has finished, and both the outbound
    // copy length and the descriptor depend on it. Unit k's encode therefore
    // overlaps the transfers and decodes of EARLIER units and never its own,
    // which is why unit k+1's encode is launched inside iteration k — before
    // that iteration's outbound wait and control round-trip, into the other
    // send slot.
    bool tryEncodedTiledExchange(qcomp* gpuSend, qcomp* gpuRecv,
            std::size_t payloadBytes, int pairRank, const WindowEntry* peer) {

        if (tileBytes_ == 0 || maxTileCount_ == 0 || !fusedReady_ ||
            d2hDoneEvents_.empty() || h2dDoneEvents_.empty() ||
            encodeDoneEvents_.empty() || decodeDoneEvents_.empty())
            abortWindowProtocol("fused mode has no prepared pipeline events");

        const std::size_t unitCount = payloadBytes / tileBytes_ +
            (payloadBytes % tileBytes_ != 0 ? 1 : 0);
        if (unitCount == 0 || unitCount > maxTileCount_ ||
            unitCount > d2hDoneEvents_.size() || unitCount > h2dDoneEvents_.size() ||
            unitCount > encodeDoneEvents_.size() || unitCount > decodeDoneEvents_.size())
            abortWindowProtocol("payload does not fit the prepared fused pipeline");

        ++sequence_;

        // The pipeline is fully drained before this routine returns, so the
        // slot occupancy and the retained-configuration ownership are both
        // per-exchange state and must not survive one.
        recvSlotOccupant_[0] = NO_PRIOR_UNIT;
        recvSlotOccupant_[1] = NO_PRIOR_UNIT;
        decodeConfigOwner_[0] = NO_PRIOR_UNIT;
        decodeConfigOwner_[1] = NO_PRIOR_UNIT;

        auto* src = reinterpret_cast<const std::uint8_t*>(gpuSend);
        auto* dst = reinterpret_cast<std::uint8_t*>(gpuRecv);
        auto* slotBase = static_cast<std::uint8_t*>(mySlot_);
        auto encodeStream = reinterpret_cast<cudaStream_t>(comm_compression_encodeStream());

        // Both streams that read gpuSend must see prior gate and cuQuantum
        // work.  This is the stream-scoped replacement for the bulk codec
        // path's device-wide synchronize; the uncompressed tiled path pays
        // the same one for its D2H stream.
        gpu_waitForPriorWorkOnStream(reinterpret_cast<void*>(encodeStream));
        gpu_waitForPriorWorkOnStream(reinterpret_cast<void*>(d2hStream_));

        double payloadSeconds = 0;
        double controlSeconds = 0;
        int result = cudaSuccess;

        const double fillStart = MPI_Wtime();
        comm_compression_launchEncode(src, unitBytesAt(0, payloadBytes), 0);
        result = cudaEventRecord(encodeDoneEvents_[0], encodeStream);
        if (result != cudaSuccess)
            abortWindowProtocol("fused encode event record failed");
        payloadSeconds += MPI_Wtime() - fillStart;

        for (std::size_t unit = 0; unit < unitCount; unit++) {
            const std::size_t byteOffset = unit * tileBytes_;
            const std::size_t rawBytes = unitBytesAt(unit, payloadBytes);
            const unsigned parity = static_cast<unsigned>(unit & 1u);

            const double payloadStart = MPI_Wtime();

            // Launch the previous unit's inbound copy before waiting for this
            // unit's encode, the same one-iteration deferral the uncompressed
            // tiled path uses for its H2D: the previous descriptor was
            // published during the preceding iteration, so that peer slot
            // range is ready to read.
            if (unit > 0)
                launchDeferredInbound(unit - 1, dst, peer);

            // This unit's encoded length is knowable only now.
            result = cudaEventSynchronize(encodeDoneEvents_[unit]);
            if (result != cudaSuccess)
                abortWindowProtocol("fused encode event synchronization failed");

            // Classify the unit into the sink contract's three states.  The
            // gate lives inside the codec and was applied before any nvcomp
            // call, so a below-gate unit is GATED_OFF, an attempted-but-lost
            // one is RAW_FALLBACK, and only the encoded case reads the slot.
            std::size_t storedBytes = 0;
            const unsigned unitState =
                comm_compression_unitEncoding(parity, rawBytes, &storedBytes);
            const bool encoded = unitState == COMM_COMPRESSION_UNIT_ENCODED;
            const std::uint32_t unitEncoding = encoded? BITCOMP_ENCODING :
                (unitState == COMM_COMPRESSION_UNIT_RAW_FALLBACK?
                    RAW_FALLBACK_ENCODING : GATED_OFF_ENCODING);

            // The encode event has fired, so nvcomp is done with this unit's
            // configuration object and it may be released before the next unit
            // on this parity replaces it.
            comm_compression_releaseEncodeConfig(parity);

            {
                QuestProfileRange d2hRange("quest.communication.d2h");
                const void* d2hSource = encoded?
                    comm_compression_pipelineSendSlot(parity) :
                    static_cast<const void*>(src + byteOffset);
                result = cudaMemcpyAsync(slotBase + byteOffset, d2hSource,
                    storedBytes, cudaMemcpyDeviceToHost, d2hStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("fused D2H into window slot failed");
                result = cudaEventRecord(d2hDoneEvents_[unit], d2hStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("fused D2H event record failed");
            }

            // The next unit's encode is the one stage allowed to run ahead.
            // It writes the OTHER send slot, so it cannot touch the bytes the
            // outbound copy above is still reading.  The slot it does write
            // was last read by unit-1's outbound copy; that copy was waited
            // for on the host during the previous iteration, and the
            // dependency is re-expressed here so the invariant does not rest
            // on where that host wait happens to sit.
            if (unit + 1 < unitCount) {
                if (unit >= 1) {
                    result = cudaStreamWaitEvent(encodeStream, d2hDoneEvents_[unit - 1], 0);
                    if (result != cudaSuccess)
                        abortWindowProtocol("fused encode wait on prior outbound copy failed");
                }
                comm_compression_launchEncode(src + byteOffset + rawBytes,
                    unitBytesAt(unit + 1, payloadBytes), parity ^ 1u);
                result = cudaEventRecord(encodeDoneEvents_[unit + 1], encodeStream);
                if (result != cudaSuccess)
                    abortWindowProtocol("fused encode event record failed");
            }

            // The peer reads our slot as soon as it holds the descriptor, so
            // this unit's outbound copy must have landed before we publish.
            result = cudaEventSynchronize(d2hDoneEvents_[unit]);
            if (result != cudaSuccess)
                abortWindowProtocol("fused D2H event synchronization failed");
            payloadSeconds += MPI_Wtime() - payloadStart;

            const double controlStart = MPI_Wtime();
            if (MPI_Win_sync(window_) != MPI_SUCCESS)
                abortWindowProtocol("fused MPI_Win_sync before token failed");

            WindowDescriptor descriptor {
                WINDOW_PROTOCOL_VERSION,
                unitEncoding,
                sequence_,
                static_cast<std::uint64_t>(worldRank_),
                static_cast<std::uint64_t>(rawBytes),
                static_cast<std::uint64_t>(storedBytes),
                static_cast<std::uint64_t>(byteOffset)
            };
            WindowDescriptor peerDescriptor {};
            {
                QuestProfileRange mpiRange("quest.communication.mpi");
                result = MPI_Sendrecv(
                    &descriptor, static_cast<int>(sizeof(descriptor)), MPI_BYTE,
                    peer->nodeRank, tagData_,
                    &peerDescriptor, static_cast<int>(sizeof(peerDescriptor)), MPI_BYTE,
                    peer->nodeRank, tagData_, control_, MPI_STATUS_IGNORE);
                if (result != MPI_SUCCESS)
                    abortWindowProtocol("fused unit token exchange failed");
            }

            if (MPI_Win_sync(window_) != MPI_SUCCESS)
                abortWindowProtocol("fused MPI_Win_sync after token failed");

            const bool peerEncoded = peerDescriptor.encoding == BITCOMP_ENCODING;
            // Both raw states are accepted and treated identically on receipt:
            // stored == raw, plain H2D, no decode.  Only their provenance
            // differs.
            const bool peerStagedRaw =
                peerDescriptor.encoding == GATED_OFF_ENCODING ||
                peerDescriptor.encoding == RAW_FALLBACK_ENCODING;
            // NOTE: sequence_ is a per-rank counter and pairs change with the
            // distributed target, so the two sides of a pair legitimately
            // disagree on it — the bulk codec path omits the check for the
            // same reason. Do not validate it.
            if (peerDescriptor.version != WINDOW_PROTOCOL_VERSION ||
                (!peerEncoded && !peerStagedRaw) ||
                peerDescriptor.producerWorldRank != static_cast<std::uint64_t>(pairRank) ||
                peerDescriptor.payloadBytes != rawBytes ||
                peerDescriptor.byteOffset != byteOffset ||
                peerDescriptor.storedBytes == 0 ||
                peerDescriptor.storedBytes > rawBytes ||
                (!peerEncoded && peerDescriptor.storedBytes != rawBytes) ||
                (peerEncoded && peerDescriptor.storedBytes >
                    comm_compression_windowPipelineSlotCapacity()))
                abortWindowProtocol("peer token does not describe the requested fused unit");

            // Cached for the deferred stages: the inbound copy of this unit
            // runs next iteration and its decode the iteration after, both of
            // them needing this descriptor's stored length and encoding flag.
            peerStoredBytes_[unit] = peerDescriptor.storedBytes;
            peerEncodedFlags_[unit] = peerEncoded ? 1 : 0;
            controlSeconds += MPI_Wtime() - controlStart;

            // The previous unit's decode is launched after the control
            // round-trip so its inbound copy has had the whole iteration to
            // land: configuring an nvcomp decode reads the unit header out of
            // the recv slot and blocks the host until it is there.
            const double decodeStart = MPI_Wtime();
            if (unit > 0)
                launchDeferredDecode(unit - 1, dst, unitBytesAt(unit - 1, payloadBytes));
            payloadSeconds += MPI_Wtime() - decodeStart;

            // this rank's send accounting (peer records its own side)
            comm_compression_recordChunk(rawBytes, storedBytes, !encoded);
        }

        // The final unit has no following iteration in which to run its
        // deferred stages, so they are drained here.  The waits are on the two
        // dedicated streams only, never device-wide: every raw unit's inbound
        // copy is ordered ahead of the last one on the single H2D stream, and
        // every decode is ordered on the single decode stream.
        const std::size_t lastUnit = unitCount - 1;
        const double drainStart = MPI_Wtime();
        launchDeferredInbound(lastUnit, dst, peer);
        launchDeferredDecode(lastUnit, dst, unitBytesAt(lastUnit, payloadBytes));
        result = cudaEventSynchronize(h2dDoneEvents_[lastUnit]);
        if (result != cudaSuccess)
            abortWindowProtocol("fused final inbound synchronization failed");
        result = cudaStreamSynchronize(
            reinterpret_cast<cudaStream_t>(comm_compression_decodeStream()));
        if (result != cudaSuccess)
            abortWindowProtocol("fused decode stream synchronization failed");
        // Every decode has now completed, so the two retained decode
        // configurations are provably unused and nothing survives the
        // exchange.
        comm_compression_releaseDecodeConfig(0);
        comm_compression_releaseDecodeConfig(1);
        payloadSeconds += MPI_Wtime() - drainStart;

        // one close pair for the whole exchange, as on the bulk codec path:
        // unit regions are disjoint, so the producer never rewrites a slot
        // region the peer still reads; the close guards the NEXT exchange.
        const double closeStart = MPI_Wtime();
        unsigned char closeToken = 1;
        unsigned char peerCloseToken = 0;
        {
            QuestProfileRange mpiRange("quest.communication.mpi");
            result = MPI_Sendrecv(
                &closeToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                &peerCloseToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                control_, MPI_STATUS_IGNORE);
            if (result != MPI_SUCCESS || peerCloseToken != 1)
                abortWindowProtocol("fused exchange close failed");
        }
        controlSeconds += MPI_Wtime() - closeStart;

        StagingStats& stats = stagingStats();
        if (stats.enabled) {
            stats.fusedCodec = true;
            stats.windowExchanges++;
            stats.windowControlBytes += 2 *
                (unitCount * sizeof(WindowDescriptor) + sizeof(closeToken));
            stats.windowControlSeconds += controlSeconds;
            stats.windowPayloadSeconds += payloadSeconds;
        }

        return true;
    }
#endif // COMPILE_NVCOMP

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
    std::size_t tileBytes_ = 0;
    std::size_t maxTileCount_ = 0;
    std::vector<cudaEvent_t> d2hDoneEvents_;
    std::vector<cudaEvent_t> h2dDoneEvents_;
    std::uint64_t sequence_ = 0;

    // T-079 fused pipeline state.  Allocated only when the codec-in-pipeline
    // arm is actually prepared, so every other mode keeps its exact set-up.
    bool fusedReady_ = false;
    std::vector<cudaEvent_t> encodeDoneEvents_;
    std::vector<cudaEvent_t> decodeDoneEvents_;
    std::vector<std::uint64_t> peerStoredBytes_;
    std::vector<unsigned char> peerEncodedFlags_;
    // Which unit last occupied each of the two encoded-recv slots.  This is
    // NOT simply unit-2: a unit the peer could not compress arrives raw
    // straight into gpuRecv and never touches a slot, so the decode that must
    // finish before a slot is refilled can lie further back.
    std::size_t recvSlotOccupant_[2] = { NO_PRIOR_UNIT, NO_PRIOR_UNIT };
    // Which unit owns each parity's retained nvcomp decode configuration.
    // Tracked separately from recvSlotOccupant_ because the inbound copy
    // updates that one before the decode of the same unit is launched.
    std::size_t decodeConfigOwner_[2] = { NO_PRIOR_UNIT, NO_PRIOR_UNIT };
    bool fusedAttempted_ = false;

    std::vector<WindowEntry> entries_;
    std::unordered_map<int, PairDecision> pairDecisions_;

    WindowEntry* findWorldRank(int worldRank) {
        for (WindowEntry& entry : entries_)
            if (entry.worldRank == worldRank)
                return &entry;
        return nullptr;
    }

    bool configureTileGeometry() {
        if (!comm_isTiledMaterializeEnabled())
            return true;
        if (slotBytes_ == 0 || slotBytes_ % sizeof(qcomp) != 0)
            return false;

        const char* bytesText = std::getenv("QUEST_GPU_STAGING_TILE_BYTES");
        const char* megabytesText = std::getenv("QUEST_GPU_STAGING_TILE_MB");
        const bool explicitSize = bytesText != nullptr || megabytesText != nullptr;
        std::uint64_t requested = 16ULL * 1024ULL * 1024ULL;
        if (bytesText != nullptr && *bytesText != '\0') {
            errno = 0;
            char* end = nullptr;
            unsigned long long parsed = std::strtoull(bytesText, &end, 10);
            if (errno == ERANGE || end == bytesText || *end != '\0')
                return false;
            requested = static_cast<std::uint64_t>(parsed);
        } else if (megabytesText != nullptr && *megabytesText != '\0') {
            errno = 0;
            char* end = nullptr;
            unsigned long long parsed = std::strtoull(megabytesText, &end, 10);
            if (errno == ERANGE || end == megabytesText || *end != '\0' ||
                parsed > std::numeric_limits<std::uint64_t>::max() / (1024ULL * 1024ULL))
                return false;
            requested = static_cast<std::uint64_t>(parsed) * 1024ULL * 1024ULL;
        } else if (explicitSize) {
            return false;
        }

        if (!explicitSize && requested > slotBytes_)
            requested = slotBytes_;
        if (requested == 0 || requested > slotBytes_ ||
            requested % sizeof(qcomp) != 0 ||
            requested > std::numeric_limits<std::size_t>::max())
            return false;

        tileBytes_ = static_cast<std::size_t>(requested);
        maxTileCount_ = slotBytes_ / tileBytes_ + (slotBytes_ % tileBytes_ != 0 ? 1 : 0);
        return maxTileCount_ > 0;
    }

    std::size_t unitBytesAt(std::size_t unit, std::size_t payloadBytes) const {
        const std::size_t byteOffset = unit * tileBytes_;
        return std::min(tileBytes_, payloadBytes - byteOffset);
    }

#ifdef COMPILE_NVCOMP
    // The fused pipeline's two deferred stages.  Both read the descriptor
    // values cached one iteration earlier, and both express their buffer
    // dependencies on the stream rather than on the host, because neither the
    // inbound copy nor the decode is ever waited for in the iteration that
    // launches it.
    void launchDeferredInbound(std::size_t unit, std::uint8_t* dst, const WindowEntry* peer) {
        const std::size_t byteOffset = unit * tileBytes_;
        const unsigned parity = static_cast<unsigned>(unit & 1u);
        const bool encoded = peerEncodedFlags_[unit] != 0;
        const std::size_t storedBytes = static_cast<std::size_t>(peerStoredBytes_[unit]);

        // The recv slots are double buffered by unit parity, so this copy
        // refills the buffer the slot's previous occupant is still being
        // decoded out of.  That decode is never waited for on the host, hence
        // the stream dependency.  Its launch happened one iteration after its
        // own inbound copy, so it is always already recorded here.
        if (encoded) {
            const std::size_t previous = recvSlotOccupant_[parity];
            if (previous != NO_PRIOR_UNIT &&
                cudaStreamWaitEvent(h2dStream_, decodeDoneEvents_[previous], 0) != cudaSuccess)
                abortWindowProtocol("fused inbound wait on prior decode failed");
            recvSlotOccupant_[parity] = unit;
        }

        QuestProfileRange h2dRange("quest.communication.h2d");
        void* h2dTarget = encoded?
            comm_compression_pipelineRecvSlot(parity) :
            static_cast<void*>(dst + byteOffset);
        cudaError_t result = cudaMemcpyAsync(h2dTarget,
            static_cast<const std::uint8_t*>(peer->slot) + byteOffset,
            storedBytes, cudaMemcpyHostToDevice, h2dStream_);
        if (result != cudaSuccess)
            abortWindowProtocol("fused H2D from peer slot failed");
        result = cudaEventRecord(h2dDoneEvents_[unit], h2dStream_);
        if (result != cudaSuccess)
            abortWindowProtocol("fused H2D event record failed");
    }

    void launchDeferredDecode(std::size_t unit, std::uint8_t* dst, std::size_t rawBytes) {
        // a unit the peer could not compress arrived raw, already in place
        if (peerEncodedFlags_[unit] == 0)
            return;

        auto decodeStream = reinterpret_cast<cudaStream_t>(comm_compression_decodeStream());
        const std::size_t byteOffset = unit * tileBytes_;
        const unsigned parity = static_cast<unsigned>(unit & 1u);

        // nvcomp keeps its decode configuration alive across the work it
        // enqueues, so the one retained for this parity may only be released
        // once its own decode has finished.  That decode belongs to a unit at
        // least two back, launched at least two iterations ago, and is already
        // an ordering prerequisite of the inbound copy issued earlier in THIS
        // iteration — so this wait is expected to be satisfied on arrival, and
        // it is the only place the invariant is enforced rather than assumed.
        const std::size_t previousConfigOwner = decodeConfigOwner_[parity];
        if (previousConfigOwner != NO_PRIOR_UNIT) {
            if (cudaEventSynchronize(decodeDoneEvents_[previousConfigOwner]) != cudaSuccess)
                abortWindowProtocol("fused decode configuration release wait failed");
            comm_compression_releaseDecodeConfig(parity);
        }
        decodeConfigOwner_[parity] = unit;

        if (cudaStreamWaitEvent(decodeStream, h2dDoneEvents_[unit], 0) != cudaSuccess)
            abortWindowProtocol("fused decode wait on inbound copy failed");
        comm_compression_launchDecode(dst + byteOffset, rawBytes, parity);
        if (cudaEventRecord(decodeDoneEvents_[unit], decodeStream) != cudaSuccess)
            abortWindowProtocol("fused decode event record failed");
    }
#endif // COMPILE_NVCOMP

    // T-079: a rank that cannot bring the fused arm up votes the codec down
    // and the pair runs the uncompressed tiled path — the existing fallback
    // shape, and the only consensus-safe answer to a one-sided failure. That
    // silence is itself the hazard: a campaign would then measure win/off/tiled
    // while believing it measured win/on/tiled. So the fallback names itself,
    // per rank and with its cause, in a form the verification harness rejects
    // outright. The reported staging mode is the second, independent tell: it
    // stays `tiled_materialize` when no fused exchange ran.
    void reportFusedDisabled(const char* reason) {
        std::fprintf(stderr,
            "[quest-staging] A3_INIT_FALLBACK rank=%d reason=%s — the fused "
            "win/on/tiled arm is DISABLED on this rank; the pair will vote the "
            "codec down and run win/off/tiled instead\n",
            worldRank_, reason);
        std::fflush(stderr);
    }

    // Builds the fused arm on first use, once the pair is already known to be
    // eligible for the window.  Cached: one attempt per window, success or
    // failure, so the vote is stable for the lifetime of the Qureg.
    void ensureFusedPipeline() {
#ifdef COMPILE_NVCOMP
        if (fusedAttempted_)
            return;
        fusedAttempted_ = true;

        if (!setupReady_ || tileBytes_ == 0 || maxTileCount_ == 0) {
            reportFusedDisabled("window set-up did not complete on this rank");
            return;
        }
        if (!comm_compression_prepareWindowPipeline(tileBytes_)) {
            reportFusedDisabled("nvcomp pipeline initialisation failed "
                "(see the preceding [quest-nvcomp] line for the cause)");
            return;
        }
        if (!createFusedPipelineEvents())
            return; // reports its own CUDA error

        fusedReady_ = true;
#endif
    }

    bool createFusedPipelineEvents() {
        encodeDoneEvents_.reserve(maxTileCount_);
        decodeDoneEvents_.reserve(maxTileCount_);
        for (std::size_t unit = 0; unit < maxTileCount_; unit++) {
            cudaEvent_t encodeEvent = nullptr;
            cudaEvent_t decodeEvent = nullptr;
            if (cudaEventCreateWithFlags(&encodeEvent, cudaEventDisableTiming) != cudaSuccess ||
                cudaEventCreateWithFlags(&decodeEvent, cudaEventDisableTiming) != cudaSuccess) {
                // Clear the creation error before the vote is allowed to gate
                // this rank's codec off, and say what it was.
                char reason[256];
                std::snprintf(reason, sizeof(reason),
                    "CUDA event creation failed after %zu of %zu unit events: %s",
                    encodeDoneEvents_.size(), maxTileCount_,
                    cudaGetErrorString(cudaGetLastError()));
                reportFusedDisabled(reason);
                if (encodeEvent != nullptr)
                    cudaEventDestroy(encodeEvent);
                if (decodeEvent != nullptr)
                    cudaEventDestroy(decodeEvent);
                for (cudaEvent_t event : encodeDoneEvents_)
                    cudaEventDestroy(event);
                for (cudaEvent_t event : decodeDoneEvents_)
                    cudaEventDestroy(event);
                encodeDoneEvents_.clear();
                decodeDoneEvents_.clear();
                return false;
            }
            encodeDoneEvents_.push_back(encodeEvent);
            decodeDoneEvents_.push_back(decodeEvent);
        }
        peerStoredBytes_.assign(maxTileCount_, 0);
        peerEncodedFlags_.assign(maxTileCount_, 0);
        return maxTileCount_ > 0;
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

    bool tryTiledExchange(
        Qureg qureg, qcomp* gpuSend, qcomp* gpuRecv, qindex numAmps, int pairRank,
        const WindowEntry* peer) {
        if (tileBytes_ == 0 || maxTileCount_ == 0 ||
            d2hDoneEvents_.empty() || h2dDoneEvents_.empty())
            abortWindowProtocol("tiled mode has no prepared tile events");

        const std::size_t payloadBytes = static_cast<std::size_t>(numAmps) * sizeof(qcomp);
        const std::size_t tileCount = payloadBytes / tileBytes_ +
            (payloadBytes % tileBytes_ != 0 ? 1 : 0);
        if (tileCount == 0 || tileCount > maxTileCount_ ||
            tileCount > d2hDoneEvents_.size() || tileCount > h2dDoneEvents_.size())
            abortWindowProtocol("payload does not fit the prepared tiled window");

        ++sequence_;
        gpu_waitForPriorWorkOnStream(reinterpret_cast<void*>(d2hStream_));

        double d2hSeconds = 0;
        double h2dSeconds = 0;
        double controlSeconds = 0;
        int result = cudaSuccess;
        for (std::size_t tile = 0; tile < tileCount; tile++) {
            const std::size_t byteOffset = tile * tileBytes_;
            const std::size_t rawBytes = std::min(tileBytes_, payloadBytes - byteOffset);
            const std::size_t elemOffset = byteOffset / sizeof(qcomp);

            // Launch the previous consumer copy before waiting for this
            // producer tile.  The previous descriptor was published during
            // the preceding iteration, so its peer slot range is ready to
            // read while the next D2H is in flight.
            if (tile > 0) {
                const std::size_t previousOffset = (tile - 1) * tileBytes_;
                const std::size_t previousBytes =
                    std::min(tileBytes_, payloadBytes - previousOffset);
                const double h2dLaunchStart = MPI_Wtime();
                {
                    QuestProfileRange h2dRange("quest.communication.h2d");
                    result = cudaMemcpyAsync(
                        gpuRecv + previousOffset / sizeof(qcomp),
                        static_cast<const unsigned char*>(peer->slot) + previousOffset,
                        previousBytes, cudaMemcpyHostToDevice, h2dStream_);
                    if (result != cudaSuccess)
                        abortWindowProtocol("tiled cudaMemcpyAsync H2D failed");
                    result = cudaEventRecord(h2dDoneEvents_[tile - 1], h2dStream_);
                    if (result != cudaSuccess)
                        abortWindowProtocol("tiled H2D event record failed");
                }
                h2dSeconds += MPI_Wtime() - h2dLaunchStart;
            }

            const double d2hStart = MPI_Wtime();
            {
                QuestProfileRange d2hRange("quest.communication.d2h");
                result = cudaMemcpyAsync(
                    static_cast<unsigned char*>(mySlot_) + byteOffset,
                    gpuSend + elemOffset, rawBytes,
                    cudaMemcpyDeviceToHost, d2hStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("tiled cudaMemcpyAsync D2H failed");
                result = cudaEventRecord(d2hDoneEvents_[tile], d2hStream_);
                if (result != cudaSuccess)
                    abortWindowProtocol("tiled D2H event record failed");
            }
            result = cudaEventSynchronize(d2hDoneEvents_[tile]);
            if (result != cudaSuccess)
                abortWindowProtocol("tiled D2H event synchronization failed");
            d2hSeconds += MPI_Wtime() - d2hStart;

            const double controlStart = MPI_Wtime();
            if (MPI_Win_sync(window_) != MPI_SUCCESS)
                abortWindowProtocol("tiled MPI_Win_sync before token failed");

            WindowDescriptor descriptor {
                WINDOW_PROTOCOL_VERSION,
                GATED_OFF_ENCODING,
                sequence_,
                static_cast<std::uint64_t>(worldRank_),
                static_cast<std::uint64_t>(rawBytes),
                static_cast<std::uint64_t>(rawBytes),
                static_cast<std::uint64_t>(byteOffset)
            };
            WindowDescriptor peerDescriptor {};
            {
                QuestProfileRange mpiRange("quest.communication.mpi");
                result = MPI_Sendrecv(
                    &descriptor, static_cast<int>(sizeof(descriptor)), MPI_BYTE,
                    peer->nodeRank, tagData_,
                    &peerDescriptor, static_cast<int>(sizeof(peerDescriptor)), MPI_BYTE,
                    peer->nodeRank, tagData_, control_, MPI_STATUS_IGNORE);
                if (result != MPI_SUCCESS)
                    abortWindowProtocol("tiled pairwise data token exchange failed");
            }

            if (MPI_Win_sync(window_) != MPI_SUCCESS)
                abortWindowProtocol("tiled MPI_Win_sync after token failed");

            if (peerDescriptor.version != WINDOW_PROTOCOL_VERSION ||
                peerDescriptor.encoding != GATED_OFF_ENCODING ||
                peerDescriptor.sequence != sequence_ ||
                peerDescriptor.payloadBytes != rawBytes ||
                peerDescriptor.storedBytes != rawBytes ||
                peerDescriptor.byteOffset != byteOffset ||
                peerDescriptor.producerWorldRank != static_cast<std::uint64_t>(pairRank))
                abortWindowProtocol("tiled peer token does not describe the requested payload");
            controlSeconds += MPI_Wtime() - controlStart;
        }

        // The final tile has no following iteration in which to launch its
        // H2D.  Queue it after its token is published, then wait only for the
        // dedicated H2D stream.  This is a stream/event wait, never a
        // device-wide synchronization.
        const std::size_t lastTile = tileCount - 1;
        const std::size_t lastOffset = lastTile * tileBytes_;
        const std::size_t lastBytes = std::min(tileBytes_, payloadBytes - lastOffset);
        const double h2dWaitStart = MPI_Wtime();
        {
            QuestProfileRange h2dRange("quest.communication.h2d");
            result = cudaMemcpyAsync(
                gpuRecv + lastOffset / sizeof(qcomp),
                static_cast<const unsigned char*>(peer->slot) + lastOffset,
                lastBytes, cudaMemcpyHostToDevice, h2dStream_);
            if (result != cudaSuccess)
                abortWindowProtocol("tiled final cudaMemcpyAsync H2D failed");
            result = cudaEventRecord(h2dDoneEvents_[lastTile], h2dStream_);
            if (result != cudaSuccess)
                abortWindowProtocol("tiled final H2D event record failed");
            result = cudaEventSynchronize(h2dDoneEvents_[lastTile]);
            if (result != cudaSuccess)
                abortWindowProtocol("tiled final H2D event synchronization failed");
        }
        h2dSeconds += MPI_Wtime() - h2dWaitStart;

        unsigned char closeToken = 1;
        unsigned char peerCloseToken = 0;
        const double closeStart = MPI_Wtime();
        {
            QuestProfileRange mpiRange("quest.communication.mpi");
            result = MPI_Sendrecv(
                &closeToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                &peerCloseToken, 1, MPI_BYTE, peer->nodeRank, tagClose_,
                control_, MPI_STATUS_IGNORE);
            if (result != MPI_SUCCESS || peerCloseToken != 1)
                abortWindowProtocol("tiled pairwise close failed");
        }
        controlSeconds += MPI_Wtime() - closeStart;

        StagingStats& stats = stagingStats();
        if (stats.enabled) {
            stats.windowExchanges++;
            stats.windowControlBytes += 2 *
                (tileCount * sizeof(WindowDescriptor) + sizeof(closeToken));
            stats.windowControlSeconds += controlSeconds;
            stats.windowPayloadSeconds += d2hSeconds + h2dSeconds;
        }

        (void) qureg;
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

        if (setupReady_ && comm_isTiledMaterializeEnabled()) {
            bool eventsReady = true;
            d2hDoneEvents_.reserve(maxTileCount_);
            h2dDoneEvents_.reserve(maxTileCount_);
            for (std::size_t tile = 0; tile < maxTileCount_; tile++) {
                cudaEvent_t d2hEvent = nullptr;
                cudaEvent_t h2dEvent = nullptr;
                if (cudaEventCreateWithFlags(&d2hEvent, cudaEventDisableTiming) != cudaSuccess ||
                    cudaEventCreateWithFlags(&h2dEvent, cudaEventDisableTiming) != cudaSuccess) {
                    if (d2hEvent != nullptr)
                        cudaEventDestroy(d2hEvent);
                    if (h2dEvent != nullptr)
                        cudaEventDestroy(h2dEvent);
                    eventsReady = false;
                    break;
                }
                d2hDoneEvents_.push_back(d2hEvent);
                h2dDoneEvents_.push_back(h2dEvent);
            }
            if (!eventsReady) {
                for (cudaEvent_t event : d2hDoneEvents_)
                    cudaEventDestroy(event);
                for (cudaEvent_t event : h2dDoneEvents_)
                    cudaEventDestroy(event);
                d2hDoneEvents_.clear();
                h2dDoneEvents_.clear();
                setupReady_ = false;
            }
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

        for (cudaEvent_t event : d2hDoneEvents_)
            cudaEventDestroy(event);
        for (cudaEvent_t event : h2dDoneEvents_)
            cudaEventDestroy(event);
        for (cudaEvent_t event : encodeDoneEvents_)
            cudaEventDestroy(event);
        for (cudaEvent_t event : decodeDoneEvents_)
            cudaEventDestroy(event);
        d2hDoneEvents_.clear();
        h2dDoneEvents_.clear();
        encodeDoneEvents_.clear();
        decodeDoneEvents_.clear();
        fusedReady_ = false;

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
    if (!comm_isWindowStagingEnabled() || !qureg.isDistributed || !qureg.isGpuAccelerated)
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
