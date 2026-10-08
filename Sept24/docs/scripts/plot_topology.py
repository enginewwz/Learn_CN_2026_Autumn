#!/usr/bin/python3
"""绘制 seven_node_mesh.py 的七节点拓扑图。

运行方式（在仓库根目录下）：

    MPLCONFIGDIR=/tmp/mpl python3 Sept24/docs/scripts/plot_topology.py

输出：Sept24/docs/topology-seven-node.png
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
DOC_DIR = os.path.dirname(HERE)
OUT = os.path.join(DOC_DIR, "topology-seven-node.png")

for cand in ("Noto Sans CJK SC", "Noto Serif CJK SC", "WenQuanYi Zen Hei"):
    if any(f.name == cand for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [cand]
        break
plt.rcParams["axes.unicode_minus"] = False

# 节点位置（画布坐标 0~1）
POS = {
    "b1": (0.36, 0.70),
    "b2": (0.78, 0.88),
    "b3": (0.10, 0.88),
    "b4": (0.36, 0.38),
    "b5": (0.88, 0.52),
    "b6": (0.64, 0.14),
    "b7": (0.10, 0.14),
}
LABEL = {"b1": "b1\n根桥"}

# 收敛后承载转发的 6 条链路（生成树）
TREE = [("b1", "b2"), ("b1", "b3"), ("b1", "b4"),
        ("b2", "b5"), ("b4", "b6"), ("b4", "b7")]
# 3 条冗余链路（收敛后被逻辑阻塞）
REDUNDANT = [("b2", "b3"), ("b4", "b5"), ("b5", "b6")]

BW, BH = 0.16, 0.12
BLUE = "#2471a3"
RED = "#c0392b"


def draw_box(ax, name):
    x, y = POS[name]
    ax.add_patch(Rectangle((x - BW / 2, y - BH / 2), BW, BH, facecolor="white",
                           edgecolor=BLUE, lw=1.8, zorder=3))
    ax.text(x, y, LABEL.get(name, name), ha="center", va="center",
            fontsize=10, zorder=4, linespacing=1.4)


def draw_edge(ax, a, b, color, ls, lw):
    ax.add_patch(FancyArrowPatch(POS[a], POS[b], arrowstyle="-", color=color,
                                 lw=lw, ls=ls, zorder=2, shrinkA=0, shrinkB=0))


def main():
    fig, ax = plt.subplots(figsize=(6.6, 4.4))

    for a, b in TREE:
        draw_edge(ax, a, b, BLUE, "-", 1.8)
    for a, b in REDUNDANT:
        draw_edge(ax, a, b, RED, "--", 1.8)

    for name in POS:
        draw_box(ax, name)

    handles = [
        Line2D([0], [0], color=BLUE, lw=1.8, label="承载转发的链路（6 条）"),
        Line2D([0], [0], color=RED, lw=1.8, ls="--", label="冗余链路（3 条，收敛后被阻塞）"),
    ]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.0),
              ncol=2, frameon=False, fontsize=9)

    ax.set_xlim(0, 1)
    ax.set_ylim(-0.20, 1.02)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(OUT, dpi=160)
    plt.close(fig)
    print("已输出", OUT)


if __name__ == "__main__":
    main()
