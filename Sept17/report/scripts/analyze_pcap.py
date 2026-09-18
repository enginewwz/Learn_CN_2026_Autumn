#!/usr/bin/python3
"""分析环形拓扑抓包 h1ping-c1h2result.pcapng，输出统计数据和图表。

该脚本只依赖 scapy / numpy / matplotlib，用于实验报告中“环路广播风暴”一节。
运行方式（在仓库根目录下）：

    python3 Sept17/report/scripts/analyze_pcap.py
"""

import collections
import hashlib
import json
import os

from scapy.all import ARP, ICMP, IP, Ether, PcapReader

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.dirname(HERE)
FIG_DIR = os.path.join(REPORT_DIR, "figures")
DATA_DIR = os.path.join(REPORT_DIR, "data")
PCAP = os.path.join(
    REPORT_DIR, "..", "03-hub+switch", "hub", "h1ping-c1h2result.pcapng"
)

os.makedirs(FIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def collect():
    r = PcapReader(PCAP)
    first = last = None
    bucket = collections.defaultdict(lambda: [0, 0])  # 10ms -> [packets, bytes]
    mac_src = collections.Counter()
    mac_dst = collections.Counter()
    mac_pair = collections.Counter()
    ethertype = collections.Counter()
    kind = collections.Counter()
    signatures = collections.Counter()
    sizes = collections.Counter()
    icmp_req_sig = collections.Counter()
    arp_op = collections.Counter()
    ip_pair = collections.Counter()
    n = 0

    for pkt in r:
        n += 1
        t = float(pkt.time)
        if first is None:
            first = t
        last = t
        b = int((t - first) * 100)
        bucket[b][0] += 1
        bucket[b][1] += len(pkt)
        sizes[len(pkt)] += 1
        signatures[hashlib.md5(bytes(pkt)).hexdigest()] += 1

        e = pkt[Ether]
        mac_src[e.src] += 1
        mac_dst[e.dst] += 1
        mac_pair[(e.src, e.dst)] += 1
        ethertype[hex(e.type)] += 1

        if ARP in pkt:
            kind["ARP"] += 1
            arp_op[pkt[ARP].op] += 1
        elif ICMP in pkt:
            ic = pkt[ICMP]
            kind["ICMP request" if ic.type == 8 else "ICMP reply"] += 1
            if ic.type == 8:
                icmp_req_sig[(ic.id, ic.seq)] += 1
        else:
            kind["other"] += 1

        if IP in pkt:
            ip_pair[(pkt[IP].src, pkt[IP].dst)] += 1

    duration = last - first
    series = [
        {"t": k / 100.0, "packets": v[0], "bytes": v[1]}
        for k, v in sorted(bucket.items())
    ]
    stats = {
        "packets": n,
        "duration_s": duration,
        "avg_pps": n / duration,
        "total_bytes": sum(v[1] for v in bucket.values()),
        "avg_bps": sum(v[1] for v in bucket.values()) * 8 / duration,
        "avg_size": sum(k * v for k, v in sizes.items()) / n,
        "unique_frames": len(signatures),
        "amplification": n / len(signatures),
        "kind": dict(kind),
        "ethertype": dict(ethertype),
        "mac_src": mac_src.most_common(10),
        "mac_dst": mac_dst.most_common(10),
        "mac_pair": [[list(k), v] for k, v in mac_pair.most_common(10)],
        "sizes": sizes.most_common(10),
        "icmp_req_unique": len(icmp_req_sig),
        "icmp_req_total": sum(icmp_req_sig.values()),
        "icmp_req_top": [[list(k), v] for k, v in icmp_req_sig.most_common(5)],
        "arp_op": {str(k): v for k, v in arp_op.items()},
        "ip_pair": [[list(k), v] for k, v in ip_pair.most_common(10)],
        "series": series,
    }
    with open(os.path.join(DATA_DIR, "pcap_stats.json"), "w") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print(f"packets={n} duration={duration:.3f}s avg_pps={stats['avg_pps']:.1f}")
    print(f"unique_frames={stats['unique_frames']} amplification={stats['amplification']:.1f}x")
    print(f"avg_bps={stats['avg_bps']/1e6:.2f} Mbit/s avg_size={stats['avg_size']:.1f} B")
    print("kind:", stats["kind"])
    print("ethertype:", stats["ethertype"])
    print("arp_op:", stats["arp_op"])
    print("icmp_req_unique:", stats["icmp_req_unique"], "total:", stats["icmp_req_total"])
    print("mac_src:", stats["mac_src"])
    print("mac_dst:", stats["mac_dst"])
    print("ip_pair:", stats["ip_pair"])
    print("sizes:", stats["sizes"])
    return stats


def plot(stats):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    # 中文字体
    for cand in ("Noto Sans CJK SC", "Noto Serif CJK SC", "WenQuanYi Zen Hei"):
        if any(f.name == cand for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [cand]
            break
    plt.rcParams["axes.unicode_minus"] = False

    series = stats["series"]
    t = [p["t"] for p in series]
    pkts = [p["packets"] / 0.01 for p in series]  # packets/s
    mbps = [p["bytes"] * 8 / 0.01 / 1e6 for p in series]

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    axes[0].plot(t, pkts, color="#c0392b", lw=1.2)
    axes[0].set_ylabel("数据包速率 (packet/s)")
    axes[0].set_title("环形拓扑下 h1 ping h2 时 h1-eth0 观察到的广播风暴")
    axes[0].grid(alpha=0.3)
    axes[1].plot(t, mbps, color="#2471a3", lw=1.2)
    axes[1].set_ylabel("吞吐 (Mbit/s)")
    axes[1].set_xlabel("抓包开始后的时间 (s)")
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, "ring_broadcast_storm_timeseries.png"), dpi=160)
    plt.close(fig)

    # 帧类型占比
    kinds = stats["kind"]
    labels = list(kinds.keys())
    values = [kinds[k] for k in labels]
    colors = ["#e67e22", "#2980b9", "#27ae60", "#8e44ad"]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    bars = ax.bar(labels, values, color=colors[: len(labels)])
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:,}\n({v/sum(values)*100:.1f}%)",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("帧数量")
    ax.set_ylim(0, max(values) * 1.22)
    ax.set_title("广播风暴中的帧类型构成")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, "ring_frame_composition.png"), dpi=160)
    plt.close(fig)

    # 帧长分布
    sizes = stats["sizes"]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    xs = [s[0] for s in sizes]
    ys = [s[1] for s in sizes]
    bars = ax.bar([str(x) for x in xs], ys, color="#16a085")
    for b, v in zip(bars, ys):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:,}", ha="center",
                va="bottom", fontsize=8)
    ax.set_xlabel("以太网帧长度 (Byte)")
    ax.set_ylabel("帧数量")
    ax.set_ylim(0, max(ys) * 1.18)
    ax.set_title("广播风暴中的帧长分布")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, "ring_frame_sizes.png"), dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    plot(collect())
