"""Cheap-information selectors: frequency, STA-greedy, STA+rules, LLM (claude -p)."""
import json, os, re, subprocess
from collections import Counter
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
make the cell unbuildable (zero benefit). Library _1 sizing, unscaled stacks. The input most often entering on violating paths is placed
nearest the output. Fused-cell delay replaces the summed delay of the cluster's cells on each path that traverses it (per entry input).
Overlapping clusters on one path cannot both apply.

Design: {design}. Clock period {period} ns. {nviol} of the top {npaths} endpoints violate.

Worst paths (slack ns; cells along the path as cell_type:delay_ns; [K#] marks candidate cluster instances on the path):
{paths}

Candidates (id | key | inputs | function (x_i = i-th leaf input; the key lists leaves left to right, depth-first) | instances in design | summed delay on violating paths ns | #violating paths touched | violating traversals by entry input x_i):
{cands}

Reply with a short reasoning paragraph, then a JSON block exactly like:
```json
{{"picks": [{{"key": "<candidate key>", "why": "<one line>"}}]}}
```"""

def build_prompt(cl, pl, k, hits, design, period, lib):
    idx = {key: i for i, key in enumerate(pl)}
    viol = [h for h in hits if h["slack"] < 0]
    touched = {key: sum(1 for h in viol if any(x["key"] == key for x in h["hits"])) for key in pl}
    entries = {key: Counter(x["entry"] for h in viol for x in h["hits"] if x["key"] == key) for key in pl}
    cand_lines = []
    for key in pl:
        n, tt = cellgen.truth_table(cl[key]["tree"], lib)
        ent = " ".join(f"x{i}:{c}" for i, c in sorted(entries[key].items()))
        cand_lines.append(f"K{idx[key]} | {key} | {n} | {_sop_str(n, tt)} | {cl[key]['count']} | {cl[key]['crit']} | {touched[key]} | {ent}")
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
    if not isinstance(picks, list):
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
    env = {k_: v for k_, v in os.environ.items() if not k_.startswith("CLAUDE_CODE") and k_ != "CLAUDECODE"}
    ver = subprocess.run(["claude", "--version"], capture_output=True, text=True, env=env).stdout.strip()
    out = subprocess.run(["claude", "-p", "--output-format", "text"], input=prompt, env=env,
                         capture_output=True, text=True, timeout=900).stdout
    open(f"{base}.response.txt", "w").write(f"# claude {ver}\n{out}")
    picks = parse_llm(out, pl, k)
    if len(picks) < k:
        print(f"  llm returned {len(picks)}/{k} valid picks for {design} (logged)")
    return picks
