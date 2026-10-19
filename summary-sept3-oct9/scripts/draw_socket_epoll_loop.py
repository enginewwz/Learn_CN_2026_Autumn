#!/usr/bin/env python3
"""Horizontal flowchart-style event loop with curved back edges."""

from __future__ import annotations

from matplotlib.path import Path
from matplotlib.patches import FancyArrowPatch, Polygon

from socket_draw_common import (
    BLUE, BLUE_BG, GREEN, GREEN_BG, ORANGE, ORANGE_BG, OUT,
    arrow, block, canvas, label, save,
)

fig, ax = canvas()
Y = 70.0

# six nodes, lower profile than before
block(ax, 2, Y - 4.25, 23, 8.5, "epoll_wait",
      "阻塞等待，最多 64 个就绪", BLUE, BLUE_BG,
      title_fs=10.8, desc_fs=7.2)
block(ax, 28, Y - 3.5, 19, 7.0, "循环处理 64 个事件", "", BLUE, BLUE_BG,
      title_fs=9.4)
# decision diamond
dx, dy, dw, dh = 60.5, Y, 22, 13.5
ax.add_patch(Polygon(
    [(dx, dy + dh / 2), (dx + dw / 2, dy), (dx, dy - dh / 2), (dx - dw / 2, dy)],
    closed=True, facecolor=BLUE_BG, edgecolor=BLUE, linewidth=1.4, zorder=3,
))
ax.text(dx, dy, "检查\nevent.data.ptr", ha="center", va="center",
        fontsize=8.4, color="#24303F", weight="bold", zorder=5, linespacing=1.3)
block(ax, 75, Y - 4.25, 22, 8.5, "接受新连接",
      "接口 80/443", GREEN, GREEN_BG, title_fs=9.8, desc_fs=7.6)
block(ax, 100, Y - 3.5, 36, 7.0, "新建 connection 结构体并注册到 epoll", "",
      GREEN, GREEN_BG, title_fs=7.2)
block(ax, 139, Y - 4.25, 19, 8.5, "drive(connection)",
      "处理对应的连接", ORANGE, ORANGE_BG, title_fs=8.6, desc_fs=7.2)

# straight forward edges
arrow(ax, (25, Y), (28, Y), color=BLUE)
arrow(ax, (47, Y), (49.5, Y), color=BLUE)
arrow(ax, (71.5, Y), (75, Y), color=GREEN)
label(ax, 73.2, Y + 1.4, "listener", fs=7.2, color=GREEN)
arrow(ax, (97, Y), (100, Y), color=GREEN)
arrow(ax, (136, Y), (139, Y), color=GREEN)


def curve(p0, p1, c1, c2, color):
    path = Path([p0, c1, c2, p1],
                [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4])
    ax.add_patch(FancyArrowPatch(
        path=path, arrowstyle="-|>", mutation_scale=13, linewidth=1.5,
        color=color, zorder=1.8, shrinkA=0, shrinkB=0,
    ))


# 2 -> 1: one batch completed (curve above)
curve((37.5, Y + 3.5), (13.5, Y + 4.25),
      (37.5, 84), (13.5, 84), BLUE)
label(ax, 25.5, 81.3, "处理一组完成", fs=7.4, color=BLUE)

# 3 -> 6: connection branch (curve below)
curve((dx, dy - dh / 2), (143, Y - 4.25),
      (72, 52), (120, 52), ORANGE)
label(ax, 104, 58.5, "connection", fs=7.4, color=ORANGE)

# 6 -> 2: loop back after one event (deeper curve)
curve((154, Y - 4.25), (37.5, Y - 3.5),
      (130, 44), (60, 44), ORANGE)
label(ax, 104, 47.5, "处理完一个事件", fs=7.2, color=ORANGE)

ax.set_ylim(38, 88)
fig.set_size_inches(16, 6.0)

save(fig, "02-socket-epoll-loop")

# trim white margin from PNG
try:
    from PIL import Image, ImageChops
    path = OUT / "02-socket-epoll-loop.png"
    img = Image.open(path).convert("RGB")
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bbox = ImageChops.difference(img, bg).getbbox()
    if bbox:
        pad = 18
        bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                min(img.width, bbox[2] + pad), min(img.height, bbox[3] + pad))
        img.crop(bbox).save(path)
except Exception as exc:
    print("png crop skipped:", exc)
