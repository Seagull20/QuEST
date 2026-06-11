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

typedef struct {
    int gate_count;
    int single_qubit_gate_count;
    int two_qubit_gate_count;
} RandomCircuitStats;

static int random_distinct_qubit(BenchRng* rng, int num_qubits, int q0) {
    int q1 = bench_rng_next_int(rng, num_qubits - 1);
    if (q1 >= q0)
        q1++;
    return q1;
}

static void set_random_single_gate(BenchRng* rng, RandomGate* gate, int qubit) {
    int gate_pick = bench_rng_next_int(rng, 5);

    gate->q0 = qubit;
    gate->q1 = -1;
    gate->angle = 0;

    if (gate_pick == 0)
        gate->type = RAND_GATE_H;
    else if (gate_pick == 1)
        gate->type = RAND_GATE_X;
    else if (gate_pick == 2) {
        gate->type = RAND_GATE_RZ;
        gate->angle = bench_discrete_angle_from_index(bench_rng_next_int(rng, 6));
    } else if (gate_pick == 3) {
        gate->type = RAND_GATE_RX;
        gate->angle = bench_discrete_angle_from_index(bench_rng_next_int(rng, 6));
    } else {
        gate->type = RAND_GATE_RY;
        gate->angle = bench_discrete_angle_from_index(bench_rng_next_int(rng, 6));
    }
}

static void set_random_two_qubit_gate(BenchRng* rng, RandomGate* gate, int num_qubits) {
    int gate_pick = bench_rng_next_int(rng, 3);

    gate->q0 = bench_rng_next_int(rng, num_qubits);
    gate->q1 = random_distinct_qubit(rng, num_qubits, gate->q0);
    gate->angle = 0;

    if (gate_pick == 0)
        gate->type = RAND_GATE_CNOT;
    else if (gate_pick == 1)
        gate->type = RAND_GATE_CZ;
    else
        gate->type = RAND_GATE_SWAP;
}

static int generate_random_circuit(const BenchOptions* opts, RandomGate** out_gates, RandomCircuitStats* out_stats) {
    int layer;
    int gate_index = 0;
    int max_gates = opts->depth * opts->num_qubits;
    int two_qubit_per_layer = (int) (opts->two_qubit_ratio * (double) opts->num_qubits + 0.5);
    int single_qubit_per_layer;
    RandomGate* gates = NULL;
    BenchRng rng;

    if (two_qubit_per_layer < 0)
        two_qubit_per_layer = 0;
    if (two_qubit_per_layer > opts->num_qubits)
        two_qubit_per_layer = opts->num_qubits;
    single_qubit_per_layer = opts->num_qubits - two_qubit_per_layer;

    gates = (RandomGate*) malloc((size_t) max_gates * sizeof(RandomGate));
    if (gates == NULL) {
        free(gates);
        return 0;
    }

    bench_rng_seed(&rng, (uint64_t) opts->seed);

    for (layer = 0; layer < opts->depth; layer++) {
        int i;

        for (i = 0; i < single_qubit_per_layer; i++) {
            set_random_single_gate(&rng, &gates[gate_index], bench_rng_next_int(&rng, opts->num_qubits));
            gate_index++;
        }

        for (i = 0; i < two_qubit_per_layer; i++) {
            set_random_two_qubit_gate(&rng, &gates[gate_index], opts->num_qubits);
            gate_index++;
        }
    }

    *out_gates = gates;
    out_stats->gate_count = gate_index;
    out_stats->single_qubit_gate_count = single_qubit_per_layer * opts->depth;
    out_stats->two_qubit_gate_count = two_qubit_per_layer * opts->depth;
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
    RandomCircuitStats preheat_stats;
    int preheat_qubits = bench_effective_preheat_qubits(opts);
    Qureg preheat_qureg;

    preheat_opts.num_qubits = preheat_qubits;
    preheat_opts.depth = 2 * preheat_qubits;
    if (preheat_qubits < 2)
        preheat_opts.two_qubit_ratio = 0.0;

    if (!generate_random_circuit(&preheat_opts, &preheat_gates, &preheat_stats))
        return 0;

    preheat_qureg = bench_create_state_qureg_with_qubits(opts, preheat_qubits);
    initZeroState(preheat_qureg);
    syncQuESTEnv();
    (void) run_random_circuit(preheat_qureg, &preheat_opts, preheat_gates, preheat_stats.gate_count);
    destroyQureg(preheat_qureg);
    free(preheat_gates);
    return 1;
}

