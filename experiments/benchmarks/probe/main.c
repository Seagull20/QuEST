#include "../common/bench.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static void silence_child_output(void) {
    int fd = open("/dev/null", O_WRONLY);

    if (fd < 0)
        return;

    (void) dup2(fd, STDOUT_FILENO);
    (void) dup2(fd, STDERR_FILENO);

    if (fd > STDERR_FILENO)
        close(fd);
}

static int wait_for_child_success(pid_t pid) {
    int status = 0;

    while (waitpid(pid, &status, 0) < 0) {
        if (errno == EINTR)
            continue;
        perror("waitpid");
        exit(EXIT_FAILURE);
    }

    return WIFEXITED(status) && WEXITSTATUS(status) == EXIT_SUCCESS;
}

static void apply_validation_circuit(Qureg qureg, int num_qubits) {
    int limit = (num_qubits < 8) ? num_qubits : 8;
    int i;

    initZeroState(qureg);

    for (i = 0; i < limit; i++)
        applyHadamard(qureg, i);

    for (i = 0; i + 1 < limit; i++)
        applyControlledPauliX(qureg, i, i + 1);

    for (i = 0; i < limit; i++)
        applyRotateZ(qureg, i, (qreal) (BENCH_PI / 4.0));
}

static int probe_single_allocation(const BenchOptions* opts, int num_qubits) {
    pid_t pid;
    BenchOptions child_opts = *opts;

    fflush(NULL);
    pid = fork();
    if (pid < 0) {
        perror("fork");
        exit(EXIT_FAILURE);
    }

    if (pid == 0) {
        Qureg qureg;

        child_opts.num_qubits = num_qubits;
        silence_child_output();
        bench_init_environment();
        qureg = bench_create_state_qureg(&child_opts);
        destroyQureg(qureg);
        finalizeQuESTEnv();
        _exit(EXIT_SUCCESS);
    }

    return wait_for_child_success(pid);
}

static int probe_with_count(const BenchOptions* opts, int num_qubits, int* attempts) {
    (*attempts)++;
    return probe_single_allocation(opts, num_qubits);
}

static int find_max_allocatable_qubits(const BenchOptions* opts, int* attempts) {
    int low = 0;
    int high = opts->search_min;

    *attempts = 0;

    if (!probe_with_count(opts, high, attempts))
        return 0;

    low = high;
    while (high < opts->search_max) {
        int next_high = high;

        if (high > opts->search_max / 2)
            next_high = opts->search_max;
        else
            next_high = high * 2;

        if (next_high <= high)
            break;

        if (!probe_with_count(opts, next_high, attempts)) {
            high = next_high;
            break;
        }

        low = next_high;
        high = next_high;
    }

    if (low == opts->search_max)
        return low;

    while (low + 1 < high) {
        int mid = low + (high - low) / 2;
        if (probe_with_count(opts, mid, attempts))
            low = mid;
        else
            high = mid;
    }

    return low;
}

static void write_header(FILE* out) {
    fprintf(out,
            "platform\tbackend\tdeployment\tbenchmark\tlabel\tnum_qubits\trep\twarmup\tstatus\tsync_mode\ttotal_prob\tenv_num_nodes\tenv_num_threads\tpreheat_mode\tpreheat_qubits\tmax_qubits\tprobe_attempts\tsearch_min\tsearch_max\talloc_time_s\tvalidation_time_s\n");
}

static void write_row(FILE* out,
                      const BenchOptions* opts,
                      const char* status,
                      qreal total_prob,
                      int max_qubits,
                      int attempts,
                      double alloc_time_s,
                      double validation_time_s) {
    fprintf(out,
            "%s\t%s\t%s\t%s\t%s\t%d\t0\t0\t%s\t%s\t%.12f\t%d\t%d\t%s\t%d\t%d\t%d\t%d\t%d\t%.9f\t%.9f\n",
            bench_detect_platform(),
            bench_build_backend(),
            bench_distribution_string(opts->distribution),
            opts->benchmark_name,
            bench_label_or_default(opts),
            max_qubits,
            status,
            bench_sync_mode_string(opts->sync_mode),
            (double) total_prob,
            bench_env_num_nodes(),
            bench_env_num_threads(),
            bench_preheat_mode_string(opts->preheat_mode),
            bench_effective_preheat_qubits(opts),
            max_qubits,
            attempts,
            opts->search_min,
            opts->search_max,
            alloc_time_s,
            validation_time_s);
}

int main(int argc, char** argv) {
    BenchOptions opts;
    BenchParseResult parse_result;
    FILE* out = NULL;
    int should_close = 0;
    int should_write_header = 0;
    int attempts = 0;
    int max_qubits;
    Qureg qureg;
    qreal total_prob;
    double alloc_start;
    double alloc_time_s;
    double validation_start;
    double validation_time_s;

    bench_options_init(&opts, "probe");
    parse_result = bench_parse_options(&opts, argc, argv);
    if (parse_result == BENCH_PARSE_HELP) {
        bench_print_common_usage(stdout, "probe", "Probe searches the maximum allocatable qubit count.");
        return EXIT_SUCCESS;
    }
    if (parse_result != BENCH_PARSE_OK || !bench_validate_runtime_request(&opts, stderr))
        return EXIT_FAILURE;

    if (!bench_open_output(opts.output_path, &out, &should_close, &should_write_header))
        return EXIT_FAILURE;
    if (should_write_header)
        write_header(out);

    max_qubits = find_max_allocatable_qubits(&opts, &attempts);
    if (max_qubits < 1) {
        write_row(out, &opts, BENCH_STATUS_FAILURE, (qreal) 0.0, 0, attempts, 0.0, 0.0);
        bench_close_output(out, should_close);
        return EXIT_FAILURE;
    }

    opts.num_qubits = max_qubits;
    bench_init_environment();

    alloc_start = bench_wall_time();
    qureg = bench_create_state_qureg(&opts);
    alloc_time_s = bench_wall_time() - alloc_start;

    validation_start = bench_wall_time();
    apply_validation_circuit(qureg, opts.num_qubits);
    syncQuESTEnv();
    validation_time_s = bench_wall_time() - validation_start;

    total_prob = calcTotalProb(qureg);
    write_row(out,
              &opts,
              bench_prob_is_valid(total_prob) ? BENCH_STATUS_PASS : BENCH_STATUS_FAILURE,
              total_prob,
              max_qubits,
              attempts,
              alloc_time_s,
              validation_time_s);

    destroyQureg(qureg);
    finalizeQuESTEnv();
    bench_close_output(out, should_close);

    return bench_prob_is_valid(total_prob) ? EXIT_SUCCESS : EXIT_FAILURE;
}
