#include "../common/bench.h"

#include <stdio.h>
#include <stdlib.h>

static void write_header(FILE* out) {
    fprintf(out,
            "platform\tbackend\tdeployment\tbenchmark\tlabel\tnum_qubits\trep\twarmup\tstatus\tsync_mode\ttotal_prob\tenv_num_nodes\ttarget_qubit\tgate_time_s\n");
}

static void write_row(FILE* out,
                      const BenchOptions* opts,
                      int rep,
                      int is_warmup,
                      const char* status,
                      qreal total_prob,
                      int target_qubit,
                      double gate_time_s) {
    fprintf(out,
            "%s\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%s\t%s\t%.12f\t%d\t%d\t%.9f\n",
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
            target_qubit,
            gate_time_s);
}

int main(int argc, char** argv) {
    BenchOptions opts;
    BenchParseResult parse_result;
    FILE* out = NULL;
    int should_close = 0;
    int should_write_header = 0;
    double* gate_times = NULL;
    Qureg qureg;
    int rep;
    int begin_target;
    int end_target;

    bench_options_init(&opts, "h_sweep");
    parse_result = bench_parse_options(&opts, argc, argv);
    if (parse_result == BENCH_PARSE_HELP) {
        bench_print_common_usage(stdout, "h_sweep", "When --target is omitted, h_sweep measures every target qubit.");
        return EXIT_SUCCESS;
    }
    if (parse_result != BENCH_PARSE_OK || !bench_validate_runtime_request(&opts, stderr))
        return EXIT_FAILURE;

    if (!bench_open_output(opts.output_path, &out, &should_close, &should_write_header))
        return EXIT_FAILURE;
    if (should_write_header)
        write_header(out);

    gate_times = (double*) malloc((size_t) opts.num_qubits * sizeof(double));
    if (gate_times == NULL) {
        fprintf(stderr, "ERROR: failed to allocate h_sweep timing buffer\n");
        bench_close_output(out, should_close);
        return EXIT_FAILURE;
    }

    begin_target = (opts.target >= 0) ? opts.target : 0;
    end_target = (opts.target >= 0) ? (opts.target + 1) : opts.num_qubits;

    bench_init_environment();
    qureg = bench_create_state_qureg(&opts);

    for (rep = 0; rep < opts.warmup + opts.reps; rep++) {
        int is_warmup = rep < opts.warmup;
        qreal total_prob;
        const char* status;
        int target;

        initZeroState(qureg);
        syncQuESTEnv();

        for (target = begin_target; target < end_target; target++) {
            double start = bench_wall_time();

            applyHadamard(qureg, target);
            bench_sync_stage_if_needed(&opts);
            gate_times[target] = bench_wall_time() - start;

            applyHadamard(qureg, target);
            if (opts.sync_mode == BENCH_SYNC_BENCHMARK)
                syncQuESTEnv();
        }

        bench_sync_finalize(&opts);
        total_prob = calcTotalProb(qureg);
        status = bench_prob_is_valid(total_prob) ? BENCH_STATUS_PASS : BENCH_STATUS_FAILURE;

        for (target = begin_target; target < end_target; target++)
            write_row(out, &opts, rep, is_warmup, status, total_prob, target, gate_times[target]);
        fflush(out);
    }

    destroyQureg(qureg);
    finalizeQuESTEnv();
    free(gate_times);
    bench_close_output(out, should_close);
    return EXIT_SUCCESS;
}
