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

#else

static inline void comm_compression_init() { }

static inline bool comm_compression_tryExchange(qcomp*, qcomp*, qindex, int) { return false; }

#endif // COMPILE_NVCOMP

#endif // COMM_COMPRESSION_HPP
