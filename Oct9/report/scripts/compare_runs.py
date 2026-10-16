#!/usr/bin/env python3
"""把两次（或多次）实验结果目录做对比汇总，输出 markdown 表格。

每个"实验目录"就是 run_lab.py 的输出根目录，里面按算法分子目录：

    <run-dir>/
        taildrop/{rtt.txt,qlen-r1eth1.txt,throughput-server.csv,...}
        red/...
        codel/...

典型用法（配合 run_compare.sh 的 A/B 两轮）：

    MPLCONFIGDIR=/tmp/mpl python3 compare_runs.py \
        Oct9/05-bufferbloat/runs/tso-off Oct9/05-bufferbloat/runs/tso-on \
        --labels "TSO 关" "TSO 开" --out Oct9/report/compare.md

指标（按 dynamic_bw 的六个相位分别统计）：
  * RTT 中位 / p95（ms）        —— rtt.txt
  * 线上吞吐均值（Mbit/s）      —— throughput-server.csv（服务端实际收到）
  * 正向队列中位（KB）          —— qlen-r1eth1.txt 的字节列
  * "队列解释度" = RTT / (队列字节 ÷ 相位速率)，≈1 表示延迟就是排队延迟
"""

import argparse
import os
import statistics
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plot_bufferbloat as pb                                        # noqa: E402

PHASE_RATE = {name: rate for (_, _, name), rate in
              zip(pb.PHASES, (100, 10, 1, 50, 1, 100))}


def load_queue(path):
    """qlen-*.txt -> [(epoch, packets, bytes)]，兼容两列(旧)格式。"""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, errors="replace") as fh:
        for line in fh:
            c = line.strip().split(",")
            if len(c) == 3:
                try:
                    rows.append((float(c[0]), int(c[1]), int(c[2])))
                except ValueError:
                    pass
            elif len(c) == 2:
                try:
                    rows.append((float(c[0]), int(c[1]), int(c[1]) * 1500))
                except ValueError:
                    pass
    return sorted(rows)


def run_t0(run_dir, algo, fallback):
    """该算法的实验时钟起点：t0.txt > iperf 反推 > 第一条样本。"""
    t0 = pb.read_t0(os.path.join(run_dir, algo), verbose=False)
    return t0 if t0 else fallback


def algo_available(run_dir, algo):
    """该 run 目录里这个算法的数据在不在（只跑 taildrop 时 red/codel 就是缺的）。"""
    d = os.path.join(run_dir, algo)
    return any(os.path.exists(os.path.join(d, f)) for f in
               ("rtt.txt", "qlen-r1eth1.txt", "throughput-server.csv", "throughput.csv"))


def phase_stats(run_dir, algo, phase):
    a, b, name = phase
    rate = PHASE_RATE[name]
    d = os.path.join(run_dir, algo)

    rtts = []
    t, v = pb.read_rtt(os.path.join(d, "rtt.txt"))
    if len(t):
        t0 = run_t0(run_dir, algo, t[0])
        rtts = [x for tt, x in zip(t, v) if a <= tt - t0 < b]
    que = load_queue(os.path.join(d, "qlen-r1eth1.txt"))
    qb = []
    if que:
        t0q = run_t0(run_dir, algo, que[0][0])
        qb = [by for tt, _, by in que if a <= tt - t0q < b]
    thr = []
    for path in ("throughput-server.csv", "throughput.csv"):
        ts, vs = pb.read_throughput(os.path.join(d, path), max_gap=0)
        if len(ts):
            thr = [x for tt, x in zip(ts, vs) if a <= tt < b]
            if thr:
                break
    out = {"n": len(rtts), "rate": rate}
    if rtts:
        s = sorted(rtts)
        out["rtt_med"] = statistics.median(s)
        out["rtt_p95"] = s[min(len(s) - 1, int(0.95 * len(s)))]
    if qb:
        out["q_kb"] = statistics.median(qb) / 1024.0
        predicted = statistics.median(qb) * 8 / (rate * 1e6)     # 秒
        if out.get("rtt_med") and predicted > 0:
            out["explain"] = (out["rtt_med"] / 1000.0) / predicted
    if thr:
        out["thr"] = statistics.mean(thr)
    return out


def rolling_median(values, window):
    """滑动中位数，用于让队列曲线可读（窗口=样本数，1 表示不平滑）。"""
    if window <= 1 or len(values) <= window:
        return list(values)
    out = []
    half = window // 2
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(statistics.median(values[lo:hi]))
    return out


