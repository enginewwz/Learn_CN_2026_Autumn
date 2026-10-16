#!/usr/bin/env python3
"""Oct9 实验 05-bufferbloat 绘图脚本。

画 4 张图（输出到 Oct9/report/）：

    exp1-cwnd.png   实验1 拥塞窗口 cwnd 随时间    <- qlen-<maxq>/cwnd.txt
    exp1-qlen.png   实验1 瓶颈队列长度 随时间     <- qlen-<maxq>/qlen.txt
    exp1-rtt.png    实验1 往返时延 RTT 随时间     <- qlen-<maxq>/rtt.txt
    exp234-rtt.png  实验2/3/4 等待时间(RTT) 对比   <- taildrop|red|codel/rtt.txt

其中 exp1 三张图共用同一个时间零点（三个监视器里最早的一次采样），可以叠着看；
exp234 每条曲线以各自实验的第一帧为 0（三个实验结构相同，这样相位对齐）。

运行方式（需要 matplotlib 的 python，CN_lab_env 或 conda base 都行）：

    MPLCONFIGDIR=/tmp/mpl python3 Oct9/report/scripts/plot_bufferbloat.py

可选参数：--data-dir --outdir --maxq --algos --dpi --no-phases
"""

import argparse
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator

HERE = os.path.dirname(os.path.abspath(__file__))
OCT9 = os.path.dirname(os.path.dirname(HERE))            # .../CN_lab_2026_Autumn/Oct9
DEFAULT_DATA = os.path.join(OCT9, "05-bufferbloat")
DEFAULT_OUT = os.path.join(OCT9, "report")

# 与 Sept24 的绘图脚本保持一致的配色
BLUE = "#2471a3"
RED = "#c0392b"
GRAY = "#7f8c8d"
GAP_COLOR = "#ff8a80"
RTT_FLOOR_MS = 0.05            # 对数轴下界（ping 的最小值也可能是 0.0，log 不能取 0）          # 采样空档标注色（浅红，明显区别于各算法本色）
GREEN = "#1e8449"
ALGO_STYLE = {
    "taildrop": (GRAY, "-", "taildrop (尾丢弃)"),
    "red": (RED, "-", "red"),
    "codel": (BLUE, "-", "codel"),
}
# dynamic_bw 的相位：每 10 s 换一次速率
PHASES = [(0, 10, "100 Mbit"), (10, 20, "10 Mbit"), (20, 30, "1 Mbit"),
          (30, 40, "50 Mbit"), (40, 50, "1 Mbit"), (50, 60, "100 Mbit")]


