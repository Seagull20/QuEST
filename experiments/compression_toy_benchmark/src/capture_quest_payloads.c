#include "quest.h"

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

typedef struct {
    int qubits;
    int depth;
    unsigned seed;
    double two_qubit_ratio;
    qindex payload_amps;
    const char* output_dir;
    const char* patterns;
    const char* exchange_shapes;
} Options;

static void usage(const char* prog) {
    fprintf(stderr,
        "Usage: %s --output-dir DIR [options]\n"
        "Options:\n"
        "  --qubits N                 Qubits for generated Quregs (default 24)\n"
        "  --payload-amps N           Amplitudes to dump per rank/shape (default 0 = full local shape payload)\n"
        "  --patterns LIST            Comma list or all (default all)\n"
        "  --exchange-shapes LIST     amps_to_buffers,sub_buffers,both (default both)\n"
        "  --depth N                  Random workload depth (default 2*qubits)\n"
        "  --seed N                   Random workload seed (default 20260402)\n"
        "  --two-qubit-ratio R        Random workload two-qubit gate ratio (default 0.5)\n",
        prog);
}

static int parse_positive_int(const char* text, int* out) {
    char* end = NULL;
    long value = strtol(text, &end, 10);
    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0' || value <= 0 || value > 2147483647L)
        return 0;
    *out = (int) value;
    return 1;
}

static int parse_nonnegative_qindex(const char* text, qindex* out) {
    char* end = NULL;
    unsigned long long value = strtoull(text, &end, 10);
    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0')
        return 0;
    *out = (qindex) value;
    return 1;
}

static int parse_unsigned(const char* text, unsigned* out) {
    char* end = NULL;
    unsigned long value = strtoul(text, &end, 10);
    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0' || value > 4294967295UL)
        return 0;
    *out = (unsigned) value;
    return 1;
}

static int parse_unit_double(const char* text, double* out) {
    char* end = NULL;
    double value = strtod(text, &end);
    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0' || value < 0.0 || value > 1.0)
        return 0;
    *out = value;
    return 1;
}

static int parse_options(int argc, char** argv, Options* opts) {
    int i;
    opts->qubits = 24;
    opts->depth = -1;
    opts->seed = 20260402u;
    opts->two_qubit_ratio = 0.5;
    opts->payload_amps = 0;
    opts->output_dir = NULL;
    opts->patterns = "all";
    opts->exchange_shapes = "both";

    for (i = 1; i < argc; i++) {
        const char* arg = argv[i];
        if (strcmp(arg, "--help") == 0 || strcmp(arg, "-h") == 0) {
            usage(argv[0]);
            exit(EXIT_SUCCESS);
        }
        if (i + 1 >= argc) {
            fprintf(stderr, "ERROR: option %s requires a value\n", arg);
            return 0;
        }
        if (strcmp(arg, "--output-dir") == 0)
            opts->output_dir = argv[++i];
        else if (strcmp(arg, "--qubits") == 0) {
            if (!parse_positive_int(argv[++i], &opts->qubits)) return 0;
        } else if (strcmp(arg, "--payload-amps") == 0) {
            if (!parse_nonnegative_qindex(argv[++i], &opts->payload_amps)) return 0;
        } else if (strcmp(arg, "--patterns") == 0)
            opts->patterns = argv[++i];
        else if (strcmp(arg, "--exchange-shapes") == 0)
            opts->exchange_shapes = argv[++i];
        else if (strcmp(arg, "--depth") == 0) {
            if (!parse_positive_int(argv[++i], &opts->depth)) return 0;
        } else if (strcmp(arg, "--seed") == 0) {
            if (!parse_unsigned(argv[++i], &opts->seed)) return 0;
        } else if (strcmp(arg, "--two-qubit-ratio") == 0) {
            if (!parse_unit_double(argv[++i], &opts->two_qubit_ratio)) return 0;
        } else {
            fprintf(stderr, "ERROR: unknown option %s\n", arg);
            return 0;
        }
    }

    if (opts->output_dir == NULL) {
        fprintf(stderr, "ERROR: --output-dir is required\n");
        return 0;
    }
    if (opts->depth < 0)
        opts->depth = 2 * opts->qubits;
    return 1;
}

