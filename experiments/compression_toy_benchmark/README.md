# QuEST Compression Toy Benchmark

这个目录是 standalone CUDA+MPI+nvCOMP benchmark campaign，用来模拟 QuEST 当前 GPU+MPI 下的 CPU-staged amplitude exchange：

```text
raw path:
  GPU buffer -> D2H -> MPI host exchange -> H2D

compressed path:
  GPU buffer -> nvCOMP compress -> D2H compressed bytes -> MPI host exchange
  -> H2D compressed bytes -> nvCOMP decompress
```

它不 patch `comm_exchangeAmpsToBuffers` 或 `comm_exchangeSubBuffers`。目标是在 QuEST source integration 之前，用 genuine QuEST-generated FP64 complex amplitude payload 判断 compression 是否满足 break-even：

```text
T_compress + T_send_compressed + T_decompress < T_send_raw
```

## Components

- `src/capture_quest_payloads.c`: 使用 QuEST GPU+MPI build 生成 genuine payload files 和 `payload_manifest.tsv`。
- `src/compression_exchange.cu`: CUDA+MPI+nvCOMP exchange benchmark，输出 rank-local TSV。
- `scripts/run_campaign.py`: 运行 capture、synthetic/genuine matrix、合并 TSV、调用 analysis。
- `scripts/analyze_campaign.py`: 生成 `summary.tsv`、`condition_verdicts.tsv`、`outliers.tsv` 和 `conclusion.md`。
- `tests/test_analyze_campaign.py`: analysis verdict rules 的本地单元测试。

## Data Patterns

Genuine payload 是最终证据：

- `quest_h_plus_pre_exchange`: `initPlusState()` 后、第一次 high-target `applyHadamard(target=num_qubits-1)` 前。
- `quest_h_halfzero_pre_exchange`: 执行一次 high-target H 后、第二次 high-target H 前。
- `quest_qft`: QuEST `applyFullQuantumFourierTransform()` 后捕获。
- `quest_random`: deterministic mixed-gate random workload 后捕获。

Synthetic calibration 只验证 pipeline 行为，不作为 patch 证据：

- `zero_sparse`: positive control。
- `h_halfzero_real`: H-like real-only half-zero/repeated-value control。
- `phase_lattice`: finite deterministic phase set，模拟 QFT-like structure。
- `random_mantissa_normed`: high-entropy FP64 negative control。

## Build

```bash
cd /Users/linzeyu/Documents/01_Degree_Project/03_degree_project/work/QuEST
NVCOMP_ROOT=/path/to/nvcomp \
QUEST_COMPRESSION_CUDA_ARCH=75 \
experiments/compression_toy_benchmark/build.sh all
```

`build.sh capture` builds the QuEST `USER_SOURCE` capture executable. `build.sh exchange` builds the standalone nvCOMP exchange executable.

## Smoke Run

```bash
cd /Users/linzeyu/Documents/01_Degree_Project/03_degree_project/work/QuEST
experiments/compression_toy_benchmark/scripts/run_campaign.py --mode smoke
```

Smoke defaults:

- 2 ranks
- `quest_h_plus_pre_exchange`
- `zero_sparse,h_halfzero_real`
- `amps_to_buffers`
- `warmup=1`, `reps=2`
- `payload_amps=65536`

## Main Campaign

```bash
cd /Users/linzeyu/Documents/01_Degree_Project/03_degree_project/work/QuEST
experiments/compression_toy_benchmark/scripts/run_campaign.py \
  --mode main \
  --ranks 4 \
  --qubits 28 \
  --allocation-id "${SLURM_JOB_ID:-manual}"
```

Main defaults:

- 4 ranks
- genuine patterns: H-plus, H-halfzero, QFT, random
- synthetic patterns: zero-sparse, H-like, phase-lattice, random-mantissa
- exchange shapes: `amps_to_buffers,sub_buffers`
- codecs: `raw,nvcomp_lz4,nvcomp_gdeflate,nvcomp_bitcomp`
- `warmup=1`, `reps=7`
- `payload_amps=0`, meaning full captured local exchange payload

True independent-allocation evidence should come from separate Slurm allocations. The runner's `--allocations N` repeats labels inside one invocation; it is useful for scheduler scripting, but it does not by itself create independent cluster allocations.

## Verdict Rules

`scripts/analyze_campaign.py` reports:

- `PATCH_CANDIDATE`: required genuine H-plus, H-halfzero, and QFT conditions all have stable median speedup `>= 1.10` with no allocation regression.
- `CONDITIONAL_BENEFIT`: only some genuine conditions win.
- `SYNTHETIC_ONLY_BENEFIT`: calibration controls win but genuine QuEST payloads do not.
- `NO_BENEFIT`: genuine payloads fail break-even.

Random genuine payload is expected to either win or cleanly fall back; it should not drive an optimistic patch conclusion.

## nvCOMP API Notes

The exchange tool uses nvCOMP high-level C++ managers. NVIDIA documents that the high-level interface can chunk contiguous buffers and that decompression can be configured from an nvCOMP-native compressed buffer header, which is why MPI receive-side decompression parses the received compressed buffer rather than reusing the sender's local compression config:

- [NVIDIA nvCOMP docs](https://docs.nvidia.com/cuda/nvcomp/index.html)
- [High-level C++ Quick Start](https://docs.nvidia.com/cuda/nvcomp/samples/highlevel_cpp_quickstart.html)
- [C++ API reference](https://docs.nvidia.com/cuda/nvcomp/cpp_api.html)

For `nvcomp_bitcomp`, the tool uses an 8-byte Bitcomp lane for QuEST's interleaved FP64 complex values: `NVCOMP_TYPE_DOUBLE` when the installed nvCOMP exposes it, otherwise `NVCOMP_TYPE_ULONGLONG` for older nvCOMP headers that treat the same 64-bit payload as bit patterns.
