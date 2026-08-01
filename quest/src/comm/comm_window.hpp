/** @file
 * Opt-in shared-host-window transport for the full GPU amplitude exchange.
 *
 * The public Qureg ABI is intentionally unchanged.  Window state is owned by
 * the implementation and keyed by a Qureg's retained cpuCommBuffer.
 */

#ifndef COMM_WINDOW_HPP
#define COMM_WINDOW_HPP

#include "quest/include/qureg.h"

#include <cstddef>

void comm_window_initForQureg(Qureg qureg);
void comm_window_destroyForQureg(Qureg qureg);

/**
 * Attempts the T-039 bulk_async exchange for one full-amplitude exchange.
 *
 * Returns false before entering the protocol when the peer is off-node, the
 * shared window is unavailable, or pairwise registration consensus fails. In
 * those cases the caller must execute the unchanged raw CPU-staged path.
 */
bool comm_window_tryExchange(
    Qureg qureg, qcomp* gpuSend, qcomp* gpuRecv, qindex numAmps, int pairRank);

bool comm_window_statsEnabled();
void comm_window_recordPayloadMpi(std::size_t bytes, double seconds);

#endif // COMM_WINDOW_HPP
