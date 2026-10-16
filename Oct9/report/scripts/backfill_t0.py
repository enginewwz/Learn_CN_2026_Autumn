#!/usr/bin/env python3
"""给已有实验目录补写 t0.txt（实验时钟起点），让新旧数据都能正确对齐相位栅格。

新版 run_lab.py 会自己写 t0.txt（在 start_rtt_monitor 返回时打点，≈ dynamic_bw 相位
时钟的 0 点）。更早跑出来的数据没有这个文件，只能事后补：

  --mode iperf  用 iperf 客户端第一条逐秒行反推：t0 = epoch - end_s + 0.03
                实测误差 < 50 ms。适用于 mitigate 系列（start_iperf 在
                start_rtt_monitor 之前，这个值≈相位时钟起点）。
  --mode first  取 rtt/qlen 中最早的样本时刻，不做任何假设。适用于 reproduce
                （监视器先于 iperf 启动，且没有相位栅格要求）。

用法：
    python3 backfill_t0.py Oct9/05-bufferbloat/{taildrop,red,codel} \
        Oct9/05-bufferbloat/runs/tso-{off,on}/taildrop --mode iperf
    python3 backfill_t0.py Oct9/05-bufferbloat/qlen-1000 --mode first
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plot_bufferbloat as pb                                        # noqa: E402


def t0_from_iperf(d):
    for name in ("throughput.csv", "throughput-server.csv"):
        got = pb._first_interval_epoch(os.path.join(d, name))
        if got:
            epoch, end_s = got
            return epoch - end_s + 0.03, "iperf 首行反推 (epoch-end_s+0.03)"
    return None, None


def t0_from_first(d):
    times = []
    for name in ("rtt.txt", "qlen.txt", "qlen-r1eth1.txt", "cwnd.txt", "tcp.txt"):
        path = os.path.join(d, name)
        if not os.path.exists(path):
            continue
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                try:
                    times.append(float(line.split(",")[0]))
                except ValueError:
                    continue
                break
    if not times:
        return None, None
    return min(times), "最早样本时刻"


def main():
    ap = argparse.ArgumentParser(description="补写实验目录的 t0.txt")
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--mode", choices=("iperf", "first"), required=True)
    ap.add_argument("--force", action="store_true", help="已有 t0.txt 也覆盖")
    args = ap.parse_args()

    rc = 0
    for d in args.dirs:
        if not os.path.isdir(d):
            print("  [!] 目录不存在: %s" % d)
            rc = 1
            continue
        target = os.path.join(d, "t0.txt")
        if os.path.exists(target) and not args.force:
            print("  = %-32s 已有 t0.txt，跳过（--force 可覆盖）" % d)
            continue
        t0, src = (t0_from_iperf(d) if args.mode == "iperf" else t0_from_first(d))
        if t0 is None:
            print("  [!] %-32s 没有可用于推算的数据" % d)
            rc = 1
            continue
        with open(target, "w") as fh:
            fh.write("%.6f\n" % t0)
        with open(os.path.join(d, "t0.source"), "w") as fh:
            fh.write("%s: %s\n" % (args.mode, src))
        print("  + %-32s t0=%.6f  (%s)" % (d, t0, src))
    return rc


if __name__ == "__main__":
    sys.exit(main())