static int list_contains(const char* list, const char* value) {
    const char* pos = list;
    size_t value_len = strlen(value);
    if (strcmp(list, "all") == 0 || strcmp(list, "both") == 0)
        return 1;
    while (*pos != '\0') {
        const char* comma = strchr(pos, ',');
        size_t len = comma ? (size_t) (comma - pos) : strlen(pos);
        if (len == value_len && strncmp(pos, value, len) == 0)
            return 1;
        if (!comma)
            break;
        pos = comma + 1;
    }
    return 0;
}

static uint64_t lcg_next(uint64_t* state) {
    *state = (*state * 6364136223846793005ULL) + 1442695040888963407ULL;
    return *state;
}

static int rand_int(uint64_t* state, int limit) {
    return (int) (lcg_next(state) % (uint64_t) limit);
}

static qreal discrete_angle(int index) {
    static const qreal values[] = {
        0.0, (qreal) (M_PI / 8.0), (qreal) (M_PI / 4.0),
        (qreal) (M_PI / 2.0), (qreal) M_PI, (qreal) (-M_PI / 4.0)
    };
    return values[index % 6];
}

static void apply_random_workload(Qureg qureg, const Options* opts) {
    uint64_t rng = opts->seed;
    int layer;
    for (layer = 0; layer < opts->depth; layer++) {
        int gate;
        int two_qubit_gates = (int) (opts->two_qubit_ratio * (double) opts->qubits + 0.5);
        int single_gates = opts->qubits - two_qubit_gates;
        if (two_qubit_gates < 0) two_qubit_gates = 0;
        if (two_qubit_gates > opts->qubits) two_qubit_gates = opts->qubits;
        if (single_gates < 0) single_gates = 0;

        for (gate = 0; gate < single_gates; gate++) {
            int q = rand_int(&rng, opts->qubits);
            int kind = rand_int(&rng, 5);
            if (kind == 0) applyHadamard(qureg, q);
            else if (kind == 1) applyPauliX(qureg, q);
            else if (kind == 2) applyRotateZ(qureg, q, discrete_angle(rand_int(&rng, 6)));
            else if (kind == 3) applyRotateX(qureg, q, discrete_angle(rand_int(&rng, 6)));
            else applyRotateY(qureg, q, discrete_angle(rand_int(&rng, 6)));
        }

        for (gate = 0; gate < two_qubit_gates; gate++) {
            int q0 = rand_int(&rng, opts->qubits);
            int q1 = rand_int(&rng, opts->qubits - 1);
            int kind = rand_int(&rng, 3);
            if (q1 >= q0) q1++;
            if (kind == 0) applyControlledPauliX(qureg, q0, q1);
            else if (kind == 1) applyTwoQubitPhaseShift(qureg, q0, q1, (qreal) M_PI);
            else applySwap(qureg, q0, q1);
        }
    }
}

static void prepare_pattern(Qureg qureg, const Options* opts, const char* pattern) {
    if (strcmp(pattern, "quest_h_plus_pre_exchange") == 0) {
        initPlusState(qureg);
    } else if (strcmp(pattern, "quest_h_halfzero_pre_exchange") == 0) {
        initPlusState(qureg);
        applyHadamard(qureg, opts->qubits - 1);
    } else if (strcmp(pattern, "quest_qft") == 0) {
        initZeroState(qureg);
        applyFullQuantumFourierTransform(qureg);
    } else if (strcmp(pattern, "quest_random") == 0) {
        initZeroState(qureg);
        apply_random_workload(qureg, opts);
    } else {
        fprintf(stderr, "ERROR: unsupported pattern %s\n", pattern);
        exit(EXIT_FAILURE);
    }
    syncQuESTEnv();
    syncQuregFromGpu(qureg);
}

