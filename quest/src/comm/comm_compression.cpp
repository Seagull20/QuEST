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

// MPI tags private to this module; the raw path is never concurrently active,
// and its tags start at 0, so any distinct constants are safe.
constexpr int TAG_ACTIVE = 9001;
constexpr int TAG_SIZE   = 9002;
constexpr int TAG_DATA   = 9003;
constexpr int TAG_VERIFY = 9004;

constexpr std::size_t NVCOMP_INTERNAL_CHUNK = std::size_t(1) << 20; // 1 MiB, as in the campaign

struct Config {
    bool enabled = false;
    bool verify = false;
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
        c.minBytes = readEnvBytes("QUEST_EXCHANGE_COMPRESSION_MIN_BYTES", c.minBytes);
        c.chunkBytes = readEnvBytes("QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES", c.chunkBytes);
        // chunk must be an amp multiple, sane, and single-MPI-message safe (< INT_MAX)
        c.chunkBytes -= c.chunkBytes % sizeof(qcomp);
        if (c.chunkBytes < (std::size_t(1) << 20)) c.chunkBytes = std::size_t(1) << 20;
        if (c.chunkBytes > (std::size_t(1) << 30)) c.chunkBytes = std::size_t(1) << 30;
        if (c.enabled) {
            std::fprintf(stderr,
                "[quest-nvcomp] exchange compression ENABLED (bitcomp, chunk=%zu MiB, "
                "threshold=%zu MiB, verify=%d)\n",
                c.chunkBytes >> 20, c.minBytes >> 20, (int) c.verify);
        }
        return c;
    }();
    return cfg;
}

// Persistent per-process context: one stream, one Bitcomp manager, scratch
// buffers bounded by the logical chunk size. Allocated on first use, freed at
// process exit (intentionally leaked to the CUDA teardown, like other QuEST
// process-lifetime GPU state).
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
    static Context ctx = [] {
        Context c;
        const Config& cfg = getConfig();
        try {
            if (!cudaOk(cudaStreamCreate(&c.stream), "cudaStreamCreate")) return c;

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

            if (!cudaOk(cudaMalloc(&c.dCompSend, c.maxCompBytes), "cudaMalloc dCompSend")) return c;
            if (!cudaOk(cudaMalloc(&c.dCompRecv, c.maxCompBytes), "cudaMalloc dCompRecv")) return c;
            if (!cudaOk(cudaMalloc(&c.dCompSize, sizeof(std::size_t)), "cudaMalloc dCompSize")) return c;
            if (!cudaOk(cudaMallocHost(&c.hCompSend, c.maxCompBytes), "cudaMallocHost hCompSend")) return c;
            if (!cudaOk(cudaMallocHost(&c.hCompRecv, c.maxCompBytes), "cudaMallocHost hCompRecv")) return c;
            if (!cudaOk(cudaMallocHost(&c.hRawSend, cfg.chunkBytes), "cudaMallocHost hRawSend")) return c;
            if (!cudaOk(cudaMallocHost(&c.hRawRecv, cfg.chunkBytes), "cudaMallocHost hRawRecv")) return c;
            c.ok = true;
        } catch (const std::exception& e) {
            std::fprintf(stderr, "[quest-nvcomp] init failed: %s — compression disabled\n", e.what());
            c.ok = false;
        }
        return c;
    }();
    return ctx;
}

// One raw chunk exchange through pinned host staging (fallback + verify path).
void exchangeRawChunk(const std::uint8_t* dSrc, std::uint8_t* hSend, std::uint8_t* hRecv,
                      std::size_t bytes, int pairRank, int tag, cudaStream_t stream) {
    {
        QuestProfileRange r("quest.communication.d2h");
        cudaMemcpyAsync(hSend, dSrc, bytes, cudaMemcpyDeviceToHost, stream);
        cudaStreamSynchronize(stream);
    }
    {
        QuestProfileRange r("quest.communication.mpi");
        MPI_Sendrecv(hSend, (int) bytes, MPI_BYTE, pairRank, tag,
                     hRecv, (int) bytes, MPI_BYTE, pairRank, tag,
                     MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    }
}

} // anonymous namespace


