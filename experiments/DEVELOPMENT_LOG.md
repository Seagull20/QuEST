## 2026-04-02 20:35 - benchmark 骨架迁移与公共接口定稿

- 模块：probe / qft / h_sweep / random / build
- 目标：把 `quest_smoke` 中分散的测试逻辑迁入 `experiments/`，并统一 CLI、输出列和 backend 接口。
- 已完成：
  - 新增 `probe / qft / h_sweep / random` 四个 benchmark 的 `main.c`。
  - 定义统一 CLI：`--qubits --reps --warmup --distribution --sync-mode --output --label`，并补上 `probe`、`h_sweep`、`random` 的专属参数。
  - 扩展 `experiments/build.sh`，支持 `probe`、`cpu_mpi` 和 `gpu_mpi` 占位接口。
  - 在 `.gitignore` 中忽略 `experiments/build/` 和结果目录生成物。
- 关键决定：
  - 本轮只保留一套 `experiments/` 入口，不再继续使用 `quest_smoke` 作为执行路径。
  - raw TSV 必须保留统一公共列，便于后续 parser 和 profiler 扩展。
  - `gpu_mpi` 本轮只保留 build/runtime/submit 占位，不尝试打通。
- 涉及文件：
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/build.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/benchmarks/probe/main.c`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/benchmarks/qft/main.c`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/benchmarks/h_sweep/main.c`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/benchmarks/random/main.c`
- 验证结果：
  - `./experiments/build.sh list` 能列出 `probe / qft / h_sweep / random`。
  - `./experiments/build.sh qft gpu_mpi` 会按预期直接报 `TODO`。
- 未完成 / TODO：
  - 共享实现当时仍采用多编译单元，后续暴露出 QuEST 头文件重复符号问题。
  - 远端 submit 脚本、suite runner、parser 尚未补齐。
- 下一步：
  - 解决链接问题，确保四个 benchmark 至少在本地 `cpu` 路径可编译可运行。
- 接手提示：
  - 如果后续需要扩展新 benchmark，优先复用 `bench.h` 的 CLI 和输出列约定，不要先加新的单独参数体系。

## 2026-04-02 20:47 - 修复 QuEST 多编译单元链接冲突并打通本地 cpu 构建

- 模块：build / common / probe / qft / h_sweep / random
- 目标：解决 `quest.h` 引发的 duplicate symbol 链接失败，恢复最短路径构建。
- 已完成：
  - 将公共实现改为 header-only：`bench.h` 与 `bench_rng.h` 内部提供 `static` 实现。
  - 删除 `experiments/benchmarks/common/bench.c` 与 `bench_rng.c`。
  - 把 `experiments/build.sh` 改回每个 benchmark 只编译一个 `main.c`。
  - 本地成功构建 `probe / qft / h_sweep / random` 的 `cpu` 可执行文件。
- 关键决定：
  - 不再坚持“一个 benchmark 多个 `.c` 文件”这条路，因为 QuEST 头文件在多翻译单元下会触发重复外部符号。
  - 当前阶段优先稳定构建，而不是继续重构 CMake 或为共享模块引入额外复杂度。
- 涉及文件：
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/build.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/benchmarks/common/bench.h`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/benchmarks/common/bench_rng.h`
- 验证结果：
  - `./experiments/build.sh probe cpu`
  - `./experiments/build.sh qft cpu`
  - `./experiments/build.sh h_sweep cpu`
  - `./experiments/build.sh random cpu`
  - 上述命令均成功完成；构建时出现的 `-Ofast` deprecation warning 来自上游 QuEST，不是 benchmark 代码新增问题。
- 未完成 / TODO：
  - 尚未补远端 sweep runner 和结果 parser。
  - `cpu_mpi / gpu / cuquantum` 仍需在远端环境验证。
- 下一步：
  - 做一轮最小本地运行验证，然后补统一 runner、SLURM 包装和 parser。
- 接手提示：
  - 如果将来又想恢复多文件结构，先确认 QuEST 的公共头文件是否改成了只声明不定义，否则会再次撞上 duplicate symbols。

## 2026-04-02 20:55 - suite runner、结果 parser 与远端脚本骨架完成

- 模块：scripts / parser / remote / build
- 目标：把 one-node sweep、ARCHER2 MPI 扩展和结果聚合都落成可执行脚本，同时给 future profiling 留接口。
- 已完成：
  - 新增 `experiments/scripts/run_suite.py`：
    - `one-node` 子命令执行 `probe + qft + h_sweep + random`
    - `mpi-qft` 子命令执行 `ARCHER2 + cpu_mpi + QFT` 的 `2 -> 4 -> 8` 节点扩展
    - 在 benchmark 失败时追加 synthetic failure row，保证 raw TSV 里能看到 fail-fast 截断点
  - 新增 `experiments/scripts/parse_results.py`，产出：
    - `capacity_matrix.tsv`
    - `perf_matrix.tsv`
    - `degradation_matrix.tsv`
    - `qft_stage_matrix.tsv`
    - `h_target_matrix.tsv`
    - `mpi_extension_matrix.tsv`
  - 新增远端脚本：
    - `sbatch_cluster_one_node.sh`
    - `sbatch_archer2_one_node.sh`
    - `sbatch_archer2_qft_mpi.sh`
    - `sbatch_cluster_gpu_mpi.sh`（占位 TODO）
  - 新增 profiler 包装：
    - `profile_nsys.sh`
    - `profile_ncu.sh`
  - 重写 `experiments/scripts/README.md` 说明脚本职责和示例用法。
