#!/usr/bin/env python3
"""Draw the port-multiplexing (single-thread epoll) state machine for the
Socket lab (Sept10).  Outputs PNG + SVG into ../figures/.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "figures"
OUT.mkdir(exist_ok=True)

# Chinese font
for cand in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei"):
    if any(f.name == cand for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [cand]
        break
plt.rcParams["axes.unicode_minus"] = False

# palette
BLUE = "#2F6FB0"
BLUE_BG = "#EAF2FA"
GREEN = "#2E8B57"
GREEN_BG = "#EAF5EE"
ORANGE = "#C97A1E"
ORANGE_BG = "#FCF0E1"
RED = "#B94A48"
RED_BG = "#FAEBEA"
PURPLE = "#6F5AA8"
PURPLE_BG = "#F1EDF8"
GRAY = "#5F6B7A"
GRAY_BG = "#F2F4F7"
INK = "#24303F"
MUTED = "#5E6B7A"
PANEL = "#FAFBFD"
PANEL_EDGE = "#D9E1EA"


def box(ax, x, y, w, h, text, edge, face, fs=10.5, lw=1.6, ls="-",
        color=INK, weight="normal", z=3, rounding=0.9, pad=0.35, va="center"):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad={pad},rounding_size={rounding}",
        linewidth=lw, edgecolor=edge, facecolor=face, linestyle=ls, zorder=z,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va=va, fontsize=fs,
            color=color, weight=weight, zorder=z + 1, linespacing=1.18)
    return (x, y, w, h)


def panel(ax, x, y, w, h, title=None, fs=11.5):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.45,rounding_size=1.4",
        linewidth=1.0, edgecolor=PANEL_EDGE, facecolor=PANEL, zorder=0,
    ))
    if title:
        ax.text(x + 1.2, y + h - 2.0, title, ha="left", va="center",
                fontsize=fs, color=INK, weight="bold", zorder=1)


def arrow(ax, p, q, color=GRAY, lw=1.9, ls="-", style="-|>", rad=0.0,
          z=1.5, shrink=2.0, ms=12):
    ax.add_patch(FancyArrowPatch(
        p, q, arrowstyle=style, mutation_scale=ms, linewidth=lw,
        color=color, linestyle=ls, zorder=z, shrinkA=shrink, shrinkB=shrink,
        connectionstyle=f"arc3,rad={rad}",
    ))


def label(ax, x, y, text, fs=8.8, color=MUTED, ha="center", va="center",
          weight="normal", bg=True, z=6):
    kw = {}
    if bg:
        kw["bbox"] = dict(facecolor="white", edgecolor="none", alpha=0.92, pad=1.2)
    ax.text(x, y, text, ha=ha, va=va, fontsize=fs, color=color,
            weight=weight, zorder=z, linespacing=1.15, **kw)


fig, ax = plt.subplots(figsize=(16, 9))
ax.set_xlim(0, 160)
ax.set_ylim(0, 90)
ax.axis("off")
fig.patch.set_facecolor("white")

# ---------------------------------------------------------------------------
# title
# ---------------------------------------------------------------------------
ax.text(80, 87.0, "端口复用：单线程 epoll 如何同时处理 80 / 443 与多个连接",
        ha="center", va="center", fontsize=21, color=INK, weight="bold")
ax.text(80, 83.4,
        "每个连接一个独立状态机；epoll 只把“就绪”的连接交给 drive()，谁就绪就推进谁",
        ha="center", va="center", fontsize=11.5, color=MUTED)

# ---------------------------------------------------------------------------
# 1) event dispatch
# ---------------------------------------------------------------------------
panel(ax, 3, 64.5, 154, 15.0)
ax.text(4.2, 80.9, "① 事件分发：两个监听 fd + 所有 client fd 共用同一个 epoll",
        ha="left", va="center", fontsize=10.8, color=INK, weight="bold", zorder=2)

box(ax, 5, 68.6, 20, 6.2, "epoll_wait()", BLUE, BLUE_BG, fs=11.5, weight="bold")
label(ax, 15, 66.8, "无事件则睡眠；有事件返回就绪列表", fs=8.2)

# split
ax.plot([25, 29], [71.7, 71.7], color=BLUE, lw=1.9, zorder=1.5)
ax.plot([29, 29], [69.4, 76.6], color=BLUE, lw=1.9, zorder=1.5)
arrow(ax, (29, 76.6), (33, 76.6), color=BLUE)
arrow(ax, (29, 69.4), (33, 69.4), color=BLUE)
label(ax, 30.8, 77.6, "监听 fd 就绪", fs=8.2, color=BLUE)
label(ax, 30.8, 68.2, "连接 fd 就绪", fs=8.2, color=GREEN)

box(ax, 33, 74.2, 17, 5.0, "监听 fd\n80 / 443", BLUE, BLUE_BG, fs=9.6)
box(ax, 53, 74.2, 19, 5.0, "accept_clients()\n每次最多 64 个", BLUE, BLUE_BG, fs=9.2)
box(ax, 75, 74.2, 24, 5.0, "new connection\nHTTP→READING\nHTTPS→HANDSHAKE",
    PURPLE, PURPLE_BG, fs=8.5)
box(ax, 102, 74.2, 20, 5.0, "EPOLL_CTL_ADD\nEPOLLIN", GRAY, GRAY_BG, fs=9.0)
ax.text(112, 73.2, "→ 下一轮 epoll_wait", ha="center", va="center",
        fontsize=8.0, color=BLUE, zorder=6)

box(ax, 33, 67.0, 17, 4.6, "连接 fd\n（已 accept）", GREEN, GREEN_BG, fs=9.3)
box(ax, 53, 67.0, 19, 4.6, "drive(c)", GREEN, GREEN_BG, fs=10.5,
    weight="bold")
arrow(ax, (62.5, 67.0), (62.5, 64.2), color=GREEN)
ax.text(64.0, 65.4, "→ ② 状态机", ha="left", va="center", fontsize=8.5,
        color=GREEN, zorder=6)

# listener path arrows
for x0, w in ((50, 3), (72, 3), (99, 3)):
    arrow(ax, (x0, 76.7), (x0 + w, 76.7), color=BLUE)
# note
ax.text(125.5, 77.0,
        "80/443 不是两个线程，\n它们只是 epoll 里的两个 listener fd；\n每个 client 连接也是独立 fd。",
        ha="left", va="center", fontsize=8.2, color=MUTED, linespacing=1.25,
        zorder=6)

# ---------------------------------------------------------------------------
# 2) per-connection state machine
# ---------------------------------------------------------------------------
panel(ax, 3, 33, 154, 30.5,
      "② 每个连接一个状态机：state、input/output 缓冲、文件偏移都独立")

# states
box(ax, 6, 48.2, 18, 6.4, "HANDSHAKE\nTLS 握手", PURPLE, PURPLE_BG, fs=10.0)
box(ax, 32, 48.2, 18, 6.4, "READING\n收 HTTP 请求", GREEN, GREEN_BG, fs=10.0)
box(ax, 58, 48.2, 20, 6.4, "WRITING\n发响应 / 文件", ORANGE, ORANGE_BG, fs=10.0)
box(ax, 86, 48.2, 18, 6.4, "CLOSING\nTLS 关闭", RED, RED_BG, fs=10.0)
box(ax, 112, 48.2, 22, 6.4, "drop(c)\n释放资源", GRAY, GRAY_BG, fs=10.0)

# horizontal transitions
arrow(ax, (24, 51.4), (32, 51.4), color=PURPLE)
label(ax, 28, 57.2, "SSL_accept\n成功", fs=8.2)
arrow(ax, (50, 51.4), (58, 51.4), color=GREEN)
label(ax, 54, 57.2, "\\r\\n\\r\\n 到齐\nbuild_response()", fs=8.2)
arrow(ax, (78, 51.4), (86, 51.4), color=ORANGE)
label(ax, 82, 57.2, "output 空且\nfile_remaining=0", fs=8.2)
arrow(ax, (104, 51.4), (112, 51.4), color=RED)
label(ax, 108, 57.2, "SSL_shutdown\n→ -1", fs=8.2)

# read_file_chunk loop
box(ax, 58, 37.6, 20, 5.4, "read_file_chunk()\npread 8 KB", ORANGE, ORANGE_BG, fs=9.4)
arrow(ax, (68, 48.2), (68, 43.0), color=ORANGE)
label(ax, 70.5, 45.5, "output 空\n且还有文件", fs=8.0, ha="left")
arrow(ax, (58, 40.3), (54, 40.3), color=ORANGE, ls="--", rad=0.0)
ax.plot([54, 54], [40.3, 48.2], color=ORANGE, lw=1.9, ls="--", zorder=1.5)
arrow(ax, (54, 48.2), (58, 48.2), color=ORANGE, ls="--")
label(ax, 51.8, 44.2, "读下一块", fs=8.0, color=ORANGE, ha="right")

# wait note
ax.text(124, 38.8,
        "等待不是阻塞：\nEAGAIN / WANT_READ / WANT_WRITE\n→ 修改 epoll 关注事件并 return；\n下次事件从原状态继续。\n状态、缓冲区、文件偏移都不丢。",
        ha="left", va="center", fontsize=8.6, color=MUTED, linespacing=1.35,
        zorder=6, bbox=dict(boxstyle="round,pad=0.55", facecolor="#F2F4F7",
                            edgecolor="#D9E1EA", linewidth=0.9))
ax.text(124, 46.0, "任一阶段错误 / 对端关闭 → drop(c)", ha="left", va="center",
        fontsize=8.2, color=RED, zorder=6)

# 64 KiB note
ax.text(80, 60.2,
        "连续写满 64 KiB → MOD EPOLLOUT，主动让出，避免大文件独占事件循环",
        ha="left", va="center", fontsize=8.4, color=ORANGE, zorder=6,
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.9, pad=1.0))

# ---------------------------------------------------------------------------
# 3) concurrency lanes
# ---------------------------------------------------------------------------
panel(ax, 3, 3, 154, 27,
      "③ 多个连接“同时发送”：单线程在就绪事件之间切换（示意）")


def pill(ax, x, y, w, h, text, edge, face, fs=8.3, ls="-", color=INK):
    box(ax, x, y, w, h, text, edge, face, fs=fs, lw=1.4, ls=ls, color=color,
        rounding=1.4, pad=0.25)


# lane A
ax.text(5.5, 20.2, "Conn A", ha="left", va="center", fontsize=9.5,
        color=INK, weight="bold")
pill(ax, 17, 18.2, 12, 4.0, "HANDSHAKE", PURPLE, PURPLE_BG)
pill(ax, 32, 18.2, 10, 4.0, "READING", GREEN, GREEN_BG)
pill(ax, 45, 18.2, 13, 4.0, "WRITING 8KB", ORANGE, ORANGE_BG)
pill(ax, 61, 18.2, 14, 4.0, "等 EPOLLOUT", GRAY, "white", ls="--", color=GRAY)
pill(ax, 78, 18.2, 13, 4.0, "WRITING 8KB", ORANGE, ORANGE_BG)
for x0, x1 in ((29, 32), (42, 45), (58, 61), (75, 78)):
    arrow(ax, (x0, 20.2), (x1, 20.2), color=GRAY, lw=1.3, ms=9)

# lane B
ax.text(5.5, 14.2, "Conn B", ha="left", va="center", fontsize=9.5,
        color=INK, weight="bold")
pill(ax, 17, 12.2, 10, 4.0, "READING", GREEN, GREEN_BG)
pill(ax, 30, 12.2, 11, 4.0, "WRITING", ORANGE, ORANGE_BG)
pill(ax, 44, 12.2, 13, 4.0, "64 KiB 让出", GRAY, "white", ls="--", color=GRAY)
pill(ax, 60, 12.2, 11, 4.0, "WRITING", ORANGE, ORANGE_BG)
for x0, x1 in ((27, 30), (41, 44), (57, 60)):
    arrow(ax, (x0, 14.2), (x1, 14.2), color=GRAY, lw=1.3, ms=9)

# lane C
ax.text(5.5, 8.2, "Conn C", ha="left", va="center", fontsize=9.5,
        color=INK, weight="bold")
pill(ax, 17, 6.2, 12, 4.0, "HANDSHAKE", PURPLE, PURPLE_BG)
pill(ax, 32, 6.2, 13, 4.0, "WANT_READ", GRAY, "white", ls="--", color=GRAY)
pill(ax, 48, 6.2, 12, 4.0, "HANDSHAKE", PURPLE, PURPLE_BG)
for x0, x1 in ((29, 32), (45, 48)):
    arrow(ax, (x0, 8.2), (x1, 8.2), color=GRAY, lw=1.3, ms=9)

# explanatory note in lane area
ax.text(97, 16.2,
        "A 在等 EPOLLOUT 时，B 的 EPOLLOUT、C 的 EPOLLIN\n"
        "仍然会被 epoll 返回；线程不会停在 A 上。\n"
        "同一时刻只有一个线程在跑，但多个连接各自“停在”\n"
        "不同的等待点，下一次事件来了就从原状态继续。",
        ha="left", va="center", fontsize=8.6, color=MUTED, linespacing=1.35,
        zorder=6, bbox=dict(boxstyle="round,pad=0.55", facecolor="white",
                            edgecolor="#D9E1EA", linewidth=0.9))

# legend / time arrow
ax.text(5.5, 4.3,
        "实线块 = 本次事件推进；虚线块 = 等待中（不占 CPU）；颜色对应状态",
        ha="left", va="center", fontsize=8.0, color=MUTED)
arrow(ax, (143, 4.5), (154, 4.5), color=GRAY, lw=1.2, ms=9)
ax.text(148.5, 5.6, "时间", ha="center", va="center", fontsize=8.2, color=MUTED)

fig.savefig(OUT / "02-socket-state-machine.png", dpi=220,
            facecolor="white", bbox_inches="tight", pad_inches=0.12)
fig.savefig(OUT / "02-socket-state-machine.svg", facecolor="white",
            bbox_inches="tight", pad_inches=0.12)
plt.close(fig)
print("saved", OUT / "02-socket-state-machine.png")
