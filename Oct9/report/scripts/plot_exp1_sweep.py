#!/usr/bin/env python3
"""实验1（复现 bufferbloat）：把不同缓冲区大小（maxq）的结果画在一起对比。

数据来自 `run_exp1_sweep.sh` 跑出的若干 `qlen-<maxq>/` 目录（与单次实验的产物格式完全一致）：

    <data-dir>/
        qlen-10/{cwnd.txt,qlen.txt,qlen-r1eth1.txt,rtt.txt,...}
        qlen-50/...
        qlen-200/...
        qlen-1000/...

输出一张三联图：拥塞窗口 / 瓶颈队列（字节优先） / RTT，每条曲线一个 maxq。
另外在 legend 里标注该缓冲在 10 Mbit 瓶颈下的**理论上限排队延迟** maxq × 1.5 KB ÷ 10 Mbit。

用法：
    MPLCONFIGDIR=/tmp/mpl python3 Oct9/report/scripts/plot_exp1_sweep.py \
        --data-dir Oct9/05-bufferbloat --maxq 10,50,200,1000 \
        --out Oct9/report/exp1-sweep.png
"""

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plot_bufferbloat as pb                                        # noqa: E402

COLORS = ["#c0392b", "#2471a3", "#1e8449", "#8e44ad", "#f39c12", "#16a085", "#7f8c8d"]
BOTTLENECK_MBIT = 10.0            # reproduce_bufferbloat.py 的瓶颈速率
BYTES_PER_PKT = 1500.0            # 按 1500 B/包估算理论上限排队延迟


def read_qdisc(path):
    """qlen-r1eth1.txt（3 列：epoch, 包数, 字节数）或 qlen.txt（2 列：epoch, 包数）。"""
    ts, pk, by = [], [], []
    if not os.path.exists(path):
        return np.array([]), np.array([]), np.array([])
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            c = line.strip().split(",")
            try:
                ts.append(float(c[0]))
                pk.append(float(c[1]))
                raw = float(c[2]) if len(c) > 2 else float(c[1]) * BYTES_PER_PKT
                by.append(raw / 1024.0)                # 统一成 KB
            except (ValueError, IndexError):
                continue
    return np.array(ts), np.array(pk), np.array(by)


def smooth(t, v, window_s=0.2):
    """时间窗滑动中位：队列曲线在 TSO 下是密集锯齿，平滑后才看得出趋势。"""
    if len(t) < 5 or window_s <= 0:
        return v
    dt = float(np.median(np.diff(t)))
    win = max(1, int(round(window_s / max(dt, 1e-6))))
    if win <= 1:
        return v
    half = win // 2
    out = []
    for i in range(len(v)):
        lo, hi = max(0, i - half), min(len(v), i + half + 1)
        out.append(float(np.median(v[lo:hi])))
    return np.array(out)


def load_one(d):
    """读一个 qlen-<maxq>/ 目录；三张图共用该目录里最早的样本时刻为 0。"""
    ct, cw = pb.read_cwnd(os.path.join(d, "cwnd.txt"))
    qt, qp, qb = read_qdisc(os.path.join(d, "qlen-r1eth1.txt"))
    if len(qt) == 0:                                   # 退回 lab 自带的包数文件
        qt, qp, qb = read_qdisc(os.path.join(d, "qlen.txt"))
    rt, rv = pb.read_rtt(os.path.join(d, "rtt.txt"))
    ts = [x[0] for x in (ct, qt, rt) if len(x)]
    if not ts:
        return None
    t0 = min(ts)
    return {"cwnd": (ct - t0, cw), "qlen": (qt - t0, qb),
            "rtt": (rt - t0, rv), "t0": t0}


