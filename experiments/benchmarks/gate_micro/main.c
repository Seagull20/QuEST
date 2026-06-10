#include "../common/bench.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef enum {
    GATE_KIND_H = 0,
    GATE_KIND_CNOT = 1,
    GATE_KIND_CPHASE = 2,
    GATE_KIND_HN = 3
} GateMicroKind;

static int parse_gate_kind(const char* text, GateMicroKind* out) {
    if (strcmp(text, "h") == 0) {
        *out = GATE_KIND_H;
        return 1;
    }
    if (strcmp(text, "cnot") == 0) {
        *out = GATE_KIND_CNOT;
        return 1;
    }
    if (strcmp(text, "cphase") == 0) {
        *out = GATE_KIND_CPHASE;
        return 1;
    }
    if (strcmp(text, "hn") == 0) {
        *out = GATE_KIND_HN;
        return 1;
    }
    return 0;
}

static const char* gate_kind_string(GateMicroKind kind) {
    switch (kind) {
        case GATE_KIND_CNOT:
            return "cnot";
        case GATE_KIND_CPHASE:
            return "cphase";
        case GATE_KIND_HN:
            return "hn";
        case GATE_KIND_H:
        default:
            return "h";
    }
}

static int resolve_target_qubit(const BenchOptions* opts, GateMicroKind kind) {
    if (kind == GATE_KIND_HN)
        return -1;
    if (opts->target >= 0)
        return opts->target;
    return opts->num_qubits - 1;
}

static int resolve_control_qubit(const BenchOptions* opts, int target) {
    if (opts->control >= 0)
        return opts->control;
    if (target != 0)
        return 0;
    return 1;
}

static int gate_count_for_kind(const BenchOptions* opts, GateMicroKind kind) {
    if (kind == GATE_KIND_HN)
        return opts->gate_repeats * opts->num_qubits;
    return opts->gate_repeats;
}

static double run_gate_micro(Qureg qureg, const BenchOptions* opts, GateMicroKind kind, int control, int target) {
    double start;
    int rep;
    int qubit;

    start = bench_wall_time();
    for (rep = 0; rep < opts->gate_repeats; rep++) {
        switch (kind) {
            case GATE_KIND_H:
                applyHadamard(qureg, target);
                break;
            case GATE_KIND_CNOT:
                applyControlledPauliX(qureg, control, target);
                break;
            case GATE_KIND_CPHASE:
                applyTwoQubitPhaseShift(qureg, control, target, (qreal) (BENCH_PI / 4.0));
                break;
            case GATE_KIND_HN:
                for (qubit = 0; qubit < opts->num_qubits; qubit++)
                    applyHadamard(qureg, qubit);
                break;
        }
    }

    bench_sync_stage_if_needed(opts);
    bench_sync_finalize(opts);
    return bench_wall_time() - start;
}

static int run_gate_micro_light_preheat(const BenchOptions* opts, GateMicroKind kind) {
    BenchOptions preheat_opts = *opts;
    int preheat_qubits = bench_effective_preheat_qubits(opts);
    int target;
    int control;
    Qureg preheat_qureg;

    if (preheat_qubits < 2 && kind != GATE_KIND_H && kind != GATE_KIND_HN)
        preheat_qubits = 2;

    preheat_opts.num_qubits = preheat_qubits;
    target = resolve_target_qubit(&preheat_opts, kind);
    control = (kind == GATE_KIND_CNOT || kind == GATE_KIND_CPHASE) ? resolve_control_qubit(&preheat_opts, target) : -1;

    preheat_qureg = bench_create_state_qureg_with_qubits(opts, preheat_qubits);
    initPlusState(preheat_qureg);
    syncQuESTEnv();
    (void) run_gate_micro(preheat_qureg, &preheat_opts, kind, control, target);
    destroyQureg(preheat_qureg);
    return 1;
}

static void write_header(FILE* out) {
    if (out == NULL)
        return;
    fprintf(out,
            "platform\tbackend\tdeployment\tbenchmark\tlabel\tnum_qubits\trep\twarmup\tstatus\tsync_mode\ttotal_prob\tenv_num_nodes\tenv_num_threads\tpreheat_mode\tpreheat_qubits\tgate_kind\tcontrol_qubit\ttarget_qubit\tgate_repeats\tgate_count\ttotal_time_s\ttime_per_gate_s\n");
}

