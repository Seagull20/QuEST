# Compression Toy Benchmark Development Log

## 2026-06-19 - Standalone QuEST-mimicking compression campaign scaffold

目标：建立一个不修改 QuEST communication internals 的 toy benchmark campaign，用 genuine QuEST-generated FP64 complex amplitude payload 检验 GPU-side lossless compression 是否值得进入 QuEST patch。

当前结论边界：

- 已实现 benchmark scaffold、capture tool、exchange tool、campaign runner 和 analysis rules。
- 尚未在 CUDA+MPI+nvCOMP cluster allocation 上执行 main campaign；因此目前还没有实验性 `PATCH_CANDIDATE` / `CONDITIONAL_BENEFIT` / `NO_BENEFIT` 结论。
- 本地可验证部分是 Python analysis verdict logic 和脚本语法；GPU build/run 需要带 nvCOMP 的 CUDA cluster environment。

## Design

模拟路径固定为当前 QuEST bottleneck 的 host-staged communication pattern：

```text
raw:
  GPU buffer -> D2H -> MPI host exchange -> H2D

compressed:
  GPU buffer -> nvCOMP compress -> D2H compressed bytes -> MPI host exchange
  -> H2D compressed bytes -> nvCOMP decompress
```

这个 benchmark 只回答 communication-path compression 是否 break even。它不评估 memory-capacity compression，不改变 state-vector layout，也不引入 lossy approximation。

## Genuine Payload Capture

`src/capture_quest_payloads.c` 通过 QuEST GPU+MPI build 捕获真实 amplitude payload：

- `quest_h_plus_pre_exchange`
- `quest_h_halfzero_pre_exchange`
- `quest_qft`
- `quest_random`

`quest_h_like` 在这个 campaign 中不是 synthetic pattern，而是 QuEST 真实生成的 H-path pre-exchange payload。原因是当前 profile 中 H/4-GPU 的问题点来自 high-target H 触发 distributed amplitude exchange；只看最终 state 会丢掉 repeated H 在 structured states 间切换的中间形态。

两个 H checkpoint 的语义：

- `quest_h_plus_pre_exchange`: `initPlusState()` 后、第一次 high-target H 前。它代表 uniform real amplitudes，对应第一次 exchange 前的高度 structured send data。
- `quest_h_halfzero_pre_exchange`: 执行一次 high-target H 后、第二次 high-target H 前。它代表 real-only half-zero / repeated structure，对应 repeated H path 的另一类真实 payload。

## Synthetic Calibration

Synthetic patterns 用来校准 pipeline，不作为 patch 证据：

- `zero_sparse`: positive control。若它不能压缩或加速，说明 codec overhead、chunking、MPI setup 或 implementation 有问题。
- `h_halfzero_real`: H-like structured control，验证 half-zero/repeated FP64 bytes 是否能被 lossless codec 利用。
- `phase_lattice`: deterministic finite phase set，校准 QFT-like structured complex phase data。
- `random_mantissa_normed`: normalized-scale FP64 complex random mantissa negative control，用来验证 fallback 和防止过度声称 compression 有效。

可压缩性判断最终以 genuine QuEST payload 为准。Synthetic win 只能证明 pipeline 对可压缩 bytes 工作正常。

## Implemented Files

- `CMakeLists.txt`: standalone CUDA+CXX build for `compression_exchange`，finds MPI, CUDA Toolkit, and nvCOMP.
- `build.sh`: builds QuEST `USER_SOURCE` capture executable and standalone nvCOMP exchange executable.
- `src/capture_quest_payloads.c`: writes per-rank `.bin` payloads and merged `payload_manifest.tsv`.
- `src/compression_exchange.cu`: runs raw and compressed exchange paths; verifies byte-identical reconstruction.
- `scripts/run_campaign.py`: executes capture, genuine/synthetic matrix, per-rank TSV collection, merge, and analysis.
- `scripts/analyze_campaign.py`: emits `summary.tsv`, `condition_verdicts.tsv`, `outliers.tsv`, and `conclusion.md`.
- `tests/test_analyze_campaign.py`: local unit tests for analysis verdict behavior.

