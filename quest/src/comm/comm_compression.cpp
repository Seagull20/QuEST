/** @file
 * Implementation of the experimental Bitcomp-compressed CPU-staged exchange.
 * Ported from the validated standalone benchmark (compression_exchange.cu,
 * campaign compression_generalization_main_combined_3519907_3519909:
 * median 13.2x exchange-time speedup on structured >=16 MiB payloads).
 * See comm_compression.hpp for the runtime contract.
 *
 * @author Zeyu Lin (experimental fork feature; not upstream QuEST)
 */

#include "quest/src/comm/comm_compression.hpp"

#ifdef COMPILE_NVCOMP

#if !COMPILE_MPI
#error "ENABLE_NVCOMP requires ENABLE_DISTRIBUTION (COMPILE_MPI)"
#endif

#include "quest/src/core/profiling.hpp"

#include <mpi.h>
#include <cuda_runtime.h>
#include <nvcomp.hpp>
#include <nvcomp/nvcompManager.hpp>
#include <nvcomp/bitcomp.hpp>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <utility>

namespace {

// tags used on a private duplicated communicator (see g_comm), so they
// can never collide with QuEST's raw-path tags on MPI_COMM_WORLD
constexpr int TAG_ACTIVE = 1;
constexpr int TAG_SIZE   = 2;
constexpr int TAG_DATA   = 3;
constexpr int TAG_VERIFY = 4;

#define COMM_COMPRESSION_CUDA_ABORT(call)                                        \
    do {                                                                          \
        cudaError_t err_ = (call);                                                \
        if (err_ != cudaSuccess) {                                                \
            std::fprintf(stderr,                                                  \
                "[quest-nvcomp] FATAL %s: %s (post-agreement, cannot fall back)\n",\
                #call, cudaGetErrorString(err_));                                 \
            MPI_Abort(MPI_COMM_WORLD, 1);                                         \
        }                                                                         \
    } while (0)

constexpr std::size_t NVCOMP_INTERNAL_CHUNK = std::size_t(1) << 20; // 1 MiB, as in the campaign

struct Config {
    bool enabled = false;
    bool verify = false;
    bool stats = false;
    bool forceRaw = false; // T-075: encoding-only ablation, see comm_compression_tryExchange
    std::size_t minBytes   = std::size_t(16) << 20; // SIZE_LIMITED boundary
    std::size_t chunkBytes = std::size_t(64) << 20; // campaign's best large-payload chunk
};

std::size_t readEnvBytes(const char* name, std::size_t fallback) {
    const char* v = std::getenv(name);
    if (v == nullptr || *v == '\0')
        return fallback;
    char* end = nullptr;
    unsigned long long parsed = std::strtoull(v, &end, 10);
    if (end == v || parsed == 0)
        return fallback;
    return static_cast<std::size_t>(parsed);
}

const Config& getConfig() {
    static Config cfg = [] {
        Config c;
        const char* on = std::getenv("QUEST_ENABLE_EXCHANGE_COMPRESSION");
        c.enabled = (on != nullptr && on[0] == '1');
        const char* vf = std::getenv("QUEST_EXCHANGE_COMPRESSION_VERIFY");
        c.verify = (vf != nullptr && vf[0] == '1');
        const char* sf = std::getenv("QUEST_EXCHANGE_COMPRESSION_STATS");
        c.stats = (sf != nullptr && sf[0] == '1');
        const char* fr = std::getenv("QUEST_EXCHANGE_COMPRESSION_FORCE_RAW");
        c.forceRaw = (fr != nullptr && fr[0] == '1');
        c.minBytes = readEnvBytes("QUEST_EXCHANGE_COMPRESSION_MIN_BYTES", c.minBytes);
        c.chunkBytes = readEnvBytes("QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES", c.chunkBytes);
        // chunk must be an amp multiple, sane, and single-MPI-message safe (< INT_MAX)
        c.chunkBytes -= c.chunkBytes % sizeof(qcomp);
        if (c.chunkBytes < (std::size_t(1) << 20)) c.chunkBytes = std::size_t(1) << 20;
        if (c.chunkBytes > (std::size_t(1) << 30)) c.chunkBytes = std::size_t(1) << 30;
        if (c.enabled) {
            std::fprintf(stderr,
                "[quest-nvcomp] exchange compression ENABLED (bitcomp, chunk=%zu MiB, "
                "threshold=%zu MiB, verify=%d, stats=%d, force_raw=%d)\n",
                c.chunkBytes >> 20, c.minBytes >> 20, (int) c.verify, (int) c.stats,
                (int) c.forceRaw);
        }
        if (c.enabled && c.forceRaw) {
            std::fprintf(stderr,
                "[quest-nvcomp] FORCE_RAW arm: codec never invoked; every chunk takes the "
                "raw fallback through the same pinned buffers and chunk loop (T-075)\n");
        }
        return c;
    }();
    return cfg;
}

struct CompressionStats {
    bool registered = false;
    bool rankKnown = false;
    int rank = -1;
    unsigned long long rawBytes = 0;
    unsigned long long sentBytes = 0;
    unsigned long long compressedChunks = 0;
    unsigned long long fallbackChunks = 0;
    unsigned long long controlBytes = 0;
};

void printStatsAtExit();

CompressionStats& getStats() {
    static CompressionStats stats;
    const Config& cfg = getConfig();
    if (cfg.stats && !stats.registered) {
        stats.registered = true;
        std::atexit(printStatsAtExit);
    }
    return stats;
}

void ensureStatsRank(CompressionStats& stats) {
    if (stats.rankKnown)
        return;
    int mpiInitialised = 0;
    int mpiFinalised = 0;
    MPI_Initialized(&mpiInitialised);
    MPI_Finalized(&mpiFinalised);
    if (mpiInitialised && !mpiFinalised && MPI_Comm_rank(MPI_COMM_WORLD, &stats.rank) == MPI_SUCCESS)
        stats.rankKnown = true;
}

void printStatsAtExit() {
    const Config& cfg = getConfig();
    if (!cfg.stats)
        return;
    CompressionStats& stats = getStats();
    std::fprintf(stderr,
        "[quest-nvcomp-stats] rank=%d raw_bytes=%llu sent_bytes=%llu "
        "compressed_chunks=%llu fallback_chunks=%llu control_bytes=%llu\n",
        stats.rank,
        stats.rawBytes,
        stats.sentBytes,
        stats.compressedChunks,
        stats.fallbackChunks,
        stats.controlBytes);
    std::fflush(stderr);
}

void addControlBytes(std::size_t bytes) {
    const Config& cfg = getConfig();
    if (!cfg.stats)
        return;
    CompressionStats& stats = getStats();
    ensureStatsRank(stats);
    stats.controlBytes += static_cast<unsigned long long>(bytes);
}

void addChunkStats(std::size_t rawBytes, std::uint64_t sentBytes, bool fallback) {
    const Config& cfg = getConfig();
    if (!cfg.stats)
        return;
    CompressionStats& stats = getStats();
    ensureStatsRank(stats);
    stats.rawBytes += static_cast<unsigned long long>(rawBytes);
    stats.sentBytes += static_cast<unsigned long long>(sentBytes);
    if (fallback)
        stats.fallbackChunks++;
    else
        stats.compressedChunks++;
}

// private communicator, dup'ed collectively in comm_compression_init()
MPI_Comm g_comm = MPI_COMM_NULL;

// Set once by comm_compression_init(): every rank owns the private
// communicator and agreed on the configuration. Fails closed (compression
// never activates) if init was somehow not reached.
bool g_uniform = false;

// Persistent per-process context: one stream, one Bitcomp manager, scratch
// buffers bounded by the logical chunk size. Heap-allocated on first use and
// intentionally leaked whole (never destructed), so no nvcomp/CUDA destructor
// can run after CUDA/MPI teardown at exit.

struct Context {
    cudaStream_t stream = nullptr;
    std::unique_ptr<nvcomp::nvcompManagerBase> manager;
    std::size_t maxCompBytes = 0;
    std::uint8_t* dCompSend = nullptr;
    std::uint8_t* dCompRecv = nullptr;
    std::size_t* dCompSize = nullptr;
    std::uint8_t* hCompSend = nullptr; // pinned
    std::uint8_t* hCompRecv = nullptr; // pinned
    std::uint8_t* hRawSend = nullptr;  // pinned, chunk-sized (fallback + verify)
    std::uint8_t* hRawRecv = nullptr;  // pinned, chunk-sized
    std::uint8_t* hVerify = nullptr;   // pinned, chunk-sized (verify only)
    bool ok = false;
};

bool cudaOk(cudaError_t err, const char* what) {
    if (err == cudaSuccess)
        return true;
    std::fprintf(stderr, "[quest-nvcomp] %s failed: %s — compression disabled\n",
                 what, cudaGetErrorString(err));
    return false;
}

Context& getContext() {
    static Context* ctxPtr = [] {
        auto* cp = new Context(); // leaked by design (see struct comment)
        Context& c = *cp;
        const Config& cfg = getConfig();
        try {
            if (!cudaOk(cudaStreamCreate(&c.stream), "cudaStreamCreate")) return cp;

            auto opts = nvcompBatchedBitcompCompressDefaultOpts;
            // match the campaign benchmark exactly: DOUBLE where the nvcomp
            // version provides it, ULONGLONG otherwise (same 8-byte lanes)
#ifdef NVCOMP_TYPE_DOUBLE
            opts.data_type = NVCOMP_TYPE_DOUBLE;
#else
            opts.data_type = NVCOMP_TYPE_ULONGLONG;
#endif
            c.manager = std::make_unique<nvcomp::BitcompManager>(
                NVCOMP_INTERNAL_CHUNK, opts, nvcompBatchedBitcompDecompressDefaultOpts,
                c.stream, nvcomp::NoComputeNoVerify, nvcomp::BitstreamKind::NVCOMP_NATIVE);

            auto maxCfg = c.manager->configure_compression(cfg.chunkBytes);
            c.maxCompBytes = maxCfg.max_compressed_buffer_size;

            if (!cudaOk(cudaMalloc(&c.dCompSend, c.maxCompBytes), "cudaMalloc dCompSend")) return cp;
            if (!cudaOk(cudaMalloc(&c.dCompRecv, c.maxCompBytes), "cudaMalloc dCompRecv")) return cp;
            if (!cudaOk(cudaMalloc(&c.dCompSize, sizeof(std::size_t)), "cudaMalloc dCompSize")) return cp;
            if (!cudaOk(cudaMallocHost(&c.hCompSend, c.maxCompBytes), "cudaMallocHost hCompSend")) return cp;
            if (!cudaOk(cudaMallocHost(&c.hCompRecv, c.maxCompBytes), "cudaMallocHost hCompRecv")) return cp;
            if (!cudaOk(cudaMallocHost(&c.hRawSend, cfg.chunkBytes), "cudaMallocHost hRawSend")) return cp;
            if (!cudaOk(cudaMallocHost(&c.hRawRecv, cfg.chunkBytes), "cudaMallocHost hRawRecv")) return cp;
            if (cfg.verify &&
                !cudaOk(cudaMallocHost(&c.hVerify, cfg.chunkBytes), "cudaMallocHost hVerify")) return cp;
            c.ok = true;
        } catch (const std::exception& e) {
            std::fprintf(stderr, "[quest-nvcomp] init failed: %s — compression disabled\n", e.what());
            c.ok = false;
        }
        return cp;
    }();
    return *ctxPtr;
}

// Global agreement that every rank sees the same configuration, performed
// once by comm_compression_init(). A per-rank env mismatch
// (enabled/threshold/chunk/verify/force_raw) would otherwise desynchronise the pair —
// one rank in the compressed protocol, its peer in the raw one (deadlock).
//
// This MUST run from a rank-symmetric point, which the exchange path is not:
// only the *pair* is symmetric there. A gate whose control qubit lies in the
// prefix substate makes every rank with the wrong rank-index bit return from
// the localiser before reaching comm at all (doAnyLocalStatesHaveQubitValues,
// localiser.cpp), so a WORLD collective placed in the exchange is joined by
// only a subset of ranks.
bool agreeOnConfig() {
    const Config& cfg = getConfig();
    long long vals[6] = {
        (long long) cfg.enabled, (long long) cfg.minBytes,
        (long long) cfg.chunkBytes, (long long) cfg.verify,
        (long long) cfg.forceRaw, 0 };
    // last field: this rank can create the private communicator
    vals[5] = (MPI_Comm_dup(MPI_COMM_WORLD, &g_comm) == MPI_SUCCESS) ? 1 : 0;
    long long mins[6], maxs[6];
    MPI_Allreduce(vals, mins, 6, MPI_LONG_LONG, MPI_MIN, MPI_COMM_WORLD);
    MPI_Allreduce(vals, maxs, 6, MPI_LONG_LONG, MPI_MAX, MPI_COMM_WORLD);
    if (mins[5] != 1) {
        std::fprintf(stderr, "[quest-nvcomp] MPI_Comm_dup failed on some rank — "
                             "compression disabled on ALL ranks\n");
        return false;
    }
    for (int i = 0; i < 5; i++) {
        if (mins[i] != maxs[i]) {
            std::fprintf(stderr,
                "[quest-nvcomp] config differs across ranks (field %d) — "
                "compression disabled on ALL ranks\n", i);
            return false;
        }
    }
    return true;
}

// T-079 spec A3 (win/on/tiled): the fused window pipeline's own context.
// Deliberately separate from Context above — that one backs the measured
// win/on/bulk arms, so neither its allocation nor its synchronisation
// structure may move underneath them. Heap-allocated once and leaked whole
// for the same teardown reason as Context.
// The nvcomp configuration types, named through the manager rather than
// spelled out, so this stays correct across nvcomp releases that rename or
// re-namespace them. A configuration must outlive the work it configures —
// every other call site in this file keeps one alive until a stream
// synchronise — so the pipeline retains them per parity rather than letting
// them die at enqueue. C++17's guaranteed copy elision constructs them
// directly on the heap, so neither type needs to be copyable or movable.
using PipelineEncodeConfig = decltype(
    std::declval<nvcomp::nvcompManagerBase&>().configure_compression(std::size_t(0)));
using PipelineDecodeConfig = decltype(
    std::declval<nvcomp::nvcompManagerBase&>().configure_decompression(
        std::declval<std::uint8_t*>()));

struct PipelineContext {
    cudaStream_t encodeStream = nullptr;
    cudaStream_t decodeStream = nullptr;
    std::unique_ptr<nvcomp::nvcompManagerBase> encodeManager;
    std::unique_ptr<nvcomp::nvcompManagerBase> decodeManager;
    std::size_t unitBytes = 0;
    std::size_t slotCapacity = 0;
    std::uint8_t* dSend[2] = { nullptr, nullptr };
    std::uint8_t* dRecv[2] = { nullptr, nullptr };
    std::size_t* dSize[2] = { nullptr, nullptr };
    std::size_t* hSize[2] = { nullptr, nullptr }; // pinned
    // whether the codec was invoked at all for the unit occupying each parity;
    // this is what separates GATED_OFF from RAW_FALLBACK
    unsigned char attempted[2] = { 0, 0 };
    PipelineEncodeConfig* encodeConfig[2] = { nullptr, nullptr };
    PipelineDecodeConfig* decodeConfig[2] = { nullptr, nullptr };
    bool ok = false;
};

PipelineContext* g_pipeline = nullptr;

int currentWorldRank() {
    int initialised = 0;
    int finalised = 0;
    int rank = -1;
    MPI_Initialized(&initialised);
    MPI_Finalized(&finalised);
    if (initialised && !finalised)
        MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    return rank;
}

// Device headroom left for nvcomp's own lazily-allocated scratch and for the
// allocator's granularity, so the arithmetic below refuses a unit that would
// only just fit and then fail inside the first encode.
constexpr std::size_t PIPELINE_DEVICE_MARGIN = std::size_t(64) << 20;

[[noreturn]] void abortPipelineMemory(const PipelineContext& c, std::size_t required) {
    std::size_t freeBytes = 0;
    std::size_t totalBytes = 0;
    if (cudaMemGetInfo(&freeBytes, &totalBytes) != cudaSuccess)
        cudaGetLastError();
    std::fprintf(stderr,
        "[quest-nvcomp] FATAL fused window pipeline does not fit: unit=%zu MiB, "
        "encoded slot capacity=%zu MiB, double-buffered device staging = "
        "2 send + 2 recv = %zu MiB (+%zu MiB reserved margin), device free=%zu MiB "
        "of %zu MiB. Lower QUEST_GPU_STAGING_TILE_MB or use fewer qubits per rank; "
        "refusing to downgrade silently to the uncompressed tiled arm\n",
        c.unitBytes >> 20, c.slotCapacity >> 20, required >> 20,
        PIPELINE_DEVICE_MARGIN >> 20, freeBytes >> 20, totalBytes >> 20);
    std::fflush(stderr);
    MPI_Abort(MPI_COMM_WORLD, 1);
    std::abort();
}

[[noreturn]] void abortPipelineUnitGrew(const PipelineContext& c, std::size_t unitBytes) {
    std::fprintf(stderr,
        "[quest-nvcomp] FATAL fused window pipeline was sized for a %zu MiB unit and "
        "a second window asked for %zu MiB; the staging slots would overrun\n",
        c.unitBytes >> 20, unitBytes >> 20);
    std::fflush(stderr);
    MPI_Abort(MPI_COMM_WORLD, 1);
    std::abort();
}

// One raw chunk exchange through pinned host staging (fallback + verify path).
void exchangeRawChunk(const std::uint8_t* dSrc, std::uint8_t* hSend, std::uint8_t* hRecv,
                      std::size_t bytes, int pairRank, int tag, cudaStream_t stream) {
    {
        QuestProfileRange r("quest.communication.d2h");
        COMM_COMPRESSION_CUDA_ABORT(cudaMemcpyAsync(hSend, dSrc, bytes, cudaMemcpyDeviceToHost, stream));
        COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(stream));
    }
    {
        QuestProfileRange r("quest.communication.mpi");
        MPI_Sendrecv(hSend, (int) bytes, MPI_BYTE, pairRank, tag,
                     hRecv, (int) bytes, MPI_BYTE, pairRank, tag,
                     g_comm, MPI_STATUS_IGNORE);
    }
}

} // anonymous namespace


