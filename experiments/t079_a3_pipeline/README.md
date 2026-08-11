# T-079 spec A3 (win/on/tiled) verification

This driver proves the fused arm: the nvCOMP codec running *inside* the tiled
shared-window staging pipeline, rather than as an alternative to it. Before
T-079 the exchange took the tiled branch ahead of the codec vote, so a run
asking for both executed tiled staging with the codec inactive.

The pipeline unit is the tile (D-032: unit = tile = chunk;
`QUEST_EXCHANGE_COMPRESSION_CHUNK_BYTES` keeps its meaning only on the bulk
paths). Per unit the encode runs on its own codec stream, the encoded bytes are
staged into this rank's window slot at the unit's raw offset, the unchanged
T-069 descriptor `{raw, stored, encoding}` is exchanged, and the peer's bytes
are copied back and decoded on a second codec stream one iteration later. Unit
*k*'s encode is launched inside iteration *k−1*, so it overlaps earlier units'
transfers and decodes but never its own — the encoded length is knowable only
after the encode kernel, and both the outbound copy length and the descriptor
depend on it.

The run covers, in order:

1. **Bit-identity gate** — A3 against the raw CPU-staged exchange at q24 and
   q26 on 2 and 4 ranks, at the default unit, at 4 and 128 MiB units, and at a
   20,000,000-byte unit whose final unit is a short tail. Per-rank state hashes
   must match exactly. Nothing is timed until this passes.
2. **Counter engagement** — the reported mode is `tiled_materialize_codec`, no
   payload byte crossed MPI, no exchange fell back, the codec counters moved,
   and every rank agrees on the effective unit the run announces.
3. **Per-unit raw fallback** — the force-raw ablation drives every unit down
   the incompressible branch inside the same pipeline and must stay
   bit-identical, with `sent_bytes == raw_bytes`.
4. **Mode regressions** — `win/off/tiled` and `win/on/bulk` are re-proved,
   because T-079 reordered the dispatch both of them pass through.
5. **Timing smoke** — A3 against `win/off/tiled` at q26 on a compressible
   state. This is whole-process wall time on a correctness probe, not a
   benchmark: it is engagement evidence that the codec executes inside the
   pipeline, nothing more.

Run inside an existing GPU/MPI Slurm allocation, from the repository root:

```sh
bash experiments/t079_a3_pipeline/run_verification.sh
```

Set `T079_BUILD_DIR` and `T079_RESULT_DIR` to keep build and raw logs in a
session-specific location. The driver writes no cluster-side manifest; the
producing session must download its raw logs and checksum them before claiming
any acceptance item, and no A3 timing number may enter the dissertation before
the cross-review PASS.
