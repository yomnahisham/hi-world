# PPA Cell-Specialization PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce two numbers for the LAD proposal — (1) frequency-ranked cell candidates miss timing-critical ones; (2) an LLM choosing k new cells *before measurement* gets closer to the oracle-optimal WNS gain than frequency and STA heuristics.

**Architecture:** Shell drives Yosys (sky130 mapping) and OpenSTA per design. Python stdlib scripts parse the netlist/paths, mine 2–3-cell fanout-free clusters, build fused complex-gate SPICE and measure it against the original cluster in one ngspice run (ratio calibration), run five selectors (frequency, STA-greedy, STA+rules, LLM via `claude -p`, oracle), and score them by recomputing top-N path slacks.

**Tech Stack:** Yosys 0.64, OpenSTA 3.1.0, ngspice + sky130A (ciel), Python 3.14 stdlib + matplotlib, `claude` CLI 2.1.282.

**Spec:** `docs/superpowers/specs/2026-09-25-ppa-cell-specialization-poc-design.md`

## Global Constraints

- PDK: `~/.ciel/ciel/sky130/versions/*/sky130A`, library `sky130_fd_sc_hd`, corner `tt_025C_1v80`, ngspice `.lib sky130.lib.spice tt`.
- Clock = 0.8 × (critical delay at a loose 100 ns clock). Near-critical ≡ slack < 0 at that clock.
- N = 200 paths (unique endpoints). Budgets k ∈ {3, 5, 10}. Cluster size 2–3 cells, ≤ 6 inputs, internal nets fanout-free.
- Fused sizing: NMOS `nfet_01v8` W=0.65µ, PMOS `pfet_01v8_hvt` W=1.0µ, L=0.15µ, stacks unscaled; stack depth > 4 → infeasible.
- Selectors see only cheap info (STA, structure, function). Oracle numbers never enter a selector prompt.
- No new Python dependencies (stdlib + matplotlib only). One self-check file `test_poc.py`, run with `python3 test_poc.py`.

## Review Focus

1. Liberty function strings with operators beyond `! & | ^ ( )` → must fail loudly, not mis-evaluate (Task 2 test).
2. STA rows for launch-flop CLK pins and required-time section → must not be parsed as data-path cells (Task 2 test).
3. Two chosen clusters overlapping on one path → savings must not double count (Task 5 test).
4. LLM returns keys not in the candidate pool or malformed JSON → dropped and logged, never crash, never silently padded (Task 6 test).
5. Fused cell slower than original (ratio > 1) → saving clipped at 0 for that path, not a negative "gain" (Task 5 test).

## File Structure

```
poc/
  designs.txt   design name, repo, commit, top ('-' = auto), comma-separated files
  fetch.sh      clone designs at pinned commits into src/
  sta.tcl       OpenSTA template (@LIB@ @NET@ @TOP@ @P@ @N@)
  run.sh        per design: yosys → sta (2 passes) → mine → cellgen → select → score
  netlist.py    liberty / yosys-json / STA-report parsers
  mine.py       clusters, per-path hits, Tier 1 metrics
  cellgen.py    truth tables, SOP realization, SPICE oracle
  select.py     five selectors incl. LLM
  score.py      path recompute, Tier 2 table, plots
  test_poc.py   assert-based self-check
  build/<d>/    generated per design
  results/      tier1.csv tier2.csv *.png llm/
```

---

### Task 1: Designs, fetch, synthesis + STA

**Files:** Create `designs.txt`, `fetch.sh`, `sta.tcl`, `run.sh` (synth/STA part), `.gitignore`

**Interfaces:**
- Produces: `build/<d>/net.json`, `build/<d>/net.v`, `build/<d>/paths.rpt`, `build/<d>/meta.json` = `{"top": str, "period": float}`.

- [ ] **Step 1: `git init` in `poc/`, write `.gitignore`**

```
src/
build/
__pycache__/
```

- [ ] **Step 2: Write `designs.txt`**

```
# name repo commit top files
picorv32 https://github.com/YosysHQ/picorv32 ef203c2b0a3fb793280f5114941416c425c5b461 picorv32 picorv32.v
aes https://github.com/secworks/aes 80dc4718e1dcbbdb4b0dd1bdb393d8f7b98981dc aes_core src/rtl/aes_core.v,src/rtl/aes_encipher_block.v,src/rtl/aes_decipher_block.v,src/rtl/aes_key_mem.v,src/rtl/aes_sbox.v,src/rtl/aes_inv_sbox.v
sha256 https://github.com/secworks/sha256 837c5cc396f001d18f2c765721c585716eb439ae sha256_core src/rtl/sha256_core.v,src/rtl/sha256_k_constants.v,src/rtl/sha256_w_mem.v
epfl_adder https://github.com/lsils/benchmarks 82d8cc6910419298e713a46644ed59fd3df53038 - arithmetic/adder.v
epfl_bar https://github.com/lsils/benchmarks 82d8cc6910419298e713a46644ed59fd3df53038 - arithmetic/bar.v
epfl_max https://github.com/lsils/benchmarks 82d8cc6910419298e713a46644ed59fd3df53038 - arithmetic/max.v
epfl_sin https://github.com/lsils/benchmarks 82d8cc6910419298e713a46644ed59fd3df53038 - arithmetic/sin.v
epfl_multiplier https://github.com/lsils/benchmarks 82d8cc6910419298e713a46644ed59fd3df53038 - arithmetic/multiplier.v
epfl_sqrt https://github.com/lsils/benchmarks 82d8cc6910419298e713a46644ed59fd3df53038 - arithmetic/sqrt.v
```

- [ ] **Step 3: Write `fetch.sh`, run it**

```sh
#!/bin/sh
set -e
cd "$(dirname "$0")"
mkdir -p src
grep -v '^#' designs.txt | while read name repo commit top files; do
  [ -z "$name" ] && continue
  d=src/$(basename "$repo")
  [ -d "$d" ] || git clone -q "$repo" "$d"
  git -C "$d" checkout -q "$commit"
done
```

Run: `chmod +x fetch.sh && ./fetch.sh && ls src` → Expected: `aes benchmarks picorv32 sha256`

- [ ] **Step 4: Write `sta.tcl`**

```tcl
read_liberty @LIB@
read_verilog @NET@
link_design @TOP@
if {[llength [get_ports -quiet clk]]} {
  create_clock -name clk -period @P@ [get_ports clk]
  set ins [delete_from_list [all_inputs] [get_ports clk]]
} else {
  create_clock -name clk -period @P@
  set ins [all_inputs]
}
set_input_delay 0 -clock clk $ins
set_output_delay 0 -clock clk [all_outputs]
set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 -pin Y $ins
set_load 0.01 [all_outputs]
report_worst_slack -max -digits 4
report_checks -path_delay max -group_path_count @N@ -fields {cap slew input_pins} -digits 4
```

- [ ] **Step 5: Write `run.sh` (synth + STA; later tasks append steps)**