def setup_font():
    for cand in ("Noto Sans CJK SC", "Noto Serif CJK SC", "WenQuanYi Zen Hei",
                 "WenQuanYi Micro Hei", "Source Han Sans SC", "DejaVu Sans"):
        if any(f.name == cand for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [cand]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 120
    plt.rcParams["savefig.bbox"] = "tight"


# --------------------------------------------------------------------------
# 读取三种监控文件（格式见 Oct9/05-bufferbloat/utils.py）
# --------------------------------------------------------------------------
def _read_lines(path):
    with open(path, errors="replace") as fh:
        return fh.readlines()


def read_cwnd(path):
    """cwnd.txt: '<epoch>, \\t reno ... cwnd:10 ...' -> (t, cwnd 包数)"""
    ts, vals = [], []
    for line in _read_lines(path):
        m = re.search(r"cwnd:(\d+)", line)
        if not m:
            continue
        try:
            t = float(line.split(",", 1)[0])
        except ValueError:
            continue
        ts.append(t)
        vals.append(int(m.group(1)))
    return np.array(ts), np.array(vals, dtype=float)


def read_qlen(path):
    """qlen.txt: '<epoch>, <包数>' 两列"""
    ts, vals = [], []
    for line in _read_lines(path):
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            ts.append(float(parts[0]))
            vals.append(float(parts[1]))
        except ValueError:
            continue
    return np.array(ts), np.array(vals)


def read_rtt(path):
    """rtt.txt: '<epoch>, 64 bytes from ... time=12.3 ms' -> (t, ms)"""
    ts, vals = [], []
    for line in _read_lines(path):
        m = re.search(r"time=([\d.]+)\s*ms", line)
        if m:
            v = float(m.group(1))
        elif re.search(r"time<1\s*ms", line):                # busybox 之类
            v = 0.5
        else:
            continue                                          # 丢包/超时那一轮没有值
        try:
            t = float(line.split(",", 1)[0])
        except ValueError:
            continue
        ts.append(t)
        vals.append(v)
    return np.array(ts), np.array(vals)


def _first_interval_epoch(path):
    """throughput*.csv 里第一条 kind=interval 的 (epoch, end_s)。"""
    if not os.path.exists(path):
        return None
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("epoch"):
                continue
            c = line.strip().split(",")
            if len(c) == 6 and c[5] == "interval":
                try:
                    return float(c[0]), float(c[2])
                except ValueError:
                    return None
    return None


def read_t0(data_dir, verbose=True):
    """实验时钟起点。

    1) <dir>/t0.txt —— run_lab.py 在 start_rtt_monitor 时写下的精确值（首选）；
    2) 回退：用 iperf 客户端第一条逐秒行反推 epoch - end_s + 0.03（≈ 实验起点，
       实测误差 < 50 ms）。没有它就只能用"第一条样本"当 0 点，会因监视器启动延迟
       （fork + 开工自检重试 ~0.5 s）整体左偏，相位栅格对不齐。
    3) 都没有则返回 None，由调用方退回"第一条样本"。
    """
    path = os.path.join(data_dir, "t0.txt")
    if os.path.exists(path):
        try:
            with open(path) as fh:
                t0 = float(fh.read().strip())
            if verbose:
                src = os.path.join(data_dir, "t0.source")
                tag = "精确"
                if os.path.exists(src):
                    try:
                        tag = open(src).read().strip()
                    except OSError:
                        pass
                print("  [i] %s: 时间零点 = t0.txt（%s）"
                      % (os.path.basename(data_dir), tag))
            return t0
        except ValueError:
            pass
    if os.path.basename(data_dir.rstrip("/")).startswith("qlen-"):
        # reproduce 系列（qlen-<maxq>/）没有相位栅格，且它的监视器在 iperf 之前启动；
        # 用 iperf 反推会把零点后移 ~0.45s，不如直接以最早样本为 0（与旧图一致）。
        return None
    for name in ("throughput.csv", "throughput-server.csv"):
        got = _first_interval_epoch(os.path.join(data_dir, name))
        if got:
            epoch, end_s = got
            t0 = epoch - end_s + 0.03
            if verbose:
                print("  [i] %s: 无 t0.txt，用 iperf 首行反推时间零点（误差 <50 ms）"
                      % os.path.basename(data_dir))
            return t0
    return None


def auto_gap_threshold(t, minimum=0.3, factor=5.0):
    """相邻样本间隔超过该秒数就算"空档"。默认取 max(0.3, 5×采样间隔中位)。"""
    t = np.asarray(t, dtype=float)
    if len(t) < 3:
        return minimum
    dt = np.median(np.diff(t))
    return max(minimum, factor * dt)


def mark_gaps(ax, t, v, color, gap_th, lw=1.0, label=None, zorder=3):
    """按空档分段画线：实测段用本色，跨越空档的那一段用**浅红色**标出（不铺底纹）。

    返回空档个数。这样曲线保持连续，又能一眼看出哪些部分不是实测数据。
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    if len(t) == 0:
        return 0
    gaps = np.where(np.diff(t) > gap_th)[0] if len(t) > 1 else np.array([], dtype=int)
    starts = [0] + [int(i) + 1 for i in gaps]
    ends = [int(i) for i in gaps] + [len(t) - 1]
    for k, (a, b) in enumerate(zip(starts, ends)):
        if b > a:
            ax.plot(t[a:b + 1], v[a:b + 1], color=color, lw=lw,
                    label=label if k == 0 else None, zorder=zorder)
    for i in gaps:
        # 只画浅红色的"跨空档连线"，不铺底纹：三条曲线同时有空档时底纹叠一起太乱
        ax.plot(t[i:i + 2], v[i:i + 2], color=GAP_COLOR, alpha=0.95,
                lw=lw * 2.0, solid_capstyle="round", zorder=zorder - 1)
    return len(gaps)


def add_gap_legend(ax, gap_th, anchor=None):
    """在 legend 里加一条"浅色=采样空档"的说明。"""
    from matplotlib.lines import Line2D
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([], [], color=GAP_COLOR, lw=3.0))
    labels.append("采样空档 (>%.2g s 无样本)" % gap_th)
    ax.legend(handles, labels, loc="upper right", framealpha=0.9, fontsize=9)


def sort_series(t, v):
    """按时间戳排序。

    utils.py 原版 rtt 监视器每 100 ms 起一个 ping 线程、互不等待，RTT 大时会有
    上百个探针在途：文件按**完成**顺序落盘、时间戳却是**发送**时刻，于是时间戳
    逆序、折线来回跳。这里排序抹平绘图顺序问题，并按逆序数量给出数据质量提示。
    """
    if len(t) == 0:
        return t, v, 0
    order = np.argsort(t, kind="stable")
    inversions = int(np.sum(np.diff(t) < 0))
    return t[order], v[order], inversions


def read_rtt_checked(path, tag):
    t, v = read_rtt(path)
    t, v, inv = sort_series(t, v)
    if inv:
        print("  [!] %s/rtt.txt 有 %d 处时间戳逆序：同一批在途探针 RTT 差异极大时，"
              "晚发的先打印（例如被扣住的探针最后才回来）；已按发送时刻排序"
              % (tag, inv))
    if len(t) > 1:
        gaps = np.diff(t)
        big = np.where(gaps > 1.0)[0]
        if len(big):
            print("  [i] %s/rtt.txt 有 %d 处采样间隔 > 1 s（最大 %.1f s），"
                  "已按直线连接；需要断口请加 --max-gap 1.5"
                  % (tag, len(big), float(gaps.max())))
    return t, v


def break_gaps(t, v, max_gap):
    """相邻样本间隔超过 max_gap 秒就断开折线（默认 max_gap=0 表示不断开）。"""
    if max_gap <= 0 or len(t) < 2:
        return t, v
    gaps = np.diff(t)
    if not np.any(gaps > max_gap):
        return t, v
    xs, ys = [t[0]], [v[0]]
    for i, g in enumerate(gaps):
        if g > max_gap:
            xs.append(np.nan)
            ys.append(np.nan)
        xs.append(t[i + 1])
        ys.append(v[i + 1])
    return np.array(xs), np.array(ys)


def stats(v):
    if len(v) == 0:
        return None
    return dict(n=len(v), min=float(np.min(v)), avg=float(np.mean(v)),
                med=float(np.median(v)), max=float(np.max(v)))


def parse_cuts(text):
    """'2000:4000' / '2000:4000,9000:12000' -> [(2000,4000), (9000,12000)]；none/空 -> []"""
    if not text or str(text).lower() in ("none", "off", "0"):
        return []
    cuts = []
    for part in str(text).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        bits = re.split(r"[:~\-]", part, maxsplit=1)
        if len(bits) != 2:
            continue
        try:
            lo, hi = float(bits[0]), float(bits[1])
        except ValueError:
            continue
        if hi > lo:
            cuts.append((lo, hi))
    return sorted(cuts)


def resolve_segments(text, data_max):
    """把 [0, data_max*1.03] 按 --cut 指定的区间挖掉，返回保留段 [(0,lo),(hi,...)]。

    挖掉的段完全不出现在图上（曲线在那儿断开），其余各段**比例尺一致**：
    面板高度按各段的"数值跨度"分配，所以两边的 ms/像素是一样的。
    """
    top = float(data_max) * 1.03
    cuts = [(lo, hi) for lo, hi in parse_cuts(text) if lo < hi < top]
    if not cuts:
        return [(0.0, top)], []
    segs, cur = [], 0.0
    for lo, hi in cuts:
        if lo > cur:
            segs.append((cur, lo))
        cur = max(cur, hi)
    if cur < top:
        segs.append((cur, top))
    return (segs or [(0.0, top)]), cuts


def stacked_axes(fig, segments, style="scale", hspace=0.06):
    """按保留段建共享 x 的纵向面板。

    style="scale"（默认）：各段高度 ∝ 数值跨度 -> 两侧比例尺一致（截断坐标轴）
    style="equal"        ：各段等高（每段各自缩放）
    """
    if len(segments) <= 1:
        ax = fig.subplots(1, 1)
        ax.set_ylim(0, segments[0][1])
        return [ax], segments
    # axes 从上到下，而 segments 是从低到高 -> 高度比例也要反着配，两侧比例尺才一致
    ordered = list(reversed(segments))
    ratios = [hi - lo for lo, hi in ordered] if style == "scale" else [1.0] * len(ordered)
    axes = list(fig.subplots(len(segments), 1, sharex=True,
                             gridspec_kw={"hspace": hspace, "height_ratios": ratios}))
    for ax, (lo, hi) in zip(axes, reversed(segments)):     # axes[0] 在最上面
        ax.set_ylim(lo, hi)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    d = 0.012
    for i in range(len(segments) - 1):
        upper, lower = axes[i], axes[i + 1]
        upper.spines["bottom"].set_visible(False)
        lower.spines["top"].set_visible(False)
        for ax, ys in ((upper, (-d, d)), (lower, (1 - d, 1 + d))):
            kw = dict(transform=ax.transAxes, color="#2c3e50", clip_on=False, lw=1.0)
            ax.plot((-d, d), ys, **kw)
            ax.plot((1 - d, 1 + d), ys, **kw)
    return axes, segments


def finish_banded(fig, axes, segments, ylabel, xlabel, title,
                  gap_legend=None, ncol=3, xlim=None):
    """截断轴的收尾：网格 / 只留最下面板的 x 刻度 / 图例放 x 标签下方 / 标题，y 标签居中。"""
    from matplotlib.lines import Line2D
    for ax in axes:
        ax.grid(True, ls=":", lw=0.6, alpha=0.7)
        ax.margins(x=0.01)
        if xlim:
            ax.set_xlim(*xlim)
    for ax in axes[:-1]:
        ax.tick_params(labelbottom=False)
    handles, labels = axes[0].get_legend_handles_labels()
    if gap_legend:
        handles.append(Line2D([], [], color=GAP_COLOR, lw=3.0))
        labels.append(gap_legend)
    axes[-1].set_xlabel(xlabel)
    # 用图级 legend 挂在画布下方：底段可能很矮，用 axes 坐标的固定偏移会压住 xlabel
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.0),
               ncol=max(ncol, len(labels)), framealpha=0.9, fontsize=9)
    fig.supylabel(ylabel, fontsize=10)
    axes[0].set_title(title, pad=26)
    if segments and len(segments) > 1:
        print("  [i] 纵轴保留段: %s ms（各段比例尺一致）"
              % " / ".join("%g-%g" % seg for seg in segments))


def _finish(ax, xlabel, ylabel, title):
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, ls=":", lw=0.6, alpha=0.7)
    ax.margins(x=0.01)


def _save(fig, outdir, name):
    path = os.path.join(outdir, name)
    fig.savefig(path)
    plt.close(fig)
    print("  写出 %s" % path)
    return path


# --------------------------------------------------------------------------
# 实验 1 的三张图
# --------------------------------------------------------------------------
def plot_exp1(data_dir, outdir, maxq, dpi, max_gap=0.0, gap_threshold=0.0):
    d = os.path.join(data_dir, "qlen-%d" % maxq)
    print("实验1 数据目录: %s" % d)
    cwnd_f = os.path.join(d, "cwnd.txt")
    qlen_f = os.path.join(d, "qlen.txt")
    rtt_f = os.path.join(d, "rtt.txt")
    missing = [f for f in (cwnd_f, qlen_f, rtt_f) if not os.path.exists(f)]
    if missing:
        print("  [!] 缺少 %s，跳过实验1" % ", ".join(os.path.basename(f) for f in missing))
        return []

    tc, cwnd = read_cwnd(cwnd_f)
    tq, qlen = read_qlen(qlen_f)
    tc, cwnd, _ = sort_series(tc, cwnd)
    tq, qlen, _ = sort_series(tq, qlen)
    tr, rtt = read_rtt_checked(rtt_f, "qlen-%d" % maxq)
    t0 = read_t0(d)                                          # 三张图共用零点
    if t0 is None:
        t0 = min(tc[0], tq[0], tr[0])
        print("  [i] qlen-%d: 无时钟信息，退回三条曲线的最早样本为 0" % maxq)
    tc, tq, tr = tc - t0, tq - t0, tr - t0

    out = []
    # cwnd
    fig, ax = plt.subplots(figsize=(8, 4.2))
    gap_th_c = auto_gap_threshold(tc) if gap_threshold <= 0 else gap_threshold
    mark_gaps(ax, tc, cwnd, BLUE, gap_th_c, lw=1.0)
    _finish(ax, "时间 (s)", "拥塞窗口 cwnd (包)",
            "实验1 拥塞窗口变化 (瓶颈 10 Mbit, 缓冲区 maxq=%d)" % maxq)
    out.append(_save(fig, outdir, "exp1-cwnd.png"))

    # 队列长度
    fig, ax = plt.subplots(figsize=(8, 4.2))
    gap_th_q = auto_gap_threshold(tq) if gap_threshold <= 0 else gap_threshold
    mark_gaps(ax, tq, qlen, GREEN, gap_th_q, lw=1.0)
    qavg = float(np.mean(qlen))
    ax.axhline(qavg, color=GRAY, ls="--", lw=1.2,
               label="平均队列长度 %.0f 包" % qavg)
    ax.legend(loc="upper right", framealpha=0.9)
    _finish(ax, "时间 (s)", "瓶颈队列长度 (包)",
            "实验1 瓶颈队列长度 (r1-eth1, maxq=%d)" % maxq)
    out.append(_save(fig, outdir, "exp1-qlen.png"))

    # RTT
    fig, ax = plt.subplots(figsize=(8, 4.2))
    gap_th = auto_gap_threshold(tr) if gap_threshold <= 0 else gap_threshold
    ngap = mark_gaps(ax, tr, rtt, RED, gap_th, lw=1.0)
    ravg = float(np.mean(rtt))
    ax.axhline(ravg, color=GRAY, ls="--", lw=1.2, label="平均 RTT %.0f ms" % ravg)
    if ngap:
        add_gap_legend(ax, gap_th)
    else:
        ax.legend(loc="upper right", framealpha=0.9)
    _finish(ax, "时间 (s)", "RTT (ms)",
            "实验1 往返时延 (h1 ping h2, maxq=%d)" % maxq)
    out.append(_save(fig, outdir, "exp1-rtt.png"))

    print("  cwnd: %s" % stats(cwnd))
    print("  qlen: %s" % stats(qlen))
    print("  rtt : %s" % stats(rtt))
    return out


# --------------------------------------------------------------------------
# 实验 2/3/4：三条 RTT 曲线画在一起
# --------------------------------------------------------------------------
def plot_exp234(data_dir, outdir, algos, dpi, show_phases=True, max_gap=0.0,
                gap_threshold=0.0, rtt_scale="linear",
                cut_axis="2000:4000", band_style="scale"):
    series = {}
    for algo in algos:
        f = os.path.join(data_dir, algo, "rtt.txt")
        if not os.path.exists(f):
            print("  [!] 缺少 %s/rtt.txt，跳过 %s" % (algo, algo))
            continue
        ts, vals = read_rtt_checked(f, algo)
        if len(ts) == 0:
            print("  [!] %s/rtt.txt 没有有效样本，跳过" % algo)
            continue
        x0 = read_t0(os.path.join(data_dir, algo)) or ts[0]   # 优先 t0.txt
        series[algo] = (ts - x0, vals)
        st = stats(vals)
        print("  %-8s n=%-4d min=%-7.2f avg=%-8.1f med=%-8.1f max=%.1f ms"
              % (algo, st["n"], st["min"], st["avg"], st["med"], st["max"]))
        if st["max"] < 5.0:
            print("  [!] %s 的最大 RTT 仅 %.2f ms：这一轮 iperf 很可能没有流量"
                  "（空载），结果不可用，需重跑该实验" % (algo, st["max"]))

    if not series:
        print("  [!] 没有可画的曲线，跳过 exp234-rtt.png")
        return []

    data_max = max(float(np.max(v)) for _, v in series.values())
    segments, cuts = resolve_segments(cut_axis, data_max)
    fig = plt.figure(figsize=(9.5, 5.0))
    axes, segments = stacked_axes(fig, segments, style=band_style)
    gaps_seen, gap_th_used = 0, auto_gap_threshold(series[list(series)[0]][0])

    if show_phases:
        for k, ax in enumerate(axes):
            for i, (a, b, label) in enumerate(PHASES):
                if i % 2 == 0:
                    ax.axvspan(a, b, color="#f2f4f4", zorder=0)
                ax.axvline(a, color="#d5d8dc", lw=0.8, ls="--", zorder=1)
                if k == 0:                      # 相位文字只标在最上面板
                    ax.text((a + b) / 2.0, 1.01, label,
                            transform=ax.get_xaxis_transform(),
                            ha="center", va="bottom", fontsize=8, color="#566573")

    for algo in algos:
        if algo not in series:
            continue
        raw_t, raw_v = series[algo]
        t, v = break_gaps(raw_t, raw_v, max_gap)      # NaN 只用于断线，不能算均值
        color, ls, label = ALGO_STYLE.get(algo, (BLUE, "-", algo))
        gth = auto_gap_threshold(raw_t) if gap_threshold <= 0 else gap_threshold
        lname = "%s (avg %.0f ms)" % (label, float(np.mean(raw_v)))
        for k, ax in enumerate(axes):
            ng = mark_gaps(ax, t, v, color, gth, lw=1.1, label=lname if k == 0 else None)
            gaps_seen = gaps_seen or ng
        gap_th_used = gth

    ylab = "等待时间 RTT (ms)"
    xlabel = "时间 (s，以各自实验起点为 0)"
    title = "实验2/3/4 等待时间对比 (动态带宽 100/10/1/50/1/100 Mbit)"
    if cuts:
        title += "    (纵轴省略 %s ms)" % "、".join("%g~%g" % c for c in cuts)
    gap_legend = ("采样空档 (>%.2g s 无样本)" % gap_th_used) if gaps_seen else None
    finish_banded(fig, axes, segments, ylab, xlabel, title,
                  gap_legend=gap_legend, ncol=len(series))
    return [_save(fig, outdir, "exp234-rtt.png")]



def read_throughput(path, max_gap=0.0):
    """throughput*.csv -> (相对时间, Mbit/s)，只取 kind=interval 的行。"""
    ts, vals = [], []
    if not os.path.exists(path):
        return np.array([]), np.array([])
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("epoch"):
                continue
            parts = line.strip().split(",")
            if len(parts) != 6 or parts[5] != "interval":
                continue
            try:
                ts.append(float(parts[2]))          # end_s：相对 iperf 起点
                vals.append(float(parts[4]))        # rate_Mbit
            except ValueError:
                continue
    t = np.array(ts)
    v = np.array(vals)
    t, v, _ = sort_series(t, v)
    t, v = break_gaps(t, v, max_gap)
    return t, v


def plot_exp234_throughput(data_dir, outdir, algos, max_gap=0.0):
    """客户端写入速率 vs 服务端收到速率：后者才是真正的线上速率。"""
    fig, axes = plt.subplots(len(algos), 1, figsize=(9.5, 2.2 * len(algos)),
                             sharex=True, sharey=True)
    if len(algos) == 1:
        axes = [axes]
    drew = 0
    for ax, algo in zip(axes, algos):
        d = os.path.join(data_dir, algo)
        # 注意: read_throughput 给出的 x 是 iperf 自己的 end_s（相对客户端起点），
        # 不是 epoch，所以这里**不能**再减 t0（之前减了，图直接炸掉）。
        # 客户端起点与实验起点 t0 只差约 30 ms，对相位栅格无影响。
        tc, vc = read_throughput(os.path.join(d, "throughput.csv"), max_gap)
        ts_, vs = read_throughput(os.path.join(d, "throughput-server.csv"), max_gap)
        color, _, label = ALGO_STYLE.get(algo, (BLUE, "-", algo))
        if len(tc):
            ax.plot(tc, vc, color=color, lw=1.0, label="客户端写入")
            drew += 1
        if len(ts_):
            ax.plot(ts_, vs, color=color, lw=1.0, ls="--", label="服务端收到(线上)")
            drew += 1
        for a, b, name in PHASES:
            if (a // 10) % 2 == 0:
                ax.axvspan(a, b, color="#f2f4f4", zorder=0)
            ax.axvline(a, color="#d5d8dc", lw=0.7, ls=":", zorder=1)
        ax.set_ylabel("Mbit/s")
        ax.set_title("%s" % label, fontsize=10, loc="left")
        ax.grid(True, ls=":", lw=0.6, alpha=0.7)
        ax.margins(x=0.01)
        ax.set_ylim(bottom=0)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    axes[-1].set_xlabel("时间 (s，相对 iperf 客户端起点)")
    axes[0].text(0.5, 1.35, "实验2/3/4 吞吐率：客户端写入 vs 服务端线上收到"
                 "（相位 100/10/1/50/1/100 Mbit）", transform=axes[0].transAxes,
                 ha="center", va="bottom", fontsize=11)
    if not drew:
        plt.close(fig)
        print("  [!] 没有 throughput csv，跳过 exp234-throughput.png")
        return []
    return [_save(fig, outdir, "exp234-throughput.png")]


def plot_exp234_zoom(data_dir, outdir, algos, dpi, show_phases=True, gap_threshold=0.0,
                     zoom_algos=("red", "codel"), cut_axis="none"):
    """单独一张小图：只画指定算法（默认 red + codel），线性轴。

    taildrop 的秒级 RTT 会把 red/codel 的细节压扁，所以另出一张放大版。
    """
    subset = [a for a in zoom_algos if a in algos]
    if not subset:
        return []
    series = {}
    for algo in subset:
        f = os.path.join(data_dir, algo, "rtt.txt")
        if not os.path.exists(f):
            print("  [!] 缺少 %s/rtt.txt，%s 小图跳过该曲线" % (algo, "+".join(subset)))
            continue
        t, v = read_rtt_checked(f, algo)
        if len(t) == 0:
            continue
        x0 = read_t0(os.path.join(data_dir, algo)) or t[0]
        series[algo] = (t - x0, v)
    if not series:
        return []

    data_max = max(float(np.max(v)) for _, v in series.values())
    segments, cuts = resolve_segments(cut_axis, data_max)
    fig = plt.figure(figsize=(7.6, 3.4))
    axes, segments = stacked_axes(fig, segments, style="scale")
    gaps_seen, gap_th_used = 0, auto_gap_threshold(series[list(series)[0]][0])

    for k, ax in enumerate(axes):
        if show_phases:
            for i, (a, b, label) in enumerate(PHASES):
                if i % 2 == 0:
                    ax.axvspan(a, b, color="#f2f4f4", zorder=0)
                ax.axvline(a, color="#d5d8dc", lw=0.8, ls="--", zorder=1)
                if k == 0:
                    ax.text((a + b) / 2.0, 1.02, label, transform=ax.get_xaxis_transform(),
                            ha="center", va="bottom", fontsize=7.5, color="#566573")
    for algo, (t, v) in series.items():
        color, ls, label = ALGO_STYLE.get(algo, (BLUE, "-", algo))
        gth = auto_gap_threshold(t) if gap_threshold <= 0 else gap_threshold
        for k, ax in enumerate(axes):
            ng = mark_gaps(ax, t, v, color, gth, lw=1.0,
                           label="%s (avg %.0f ms)" % (label, float(np.mean(v))) if k == 0 else None)
            gaps_seen = gaps_seen or ng
        gap_th_used = gth

    from matplotlib.lines import Line2D
    for ax in axes:
        ax.grid(True, ls=":", lw=0.6, alpha=0.7)
        ax.margins(x=0.01)
    for ax in axes[:-1]:
        ax.tick_params(labelbottom=False)
    handles, labels = axes[0].get_legend_handles_labels()
    if gaps_seen:
        handles.append(Line2D([], [], color=GAP_COLOR, lw=3.0))
        labels.append("采样空档 (>%.2g s)" % gap_th_used)
    axes[-1].set_xlabel("时间 (s，以各自实验起点为 0)")
    axes[-1].legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.22),
                    ncol=len(labels), framealpha=0.9, fontsize=8.5)
    axes[0].set_ylabel("RTT (ms)", fontsize=9)
    axes[0].set_title("%s 放大：动态带宽 100/10/1/50/1/100 Mbit"
                      % "+".join(subset), pad=20)
    return [_save(fig, outdir, "exp234-rtt-%s.png" % "-".join(subset).replace("/", "_"))]


def main():
    ap = argparse.ArgumentParser(description="Oct9 bufferbloat 绘图")
    ap.add_argument("--data-dir", default=DEFAULT_DATA, help="实验结果目录（默认 Oct9/05-bufferbloat）")
    ap.add_argument("--outdir", default=DEFAULT_OUT, help="图片输出目录（默认 Oct9/report）")
    ap.add_argument("--maxq", type=int, default=1000, help="实验1 的缓冲区大小，决定 qlen-<maxq>/ 目录名")
    ap.add_argument("--algos", default="taildrop,red,codel", help="实验2/3/4 的算法，逗号分隔")
    ap.add_argument("--dpi", type=int, default=150, help="输出图片 dpi")
    ap.add_argument("--no-phases", action="store_true", help="不画动态带宽相位标注")
    ap.add_argument("--zoom-algos", default="red,codel",
                    help="小图（放大版）要画的算法，默认 red,codel；留空则不画小图")
    ap.add_argument("--rtt-scale", choices=("log", "linear"), default="linear",
                    help="实验2/3/4 的 RTT 纵轴，默认 linear（配合 --break-axis 断轴）")
    ap.add_argument("--cut", dest="cut_axis", default="none",
                    help="RTT 纵轴要省略的区间（ms），如 '2000:4000'；默认 none=普通线性轴")
    ap.add_argument("--band-style", choices=("scale", "equal"), default="scale",
                    help="省略区间后各段的高度分配：scale=按数值跨度（比例尺一致，默认），"
                         "equal=每段等高")
    ap.add_argument("--gap-threshold", type=float, default=0.0,
                    help="相邻样本间隔超过该秒数就视为采样空档并用浅色标出；"
                         "0=自动（max(0.3, 5×采样间隔中位)）")
    ap.add_argument("--max-gap", type=float, default=0.0,
                    help="相邻样本间隔超过该秒数就断开折线；默认 0=不断开（曲线连续），"
                         "想要把缺失区间画成断口就设成 1.5 之类")
    args = ap.parse_args()

    setup_font()
    plt.rcParams["figure.dpi"] = args.dpi
    os.makedirs(args.outdir, exist_ok=True)

    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        print("错误: 数据目录不存在: %s" % data_dir, file=sys.stderr)
        return 1

    print("数据目录: %s" % data_dir)
    print("输出目录: %s" % os.path.abspath(args.outdir))
    algos = [a.strip() for a in args.algos.split(",") if a.strip()]

    out = []
    out += plot_exp1(data_dir, args.outdir, args.maxq, args.dpi, max_gap=args.max_gap,
                     gap_threshold=args.gap_threshold)
    out += plot_exp234(data_dir, args.outdir, algos, args.dpi,
                       show_phases=not args.no_phases, max_gap=args.max_gap,
                       gap_threshold=args.gap_threshold, rtt_scale=args.rtt_scale,
                       cut_axis=args.cut_axis, band_style=args.band_style)
    zoom_algos = [a.strip() for a in args.zoom_algos.split(",") if a.strip()]
    if zoom_algos:
        out += plot_exp234_zoom(data_dir, args.outdir, algos, args.dpi,
                                show_phases=not args.no_phases,
                                gap_threshold=args.gap_threshold,
                                zoom_algos=tuple(zoom_algos), cut_axis=args.cut_axis)
    out += plot_exp234_throughput(data_dir, args.outdir, algos, max_gap=args.max_gap)

    print("\n共写出 %d 张图:" % len(out))
    for p in out:
        print("  %s (%.1f KB)" % (p, os.path.getsize(p) / 1024.0))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
