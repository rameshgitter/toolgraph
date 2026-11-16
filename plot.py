#!/usr/bin/env python3
"""Render results/curve.json to results/curve.png (4 panels, direct labels, min-max band over seeds)."""
import json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC = sys.argv[1] if len(sys.argv) > 1 else "results/curve.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "results/curve.png"
d = json.load(open(SRC))
agg, n_seeds = d["agg"], len(d["per_seed"])

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
panels = [("success", "Task success on held-out tasks", "share of tasks with every needed tool pulled", (0, 1.02), "{:.0%}"),
          ("f1", "F1 of pulled context", "recall and precision combined", (0, 1.02), "{:.2f}"),
          ("tool_tokens", "Tool tokens loaded per task", "tokens of tool definitions in the window", None, "{:.0f}"),
          ("round_trips", "Model round trips before acting", "1 + 2 per unresolved entity", (0, 3), "{:.1f}")]

fig, axes = plt.subplots(2, 2, figsize=(11, 7.2), facecolor=SURFACE)
for ax, (m, title, sub, ylim, fmt) in zip(axes.flat, panels):
    ax.set_facecolor(SURFACE)
    g, fl, fs = agg["graph_learning"][m], agg["flat_learning"][m], agg["flat_static"][m]
    x = list(range(len(g["mean"])))
    for s, c in ((g, BLUE), (fl, ORANGE)):
        ax.fill_between(x, s["min"], s["max"], color=c, alpha=0.14, linewidth=0)
    ax.plot(x, [g["mean"][0]] * len(x), color=YELLOW, lw=2, ls=(0, (6, 3)))
    ax.plot(x, fs["mean"], color=AQUA, lw=2, ls=(0, (1, 2)))
    ax.plot(x, fl["mean"], color=ORANGE, lw=2)
    ax.plot(x, g["mean"], color=BLUE, lw=2.4)
    for xi in (0, x[-1]):
        ax.plot([xi], [g["mean"][xi]], "o", ms=7, color=BLUE, mec=SURFACE, mew=2)
        ax.plot([xi], [fl["mean"][xi]], "o", ms=7, color=ORANGE, mec=SURFACE, mew=2)
    ends = [(g["mean"][-1], "Graph + learning", BLUE), (fl["mean"][-1], "Flat table + learning", ORANGE),
            (fs["mean"][-1], "Flat table, static", AQUA), (g["mean"][0], "Graph, static", YELLOW)]
    ends.sort(key=lambda e: e[0])
    lo, hi = ax.get_ylim() if ylim is None else ylim
    gap = (hi - lo) * 0.07
    placed = []
    for v, name, c in ends:           # nudge labels apart
        y = v if not placed or v - placed[-1] >= gap else placed[-1] + gap
        placed.append(y)
        ax.annotate(name, (x[-1], v), xytext=(x[-1] + 0.4, y), color=INK, fontsize=8.5, va="center", annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", color=c, lw=0.8, shrinkA=0, shrinkB=2) if abs(y - v) > 1e-9 else None)
    if ylim:
        ax.set_ylim(*ylim)
    ax.set_xlim(0, x[-1])
    ax.set_title(title, loc="left", fontsize=11.5, color=INK, fontweight="bold", pad=18)
    ax.text(0, 1.03, sub, transform=ax.transAxes, fontsize=8.5, color=INK2)
    ax.set_xlabel("training round (15 tasks each)", fontsize=8.5, color=INK2)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5, length=0)
    if "%" in fmt:
        ax.set_yticks([0, .25, .5, .75, 1]); ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
fig.suptitle(f"ToolGraph on HydraDB: accuracy rises with use  (mean of {n_seeds} seeds, band = min to max, {d['n_test']} held-out tasks)",
             x=0.01, ha="left", fontsize=12.5, color=INK, fontweight="bold")
fig.tight_layout(rect=(0, 0, 0.93, 0.95))
fig.subplots_adjust(right=0.82)
fig.savefig(OUT, dpi=170, facecolor=SURFACE)
print("wrote", OUT)