## Codec Scope

初始受测 codec：

- `raw`
- `nvcomp_lz4`
- `nvcomp_gdeflate`
- `nvcomp_bitcomp`

`nvcomp_bitcomp` 使用 8-byte Bitcomp lane，因为 QuEST payload 是 interleaved FP64 complex amplitudes。若 installed nvCOMP 暴露 `NVCOMP_TYPE_DOUBLE`，则使用该类型；若使用 nvCOMP 5.1 这类只暴露 integer-width enum 的版本，则 fallback 到 `NVCOMP_TYPE_ULONGLONG`，把 FP64 payload 当作 64-bit bit patterns 压缩。

缺失 codec header 时，CMake macro 会让对应 path 返回 `SKIPPED_DEPENDENCY_MISSING`，不把 dependency absence 误判为 compression failure。

## Hetero Amp Microbench Reuse Decision

`/home/s2866920/hetero_amp_microbench` 暂不作为核心实现依赖。

可以复用的思想：

- timing breakdown fields
- TSV-first result format
- Slurm environment snapshot
- benchmark-local docs

不复用的部分：

- overflow compute model
- host/device memory capacity experiment assumptions
- simulator redesign logic

原因：本 campaign 的问题是 communication-path compression break-even，不是 heterogenous host overflow simulation。

## Acceptance Criteria

推荐进入 QuEST patch 之前必须满足：

- genuine payload reconstruction is byte-identical
- at least one codec has median speedup `>= 1.10`
- benefit holds for both H-like checkpoints and QFT
- no independent allocation regresses below raw median speedup `1.00`
- random payload either wins or cleanly falls back with low overhead

Outliers are flagged by IQR and coefficient of variation; they are never silently removed.

## Result Status

Cluster campaign executed on `mls-cluster` Teaching / Interactive RTX 2080 Ti nodes on 2026-06-19.

Documented result:

- Local report: `experiments/compression_toy_benchmark/docs/CLUSTER_CAMPAIGN_2026-06-19.md`
- Combined raw TSV: `experiments/results/raw/compression_toy_main_combined_3510764_3510766/exchange_samples.tsv`
- Combined processed summary: `experiments/results/processed/compression_toy_main_combined_3510764_3510766/summary.tsv`
- Combined condition verdicts: `experiments/results/processed/compression_toy_main_combined_3510764_3510766/condition_verdicts.tsv`
- Combined outliers: `experiments/results/processed/compression_toy_main_combined_3510764_3510766/outliers.tsv`

Cluster execution summary:

- Smoke job `3510673`: 2 ranks, small payload, raw/LZ4/GDeflate/Bitcomp; 48/48 rows `PASS`, byte-identical reconstruction.
- Main job `3510721`: full-size GDeflate attempt failed with CUDA out-of-memory on the first 1 GiB/rank genuine H-plus case.
- Main jobs `3510764`, `3510765`, `3510766`: 4 ranks, qubits=28, raw/LZ4/Bitcomp; each produced 2016/2016 `PASS` rows with byte-identical reconstruction.
- Combined main TSV: 6048 rows, all `PASS`, all `verify_status=PASS`, no missing raw baseline.
- Combined analyzer verdict: `PATCH_CANDIDATE`.

Engineering interpretation:

- Treat the verdict as a **conditional `PATCH_CANDIDATE`** rather than a universal compression win.
- `nvcomp_bitcomp` is the best first-path codec for a guarded QuEST prototype.
- H-like and QFT genuine payloads are consistently strong wins across all three independent allocations.
- Random genuine payload is only marginal for Bitcomp and loses for LZ4; production code therefore needs raw fallback and a cheap gating rule.
- GDeflate is not a first-path full-payload codec on the 2080Ti condition because it failed with CUDA out-of-memory.

