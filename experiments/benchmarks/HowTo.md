# QuEST Benchmark Suite 使用说明

这份说明面向当前 fork 中 `experiments/` 下的 benchmark harness。它说明如何 build、如何运行单个 benchmark、如何运行 proposal suite、如何在 cluster 上跑 4-GPU GPU+MPI validate/profile，以及如何读取结果。

所有命令默认从 QuEST repo root 执行：

```bash
cd /path/to/QuEST
```

在本机当前 workspace 中，repo root 是：

```bash
cd /Users/linzeyu/Documents/01_Degree_Project/03_degree_project/work/QuEST
```

## 1. Benchmark Suite 由什么组成

当前 proposal-aligned suite 的核心 benchmark 是三类：

| benchmark | 角色 | 主要观察什么 |
|---|---|---|
| `gate_micro` | gate-level microbenchmark | 单门 kernel 成本、qubit index/stride 敏感性、local vs rank-crossing 行为、launch/runtime overhead |
| `qft` | structured algorithmic workload | QFT 这种 long-range / controlled-phase-heavy 线路的整体 runtime、locality/data movement/communication sensitivity |
| `random` | fixed-depth random circuits | 非 QFT 的 average-case 行为、depth 和 two-qubit ratio 对 runtime 的影响 |

非核心但仍可用的辅助 benchmark：

| benchmark | 用途 |
|---|---|
| `probe` | 容量/环境 smoke test，用于确认某 backend 大致能跑到多少 qubits |
| `h_sweep` | legacy stride/communication diagnostic，不是 proposal 主 suite |

proposal 中的 optional `QAOA(p=2)` 当前没有纳入实现。

## 2. Build

通用 build 入口是：

```bash
./experiments/build.sh <benchmark> <backend> [build_type]
```

列出所有 benchmark：

```bash
./experiments/build.sh list
```

清理所有 build tree：

```bash
./experiments/build.sh clean
```

### 2.1 benchmark 参数

可用 benchmark：

| benchmark | 说明 |
|---|---|
| `probe` | capacity / runtime smoke benchmark |
| `gate_micro` | proposal 的 gate-level microbenchmark，支持 `h/cnot/cphase/hn` |
| `h_sweep` | legacy Hadamard target sweep |
| `qft` | QuEST API QFT benchmark，调用 `applyFullQuantumFourierTransform()` |
| `random` | fixed-depth random circuit benchmark，支持 `--two-qubit-ratio` |

注意：`h`、`cnot`、`cphase`、`hn` 不是单独的 benchmark 名称，而是 `gate_micro` 的 `--gate-kind` 参数。

### 2.2 backend 参数

可用 backend：

| backend | 说明 |
|---|---|
| `cpu` | 单进程 CPU path |
| `cpu_mpi` | MPI build，通常用于 distributed CPU 或 ARCHER2 路径 |
| `gpu` | CUDA GPU path，非 MPI |
| `cuquantum` | CUDA + cuQuantum path |
| `gpu_mpi` | CUDA + MPI distributed path，用于 MLS cluster GPU+MPI |

### 2.3 build type

第三个参数是 CMake build type，可省略。默认是 `Release`。

常用值：

| build type | 说明 |
|---|---|
| `Release` | 默认，用于正式 benchmark |
| `Debug` | 调试用，性能结果不可直接作为 benchmark 结论 |
| `RelWithDebInfo` | release 优化 + debug symbol，适合 profiling/debug 混合场景 |
| `MinSizeRel` | size-optimised build，当前 benchmark 一般不用 |

### 2.4 build 示例

本机 CPU smoke：

```bash
./experiments/build.sh gate_micro cpu
./experiments/build.sh qft cpu
./experiments/build.sh random cpu
```

GPU+MPI build：

```bash
./experiments/build.sh qft gpu_mpi
./experiments/build.sh gate_micro gpu_mpi
./experiments/build.sh random gpu_mpi
```

build 输出目录形如：

