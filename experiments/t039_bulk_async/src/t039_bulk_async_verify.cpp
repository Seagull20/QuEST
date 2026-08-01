/**
 * T-039 cluster correctness probe.
 *
 * The same deterministic Qureg/state operation is run once in raw CPU-staged
 * mode and once in bulk_async mode by the surrounding shell script.  The
 * script compares this program's per-rank byte hash and the transport stats.
 */

#include "quest/include/quest.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Options {
    int numQubits = -1;
    std::vector<int> targets;
};

bool parsePositiveInt(const char* text, int& output) {
    if (text == nullptr || *text == '\0')
        return false;

    char* end = nullptr;
    long parsed = std::strtol(text, &end, 10);
    if (end == text || *end != '\0' || parsed < 1 || parsed > 62)
        return false;

    output = static_cast<int>(parsed);
    return true;
}

bool parseTargets(const char* text, std::vector<int>& targets) {
    if (text == nullptr || *text == '\0')
        return false;

    std::stringstream stream(text);
    std::string item;
    while (std::getline(stream, item, ',')) {
        int target = -1;
        if (!parsePositiveInt(item.c_str(), target) && item != "0")
            return false;
        targets.push_back(target);
    }
    return !targets.empty();
}

bool parseOptions(int argc, char** argv, Options& options) {
    for (int i = 1; i < argc; i++) {
        if (std::strcmp(argv[i], "--qubits") == 0 && i + 1 < argc) {
            if (!parsePositiveInt(argv[++i], options.numQubits))
                return false;
        } else if (std::strcmp(argv[i], "--targets") == 0 && i + 1 < argc) {
            if (!parseTargets(argv[++i], options.targets))
                return false;
        } else {
            return false;
        }
    }
    return options.numQubits > 0 && !options.targets.empty();
}

std::uint64_t hashAmplitudes(const std::vector<qcomp>& amplitudes) {
    constexpr std::uint64_t FNV_OFFSET = 1469598103934665603ULL;
    constexpr std::uint64_t FNV_PRIME = 1099511628211ULL;
    std::uint64_t hash = FNV_OFFSET;

    const unsigned char* bytes = reinterpret_cast<const unsigned char*>(amplitudes.data());
    const std::size_t numBytes = amplitudes.size() * sizeof(qcomp);
    for (std::size_t i = 0; i < numBytes; i++) {
        hash ^= bytes[i];
        hash *= FNV_PRIME;
    }
    return hash;
}

void printUsage(const char* executable) {
    std::fprintf(stderr, "usage: %s --qubits N --targets t0[,t1,...]\n", executable);
}

} // namespace

int main(int argc, char** argv) {
    Options options;
    if (!parseOptions(argc, argv, options)) {
        printUsage(argv[0]);
        return EXIT_FAILURE;
    }

    initCustomQuESTEnv(1, 1, 0);
    QuESTEnv env = getQuESTEnv();
    int distributedQubits = 0;
    for (int nodes = env.numNodes; nodes > 1; nodes >>= 1)
        distributedQubits++;

    if (!env.isDistributed || !env.isGpuAccelerated) {
        std::fprintf(stderr, "T039 requires distributed GPU deployment\n");
        finalizeQuESTEnv();
        return EXIT_FAILURE;
    }

    for (int target : options.targets) {
        if (target < 0 || target >= distributedQubits) {
            if (env.rank == 0)
                std::fprintf(stderr,
                    "target %d is not a distributed qubit for %d ranks\n",
                    target, env.numNodes);
            finalizeQuESTEnv();
            return EXIT_FAILURE;
        }

        Qureg qureg = createCustomQureg(options.numQubits, 0, 1, 1, 0);
        initDebugState(qureg);
        syncQuESTEnv();
        applyHadamard(qureg, target);
        syncQuESTEnv();

        std::vector<qcomp> localAmplitudes = getQuregAmps(
            qureg, 0, qureg.numAmpsPerNode);
        const std::uint64_t hash = hashAmplitudes(localAmplitudes);

        std::cout << "T039_STATE qubits=" << options.numQubits
                  << " target=" << target
                  << " rank=" << env.rank
                  << " local_amps=" << qureg.numAmpsPerNode
                  << " hash=" << std::hex << std::setfill('0') << std::setw(16) << hash
                  << std::dec << '\n';

        destroyQureg(qureg);
        syncQuESTEnv();
    }

    finalizeQuESTEnv();
    return EXIT_SUCCESS;
}