```sh
#!/bin/sh
# usage: ./run.sh [design ...]   (default: all designs in designs.txt)
cd "$(dirname "$0")"
mkdir -p results
PDK=$(ls -d ~/.ciel/ciel/sky130/versions/*/sky130A | head -1)
LIB=$PDK/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
export PDK LIB
N=${N:-200}
grep -v '^#' designs.txt | while read name repo commit top files; do
  [ -z "$name" ] && continue
  if [ $# -gt 0 ] && ! echo " $* " | grep -q " $name "; then continue; fi
  echo "== $name"
  src=src/$(basename "$repo"); out=build/$name; mkdir -p "$out"
  rv=""; for f in $(echo "$files" | tr , ' '); do rv="$rv $src/$f"; done
  if [ "$top" = "-" ]; then topopt="-auto-top"; else topopt="-top $top"; fi
  yosys -q -l "$out/yosys.log" -p "read_liberty -lib $LIB; read_verilog $rv; hierarchy -check $topopt; synth -flatten; dfflibmap -liberty $LIB; abc -liberty $LIB; setundef -zero; hilomap -singleton -hicell sky130_fd_sc_hd__conb_1 HI -locell sky130_fd_sc_hd__conb_1 LO; opt_clean -purge; rename -enumerate -pattern u_% t:*; write_json $out/net.json; write_verilog -noattr -noexpr $out/net.v" </dev/null || { echo "$name: synth failed" >> results/failed.txt; continue; }
  tn=$(python3 -c "import json;d=json.load(open('$out/net.json'));print(next(k for k,m in d['modules'].items() if m['attributes'].get('top')))")
  sed -e "s|@LIB@|$LIB|;s|@NET@|$out/net.v|;s|@TOP@|$tn|;s|@P@|100|;s|@N@|1|" sta.tcl > "$out/sta1.tcl"
  ws=$(sta -no_init -no_splash -exit "$out/sta1.tcl" </dev/null | awk '/worst slack/{print $3}')
  p=$(python3 -c "print(round(0.8*(100-($ws)),4))")
  sed -e "s|@LIB@|$LIB|;s|@NET@|$out/net.v|;s|@TOP@|$tn|;s|@P@|$p|;s|@N@|$N|" sta.tcl > "$out/sta2.tcl"
  sta -no_init -no_splash -exit "$out/sta2.tcl" </dev/null > "$out/paths.rpt"
  echo "{\"top\": \"$tn\", \"period\": $p}" > "$out/meta.json"
done
```

- [ ] **Step 6: Run on the smallest design**

Run: `chmod +x run.sh && ./run.sh epfl_adder && cat build/epfl_adder/meta.json && grep -c 'slack (VIOLATED)' build/epfl_adder/paths.rpt`
Expected: meta with period ≈ 0.8× critical delay; violated count > 0 and ≤ 200.

- [ ] **Step 7: Commit** `git add -A && git commit -m "poc: design list, fetch, sky130 synth + STA"`

---

### Task 2: Parsers (`netlist.py`)

**Interfaces:**
- Produces:
  - `load_liberty(path) -> {cell: {"inputs": [pin], "outputs": {pin: fn|None}, "area": float, "seq": bool}}`
  - `eval_fn(expr: str, env: dict[str,int]) -> int` (0/1; raises ValueError on unknown syntax)
  - `load_netlist(json_path, lib) -> (cells, driver, loads)`; `cells[inst] = {"type": str, "pins": {pin: bit}}`, `driver[bit] = (inst, pin)`, `loads[bit] = [(inst|"$port", pin)]`
  - `parse_paths(text) -> [{"slack": float, "rows": [{"inst","pin","cell","delay","time","cap"}]}]` (data-arrival section only)

- [ ] **Step 1: Write failing tests in `test_poc.py`**

```python
import os, json, tempfile
import netlist

RPT = """Startpoint: u_1 (rising edge-triggered flip-flop clocked by clk)
Endpoint: u_9 (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

     Cap      Slew     Delay      Time   Description
-----------------------------------------------------------------------------
            0.0000    0.0000    0.0000   clock clk (rise edge)
                      0.0000    0.0000   clock network delay (ideal)
            0.0000    0.0000    0.0000 ^ u_1/CLK (sky130_fd_sc_hd__dfxtp_1)
  0.0050    0.0400    0.3000    0.3000 v u_1/Q (sky130_fd_sc_hd__dfxtp_1)
            0.0400    0.0000    0.3000 v u_2/A (sky130_fd_sc_hd__nand2_1)
  0.0040    0.0500    0.0800    0.3800 ^ u_2/Y (sky130_fd_sc_hd__nand2_1)
            0.0500    0.0000    0.3800 ^ u_9/D (sky130_fd_sc_hd__dfxtp_1)
                                0.3800   data arrival time

            0.0000    1.0000    1.0000   clock clk (rise edge)
                                1.0000 ^ u_9/CLK (sky130_fd_sc_hd__dfxtp_1)
                     -0.1000    0.9000   library setup time
                                0.9000   data required time
-----------------------------------------------------------------------------
                               -0.2000   slack (VIOLATED)
"""

def test_parsers():
    p = netlist.parse_paths(RPT)
    assert len(p) == 1 and p[0]["slack"] == -0.2
    names = [(r["inst"], r["pin"]) for r in p[0]["rows"]]
    assert names == [("u_1", "CLK"), ("u_1", "Q"), ("u_2", "A"), ("u_2", "Y"), ("u_9", "D")], names
    assert p[0]["rows"][3]["delay"] == 0.08 and p[0]["rows"][3]["cap"] == 0.004
    assert netlist.eval_fn("!((A1&A2) | B1)", {"A1": 1, "A2": 1, "B1": 0}) == 0
    assert netlist.eval_fn("A^B", {"A": 1, "B": 1}) == 0
    try:
        netlist.eval_fn("A'", {"A": 1}); assert False, "must reject"
    except ValueError:
        pass
    lib = netlist.load_liberty(os.environ["LIB"])
    a = lib["sky130_fd_sc_hd__a21oi_1"]
    assert sorted(a["inputs"]) == ["A1", "A2", "B1"] and "Y" in a["outputs"] and not a["seq"]
    assert lib["sky130_fd_sc_hd__dfxtp_1"]["seq"]

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
```

- [ ] **Step 2: Run** `LIB=... python3 test_poc.py` → Expected: `ModuleNotFoundError: netlist`

- [ ] **Step 3: Implement `netlist.py`**