```text
experiments/build/<benchmark>/<backend>/<executable>
```

例如：

```text
experiments/build/qft/cpu/qft
experiments/build/random/gpu_mpi/random
```

### 2.5 build 并行度

`build.sh` 会调用：

```bash
cmake --build <build_dir> --config <build_type> --parallel <jobs> --target <executable>
```

默认并行度：

- 在 Slurm job 中：优先使用 `SLURM_CPUS_PER_TASK * SLURM_NTASKS`
- 非 Slurm 环境：使用本机在线 CPU 数

手动覆盖：

```bash
QUEST_BENCH_BUILD_PARALLEL=4 ./experiments/build.sh qft gpu_mpi
```

## 3. 直接运行单个 benchmark

每个 benchmark executable 都支持一组 shared CLI 参数。常用参数如下：

| 参数 | 含义 |
|---|---|
| `--qubits N` | qubit 数 |
| `--reps N` | timed repetition 数 |
| `--warmup N` | warmup repetition 数 |
| `--distribution off/on` | 是否启用 QuEST distributed mode |
| `--sync-mode benchmark/profile` | 普通 benchmark timing 或 profiler timing |
| `--preheat-mode identical/light/off` | 是否在正式测量前做 preheat |
| `--preheat-qubits N` | light preheat 使用的 qubit 数 |
| `--output PATH` | 追加 TSV 输出到指定文件 |
| `--label TEXT` | 写入 TSV 的 label |
| `--help` | 查看 benchmark 自身帮助 |

### 3.1 `gate_micro`

`gate_micro` 支持：

| `--gate-kind` | 含义 |
|---|---|
| `h` | 在一个 target qubit 上重复应用 Hadamard |
| `cnot` | 重复应用 controlled-X |
| `cphase` | 重复应用 controlled phase shift |
| `hn` | 每轮对全部 qubits 应用 Hadamard，即 `H^{\otimes n}` 风格 |

常用参数：

| 参数 | 含义 |
|---|---|
| `--gate-kind h|cnot|cphase|hn` | gate 类型 |
| `--target K` | target qubit；不传时使用默认 target |
| `--control K` | control qubit；仅对 controlled gates 有意义 |
| `--gate-repeats N` | 重复次数 |

示例：

```bash
./experiments/build/gate_micro/cpu/gate_micro \
  --qubits 4 \
  --gate-kind h \
  --gate-repeats 3 \
  --reps 1 \
  --warmup 0 \
  --preheat-mode off
```

跑四种 gate kind：

```bash
for kind in h cnot cphase hn; do
  ./experiments/build/gate_micro/cpu/gate_micro \
    --qubits 4 \
    --gate-kind "$kind" \
    --gate-repeats 3 \
    --reps 1 \
    --warmup 0 \
    --preheat-mode off
done
```

关键 TSV 字段：

```text
gate_kind
control_qubit
target_qubit
gate_repeats
gate_count
total_time_s
time_per_gate_s
```

### 3.2 `qft`

当前 `qft` benchmark 直接调用 QuEST API：

```c
applyFullQuantumFourierTransform()
```

示例：

```bash
./experiments/build/qft/cpu/qft \
  --qubits 4 \
  --reps 1 \
  --warmup 0 \
  --preheat-mode off
```

关键 TSV 字段：

```text
stage
stage_label
stage_time_s
total_time_s
```

预期会输出两行：

```text
api_full_qft
total
```

### 3.3 `random`

`random` 是 fixed-depth random circuit benchmark。

常用参数：

| 参数 | 含义 |
|---|---|
| `--depth N` | circuit depth |
| `--seed N` | deterministic seed |
| `--two-qubit-ratio R` | two-qubit gate ratio，范围 `[0, 1]` |

示例：

```bash
./experiments/build/random/cpu/random \
  --qubits 4 \
  --depth 4 \
  --two-qubit-ratio 0.5 \
  --reps 1 \
  --warmup 0 \
  --preheat-mode off
```

