#ifndef EXPERIMENTS_BENCH_H
#define EXPERIMENTS_BENCH_H

#include "quest.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

#ifdef __cplusplus
extern "C" {
#endif

void quest_profile_range_push(const char* name);
void quest_profile_range_pop(void);

#ifdef __cplusplus
}
#endif

static void bench_profile_range_push(const char* name) {
    quest_profile_range_push(name);
}

static void bench_profile_range_pop(void) {
    quest_profile_range_pop();
}

static void bench_profile_timed_begin(int is_warmup) {
    if (!is_warmup)
        bench_profile_range_push("quest.execution.timed");
}

static void bench_profile_timed_end(int is_warmup) {
    if (!is_warmup)
        bench_profile_range_pop();
}

#ifndef BENCH_BUILD_BACKEND_STR
#define BENCH_BUILD_BACKEND_STR "unknown"
#endif

#ifndef BENCH_BUILD_SUPPORTS_DISTRIBUTION
#define BENCH_BUILD_SUPPORTS_DISTRIBUTION 0
#endif

#ifndef BENCH_BUILD_SUPPORTS_GPU
#define BENCH_BUILD_SUPPORTS_GPU 0
#endif

#ifndef BENCH_BUILD_SUPPORTS_CUQUANTUM
#define BENCH_BUILD_SUPPORTS_CUQUANTUM 0
#endif

#define BENCH_DEFAULT_QUBITS 26
#define BENCH_DEFAULT_REPS 3
#define BENCH_DEFAULT_WARMUP 1
#define BENCH_DEFAULT_SEED 20260402u
#define BENCH_DEFAULT_SEARCH_MIN 1
#define BENCH_DEFAULT_SEARCH_MAX 62
#define BENCH_DEFAULT_GATE_REPEATS 64
#define BENCH_DEFAULT_TWO_QUBIT_RATIO 0.5
#define BENCH_PROB_TOL 1e-9
#define BENCH_PI 3.14159265358979323846

#define BENCH_STATUS_PASS "PASS"
#define BENCH_STATUS_FAILURE "FAILURE"
#define BENCH_STATUS_TODO "TODO"
#define BENCH_STATUS_MPI_CAPACITY_EXHAUSTED "MPI_CAPACITY_EXHAUSTED"

typedef enum {
    BENCH_DISTRIBUTION_OFF = 0,
    BENCH_DISTRIBUTION_ON = 1
} BenchDistribution;

typedef enum {
    BENCH_SYNC_BENCHMARK = 0,
    BENCH_SYNC_PROFILE = 1
} BenchSyncMode;

typedef enum {
    BENCH_PREHEAT_IDENTICAL = 0,
    BENCH_PREHEAT_LIGHT = 1,
    BENCH_PREHEAT_OFF = 2
} BenchPreheatMode;

typedef enum {
    BENCH_VALIDATION_DEFAULT = 0,
    BENCH_VALIDATION_H_LAST = 1,
    BENCH_VALIDATION_ALLOC_ONLY = 2
} BenchValidationKind;

typedef enum {
    BENCH_PARSE_OK = 0,
    BENCH_PARSE_HELP = 1,
    BENCH_PARSE_ERROR = 2
} BenchParseResult;

typedef struct {
    const char* benchmark_name;
    int num_qubits;
    int reps;
    int warmup;
    BenchDistribution distribution;
    BenchSyncMode sync_mode;
    BenchPreheatMode preheat_mode;
    const char* output_path;
    const char* label;
    const char* gate_kind;
    int control;
    int target;
    int gate_repeats;
    unsigned seed;
    int depth;
    double two_qubit_ratio;
    int search_min;
    int search_max;
    int preheat_qubits;
    BenchValidationKind validation_kind;
} BenchOptions;

static int bench_parse_positive_int(const char* text, int* out) {
    char* end = NULL;
    long value = strtol(text, &end, 10);

    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0' || value <= 0 || value > 2147483647L)
        return 0;

    *out = (int) value;
    return 1;
}

static int bench_parse_nonnegative_int(const char* text, int* out) {
    char* end = NULL;
    long value = strtol(text, &end, 10);

    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0' || value < 0 || value > 2147483647L)
        return 0;

    *out = (int) value;
    return 1;
}