## 2026-06-22 - NVTX / Nsight breakdown profile

目标：补做 standalone `compression_exchange` 的 NVTX breakdown，直接比较 default host staging 和 Bitcomp compression path。

新增实现：

- `compression_exchange.cu` 增加 opt-in NVTX ranges：
  - `quest_compression.exchange.raw`
  - `quest_compression.exchange.compressed`
  - `quest_compression.exchange.fallback_raw`
  - `quest_compression.stage.compress`
  - `quest_compression.stage.size_exchange`
  - `quest_compression.stage.d2h`
  - `quest_compression.stage.mpi`
  - `quest_compression.stage.h2d`
  - `quest_compression.stage.decompress`
- `CMakeLists.txt` / `build.sh` 支持 `QUEST_COMPRESSION_ENABLE_NVTX=1` 和 `QUEST_NVTX_INCLUDE_DIR`。
- `scripts/nvtx_breakdown.py` 从 Nsight `.sqlite` 生成 range samples、stage summary、raw-vs-Bitcomp comparison 和 markdown conclusion。
- `scripts/run_nvtx_profile.py` 串联 capture、raw profile、Bitcomp profile、SQLite export、TSV merge 和 analysis。
- `scripts/sbatch_nvtx_profile.sh` 在 cluster allocation 内 build + profile；默认使用 64 MiB exchange chunks for profiling correctness。
- `tests/test_nvtx_breakdown.py` 覆盖 direct `text` NVTX schema、`textId -> StringIds` schema，以及 missing stage 不静默变成 zero。

Local verification:

- `python3 -m unittest experiments/compression_toy_benchmark/tests/test_nvtx_breakdown.py experiments/compression_toy_benchmark/tests/test_analyze_campaign.py` passed。
- `bash -n experiments/compression_toy_benchmark/scripts/sbatch_nvtx_profile.sh` passed。

Cluster evidence:

- Accepted job: `3515222`
- Node: `landonia01`
- Resources: 4 ranks, 4 GPUs, 1 node
- Payload: genuine `quest_h_plus_pre_exchange`, `qubits=28`, full `1,073,741,824 bytes/rank`
- Profiling chunk: `chunk_amps=4,194,304` = 64 MiB/message
- Reps: `warmup=0`, `reps=3`
- Nsight: `--trace=cuda,mpi,nvtx,osrt --mpi-impl=openmpi`
- Correctness: 24/24 rows `status=PASS`, `verify_status=PASS`

Accepted result:

| Stage | raw median | Bitcomp median |
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
- Missing NVTX stages: none
- Outliers in focused run: none

Profiling-condition outlier:

- Job `3515204` used a single 1 GiB MPI message under Nsight MPI tracing.
- It produced complete NVTX ranges, but raw rows had `verify_status=FAIL` on several ranks.
- Because the previous non-Nsight campaign had raw 2016/2016 `PASS`, this is treated as a Nsight/MPI large-message profiling outlier, not as final benchmark evidence.
- The accepted run `3515222` keeps the same full payload but uses 64 MiB chunks; all rows pass.

Artifacts:

- Raw/profile: `experiments/results/raw/compression_toy_nvtx_3515222`
- Slurm output: `experiments/results/raw/compression_toy_nvtx_3515222_slurm.out`
- Processed: `experiments/results/processed/compression_toy_nvtx_3515222`
- Detailed note: `experiments/compression_toy_benchmark/docs/NVTX_BREAKDOWN_2026-06-22.md`

Interpretation:

- The breakdown explains why Bitcomp won in the main campaign: raw time is mostly MPI + host transfers, while Bitcomp makes transferred bytes tiny enough that compressed D2H/MPI/H2D are nearly negligible.
- The cost center moves to GPU compress/decompress.
- This supports a guarded Bitcomp communication-path prototype, not an unconditional QuEST patch.
- The prototype should preserve chunking, raw fallback, byte-identical verification, and a workload/entropy gate.
