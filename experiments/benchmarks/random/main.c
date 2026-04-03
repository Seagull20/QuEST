#include "../common/bench.h"
#include "../common/bench_rng.h"

#include <stdio.h>
#include <stdlib.h>

typedef enum {
    RAND_GATE_H = 0,
    RAND_GATE_X = 1,
    RAND_GATE_RZ = 2,
    RAND_GATE_RX = 3,
    RAND_GATE_RY = 4,
    RAND_GATE_CNOT = 5,
    RAND_GATE_CZ = 6,
    RAND_GATE_SWAP = 7
} RandomGateType;

typedef struct {
    RandomGateType type;
    int q0;
    int q1;
    qreal angle;
} RandomGate;

static int generate_random_circuit(const BenchOptions* opts, RandomGate** out_gates, int* out_gate_count) {
    int layer;
    int gate_index = 0;
    int pair_count = opts->num_qubits / 2;
    int max_gates = opts->depth * (opts->num_qubits + pair_count);
    int* permutation = NULL;
    RandomGate* gates = NULL;
    BenchRng rng;

    gates = (RandomGate*) malloc((size_t) max_gates * sizeof(RandomGate));
    permutation = (int*) malloc((size_t) opts->num_qubits * sizeof(int));
    if (gates == NULL || permutation == NULL) {
        free(gates);
        free(permutation);
        return 0;
    }

    bench_rng_seed(&rng, (uint64_t) opts->seed);

    for (layer = 0; layer < opts->depth; layer++) {
        int i;

        for (i = 0; i < opts->num_qubits; i++) {
            int gate_pick = bench_rng_next_int(&rng, 5);

            gates[gate_index].q0 = i;
            gates[gate_index].q1 = -1;
            gates[gate_index].angle = 0;

            if (gate_pick == 0)
                gates[gate_index].type = RAND_GATE_H;
            else if (gate_pick == 1)
                gates[gate_index].type = RAND_GATE_X;
            else if (gate_pick == 2) {
                gates[gate_index].type = RAND_GATE_RZ;
                gates[gate_index].angle = bench_discrete_angle_from_index(bench_rng_next_int(&rng, 6));
            } else if (gate_pick == 3) {
                gates[gate_index].type = RAND_GATE_RX;
                gates[gate_index].angle = bench_discrete_angle_from_index(bench_rng_next_int(&rng, 6));
            } else {
                gates[gate_index].type = RAND_GATE_RY;
                gates[gate_index].angle = bench_discrete_angle_from_index(bench_rng_next_int(&rng, 6));
            }

            gate_index++;
            permutation[i] = i;
        }

        bench_rng_shuffle_ints(&rng, permutation, opts->num_qubits);
        for (i = 0; i + 1 < opts->num_qubits; i += 2) {
            int gate_pick = bench_rng_next_int(&rng, 3);

            gates[gate_index].q0 = permutation[i];
            gates[gate_index].q1 = permutation[i + 1];
            gates[gate_index].angle = 0;

            if (gate_pick == 0)
                gates[gate_index].type = RAND_GATE_CNOT;
            else if (gate_pick == 1)
                gates[gate_index].type = RAND_GATE_CZ;
            else
                gates[gate_index].type = RAND_GATE_SWAP;

            gate_index++;
        }
    }

    free(permutation);
    *out_gates = gates;
    *out_gate_count = gate_index;
    return 1;
}

static void apply_random_gate(Qureg qureg, const RandomGate* gate) {
    switch (gate->type) {
        case RAND_GATE_H:
            applyHadamard(qureg, gate->q0);
            break;
        case RAND_GATE_X:
            applyPauliX(qureg, gate->q0);
            break;
        case RAND_GATE_RZ:
            applyRotateZ(qureg, gate->q0, gate->angle);
            break;
        case RAND_GATE_RX:
            applyRotateX(qureg, gate->q0, gate->angle);
            break;
        case RAND_GATE_RY:
            applyRotateY(qureg, gate->q0, gate->angle);
            break;
        case RAND_GATE_CNOT:
            applyControlledPauliX(qureg, gate->q0, gate->q1);
            break;
        case RAND_GATE_CZ:
            applyTwoQubitPhaseShift(qureg, gate->q0, gate->q1, (qreal) BENCH_PI);
            break;
        case RAND_GATE_SWAP:
            applySwap(qureg, gate->q0, gate->q1);
            break;
    }
}

static double run_random_circuit(Qureg qureg, const BenchOptions* opts, const RandomGate* gates, int gate_count) {
    double start = bench_wall_time();
    int i;

    for (i = 0; i < gate_count; i++)
        apply_random_gate(qureg, &gates[i]);

    bench_sync_stage_if_needed(opts);
    bench_sync_finalize(opts);
    return bench_wall_time() - start;
}