static int bench_parse_unsigned_int(const char* text, unsigned* out) {
    char* end = NULL;
    unsigned long value = strtoul(text, &end, 10);

    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0' || value > 4294967295UL)
        return 0;

    *out = (unsigned) value;
    return 1;
}

static int bench_parse_unit_interval_double(const char* text, double* out) {
    char* end = NULL;
    double value = strtod(text, &end);

    if (text == NULL || text[0] == '\0' || end == NULL || *end != '\0' || value != value || value < 0.0 || value > 1.0)
        return 0;

    *out = value;
    return 1;
}

static int bench_hostname_contains(const char* haystack, const char* needle) {
    return strstr(haystack, needle) != NULL;
}

static void bench_options_init(BenchOptions* opts, const char* benchmark_name) {
    memset(opts, 0, sizeof(*opts));
    opts->benchmark_name = benchmark_name;
    opts->num_qubits = BENCH_DEFAULT_QUBITS;
    opts->reps = BENCH_DEFAULT_REPS;
    opts->warmup = BENCH_DEFAULT_WARMUP;
    opts->distribution = BENCH_DISTRIBUTION_OFF;
    opts->sync_mode = BENCH_SYNC_BENCHMARK;
    opts->preheat_mode = BENCH_PREHEAT_IDENTICAL;
    opts->output_path = NULL;
    opts->label = benchmark_name;
    opts->gate_kind = "h";
    opts->control = -1;
    opts->target = -1;
    opts->gate_repeats = BENCH_DEFAULT_GATE_REPEATS;
    opts->seed = BENCH_DEFAULT_SEED;
    opts->depth = -1;
    opts->two_qubit_ratio = BENCH_DEFAULT_TWO_QUBIT_RATIO;
    opts->search_min = BENCH_DEFAULT_SEARCH_MIN;
    opts->search_max = BENCH_DEFAULT_SEARCH_MAX;
    opts->preheat_qubits = 24;
    opts->validation_kind = BENCH_VALIDATION_DEFAULT;
}

static void bench_print_common_usage(FILE* out, const char* benchmark_name, const char* extra_usage) {
    fprintf(out, "Usage: %s [options]\n", benchmark_name);
    fprintf(out, "Common options:\n");
    fprintf(out, "  --qubits N            Number of qubits (default %d)\n", BENCH_DEFAULT_QUBITS);
    fprintf(out, "  --reps N              Number of timed repetitions (default %d)\n", BENCH_DEFAULT_REPS);
    fprintf(out, "  --warmup N            Number of warm-up repetitions (default %d)\n", BENCH_DEFAULT_WARMUP);
    fprintf(out, "  --distribution MODE   off | on\n");
    fprintf(out, "  --sync-mode MODE      benchmark | profile\n");
    fprintf(out, "  --preheat-mode MODE   identical | light | off\n");
    fprintf(out, "  --preheat-qubits N    Qubit count for light preheat (default %d)\n", 24);
    fprintf(out, "  --output PATH         Append TSV rows to PATH\n");
    fprintf(out, "  --label TEXT          Series label written to TSV\n");
    fprintf(out, "  --gate-kind KIND      Gate micro kind: h | cnot | cphase | hn\n");
    fprintf(out, "  --control K           Optional control qubit for gate_micro\n");
    fprintf(out, "  --target K            Optional target qubit for h_sweep/gate_micro\n");
    fprintf(out, "  --gate-repeats N      Gate micro repetitions (default %d)\n", BENCH_DEFAULT_GATE_REPEATS);
    fprintf(out, "  --seed N              RNG seed for random benchmark\n");
    fprintf(out, "  --depth N             Circuit depth for random benchmark\n");
    fprintf(out, "  --two-qubit-ratio R   Random circuit two-qubit gate ratio in [0,1]\n");
    fprintf(out, "  --search-min N        Probe lower bound\n");
    fprintf(out, "  --search-max N        Probe upper bound\n");
    fprintf(out, "  --validation-kind K   default | h_last | alloc_only (probe only)\n");
    fprintf(out, "  --help                Show this message\n");
    if (extra_usage != NULL && extra_usage[0] != '\0')
        fprintf(out, "\n%s\n", extra_usage);
}

