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
            // what the codec delivered (hRawRecv holds the peer's raw chunk)
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

#endif // COMPILE_NVCOMP
