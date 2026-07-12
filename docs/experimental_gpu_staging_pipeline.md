# Experimental GPU CPU-staging pipeline

This note records the Phase-0 map and T-035 configuration scaffold. It does not
claim an implemented async or tiled algorithm: every requested staging mode
currently dispatches to QuEST's existing raw CPU-staged path.

## Build and runtime configuration

The scaffold is opt-in and is excluded from the default compilation:

```bash
cmake -S . -B build \
  -DQUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE=ON
```

When the option is `OFF` (the default), QuEST defines no experimental macro,
does not compile `comm_staging.cpp`, ignores the experimental variables, and
retains its pre-experiment behaviour.

When the option is `ON`, the following variables are loaded and validated once
during environment initialisation, following the existing `envvars.cpp`
pattern:

| Variable | Accepted values | Default | T-035 effect |
|---|---|---:|---|
| `QUEST_GPU_STAGING_MODE` | `raw`, `bulk_async`, `tiled_materialize`, `tiled_fused` | `raw` | all select raw |
| `QUEST_GPU_STAGING_TILE_MB` | positive integer MiB | `64` | recorded for later paths |
| `QUEST_GPU_STAGING_SLOTS` | positive integer | `3` | recorded for later paths |
| `QUEST_GPU_STAGING_PINNED` | `0`, `1` | `1` | recorded for later paths |
| `QUEST_GPU_STAGING_MPI_PROGRESS` | `wait`, `testsome`, `testany` | `testsome` | recorded for later paths |
| `QUEST_FORCE_CPU_STAGING` | `0`, `1` | `0` | `1` bypasses direct-GPU communication |

`quest/src/comm/comm_staging.cpp` is the mode-selection seam. Its selector has
only one effective path, `RAW`, until later phases implement and validate the
individual algorithms. The raw implementation remains in
`quest/src/comm/comm_routines.cpp`.

## Phase-0 communication map

This map consolidates the already completed communication-path audit, T-032 and
T-033 reports, and job 3527842 baseline. No source or profile re-inspection was
performed for this note.

| Concern | Existing QuEST location | Consolidated baseline |
|---|---|---|
| Qureg and communication-buffer allocation | `quest/src/api/qureg.cpp` | Distributed GPU Quregs allocate full-size `gpuAmps` and `gpuCommBuffer`; host `cpuAmps` and `cpuCommBuffer` are pageable allocations. |
| MPI exchange | `quest/src/comm/comm_routines.cpp` | Without direct GPU communication, the exchange is bulk D2H, MPI, then H2D. The MPI layer already slices large payloads into bounded messages. |
| GPU copy wrappers | `quest/src/gpu/gpu_config.cpp` and `.hpp` | `gpu_copyGpuToCpu()` and `gpu_copyCpuToGpu()` are the copy boundary used by the raw staged path. |
| Distributed target-gate dispatch | `quest/src/core/localiser.cpp`, then `quest/src/core/accelerator.cpp` | Localisation selects full-state or packed/sub-buffer exchange; accelerator dispatch reaches consumers which combine local amplitudes with `gpuCommBuffer`. |
| Memory accounting and fit checks | `quest/src/core/memory.cpp` and `quest/src/core/validation.cpp` | Current accounting includes full communication buffers; no tiled-capacity gain may be claimed until allocation itself becomes bounded. |

The validated T-032/T-033 harnesses established that bounded persistent scratch
can preserve q28 exchange throughput, handle packed/combining consumers and a
partial tail, and fit a q31-sized 8 GiB local payload using 512 MiB chunks. Those
are portability and capacity proofs, not an end-to-end QuEST speedup. The raw
3527842 profiles remain the comparison baseline; their reported ideal
D2H/MPI/H2D overlap bounds (1.48x--1.77x at selected q28/p4 points) are ceilings,
not achieved results.

Consolidated evidence sources in the parent degree-project workspace:

- `pm/analysis/2026-07-04-scratch-buffer-exchange-validation.md`
- `pm/analysis/2026-07-04-scratch-buffer-prototype-results.md` (T-032)
- `pm/analysis/2026-07-04-quest-portable-scratch-window-results.md` (T-033)
- `03_degree_project/Codebase/devlog/2026-07-04-tranche1-complete-qsd-access-bandwidth-answer.md` (3527842)