```python
"""Parsers for sky130 liberty, Yosys JSON netlists and OpenSTA path reports."""
import json, re

_HDR = re.compile(r'(\w+)\s*\(\s*"?([^")]*)"?\s*\)\s*\{')

def load_liberty(path):
    cells, stack, cell, pin, pdir = {}, [], None, None, {}
    for line in open(path):
        s = line.strip()
        m = _HDR.match(s)
        if m:
            kind, name = m.groups()
            stack.append(kind)
            if kind == "cell" and len(stack) == 2:
                cell = cells.setdefault(name, {"inputs": [], "outputs": {}, "area": 0.0, "seq": False})
            elif kind == "pin" and cell is not None and stack[-2] == "cell":
                pin = name
            elif kind in ("ff", "latch", "statetable") and cell is not None:
                cell["seq"] = True
        elif cell is not None and pin and stack[-1] == "pin":
            d = re.match(r'direction\s*:\s*"?(\w+)', s)
            f = re.match(r'function\s*:\s*"([^"]*)"', s)
            if d and d.group(1) == "input":
                cell["inputs"].append(pin)
            elif d and d.group(1) == "output":
                cell["outputs"].setdefault(pin, None)
            if f:
                cell["outputs"][pin] = f.group(1)
        elif cell is not None and stack and stack[-1] == "cell":
            a = re.match(r'area\s*:\s*([\d.]+)', s)
            if a:
                cell["area"] = float(a.group(1))
        for _ in range(s.count("}")):
            kind = stack.pop()
            if kind == "pin":
                pin = None
            elif kind == "cell":
                cell = None
    return cells

_FN_OK = re.compile(r"[\w\s!&|^()*+]+")

def eval_fn(expr, env):
    if not _FN_OK.fullmatch(expr):
        raise ValueError(f"unsupported liberty function: {expr}")
    py = expr.replace("!", " ~").replace("*", "&").replace("+", "|")
    return eval(py, {"__builtins__": {}}, dict(env)) & 1

def load_netlist(json_path, lib):
    d = json.load(open(json_path))
    top = next(m for m in d["modules"].values() if m["attributes"].get("top"))
    cells, driver, loads = {}, {}, {}
    for inst, c in top["cells"].items():
        pins = {p: b[0] for p, b in c["connections"].items() if b}
        cells[inst] = {"type": c["type"], "pins": pins}
        info = lib.get(c["type"], {"inputs": [], "outputs": {}})
        for p, bit in pins.items():
            if p in info["outputs"]:
                driver[bit] = (inst, p)
            elif p in info["inputs"]:
                loads.setdefault(bit, []).append((inst, p))
    for pname, port in top["ports"].items():
        if port["direction"] == "output":
            for bit in port["bits"]:
                loads.setdefault(bit, []).append(("$port", pname))
    return cells, driver, loads

_ROW = re.compile(r'^\s*([-\d.\s]+?)\s+[\^v]\s+(\S+)/(\S+)\s+\((\S+)\)\s*$')

def parse_paths(text):
    paths, rows, active = [], None, False
    for line in text.splitlines():
        if line.startswith("Startpoint:"):
            rows, active = [], True
        elif "data arrival time" in line:
            active = False
        elif "slack (" in line and rows is not None:
            paths.append({"slack": float(line.split()[0]), "rows": rows})
            rows = None
        elif active:
            m = _ROW.match(line)
            if m:
                nums = [float(x) for x in m.group(1).split()]
                rows.append({"inst": m.group(2), "pin": m.group(3), "cell": m.group(4),
                             "delay": nums[-2], "time": nums[-1],
                             "cap": nums[0] if len(nums) == 4 else None})
    return paths
```

- [ ] **Step 4: Run** `LIB=$(ls ~/.ciel/ciel/sky130/versions/*/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib) python3 test_poc.py` → Expected: `ok test_parsers`

- [ ] **Step 5: Parse real output** `python3 -c "import netlist;p=netlist.parse_paths(open('build/epfl_adder/paths.rpt').read());print(len(p),p[0]['slack'],len(p[0]['rows']))"` → Expected: ~200 paths, negative first slack.

- [ ] **Step 6: Commit** `git commit -am "poc: liberty/netlist/STA parsers"` (after `git add netlist.py test_poc.py`)

---

### Task 3: Cluster mining + Tier 1 (`mine.py`)

**Interfaces:**
- Consumes: `netlist.load_liberty`, `load_netlist`, `parse_paths`.
- Produces: `build/<d>/clusters.json` = `{key: {"tree": node, "n_in": int, "count": int, "crit": float, "late": int, "load": float, "instances": [{"members": [inst], "leaves": [[inst, pin]]}]}}` where `node = {"cell": str, "out": str, "ins": {pin: node|None}}` (leaf order = DFS over sorted pin names; `None` = leaf);
  `build/<d>/pathrows.json` = per path, list of `"celltype:delay"` strings (LLM prompt input);
  `build/<d>/hits.json` = `[{"slack": float, "hits": [{"key": str, "members": [inst], "entry": int, "delay": float}]}]` (one entry per parsed path);
  appends a row to `results/tier1.csv`: `design,n_cells,n_paths,n_viol,n_keys,overlap5,overlap10,cov5,cov10`.
- Function `mine(cells, driver, loads, lib) -> dict[key, {"tree","n_in","instances"}]` (pure, testable).

- [ ] **Step 1: Add failing test to `test_poc.py`**

```python
import mine

def test_mine_toy():
    lib = {"nand2": {"inputs": ["A", "B"], "outputs": {"Y": "!(A&B)"}, "seq": False, "area": 1},
           "inv":   {"inputs": ["A"], "outputs": {"Y": "!A"}, "seq": False, "area": 1}}
    # u1 = nand(a,b) -> u2 = inv -> port ; u3 = nand(a, u1)  => u1 has fanout 2: no (u1,u2) cluster
    cells = {"u1": {"type": "nand2", "pins": {"A": 1, "B": 2, "Y": 3}},
             "u2": {"type": "inv",   "pins": {"A": 3, "Y": 4}},
             "u3": {"type": "nand2", "pins": {"A": 1, "B": 3, "Y": 5}},
             "u4": {"type": "inv",   "pins": {"A": 5, "Y": 6}}}
    driver = {3: ("u1", "Y"), 4: ("u2", "Y"), 5: ("u3", "Y"), 6: ("u4", "Y")}
    loads = {1: [("u1", "A"), ("u3", "A")], 2: [("u1", "B")], 3: [("u2", "A"), ("u3", "B")],
             5: [("u4", "A")], 4: [("$port", "o1")], 6: [("$port", "o2")]}
    cl = mine.mine(cells, driver, loads, lib)
    assert list(cl) == ["inv(A=nand2(A=*,B=*))"], list(cl)
    inst = cl["inv(A=nand2(A=*,B=*))"]["instances"][0]
    assert sorted(inst["members"]) == ["u3", "u4"] and inst["leaves"] == [["u3", "A"], ["u3", "B"]]
```

- [ ] **Step 2: Run** → Expected: FAIL `No module named 'mine'`

- [ ] **Step 3: Implement `mine.py`**