void comm_compression_init() {

    // called from comm_init(), which every rank reaches unconditionally; this
    // is the only symmetric point available, so all WORLD collectives of this
    // module live here (see agreeOnConfig)
    g_uniform = agreeOnConfig();
}


bool comm_compression_tryExchange(qcomp* dSend, qcomp* dRecv, qindex numAmps, int pairRank) {

    const Config& cfg = getConfig();
    if (cfg.stats)
        ensureStatsRank(getStats());

    // agreed collectively at init; a plain load here, never a collective, as
    // this path is only pair-symmetric (see agreeOnConfig)
    if (!g_uniform)
        return false;

    if (!cfg.enabled)
        return false;

    if (numAmps <= 0 || (std::size_t) numAmps > SIZE_MAX / sizeof(qcomp))
        return false; // symmetric: numAmps identical on both ranks

    const std::size_t payloadBytes = (std::size_t) numAmps * sizeof(qcomp);
    if (payloadBytes < cfg.minBytes)
        return false; // symmetric, as above

    Context& ctx = getContext();

    // agree with the pair rank on taking the compressed path, so a one-sided
    // init failure can never desynchronise the MPI traffic below
    std::uint8_t localActive = ctx.ok ? 1 : 0, peerActive = 0;
    MPI_Sendrecv(&localActive, 1, MPI_UINT8_T, pairRank, TAG_ACTIVE,
                 &peerActive, 1, MPI_UINT8_T, pairRank, TAG_ACTIVE,
                 g_comm, MPI_STATUS_IGNORE);
    addControlBytes(sizeof(localActive));
    if (!localActive || !peerActive)
        return false;

    QuestProfileRange pathRange("quest.communication.exchange.cpu_staged.compressed");

    // prior gate kernels may still be writing the send buffer
    COMM_COMPRESSION_CUDA_ABORT(cudaDeviceSynchronize());

    auto* src = reinterpret_cast<const std::uint8_t*>(dSend);
    auto* dst = reinterpret_cast<std::uint8_t*>(dRecv);

    for (std::size_t offset = 0; offset < payloadBytes; offset += cfg.chunkBytes) {
        const std::size_t bytes = std::min(cfg.chunkBytes, payloadBytes - offset);
        std::uint64_t localCompSize = 0, peerCompSize = 0;

        if (cfg.forceRaw) {
            // T-075 encoding-only ablation: the codec is never invoked, so the
            // incompressible-chunk branch below claims every chunk. Everything
            // else is untouched — same chunk loop, same pinned hRawSend/hRawRecv
            // staging, same size handshake, same sync structure — so RAW differs
            // from ON in encoding ALONE, and from OFF in the plumbing alone.
            // Bit-identical by construction: raw bytes on both arms.
            localCompSize = 0;
        } else {
            QuestProfileRange r("quest.communication.compress");
            auto compCfg = ctx.manager->configure_compression(bytes);
            ctx.manager->compress(src + offset, ctx.dCompSend, compCfg, ctx.dCompSize);
            COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(ctx.stream));
            COMM_COMPRESSION_CUDA_ABORT(cudaMemcpy(&localCompSize, ctx.dCompSize, sizeof(std::size_t), cudaMemcpyDeviceToHost));
        }

        {
            QuestProfileRange r("quest.communication.size_exchange");
            MPI_Sendrecv(&localCompSize, 1, MPI_UINT64_T, pairRank, TAG_SIZE,
                         &peerCompSize, 1, MPI_UINT64_T, pairRank, TAG_SIZE,
                         g_comm, MPI_STATUS_IGNORE);
            addControlBytes(sizeof(localCompSize));
        }

        if (localCompSize == 0 || peerCompSize == 0 ||
            localCompSize >= bytes || peerCompSize >= bytes) {
            // incompressible chunk on either side: byte-exact raw fallback
            addChunkStats(bytes, bytes, true);
            exchangeRawChunk(src + offset, ctx.hRawSend, ctx.hRawRecv, bytes,
                             pairRank, TAG_DATA, ctx.stream);
            QuestProfileRange r("quest.communication.h2d");
            COMM_COMPRESSION_CUDA_ABORT(cudaMemcpyAsync(dst + offset, ctx.hRawRecv, bytes, cudaMemcpyHostToDevice, ctx.stream));
            COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(ctx.stream));
            continue;
        }

        {
            QuestProfileRange r("quest.communication.d2h");
            COMM_COMPRESSION_CUDA_ABORT(cudaMemcpyAsync(ctx.hCompSend, ctx.dCompSend, localCompSize,
                            cudaMemcpyDeviceToHost, ctx.stream));
            COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(ctx.stream));
        }
        {
            QuestProfileRange r("quest.communication.mpi");
            MPI_Sendrecv(ctx.hCompSend, (int) localCompSize, MPI_BYTE, pairRank, TAG_DATA,
                         ctx.hCompRecv, (int) peerCompSize, MPI_BYTE, pairRank, TAG_DATA,
                         g_comm, MPI_STATUS_IGNORE);
            addChunkStats(bytes, localCompSize, false);
        }
        {
            QuestProfileRange r("quest.communication.h2d");
            COMM_COMPRESSION_CUDA_ABORT(cudaMemcpyAsync(ctx.dCompRecv, ctx.hCompRecv, peerCompSize,
                            cudaMemcpyHostToDevice, ctx.stream));
            COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(ctx.stream));
        }
        {
            QuestProfileRange r("quest.communication.decompress");
            auto decompCfg = ctx.manager->configure_decompression(ctx.dCompRecv);
            ctx.manager->decompress(dst + offset, ctx.dCompRecv, decompCfg);
            COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(ctx.stream));
        }

        if (cfg.verify) {
            // debug shadow: raw-exchange the same chunk and byte-compare with
            // what the codec delivered (hRawRecv holds the peer raw chunk)
            exchangeRawChunk(src + offset, ctx.hRawSend, ctx.hRawRecv, bytes,
                             pairRank, TAG_VERIFY, ctx.stream);
            COMM_COMPRESSION_CUDA_ABORT(cudaMemcpy(ctx.hVerify, dst + offset, bytes, cudaMemcpyDeviceToHost));
            if (std::memcmp(ctx.hVerify, ctx.hRawRecv, bytes) != 0) {
                std::fprintf(stderr,
                    "[quest-nvcomp] VERIFY FAILED at offset %zu (%zu bytes) — aborting\n",
                    offset, bytes);
                MPI_Abort(MPI_COMM_WORLD, 1);
            }
        }
    }

    return true;
}


