/** @file
 * Experimental, env-gated nvCOMP Bitcomp compression for the CPU-staged
 * distributed-GPU exchange path (build option ENABLE_NVCOMP, default OFF).
 *
 * When active, the staged exchange
 *     D2H(raw) -> MPI(raw) -> H2D(raw)
 * is replaced per logical chunk by
 *     GPU compress -> D2H(compressed) -> MPI(sizes) -> MPI(compressed)
 *     -> H2D(compressed) -> GPU decompress,
 * with a per-chunk raw fallback whenever compression does not shrink the
 * payload on either rank. Numerical results are bit-identical to the raw
 * path (lossless codec + byte-exact fallback).
 *
 * Runtime gating (read once per process):
 *   QUEST_ENABLE_EXCHANGE_COMPRESSION=1   enable (default off)
 *   QUEST_EXCHANGE_COMPRESSION_MIN_BYTES  activation threshold (default 16 MiB)
 *   QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES logical chunk size (default 64 MiB)
 *   QUEST_EXCHANGE_COMPRESSION_VERIFY=1   debug: shadow raw exchange + memcmp
 *   QUEST_EXCHANGE_COMPRESSION_STATS=1    emit per-rank raw/sent/control byte counters
 *
 * @author Zeyu Lin (experimental fork feature; not upstream QuEST)
 */

#ifndef COMM_COMPRESSION_HPP
#define COMM_COMPRESSION_HPP

#include "quest/include/types.h"

#include <cstddef>

#ifdef COMPILE_NVCOMP

/// Performs this module's one-time collective setup: duplicates the private
/// communicator and agrees that every rank read the same configuration.
/// MUST be called from a rank-symmetric point (comm_init), never lazily from
/// the exchange — a gate whose control qubit lies in the prefix substate lets
/// ranks with the wrong rank-index bit skip the exchange entirely, so a WORLD
/// collective placed there is joined by only a subset of ranks. Until this
/// runs, the compressed path stays inactive.
void comm_compression_init();

/// Attempts the compressed staged exchange between this rank's device buffer
/// dSend and pair rank's, receiving into device buffer dRecv. Returns true if
/// the exchange was fully handled (caller must skip the raw path), false if
/// the compressed path is inactive/unavailable (caller falls through to raw).
/// Symmetric: returns the same decision on both ranks of the pair.
bool comm_compression_tryExchange(qcomp* dSend, qcomp* dRecv, qindex numAmps, int pairRank);

/*
 * Chunk-codec API for the window transport (T-040 A3, codec-over-window).
 *
 * The window path stages compressed chunk bytes through the shared-host slot
 * instead of MPI, so it needs the codec primitives WITHOUT the transport:
 * compress one chunk into the module's device send buffer, read its size,
 * and decompress one chunk out of the module's device recv buffer. The
 * per-chunk protocol, slot staging and descriptor traffic stay in
 * comm_window.cpp. All functions are process-local (no MPI inside); rank
 * agreement on using the codec is the caller's job (pairwise, on the
 * window's control communicator).
 */

/// Rank-uniform candidacy check: config collectively uniform + enabled +
/// payload >= min-bytes gate, excluding the debug verify mode (its shadow
/// exchange is MPI-shaped). Guaranteed to return the same value on every
/// rank of a symmetric exchange, so callers may gate a pairwise vote on it
/// without desynchronising. Does NOT check per-rank context health.
bool comm_compression_windowCodecCandidate(qindex numAmps);

/// Candidate AND this rank's codec context initialised successfully. NOT
/// rank-uniform (a one-sided nvcomp init failure differs per rank) — use as
/// the pairwise vote payload, never as a silent branch condition.
bool comm_compression_windowCodecUsable(qindex numAmps);

/// Logical chunk size in bytes (window chunk loop granularity).
std::size_t comm_compression_chunkBytes();

/// Compresses `bytes` device bytes at dSrc into the module's device send
/// buffer. Returns the compressed size, or 0 when the chunk must go raw
/// (force-raw ablation, or compression failed to shrink it). Synchronous:
/// on return the send buffer is safe to copy from any stream.
std::size_t comm_compression_compressChunk(const void* dSrc, std::size_t bytes);

/// Device pointer holding the last compressChunk output.
const void* comm_compression_deviceCompressedSend();

/// Device buffer for staging the peer's compressed chunk before decode.
void* comm_compression_deviceCompressedRecv();

/// Decompresses the recv buffer's chunk into `bytes` device bytes at dDst.
/// Synchronous. Aborts on codec failure (lossless contract, same as the MPI
/// path).
void comm_compression_decompressChunk(void* dDst, std::size_t bytes);

/// Stats hooks so window-staged chunks land in the same [quest-nvcomp-stats]
/// counters as MPI-staged ones (raw_bytes/sent_bytes/fallback_chunks).
void comm_compression_recordChunk(std::size_t rawBytes, std::size_t sentBytes, bool fallback);
void comm_compression_recordControl(std::size_t bytes);