static BenchParseResult bench_parse_options(BenchOptions* opts, int argc, char** argv) {
    int i;

    for (i = 1; i < argc; i++) {
        const char* arg = argv[i];

        if (strcmp(arg, "--help") == 0)
            return BENCH_PARSE_HELP;

        if (i + 1 >= argc) {
            fprintf(stderr, "ERROR: option '%s' requires a value\n", arg);
            return BENCH_PARSE_ERROR;
        }

        if (strcmp(arg, "--qubits") == 0) {
            if (!bench_parse_positive_int(argv[++i], &opts->num_qubits))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--reps") == 0) {
            if (!bench_parse_positive_int(argv[++i], &opts->reps))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--warmup") == 0) {
            if (!bench_parse_nonnegative_int(argv[++i], &opts->warmup))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--distribution") == 0) {
            const char* mode = argv[++i];
            if (strcmp(mode, "off") == 0)
                opts->distribution = BENCH_DISTRIBUTION_OFF;
            else if (strcmp(mode, "on") == 0)
                opts->distribution = BENCH_DISTRIBUTION_ON;
            else
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--sync-mode") == 0) {
            const char* mode = argv[++i];
            if (strcmp(mode, "benchmark") == 0)
                opts->sync_mode = BENCH_SYNC_BENCHMARK;
            else if (strcmp(mode, "profile") == 0)
                opts->sync_mode = BENCH_SYNC_PROFILE;
            else
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--preheat-mode") == 0) {
            const char* mode = argv[++i];
            if (strcmp(mode, "identical") == 0)
                opts->preheat_mode = BENCH_PREHEAT_IDENTICAL;
            else if (strcmp(mode, "light") == 0)
                opts->preheat_mode = BENCH_PREHEAT_LIGHT;
            else if (strcmp(mode, "off") == 0)
                opts->preheat_mode = BENCH_PREHEAT_OFF;
            else
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--preheat-qubits") == 0) {
            if (!bench_parse_positive_int(argv[++i], &opts->preheat_qubits))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--output") == 0) {
            opts->output_path = argv[++i];
        } else if (strcmp(arg, "--label") == 0) {
            opts->label = argv[++i];
        } else if (strcmp(arg, "--gate-kind") == 0) {
            opts->gate_kind = argv[++i];
        } else if (strcmp(arg, "--control") == 0) {
            if (!bench_parse_nonnegative_int(argv[++i], &opts->control))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--target") == 0) {
            if (!bench_parse_nonnegative_int(argv[++i], &opts->target))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--gate-repeats") == 0) {
            if (!bench_parse_positive_int(argv[++i], &opts->gate_repeats))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--seed") == 0) {
            if (!bench_parse_unsigned_int(argv[++i], &opts->seed))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--depth") == 0) {
            if (!bench_parse_positive_int(argv[++i], &opts->depth))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--two-qubit-ratio") == 0) {
            if (!bench_parse_unit_interval_double(argv[++i], &opts->two_qubit_ratio))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--search-min") == 0) {
            if (!bench_parse_positive_int(argv[++i], &opts->search_min))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--search-max") == 0) {
            if (!bench_parse_positive_int(argv[++i], &opts->search_max))
                return BENCH_PARSE_ERROR;
        } else if (strcmp(arg, "--validation-kind") == 0) {
            const char* kind = argv[++i];
            if (strcmp(kind, "default") == 0)
                opts->validation_kind = BENCH_VALIDATION_DEFAULT;
            else if (strcmp(kind, "h_last") == 0)
                opts->validation_kind = BENCH_VALIDATION_H_LAST;
            else if (strcmp(kind, "alloc_only") == 0)
                opts->validation_kind = BENCH_VALIDATION_ALLOC_ONLY;
            else
                return BENCH_PARSE_ERROR;
        } else {
            fprintf(stderr, "ERROR: unknown option '%s'\n", arg);
            return BENCH_PARSE_ERROR;
        }
    }

    return BENCH_PARSE_OK;
}

