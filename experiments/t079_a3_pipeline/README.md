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

Each unit carries one of the sink contract's three encoding states.
`GATED_OFF` means the codec was never attempted — the unit is below the
`QUEST_EXCHANGE_COMPRESSION_MIN_BYTES` gate, or the force-raw ablation is
active. `RAW_FALLBACK` means it was attempted and did not shrink the unit. Both
stage raw bytes and both are received by a plain H2D with no decode; the
distinction is provenance. The gate is applied **per unit**, before any nvcomp
call — testing it once per exchange was the round-1 defect, and it let 4 MiB
units through the codec under a 16 MiB gate while bit identity still passed.

The run covers, in order:

0. **Provenance** — commit, branch, working-tree cleanliness, the CMake cache,
   nvcc and MPI versions, the visible GPUs, both build logs and the complete
   driver transcript are written into the results directory, so the archive is
   bound to the code that produced it.
1. **Bit-identity gate** — A3 against the raw CPU-staged exchange at q24 and
   q26 on 2 and 4 ranks and at four unit sizes. Per-rank state hashes must
   match exactly, and the set of state records must be exactly the expected
   `(qubits, target, rank)` keys — equality between the two logs alone would
   pass if both sides dropped the same records. Nothing is timed until this
   passes.
2. **Per-unit gate and counter engagement** — the reported mode is
   `tiled_materialize_codec`, no payload byte crossed MPI, no exchange fell
   back, every rank announces the same exact unit size in bytes, and the codec
   was invoked on exactly the units the gate should have let through. The
   expectation is exact: units-per-exchange and gated-per-exchange are declared
   per leg and multiplied by each rank's own observed exchange count. The
   4 MiB leg sits below the gate and must encode **nothing**; the
   20,000,000-byte leg straddles it and must encode all but its short final
   unit.
3. **Per-unit raw staging** — the force-raw ablation drives every unit down the
   un-encoded branch inside the same pipeline and must stay bit-identical, with
   `sent_bytes == raw_bytes`.
4. **Mode regressions** — `win/off/tiled` and `win/on/bulk` are re-proved,
   because T-079 reordered the dispatch both of them pass through. Neither may
   prepare the fused pipeline, which is asserted by the absence of its start-up
   record, and the `win/on/bulk` leg must still show its own codec engaged.
5. **Engagement smoke** — A3 and `win/off/tiled` at q26, run back to back. Wall
   times are recorded, not asserted: this is a correctness probe inside a whole
   process lifetime, not a benchmark. Codec engagement is established by the
   counters in leg 2.

Any rank that cannot bring the fused arm up prints `A3_INIT_FALLBACK` with its
cause, and the checker rejects any log containing it — the pair would otherwise
vote the codec down and measure `win/off/tiled` under an A3 label.

**Not covered:** the `RAW_FALLBACK` state itself. Reaching it needs a unit that
is above the gate and that Bitcomp fails to shrink, and this probe's
`initDebugState` is compressible everywhere. It shares its entire staging and
receive path with `GATED_OFF`, which the force-raw and sub-gate legs do cover;
only the descriptor value differs.

Run inside an existing GPU/MPI Slurm allocation, from the repository root:

```sh
bash experiments/t079_a3_pipeline/run_verification.sh
```

Set `T079_BUILD_DIR` and `T079_RESULT_DIR` to keep build and raw logs in a
session-specific location. The producing session must still download the
results directory and checksum it before claiming any acceptance item, and no
A3 timing number may enter the dissertation before the cross-review PASS.
