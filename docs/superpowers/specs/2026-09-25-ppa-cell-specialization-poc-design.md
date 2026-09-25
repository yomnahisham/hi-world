# PoC Design: PPA-Driven Cell Specialization — Does the LLM Help?

Date: 2026-09-25 · Deadline driving scope: LAD Fellowship, 2026-09-27 AoE

## Purpose

Produce preliminary evidence for a reframed LAD proposal (topic A+B):
an LLM agent that decides *where and how* to add design-specific standard cells
to improve post-route timing/power/area, evaluated on a released benchmark.

The PoC must support two claims, each with a number that goes in the proposal:

1. **The field optimizes the wrong objective.** Area-driven library extension
   (AutoCellLibX, TeMACLE, CellE) ranks candidate cells by frequency. Frequency
   does not predict timing criticality.
2. **The LLM adds value beyond an STA script.** Choosing k cells before any
   can be measured, the LLM's picks reach a larger fraction of the
   oracle-optimal ΔWNS than frequency, STA-greedy, and STA-greedy + rules.

If claim 2 fails (LLM ties the strong heuristic), that is reported as-is; the
proposal then locates the LLM's role in moves heuristics cannot parameterize.

## Environment (verified present)

- Yosys + yosys-abc (Homebrew), OpenSTA (`~/.local/bin/sta`), ngspice
- SKY130 PDK via ciel (`sky130A`, `sky130_fd_sc_hd`: lib, spice, lef)
- `claude` CLI 2.1.282 — LLM calls via `claude -p` on the user's subscription
- `efabless/openlane` Docker image (stretch goal only)

## Designs

8–12 plain-Verilog designs, each pinned to a commit hash in `designs.txt`:
PicoRV32; secworks AES and SHA-256; EPFL arithmetic (adder, multiplier, sqrt,
sin, log2); 1–3 further open cores if they read cleanly in Yosys without sv2v.

## Tier 1 — Frequency vs. criticality

Per design:

1. `synth -top X; dfflibmap -liberty L; abc -liberty L; opt_clean; write_json; write_verilog`
   using `sky130_fd_sc_hd__tt_025C_1v80.lib`.
2. Clock period = 0.8 × the period at which the design closes with zero
   slack (found once by an STA run at a loose period: `period = 0.8 × max arrival`),
   so a meaningful set of paths is critical.
3. OpenSTA: `report_checks -group_path_count N -fields {fanout cap slew input_pins}`
   with N = 200. Parse per-pin arrival, cell delay, slack.
4. Mine clusters: connected 2–3 cell subgraphs of combinational cells where
   internal nets are fanout-free (the pattern space prior extension work mines).
   Cluster identity = canonical (cell types + internal connectivity) string.
5. Rank A (frequency): instance count. Rank B (criticality): number of
   near-critical path traversals (slack within 10% of WNS) × delay the cluster
   contributes on those paths.

Metrics per design: overlap@K (K = 5, 10) between rank A and rank B top-K;
fraction of near-critical-path delay covered by rank-A top-K.

## Tier 2 — Choose-before-you-measure selection

**Framing (fixes a flaw found in self-review).** If selectors could query the
scorer, exhaustive search over ~50 candidates would be optimal and no LLM could
beat it. In the real flow the scorer is expensive (layout + characterization +
flow re-run per cell), so the research question is *which few cells to build
before any can be measured*. Selectors therefore see only **cheap information**
(STA reports, cluster structure, Boolean function). The ngspice **oracle**
scores their picks afterwards; exhaustive search over oracle scores gives the
**upper bound**. Headline metric: fraction of oracle-optimal ΔWNS achieved.

Budget k ∈ {3, 5, 10}. Selectors (all cheap-info only):

1. **Frequency** — top-k by instance count (CellE/TeMACLE proxy).
2. **STA-greedy** — top-k by current delay the cluster contributes on
   near-critical paths.
