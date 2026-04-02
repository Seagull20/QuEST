#ifndef EXPERIMENTS_BENCH_RNG_H
#define EXPERIMENTS_BENCH_RNG_H

#include <stdint.h>

typedef struct {
    uint64_t state;
} BenchRng;

static void bench_rng_seed(BenchRng* rng, uint64_t seed) {
    if (seed == 0)
        seed = 0x9E3779B97F4A7C15ULL;
    rng->state = seed;
}

static uint64_t bench_rng_next_u64(BenchRng* rng) {
    uint64_t x = rng->state;

    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;

    rng->state = x;
    return x * 0x2545F4914F6CDD1DULL;
}

static double bench_rng_next_unit(BenchRng* rng) {
    return (bench_rng_next_u64(rng) >> 11) * (1.0 / 9007199254740992.0);
}

static int bench_rng_next_int(BenchRng* rng, int upper_exclusive) {
    if (upper_exclusive <= 1)
        return 0;
    return (int) (bench_rng_next_u64(rng) % (uint64_t) upper_exclusive);
}

static void bench_rng_shuffle_ints(BenchRng* rng, int* values, int len) {
    int i;
    for (i = len - 1; i > 0; i--) {
        int j = bench_rng_next_int(rng, i + 1);
        int tmp = values[i];
        values[i] = values[j];
        values[j] = tmp;
    }
}

#endif
