/** T-030 P3' correctness driver: distributed-GPU circuit whose exchanges cross
 *  both CPU-staged hooks (amps-to-buffers via non-local H, packed sub-buffer
 *  via controlled ops on non-local targets). Prints deterministic state
 *  fingerprints; a run with QUEST_ENABLE_EXCHANGE_COMPRESSION=1 must produce
 *  BIT-IDENTICAL output to a run with it unset (lossless codec + byte-exact
 *  fallback). Combine with QUEST_EXCHANGE_COMPRESSION_VERIFY=1 for in-module
 *  per-chunk byte assertions against a shadow raw exchange.
 */

#include "quest.h"

#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstdlib>

int main(int argc, char** argv) {
    initQuESTEnv();
    QuESTEnv env = getQuESTEnv();

    int numQubits = (argc > 1) ? std::atoi(argv[1]) : 24;
    // force distribution + GPU: auto-deployment skips distribution at small q,
    // which would bypass the exchange paths this driver exists to test
    Qureg qureg = createCustomQureg(numQubits, /*isDensMatr*/ 0,
                                    /*useDistrib*/ 1, /*useGpuAccel*/ 1, /*useMultithread*/ 0);

    if (env.rank == 0)
        std::printf("q%d ranks=%d gpu=%d dist=%d\n",
                    numQubits, env.numNodes, qureg.isGpuAccelerated, qureg.isDistributed);

    // structured, non-trivial state: uniform + local phase texture
    initPlusState(qureg);
    for (int t = 0; t < 8 && t < numQubits; t++)
        applyRotateZ(qureg, t, 0.1 + 0.05 * t);

    // amps-to-buffers exchanges: H on the top (non-local) qubits
    applyHadamard(qureg, numQubits - 1);
    applyHadamard(qureg, numQubits - 2);

    // packed/sub-buffer exchanges: controlled ops with non-local targets
    applyControlledPauliX(qureg, 0, numQubits - 1);
    applyControlledRotateX(qureg, 1, numQubits - 2, 0.3);

    // one more full exchange after the controlled layer
    applyHadamard(qureg, numQubits - 1);

    // deterministic fingerprints
    qreal totalProb = calcTotalProb(qureg);

    syncQuregFromGpu(qureg);
    double sumRe = 0, sumIm = 0, sumSq = 0;
    for (qindex i = 0; i < qureg.numAmpsPerNode; i++) {
        qcomp a = qureg.cpuAmps[i];
        sumRe += std::real(a);
        sumIm += std::imag(a);
        sumSq += std::norm(a);
    }

    // print per-rank lines in rank order (fingerprints must match bit-for-bit
    // between compression-off and compression-on runs)
    for (int r = 0; r < env.numNodes; r++) {
        syncQuESTEnv();
        if (r != env.rank)
            continue;
        std::printf("rank %d fingerprint re=%.17e im=%.17e sq=%.17e\n",
                    env.rank, sumRe, sumIm, sumSq);
        std::fflush(stdout);
    }
    syncQuESTEnv();
    if (env.rank == 0)
        std::printf("totalProb=%.17e\nDONE\n", (double) totalProb);

    destroyQureg(qureg);
    finalizeQuESTEnv();
    return 0;
}