static void write_header(FILE* out) {
    if (out == NULL)
        return;
    fprintf(out,
            "platform\tbackend\tdeployment\tbenchmark\tlabel\tnum_qubits\trep\twarmup\tstatus\tsync_mode\ttotal_prob\tenv_num_nodes\tenv_num_threads\tpreheat_mode\tpreheat_qubits\tdepth\tseed\ttwo_qubit_ratio\tactual_two_qubit_ratio\tsingle_qubit_gate_count\ttwo_qubit_gate_count\tgate_count\ttotal_time_s\n");
}

static void write_row(FILE* out,
                      const BenchOptions* opts,
                      int rep,
                      int is_warmup,
                      const char* status,
                      qreal total_prob,
                      const RandomCircuitStats* stats,
                      double total_time_s) {
    double actual_two_qubit_ratio;

    if (out == NULL)
        return;
    actual_two_qubit_ratio = (stats->gate_count > 0) ? ((double) stats->two_qubit_gate_count / (double) stats->gate_count) : 0.0;
    fprintf(out,
            "%s\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%s\t%s\t%.12f\t%d\t%d\t%s\t%d\t%d\t%u\t%.6f\t%.6f\t%d\t%d\t%d\t%.9f\n",
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
            opts->two_qubit_ratio,
            actual_two_qubit_ratio,
            stats->single_qubit_gate_count,
            stats->two_qubit_gate_count,
            stats->gate_count,
            total_time_s);
}

int main(int argc, char** argv) {
    BenchOptions opts;
    BenchParseResult parse_result;
    FILE* out = NULL;
    int should_close = 0;
    int should_write_header = 0;
    RandomGate* gates = NULL;
    RandomCircuitStats stats;
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
    if (opts.num_qubits < 2 && opts.two_qubit_ratio > 0.0) {
        fprintf(stderr, "ERROR: two-qubit ratio requires at least 2 qubits\n");
        return EXIT_FAILURE;
    }

    if (!generate_random_circuit(&opts, &gates, &stats)) {
        fprintf(stderr, "ERROR: failed to generate random circuit\n");
        return EXIT_FAILURE;
    }

    bench_profile_range_push("quest.procedure");
    bench_profile_range_push("quest.lifecycle.environment_init");
    bench_init_environment();
    bench_profile_range_pop();
    if (!bench_open_output_for_rank(opts.output_path, &out, &should_close, &should_write_header)) {
        free(gates);
        bench_profile_range_push("quest.lifecycle.environment_finalize");
        finalizeQuESTEnv();
        bench_profile_range_pop();
        bench_profile_range_pop();
        return EXIT_FAILURE;
    }
    if (should_write_header)
        write_header(out);
    if (opts.preheat_mode == BENCH_PREHEAT_LIGHT && !run_random_light_preheat(&opts)) {
        fprintf(stderr, "ERROR: failed to run random light preheat\n");
        free(gates);
        bench_close_output(out, should_close);
        bench_profile_range_push("quest.lifecycle.environment_finalize");
        finalizeQuESTEnv();
        bench_profile_range_pop();
        bench_profile_range_pop();
        return EXIT_FAILURE;
    }
    bench_profile_range_push("quest.lifecycle.qureg_create");
    qureg = bench_create_state_qureg(&opts);
    bench_profile_range_pop();

    for (rep = 0; rep < opts.warmup + opts.reps; rep++) {
        int is_warmup = rep < opts.warmup;
        double total_time_s;
        qreal total_prob;
        const char* status;

        bench_profile_range_push("quest.lifecycle.state_init");
        initZeroState(qureg);
        syncQuESTEnv();
        bench_profile_range_pop();

        bench_profile_timed_begin(is_warmup);
        total_time_s = run_random_circuit(qureg, &opts, gates, stats.gate_count);
        bench_profile_timed_end(is_warmup);
        bench_profile_range_push("quest.lifecycle.validation");
        total_prob = calcTotalProb(qureg);
        status = bench_prob_is_valid(total_prob) ? BENCH_STATUS_PASS : BENCH_STATUS_FAILURE;
        bench_profile_range_pop();

        write_row(out, &opts, rep, is_warmup, status, total_prob, &stats, total_time_s);
        fflush(out);
    }

    bench_profile_range_push("quest.lifecycle.qureg_destroy");
    destroyQureg(qureg);
    bench_profile_range_pop();
    bench_profile_range_push("quest.lifecycle.environment_finalize");
    finalizeQuESTEnv();
    bench_profile_range_pop();
    bench_profile_range_pop();
    free(gates);
    bench_close_output(out, should_close);
    return EXIT_SUCCESS;
}