- 关键决定：
  - 统一 sweep 逻辑放在 `run_suite.py`，SLURM 脚本只负责资源申请和环境准备，不复制 benchmark 逻辑。
  - parser 只依赖 Python 标准库，并回避 Python 3.10+ 语法，降低 ARCHER2/cluster 环境兼容风险。
  - `QFT` 的 MPI 失败记录采用 synthetic row；成功记录仍由 benchmark 本体输出真实 per-stage / total row。
- 涉及文件：
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/common.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/run_suite.py`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/parse_results.py`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_cluster_one_node.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_archer2_one_node.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_archer2_qft_mpi.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_cluster_gpu_mpi.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/profile_nsys.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/profile_ncu.sh`
- 验证结果：
  - `bash -n experiments/scripts/*.sh` 通过。
  - `python3 -m py_compile experiments/scripts/run_suite.py experiments/scripts/parse_results.py` 通过。
  - 本地 dry-run：
    ```bash
    python3 experiments/scripts/run_suite.py one-node \
      --platform local \
      --backend cpu \
      --deployment off \
      --raw-dir /tmp/quest_suite_raw \
      --base-qubits 4 \
      --search-min 1 \
      --search-max 4 \
      --reps 1 \
      --warmup 0 \
      --random-depth 4
    ```
  - 本地 parser dry-run：
    ```bash
    python3 experiments/scripts/parse_results.py \
      --raw-dir /tmp/quest_suite_raw \
      --out-dir /tmp/quest_suite_processed
    ```
  - `bash experiments/scripts/sbatch_cluster_gpu_mpi.sh` 会按预期直接报 `TODO`。
- 未完成 / TODO：
  - 尚未实际在 ARCHER2 和 cluster 上 build、pull、submit。
  - 尚未验证 `cpu_mpi / gpu / cuquantum` 在远端环境中的 toolchain、module、runtime library 是否完整。
  - 尚未创建 git 分支、提交并 push。
- 下一步：
  - 创建 `codex/benchmark-suite` 分支。
  - commit 当前改动并 push 到 GitHub。
  - 在 `cluster` 上先跑 `sbatch_cluster_one_node.sh gpu` 与 `sbatch_cluster_one_node.sh cuquantum`。
  - 在 `ARCHER2` 上跑 `sbatch_archer2_one_node.sh`，拿到 one-node max 后再跑 `sbatch_archer2_qft_mpi.sh`。
- 接手提示：
  - 本地最值得先看的入口是 `experiments/scripts/run_suite.py`，因为 one-node 与 MPI 的 fail-fast 策略都集中在这里。
  - 远端如果 batch job 启动失败，先检查 Python 是否可用、MPI toolchain 是否由 `.quest_toolchain_env.sh` 提供、以及 cuQuantum 运行库是否进入 `LD_LIBRARY_PATH`。

## 2026-04-02 20:59 - 适配 cluster 的 repo 外部 toolchain 路径

- 模块：build / scripts / remote
- 目标：让远端脚本在 `.quest_toolchain_env.sh` 不在 repo 根目录时仍能自动找到 toolchain 环境。
- 已完成：
  - `experiments/build.sh` 增加向上回溯查找 `.quest_toolchain_env.sh` 的逻辑。
  - `experiments/scripts/common.sh` 同步增加相同的 fallback 搜索顺序。
- 关键决定：
  - toolchain env 搜索顺序固定为：
    - repo 根目录
    - repo 父目录
    - repo 爷目录
  - 这样可以兼容 cluster 上现有的 `~/quest_project/.quest_toolchain_env.sh`。
- 涉及文件：
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/build.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/common.sh`
- 验证结果：
  - `bash -n experiments/build.sh experiments/scripts/common.sh` 通过。
  - `./experiments/build.sh list` 仍能正常输出 benchmark 列表。
- 未完成 / TODO：
  - 还未在 cluster 上实际提交 GPU 作业验证 `nvcc`、CUDA 运行库和 cuQuantum 路径。
  - ARCHER2 SSH 仍未打通。
- 下一步：
  - 重新 push 本次补丁。
  - 在 cluster 上重新 fetch 分支并尝试 `sbatch_cluster_one_node.sh gpu`。
- 接手提示：
  - 如果将来 toolchain env 又挪位置，优先改这两个脚本的搜索路径，不要把绝对路径重新写死到 submit 脚本里。
