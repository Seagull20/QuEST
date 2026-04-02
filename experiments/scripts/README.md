# Scripts

本目录放 benchmark suite 的执行、提交和结果处理脚本。

## 主要脚本

- `common.sh`
  - 共享路径、结果目录、toolchain 环境加载和批量 build 函数。
- `run_suite.py`
  - 统一的 sweep runner。
  - `one-node` 子命令执行 `probe + qft + h_sweep + random`。
  - `mpi-qft` 子命令执行 `ARCHER2 + cpu_mpi + QFT` 的超单节点扩展。
- `parse_results.py`
  - 将 `experiments/results/raw/*.tsv` 聚合为：
    - `capacity_matrix.tsv`
    - `perf_matrix.tsv`
    - `degradation_matrix.tsv`
    - `qft_stage_matrix.tsv`
    - `h_target_matrix.tsv`
    - `mpi_extension_matrix.tsv`

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
- `sbatch_archer2_qft_mpi.sh`
  - ARCHER2 的 `QFT` MPI 扩展路径。
  - 固定尝试 `2 -> 4 -> 8` 节点。
- `sbatch_cluster_gpu_mpi.sh`
  - 仅占位。
  - 本轮会直接报 `TODO`。

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
python3 experiments/scripts/parse_results.py
```

```bash
bash experiments/scripts/submit_archer2_parallel.sh
```

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

## 说明

- `gpu_mpi` 本轮只保留接口，不提供实际构建或提交能力。
- 远端路径均以 repo root 为基准，不硬编码 clone 绝对路径。
- 并行提交流程默认把一次 run 的 raw TSV 写到 `results/raw/<run_tag>/`，避免和旧的 ARCHER2 结果互相污染。