```python
"""Mine 2-3 cell fanout-free clusters; attach STA criticality; Tier 1 metrics."""
import json, os, statistics, sys
import netlist

MAX_IN = 6
PFX = "sky130_fd_sc_hd__"

def _comb(lib, t):
    c = lib.get(t)
    return c is not None and not c["seq"] and len(c["outputs"]) == 1 and c["inputs"]

def _build(inst, cells, lib, kids):
    """kids: {inst: {pin: child_inst}} -> (key, tree, leaves, members)"""
    t = cells[inst]["type"]
    out = next(iter(lib[t]["outputs"]))
    parts, ins, leaves, members = [], {}, [], [inst]
    for p in sorted(lib[t]["inputs"]):
        c = kids.get(inst, {}).get(p)
        if c:
            k, tr, lv, mb = _build(c, cells, lib, kids)
            parts.append(f"{p}={k}"); ins[p] = tr; leaves += lv; members += mb
        else:
            parts.append(f"{p}=*"); ins[p] = None; leaves.append([inst, p])
    return f"{t.removeprefix(PFX)}({','.join(parts)})", {"cell": t, "out": out, "ins": ins}, leaves, members

def mine(cells, driver, loads, lib):
    def child(inst, p):
        bit = cells[inst]["pins"].get(p)
        d = driver.get(bit)
        if d and _comb(lib, cells[d[0]]["type"]) and len(loads.get(bit, [])) == 1:
            return d[0]
    out = {}
    for r in sorted(cells):
        if not _comb(lib, cells[r]["type"]):
            continue
        ch = [(p, child(r, p)) for p in sorted(lib[cells[r]["type"]]["inputs"])]
        ch = [(p, c) for p, c in ch if c]
        shapes = [{r: {p: c}} for p, c in ch]
        shapes += [{r: {p1: c1, p2: c2}} for i, (p1, c1) in enumerate(ch) for p2, c2 in ch[i + 1:]]
        for p, c in ch:
            for q in sorted(lib[cells[c]["type"]]["inputs"]):
                g = child(c, q)
                if g:
                    shapes.append({r: {p: c}, c: {q: g}})
        for kids in shapes:
            key, tree, leaves, members = _build(r, cells, lib, kids)
            if len(leaves) > MAX_IN:
                continue
            e = out.setdefault(key, {"tree": tree, "n_in": len(leaves), "instances": []})
            e["instances"].append({"members": members, "leaves": leaves})
    return out

def hits_for_path(path, index):
    """index: {(inst, pin): [(key, inst_id, leaf_idx)]} for leaf pins."""
    rows, hits = path["rows"], []
    for i, r in enumerate(rows):
        for key, iid, members, leaf in index.get((r["inst"], r["pin"]), []):
            ms, d, j = set(members), 0.0, i
            while j < len(rows) and rows[j]["inst"] in ms:
                d += rows[j]["delay"]; j += 1
            hits.append({"key": key, "members": members, "entry": leaf, "delay": round(d, 4),
                         "iid": iid, "load": rows[j - 1]["cap"]})
    return hits

def run(bdir):
    lib = netlist.load_liberty(os.environ["LIB"])
    cells, driver, loads = netlist.load_netlist(f"{bdir}/net.json", lib)
    paths = netlist.parse_paths(open(f"{bdir}/paths.rpt").read())
    cl = mine(cells, driver, loads, lib)
    index = {}
    for key, e in cl.items():
        for iid, ins in enumerate(e["instances"]):
            for li, (inst, pin) in enumerate(ins["leaves"]):
                index.setdefault((inst, pin), []).append((key, iid, ins["members"], li))
    allhits = []
    for p in paths:
        h = hits_for_path(p, index)
        # only full traversals (path reaches the cluster root output) count
        h = [x for x in h if x["delay"] > 0]
        allhits.append({"slack": p["slack"], "hits": h})
    for key, e in cl.items():
        e["count"] = len(e["instances"])
        viol = [x for ph in allhits if ph["slack"] < 0 for x in ph["hits"] if x["key"] == key]
        e["crit"] = round(sum(x["delay"] for x in viol), 4)
        entries = [x["entry"] for x in viol]
        e["late"] = max(set(entries), key=entries.count) if entries else 0
        caps = [x["load"] for x in viol if x["load"]]
        e["load"] = statistics.median(caps) if caps else 0.005
    json.dump(cl, open(f"{bdir}/clusters.json", "w"))
    json.dump([[f"{r['cell'].removeprefix(PFX)}:{r['delay']}" for r in p["rows"] if r["delay"] > 0] for p in paths],
              open(f"{bdir}/pathrows.json", "w"))
    json.dump(allhits, open(f"{bdir}/hits.json", "w"))
    return tier1(os.path.basename(bdir), cells, paths, allhits, cl)

def tier1(name, cells, paths, allhits, cl):
    viol = [(p, ph) for p, ph in zip(paths, allhits) if p["slack"] < 0]
    total = sum(r["delay"] for p, _ in viol for r in p["rows"])
    by_freq = sorted(cl, key=lambda k: (-cl[k]["count"], k))
    by_crit = sorted(cl, key=lambda k: (-cl[k]["crit"], k))
    def cov(keys):
        ks, s = set(keys), 0.0
        for p, ph in viol:
            covered = {m for x in ph["hits"] if x["key"] in ks for m in x["members"]}
            s += sum(r["delay"] for r in p["rows"] if r["inst"] in covered)
        return round(s / total, 4) if total else 0.0
    ov = lambda K: round(len(set(by_freq[:K]) & set(by_crit[:K])) / K, 2)
    row = [name, len(cells), len(paths), len(viol), len(cl), ov(5), ov(10), cov(by_freq[:5]), cov(by_freq[:10])]
    os.makedirs("results", exist_ok=True)
    new = not os.path.exists("results/tier1.csv")
    with open("results/tier1.csv", "a") as f:
        if new:
            f.write("design,n_cells,n_paths,n_viol,n_keys,overlap5,overlap10,cov5,cov10\n")
        f.write(",".join(map(str, row)) + "\n")
    return row

if __name__ == "__main__":
    print(run(sys.argv[1].rstrip("/")))
```

Note on `hits_for_path`: `index` values are 4-tuples `(key, iid, members, leaf)`.

- [ ] **Step 4: Run** `python3 test_poc.py` → Expected: `ok test_parsers`, `ok test_mine_toy`

- [ ] **Step 5: Real design** `python3 mine.py build/epfl_adder && cat results/tier1.csv` → Expected: one row with n_keys > 0.

- [ ] **Step 6: Append to `run.sh` loop (before `done`):** `python3 mine.py "$out" </dev/null`; commit `git commit -am "poc: cluster mining + tier1"` (after `git add mine.py`).

---

### Task 4: Realization + SPICE oracle (`cellgen.py`)

**Interfaces:**
- Consumes: `clusters.json` trees, `netlist.load_liberty`, `netlist.eval_fn`.
- Produces:
  - `truth_table(tree, lib) -> (n, tt)` (tt: int bitmask, bit m = f(minterm m), var i = bit i of m)
  - `sop(n, tt) -> [(mask, val)]` minimal-ish prime cover
  - `realize(n, tt, late=0) -> {"feasible": bool, "invert_out": bool, "cubes": [(mask,val)], "neg_inputs": [int], "tx": int}`
  - `oracle(bdir) -> writes build/<d>/oracle.json = {key: {"feasible", "ratio": [float]*n_in, "orig_ps": [...], "fused_ps": [...], "tx_orig": int, "tx_fused": int}}`

