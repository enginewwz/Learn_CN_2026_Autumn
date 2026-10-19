"""Shared drawing helpers for the Socket epoll/FSM figures."""

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

for cand in ("Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Zen Hei"):
    if any(f.name == cand for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.sans-serif"] = [cand]
        break
plt.rcParams["axes.unicode_minus"] = False

INK = "#24303F"
MUTED = "#5E6B7A"
EDGE = "#D6DFE8"
PANEL = "#F8FAFC"
BOXBG = "#FCFDFE"

BLUE, BLUE_BG = "#2F6FB0", "#EAF2FA"
PURPLE, PURPLE_BG = "#6F5AA8", "#F1EDF8"
GREEN, GREEN_BG = "#2E8B57", "#EAF5EE"
ORANGE, ORANGE_BG = "#C97A1E", "#FCF0E1"
RED, RED_BG = "#B94A48", "#FAEBEA"
GRAY, GRAY_BG = "#5F6B7A", "#F2F4F7"


def canvas():
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.set_xlim(0, 160)
    ax.set_ylim(0, 90)
    ax.axis("off")
    fig.patch.set_facecolor("white")
    return fig, ax


def rounded(ax, x, y, w, h, fc, ec, lw=1.6, radius=1.2, shadow=True, z=3,
            ls="-"):
    if shadow:
        ax.add_patch(FancyBboxPatch(
            (x + 0.4, y - 0.4), w, h,
            boxstyle=f"round,pad=0.0,rounding_size={radius}",
            linewidth=0, facecolor="#E8EDF3", zorder=z - 1,
        ))
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.0,rounding_size={radius}",
        linewidth=lw, edgecolor=ec, facecolor=fc, linestyle=ls, zorder=z,
    ))


def block(ax, x, y, w, h, title, desc="", color=GRAY, bg=GRAY_BG,
          title_fs=11.0, desc_fs=8.7, align="center", shadow=False):
    rounded(ax, x, y, w, h, bg, color, radius=1.2, shadow=shadow)
    cx = x + w / 2 if align == "center" else x + 2.2
    ha = "center" if align == "center" else "left"
    top = y + h
    if desc:
        ax.text(cx, top - 2.0, title, ha=ha, va="top",
                fontsize=title_fs, color=INK, weight="bold", zorder=5)
        ax.text(cx, top - 4.6, desc, ha=ha, va="top",
                fontsize=desc_fs, color=MUTED, linespacing=1.3, zorder=5)
    else:
        ax.text(cx, y + h / 2, title, ha=ha, va="center",
                fontsize=title_fs, color=INK, weight="bold", zorder=5)


def pill(ax, x, y, w, h, text, color=GRAY, bg=GRAY_BG, fs=8.6, ls="-"):
    rounded(ax, x, y, w, h, bg, color, lw=1.2, radius=1.4, shadow=False, z=3,
            ls=ls)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=INK, zorder=5)


def arrow(ax, p, q, color=MUTED, lw=1.8, ls="-", z=2, rad=0.0):
    ax.add_patch(FancyArrowPatch(
        p, q, arrowstyle="-|>", mutation_scale=13, linewidth=lw,
        color=color, linestyle=ls, zorder=z, shrinkA=2, shrinkB=2,
        connectionstyle=f"arc3,rad={rad}",
    ))


def label(ax, x, y, text, fs=8.8, color=MUTED, ha="center", va="center", z=6):
    ax.text(x, y, text, ha=ha, va=va, fontsize=fs, color=color, zorder=z,
            linespacing=1.2,
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.94, pad=1.2))


def save(fig, name):
    for ext in ("png", "svg", "pdf"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=200, facecolor="white",
                    bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    print("saved", OUT / f"{name}.png")