关键 TSV 字段：

```text
depth
seed
two_qubit_ratio
actual_two_qubit_ratio
single_qubit_gate_count
two_qubit_gate_count
gate_count
total_time_s
```

## 4. 运行 proposal suite

proposal suite 是：

```text
gate_micro + qft + random
```

不包含 optional QAOA。

通用入口：

```bash
python3 experiments/scripts/run_suite.py proposal ...
```

这个入口负责在一个已有环境中编排 proposal benchmark points。它不负责申请 cluster GPU，也不负责 Slurm。

### 4.1 本机 CPU smoke 示例

先 build：

```bash
./experiments/build.sh gate_micro cpu
./experiments/build.sh qft cpu
./experiments/build.sh random cpu
```

再运行 suite：

```bash
python3 experiments/scripts/run_suite.py proposal \
  --platform local \
  --backend cpu \
  --deployment off \
  --base-qubits 4 \
  --reps 1 \
  --warmup 0 \
  --sync-mode benchmark \
  --preheat-mode off \
  --gate-repeats 3 \
  --random-depth 4 \
  --random-two-qubit-ratios 0 0.5 1
```

### 4.2 `run_suite.py proposal` 常用参数

| 参数 | 含义 |
|---|---|
| `--platform local/cluster/...` | 写入 TSV 的 platform metadata |
| `--backend cpu/gpu/gpu_mpi/...` | 选择已 build 的 executable 路径 |
| `--deployment off/on` | 写入 TSV 的 deployment metadata，并用于 benchmark `--distribution` |
| `--raw-dir PATH` | raw TSV 输出目录 |
| `--base-qubits N` | suite 使用的 qubit 数 |
| `--reps N` | timed repetitions |
| `--warmup N` | warmup repetitions |
| `--sync-mode benchmark/profile` | benchmark 或 profile timing mode |
| `--preheat-mode identical/light/off` | preheat 模式 |
| `--preheat-qubits N` | light preheat qubit 数 |
| `--gate-kinds ...` | gate_micro 跑哪些 gate kind |
| `--gate-repeats N` | gate_micro 每个 point 的重复次数 |
| `--random-depth N` | random circuit depth |
| `--random-two-qubit-ratios ...` | random 跑哪些 two-qubit ratios |
| `--random-seed N` | random seed |

### 4.3 proposal suite 与 cluster GPU+MPI 入口的区别

两者都会跑 benchmark，但职责不同：

| 入口 | 负责什么 | 不负责什么 |
|---|---|---|
| `run_suite.py proposal` | 编排 proposal benchmark points，在已有环境中运行 | 不申请 Slurm/GPU，不处理 cluster module，不包装 `nsys` |
| `sbatch_cluster_gpu_mpi_proposal_suite.sh` | 申请 cluster GPU+MPI 资源，在 allocation 内 build/run/profile | 不作为通用本地 runner |

可以理解为：

- `run_suite.py proposal` 回答“proposal suite 应该跑哪些 benchmark 参数？”
- `sbatch_cluster_gpu_mpi_proposal_suite.sh` 回答“如何在 MLS cluster 上申请 4 GPU 并把这些点跑完？”

## 5. MLS cluster GPU+MPI 路径

MLS cluster 的 proposal suite 提交入口：

```bash
bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh [auto|a6000|a40|2080ti] [ranks] [validate|profile]
```

常用：

```bash
bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 validate
bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 profile
```

### 5.1 validate vs profile

| mode | 行为 |
|---|---|
| `validate` | 直接运行 compact 4-GPU suite，输出 TSV |
| `profile` | 用 Nsight Systems 包装每个 benchmark point，输出 TSV、`.nsys-rep`、`.sqlite` 和 whole-procedure breakdown |

profile mode 会在 allocated job 中尝试加载 CUDA module 暴露 `nsys`。默认尝试：

```text
cuda/13.2.1 cuda/13.1.1 cuda/12.8.0 cuda
```