static void write_row(FILE* out,
                      const BenchOptions* opts,
                      int rep,
                      int is_warmup,
                      const char* status,
                      qreal total_prob,
                      GateMicroKind kind,
                      int control,
                      int target,
                      int gate_count,
                      double total_time_s) {
    if (out == NULL)
        return;
    fprintf(out,
            "%s\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%s\t%s\t%.12f\t%d\t%d\t%s\t%d\t%s\t%d\t%d\t%d\t%d\t%.9f\t%.12f\n",
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
            gate_kind_string(kind),
            control,
            target,
            opts->gate_repeats,
            gate_count,
            total_time_s,
            total_time_s / (double) gate_count);
}

int main(int argc, char** argv) {
    BenchOptions opts;
    BenchParseResult parse_result;
    FILE* out = NULL;
    int should_close = 0;
    int should_write_header = 0;
    GateMicroKind kind;
    Qureg qureg;
    int rep;
    int target;
    int control = -1;
    int gate_count;

    bench_options_init(&opts, "gate_micro");
    parse_result = bench_parse_options(&opts, argc, argv);
    if (parse_result == BENCH_PARSE_HELP) {
        bench_print_common_usage(stdout, "gate_micro", "Gate micro kinds: h, cnot, cphase, hn. Defaults target to the highest qubit and control to q0 when needed.");
        return EXIT_SUCCESS;
    }
    if (parse_result != BENCH_PARSE_OK || !bench_validate_runtime_request(&opts, stderr))
        return EXIT_FAILURE;
    if (!parse_gate_kind(opts.gate_kind, &kind)) {
        fprintf(stderr, "ERROR: unsupported gate kind '%s'\n", opts.gate_kind);
        return EXIT_FAILURE;
    }
    if (opts.num_qubits < 2 && (kind == GATE_KIND_CNOT || kind == GATE_KIND_CPHASE)) {
        fprintf(stderr, "ERROR: gate kind '%s' requires at least 2 qubits\n", opts.gate_kind);
        return EXIT_FAILURE;
    }

    target = resolve_target_qubit(&opts, kind);
    if (kind == GATE_KIND_CNOT || kind == GATE_KIND_CPHASE) {
        control = resolve_control_qubit(&opts, target);
        if (control == target) {
            fprintf(stderr, "ERROR: control and target qubits must be different\n");
            return EXIT_FAILURE;
        }
    }
    gate_count = gate_count_for_kind(&opts, kind);

    bench_profile_range_push("quest.procedure");
    bench_init_environment();
    if (!bench_open_output_for_rank(opts.output_path, &out, &should_close, &should_write_header)) {
        finalizeQuESTEnv();
        bench_profile_range_pop();
        return EXIT_FAILURE;
    }
    if (should_write_header)
        write_header(out);
    if (opts.preheat_mode == BENCH_PREHEAT_LIGHT && !run_gate_micro_light_preheat(&opts, kind)) {
        fprintf(stderr, "ERROR: failed to run gate_micro light preheat\n");
        bench_close_output(out, should_close);
        finalizeQuESTEnv();
        bench_profile_range_pop();
        return EXIT_FAILURE;
    }
    qureg = bench_create_state_qureg(&opts);

    for (rep = 0; rep < opts.warmup + opts.reps; rep++) {
        int is_warmup = rep < opts.warmup;
        double total_time_s;
        qreal total_prob;
        const char* status;

        initPlusState(qureg);
        syncQuESTEnv();

        bench_profile_timed_begin(is_warmup);
        total_time_s = run_gate_micro(qureg, &opts, kind, control, target);
        bench_profile_timed_end(is_warmup);
        total_prob = calcTotalProb(qureg);
        status = bench_prob_is_valid(total_prob) ? BENCH_STATUS_PASS : BENCH_STATUS_FAILURE;

        write_row(out, &opts, rep, is_warmup, status, total_prob, kind, control, target, gate_count, total_time_s);
        fflush(out);
    }

    destroyQureg(qureg);
    finalizeQuESTEnv();
    bench_profile_range_pop();
    bench_close_output(out, should_close);
    return EXIT_SUCCESS;
}
