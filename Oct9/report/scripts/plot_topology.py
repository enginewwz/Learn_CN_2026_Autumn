#!/usr/bin/env python3
"""画 Oct9 bufferbloat 的实验拓扑与带宽时隙表（输出 Oct9/report/topology.png）。

三个面板：
  1) 实验1 复现：h1—r1—h2，瓶颈在 r1-eth1（10 Mbit + maxq 包缓冲）
  2) 实验2/3/4：同样三节点，两条 100 Mbit，AQM 装在 r1-eth1
  3) dynamic_bw 的带宽时隙表（100/10/1/50/1/100 Mbit）
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "topology.png")

BLUE = "#1f618d"
RED = "#c0392b"
GREEN = "#1e8449"
GRAY = "#5d6d7e"
LIGHT = "#eaf2f8"

for cand in ("Noto Sans CJK SC", "Noto Serif CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans"):
    if any(f.name == cand for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [cand]
        break
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150

BOW, BOH = 0.155, 0.17
XY = {"h1": 0.09, "r1": 0.50, "h2": 0.91}


def node(ax, name, x, y, role, color=BLUE, face="white"):
    ax.add_patch(FancyBboxPatch((x - BOW / 2, y - BOH / 2), BOW, BOH,
                                boxstyle="round,pad=0.006,rounding_size=0.02",
                                facecolor=face, edgecolor=color, lw=2.0, zorder=4))
    ax.text(x, y, name, ha="center", va="center", fontsize=15,
            fontweight="bold", color=color, zorder=5)
    ax.text(x, y - BOH / 2 - 0.035, role, ha="center", va="top", fontsize=9,
            color=GRAY, zorder=5, linespacing=1.35)


def link(ax, x1, x2, y, top, color=GRAY, lw=2.0):
    ax.add_patch(FancyArrowPatch((x1 + BOW / 2, y), (x2 - BOW / 2, y),
                                 arrowstyle="-", lw=lw, color=color, zorder=2))
    ax.text((x1 + x2) / 2, y + 0.105, top, ha="center", va="bottom",
            fontsize=10.5, color=color)


def callout(ax, x, y, text, width=0.64, color=RED):
    ax.add_patch(FancyBboxPatch((x - width / 2, y - 0.095), width, 0.19,
                                boxstyle="round,pad=0.014,rounding_size=0.03",
                                facecolor="#fdf2f2", edgecolor=color, lw=1.6, zorder=6))
    ax.text(x, y, text, ha="center", va="center", fontsize=10, color=color,
            zorder=7, linespacing=1.6)


def main():
    fig = plt.figure(figsize=(10.0, 8.6))
    gs = fig.add_gridspec(3, 1, height_ratios=[1.0, 1.0, 0.95], hspace=0.40)

    # ---------------- 面板 1：复现 ----------------
    ax = fig.add_subplot(gs[0])
    ax.set_xlim(0, 1), ax.set_ylim(0, 1), ax.axis("off")
    y = 0.70
    node(ax, "h1", XY["h1"], y, "发送端")
    node(ax, "r1", XY["r1"], y, "路由器（开启 IP 转发）", color=GREEN, face=LIGHT)
    node(ax, "h2", XY["h2"], y, "接收端")
    link(ax, XY["h1"], XY["r1"], y, "延迟 5 ms")
    link(ax, XY["r1"], XY["h2"], y, "带宽 10 Mbit/s，延迟 5 ms", color=RED, lw=3.0)
    callout(ax, 0.50, 0.32, "瓶颈缓冲：max_queue_size = maxq 个包")
    ax.annotate("", xy=(0.68, y), xytext=(0.58, 0.42),
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.6))
    ax.text(0.50, 0.13, "链路：h1-eth0 ── r1-eth0（延迟 5 ms）　｜　"
            "r1-eth1 ── h2-eth0（带宽 10 Mbit/s + 延迟 5 ms，瓶颈）",
            ha="center", va="center", fontsize=9, color=GRAY)
    ax.text(0.50, 0.95, "实验 1：复现 Bufferbloat　h1 ──iperf──▶ h2，同时 h1 ping h2 测 RTT",
            ha="center", va="center", fontsize=12.5, fontweight="bold")

    # ---------------- 面板 2：三种 AQM ----------------
    ax = fig.add_subplot(gs[1])
    ax.set_xlim(0, 1), ax.set_ylim(0, 1), ax.axis("off")
    y = 0.65
    node(ax, "h1", XY["h1"], y, "发送端")
    node(ax, "r1", XY["r1"], y, "路由器（开启 IP 转发）", color=GREEN, face=LIGHT)
    node(ax, "h2", XY["h2"], y, "接收端")
    link(ax, XY["h1"], XY["r1"], y, "带宽 100 Mbit/s")
    link(ax, XY["r1"], XY["h2"], y, "带宽 100 Mbit/s（速率每 10 s 变一次）",
         color=RED, lw=3.0)
    callout(ax, 0.50, 0.30, "队列规则（AQM）装在这里：\ntaildrop（默认 pfifo） / RED / CoDel")
    ax.annotate("", xy=(0.68, y), xytext=(0.58, 0.40),
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.6))
    ax.text(0.50, 0.11, "链路：h1-eth0 ── r1-eth0（100 Mbit/s）　｜　"
            "r1-eth1 ── h2-eth0（瓶颈，100 Mbit/s，速率时隙见下排）",
            ha="center", va="center", fontsize=9, color=GRAY)
    ax.text(0.50, 0.95, "实验 2/3/4：同一拓扑下切换瓶颈链路的队列规则",
            ha="center", va="center", fontsize=12.5, fontweight="bold")

    # ---------------- 面板 3：带宽时隙表 ----------------
    ax = fig.add_subplot(gs[2])
    phases = [(0, 10, 100), (10, 20, 10), (20, 30, 1), (30, 40, 50), (40, 50, 1), (50, 60, 100)]
    for a, b, v in phases:
        ax.plot([a, b], [v, v], color=BLUE, lw=2.6)
        ax.text((a + b) / 2, v + 4, "%d Mbit/s" % v, ha="center", va="bottom",
                fontsize=10.5, color=BLUE)
    for a, _, _ in phases[1:]:
        ax.axvline(a, color="#d5d8dc", lw=1.0, ls=":")
    ax.set_xlim(0, 60), ax.set_ylim(0, 135)
    ax.set_xlabel("时间 (s)")
    ax.set_ylabel("瓶颈带宽 (Mbit/s)")
    ax.set_yticks([0, 10, 50, 100])
    ax.grid(True, ls=":", lw=0.7, alpha=0.7)
    ax.set_title("dynamic_bw：瓶颈链路带宽的时隙表（每 10 s 切换一次，用来逼出排队）",
                 fontsize=12, pad=8)

    fig.savefig(OUT, bbox_inches="tight")
    plt.close(fig)
    print("写出 %s" % OUT)


if __name__ == "__main__":
    sys.exit(main())
