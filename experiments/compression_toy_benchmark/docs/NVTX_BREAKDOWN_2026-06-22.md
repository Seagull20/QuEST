# NVTX Breakdown - Compression Toy Benchmark - 2026-06-22

## Purpose

This profile compares the standalone QuEST-mimicking exchange benchmark under two paths:

- raw default staging: `GPU -> D2H -> MPI host exchange -> H2D`
- Bitcomp compression: `GPU -> compress -> D2H compressed -> MPI -> H2D compressed -> decompress`

This is not a QuEST in-tree communication patch profile. It profiles `experiments/compression_toy_benchmark/src/compression_exchange.cu`.

## Accepted Run

- Job: `3515222`
- Cluster: `mls-cluster`
- Node: `landonia01`
- GPUs: 4x RTX 2080 Ti
- MPI: Open MPI 4.1.6
- CUDA: 12.8
- Nsight Systems: 2024.6.2
- nvCOMP root: `/home/s2866920/miniconda3/envs/quest_compression`
- Payload: genuine `quest_h_plus_pre_exchange`
- Qubits: 28
- Payload bytes per rank: `1,073,741,824`
- Chunking: `chunk_amps=4,194,304` = 64 MiB per MPI message
- Reps: `warmup=0`, `reps=3`
- Trace: `cuda,mpi,nvtx,osrt`

Correctness:

- rows: 24
- `status=PASS`: 24/24
- `verify_status=PASS`: 24/24
- required NVTX stages present: yes

## Breakdown

| Stage | Raw median | Bitcomp median |
|---|---:|---:|
| exchange total | 0.855085 s | 0.022647 s |
| D2H | 0.191693 s | 0.000756 s |
| MPI | 0.465922 s | 0.000911 s |
| H2D | 0.201224 s | 0.000851 s |
| compress | n/a | 0.009356 s |
| size exchange | n/a | 0.000937 s |
| decompress | n/a | 0.008377 s |

Derived metrics:

- NVTX top-level exchange speedup: `37.758x`
- Analyzer median speedup: `37.759x`
- Median compression ratio: `335.961x`
- Outlier count in focused run: `0`

## Interpretation

Raw staging is dominated by communication and host transfer:

- MPI median: `0.465922 s`
- D2H median: `0.191693 s`
- H2D median: `0.201224 s`

Bitcomp makes the transferred payload small enough that the compressed movement stages are almost negligible:

- compressed D2H median: `0.000756 s`
- compressed MPI median: `0.000911 s`
- compressed H2D median: `0.000851 s`

The dominant cost in the compressed path becomes GPU-side codec work:

- compress median: `0.009356 s`
- decompress median: `0.008377 s`

This supports the previous conditional patch-candidate conclusion for structured H-like payloads. It does not prove universal compression benefit.

## Rejected Profiling Run

Job `3515204` profiled the same full 1 GiB/rank payload as a single MPI message under Nsight MPI tracing.

Observed issue:

- NVTX ranges and SQLite export were produced.
- Bitcomp rows passed.
- Several raw rows had `verify_status=FAIL`.

Reason for rejection:

- The previous non-Nsight main campaign had raw 2016/2016 `PASS`.
- The accepted chunked profile `3515222` kept the same full payload and passed 24/24 rows.
- Therefore `3515204` is treated as a profiling-condition outlier, likely caused by Nsight MPI tracing around very large `MPI_Sendrecv` messages.

## Artifacts

- Raw/profile dir: `experiments/results/raw/compression_toy_nvtx_3515222`
- Slurm output: `experiments/results/raw/compression_toy_nvtx_3515222_slurm.out`
- Processed dir: `experiments/results/processed/compression_toy_nvtx_3515222`
- Main comparison: `experiments/results/processed/compression_toy_nvtx_3515222/nvtx_breakdown_comparison.tsv`
- Stage summary: `experiments/results/processed/compression_toy_nvtx_3515222/nvtx_breakdown_summary.tsv`
- Conclusion: `experiments/results/processed/compression_toy_nvtx_3515222/nvtx_breakdown_conclusion.md`

## QuEST Prototype Implication

The next QuEST-side experiment should be guarded:

- first codec: `nvcomp_bitcomp`
- scope: communication-path payloads only
- required: byte-identical reconstruction
- required: raw fallback
- required: chunked exchange path
- required: cheap workload or entropy gate

The prototype should not start with ML compression, lossy compression, GDeflate full-payload integration, or simulator redesign.
