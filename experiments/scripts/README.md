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

## 说明

- `gpu_mpi` 本轮只保留接口，不提供实际构建或提交能力。
- 远端路径均以 repo root 为基准，不硬编码 clone 绝对路径。