可覆盖：

```bash
QUEST_GPU_MPI_NSYS_MODULES="cuda/12.8.0" \
  bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 profile
```

### 5.2 cluster suite 默认参数

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `QUEST_GPU_MPI_SUITE_BENCHMARKS` | `"gate_micro qft random"` | 选择要 build/run 的核心 benchmark 子集 |
| `QUEST_GPU_MPI_SUITE_QUBITS` | `24` | qubit 数 |
| `QUEST_GPU_MPI_SUITE_REPS` | `1` | timed reps |
| `QUEST_GPU_MPI_SUITE_WARMUP` | `0` | warmup reps |
| `QUEST_GPU_MPI_SUITE_GATE_REPEATS` | `64` | gate_micro repeats |
| `QUEST_GPU_MPI_SUITE_RANDOM_DEPTH` | `48` | random depth |
| `QUEST_GPU_MPI_SUITE_RANDOM_RATIOS` | `"0.25 0.50"` | random two-qubit ratios |
| `QUEST_GPU_MPI_PREHEAT_MODE` | `off` | preheat mode |
| `QUEST_GPU_MPI_PREHEAT_QUBITS` | `24` | preheat qubits |

示例：降低 gate repeats 做快速 profile smoke：

```bash
QUEST_GPU_MPI_SUITE_GATE_REPEATS=8 \
QUEST_GPU_MPI_SUITE_RANDOM_DEPTH=8 \
  bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 profile
```

示例：只 profile QFT q28：

```bash
QUEST_GPU_MPI_SUITE_BENCHMARKS=qft \
QUEST_GPU_MPI_SUITE_QUBITS=28 \
QUEST_GPU_MPI_SUITE_REPS=1 \
QUEST_GPU_MPI_SUITE_WARMUP=0 \
QUEST_GPU_MPI_PREHEAT_MODE=off \
QUEST_BENCH_BUILD_PARALLEL=8 \
  bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh 2080ti 2 profile
```

### 5.3 cluster 输出

validate/profile 都会生成一个 run directory：

```text
experiments/results/raw/gpu_mpi_proposal_<mode>_<gpu>_r<ranks>_<jobid>/
```

其中包含：

```text
gate_micro_cluster_gpu_mpi_on_<gpu>_r<ranks>_q<qubits>_<jobid>.tsv
qft_cluster_gpu_mpi_on_<gpu>_r<ranks>_q<qubits>_<jobid>.tsv
random_cluster_gpu_mpi_on_<gpu>_r<ranks>_q<qubits>_<jobid>.tsv
suite_manifest.txt
```

profile mode 还会生成：

```text
profiles/
  gate_micro_h.nsys-rep
  gate_micro_cnot.nsys-rep
  gate_micro_cphase.nsys-rep
  gate_micro_hn.nsys-rep
  qft.nsys-rep
  random_tqr0p25.nsys-rep
  random_tqr0p50.nsys-rep
  # 每个 report 还会导出同名 .sqlite

procedure_breakdown_rank.tsv
procedure_breakdown.tsv
communication_breakdown_rank.tsv
computation_breakdown_rank.tsv
lifecycle_breakdown_rank.tsv
cuda_runtime_summary_rank.tsv
```

`procedure_breakdown_rank.tsv` 保留每个 MPI rank 的结果。`procedure_breakdown.tsv` 选择 procedure wall time 最大的 critical rank 作为主时间行；`critical_rank_mpi_*` 只描述该 rank，`aggregate_mpi_*` 才是所有 rank 的合计。主表还记录 rank wall time 的最小值、最大值和不均衡程度。

whole-procedure 分类固定为：

```text
Procedure = Communication + Computation + Lifecycle + Execution Overhead
Others = Lifecycle + Execution Overhead
```