- [ ] **Step 1: Add failing tests**

```python
import cellgen

def test_logic():
    # f = a&b (and2): minimal SOP one cube; realized as nand stage + output inverter
    r = cellgen.realize(2, 0b1000)
    assert r["feasible"] and r["invert_out"] and r["tx"] == 6, r
    # f = !(a&b | c) (a21oi): single stage, no inverters, 6 transistors
    tt = sum(1 << m for m in range(8) if not (((m & 1) and (m >> 1 & 1)) or (m >> 2 & 1)))
    r = cellgen.realize(3, tt)
    assert r["feasible"] and not r["invert_out"] and r["neg_inputs"] == [] and r["tx"] == 6, r
    # 6-input AND needs a 6-deep stack: infeasible
    assert not cellgen.realize(6, 1 << 63)["feasible"]
    # sop covers exactly
    for n, tt in [(3, 0b10010110), (4, 0xBEEF)]:
        cubes = cellgen.sop(n, tt)
        got = sum(1 << m for m in range(1 << n) if any((m & mk) == v for mk, v in cubes))
        assert got == tt

def test_oracle_sanity():
    """nand2 -> inv equals and2: fused and original must be within 25%."""
    lib = netlist.load_liberty(os.environ["LIB"])
    tree = {"cell": "sky130_fd_sc_hd__inv_1", "out": "Y",
            "ins": {"A": {"cell": "sky130_fd_sc_hd__nand2_1", "out": "Y", "ins": {"A": None, "B": None}}}}
    res = cellgen.measure({"k": {"tree": tree, "n_in": 2, "late": 0, "load": 0.005}}, lib)
    r = res["k"]["ratio"]
    assert all(0.75 < x < 1.25 for x in r), res
```

- [ ] **Step 2: Run** → Expected: FAIL `No module named 'cellgen'`

- [ ] **Step 3: Implement `cellgen.py`**

