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
