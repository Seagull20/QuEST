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

## 2026-04-02 21:00 - 修复 SLURM spool 目录下的 common.sh 相对路径失效

- 模块：scripts / remote
- 目标：让 `sbatch` 提交脚本在被 SLURM 复制到 spool 目录执行时，仍能正确找到 repo 内的 `common.sh`。
- 已完成：
  - `sbatch_cluster_one_node.sh`
  - `sbatch_archer2_one_node.sh`
  - `sbatch_archer2_qft_mpi.sh`
  - 上述脚本均改为优先使用 `SLURM_SUBMIT_DIR` 作为 repo root，再从 `${REPO_ROOT}/experiments/scripts/common.sh` 引入共享逻辑。
- 关键决定：
  - 对于 batch 脚本，不再依赖 `BASH_SOURCE` 的目录，因为 SLURM 会把脚本复制到 `/var/spool/slurmd/job*/` 执行。
  - 默认假设 `sbatch` 从 repo root 提交；若不是，则至少要保证 `SLURM_SUBMIT_DIR` 指向 repo root。
- 涉及文件：
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_cluster_one_node.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_archer2_one_node.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_archer2_qft_mpi.sh`
- 验证结果：
  - `bash -n experiments/scripts/sbatch_cluster_one_node.sh experiments/scripts/sbatch_archer2_one_node.sh experiments/scripts/sbatch_archer2_qft_mpi.sh` 通过。
  - cluster 上第一次提交的失败原因已明确为 `common.sh` 路径丢失，本次修复正对该问题。
- 未完成 / TODO：
  - 还没用修复后的脚本重新提交 cluster 作业。
  - ARCHER2 侧还没验证同类问题是否也会出现。
- 下一步：
  - push 本次修复。
  - 在 cluster 上 fast-forward 后重新提交 `gpu` 与 `cuquantum` one-node 作业。
- 接手提示：
  - 以后任何 `sbatch` 脚本如果还要 source repo 内文件，都应默认走 `SLURM_SUBMIT_DIR`，不要再写 `$(dirname "${BASH_SOURCE[0]}")` 这种本地脚本路径假设。

## 2026-04-02 21:02 - 补回 cluster 上的 cuQuantum 自动探测与运行库路径

- 模块：build / scripts / remote
- 目标：修复 cluster 上 `cuquantum` 作业因 `CUQUANTUM_ROOT` 未设置而在 CMake 阶段失败的问题。
- 已完成：
  - `experiments/build.sh` 新增 `detect_cuquantum_root()`：
    - 优先检查 `~/miniconda3/envs/quest_env`
    - 再检查 `/usr/local`
    - 再检查 `~/.local/lib/python*/site-packages/cuquantum`
  - `experiments/scripts/sbatch_cluster_one_node.sh` 在 `BACKEND=cuquantum` 时自动导出：
    - `CUQUANTUM_ROOT`
    - `LD_LIBRARY_PATH=${CUQUANTUM_ROOT}/lib:...`
- 关键决定：
  - 不把 cluster 上的 Python 版本号写死到脚本里，而是用 `python*` 通配符兼容用户 site-packages 目录。
  - cuQuantum 的 include/library 探测放在 `build.sh`，运行库补丁放在 submit 脚本，避免本地 CPU/GPU 路径被无关环境变量污染。
- 涉及文件：
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/build.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_cluster_one_node.sh`
- 验证结果：
  - `bash -n experiments/build.sh experiments/scripts/sbatch_cluster_one_node.sh` 通过。
  - cluster 上已确认真实安装路径为：
    - `~/.local/lib/python3.12/site-packages/cuquantum/include/custatevec.h`
    - `~/.local/lib/python3.12/site-packages/cuquantum/lib/libcustatevec.so`
- 未完成 / TODO：
  - 还没用修复后的脚本重新提交 `cuquantum` 作业。
  - 还需要等待 `gpu` 作业完成并检查 raw TSV 是否真正生成。
- 下一步：
  - push 本次修复。
  - cluster fast-forward 后只重提 `sbatch_cluster_one_node.sh cuquantum`。
  - 作业完成后运行 `parse_results.py` 做一次远端矩阵生成。
- 接手提示：
  - 如果未来 cuQuantum 安装位置再变，优先改 `detect_cuquantum_root()`；submit 脚本只负责把已经找到的路径补进 `LD_LIBRARY_PATH`。

## 2026-04-02 21:34 - 补齐 ARCHER2 batch 环境的 CMake 自动加载

- 模块：scripts / remote / build
- 目标：避免 `ARCHER2` 的 batch job 默认落到 `/usr/bin/cmake 3.20.4`，从而在 one-node 构建阶段直接卡在 QuEST 的 `cmake_minimum_required(VERSION 3.21)`。
- 已完成：
  - 在 `experiments/scripts/common.sh` 新增 `ensure_minimum_cmake()` 与版本比较辅助函数。
  - `sbatch_archer2_one_node.sh` 在构建前自动调用 `ensure_minimum_cmake 3.21`。
  - `sbatch_archer2_qft_mpi.sh` 也同步接入相同逻辑，避免后续恢复 MPI 时再次手工修补。
- 关键决定：
  - 不把 `cmake/3.29.4` 硬编码到所有平台脚本，只在 ARCHER2 路径显式调用最低版本检查。
  - 如果 batch shell 里没有 `module` 函数，先尝试 source `/etc/profile`，再加载 `cmake/3.29.4`。
  - 本轮 MPI 提交继续暂停，但脚本层的环境兼容性一并补齐，避免下轮再返工。
- 涉及文件：
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/common.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_archer2_one_node.sh`
  - `/Users/linzeyu/Documents/Degree_Project/03_degree_project/work/QuEST/experiments/scripts/sbatch_archer2_qft_mpi.sh`
- 验证结果：
  - `bash -n experiments/scripts/common.sh experiments/scripts/sbatch_archer2_one_node.sh experiments/scripts/sbatch_archer2_qft_mpi.sh` 通过。
  - 在 ARCHER2 交互 shell 中手动执行 `module load cmake/3.29.4 && ./experiments/build.sh probe cpu_mpi` 已成功完成。
- 未完成 / TODO：
  - 还没用修复后的 `sbatch_archer2_one_node.sh` 真正提交 one-node 作业。
  - 还没在 ARCHER2 上生成 `probe / qft / h_sweep / random` 的 raw TSV 与 processed 矩阵。
- 下一步：
  - 提交当前补丁并 push。
  - ARCHER2 fast-forward 到最新分支。
  - 提交 `sbatch_archer2_one_node.sh`，只做 one-node `probe + benchmark`。
  - MPI 暂停，转入下一步待办。
- 接手提示：
  - 如果 ARCHER2 batch job 又在 CMake 阶段失败，先看 `archer2_quest-suite-cpu_<jobid>.out/.err` 里是否出现 `Loading cmake/3.29.4`；若没有，优先检查 batch shell 是否能拿到 `module` 函数。