3. **STA-greedy + rules** — as 2, after discarding candidates whose minimal
   complex-gate realization exceeds stack depth 4 (computable from the function
   without simulation). This is the hand-engineered baseline the LLM must beat.
4. **LLM** — `claude -p` receives the top-N path table, the candidate list
   (key, constituent cells, Boolean function, #inputs, instance count, critical
   delay contribution) and k. Returns JSON: chosen keys + one-line rationale
   each. Raw prompt/response and CLI version saved to `results/llm/`.
5. **Oracle** — exhaustive best k-subset on oracle scores (restricted to the 30
   candidates with highest critical contribution when C(n,k) > 200k).

**Oracle scorer** (deterministic, identical for all selectors):

- Function: truth table of the cluster from liberty `function` strings.
- Fused cell: one static-CMOS complex stage from a minimal SOP (pull-down =
  cubes of series NMOS in parallel; pull-up = dual), choosing the polarity
  (with or without output inverter) with fewer stages; negated literals get
  `inv_1` input inverters. Sizing copies the library's `_1` style: NMOS
  W=0.65µ, PMOS hvt W=1.0µ, L=0.15µ, stacks unscaled (as in `a21oi_1`).
  Stack depth > 4 in either network → infeasible, saving 0.
- Transistor order: the cluster input that is most often the path entry on
  near-critical paths goes nearest the output (fixed per cluster, same for all
  selectors — the comparison isolates selection).
- **Calibration by ratio:** in one ngspice testbench, simulate both the
  original cluster (library subckts from `sky130_fd_sc_hd.spice`) and the fused
  cell, per cluster input, with a sensitizing vector, `inv_1` input driver, and
  the cluster's median critical load; delay = max(rise, fall). The path's STA
  delay through the cluster is multiplied by fused/original for its entry
  input, so model-vs-liberty calibration errors cancel.
- Recompute top-N path slacks (overlapping chosen clusters on one path: apply
  non-overlapping, largest saving first). Report ΔWNS, ΔTNS, and area proxy
  (fused transistor count vs. original cells' transistor count).

Stated limitation: pre-layout, no parasitics, no re-placement — an estimate of
the decision's quality, not signoff PPA. The fellowship plan replaces it with
layout + full OpenLane re-runs.

## Code layout (`poc/`)

- `designs.txt` — design name, repo URL, commit, top module, file list
- `fetch.sh` — clone designs at pinned commits
- `run.sh` — Yosys synth + OpenSTA per design → `build/<design>/`
- `mine.py` — parse netlist JSON + STA reports; clusters; Tier 1 metrics
- `cellgen.py` — cluster → function → CMOS netlist → ngspice delay
- `select.py` — three selectors; LLM via `claude -p` subprocess
- `score.py` — path recompute; Tier 2 table
- `results/` — CSVs, one Tier 1 plot, one Tier 2 plot, LLM transcripts
- One `assert`-based self-check (`python3 mine.py --selftest`) on a toy netlist
  covering cluster mining and path recompute.

## Outputs for the proposal

- Table 1: per design, overlap@5/10 and critical-delay coverage by frequent patterns.
- Table 2 / plot: ΔWNS and ΔTNS vs. k for frequency, STA-greedy, LLM; area delta.
- One sentence each for claim 1 and claim 2 with the actual numbers.

## Stretch (only if Tier 1+2 done by day-2 midday)

Post-route re-scoring of Tier 1 on 2–3 designs via the OpenLane container.

## Out of scope

Layout generation, full characterization (.lib), re-running synthesis with new
cells, power analysis, tape-out, the benchmark release. These are the
fellowship-year plan, described in the proposal, not built here.

## Risks

- **Designs fail to read in Yosys** → drop them; minimum 6 designs.
- **LLM ties STA-greedy** → report honestly; reframe claim 2 as the open question.
- **Few feasible SP clusters on critical paths** (XOR-heavy datapaths) → report
  infeasible fraction; it motivates non-SP / transmission-gate moves in the plan.
- **Time** → Tier 2 on a subset of designs is acceptable; Tier 1 on all.