/*
 * Chunk-codec API for the window transport (T-040 A3). Thin process-local
 * wrappers over the module's context; no MPI in here — pair agreement and
 * transport belong to comm_window.cpp. See comm_compression.hpp.
 */

bool comm_compression_windowCodecCandidate(qindex numAmps) {

    const Config& cfg = getConfig();
    if (!g_uniform || !cfg.enabled)
        return false;

    // The debug verify mode shadows every chunk with a raw MPI exchange; that
    // shape cannot ride the window protocol, so verify keeps the MPI path.
    if (cfg.verify)
        return false;

    if (numAmps <= 0 || (std::size_t) numAmps > SIZE_MAX / sizeof(qcomp))
        return false;

    const std::size_t payloadBytes = (std::size_t) numAmps * sizeof(qcomp);
    if (payloadBytes < cfg.minBytes)
        return false;

    return true;
}

bool comm_compression_windowCodecUsable(qindex numAmps) {

    if (!comm_compression_windowCodecCandidate(numAmps))
        return false;

    if (getConfig().stats)
        ensureStatsRank(getStats());

    return getContext().ok;
}

std::size_t comm_compression_chunkBytes() {
    return getConfig().chunkBytes;
}

std::size_t comm_compression_compressChunk(const void* dSrc, std::size_t bytes) {

    const Config& cfg = getConfig();
    if (cfg.forceRaw)
        return 0; // T-075 ablation: same meaning as on the MPI path

    Context& ctx = getContext();
    std::uint64_t compSize = 0;
    {
        QuestProfileRange r("quest.communication.compress");
        auto compCfg = ctx.manager->configure_compression(bytes);
        ctx.manager->compress(static_cast<const std::uint8_t*>(dSrc),
            ctx.dCompSend, compCfg, ctx.dCompSize);
        COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(ctx.stream));
        COMM_COMPRESSION_CUDA_ABORT(cudaMemcpy(&compSize, ctx.dCompSize,
            sizeof(std::size_t), cudaMemcpyDeviceToHost));
    }

    // incompressible chunk: caller stages it raw (through the window, not MPI)
    if (compSize == 0 || compSize >= bytes)
        return 0;

    return static_cast<std::size_t>(compSize);
}