```python
"""Cluster function -> single-stage static CMOS realization -> ngspice ratio vs. original."""
import glob, json, os, re, subprocess, sys, tempfile
from itertools import combinations
import netlist

MAX_STACK = 4
NF = "sky130_fd_pr__nfet_01v8 w=650000u l=150000u"
PF = "sky130_fd_pr__pfet_01v8_hvt w=1e+06u l=150000u"
PDK = os.environ.get("PDK") or sorted(glob.glob(os.path.expanduser("~/.ciel/ciel/sky130/versions/*/sky130A")))[0]
LIBSPICE = f"{PDK}/libs.ref/sky130_fd_sc_hd/spice/sky130_fd_sc_hd.spice"

def truth_table(tree, lib):
    leaves = []
    def walk(t):
        for p in sorted(t["ins"]):
            walk(t["ins"][p]) if t["ins"][p] else leaves.append(None)
    walk(tree)
    n = len(leaves)
    def ev(t, it):
        env = {p: (ev(c, it) if c else next(it)) for p, c in sorted(t["ins"].items())}
        return netlist.eval_fn(lib[t["cell"]]["outputs"][t["out"]], env)
    tt = 0
    for m in range(1 << n):
        if ev(tree, iter([(m >> i) & 1 for i in range(n)])):
            tt |= 1 << m
    return n, tt

def sop(n, tt):
    """Prime implicants (Quine-McCluskey) + greedy cover. Cube = (mask, val)."""
    on = [m for m in range(1 << n) if tt >> m & 1]
    if not on:
        return []
    full = (1 << n) - 1
    cur, primes = {(full, m) for m in on}, set()
    while cur:
        nxt, used = set(), set()
        for (m1, v1), (m2, v2) in combinations(sorted(cur), 2):
            d = v1 ^ v2
            if m1 == m2 and d and not d & (d - 1):
                nxt.add((m1 & ~d, v1 & ~d)); used |= {(m1, v1), (m2, v2)}
        primes |= cur - used
        cur = nxt
    cover, left = [], set(on)
    while left:  # ponytail: greedy cover, not exact minimum; fine for <=6 vars
        best = max(sorted(primes), key=lambda c: (sum(1 for m in left if m & c[0] == c[1]), -bin(c[0]).count("1")))
        cover.append(best); left = {m for m in left if m & best[0] != best[1]}
    return cover

def realize(n, tt, late=0):
    full = (1 << (1 << n)) - 1
    best = None
    for invert_out in (False, True):
        g = tt if invert_out else full & ~tt          # pull-down conducts when g=1, stage out = !g
        cubes = sop(n, g)
        if not cubes:
            continue
        depth_n = max(bin(m).count("1") for m, _ in cubes)
        depth_p = len(cubes)
        neg = sorted({i for m, v in cubes for i in range(n) if m >> i & 1 and not v >> i & 1})
        lits = sum(bin(m).count("1") for m, _ in cubes)
        tx = 2 * lits + 2 * len(neg) + (2 if invert_out else 0)
        cand = {"feasible": depth_n <= MAX_STACK and depth_p <= MAX_STACK, "invert_out": invert_out,
                "cubes": cubes, "neg_inputs": neg, "tx": tx, "late": late,
                "stages": 1 + (1 if neg else 0) + (1 if invert_out else 0)}
        if best is None or (not best["feasible"], best["stages"], best["tx"]) > (not cand["feasible"], cand["stages"], cand["tx"]):
            best = cand
    return best or {"feasible": False, "invert_out": False, "cubes": [], "neg_inputs": [], "tx": 0, "late": late, "stages": 0}

def fused_subckt(name, n, r):
    """Emit SPICE subckt: ports in0..in{n-1} VGND VNB VPB VPWR Y."""
    L, k = [f".subckt {name} {' '.join(f'in{i}' for i in range(n))} VGND VNB VPB VPWR Y"], 0
    def dev(d, g, s, pmos):
        nonlocal k; k += 1
        L.append(f"XM{k} {d} {g} {s} {'VPB' if pmos else 'VNB'} {PF if pmos else NF}")
    for i in r["neg_inputs"]:
        L.append(f"XI{i} in{i} VGND VNB VPB VPWR ni{i} sky130_fd_sc_hd__inv_1")
    sig = lambda i, v: f"in{i}" if v >> i & 1 else f"ni{i}"
    z = "z" if r["invert_out"] else "Y"
    order = lambda m: sorted([i for i in range(n) if m >> i & 1], key=lambda i: (i != r["late"], i))
    for c, (m, v) in enumerate(r["cubes"]):          # pull-down: series per cube, late input at output
        top = z
        vs = order(m)
        for j, i in enumerate(vs):
            bot = "VGND" if j == len(vs) - 1 else f"pd{c}_{j}"
            dev(top, sig(i, v), bot, False); top = bot
    groups = sorted(range(len(r["cubes"])), key=lambda c: (not r["cubes"][c][0] >> r["late"] & 1, c))
    top = z                                          # pull-up: series of parallel groups, late group at output
    for j, c in enumerate(groups):
        bot = "VPWR" if j == len(groups) - 1 else f"pu{j}"
        m, v = r["cubes"][c]
        for i in order(m):
            dev(top, sig(i, v), bot, True)
        top = bot
    if r["invert_out"]:
        L.append("XO z VGND VNB VPB VPWR Y sky130_fd_sc_hd__inv_1")
    L.append(".ends")
    return "\n".join(L)

def _subckt_pins():
    pins = {}
    for line in open(LIBSPICE):
        if line.startswith(".subckt"):
            t = line.split(); pins[t[1]] = t[2:]
    return pins

def orig_subckt(name, tree, pins):
    L, leaf, k = [], [0], [0]
    def walk(t, outnet):
        k[0] += 1; me = k[0]; conn = {t["out"]: outnet}
        for p in sorted(t["ins"]):
            if t["ins"][p]:
                net = f"n{me}_{p}"; walk(t["ins"][p], net); conn[p] = net
            else:
                conn[p] = f"in{leaf[0]}"; leaf[0] += 1
        conn.update(VGND="VGND", VNB="VNB", VPB="VPB", VPWR="VPWR")
        L.append(f"XC{me} {' '.join(conn[p] for p in pins[t['cell']])} {t['cell']}")
    walk(tree, "Y")
    n = leaf[0]
    return "\n".join([f".subckt {name} {' '.join(f'in{i}' for i in range(n))} VGND VNB VPB VPWR Y"] + L + [".ends"])

def _tx(text):
    return sum(1 for l in text.splitlines() if "sky130_fd_pr__" in l)

def measure(clusters, lib):
    """clusters: {key: {"tree","n_in","late","load"}} -> {key: oracle entry}."""
    pins, res, body, subs, meas = _subckt_pins(), {}, [], [], []
    spice_txt = open(LIBSPICE).read()
    for ci, (key, e) in enumerate(sorted(clusters.items())):
        n, tt = truth_table(e["tree"], lib)
        r = realize(n, tt, e.get("late", 0))
        cells = []
        def collect(t):
            cells.append(t["cell"]); [collect(c) for c in t["ins"].values() if c]
        collect(e["tree"])
        tx_orig = sum(_tx(re.search(rf"^\.subckt {c} .*?^\.ends", spice_txt, re.M | re.S).group(0)) for c in cells)
        res[key] = {"feasible": r["feasible"], "tx_orig": tx_orig, "tx_fused": r["tx"],
                    "ratio": [1.0] * n, "orig_ps": [None] * n, "fused_ps": [None] * n}
        if not r["feasible"]:
            continue
        subs.append(orig_subckt(f"orig{ci}", e["tree"], pins))
        subs.append(fused_subckt(f"fused{ci}", n, r))
        for i in range(n):
            vec = next((m for m in range(1 << n) if not m >> i & 1 and (tt >> m & 1) != (tt >> (m | 1 << i) & 1)), None)
            if vec is None:
                continue                              # input does not affect output
            for kind in ("orig", "fused"):
                tag = f"{kind[0]}{ci}_{i}"
                ins = " ".join(f"d{tag}" if j == i else ("vdd" if vec >> j & 1 else "0") for j in range(n))
                body.append(f"XD{tag} src 0 0 vdd vdd d{tag} sky130_fd_sc_hd__inv_1")
                body.append(f"XU{tag} {ins} 0 0 vdd vdd y{tag} {kind}{ci}")
                body.append(f"C{tag} y{tag} 0 {e['load']}p")
                meas.append((key, kind, i, tag))
    if not meas:
        return res
    lines = [f'.lib "{PDK}/libs.tech/ngspice/sky130.lib.spice" tt', f'.include "{LIBSPICE}"', *subs,
             "Vdd vdd 0 1.8", "Vs src 0 PULSE(0 1.8 200p 50p 50p 1.5n 3n)", *body, ".tran 2p 3.5n",
             ".control", "run"]
    for _, _, _, tag in meas:
        lines.append(f"meas tran r{tag} TRIG v(d{tag}) VAL=0.9 CROSS=1 TARG v(y{tag}) VAL=0.9 CROSS=1")
        lines.append(f"meas tran f{tag} TRIG v(d{tag}) VAL=0.9 CROSS=2 TARG v(y{tag}) VAL=0.9 CROSS=2")
    lines += ["quit", ".endc", ".end"]
    with tempfile.TemporaryDirectory() as tmp:
        open(f"{tmp}/.spiceinit", "w").write("set ngbehavior=hsa\nset ng_nomodcheck\n")
        open(f"{tmp}/tb.sp", "w").write("\n".join(lines))
        out = subprocess.run(["ngspice", "-b", "tb.sp"], cwd=tmp, capture_output=True, text=True).stdout
    val = {m.group(1).lower(): float(m.group(2)) for m in re.finditer(r"^(\w+)\s*=\s*([-\d.e+]+)", out, re.M)}
    for key, kind, i, tag in meas:
        a, b = val.get(f"r{tag}".lower()), val.get(f"f{tag}".lower())
        if a is not None and b is not None:
            res[key][f"{kind}_ps"][i] = round(max(a, b) * 1e12, 2)
    for key, v in res.items():
        v["ratio"] = [round(f / o, 4) if (o and f) else 1.0 for o, f in zip(v["orig_ps"], v["fused_ps"])]
    return res

def oracle(bdir, pool):
    lib = netlist.load_liberty(os.environ["LIB"])
    cl = json.load(open(f"{bdir}/clusters.json"))
    res = measure({k: cl[k] for k in pool}, lib)
    json.dump(res, open(f"{bdir}/oracle.json", "w"), indent=1)
    return res
```

`cellgen.py` has no `__main__`; `tier2.py` (Task 7) drives the oracle.

- [ ] **Step 4: Run** `python3 test_poc.py` → Expected: `ok test_logic`, `ok test_oracle_sanity` (oracle test ~15 s)

- [ ] **Step 5: Commit** `git add cellgen.py && git commit -am "poc: complex-gate realization + ngspice ratio oracle"`

---

### Task 5: Scorer (`score.py`)

**Interfaces:**
- Consumes: `hits.json`, `oracle.json`, `clusters.json`.
- Produces: `score(keys, hits, orc, cl) -> {"dwns": ps, "dtns": ps, "dtx": int}`; `best_subset(pool, k, hits, orc, cl) -> (keys, label)`.

- [ ] **Step 1: Add failing test**

```python
import score

def test_score():
    hits = [{"slack": -0.10, "hits": [
                {"key": "A", "members": ["u1", "u2"], "entry": 0, "delay": 0.2},
                {"key": "B", "members": ["u2", "u3"], "entry": 0, "delay": 0.3},   # overlaps A on u2
                {"key": "C", "members": ["u9", "u8"], "entry": 0, "delay": 0.1}]},
            {"slack": -0.05, "hits": [{"key": "C", "members": ["u7", "u6"], "entry": 0, "delay": 0.1}]}]
    orc = {"A": {"feasible": True, "ratio": [0.5], "tx_orig": 10, "tx_fused": 6},
           "B": {"feasible": True, "ratio": [0.5], "tx_orig": 10, "tx_fused": 6},
           "C": {"feasible": True, "ratio": [1.3], "tx_orig": 10, "tx_fused": 6}}
    cl = {"A": {"count": 1}, "B": {"count": 1}, "C": {"count": 2}}
    s = score.score(["A", "B"], hits, orc, cl)
    # only B (saving 0.15) applies on path 0 -> slack -0.10+0.15=+0.05 ; WNS = path1 = -0.05
    assert abs(s["dwns"] - 50.0) < 1e-6, s
    s = score.score(["C"], hits, orc, cl)          # slower fused cell: clipped, no gain
    assert s["dwns"] == 0 and s["dtns"] == 0
```

