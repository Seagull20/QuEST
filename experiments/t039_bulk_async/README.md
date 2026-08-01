# T-039 bulk_async window verification

This is a PM-owned cluster probe for the QuEST implementation. It runs the
same deterministic `initDebugState` + distributed-target Hadamard operation in
raw CPU-staged mode and in `bulk_async`, then compares each rank's local
amplitude hash.

The selected path uses one `MPI_Win_allocate_shared` full-payload slot per rank
and registers the local and node-peer mappings with `cudaHostRegister`. The
ordinary `cpuCommBuffer` is intentionally retained for raw fallback, so an
opted-in distributed GPU Qureg has a transitional additional host payload
slot (`+1 payload host memory per rank`); VRAM is unchanged.

The script forces both comparison runs through CPU staging with
`QUEST_FORCE_CPU_STAGING=1`. This makes raw and window runs compare the same
QuEST exchange family even on a CUDA-aware MPI build. It also runs a forced
registration-consensus fallback case. The off-node case is run automatically
when the allocation has at least two nodes; otherwise the script reports that
case as skipped rather than pretending a one-node allocation exercised it.

## Exact cluster command

From the QuEST repository root, inside an already allocated GPU/MPI job, run:

```sh
bash experiments/t039_bulk_async/run_verification.sh
```

The script does not submit jobs, use SSH, or access the network. It configures
and builds the supplied probe in `experiments/t039_bulk_async/build/`, then
writes logs and a comparison report under `experiments/t039_bulk_async/results/`.

The default CUDA root and compiler match the T-036 cluster harness. They can be
overridden only when the PM's allocation uses different local paths:
`T039_CUDA_ROOT`, `T039_CUDA_COMPILER`, `T039_CUDA_ARCH`, and `T039_MPI_LAUNCHER`.

The local no-CUDA/default-off compile regression is separate and does not need
MPI, CUDA, a queue, or network access:

```sh
bash experiments/t039_bulk_async/run_local_compile_check.sh
```

It builds QuEST with the default-off deployment and compares the raw
`exchangeArrays` implementation byte-for-byte with base `114e021`.

Claude cross-review remains a merge gate. This worker did not run the cluster
verification or claim that review as completed.
