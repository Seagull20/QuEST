# Scripts

本目录放 benchmark suite 的执行、提交和结果处理脚本。

## 主要脚本

- `common.sh`
  - 共享路径、结果目录、toolchain 环境加载和批量 build 函数。
- `run_suite.py`
  - 统一的 sweep runner。
  - `one-node` 子命令执行 `probe + qft + h_sweep + random`。
  - `proposal` 子命令执行 proposal 非 optional suite：`gate_micro + qft + random`。
  - `mpi-qft` 子命令执行 `ARCHER2 + cpu_mpi + QFT` 的超单节点扩展。
- `parse_results.py`
  - 将 `experiments/results/raw/*.tsv` 聚合为：
    - `capacity_matrix.tsv`
    - `perf_matrix.tsv`
    - `degradation_matrix.tsv`
    - `qft_stage_matrix.tsv`
    - `h_target_matrix.tsv`
    - `mpi_extension_matrix.tsv`
    - `thread_perf_matrix.tsv`
    - `thread_speedup_matrix.tsv`
    - `thread_qft_stage_matrix.tsv`
    - `gate_micro_matrix.tsv`
    - `random_ratio_matrix.tsv`

## 集群提交脚本

- `sbatch_cluster_one_node.sh`
  - cluster 单节点 GPU 路径。
  - 支持 `gpu` 和 `cuquantum`。
- `sbatch_archer2_one_node.sh`
  - ARCHER2 单节点 CPU 路径。
  - 使用 `cpu_mpi` build，但运行时 `--distribution off`。
  - 保留为串行 fallback，不再是 ARCHER2 的首选路径。
- `sbatch_archer2_build_cpu_mpi.sh`
  - ARCHER2 CPU-only build job。
  - 单独构建 `probe / qft / h_sweep / random`，避免每个 benchmark job 重复编译。
- `sbatch_archer2_probe_cpu_mpi.sh`
  - ARCHER2 one-node probe job。
  - 默认使用 `short` QoS。
- `sbatch_archer2_point.sh`
  - ARCHER2 的单 benchmark / 单 qubit point job。
  - 通过 `BENCHMARK` 和 `SLURM_ARRAY_TASK_ID` 运行 `qft / h_sweep / random` 的 job array。
- `submit_archer2_parallel.sh`
  - ARCHER2 登录节点上的并行提交流程。
  - 自动执行：
    - build
    - probe
    - 根据 `max_qubits` 提交 `QFT` job array
    - 根据 sample points 提交 `H sweep` 和 `Random` job array
- `sbatch_archer2_thread_point.sh`
  - ARCHER2 的线程 sweep point job。
  - 通过 manifest + `BENCH_THREADS` 运行固定 `(benchmark, qubit, threads)`。
- `submit_archer2_thread_sweep.sh`
  - ARCHER2 登录节点上的 thread sweep 提交流程。
  - 固定提交：
    - `QFT q=26,29,31,33 @ t=32,64,128`
    - `H sweep q=26,33 @ t=32,64,128`
    - `Random q=26,29,33 @ t=32,64,128`
- `sbatch_archer2_qft_mpi.sh`
  - ARCHER2 的 `QFT` MPI 扩展路径。
  - 固定尝试 `2 -> 4 -> 8` 节点。
- `sbatch_cluster_gpu_mpi.sh`
  - MLS cluster 的 `gpu_mpi + QFT` smoke 提交入口。
  - 用法：`bash experiments/scripts/sbatch_cluster_gpu_mpi.sh [auto|a40|2080ti] [ranks]`。
  - 默认 `auto 2`，运行时使用 `--distribution on`，并由 QuEST 负责 rank 到 GPU 的绑定。
- `sbatch_cluster_gpu_mpi_qft_point.sh`
  - 上述入口提交的实际 Slurm payload。
  - 作业内构建 `qft/gpu_mpi`，再运行一个 QFT smoke 点。
- `sbatch_cluster_gpu_mpi_proposal_suite.sh`
  - MLS cluster 的 4-GPU proposal suite 提交入口。
  - 用法：`bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh [auto|a6000|a40|2080ti] 4 [validate|profile]`。
  - `validate` 直接运行 suite；`profile` 使用 Nsight Systems 包装每个 suite point。
- `sbatch_cluster_gpu_mpi_proposal_suite_point.sh`
  - 上述 proposal suite 的实际 Slurm payload。
  - 作业内构建 `gate_micro / qft / random` 的 `gpu_mpi` binary，避免复用错误 CUDA architecture 的旧构建。

## Profiler 包装

- `profile_nsys.sh`
  - 以 `--sync-mode profile` 运行 benchmark，并用 `nsys` 包装。
- `profile_ncu.sh`
  - 以 `--sync-mode profile` 运行 benchmark，并用 `ncu` 包装。

## 用法示例

```bash
python3 experiments/scripts/run_suite.py one-node \
  --platform local \
  --backend cpu \
  --deployment off \
  --base-qubits 4 \
  --search-max 4 \
  --reps 1 \
  --warmup 0
```

```bash
python3 experiments/scripts/run_suite.py proposal \
  --platform local \
  --backend cpu \
  --deployment off \
  --base-qubits 4 \
  --reps 1 \
  --warmup 0 \
  --preheat-mode off \
  --gate-repeats 3 \
  --random-depth 4 \
  --random-two-qubit-ratios 0 0.5 1
```

```bash
python3 experiments/scripts/parse_results.py
```

```bash
bash experiments/scripts/submit_archer2_parallel.sh
```