- `Procedure`：从 QuEST environment 初始化前到 `finalizeQuESTEnv()` 返回后的 executable 生命周期。
- `Execution`：正式 timed workload 的 wall-time 边界；它包含 Communication、Computation 和 Execution Overhead。
- `Communication`：amplitude pack 与 GPU/CPU staging、MPI point-to-point exchange、回传 GPU 的区间并集。
- `Computation`：正式 timed workload 内的 CUDA simulation kernels，先扣除与 Communication 重叠的部分。
- `Lifecycle`：environment init/finalize、qureg create/destroy、state init 和 validation marker 的区间并集；旧 profile 没有这些 marker 时退化为 Procedure 中 Execution 之外的残差。
- `Execution Overhead`：Execution 中既不是 Communication，也不是 CUDA simulation kernel 的残差，例如 host-side dispatch、同步和未标记控制流。
- `overlap_time_s`：原始 computation candidate 与 Communication 的重叠；分类时 Communication 优先，因此不会重复计入 Procedure。

四张诊断表用于解释主分类，不参与时间相加：

- `communication_breakdown_rank.tsv`：按 exchange 列出 pack、D2H、MPI、MPI wait、H2D、peer 和 byte volume。
- `computation_breakdown_rank.tsv`：把 CUDA simulation kernels 概括为 `phase`、`hadamard`、`swap` 和 `other`。
- `lifecycle_breakdown_rank.tsv`：逐 rank 汇总六个 lifecycle 阶段。
- `cuda_runtime_summary_rank.tsv`：只汇总 malloc/free、memcpy、同步和 kernel launch API 的 calls、total、median、p95；这些 CPU API 时间不能与 GPU active time直接相加。

### 5.4 GPU+MPI 注意事项

- `gpu_mpi` 使用 `mpirun -np <ranks>` 运行。
- rank 到 GPU 的绑定交给 QuEST/MPI runtime；脚本不手动设置 `CUDA_VISIBLE_DEVICES`。
- GPU binary 在 Slurm allocation 内 build，避免 A6000/A40/2080 Ti 之间复用错误 CUDA architecture 的旧 binary。
- 如果 cluster 当前不能访问 GitHub，可用本机 bundle/rsync 方式同步代码。

### 5.5 单节点 GPU strong/weak scaling

资源选择 dry-run：

```bash
bash experiments/scripts/sbatch_cluster_gpu_mpi_scaling.sh --dry-run
```

提交完整 campaign：

```bash
bash experiments/scripts/sbatch_cluster_gpu_mpi_scaling.sh
```

脚本只申请一次 4-GPU allocation，并在同一节点内顺序运行 `1/2/4` ranks。选择顺序为：五分钟内可启动的 Teaching A6000、Interactive 2080 Ti、Teaching 2080 Ti。已有运行中的 GPU allocation 时拒绝提交，避免超过用户级 4-GPU QoS 限额。

strong scaling 固定 `q=28`；weak scaling 使用 `(ranks,q)=(1,26),(2,27),(4,28)`，保持每个 GPU `2^26` amplitudes。workload 为 H、CNOT、CPhase、QFT 和 depth-8/random ratio-0.5；每点执行 1 次 warmup 和 5 次正式测量。

作业结束后执行：

```bash
bash experiments/scripts/collect_cluster_gpu_mpi_scaling.sh <jobid>
```

结果目录：

```text
experiments/results/raw/gpu_mpi_scaling_<gpu>_<jobid>/
```

主要聚合文件：

| 文件 | 含义 |
|---|---|
| `scaling_samples.tsv` | 所有 warmup/measured samples；shared endpoint 同时带 strong/weak membership |
| `scaling_summary.tsv` | median/mean/std/p95/CV、strong speedup/efficiency、weak efficiency/slowdown |
| `scaling_profile_summary.tsv` | scaling metadata 与 whole-procedure profile 的关联结果 |
| `scaling_highlights.md` | negative scaling、低效率、weak slowdown、通信增长、rank imbalance 和高 CV 提示 |
| `point_manifest.tsv` | 25 个物理 timing points 及 11 个 profile points 的完整定义 |
| `gpu_samples.tsv` | 每个 point 前后的 GPU 温度、功耗、利用率和显存快照 |
| `SHA256SUMS` | campaign 全部文件的校验和 |