static const char* bench_build_backend(void) {
    return BENCH_BUILD_BACKEND_STR;
}

static const char* bench_detect_platform(void) {
    const char* env_platform = getenv("BENCH_PLATFORM");
    static char platform[64];
    char hostname[256];

    if (env_platform != NULL && env_platform[0] != '\0')
        return env_platform;

    if (gethostname(hostname, sizeof(hostname)) != 0)
        return "local";

    hostname[sizeof(hostname) - 1] = '\0';

    if (strncmp(hostname, "ln", 2) == 0 || bench_hostname_contains(hostname, "archer2")) {
        strncpy(platform, "archer2", sizeof(platform) - 1);
    } else if (bench_hostname_contains(hostname, "crannog") ||
               bench_hostname_contains(hostname, "stanger") ||
               bench_hostname_contains(hostname, "landonia") ||
               bench_hostname_contains(hostname, "damnii") ||
               bench_hostname_contains(hostname, "saxa") ||
               bench_hostname_contains(hostname, "mlp")) {
        strncpy(platform, "cluster", sizeof(platform) - 1);
    } else {
        strncpy(platform, "local", sizeof(platform) - 1);
    }

    platform[sizeof(platform) - 1] = '\0';
    return platform;
}

static const char* bench_distribution_string(BenchDistribution distribution) {
    return (distribution == BENCH_DISTRIBUTION_ON) ? "on" : "off";
}

static const char* bench_sync_mode_string(BenchSyncMode sync_mode) {
    return (sync_mode == BENCH_SYNC_PROFILE) ? "profile" : "benchmark";
}

static const char* bench_preheat_mode_string(BenchPreheatMode preheat_mode) {
    switch (preheat_mode) {
        case BENCH_PREHEAT_LIGHT:
            return "light";
        case BENCH_PREHEAT_OFF:
            return "off";
        case BENCH_PREHEAT_IDENTICAL:
        default:
            return "identical";
    }
}

static const char* bench_validation_kind_string(BenchValidationKind validation_kind) {
    switch (validation_kind) {
        case BENCH_VALIDATION_H_LAST:
            return "h_last";
        case BENCH_VALIDATION_ALLOC_ONLY:
            return "alloc_only";
        case BENCH_VALIDATION_DEFAULT:
        default:
            return "default";
    }
}

static const char* bench_label_or_default(const BenchOptions* opts) {
    if (opts->label != NULL && opts->label[0] != '\0')
        return opts->label;
    return opts->benchmark_name;
}

static double bench_wall_time(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (double) tv.tv_sec + (double) tv.tv_usec * 1e-6;
}

static int bench_should_sync_stage(const BenchOptions* opts) {
    return opts->sync_mode == BENCH_SYNC_BENCHMARK;
}

static void bench_sync_stage_if_needed(const BenchOptions* opts) {
    if (bench_should_sync_stage(opts))
        syncQuESTEnv();
}

static void bench_sync_finalize(const BenchOptions* opts) {
    if (opts->sync_mode == BENCH_SYNC_PROFILE)
        syncQuESTEnv();
}

static int bench_validate_runtime_request(const BenchOptions* opts, FILE* err) {
    if (opts->distribution == BENCH_DISTRIBUTION_ON && !BENCH_BUILD_SUPPORTS_DISTRIBUTION) {
        fprintf(err, "ERROR: build backend '%s' does not support distribution\n", BENCH_BUILD_BACKEND_STR);
        return 0;
    }

    if (opts->num_qubits < 1 || opts->reps < 1 || opts->warmup < 0) {
        fprintf(err, "ERROR: invalid numeric options\n");
        return 0;
    }

    if (opts->search_min < 1 || opts->search_max < opts->search_min) {
        fprintf(err, "ERROR: invalid probe search range\n");
        return 0;
    }

    if (opts->target >= opts->num_qubits && opts->target >= 0) {
        fprintf(err, "ERROR: target qubit %d is outside [0, %d)\n", opts->target, opts->num_qubits);
        return 0;
    }

    if (opts->control >= opts->num_qubits && opts->control >= 0) {
        fprintf(err, "ERROR: control qubit %d is outside [0, %d)\n", opts->control, opts->num_qubits);
        return 0;
    }

    if (opts->control >= 0 && opts->target >= 0 && opts->control == opts->target) {
        fprintf(err, "ERROR: control and target qubits must be different\n");
        return 0;
    }

    if (opts->depth == 0) {
        fprintf(err, "ERROR: depth must be positive when specified\n");
        return 0;
    }

    if (opts->preheat_qubits < 1) {
        fprintf(err, "ERROR: preheat qubits must be positive\n");
        return 0;
    }

    if (opts->gate_repeats < 1) {
        fprintf(err, "ERROR: gate repeats must be positive\n");
        return 0;
    }

    return 1;
}