const void* comm_compression_deviceCompressedSend() {
    return getContext().dCompSend;
}

void* comm_compression_deviceCompressedRecv() {
    return getContext().dCompRecv;
}

void comm_compression_decompressChunk(void* dDst, std::size_t bytes) {

    Context& ctx = getContext();
    QuestProfileRange r("quest.communication.decompress");
    auto decompCfg = ctx.manager->configure_decompression(ctx.dCompRecv);
    if (decompCfg.decomp_data_size != bytes) {
        std::fprintf(stderr,
            "[quest-nvcomp] window chunk decode size mismatch (%zu != %zu) — aborting\n",
            static_cast<std::size_t>(decompCfg.decomp_data_size), bytes);
        MPI_Abort(MPI_COMM_WORLD, 1);
    }
    ctx.manager->decompress(static_cast<std::uint8_t*>(dDst), ctx.dCompRecv, decompCfg);
    COMM_COMPRESSION_CUDA_ABORT(cudaStreamSynchronize(ctx.stream));
}

void comm_compression_recordChunk(std::size_t rawBytes, std::size_t sentBytes, bool fallback) {
    addChunkStats(rawBytes, static_cast<std::uint64_t>(sentBytes), fallback);
}

void comm_compression_recordControl(std::size_t bytes) {
    addControlBytes(bytes);
}


