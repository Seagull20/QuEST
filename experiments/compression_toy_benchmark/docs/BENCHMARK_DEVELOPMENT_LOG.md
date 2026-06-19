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

`nvcomp_bitcomp` 明确使用 `NVCOMP_TYPE_DOUBLE` compression option，因为 QuEST payload 是 interleaved FP64 complex amplitudes。这个设置让 Bitcomp test 更接近 double precision amplitude buffer，而不是 generic byte-stream compression。

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

No cluster campaign has been executed from this local branch yet. The first real evidence-producing run should be:

```bash
cd /Users/linzeyu/Documents/01_Degree_Project/03_degree_project/work/QuEST
NVCOMP_ROOT=/path/to/nvcomp QUEST_COMPRESSION_CUDA_ARCH=75 \
  experiments/compression_toy_benchmark/build.sh all

experiments/compression_toy_benchmark/scripts/run_campaign.py --mode smoke
```

Then run the main campaign in three independent Slurm allocations and compare `condition_verdicts.tsv` and `conclusion.md` across allocations.