def main():
    ap = argparse.ArgumentParser(description="实验1：不同缓冲区大小的对比图")
    ap.add_argument("--data-dir", required=True, help="含 qlen-<maxq>/ 的目录")
    ap.add_argument("--maxq", default="10,50,200,1000", help="缓冲区大小列表，逗号分隔")
    ap.add_argument("--out", default="exp1-sweep.png", help="输出图片路径")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pb.setup_font()
    plt.rcParams["figure.dpi"] = args.dpi

    maxqs = [int(x) for x in args.maxq.replace("，", ",").split(",") if x.strip()]
    series = {}
    for n in maxqs:
        # 支持两种布局：<data-dir>/qlen-<N>（平铺）与 <data-dir>/exp1-maxq-<N>/qlen-<N>（扫描脚本产物）
        cands = [os.path.join(args.data_dir, "qlen-%d" % n),
                 os.path.join(args.data_dir, "exp1-maxq-%d" % n, "qlen-%d" % n)]
        d = next((c for c in cands if os.path.isdir(c)), None)
        if d is None:
            print("  [!] 找不到 maxq=%d 的数据（试过 %s）" % (n, " / ".join(cands)))
            continue
        got = load_one(d)
        if got is None:
            print("  [!] %s 没有可用数据，跳过" % d)
            continue
        series[n] = got
        rtt = got["rtt"][1]
        qb = got["qlen"][1]
        print("  maxq=%-5d RTT 中位 %7.1f ms | 队列 中位/最大 %7.0f/%7.0f KB | 理论上限 %.0f ms"
              % (n, float(np.median(rtt)) if len(rtt) else -1,
                 float(np.median(qb)) if len(qb) else -1,
                 float(np.max(qb)) if len(qb) else -1,
                 n * BYTES_PER_PKT * 8 / (BOTTLENECK_MBIT * 1e6) * 1000))
    if not series:
        print("错误: 没有任何 qlen-<maxq>/ 数据", file=sys.stderr)
        return 1

    labels = {}
    for n in sorted(series):
        limit_ms = n * BYTES_PER_PKT * 8 / (BOTTLENECK_MBIT * 1e6) * 1000
        labels[n] = "maxq=%-5d (理论上限 %.0f ms)" % (n, limit_ms)

    def draw(ax, key, legend=False, figsize=None):
        for i, n in enumerate(sorted(series)):
            t, v = series[n][key]
            if not len(t):
                continue
            if key == "qlen":                      # 队列做 0.2s 滑动中位
                v = smooth(t, v, 0.2)
            pb.mark_gaps(ax, t, v, COLORS[i % len(COLORS)], pb.auto_gap_threshold(t),
                         lw=1.1, label=labels[n] if legend else None)
        ax.grid(True, ls=":", lw=0.6, alpha=0.7)
        ax.margins(x=0.01)
        ax.set_ylim(bottom=0)
        ax.set_xlabel("时间 (s，以各自实验起点为 0)")
        if legend:
            ax.legend(loc="best", framealpha=0.9, fontsize=9)

    ttl = "实验1 缓冲区大小扫描（瓶颈 10 Mbit，RENO）：%s" % "、".join(
        "maxq=%d" % n for n in sorted(series))

    # ---- 三联图（cwnd / 队列 / RTT 各一个面板，四个档位叠在同一坐标系里）----
    fig, axes = plt.subplots(3, 1, figsize=(9.0, 8.4), sharex=True)
    for ax, key in zip(axes, ("cwnd", "qlen", "rtt")):
        draw(ax, key, legend=(ax is axes[0]))
        ax.set_xlabel("")
    axes[0].set_ylabel("拥塞窗口 cwnd (包)")
    axes[1].set_ylabel("瓶颈队列 r1-eth1 (KB，0.2s 滑动中位)")
    axes[2].set_ylabel("RTT (ms)")
    axes[2].set_xlabel("时间 (s，以各自实验起点为 0)")
    axes[0].legend(loc="lower right", framealpha=0.9, fontsize=8.5)
    axes[0].set_title(ttl, pad=14)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fig.savefig(args.out)
    plt.close(fig)
    print("写出 %s" % args.out)

    # ---- 多宫格：每档一行、每个指标一格，各自独立纵坐标，便于看单条曲线的趋势 ----
    rows = sorted(series)
    keys = (("cwnd", "拥塞窗口 (包)"), ("qlen", "瓶颈队列 (KB)"), ("rtt", "RTT (ms)"))
    fig2, axes2 = plt.subplots(len(rows), 3, figsize=(10.6, 1.65 * len(rows)),
                               sharex=True)
    axes2 = np.atleast_2d(axes2)
    for i, n in enumerate(rows):
        for j, (key, ylab) in enumerate(keys):
            ax = axes2[i][j]
            t, v = series[n][key]
            if len(t):
                if key == "qlen":
                    v = smooth(t, v, 0.2)
                pb.mark_gaps(ax, t, v, COLORS[i % len(COLORS)],
                             pb.auto_gap_threshold(t), lw=0.9)
            ax.grid(True, ls=":", lw=0.5, alpha=0.7)
            ax.margins(x=0.01)
            ax.set_ylim(bottom=0)
            ax.tick_params(labelsize=8)
            if i == 0:
                ax.set_title(ylab, fontsize=10.5)
            if j == 0:
                ax.set_ylabel("maxq=%d" % n, fontsize=10.5)
            if i == len(rows) - 1:
                ax.set_xlabel("时间 (s)", fontsize=9)
    fig2.suptitle("实验1：四档缓冲区各自的 cwnd / 队列长度 / RTT（每格独立纵坐标）",
                  fontsize=12, y=0.995)
    fig2.tight_layout()
    base0, ext0 = os.path.splitext(args.out)
    grid_path = "%s-grid%s" % (base0, ext0)
    fig2.savefig(grid_path)
    plt.close(fig2)
    print("写出 %s" % grid_path)

    # ---- 另外三张：每种指标单独一张，四个档位在同一坐标系里直接对比 ----
    base, ext = os.path.splitext(args.out)
    singles = [("cwnd", "拥塞窗口 cwnd (包)", "cwnd"),
               ("qlen", "瓶颈队列 r1-eth1 (KB，0.2s 滑动中位)", "qlen"),
               ("rtt", "等待时间 RTT (ms)", "rtt")]
    for key, ylab, tag in singles:
        fig1, ax1 = plt.subplots(figsize=(8.4, 3.4))
        draw(ax1, key, legend=True)
        ax1.set_ylabel(ylab)
        ax1.set_title("%s  ——  %s" % (ttl, ylab.split(" (")[0]), pad=12)
        path = "%s-%s%s" % (base, tag, ext)
        fig1.savefig(path)
        plt.close(fig1)
        print("写出 %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
