"""Cluster function -> single-stage static CMOS realization -> ngspice ratio vs. original."""
import glob, json, os, re, subprocess, tempfile
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
        if not cubes or cubes == [(0, 0)]:
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
        top, vs = z, order(m)
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
    lines = ["* oracle", f'.lib "{PDK}/libs.tech/ngspice/sky130.lib.spice" tt', f'.include "{LIBSPICE}"', *subs,
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