def _draw_phases(ax, labels=True):
    for a, b, name in pb.PHASES:
        if (a // 10) % 2 == 0:
            ax.axvspan(a, b, color="#f2f4f4", zorder=0)
        ax.axvline(a, color="#d5d8dc", lw=0.7, ls=":", zorder=1)
        if labels:
            ax.text((a + b) / 2.0, 1.01, name, transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", fontsize=8, color="#566573")


def _queue_for_figure(run, algo, queue_smooth):
    """返回 (相对时间, KB)，队列曲线做时间窗滑动中位。"""
    que = load_queue(os.path.join(run, algo, "qlen-r1eth1.txt"))
    if not que:
        return None, None
    x0 = run_t0(run, algo, que[0][0])
    tq = [x[0] for x in que]
    dt = statistics.median([b - a for a, b in zip(tq, tq[1:])]) if len(tq) > 2 else 0.02
    win = max(1, int(round(queue_smooth / dt))) if queue_smooth > 0 else 1
    return [x - x0 for x in tq], rolling_median([x[2] / 1024.0 for x in que], win)


def make_figure(runs, labels, algo, out_png, queue_smooth=0.3,
                rtt_scale="linear", cut_axis="2000:4000", band_style="scale"):
    """各 run 的对比图。RTT 面板按 break_axis 断轴（低延迟放大、高延迟仍可见）；
    算法名以 qlen- 开头（实验1）时额外画 cwnd 与队列面板。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator

    pb.setup_font()
    colors = ["#c0392b", "#2471a3", "#1e8449", "#7f8c8d", "#8e44ad"]
    repro = algo.startswith("qlen-")
    segments = None                    # 读数据后再定（见下）

    # ---- 读数据，确定 RTT 上界 ----
    loaded = []                       # (lb, color, rtt_t, rtt_v, cwnd_t, cwnd_v, q_t, q_kb)
    rtt_max = 0.0
    for i, (run, lb) in enumerate(zip(runs, labels)):
        c = colors[i % len(colors)]
        d = os.path.join(run, algo)
        t, v = pb.read_rtt(os.path.join(d, "rtt.txt"))
        tt = vv = None
        if len(t):
            tt = t - (pb.read_t0(d, verbose=False) or t[0])
            vv = v
            rtt_max = max(rtt_max, float(max(v)))
        ct = cv = None
        if repro:
            ct, cv = pb.read_cwnd(os.path.join(d, "cwnd.txt"))
            if len(ct):
                ct = ct - (pb.read_t0(d, verbose=False) or ct[0])
            else:
                ct = cv = None
        tq, bq = _queue_for_figure(run, algo, queue_smooth)
        loaded.append((lb, c, tt, vv, ct, cv, tq, bq))
    if rtt_max <= 0 and not any(x[6] is not None for x in loaded):
        print("  [!] %s 没有可画的数据" % algo)
        return None
    segments, cuts = pb.resolve_segments(cut_axis, max(rtt_max, 1.0))

    # ---- 建面板：RTT 段（可多段） + cwnd/队列 ----
    n_bands = len(segments)
    n_extra = 2 if repro else 1
    nrows = n_bands + n_extra
    ordered = list(reversed(segments))
    rtt_heights = ([hi - lo for lo, hi in ordered] if band_style == "scale"
                   else [1.0] * n_bands)
    scale = sum(rtt_heights) / 4000.0 if sum(rtt_heights) > 0 else 1.0
    heights = [h / scale for h in rtt_heights]
    extra_h = (sum(heights) * 0.45) / max(n_extra, 1)     # 队列/cwnd 面板约占 RTT 总高的 45%
    heights += [extra_h] * n_extra
    fig = plt.figure(figsize=(9.5, 1.9 * nrows + 1.2))
    gs = fig.add_gridspec(nrows, 1, height_ratios=heights, hspace=0.14)
    axes = []
    for i in range(nrows):
        axes.append(fig.add_subplot(gs[i], sharex=None if i == 0 else axes[0]))

    if n_bands > 1:
        for i, (lo, hi) in enumerate(ordered):     # axes[0] 在最上 -> 最大的一段
            axes[i].set_ylim(lo, hi)
            axes[i].yaxis.set_major_locator(MaxNLocator(nbins=4))
        d = 0.012
        for i in range(n_bands - 1):
            upper, lower = axes[i], axes[i + 1]
            upper.spines["bottom"].set_visible(False)
            lower.spines["top"].set_visible(False)
            for ax, ys in ((upper, (-d, d)), (lower, (1 - d, 1 + d))):
                kw = dict(transform=ax.transAxes, color="#2c3e50", clip_on=False, lw=1.0)
                ax.plot((-d, d), ys, **kw)
                ax.plot((1 - d, 1 + d), ys, **kw)
    else:
        axes[0].set_ylim(0, segments[0][1])

    # ---- 画 RTT ----
    gap_info = {}
    for i in range(n_bands):
        ax = axes[i]
        for lb, c, tt, vv, _ct, _cv, _tq, _bq in loaded:
            if tt is None:
                continue
            vals = np.maximum(vv, pb.RTT_FLOOR_MS) if rtt_scale == "log" else vv
            gth = pb.auto_gap_threshold(tt)
            ng = pb.mark_gaps(ax, tt, vals, c, gth, lw=1.0, label=lb if i == 0 else None)
            gap_info[i] = (gap_info.get(i, (0, gth))[0] + ng, gth)
        if rtt_scale == "log":
            ax.set_yscale("log")
            ax.set_ylim(bottom=pb.RTT_FLOOR_MS)
        if not repro:
            _draw_phases(ax, labels=(i == 0))

    # ---- 画 cwnd / 队列 ----
    for j in range(n_extra):
        ax = axes[n_bands + j]
        for lb, c, _tt, _vv, ct, cv, tq, bq in loaded:
            if repro and j == 0:
                if ct is None:
                    continue
                gth = pb.auto_gap_threshold(ct)
                ng = pb.mark_gaps(ax, ct, cv, c, gth, lw=1.0, label=lb)
            else:
                if tq is None:
                    continue
                gth = pb.auto_gap_threshold(tq)
                ng = pb.mark_gaps(ax, tq, bq, c, gth, lw=1.2, label=lb)
            gap_info[n_bands + j] = (gap_info.get(n_bands + j, (0, gth))[0] + ng, gth)
        ax.set_ylim(bottom=0)

    # ---- 收尾 ----
    for ax in axes:
        ax.grid(True, which="both" if rtt_scale == "log" else "major",
                ls=":", lw=0.6, alpha=0.7)
        ax.margins(x=0.01)
    for ax in axes[:-1]:                  # 手动 sharex 时需自己关掉中间面板的 x 刻度标签
        ax.tick_params(labelbottom=False)
    axes[-1].tick_params(labelbottom=True)
    rtt_ax = axes[0] if n_bands > 1 else axes[0]
    rtt_ax.set_ylabel("等待时间 RTT (ms%s)"
                      % ("，对数轴" if rtt_scale == "log" else
                         ("，省略 %s" % "、".join("%g~%g" % c for c in cuts)
                          if cuts else "")), fontsize=9)
    if repro:
        axes[n_bands].set_ylabel("cwnd (包)", fontsize=9)
        axes[n_bands + 1].set_ylabel("瓶颈队列 (KB)", fontsize=9)
    else:
        axes[n_bands].set_ylabel("正向队列 r1-eth1 (KB)", fontsize=9)

    handles, labs = axes[0].get_legend_handles_labels()
    ng = gap_info.get(0, (0, 0.3))[0]
    if ng:
        handles.append(Line2D([], [], color=pb.GAP_COLOR, lw=3.0))
        labs.append("采样空档 (>%.2g s 无样本)" % gap_info[0][1])
    axes[-1].set_xlabel("时间 (s，以各自实验起点为 0)")
    # 图级 legend，避免压住 xlabel
    fig.legend(handles, labs, loc="upper center", bbox_to_anchor=(0.5, 0.0),
               ncol=max(2, len(labs)), framealpha=0.9, fontsize=9)
    axes[0].set_title("%s：TSO/GSO 开 vs 关（%s）" % (
        algo, "瓶颈固定 10 Mbit" if repro else "相位 100/10/1/50/1/100 Mbit"), pad=26)
    fig.savefig(out_png)
    plt.close(fig)
    return out_png

def fmt(value, spec="%.1f"):
    return "n/a" if value is None else spec % value


def main():
    ap = argparse.ArgumentParser(description="两次实验结果对比汇总")
    ap.add_argument("run_dirs", nargs="+", help="结果目录（1 个或多个）")
    ap.add_argument("--labels", nargs="*", default=None, help="每个目录的名字")
    ap.add_argument("--algos", default="taildrop,red,codel", help="算法，逗号分隔")
    ap.add_argument("--out", default=None, help="同时写入 markdown 文件")
    ap.add_argument("--figure-dir", default=None,
                    help="每个算法再画一张 A/B 对比图（2 面板：RTT / 正向队列字节）")
    ap.add_argument("--figure-algos", default=None,
                    help="只给这些算法出对比图，默认=--algos")
    ap.add_argument("--quiet", action="store_true",
                    help="不打印 markdown 表（只出图）")
    ap.add_argument("--no-t0", action="store_true",
                    help="不用 t0.txt / iperf 反推，退回以第一条样本为 0（旧行为）")
    ap.add_argument("--rtt-scale", choices=("log", "linear"), default="linear",
                    help="对比图里 RTT 面板的纵轴，默认 linear（配合 --break-axis）")
    ap.add_argument("--cut", dest="cut_axis", default="none",
                    help="RTT 纵轴省略的区间（ms），如 2000:4000；默认 none=普通线性轴")
    ap.add_argument("--band-style", choices=("scale", "equal"), default="scale",
                    help="省略区间后各段高度：scale=比例尺一致（默认）/ equal=等高")
    ap.add_argument("--queue-smooth", type=float, default=0.3,
                    help="对比图里队列曲线的滑动中位窗口（秒；0=不平滑），默认 0.3")
    args = ap.parse_args()

    runs = [os.path.abspath(p) for p in args.run_dirs]
    for r in runs:
        if not os.path.isdir(r):
            print("错误: 目录不存在 %s" % r, file=sys.stderr)
            return 1
    labels = args.labels or [os.path.basename(r.rstrip("/")) for r in runs]
    algos = [a.strip() for a in args.algos.split(",") if a.strip()]

    lines = []
    quiet = args.quiet

    def emit(text=""):
        if not quiet:
            print(text)
        lines.append(text)

    emit("# 对比汇总")
    emit()
    emit("目录：" + "，".join("%s=%s" % (lb, r) for lb, r in zip(labels, runs)))
    emit()

    skipped = []
    for algo in algos:
        present = [lb for r, lb in zip(runs, labels) if algo_available(r, algo)]
        if not present:
            skipped.append(algo)
            continue
        if len(present) < len(runs):
            emit("> 注意：%s 只在 %s 里有数据，其余列为 n/a。"
                 % (algo, "、".join(present)))
            emit()
        emit("## %s" % algo)
        emit()
        emit("RTT 中位 / p95（ms）：")
        emit()
        emit("| 相位 | " + " | ".join(labels) + " |")
        emit("|---|" + "---|" * len(runs))
        for phase in pb.PHASES:
            cells = []
            for r in runs:
                s = phase_stats(r, algo, phase)
                cells.append("%s / %s" % (fmt(s.get("rtt_med")), fmt(s.get("rtt_p95"))))
            emit("| %s %d-%ds | %s |" % (phase[2], phase[0], phase[1], " | ".join(cells)))
        emit()
        emit("线上吞吐均值（Mbit/s）/ 正向队列中位（KB）/ 队列解释度：")
        emit()
        emit("| 相位 | " + " | ".join(labels) + " |")
        emit("|---|" + "---|" * len(runs))
        for phase in pb.PHASES:
            cells = []
            for r in runs:
                s = phase_stats(r, algo, phase)
                cells.append("%s / %s / %s" % (fmt(s.get("thr")), fmt(s.get("q_kb")),
                                               fmt(s.get("explain"), "%.2f")))
            emit("| %s %d-%ds | %s |" % (phase[2], phase[0], phase[1], " | ".join(cells)))
        emit()

    if skipped:
        emit("> 以下算法在所有结果目录里都没有数据，已跳过：%s" % "、".join(skipped))
        emit()
    emit("> 队列解释度 = RTT ÷ (队列字节 ÷ 相位速率)。≈1 说明延迟就是排队延迟；"
         "远大于 1 说明延迟来自队列之外；远小于 1 说明队列没被真正排空。")
    if args.no_t0:
        pb.read_t0 = lambda data_dir, verbose=True: None      # 退回旧行为

    if args.figure_dir:
        os.makedirs(args.figure_dir, exist_ok=True)
        for algo in (args.figure_algos or args.algos).split(","):
            algo = algo.strip()
            if not algo:
                continue
            if not any(algo_available(r, algo) for r in runs):
                continue
            png = os.path.join(args.figure_dir, "compare-%s.png" % algo)
            got = make_figure(runs, labels, algo, png, queue_smooth=args.queue_smooth,
                              rtt_scale=args.rtt_scale, cut_axis=args.cut_axis,
                              band_style=args.band_style)
            print("画出 %s" % png if got else "  [!] %s 没有可画的数据" % algo)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print("\n已写出 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