static int write_payload(Qureg qureg, const Options* opts, const char* pattern, const char* shape) {
    qindex send_ind = 0;
    qindex max_amps;
    qindex payload_amps;
    int rank = qureg.rank;
    char path[4096];
    char sidecar[4096];
    FILE* f;
    FILE* m;

    if (strcmp(shape, "sub_buffers") == 0)
        send_ind = qureg.numAmpsPerNode / 2;
    max_amps = qureg.numAmpsPerNode - send_ind;
    payload_amps = opts->payload_amps > 0 ? opts->payload_amps : max_amps;
    if (payload_amps > max_amps)
        payload_amps = max_amps;

    snprintf(path, sizeof(path), "%s/rank_%d_%s_%s_q%d_a%llu.bin",
        opts->output_dir, rank, pattern, shape, opts->qubits,
        (unsigned long long) payload_amps);
    f = fopen(path, "wb");
    if (f == NULL) {
        fprintf(stderr, "ERROR: cannot open payload %s: %s\n", path, strerror(errno));
        return 0;
    }
    if (fwrite(&qureg.cpuAmps[send_ind], sizeof(qcomp), (size_t) payload_amps, f) != (size_t) payload_amps) {
        fprintf(stderr, "ERROR: failed writing payload %s\n", path);
        fclose(f);
        return 0;
    }
    fclose(f);

    snprintf(sidecar, sizeof(sidecar), "%s/payload_manifest_rank%d.tsv", opts->output_dir, rank);
    m = fopen(sidecar, "a");
    if (m == NULL) {
        fprintf(stderr, "ERROR: cannot open rank manifest %s: %s\n", sidecar, strerror(errno));
        return 0;
    }
    fprintf(m, "genuine\t%s\t%s\t%s\t%d\t%d\t%d\t%d\t%llu\t%llu\t%zu\t%s\n",
        pattern, pattern, shape, opts->qubits, rank, qureg.numNodes,
        opts->seed, (unsigned long long) send_ind, (unsigned long long) payload_amps,
        sizeof(qcomp), path);
    fclose(m);
    return 1;
}

static void write_manifest_header(FILE* f) {
    fprintf(f, "source_kind\tpattern\tcheckpoint\texchange_shape\tnum_qubits\trank\tnum_ranks\tseed\tsend_ind\tpayload_amps\tamp_bytes\tpath\n");
}

static int merge_rank_manifests(const Options* opts, int num_ranks) {
    int r;
    char out_path[4096];
    FILE* out;
    snprintf(out_path, sizeof(out_path), "%s/payload_manifest.tsv", opts->output_dir);
    out = fopen(out_path, "w");
    if (out == NULL) {
        fprintf(stderr, "ERROR: cannot write manifest %s: %s\n", out_path, strerror(errno));
        return 0;
    }
    write_manifest_header(out);
    for (r = 0; r < num_ranks; r++) {
        char in_path[4096];
        char line[8192];
        FILE* in;
        snprintf(in_path, sizeof(in_path), "%s/payload_manifest_rank%d.tsv", opts->output_dir, r);
        in = fopen(in_path, "r");
        if (in == NULL) {
            fprintf(stderr, "ERROR: cannot read rank manifest %s: %s\n", in_path, strerror(errno));
            fclose(out);
            return 0;
        }
        while (fgets(line, sizeof(line), in) != NULL)
            fputs(line, out);
        fclose(in);
    }
    fclose(out);
    return 1;
}

int main(int argc, char** argv) {
    const char* patterns[] = {
        "quest_h_plus_pre_exchange",
        "quest_h_halfzero_pre_exchange",
        "quest_qft",
        "quest_random"
    };
    const char* shapes[] = {"amps_to_buffers", "sub_buffers"};
    Options opts;
    Qureg qureg;
    int p, s;
    int ok = 1;

    if (!parse_options(argc, argv, &opts)) {
        usage(argv[0]);
        return EXIT_FAILURE;
    }

    initQuESTEnv();
    qureg = createForcedQureg(opts.qubits);

    for (p = 0; p < 4; p++) {
        if (!list_contains(opts.patterns, patterns[p]))
            continue;
        prepare_pattern(qureg, &opts, patterns[p]);
        for (s = 0; s < 2; s++) {
            if (!list_contains(opts.exchange_shapes, shapes[s]))
                continue;
            ok = write_payload(qureg, &opts, patterns[p], shapes[s]) && ok;
        }
    }

    syncQuESTEnv();
    if (qureg.rank == 0)
        ok = merge_rank_manifests(&opts, qureg.numNodes) && ok;

    destroyQureg(qureg);
    finalizeQuESTEnv();
    return ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