bool comm_compression_tryExchange(qcomp* dSend, qcomp* dRecv, qindex numAmps, int pairRank) {

    const Config& cfg = getConfig();
    if (!cfg.enabled)
        return false;

    const std::size_t payloadBytes = (std::size_t) numAmps * sizeof(qcomp);
    if (payloadBytes < cfg.minBytes)
        return false; // symmetric: numAmps identical on both ranks

    Context& ctx = getContext();

    // agree with the pair rank on taking the compressed path, so a one-sided
    // init failure can never desynchronise the MPI traffic below
    std::uint8_t localActive = ctx.ok ? 1 : 0, peerActive = 0;
    MPI_Sendrecv(&localActive, 1, MPI_UINT8_T, pairRank, TAG_ACTIVE,
                 &peerActive, 1, MPI_UINT8_T, pairRank, TAG_ACTIVE,
                 MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    if (!localActive || !peerActive)
        return false;

    QuestProfileRange pathRange("quest.communication.exchange.cpu_staged.compressed");

    // prior gate kernels may still be writing the send buffer
    cudaDeviceSynchronize();

    auto* src = reinterpret_cast<const std::uint8_t*>(dSend);
    auto* dst = reinterpret_cast<std::uint8_t*>(dRecv);

    for (std::size_t offset = 0; offset < payloadBytes; offset += cfg.chunkBytes) {
        const std::size_t bytes = std::min(cfg.chunkBytes, payloadBytes - offset);
        std::uint64_t localCompSize = 0, peerCompSize = 0;

        {
            QuestProfileRange r("quest.communication.compress");
            auto compCfg = ctx.manager->configure_compression(bytes);
            ctx.manager->compress(src + offset, ctx.dCompSend, compCfg, ctx.dCompSize);
            cudaStreamSynchronize(ctx.stream);
            cudaMemcpy(&localCompSize, ctx.dCompSize, sizeof(std::size_t), cudaMemcpyDeviceToHost);
        }

        {
            QuestProfileRange r("quest.communication.size_exchange");
            MPI_Sendrecv(&localCompSize, 1, MPI_UINT64_T, pairRank, TAG_SIZE,
                         &peerCompSize, 1, MPI_UINT64_T, pairRank, TAG_SIZE,
                         MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        }

        if (localCompSize == 0 || peerCompSize == 0 ||
            localCompSize >= bytes || peerCompSize >= bytes) {
            // incompressible chunk on either side: byte-exact raw fallback
            exchangeRawChunk(src + offset, ctx.hRawSend, ctx.hRawRecv, bytes,
                             pairRank, TAG_DATA, ctx.stream);
            QuestProfileRange r("quest.communication.h2d");
            cudaMemcpyAsync(dst + offset, ctx.hRawRecv, bytes, cudaMemcpyHostToDevice, ctx.stream);
            cudaStreamSynchronize(ctx.stream);
            continue;
        }

        {
            QuestProfileRange r("quest.communication.d2h");
            cudaMemcpyAsync(ctx.hCompSend, ctx.dCompSend, localCompSize,
                            cudaMemcpyDeviceToHost, ctx.stream);
            cudaStreamSynchronize(ctx.stream);
        }
        {
            QuestProfileRange r("quest.communication.mpi");
            MPI_Sendrecv(ctx.hCompSend, (int) localCompSize, MPI_BYTE, pairRank, TAG_DATA,
                         ctx.hCompRecv, (int) peerCompSize, MPI_BYTE, pairRank, TAG_DATA,
                         MPI_COMM_WORLD, MPI_STATUS_IGNORE);
        }
        {
            QuestProfileRange r("quest.communication.h2d");
            cudaMemcpyAsync(ctx.dCompRecv, ctx.hCompRecv, peerCompSize,
                            cudaMemcpyHostToDevice, ctx.stream);
            cudaStreamSynchronize(ctx.stream);
        }
        {
            QuestProfileRange r("quest.communication.decompress");
            auto decompCfg = ctx.manager->configure_decompression(ctx.dCompRecv);
            ctx.manager->decompress(dst + offset, ctx.dCompRecv, decompCfg);
            cudaStreamSynchronize(ctx.stream);
        }

        if (cfg.verify) {
            // debug shadow: raw-exchange the same chunk and byte-compare with
            // what the codec delivered (hRawRecv holds the peer's raw chunk)
            exchangeRawChunk(src + offset, ctx.hRawSend, ctx.hRawRecv, bytes,
                             pairRank, TAG_VERIFY, ctx.stream);
            static std::uint8_t* hCheck = nullptr;
            if (hCheck == nullptr)
                cudaMallocHost(&hCheck, cfg.chunkBytes);
            cudaMemcpy(hCheck, dst + offset, bytes, cudaMemcpyDeviceToHost);
            if (std::memcmp(hCheck, ctx.hRawRecv, bytes) != 0) {
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
