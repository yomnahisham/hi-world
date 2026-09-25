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
    """index: {(inst, pin): [(key, iid, members, leaf_idx)]} for leaf pins."""
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