- [ ] **Step 2: Run** → FAIL `No module named 'score'`

- [ ] **Step 3: Implement `score.py`**

```python
"""Recompute top-N path slack with chosen fused cells; exhaustive oracle subset."""
from itertools import combinations
from math import comb

def _slacks(keys, hits, orc):
    ks, out = set(keys), []
    for ph in hits:
        cand = []
        for x in ph["hits"]:
            o = orc.get(x["key"])
            if x["key"] in ks and o and o["feasible"]:
                cand.append((max(0.0, x["delay"] * (1 - o["ratio"][x["entry"]])), x["members"]))
        used, save = set(), 0.0
        for s, mem in sorted(cand, key=lambda c: -c[0]):
            if s > 0 and not used & set(mem):
                used |= set(mem); save += s
        out.append(ph["slack"] + save)
    return out

def score(keys, hits, orc, cl):
    base = [ph["slack"] for ph in hits]
    new = _slacks(keys, hits, orc)
    tns = lambda xs: sum(min(0.0, x) for x in xs)
    dtx = sum(cl[k]["count"] * (orc[k]["tx_fused"] - orc[k]["tx_orig"]) for k in keys if k in orc and orc[k]["feasible"])
    return {"dwns": round((min(new) - min(base)) * 1000, 3), "dtns": round((tns(new) - tns(base)) * 1000, 3), "dtx": dtx}

def best_subset(pool, k, hits, orc, cl, limit=200_000):
    # ponytail: exhaustive over the 30 most critical candidates only; an LLM pick outside them can exceed 1.0 of this "oracle"
    pool = sorted(pool, key=lambda p: -cl[p]["crit"])[:30]
    def key(s):
        r = score(s, hits, orc, cl)
        return (r["dwns"], r["dtns"])
    if comb(len(pool), k) <= limit:
        return list(max(combinations(pool, k), key=key)), "exhaustive"
    chosen = []                                    # ponytail: greedy on oracle when C(n,k) too big; lower bound on optimum
    for _ in range(k):
        chosen.append(max((p for p in pool if p not in chosen), key=lambda p: key(chosen + [p])))
    return chosen, "oracle-greedy"
```

- [ ] **Step 4: Run** `python3 test_poc.py` → Expected: all ok.

- [ ] **Step 5: Commit** `git add score.py && git commit -am "poc: path-slack scorer + oracle subset"`

---

### Task 6: Selectors incl. LLM (`selectors_.py`)

Named `selectors_.py` because `select` and `selectors` shadow stdlib modules.

**Interfaces:**
- Produces: `pool(cl) -> [key]` (crit > 0, top 60 by crit); `pick_freq(cl, k)`, `pick_sta(cl, pl, k)`, `pick_rules(cl, pl, k, lib)`, `pick_llm(cl, pl, k, hits, design, period, lib) -> [key]`; `parse_llm(text, pl, k) -> [key]`.

- [ ] **Step 1: Add failing test**

```python
import selectors_

def test_parse_llm():
    pl = ["a(A=*)", "b(A=*)", "c(A=*)"]
    txt = 'blah\n```json\n{"picks": [{"key": "b(A=*)", "why": "x"}, {"key": "zzz", "why": "y"}]}\n```'
    assert selectors_.parse_llm(txt, pl, 2) == ["b(A=*)"]
    assert selectors_.parse_llm("no json here", pl, 2) == []
```

- [ ] **Step 2: Run** → FAIL

- [ ] **Step 3: Implement `selectors_.py`**

```python
"""Cheap-information selectors: frequency, STA-greedy, STA+rules, LLM (claude -p)."""
import json, os, re, subprocess
import cellgen

def pool(cl):
    return sorted((k for k in cl if cl[k]["crit"] > 0), key=lambda k: (-cl[k]["crit"], k))[:60]

def pick_freq(cl, k):
    return sorted(cl, key=lambda x: (-cl[x]["count"], x))[:k]

def pick_sta(cl, pl, k):
    return pl[:k]

def pick_rules(cl, pl, k, lib):
    ok = [x for x in pl if cellgen.realize(*cellgen.truth_table(cl[x]["tree"], lib))["feasible"]]
    return ok[:k]

def _sop_str(n, tt):
    cubes = cellgen.sop(n, tt)
    if not cubes:
        return "0"
    lit = lambda i, v: f"x{i}" if v >> i & 1 else f"!x{i}"
    return " | ".join("&".join(lit(i, v) for i in range(n) if m >> i & 1) or "1" for m, v in cubes)

PROMPT = """You are choosing new standard cells to add to a SkyWater sky130_fd_sc_hd library for ONE design, to improve timing (WNS first, then TNS).
Each candidate is a cluster of 2-3 existing cells whose internal nets are fanout-free; adding it lets every instance be replaced by one fused cell.
You must choose BEFORE anything is simulated: each chosen cell costs a layout + characterization run, so choose exactly {k} candidates.

How a fused cell will be built (fixed rules): one static-CMOS stage from a minimal sum-of-products of the function or its complement,
series NMOS per product term, dual PMOS network, optional output inverter, inv_1 on any complemented input; transistor stacks deeper than 4
make the cell unbuildable (zero benefit). Library _1 sizing, unscaled stacks. Fused-cell delay replaces the summed delay of the cluster's cells
on each path that traverses it. Overlapping clusters on one path cannot both apply.

Design: {design}. Clock period {period} ns. {nviol} of the top {npaths} endpoints violate.

Worst paths (slack ns; cells along the path as cell_type:delay_ns; [K#] marks a candidate cluster instance on the path):
{paths}

Candidates (id | key | inputs | function (x_i = i-th leaf input; the key lists leaves in order) | instances in design | summed delay on violating paths ns | #violating paths touched):
{cands}

Reply with a short reasoning paragraph, then a JSON block exactly like:
```json
{{"picks": [{{"key": "<candidate key>", "why": "<one line>"}}]}}
```"""

def build_prompt(cl, pl, k, hits, design, period, lib):
    idx = {key: i for i, key in enumerate(pl)}
    viol = [h for h in hits if h["slack"] < 0]
    touched = {key: sum(1 for h in viol if any(x["key"] == key for x in h["hits"])) for key in pl}
    cand_lines = []
    for key in pl:
        n, tt = cellgen.truth_table(cl[key]["tree"], lib)
        cand_lines.append(f"K{idx[key]} | {key} | {n} | {_sop_str(n, tt)} | {cl[key]['count']} | {cl[key]['crit']} | {touched[key]}")
    rows = json.load(open(f"build/{design}/pathrows.json"))
    path_lines = []
    for h, r in list(zip(hits, rows))[:25]:
        marks = sorted({f"K{idx[x['key']]}" for x in h["hits"] if x["key"] in idx})
        path_lines.append(f"{h['slack']:+.3f}: {' '.join(r)}  [{' '.join(marks)}]")
    return PROMPT.format(k=k, design=design, period=period, nviol=len(viol), npaths=len(hits),
                         paths="\n".join(path_lines), cands="\n".join(cand_lines))

def parse_llm(text, pl, k):
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    try:
        picks = json.loads(m[-1])["picks"] if m else []
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    out = []
    for p in picks:
        key = p.get("key") if isinstance(p, dict) else None
        if key in pl and key not in out:
            out.append(key)
    return out[:k]

def pick_llm(cl, pl, k, hits, design, period, lib):
    prompt = build_prompt(cl, pl, k, hits, design, period, lib)
    os.makedirs("results/llm", exist_ok=True)
    base = f"results/llm/{design}_k{k}"
    open(f"{base}.prompt.txt", "w").write(prompt)
    ver = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE") and k != "CLAUDECODE"}
    out = subprocess.run(["claude", "-p", "--output-format", "text"], input=prompt, env=env,
                         capture_output=True, text=True, timeout=600).stdout
    open(f"{base}.response.txt", "w").write(f"# claude {ver}\n{out}")
    picks = parse_llm(out, pl, k)
    if len(picks) < k:
        print(f"  llm returned {len(picks)}/{k} valid picks for {design} (logged)")
    return picks
```