```bash
bash experiments/scripts/submit_archer2_thread_sweep.sh
```

```bash
QUEST_GPU_MPI_QUBITS=24 QUEST_GPU_MPI_REPS=1 QUEST_GPU_MPI_WARMUP=0 \
  bash experiments/scripts/sbatch_cluster_gpu_mpi.sh auto 2
```

```bash
bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 validate
bash experiments/scripts/sbatch_cluster_gpu_mpi_proposal_suite.sh auto 4 profile
```

## Cluster GPU+MPI QFT Smoke

- 当前只实现 QFT smoke，不代表完整 GPU+MPI benchmark suite 已经全部打通。
- 默认参数：
  - `QUEST_GPU_MPI_QUBITS=24`
  - `QUEST_GPU_MPI_REPS=1`
  - `QUEST_GPU_MPI_WARMUP=0`
  - `QUEST_GPU_MPI_SYNC_MODE=benchmark`
  - `QUEST_GPU_MPI_PREHEAT_MODE=off`
  - `QUEST_GPU_MPI_PREHEAT_QUBITS=24`
- `auto` 会读取 Teaching partition 的 `sinfo`，比较 A40 与 2080 Ti 的可用状态；状态相同时优先 A40。
- 支持的 rank 范围：
  - A40：`1..4`
  - 2080 Ti：`1..8`
- 输出 TSV 路径形如：
  - `experiments/results/raw/qft_cluster_gpu_mpi_on_<gpu>_r<ranks>_q<qubits>_<jobid>.tsv`
- 预期 TSV 关键列：
  - `backend=gpu_mpi`
  - `deployment=on`
  - `benchmark=qft`
  - `status=PASS`
  - `env_num_nodes=<ranks>`
- QFT benchmark 只让 root rank 写 TSV；非 root rank 不写结果文件。

## Cluster GPU+MPI Proposal Suite

- 当前 proposal suite 明确排除 optional QAOA，只覆盖：
  - `gate_micro`: `h / cnot / cphase / hn`
  - `qft`: QuEST `applyFullQuantumFourierTransform()`
  - `random`: fixed-depth random circuit with two-qubit ratios
- 默认参数：
  - `QUEST_GPU_MPI_SUITE_QUBITS=24`
  - `QUEST_GPU_MPI_SUITE_REPS=1`
  - `QUEST_GPU_MPI_SUITE_WARMUP=0`
  - `QUEST_GPU_MPI_SUITE_GATE_REPEATS=64`
  - `QUEST_GPU_MPI_SUITE_RANDOM_DEPTH=48`
  - `QUEST_GPU_MPI_SUITE_RANDOM_RATIOS="0.25 0.50"`
  - `QUEST_GPU_MPI_PREHEAT_MODE=off`
- `auto` 会读取 Teaching partition 的 `sinfo`，优先按节点状态选择可用 GPU；状态相同时按 `A6000 -> A40 -> 2080 Ti` 排序。
- `profile` 模式要求 allocated job 环境中存在 `nsys`；若不可用，会在运行 benchmark 前失败。
- 输出写入：
  - `experiments/results/raw/gpu_mpi_proposal_<mode>_<gpu>_r<ranks>_<jobid>/`
  - profile 报告写入该目录下的 `profiles/`。

## ARCHER2 QoS 选择

- `build`
  - `partition=standard`
  - `qos=standard`
  - 原因：四个 benchmark 的 `cpu_mpi` build 总时长已经逼近 `short` 的 `20 min` 上限。
- `probe`
  - `partition=standard`
  - `qos=short`
  - 原因：probe 典型运行时间远低于 `20 min`，适合尽快出 `max_qubits`。
- `QFT`
  - `partition=standard`
  - `qos=standard`
  - 原因：高 qubit 点在 ARCHER2 CPU 上明显超过 `short` QoS 的时长上限。
- `H sweep`
  - `partition=standard`
  - `qos=short`
  - 原因：单点运行时间很短，适合用短作业快速并行完成。
- `Random`
  - `partition=standard`
  - `qos=standard`
  - 原因：高 qubit sample point 很可能长于 `20 min`，不适合继续塞进 `short`。

## ARCHER2 Thread Sweep

- 线程序列固定为 `32, 64, 128`
- 提交时统一使用：
  - `--preheat-mode light`
  - `--preheat-qubits 24`
  - `--warmup 0`
- 因为 `--cpus-per-task` 不能在同一个 Slurm array 内随 task 改变，线程 sweep 采用“每个线程数一组 array”的提交方式，而不是把 `32/64/128` 混在同一个 array 里。
- `thread_point` 在 batch shell 中直接执行 benchmark，不再额外嵌套 `srun`。
  - 原因：对照 smoke 显示，`srun --hint=nomultithread --cpu-bind=cores` 会把 `QFT q=26 t=32` 从约 `6.4s` 拉高到约 `71s`，明显偏离已有 one-node baseline。
- `short` QoS 任务分两批：
  - 先跑 `H sweep`
  - 再跑 `Random q=26,29`
  - 这样能把并发控制在 `short` 的限额内。

## 说明

- `gpu_mpi` 当前已具备 MLS cluster 的 QFT smoke 路径和 proposal suite validation/profile 路径；更大规模 sweep 仍未展开。
- 远端路径均以 repo root 为基准，不硬编码 clone 绝对路径。
- 并行提交流程默认把一次 run 的 raw TSV 写到 `results/raw/<run_tag>/`，避免和旧的 ARCHER2 结果互相污染。
