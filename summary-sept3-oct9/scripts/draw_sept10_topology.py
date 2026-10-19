#!/usr/bin/env python3
"""Draw the Sep10 h2-s1-h1 topology in the style of topology_ring.png."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "figures" / "sept10-topology.png"

HOST = "#2F6FB0"
SWITCH = "#C97A1E"
INK = "#111827"
MUTED = "#6B7280"

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

fig = plt.figure(figsize=(7.0, 1.65), dpi=300)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 1400)
ax.set_ylim(0, 330)
ax.axis("off")
ax.set_facecolor("white")
fig.patch.set_facecolor("white")

ax.text(700, 302, "topo.py：两个主机 + 一个 OVS 交换机",
        ha="center", va="center", fontsize=15, color=INK)

boxes = [
    (50, 105, 220, 88, HOST, "h2 客户端\n10.0.0.2"),
    (455, 105, 200, 88, SWITCH, "s1"),
    (860, 105, 220, 88, HOST, "h1 服务器\n10.0.0.1"),
]
for x, y, w, h, color, label in boxes:
    ax.add_patch(Rectangle((x, y), w, h, facecolor="white", edgecolor=color,
                           linewidth=2.6, joinstyle="miter"))
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
            fontsize=12, color=INK, linespacing=1.35)

# h2-s1 and s1-h1 links
ax.plot([270, 455], [149, 149], color="black", lw=2.6, solid_capstyle="butt")
ax.plot([655, 860], [149, 149], color="black", lw=2.6, solid_capstyle="butt")
ax.text(757, 205, "100 Mbit/s，10 ms", ha="center", va="bottom",
        fontsize=9.5, color=MUTED)

fig.savefig(OUT, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=0.03)
print("saved", OUT)