/*
 * Fused pipeline API for the tiled window transport (T-079 spec A3). Every
 * entry point below launches work and returns: the window owns the ordering
 * through its own events, so nothing here synchronises a stream. The one
 * unavoidable host wait lives inside nvcomp's decode configuration, which
 * reads the inbound unit's header — which is why the decode has its own
 * stream and its own manager. See comm_compression.hpp.
 */

bool comm_compression_prepareWindowPipeline(std::size_t unitBytes) {

    const Config& cfg = getConfig();
    if (!g_uniform || !cfg.enabled || cfg.verify || unitBytes == 0)
        return false;

    if (g_pipeline != nullptr) {
        // one process may open several windows; the staging was sized by the
        // first, and a larger later unit would overrun it
        if (g_pipeline->ok && unitBytes > g_pipeline->unitBytes)
            abortPipelineUnitGrew(*g_pipeline, unitBytes);
        return g_pipeline->ok;
    }

    auto* pipeline = new PipelineContext(); // leaked by design (see struct comment)
    g_pipeline = pipeline;
    PipelineContext& c = *pipeline;
    c.unitBytes = unitBytes;

    try {
        // Non-blocking streams: the size read-back and the codec launches must
        // never be ordered against the legacy default stream, which the gate
        // kernels use.
        if (!cudaOk(cudaStreamCreateWithFlags(&c.encodeStream, cudaStreamNonBlocking),
                "cudaStreamCreateWithFlags encodeStream")) return false;
        if (!cudaOk(cudaStreamCreateWithFlags(&c.decodeStream, cudaStreamNonBlocking),
                "cudaStreamCreateWithFlags decodeStream")) return false;

        auto opts = nvcompBatchedBitcompCompressDefaultOpts;
        // same lane choice as the campaign benchmark and the chunk API above
#ifdef NVCOMP_TYPE_DOUBLE
        opts.data_type = NVCOMP_TYPE_DOUBLE;
#else
        opts.data_type = NVCOMP_TYPE_ULONGLONG;
#endif
        // One manager per stream: a manager is bound to the stream it was
        // constructed with, and this pipeline's whole point is that a unit's
        // encode may run while an earlier unit's decode is still running.
        c.encodeManager = std::make_unique<nvcomp::BitcompManager>(
            NVCOMP_INTERNAL_CHUNK, opts, nvcompBatchedBitcompDecompressDefaultOpts,
            c.encodeStream, nvcomp::NoComputeNoVerify, nvcomp::BitstreamKind::NVCOMP_NATIVE);
        c.decodeManager = std::make_unique<nvcomp::BitcompManager>(
            NVCOMP_INTERNAL_CHUNK, opts, nvcompBatchedBitcompDecompressDefaultOpts,
            c.decodeStream, nvcomp::NoComputeNoVerify, nvcomp::BitstreamKind::NVCOMP_NATIVE);

        c.slotCapacity =
            c.encodeManager->configure_compression(unitBytes).max_compressed_buffer_size;
    } catch (const std::exception& e) {
        std::fprintf(stderr,
            "[quest-nvcomp] fused pipeline init failed: %s — fused window arm disabled\n",
            e.what());
        return false;
    }

    // D-032's memory arithmetic, checked BEFORE allocating so a configuration
    // that cannot fit fails at window setup with the numbers in the message,
    // not inside the first exchange.
    const std::size_t required = 4 * c.slotCapacity + 2 * sizeof(std::size_t);
    std::size_t freeBytes = 0;
    std::size_t totalBytes = 0;
    if (cudaMemGetInfo(&freeBytes, &totalBytes) != cudaSuccess) {
        cudaGetLastError();
        freeBytes = 0;
        totalBytes = 0;
    }
    if (totalBytes > 0 && required + PIPELINE_DEVICE_MARGIN > freeBytes)
        abortPipelineMemory(c, required);

    // Past the arithmetic, an allocation failure is a surprise rather than a
    // configuration error — still fatal, for the same mislabelling reason.
    if (cudaMalloc(&c.dSend[0], c.slotCapacity) != cudaSuccess ||
        cudaMalloc(&c.dSend[1], c.slotCapacity) != cudaSuccess ||
        cudaMalloc(&c.dRecv[0], c.slotCapacity) != cudaSuccess ||
        cudaMalloc(&c.dRecv[1], c.slotCapacity) != cudaSuccess ||
        cudaMalloc(&c.dSize[0], sizeof(std::size_t)) != cudaSuccess ||
        cudaMalloc(&c.dSize[1], sizeof(std::size_t)) != cudaSuccess ||
        cudaMallocHost(&c.hSize[0], sizeof(std::size_t)) != cudaSuccess ||
        cudaMallocHost(&c.hSize[1], sizeof(std::size_t)) != cudaSuccess) {
        cudaGetLastError();
        abortPipelineMemory(c, required);
    }

    *c.hSize[0] = 0;
    *c.hSize[1] = 0;
    c.ok = true;

    // The effective unit is recorded here, per rank and in exact bytes, so a
    // campaign can prove from the job log which unit actually ran (D-032:
    // unit = tile = chunk). Floored MiB would be lossy — a 20,000,000-byte
    // unit and a 19 MiB unit are not the same run.
    std::fprintf(stderr,
        "[quest-nvcomp] fused window pipeline ENABLED (T-079 spec A3, win/on/tiled) "
        "rank=%d unit_bytes=%zu gate_bytes=%zu slot_capacity_bytes=%zu "
        "device_staging_bytes=%zu device_free_bytes=%zu device_total_bytes=%zu "
        "(tile = chunk; CHUNK_BYTES is not consulted on this path; the gate is "
        "applied per unit, not per exchange)\n",
        currentWorldRank(), c.unitBytes, cfg.minBytes, c.slotCapacity,
        required, freeBytes, totalBytes);
    std::fflush(stderr);
    return true;
}

