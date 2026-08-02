# T-040 `tiled_materialize` verification

This driver selects `QUEST_GPU_STAGING_MODE=tiled_materialize` and reuses the
T-039 deterministic raw-vs-window comparison probe. The implementation keeps
the full `gpuCommBuffer` postcondition, while the shared window is addressed by
tile offset and the next-tile D2H / previous-tile H2D copies are issued on
independent streams.

The run covers 4, 16, and 64 MiB tiles plus a 20,000,000-byte tile whose final
tile is a partial tail. It is deliberately the compression-off/raw codec arm:
the T-040 core must first establish the window pipeline and its overlap proof;
nvCOMP descriptor integration remains a separate acceptance item until a
codec-enabled build is available.

Run inside an existing GPU/MPI Slurm allocation:

```sh
bash experiments/t040_tiled_materialize/run_verification.sh
```

Set `T040_BUILD_DIR` and `T040_RESULT_DIR` to keep build and raw logs in a
session-specific location. The driver writes no cluster-side manifest; the
producing session must download its raw logs and any `.nsys-rep` files and
checksum them before claiming the corresponding acceptance item.