QFT 和 random 的 weak scaling 保持 local state size，但算法 gate count 会随 qubit 数增长。因此解释时应同时看 wall time 和 `median_time_per_gate_s`，不能把全部增长都归因于 communication。

## 6. QFT GPU+MPI smoke 路径

如果只想测试 QFT 的 GPU+MPI 路径，而不是完整 proposal suite：

```bash
bash experiments/scripts/sbatch_cluster_gpu_mpi.sh auto 2
```

该路径只 build/run `qft/gpu_mpi`。它适合确认：

- `gpu_mpi` build 是否成功
- `--distribution on` 是否可运行
- root rank 是否能写 TSV

它不是完整 benchmark suite。

## 7. 结果解析

raw TSV 默认写入：

```text
experiments/results/raw/
```

聚合为 processed matrices：

```bash
python3 experiments/scripts/parse_results.py
```

常见输出：

```text
experiments/results/processed/capacity_matrix.tsv
experiments/results/processed/perf_matrix.tsv
experiments/results/processed/qft_stage_matrix.tsv
experiments/results/processed/gate_micro_matrix.tsv
experiments/results/processed/random_ratio_matrix.tsv
```

如果使用临时 raw/processed 目录，可以用环境变量：

```bash
RAW_RESULTS_DIR_OVERRIDE=/tmp/quest_raw \
PROCESSED_RESULTS_DIR_OVERRIDE=/tmp/quest_processed \
  python3 experiments/scripts/parse_results.py
```

## 8. 如何看 TSV

所有 benchmark 都包含公共 metadata，例如：

```text
platform
backend
deployment
benchmark
label
num_qubits
rep
warmup
status
sync_mode
total_prob
env_num_nodes
env_num_threads
preheat_mode
preheat_qubits
```

关键检查：

- `status=PASS`
- `total_prob` 接近 `1.0`
- `backend` 和 `deployment` 是否符合本次实验预期
- GPU+MPI 时 `env_num_nodes` 应等于 ranks/GPU 数

## 9. 如何看 `.nsys-rep`

profile mode 生成的 `.nsys-rep` 是 Nsight Systems 二进制 trace 文件。

GUI 打开：

```bash
nsys-ui experiments/results/raw/<run>/profiles/qft.nsys-rep
```

profile job 已自动导出同名 SQLite。CLI 摘要仍可按需生成：

```bash
nsys stats \
  --report cuda_gpu_kern_sum,cuda_api_sum,osrt_sum \
  experiments/results/raw/<run>/profiles/qft.nsys-rep
```

常看的 summary：

| report | 用途 |
|---|---|
| `cuda_gpu_kern_sum` | GPU kernel 耗时分布 |
| `cuda_api_sum` | CPU 侧 CUDA API 调用成本，例如 `cudaMemcpy/cudaMalloc/cudaFree/cudaLaunchKernel` |
| `osrt_sum` | OS/runtime 等待、poll、thread wait 等 |

suite 的自动 profile 命令使用：

```bash
nsys profile --trace=cuda,mpi,nvtx,osrt --mpi-impl=openmpi ...
```

因此 `.sqlite` 同时包含 CUDA、NVTX 和 MPI event。`profile_breakdown.py` 使用 NVTX procedure/execution/communication ranges、CUDA kernel intervals 和 MPI P2P events 生成三分类结果。

`Num Calls` 表示某个 API 或 runtime 函数在 profile 期间被调用的次数。例如 `cudaMalloc Num Calls=1256` 表示捕获到 1256 次 `cudaMalloc` 调用。

## 10. 推荐使用流程

第一次改 benchmark 或脚本后，建议按这个顺序验证：

