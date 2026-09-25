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
