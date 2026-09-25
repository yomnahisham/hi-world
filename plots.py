"""Two static figures for the proposal: tier1 overlap, tier2 fraction of oracle."""
import csv
from collections import defaultdict
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

INK, INK2, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
SEL = {"frequency": "#2a78d6", "sta_greedy": "#eb6834", "sta_rules": "#1baf7a", "llm": "#eda100"}
plt.rcParams.update({"font.size": 9, "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.edgecolor": GRID, "axes.spines.top": False, "axes.spines.right": False})

rows = list(csv.DictReader(open("results/tier2.csv")))
agg = defaultdict(list)
for r in rows:
    if r["frac_oracle"] and r["selector"] in SEL:
        agg[(r["selector"], int(r["k"]))].append(min(float(r["frac_oracle"]), 1.5))
ks = sorted({k for _, k in agg})
fig, ax = plt.subplots(figsize=(6, 3.2))
w = 0.8 / len(SEL)
for i, (s, c) in enumerate(SEL.items()):
    xs = [j + (i - 1.5) * w for j in range(len(ks))]
    ys = [sum(agg[(s, k)]) / len(agg[(s, k)]) if agg[(s, k)] else 0 for k in ks]
    ax.bar(xs, ys, w - 0.02, color=c, label=s)
    for x, y in zip(xs, ys):
        ax.text(x, y + 0.02, f"{y:.2f}", ha="center", va="bottom", fontsize=7, color=INK2)
ax.set_xticks(range(len(ks)), [f"k = {k}" for k in ks])
ax.set_ylabel("mean fraction of oracle ΔWNS")
ax.set_ylim(0, 1.15); ax.yaxis.grid(True, color=GRID, lw=0.6); ax.set_axisbelow(True)
ax.legend(frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.15))
fig.tight_layout(); fig.savefig("results/tier2.png", dpi=200)

t1 = list(csv.DictReader(open("results/tier1.csv")))
fig, ax = plt.subplots(figsize=(6, 3.0))
names = [r["design"].replace("epfl_", "") for r in t1]
ys = [float(r["cov10"]) for r in t1]
ax.bar(names, ys, 0.6, color=SEL["frequency"])
for x, y in enumerate(ys):
    ax.text(x, y + 0.01, f"{y:.2f}", ha="center", va="bottom", fontsize=7, color=INK2)
ax.set_ylabel("violating-path delay covered\nby 10 most frequent clusters")
ax.set_ylim(0, max(ys + [0.1]) * 1.25); ax.yaxis.grid(True, color=GRID, lw=0.6); ax.set_axisbelow(True)
ax.tick_params(axis="x", rotation=30)
fig.tight_layout(); fig.savefig("results/tier1.png", dpi=200)