`build_prompt` reads `build/<d>/pathrows.json`, written by `mine.run` (Task 3).

- [ ] **Step 4: Run** `python3 test_poc.py` → all ok.

- [ ] **Step 5: Commit** `git add selectors_.py && git commit -am "poc: selectors incl. claude -p"`

---

### Task 7: Tier 2 driver, run everything, plots

**Files:** Create `tier2.py`; modify `run.sh`.

- [ ] **Step 1: Write `tier2.py`**

```python
"""Per design: oracle -> five selectors x k -> results/tier2.csv."""
import json, os, sys
import netlist, cellgen, selectors_, score

KS = (3, 5, 10)

def run(bdir, use_llm=True):
    d = os.path.basename(bdir)
    lib = netlist.load_liberty(os.environ["LIB"])
    cl = json.load(open(f"{bdir}/clusters.json"))
    hits = json.load(open(f"{bdir}/hits.json"))
    period = json.load(open(f"{bdir}/meta.json"))["period"]
    pl = selectors_.pool(cl)
    freq_all = {x for k in KS for x in selectors_.pick_freq(cl, k)}
    orc = cellgen.oracle(bdir, sorted(set(pl) | freq_all))
    new = not os.path.exists("results/tier2.csv")
    with open("results/tier2.csv", "a") as f:
        if new:
            f.write("design,k,selector,dwns_ps,dtns_ps,frac_oracle,dtx,picks\n")
        for k in KS:
            if len(pl) < k:
                continue
            best, label = score.best_subset(pl, k, hits, orc, cl)
            picks = {"frequency": selectors_.pick_freq(cl, k), "sta_greedy": selectors_.pick_sta(cl, pl, k),
                     "sta_rules": selectors_.pick_rules(cl, pl, k, lib), label: best}
            if use_llm:
                picks["llm"] = selectors_.pick_llm(cl, pl, k, hits, d, period, lib)
            top = score.score(best, hits, orc, cl)["dwns"]
            for name, keys in picks.items():
                s = score.score(keys, hits, orc, cl)
                frac = round(s["dwns"] / top, 3) if top > 0 else ""
                f.write(f'{d},{k},{name},{s["dwns"]},{s["dtns"]},{frac},{s["dtx"]},"{";".join(keys)}"\n')

if __name__ == "__main__":
    run(sys.argv[1].rstrip("/"), use_llm=os.environ.get("NO_LLM") != "1")
```

Delete the `__main__` block from `cellgen.py` (oracle is driven from `tier2.py`).

- [ ] **Step 2: Append to `run.sh` loop after `mine.py`:** `python3 tier2.py "$out" </dev/null || echo "$name: tier2 failed" >> results/failed.txt`

- [ ] **Step 3: Dry run without LLM** `rm -f results/*.csv; NO_LLM=1 ./run.sh epfl_adder && cat results/tier2.csv` → Expected: rows for frequency/sta_greedy/sta_rules/exhaustive at k=3,5,10; exhaustive frac = 1.0.

- [ ] **Step 4: Self-review checkpoint (critical):** inspect `build/epfl_adder/oracle.json` — ratios should mostly lie in 0.4–1.3; any `None` delays mean a failed `meas` → open the ngspice output and fix before running all designs. Check that `frequency` picks with `crit == 0` score 0.

- [ ] **Step 5: Full run** `rm -rf results; ./run.sh` (all designs, LLM on). Designs that fail synthesis are dropped and listed in `results/README.md`.

- [ ] **Step 6: Plots — `plots.py`**

```python
import csv
from collections import defaultdict
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

rows = list(csv.DictReader(open("results/tier2.csv")))
agg = defaultdict(list)
for r in rows:
    if r["frac_oracle"]:
        agg[(r["selector"], int(r["k"]))].append(float(r["frac_oracle"]))
sels = ["frequency", "sta_greedy", "sta_rules", "llm"]
fig, ax = plt.subplots(figsize=(5, 3.2))
for s in sels:
    ks = sorted(k for (x, k) in agg if x == s)
    ax.plot(ks, [sum(agg[(s, k)]) / len(agg[(s, k)]) for k in ks], marker="o", label=s)
ax.set_xlabel("budget k (new cells)"); ax.set_ylabel("mean fraction of oracle ΔWNS")
ax.set_ylim(0, 1.05); ax.legend(frameon=False); fig.tight_layout(); fig.savefig("results/tier2.png", dpi=200)

t1 = list(csv.DictReader(open("results/tier1.csv")))
fig, ax = plt.subplots(figsize=(5, 3.2))
ax.bar([r["design"] for r in t1], [float(r["overlap10"]) for r in t1])
ax.set_ylabel("overlap@10 (freq vs. criticality)"); ax.tick_params(axis="x", rotation=45)
fig.tight_layout(); fig.savefig("results/tier1.png", dpi=200)
```

Run: `python3 plots.py` → Expected: `results/tier1.png`, `results/tier2.png`.

- [ ] **Step 7: Write `results/README.md`** with: designs run/dropped, Tier 1 table, Tier 2 mean frac per selector per k, the two proposal sentences with actual numbers, and the stated limitations (pre-layout, ratio-calibrated, single corner, LLM run once per design×k).

- [ ] **Step 8: Commit** `git add -A && git commit -m "poc: tier2 driver, full results, plots"`
