#!/usr/bin/python3
"""根据 hub/result.md 与 switch/result.md 中的 iperf 日志绘制性能对比图。

运行方式（在仓库根目录下）：

    python3 Sept17/report/scripts/plot_performance.py
"""

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.dirname(HERE)
FIG_DIR = os.path.join(REPORT_DIR, "figures")
DATA_DIR = os.path.join(REPORT_DIR, "data")
os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

for cand in ("Noto Sans CJK SC", "Noto Serif CJK SC", "WenQuanYi Zen Hei"):
    if any(f.name == cand for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [cand]
        break
plt.rcParams["axes.unicode_minus"] = False

# iperf 日志中的原始数据：每秒兆比特。
# 场景 A 的 client 日志无法区分两条结果分别属于 H2 还是 H3，因此统一记为“流 1 / 流 2”；
# 场景 B 的 server 日志按 [1]/[2] 明确给出了对端地址，故使用具体方向。
DATA = {
    "hub": {
        "A": {"label": "流 1", "rate": 5.85, "transfer_mb": 21.3, "sec": 30.4909},
        "A2": {"label": "流 2", "rate": 2.99, "transfer_mb": 11.0, "sec": 30.8921},
        "B": {"label": "H2→H1", "rate": 8.38, "transfer_mb": 30.6, "sec": 30.6453},
        "B2": {"label": "H3→H1", "rate": 8.26, "transfer_mb": 30.4, "sec": 30.8588},
    },
    "switch": {
        "A": {"label": "流 1", "rate": 9.38, "transfer_mb": 34.8, "sec": 31.0876},
        "A2": {"label": "流 2", "rate": 9.40, "transfer_mb": 35.0, "sec": 31.2286},
        "B": {"label": "H2→H1", "rate": 9.29, "transfer_mb": 34.4, "sec": 31.0278},
        "B2": {"label": "H3→H1", "rate": 9.30, "transfer_mb": 34.3, "sec": 30.9100},
    },
}

for proto in ("hub", "switch"):
    d = DATA[proto]
    d["sum_A"] = d["A"]["rate"] + d["A2"]["rate"]
    d["sum_B"] = d["B"]["rate"] + d["B2"]["rate"]

SUMMARY = {
    "hub": {"A": DATA["hub"]["sum_A"], "B": DATA["hub"]["sum_B"]},
    "switch": {"A": DATA["switch"]["sum_A"], "B": DATA["switch"]["sum_B"]},
    "speedup_A": DATA["switch"]["sum_A"] / DATA["hub"]["sum_A"],
    "speedup_B": DATA["switch"]["sum_B"] / DATA["hub"]["sum_B"],
    "hub_asymmetry": DATA["hub"]["sum_B"] / DATA["hub"]["sum_A"],
    "switch_asymmetry": DATA["switch"]["sum_B"] / DATA["switch"]["sum_A"],
}
with open(os.path.join(DATA_DIR, "performance_summary.json"), "w") as f:
    json.dump({"data": DATA, "summary": SUMMARY}, f, ensure_ascii=False, indent=1)


def plot_grouped_bars():
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    width = 0.35
    colors = {"hub": "#e67e22", "switch": "#2471a3"}

    for ax, scen, title in (
        (axes[0], "A", "场景 A：H1 同时向 H2、H3 发送"),
        (axes[1], "B", "场景 B：H2、H3 同时向 H1 发送"),
    ):
        flows = ("A", "A2") if scen == "A" else ("B", "B2")
        x = [0, 1]
        for i, proto in enumerate(("hub", "switch")):
            vals = [DATA[proto][f]["rate"] for f in flows]
            pos = [p + (i - 0.5) * width for p in x]
            bars = ax.bar(pos, vals, width, label="集线器 Hub" if proto == "hub"
                          else "交换机 Switch", color=colors[proto])
            for b, v in zip(bars, vals):
                ax.text(b.get_x() + b.get_width() / 2, v + 0.15, f"{v:.2f}",
                        ha="center", va="bottom", fontsize=8.5)
        ax.set_xticks(x)
        ax.set_xticklabels([DATA["hub"][flows[0]]["label"],
                            DATA["hub"][flows[1]]["label"]])
        ax.set_xlim(-0.6, 1.6)
        ax.set_ylim(0, 22)
        ax.axhline(10, color="gray", ls="--", lw=1)
        ax.axhline(20, color="red", ls=":", lw=1)
        ax.text(-0.55, 20.3, "20 Mbit/s：H1 链路容量", color="red", fontsize=8)
        ax.text(-0.55, 10.3, "10 Mbit/s：接入链路容量", color="gray", fontsize=8)
        ax.set_title(title)
        ax.set_ylabel("吞吐量 (Mbit/s)")
        ax.grid(axis="y", alpha=0.3)
        ax.legend(loc="upper right", fontsize=9)
    fig.subplots_adjust(right=0.98, wspace=0.12)
    fig.suptitle("集线器广播与交换机转发的 iperf 吞吐对比", y=0.99)
    fig.savefig(os.path.join(FIG_DIR, "perf_hub_vs_switch.png"), dpi=160)
    plt.close(fig)


def plot_aggregate():
    fig, ax = plt.subplots(figsize=(7.8, 4.4))
    labels = ["场景 A\n(H1→H2, H1→H3)", "场景 B\n(H2→H1, H3→H1)"]
    hub = [SUMMARY["hub"]["A"], SUMMARY["hub"]["B"]]
    sw = [SUMMARY["switch"]["A"], SUMMARY["switch"]["B"]]
    y = [1, 0]
    h = 0.32
    b1 = ax.barh([p + h / 2 for p in y], hub, h, label="集线器 Hub",
                 color="#e67e22")
    b2 = ax.barh([p - h / 2 for p in y], sw, h, label="交换机 Switch",
                 color="#2471a3")
    for bars, vals in ((b1, hub), (b2, sw)):
        for b, v in zip(bars, vals):
            ax.text(v + 0.3, b.get_y() + b.get_height() / 2, f"{v:.2f}",
                    va="center", ha="left", fontsize=9)
    for yi, hb, sb, ratio in ((1, hub[0], sw[0], SUMMARY["speedup_A"]),
                              (0, hub[1], sw[1], SUMMARY["speedup_B"])):
        ax.text(21.3, yi, f"×{ratio:.2f}", va="center",
                fontsize=10, fontweight="bold", color="#555")
    ax.axvline(20, color="red", ls=":", lw=1)
    ax.text(20.2, 1.45, "H1 链路容量 20 Mbit/s", color="red", fontsize=8.5,
            ha="left", va="top")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_ylim(-0.35, 1.6)
    ax.set_xlim(0, 26)
    ax.set_xlabel("两条 iperf 流合计吞吐 (Mbit/s)")
    ax.set_title("合计吞吐与提速倍数（Switch / Hub）")
    ax.grid(axis="x", alpha=0.3)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2,
              frameon=False)
    fig.subplots_adjust(left=0.26, right=0.98, bottom=0.26, top=0.9)
    fig.savefig(os.path.join(FIG_DIR, "perf_aggregate_speedup.png"), dpi=160)
    plt.close(fig)