bool comm_compression_windowPipelineUsable(qindex numAmps) {

    if (!comm_compression_windowCodecCandidate(numAmps))
        return false;

    if (getConfig().stats)
        ensureStatsRank(getStats());

    return g_pipeline != nullptr && g_pipeline->ok;
}

std::size_t comm_compression_windowPipelineUnitBytes() {
    return g_pipeline != nullptr ? g_pipeline->unitBytes : 0;
}

std::size_t comm_compression_windowPipelineSlotCapacity() {
    return g_pipeline != nullptr ? g_pipeline->slotCapacity : 0;
}

void* comm_compression_encodeStream() {
    return g_pipeline != nullptr ? reinterpret_cast<void*>(g_pipeline->encodeStream) : nullptr;
}

void* comm_compression_decodeStream() {
    return g_pipeline != nullptr ? reinterpret_cast<void*>(g_pipeline->decodeStream) : nullptr;
}

const void* comm_compression_pipelineSendSlot(unsigned parity) {
    return g_pipeline->dSend[parity & 1u];
}

void* comm_compression_pipelineRecvSlot(unsigned parity) {
    return g_pipeline->dRecv[parity & 1u];
}

void comm_compression_launchEncode(const void* dSrc, std::size_t bytes, unsigned parity) {

    const Config& cfg = getConfig();
    PipelineContext& c = *g_pipeline;
    const unsigned slot = parity & 1u;

    // The minimum-size gate is applied PER UNIT here. The exchange-wide check
    // in windowCodecCandidate only decides whether the protocol is worth
    // entering; a unit below the gate must reach the wire as GATED_OFF with
    // the codec never called, which is what the sink contract's stage_tile
    // specifies and what round 1 got wrong. The force-raw ablation is the same
    // state by construction: T-075's arm is "the codec is never invoked".
    if (cfg.forceRaw || bytes < cfg.minBytes) {
        c.attempted[slot] = 0;
        *c.hSize[slot] = 0;
        return;
    }

    c.attempted[slot] = 1;
    QuestProfileRange r("quest.communication.compress");
    // The configuration must outlive the compress it configures, so it is
    // retained here and released by the caller once this unit's encode event
    // has fired. Deleting first is a no-op after that release and keeps the
    // slot from leaking if a caller ever skips it.
    delete c.encodeConfig[slot];
    c.encodeConfig[slot] = new PipelineEncodeConfig(
        c.encodeManager->configure_compression(bytes));
    c.encodeManager->compress(static_cast<const std::uint8_t*>(dSrc),
        c.dSend[slot], *c.encodeConfig[slot], c.dSize[slot]);
    // The length lands in pinned host memory on the same stream, so the
    // caller's encode event covers it and no extra synchronisation is needed.
    COMM_COMPRESSION_CUDA_ABORT(cudaMemcpyAsync(c.hSize[slot], c.dSize[slot],
        sizeof(std::size_t), cudaMemcpyDeviceToHost, c.encodeStream));
}