static int run_random_light_preheat(const BenchOptions* opts) {
    BenchOptions preheat_opts = *opts;
    RandomGate* preheat_gates = NULL;
    int preheat_gate_count = 0;
    int preheat_qubits = bench_effective_preheat_qubits(opts);
    Qureg preheat_qureg;

    preheat_opts.num_qubits = preheat_qubits;
    preheat_opts.depth = 2 * preheat_qubits;

    if (!generate_random_circuit(&preheat_opts, &preheat_gates, &preheat_gate_count))
        return 0;

    preheat_qureg = bench_create_state_qureg_with_qubits(opts, preheat_qubits);
    initZeroState(preheat_qureg);
    syncQuESTEnv();
    (void) run_random_circuit(preheat_qureg, &preheat_opts, preheat_gates, preheat_gate_count);
    destroyQureg(preheat_qureg);
    free(preheat_gates);
    return 1;
}

static void write_header(FILE* out) {
    fprintf(out,
            "platform\tbackend\tdeployment\tbenchmark\tlabel\tnum_qubits\trep\twarmup\tstatus\tsync_mode\ttotal_prob\tenv_num_nodes\tenv_num_threads\tpreheat_mode\tpreheat_qubits\tdepth\tseed\tgate_count\ttotal_time_s\n");
}

static void write_row(FILE* out,
                      const BenchOptions* opts,
                      int rep,
                      int is_warmup,
                      const char* status,
                      qreal total_prob,
                      int gate_count,
                      double total_time_s) {
    fprintf(out,
            "%s\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%s\t%s\t%.12f\t%d\t%d\t%s\t%d\t%d\t%u\t%d\t%.9f\n",
            bench_detect_platform(),
            bench_build_backend(),
            bench_distribution_string(opts->distribution),
            opts->benchmark_name,
            bench_label_or_default(opts),
            opts->num_qubits,
            rep,
            is_warmup,
            status,
            bench_sync_mode_string(opts->sync_mode),
            (double) total_prob,
            bench_env_num_nodes(),
            bench_env_num_threads(),
            bench_preheat_mode_string(opts->preheat_mode),
            bench_effective_preheat_qubits(opts),
            opts->depth,
            opts->seed,
            gate_count,
            total_time_s);
}

int main(int argc, char** argv) {
    BenchOptions opts;
    BenchParseResult parse_result;
    FILE* out = NULL;
    int should_close = 0;
    int should_write_header = 0;
    RandomGate* gates = NULL;
    int gate_count = 0;
    Qureg qureg;
    int rep;

    bench_options_init(&opts, "random");
    parse_result = bench_parse_options(&opts, argc, argv);
    if (parse_result == BENCH_PARSE_HELP) {
        bench_print_common_usage(stdout, "random", "Random uses a layered circuit over {H, X, RZ, RX, RY, CNOT, CZ, SWAP}.");
        return EXIT_SUCCESS;
    }
    if (opts.depth < 0)
        opts.depth = 2 * opts.num_qubits;
    if (parse_result != BENCH_PARSE_OK || !bench_validate_runtime_request(&opts, stderr))
        return EXIT_FAILURE;

    if (!bench_open_output(opts.output_path, &out, &should_close, &should_write_header))
        return EXIT_FAILURE;
    if (should_write_header)
        write_header(out);

    if (!generate_random_circuit(&opts, &gates, &gate_count)) {
        fprintf(stderr, "ERROR: failed to generate random circuit\n");
        bench_close_output(out, should_close);
        return EXIT_FAILURE;
    }

    bench_init_environment();
    if (opts.preheat_mode == BENCH_PREHEAT_LIGHT && !run_random_light_preheat(&opts)) {
        fprintf(stderr, "ERROR: failed to run random light preheat\n");
        free(gates);
        bench_close_output(out, should_close);
        finalizeQuESTEnv();
        return EXIT_FAILURE;
    }
    qureg = bench_create_state_qureg(&opts);

    for (rep = 0; rep < opts.warmup + opts.reps; rep++) {
        int is_warmup = rep < opts.warmup;
        double total_time_s;
        qreal total_prob;
        const char* status;

        initZeroState(qureg);
        syncQuESTEnv();

        total_time_s = run_random_circuit(qureg, &opts, gates, gate_count);
        total_prob = calcTotalProb(qureg);
        status = bench_prob_is_valid(total_prob) ? BENCH_STATUS_PASS : BENCH_STATUS_FAILURE;

        write_row(out, &opts, rep, is_warmup, status, total_prob, gate_count, total_time_s);
        fflush(out);
    }

    destroyQureg(qureg);
    finalizeQuESTEnv();
    free(gates);
    bench_close_output(out, should_close);
    return EXIT_SUCCESS;
}