def _box(ax, x, y, w, h, text, color):
    ax.add_patch(Rectangle((x, y), w, h, facecolor="white", edgecolor=color,
                           lw=2, zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=10,
            zorder=4)


def _link(ax, p, q, color="black", lw=2, ls="-"):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-", color=color, lw=lw,
                                 ls=ls, zorder=2, shrinkA=0, shrinkB=0))


def _draw_three_nodes(ax):
    ax.set_title("three_nodes_bw.py：三节点带带宽链路")
    _box(ax, 0.05, 0.62, 0.24, 0.2, "h1\n10.0.0.1", "#2471a3")
    _box(ax, 0.05, 0.18, 0.24, 0.2, "h2\n10.0.0.2", "#2471a3")
    _box(ax, 0.71, 0.62, 0.24, 0.2, "h3\n10.0.0.3", "#2471a3")
    _box(ax, 0.4, 0.4, 0.2, 0.2, "b1 / s1", "#e67e22")
    _link(ax, (0.29, 0.72), (0.4, 0.55))
    _link(ax, (0.29, 0.28), (0.4, 0.45))
    _link(ax, (0.6, 0.5), (0.71, 0.7))
    ax.text(0.31, 0.755, "20M", fontsize=8, color="#555")
    ax.text(0.3, 0.24, "10M", fontsize=8, color="#555")
    ax.text(0.62, 0.66, "10M", fontsize=8, color="#555")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")


def _draw_ring(ax):
    ax.set_title("ring_topo.py：三个 Hub 组成环路")
    _box(ax, 0.03, 0.6, 0.22, 0.2, "h1\n10.0.0.1", "#2471a3")
    _box(ax, 0.75, 0.6, 0.22, 0.2, "h2\n10.0.0.2", "#2471a3")
    _box(ax, 0.33, 0.72, 0.22, 0.18, "b1", "#e67e22")
    _box(ax, 0.33, 0.10, 0.22, 0.18, "b2", "#e67e22")
    _box(ax, 0.05, 0.28, 0.22, 0.18, "b3", "#e67e22")
    _link(ax, (0.25, 0.7), (0.33, 0.79))
    _link(ax, (0.55, 0.79), (0.75, 0.7))
    _link(ax, (0.44, 0.72), (0.44, 0.28), color="#c0392b", lw=2.4)
    _link(ax, (0.33, 0.72), (0.27, 0.46), color="#c0392b", lw=2.4)
    _link(ax, (0.27, 0.28), (0.33, 0.19), color="#c0392b", lw=2.4, ls="--")
    _link(ax, (0.44, 0.19), (0.44, 0.28), color="#c0392b", lw=2.4, ls="--")
    ax.text(0.45, 0.5, "环路", color="#c0392b", fontsize=10)
    ax.text(0.6, 0.86, "直连", fontsize=8, color="#555")
    ax.text(0.02, 0.5, "绕行", fontsize=8, color="#c0392b")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")


def plot_topologies():
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    _draw_three_nodes(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, "topology_three_nodes.png"), dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    _draw_ring(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, "topology_ring.png"), dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    plot_grouped_bars()
    plot_aggregate()
    plot_topologies()
    print(json.dumps(SUMMARY, ensure_ascii=False, indent=1))