1. 本地语法检查：

   ```bash
   bash -n experiments/build.sh
   bash -n experiments/scripts/common.sh
   bash -n experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh
   bash -n experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite_point.sh
   python3 -m py_compile experiments/scripts/run_suite.py experiments/scripts/parse_results.py
   git diff --check
   ```

2. 本地 CPU build：

   ```bash
   ./experiments/build.sh gate_micro cpu
   ./experiments/build.sh qft cpu
   ./experiments/build.sh random cpu
   ```

3. 本地 CPU proposal smoke：

   ```bash
   python3 experiments/scripts/run_suite.py proposal \
     --platform local \
     --backend cpu \
     --deployment off \
     --base-qubits 4 \
     --reps 1 \
     --warmup 0 \
     --sync-mode benchmark \
     --preheat-mode off \
     --gate-repeats 3 \
     --random-depth 4 \
     --random-two-qubit-ratios 0 0.5 1
   ```

4. 解析结果：

   ```bash
   python3 experiments/scripts/parse_results.py
   ```

5. cluster validate：

   ```bash
   bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 validate
   ```

6. validate 通过后再跑 cluster profile：

   ```bash
   bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 profile
   ```

## 11. 常见问题

### 为什么路径里会出现两次 benchmark 名字？

例如：

```text
experiments/build/qft/cpu/qft
```

含义是：

- `build/qft/cpu/`：`qft` benchmark 的 `cpu` build tree
- 最后的 `qft`：build 出来的 executable 名

所以 `qft` 出现两次是正常的。

### 为什么有 Python runner 又有 bash launcher？

- Python `run_suite.py` 更适合表达 benchmark matrix、参数组合和 TSV failure rows。
- Bash launcher 更适合处理 Slurm、module、GPU node 选择、`mpirun`、`nsys` 和 cluster 环境。

cluster GPU+MPI proposal suite 目前在 bash payload 中显式列出同一组 benchmark points，是为了精确控制每个 point 对应一个 `.nsys-rep`。

### `sync-mode benchmark` 和 `sync-mode profile` 有什么区别？

- `benchmark`：普通计时，用于稳定 TSV runtime。
- `profile`：用于 profiler run，通常外层会用 `nsys profile` 包装。

### `two-qubit-ratio` 是什么？

`random` benchmark 中，每层 gate slots 中 two-qubit gates 的比例。它用于控制 entangling gate pressure。TSV 同时记录：

```text
two_qubit_ratio
actual_two_qubit_ratio
single_qubit_gate_count
two_qubit_gate_count
```

### benchmark 结果可以直接跨 family 比 `time_per_gate` 吗？

不建议。`time_per_gate` 只适合同一 benchmark family 内部辅助比较。`gate_micro`、`qft` 和 `random` 的线路结构不同，跨 family 直接比较 per-gate cost 容易误导。

## 12. Required follow-up experiments

这组入口用于复现和补强已有 scaling 结论，不替代普通 proposal suite：

```bash
bash experiments/scripts/sbatch_cluster_gpu_mpi_required_experiments.sh --dry-run all
bash experiments/scripts/sbatch_cluster_gpu_mpi_required_experiments.sh all
```

`all` 会顺序提交五个 4-GPU allocation：三次 QFT q28 独立复现、一次
QFT q24-q29 sweep、一次 H/CPhase matched timing/profile。所有 timing point
均使用 `warmup=1` 和 `reps=10`；GPU 型号固定为 RTX 2080 Ti，避免把硬件差异
混入 scaling curve。

最后一个任务完成后，在 cluster repo root 执行：

```bash
bash experiments/scripts/collect_cluster_gpu_mpi_required_experiments.sh \
  experiments/results/raw/required_experiments_2080ti_<timestamp>
```

collector 会拒绝不完整 campaign，并验证三次复现至少覆盖两台节点、18 个
QFT sweep points、4 个 gate timing/profile points、每点采样数及所有 Nsight
产物。分析结论写入 `required_experiment_answers.md`，原始数据和环境信息保留在
同一 campaign 目录。