unsigned comm_compression_unitEncoding(unsigned parity, std::size_t rawBytes,
        std::size_t* storedBytes) {

    const PipelineContext& c = *g_pipeline;
    const unsigned slot = parity & 1u;

    // codec never attempted for this unit: below the gate, or force-raw
    if (!c.attempted[slot]) {
        *storedBytes = rawBytes;
        return COMM_COMPRESSION_UNIT_GATED_OFF;
    }

    // attempted but lost: the encode ran and did not shrink the unit, or
    // returned something the staging slot could not have held
    const std::size_t stored = *c.hSize[slot];
    if (stored == 0 || stored >= rawBytes || stored > c.slotCapacity) {
        *storedBytes = rawBytes;
        return COMM_COMPRESSION_UNIT_RAW_FALLBACK;
    }

    *storedBytes = stored;
    return COMM_COMPRESSION_UNIT_ENCODED;
}

void comm_compression_launchDecode(void* dDst, std::size_t bytes, unsigned parity) {

    PipelineContext& c = *g_pipeline;
    const unsigned slot = parity & 1u;

    QuestProfileRange r("quest.communication.decompress");
    // nvcomp reads the unit header out of the recv slot here, so the decode
    // stream must already be waiting on the inbound copy — the caller's job.
    // The configuration is retained for the same lifetime reason as the encode
    // one, and released by the caller once this unit's decode event has fired.
    delete c.decodeConfig[slot];
    c.decodeConfig[slot] = new PipelineDecodeConfig(
        c.decodeManager->configure_decompression(c.dRecv[slot]));
    if (c.decodeConfig[slot]->decomp_data_size != bytes) {
        std::fprintf(stderr,
            "[quest-nvcomp] fused unit decode size mismatch (%zu != %zu) — aborting\n",
            static_cast<std::size_t>(c.decodeConfig[slot]->decomp_data_size), bytes);
        MPI_Abort(MPI_COMM_WORLD, 1);
    }
    c.decodeManager->decompress(static_cast<std::uint8_t*>(dDst), c.dRecv[slot],
        *c.decodeConfig[slot]);
}

void comm_compression_releaseEncodeConfig(unsigned parity) {
    if (g_pipeline == nullptr)
        return;
    const unsigned slot = parity & 1u;
    delete g_pipeline->encodeConfig[slot];
    g_pipeline->encodeConfig[slot] = nullptr;
}

void comm_compression_releaseDecodeConfig(unsigned parity) {
    if (g_pipeline == nullptr)
        return;
    const unsigned slot = parity & 1u;
    delete g_pipeline->decodeConfig[slot];
    g_pipeline->decodeConfig[slot] = nullptr;
}

#endif // COMPILE_NVCOMP