/*
 * Fused pipeline API for the tiled window transport (T-079 spec A3,
 * win/on/tiled). Here the codec runs INSIDE the tiled loop, so the window
 * owns every ordering decision and the codec may not synchronise anything on
 * its own: each entry point below launches work and returns. D-032 merges the
 * tile and chunk axes — the pipeline unit IS the tile, and
 * QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES keeps its meaning only on the bulk
 * paths above.
 *
 * This machinery is deliberately disjoint from the chunk API: the bulk arms
 * are measured, so neither their device allocation nor their synchronisation
 * structure may move underneath them.
 */

/// Sizes the fused pipeline for a `unitBytes` unit, creates its two codec
/// streams and allocates its double-buffered device staging. Call once per
/// process before any fused exchange; later calls are satisfied from the
/// existing allocation and only re-check that the unit still fits.
/// Returns false when the codec is unavailable for a reason a peer may not
/// share (config gated off, nvcomp init failure) — the caller's pairwise vote
/// then falls back to the uncompressed tiled path. A device allocation that
/// does not fit is instead a fatal startup error carrying the arithmetic,
/// because silently running the uncompressed arm would mislabel a
/// measurement.
bool comm_compression_prepareWindowPipeline(std::size_t unitBytes);

/// Candidate AND this rank's fused pipeline is ready. Not rank-uniform; use
/// as the pairwise vote payload exactly as windowCodecUsable is used for the
/// bulk path, never as a silent branch condition.
bool comm_compression_windowPipelineUsable(qindex numAmps);

/// The pipeline unit actually in force, in bytes (0 before preparation), and
/// the capacity of one encoded-staging slot. The window records the first in
/// its start-up line so a campaign can prove which unit ran, and bounds the
/// peer's declared stored length against the second.
std::size_t comm_compression_windowPipelineUnitBytes();
std::size_t comm_compression_windowPipelineSlotCapacity();

/// The pipeline's two codec streams, shaped as cudaStream_t. Encode and
/// decode never share one: nvcomp inspects the inbound unit's header from the
/// host when a decode is configured, and on a shared stream that host wait
/// would drag onto the next unit's encode and collapse the pipeline.
void* comm_compression_encodeStream();
void* comm_compression_decodeStream();

/// Double-buffered device staging, selected by unit parity. Unit k encodes
/// into send slot k&1 while unit k-1's outbound copy still reads slot (k-1)&1.
const void* comm_compression_pipelineSendSlot(unsigned parity);
void* comm_compression_pipelineRecvSlot(unsigned parity);

/// Launches the encode of `bytes` device bytes at dSrc into send slot
/// `parity`, followed by the read-back of the encoded length, both on the
/// encode stream. Nothing is synchronised: the caller records its own event
/// on that stream and reads the length once the event has fired.
void comm_compression_launchEncode(const void* dSrc, std::size_t bytes, unsigned parity);

/// The encoded length for `parity`, or 0 when the unit must be staged raw
/// (force-raw ablation, or the codec did not shrink it). Only meaningful once
/// the caller's encode event has fired.
std::size_t comm_compression_encodedSize(unsigned parity, std::size_t bytes);

/// Launches the decode of recv slot `parity` into `bytes` device bytes at
/// dDst on the decode stream. The caller must already have made that stream
/// wait on the inbound copy: nvcomp reads the unit header from the slot here.
void comm_compression_launchDecode(void* dDst, std::size_t bytes, unsigned parity);

#else

static inline void comm_compression_init() { }

static inline bool comm_compression_tryExchange(qcomp*, qcomp*, qindex, int) { return false; }

static inline bool comm_compression_windowCodecCandidate(qindex) { return false; }
static inline bool comm_compression_windowCodecUsable(qindex) { return false; }
static inline std::size_t comm_compression_chunkBytes() { return 0; }
static inline std::size_t comm_compression_compressChunk(const void*, std::size_t) { return 0; }
static inline const void* comm_compression_deviceCompressedSend() { return nullptr; }
static inline void* comm_compression_deviceCompressedRecv() { return nullptr; }
static inline void comm_compression_decompressChunk(void*, std::size_t) { }
static inline void comm_compression_recordChunk(std::size_t, std::size_t, bool) { }
static inline void comm_compression_recordControl(std::size_t) { }

static inline bool comm_compression_prepareWindowPipeline(std::size_t) { return false; }
static inline bool comm_compression_windowPipelineUsable(qindex) { return false; }
static inline std::size_t comm_compression_windowPipelineUnitBytes() { return 0; }
static inline std::size_t comm_compression_windowPipelineSlotCapacity() { return 0; }
static inline void* comm_compression_encodeStream() { return nullptr; }
static inline void* comm_compression_decodeStream() { return nullptr; }
static inline const void* comm_compression_pipelineSendSlot(unsigned) { return nullptr; }
static inline void* comm_compression_pipelineRecvSlot(unsigned) { return nullptr; }
static inline void comm_compression_launchEncode(const void*, std::size_t, unsigned) { }
static inline std::size_t comm_compression_encodedSize(unsigned, std::size_t) { return 0; }
static inline void comm_compression_launchDecode(void*, std::size_t, unsigned) { }

#endif // COMPILE_NVCOMP

#endif // COMM_COMPRESSION_HPP
