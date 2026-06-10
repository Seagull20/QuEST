#include "../common/bench.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

static double run_qft(Qureg qureg, const BenchOptions* opts) {
    double total_start = bench_wall_time();

    applyFullQuantumFourierTransform(qureg);
    bench_sync_stage_if_needed(opts);
    bench_sync_finalize(opts);
    return bench_wall_time() - total_start;
}

static int run_qft_light_preheat(const BenchOptions* opts) {
    BenchOptions preheat_opts = *opts;
    int preheat_qubits = bench_effective_preheat_qubits(opts);
    Qureg preheat_qureg;

    preheat_opts.num_qubits = preheat_qubits;
    preheat_qureg = bench_create_state_qureg_with_qubits(opts, preheat_qubits);
    initZeroState(preheat_qureg);
    syncQuESTEnv();
    (void) run_qft(preheat_qureg, &preheat_opts);
    destroyQureg(preheat_qureg);
    return 1;
}

static void write_header(FILE* out) {
    if (out == NULL)
        return;
    fprintf(out,
            "platform\tbackend\tdeployment\tbenchmark\tlabel\tnum_qubits\trep\twarmup\tstatus\tsync_mode\ttotal_prob\tenv_num_nodes\tenv_num_threads\tpreheat_mode\tpreheat_qubits\tstage\tstage_label\tstage_time_s\ttotal_time_s\n");
}

static void write_stage_row(FILE* out,
                            const BenchOptions* opts,
                            int rep,
                            int is_warmup,
                            const char* status,
                            qreal total_prob,
                            int stage,
                            const char* stage_label,
                            double stage_time_s,
                            double total_time_s) {
    if (out == NULL)
        return;
    fprintf(out,
            "%s\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%s\t%s\t%.12f\t%d\t%d\t%s\t%d\t%d\t%s\t%.9f\t%.9f\n",
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
            stage,
            stage_label,
            stage_time_s,
            total_time_s);
}

int main(int argc, char** argv) {
    BenchOptions opts;
    BenchParseResult parse_result;
    FILE* out = NULL;
    int should_close = 0;
    int should_write_header = 0;
    Qureg qureg;
    int rep;

    bench_options_init(&opts, "qft");
    parse_result = bench_parse_options(&opts, argc, argv);
    if (parse_result == BENCH_PARSE_HELP) {
        bench_print_common_usage(stdout, "qft", "QFT benchmark calls QuEST's applyFullQuantumFourierTransform() API and emits an api_full_qft timing row plus a total row with stage=-1.");
        return EXIT_SUCCESS;
    }
    if (parse_result != BENCH_PARSE_OK || !bench_validate_runtime_request(&opts, stderr))
        return EXIT_FAILURE;

    bench_profile_range_push("quest.procedure");
    bench_init_environment();
    if (!bench_open_output_for_rank(opts.output_path, &out, &should_close, &should_write_header)) {
        finalizeQuESTEnv();
        bench_profile_range_pop();
        return EXIT_FAILURE;
    }
    if (should_write_header)
        write_header(out);
    if (opts.preheat_mode == BENCH_PREHEAT_LIGHT && !run_qft_light_preheat(&opts)) {
        fprintf(stderr, "ERROR: failed to run QFT light preheat\n");
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

        initZeroState(qureg);
        syncQuESTEnv();

        bench_profile_timed_begin(is_warmup);
        total_time_s = run_qft(qureg, &opts);
        bench_profile_timed_end(is_warmup);
        total_prob = calcTotalProb(qureg);
        status = bench_prob_is_valid(total_prob) ? BENCH_STATUS_PASS : BENCH_STATUS_FAILURE;

        write_stage_row(out, &opts, rep, is_warmup, status, total_prob, 0, "api_full_qft", total_time_s, total_time_s);
        write_stage_row(out, &opts, rep, is_warmup, status, total_prob, -1, "total", nan(""), total_time_s);
        fflush(out);
    }

    destroyQureg(qureg);
    finalizeQuESTEnv();
    bench_profile_range_pop();
    bench_close_output(out, should_close);
    return EXIT_SUCCESS;
}