static void bench_init_environment(void) {
    initCustomQuESTEnv(BENCH_BUILD_SUPPORTS_DISTRIBUTION ? 1 : 0,
                       BENCH_BUILD_SUPPORTS_GPU ? 1 : 0,
                       1);
}

static Qureg bench_create_state_qureg(const BenchOptions* opts) {
    return createCustomQureg(opts->num_qubits,
                             0,
                             opts->distribution == BENCH_DISTRIBUTION_ON ? 1 : 0,
                             BENCH_BUILD_SUPPORTS_GPU ? 1 : 0,
                             1);
}

static Qureg bench_create_state_qureg_with_qubits(const BenchOptions* opts, int num_qubits) {
    return createCustomQureg(num_qubits,
                             0,
                             opts->distribution == BENCH_DISTRIBUTION_ON ? 1 : 0,
                             BENCH_BUILD_SUPPORTS_GPU ? 1 : 0,
                             1);
}

static int bench_open_output(const char* path, FILE** out, int* should_close, int* should_write_header) {
    struct stat st;

    if (path == NULL || path[0] == '\0') {
        *out = stdout;
        *should_close = 0;
        *should_write_header = 1;
        return 1;
    }

    *should_write_header = (stat(path, &st) != 0 || st.st_size == 0);
    *out = fopen(path, "a");
    if (*out == NULL) {
        fprintf(stderr, "ERROR: failed to open output '%s': %s\n", path, strerror(errno));
        return 0;
    }

    *should_close = 1;
    return 1;
}

static void bench_close_output(FILE* out, int should_close) {
    if (should_close && out != NULL)
        fclose(out);
}

static int bench_prob_is_valid(qreal prob) {
    double diff = (double) prob - 1.0;
    if (diff < 0.0)
        diff = -diff;
    return diff <= BENCH_PROB_TOL;
}

static int bench_env_num_nodes(void) {
    QuESTEnv env = getQuESTEnv();
    return env.numNodes;
}

static int bench_is_output_rank(void) {
    QuESTEnv env = getQuESTEnv();
    return env.rank == 0;
}

static int bench_env_num_threads(void) {
    const char* text = getenv("OMP_NUM_THREADS");
    int threads = 1;
    if (text != NULL && bench_parse_positive_int(text, &threads))
        return threads;
    return 1;
}

static int bench_open_output_for_rank(const char* path, FILE** out, int* should_close, int* should_write_header) {
    if (!bench_is_output_rank()) {
        *out = NULL;
        *should_close = 0;
        *should_write_header = 0;
        return 1;
    }
    return bench_open_output(path, out, should_close, should_write_header);
}

static int bench_effective_preheat_qubits(const BenchOptions* opts) {
    if (opts->preheat_qubits < opts->num_qubits)
        return opts->preheat_qubits;
    return opts->num_qubits;
}

static qreal bench_discrete_angle_from_index(int idx) {
    static const qreal angles[] = {
        (qreal) (BENCH_PI / 8.0),
        (qreal) (-BENCH_PI / 8.0),
        (qreal) (BENCH_PI / 4.0),
        (qreal) (-BENCH_PI / 4.0),
        (qreal) (BENCH_PI / 2.0),
        (qreal) (-BENCH_PI / 2.0)
    };
    int mod = idx % (int) (sizeof(angles) / sizeof(angles[0]));
    if (mod < 0)
        mod += (int) (sizeof(angles) / sizeof(angles[0]));
    return angles[mod];
}

#endif
