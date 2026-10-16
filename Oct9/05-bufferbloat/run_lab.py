#!/usr/bin/env python3
"""Oct9/05-bufferbloat 批量实验驱动：1 个 reproduce + 3 个 mitigate。

设计原则：**不改动** reproduce_bufferbloat.py / mitigate_bufferbloat.py / utils.py，
只在运行时替换两个 utils 函数：

    * utils.start_iperf -> 加 -i 1，iperf 客户端吞吐率每 1 秒一行解析进
                           <实验目录>/throughput.csv（画图用，**不打印到终端**），
                           原始文本另存 iperf-raw.txt / iperf-server-raw.txt；
    * utils.rtt_monitor -> 单进程 `ping -D -i 0.1`：时间戳=发送时刻、行序=发送序。
                           原版每 100 ms 起一个 ping 线程、互不等待，RTT 大时会有
                           上百个探针同时在途，文件按**完成**顺序落盘、时间戳却是
                           **发送**时刻，出现逆序与错位；--old-rtt-monitor 可切回原版。

cwnd.txt / qlen.txt 仍由 utils.py 原来的监视器产生。

用法：
    sudo python3 run_lab.py                      # qlen-1000 + taildrop/red/codel
    sudo python3 run_lab.py --maxq 10            # 换小缓冲
    sudo python3 run_lab.py --algos codel        # 只跑一个缓解算法
    sudo python3 run_lab.py --quiet              # 终端只留进度，不要摘要
    python3 run_lab.py --dry-run                 # 只打印计划，不需要 root
    python3 run_lab.py --selftest                # 离线自检 iperf 解析器，不需要 root
"""

import argparse
import multiprocessing as mp
import os
import re
import runpy
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

# utils.py 的监视器用 multiprocessing.Process 传 net 对象（含 pty/Popen，无法 pickle），
# 必须走 fork。Python 3.14 的默认 start method 已改成 forkserver，会直接失败，
# 所以这里强制 fork（3.12 及更早本来就是 fork，无副作用）。
try:
    mp.set_start_method('fork', force=True)
except (RuntimeError, ValueError):                                # pragma: no cover
    pass

LAB_DIR = os.path.dirname(os.path.abspath(__file__))
if LAB_DIR not in sys.path:
    sys.path.insert(0, LAB_DIR)
REPRODUCE = 'reproduce_bufferbloat.py'
MITIGATE = 'mitigate_bufferbloat.py'
SUPPORTED_ALGOS = ('taildrop', 'red', 'codel')

# iperf 2.x 输出行，例如：
#   [  1] 0.0000-1.0000 sec  1.25 MBytes  10.5 Mbits/sec   (2.1.x 新格式)
#   [  3]  1.0- 2.0 sec  1.15 MBytes  9.65 Mbits/sec        (老格式)
IPERF_RE = re.compile(
    r'^\[\s*\d+\]\s+([\d.]+)-\s*([\d.]+)\s+sec\s+'
    r'([\d.]+)\s+([KMGT]?Bytes)\s+'
    r'([\d.]+)\s+([KMGT]?bits/sec)')

_BYTE_SCALE = {'': 1e-6, 'K': 1e-3, 'M': 1.0, 'G': 1e3, 'T': 1e6}   # -> MBytes
_BIT_SCALE = {'': 1e-6, 'K': 1e-3, 'M': 1.0, 'G': 1e3, 'T': 1e6}    # -> Mbit/s


def _unit_prefix(unit):
    """'MBytes'/'Mbits/sec' -> 'M'；'Bytes'/'bits/sec'（无前缀）-> ''。

    注意不能直接取 unit[0]：无前缀时首字母是 'B'/'b'，会 KeyError
    （队列打满、吞吐掉到 0 时 iperf 就会打印 '0.000 Bytes  0.000 bits/sec'）。
    """
    first = (unit or '').strip()[:1].upper()
    return first if first in ('K', 'M', 'G', 'T') else ''


def _to_mb(value, unit):
    return float(value) * _BYTE_SCALE[_unit_prefix(unit)]


def _to_mbit(value, unit):
    return float(value) * _BIT_SCALE[_unit_prefix(unit)]


class ThroughputLogger(threading.Thread):
    """把 iperf 客户端 stdout 解析成 CSV（不打印到终端），原始文本另存一份备查。"""

    HEADER = 'epoch,start_s,end_s,transfer_MB,rate_Mbit,kind\n'

    def __init__(self, pipe, csv_path, raw_path, tag):
        super().__init__(daemon=True, name='iperf-pump')
        self.pipe = pipe
        self.csv_path = csv_path
        self.raw_path = raw_path
        self.tag = tag
        self.intervals = []      # [(end_s, Mbit/s), ...]
        self.summary = None      # 整段测试的汇总速率
        self.lines = 0           # iperf 原始输出的行数
        self.rows = 0            # 解析出的 CSV 数据行数
        self.parse_errors = 0    # 单行解析失败次数（不会中断采集）
        self.connect_failed = False

    def run(self):
        try:
            with open(self.csv_path, 'w', buffering=1) as fh, \
                    open(self.raw_path, 'w', buffering=1) as raw_fh:
                fh.write('# experiment=%s\n' % self.tag)
                fh.write(self.HEADER)
                for raw in iter(self.pipe.readline, b''):
                    line = raw.decode('utf-8', 'replace').rstrip('\n')
                    self.lines += 1
                    raw_fh.write(line + '\n')        # iperf 原始文本，等价于原来打到终端的内容
                    if 'connect failed' in line or 'Connection refused' in line:
                        self.connect_failed = True
                    m = IPERF_RE.match(line.strip())
                    if not m:
                        continue
                    # 单行出错不能弄死整个采集线程，否则这一轮后续数据全丢
                    try:
                        start, end, xfer, xunit, rate, runit = m.groups()
                        start, end = float(start), float(end)
                        mb, mbit = _to_mb(xfer, xunit), _to_mbit(rate, runit)
                    except Exception as exc:                      # noqa: BLE001
                        self.parse_errors += 1
                        if self.parse_errors <= 3:
                            sys.stderr.write('  [warn] 无法解析 iperf 行 %r: %r\n' % (line, exc))
                        continue
                    kind = 'summary' if start == 0.0 and (end - start) > 1.5 else 'interval'
                    fh.write('%.6f,%.4f,%.4f,%.4f,%.4f,%s\n'
                             % (time.time(), start, end, mb, mbit, kind))
                    self.rows += 1
                    if kind == 'summary':
                        self.summary = mbit
                    else:
                        self.intervals.append((end, mbit))
        except Exception as exc:                                  # noqa: BLE001
            sys.stderr.write('  [warn] iperf 输出泵异常: %r\n' % (exc,))

    def rate_stats(self):
        rates = [r for _, r in self.intervals]
        if not rates:
            return None
        return min(rates), sum(rates) / len(rates), max(rates)


# --------------------------------------------------------------------------
# 打补丁：utils.start_iperf -> 带 -i 1、结果落盘的版本
# --------------------------------------------------------------------------
import utils                                                   # noqa: E402

_CURRENT = {'csv': None, 'tag': ''}
_CFG = {'rtt_interval': 0.1, 'qlen_interval': 0.02, 'disable_offload': True}
_PUMPS = []     # ThroughputLogger 线程：[0]=客户端速率, [1]=服务端(线上)速率
_EXTRAS = []    # 额外 qdisc 队列监视进程


def _wait_for_iperf_server(h2, timeout=10.0):
    """等 h2 上的 iperf 服务端真正 listen 5001。

    原实现 h2.popen('iperf -s') 之后**立刻**启动客户端，存在竞态：客户端先跑起来
    就会 'tcp connect failed: Connection refused'，整轮实验没有任何流量（实测
    三个 mitigate 实验 3/3 全部踩中）。这里轮询 ss 直到出现监听者再发客户端。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = h2.cmd('ss -ltn 2>/dev/null | grep -c 5001')
        try:
            if int((out or '0').strip()) > 0:
                return True
        except ValueError:
            pass
        time.sleep(0.1)
    return False


def _qdisc_backlog_monitor(net, node_name, dev, fname, interval=0.02):
    """采样某接口 qdisc 的排队长度，写 '<epoch>, <包数>, <字节数>'。

    同时记字节数很重要：开了 TSO/GSO 时一个 skb 可能有几十 KB，只数包会把队列
    严重低估（taildrop 那轮 244 包在 50 Mbit 下"看起来"只有 60 ms 延迟，
    实际按字节算是秒级）。

    lab 的 qlen 监视器只看 r1-eth1（正向），而 dynamic_bw 把 h2-eth0（回程）也
    限速了、AQM 却只在 r1-eth1，所以四个接口都自己采。
    """
    node = net.get(node_name)
    try:
        with open(fname, 'w', buffering=1) as fh:
            while True:
                t = time.time()
                out = node.cmd('tc -s qdisc show dev %s' % dev) or ''
                pairs = [(int(p), int(b)) for b, p in
                         re.findall(r'backlog\s(\d+)b\s(\d+)p', out)]
                pk, by = max(pairs) if pairs else (0, 0)
                fh.write('%f, %d, %d\n' % (t, pk, by))
                time.sleep(interval)
    except Exception as exc:                                      # noqa: BLE001
        sys.stderr.write('  [warn] %s qdisc 监视器异常: %r\n' % (dev, exc))


def _tcp_monitor(net, fname, interval=0.02):
    """采样 h1 上 iperf 那条 TCP 连接的**内核侧**指标，写
    '<epoch>, srtt_ms, cwnd, unacked, retrans, sendq_bytes>'。

    内核记录的 RTT 与用户态 ping 的 RTT 相互独立：两者同时跳 -> 路径真的慢了；
    只有 ping 跳 -> 是用户态探针被调度/阻塞的伪影。
    """
    h1, h2 = net.get('h1', 'h2')
    ip = h2.IP()
    try:
        with open(fname, 'w', buffering=1) as fh:
            fh.write('# epoch, srtt_ms, cwnd, unacked, retrans, sendq_bytes\n')
            while True:
                t = time.time()
                # 注意: ss -tinm 的 rtt:/cwnd: 在**第二行**（socket 行的续行），
                # 不能用 head -1，否则全是 0
                out = h1.cmd('ss -tinm dst %s 2>/dev/null' % ip) or ''
                srtt = re.search(r'rtt:([\d.]+)/', out)
                cwnd = re.search(r'cwnd:(\d+)', out)
                una = re.search(r'unacked:(\d+)', out)
                rtx = re.search(r'retrans:([\d/]+)', out)
                sq = re.search(r'send (\d+)', out)
                fh.write('%f, %s, %s, %s, %s, %s\n' % (
                    t, srtt.group(1) if srtt else 0, cwnd.group(1) if cwnd else 0,
                    una.group(1) if una else 0,
                    (rtx.group(1).split('/')[0] if rtx else 0),
                    sq.group(1) if sq else 0))
                time.sleep(interval)
    except Exception as exc:                                      # noqa: BLE001
        sys.stderr.write('  [warn] tcp 监视器异常: %r\n' % (exc))


def _disable_offload(net):
    """关掉各接口的 TSO/GSO/GRO。

    mininet 的 TCLink 只做了 `ethtool -K gro off`，TSO/GSO 仍开着：一个 skb 最多
    ~64 KB，而 qdisc 的 limit 是按**包**算的，于是 lab 里"1000 包"的缓冲实际能存
    ~60 MB。实测 taildrop 队列 11~17 MB（平均 50 KB/包），在 50/100 Mbit 下就是
    2.4 s / 0.92 s 的排队延迟 —— 与 ping 实测 2.42 s / 0.94 s 几乎完全一致。
    关掉后 1 包 ≈ 1.5 KB，"1000 包"≈ 1.5 MB，才符合脚本的本意。
    """
    for name in ('h1', 'r1', 'h2'):
        node = net.get(name)
        for intf in node.intfList():
            node.cmd('ethtool -K %s tso off gso off gro off 2>/dev/null' % intf.name)


def start_iperf_logged(net, duration):
    h1, h2 = net.get('h1', 'h2')
    if _CFG['disable_offload']:
        _disable_offload(net)
        print('  已关闭各接口 TSO/GSO/GRO（让"1000 包"≈1.5 MB，而非 ~60 MB）')
    csv_path = _CURRENT['csv']
    outdir = os.path.dirname(csv_path)
    raw_path = os.path.join(outdir, 'iperf-raw.txt')
    server_raw = os.path.join(outdir, 'iperf-server-raw.txt')
    print('Start iperf ... (-i 1, 逐秒速率只写入 %s 与 throughput-server.csv)'
          % os.path.relpath(csv_path, LAB_DIR))

    # 服务端也加 -i 1：它的逐秒数字是**实际收到**的速率（线上速率），
    # 而客户端报的是写进 socket 的速率（首秒能报到 58.7 Mbit/s 那种假峰值）。
    server = h2.popen('stdbuf -oL -eL iperf -s -w 16m -i 1',
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    spump = ThroughputLogger(server.stdout,
                             os.path.join(outdir, 'throughput-server.csv'),
                             server_raw, _CURRENT['tag'] + ' [server]')
    spump.start()
    _PUMPS.append(spump)                       # _PUMPS[0] 仍是客户端，取用时注意

    if _wait_for_iperf_server(h2):
        print('  h2:5001 已在监听，启动客户端')
    else:
        print('  [warn] 等 h2:5001 监听超时（10 s），仍启动客户端；'
              '若失败请看 %s' % os.path.relpath(server_raw, LAB_DIR))

    # 先把 h1->h2 的 ARP 表项建立起来：RTT 监视器开工自检要发一个 ping，
    # 若首包因未解析地址而丢，监视器会重试、采样起点推迟 ~0.5s（曲线会整体左偏）
    h1.cmd('ping -c 1 -W 1 %s >/dev/null 2>&1' % h2.IP())

    # 额外监视：四个接口的 qdisc 队列 + h1 上 iperf 连接的内核侧指标
    targets = [('h1', 'h1-eth0', 'qlen-h1eth0.txt'),
               ('r1', 'r1-eth0', 'qlen-r1eth0.txt'),
               ('r1', 'r1-eth1', 'qlen-r1eth1.txt'),
               ('h2', 'h2-eth0', 'qlen-h2eth0.txt')]
    for node_name, dev, fname in targets:
        proc = mp.Process(target=_qdisc_backlog_monitor,
                          args=(net, node_name, dev, os.path.join(outdir, fname),
                                _CFG['qlen_interval']))
        proc.daemon = True
        proc.start()
        _EXTRAS.append(proc)
    tproc = mp.Process(target=_tcp_monitor,
                       args=(net, os.path.join(outdir, 'tcp.txt'), _CFG['qlen_interval']))
    tproc.daemon = True
    tproc.start()
    _EXTRAS.append(tproc)
    print('  额外监视: %s + tcp.txt（内核侧 srtt/cwnd/retrans）'
          % ', '.join(t[2] for t in targets))

    # stdbuf -oL: 走管道时强制行缓冲，否则 iperf 被 kill -9 时缓冲里的输出会整体丢失
    client = h1.popen(
        'stdbuf -oL -eL iperf -c %s -t %d -i 1' % (h2.IP(), duration + 5),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pump = ThroughputLogger(client.stdout, csv_path, raw_path, _CURRENT['tag'])
    pump.start()
    _PUMPS.append(pump)
    return pump


_ORIG_START_RTT_MONITOR = utils.start_rtt_monitor


def start_rtt_monitor_logged(net, fname):
    """包一层 utils.start_rtt_monitor：额外写一个 t0.txt 记录"实验时钟起点"。

    本函数返回后紧跟着就是 dynamic_bw()（相位时钟从这里开始），所以这一时刻就是
    相位栅格的 0 点。绘图若以"第一条 RTT 样本"为 0，就会因为监视器启动延迟
    (fork + 开工自检 + 首个回复) 而整体左偏。
    """
    monitor = _ORIG_START_RTT_MONITOR(net, fname)
    try:
        with open(os.path.join(os.path.dirname(_CURRENT['csv']), 't0.txt'), 'w') as fh:
            fh.write('%.6f\n' % time.time())
    except OSError as exc:
        sys.stderr.write('  [warn] 写 t0.txt 失败: %r\n' % (exc,))
    return monitor


utils.start_rtt_monitor = start_rtt_monitor_logged


def stop_iperf_logged():
    """替换 utils.stop_iperf：先收掉额外队列监视器，再走原来的 pkill。"""
    for proc in _EXTRAS:
        if proc.is_alive():
            proc.terminate()
    for proc in _EXTRAS:
        proc.join(timeout=2)
    del _EXTRAS[:]
    return _ORIG_STOP_IPERF()


_ORIG_STOP_IPERF = utils.stop_iperf
utils.start_iperf = start_iperf_logged
utils.stop_iperf = stop_iperf_logged

# 原版线程式 rtt 监视器留作兜底
_ORIG_RTT_MONITOR = utils.rtt_monitor


# --------------------------------------------------------------------------
# 打补丁：utils.rtt_monitor
#
# 原实现每 100 ms 起一个线程、各跑一次 `ping -c 1`，互不等待。RTT 一大就有
# 上百个探针同时在途，而文件是按**完成**顺序 append、时间戳记的却是**发送**
# 时刻 —— 于是出现时间戳逆序、曲线错位（taildrop 那轮实测 69 处逆序、最多
# 99 个探针在途，且 38~42 s 有整段样本缺口）。
# 这里换成单进程定频 ping：`ping -D -i 0.1`，-D 让每行带到达时刻的 UNIX
# 时间戳，发送时刻 = 到达时刻 - RTT，行序即发送序，采样率仍是 10/s。
# --------------------------------------------------------------------------
def _rtt_probe_ok(h1, h2, attempts=4, delay=0.25):
    """探测：能否 ping 通、是否支持 -D 时间戳。

    要重试：实验刚起步时邻居表还没建立，第一个 ICMP 常被丢掉，如果只探一次就
    判定"ping 不可用"，会退回旧的线程式监视器（时间戳会错序、采样会中断）。
    """
    out = ''
    for _ in range(attempts):
        out = h1.cmd('ping -D -c 1 -W 2 %s' % h2.IP()) or ''
        if re.search(r'time[=<]', out):
            return True, re.search(r'^\s*\[[\d.]+\]', out, re.M) is not None
        time.sleep(delay)
    sys.stderr.write('  [warn] ping 探测 %d 次均失败，最后输出: %r\n'
                     % (attempts, out.strip()[:160]))
    return False, False


def _rtt_write_line(fh, when, text):
    """写一行 '<发送时刻>, <ping 原始行>'，与 utils.py 的文件格式一致。"""
    fh.write('%f, %s\n' % (when, text.strip()))


def rtt_monitor_stopwait(net, fname):
    """兜底：一次只发一个探针（等回复再发下一个），采样率随 RTT 下降。"""
    h1, h2 = net.get('h1', 'h2')
    print('  rtt 监视器: 顺序单探针（stop-and-wait）')
    with open(fname, 'w', buffering=1) as fh:
        while True:
            t = time.time()
            out = h1.cmd('ping -c 1 -W 2 %s | grep ttl' % h2.IP()) or ''
            if 'time=' in out or 'time<' in out:
                _rtt_write_line(fh, t, out.splitlines()[0])


def rtt_monitor_single(net, fname):
    """单进程 ping -D -i 0.1（推荐）：时间戳=发送时刻，行序=发送序。"""
    h1, h2 = net.get('h1', 'h2')
    has_time, has_D = _rtt_probe_ok(h1, h2)
    if not has_time:
        print('  [warn] ping 探测 6 次均失败，退回原版线程式 rtt 监视器'
              '（时间戳可能错序，检查 h1/h2 是否真的互通）')
        return _ORIG_RTT_MONITOR(net, fname)
    if not has_D:
        print('  [warn] 本机 ping 不支持 -D，退回顺序单探针模式')
        return rtt_monitor_stopwait(net, fname)

    interval = _CFG['rtt_interval']
    print('  rtt 监视器: 单进程 ping -D -i %g（时间戳=发送时刻, %.0f Hz）'
          % (interval, 1.0 / interval))
    proc = h1.popen('stdbuf -oL ping -D -i %g %s' % (interval, h2.IP()),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with open(fname, 'w', buffering=1) as fh:
        def _bye(signum, frame):                                  # noqa: ARG001
            try:
                fh.flush()
            finally:
                try:
                    proc.kill()
                finally:
                    os._exit(0)
        signal.signal(signal.SIGTERM, _bye)
        for raw in iter(proc.stdout.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip('\n')
            m = re.match(r'\s*\[([\d.]+)\]\s*(.*)$', line)
            if m:
                arrive, text = float(m.group(1)), m.group(2)
            else:                                                  # 没 -D 前缀
                arrive, text = time.time(), line
            mr = re.search(r'time=([\d.]+)\s*ms', text)
            if mr:
                rtt_ms = float(mr.group(1))
            elif re.search(r'time<1\s*ms', text):
                rtt_ms = 0.5
            else:
                continue                                           # 丢包/超时
            _rtt_write_line(fh, arrive - rtt_ms / 1000.0, text)


# --------------------------------------------------------------------------
# 实验编排
# --------------------------------------------------------------------------
def _clear_dir(path):
    """删除旧的输出目录（路径由本脚本自己拼出，dname 已做白名单校验）。"""
    if os.path.isdir(path):
        shutil.rmtree(path)


def _read_rtt(dname):
    rtts = []
    path = os.path.join(LAB_DIR, dname, 'rtt.txt')
    if not os.path.exists(path):
        return rtts
    with open(path, errors='replace') as fh:
        for line in fh:
            m = re.search(r'time=([\d.]+)\s*ms', line)
            if m:
                rtts.append(float(m.group(1)))
    return rtts


def _rate_line(label, pump):
    stats = pump.rate_stats() if pump else None
    if stats:
        lo, avg, hi = stats
        print('  %s: %d 行 (CSV), min/avg/max = %.2f / %.2f / %.2f Mbit/s'
              % (label, len(pump.intervals), lo, avg, hi))
    elif pump and pump.summary is not None:
        print('  %s: %.2f Mbit/s (只有汇总行)' % (label, pump.summary))
    else:
        print('  %s: 无速率数据' % label)


def summarize(dname, elapsed, rc, pumps):
    client = pumps[0] if pumps else None
    server = pumps[1] if len(pumps) > 1 else None
    pump = client
    print('\n---- %s 摘要 ----' % dname)
    _rate_line('iperf 客户端写入', client)
    _rate_line('iperf 服务端收到', server)
    if pump and pump.parse_errors:
        print('  iperf 解析告警 : %d 行未解析（原因见终端 [warn]）' % pump.parse_errors)
    if pump and pump.rows == 0 and pump.lines > 0:
        hint = '（iperf 连接被拒，本轮无流量、实验无效，需重跑）' if pump.connect_failed else ''
        print('  [!] 没有解析出任何速率行%s，请查 iperf-raw.txt / iperf-server-raw.txt' % hint)

    server_raw = os.path.join(LAB_DIR, dname, 'iperf-server-raw.txt')
    if os.path.exists(server_raw):
        errors = [ln.strip() for ln in open(server_raw, errors='replace')
                  if re.search(r'unable to bind|Address already in use|error', ln, re.I)]
        if errors:
            print('  [!] iperf 服务端报错: %s' % errors[0])

    rtts = _read_rtt(dname)
    if rtts:
        print('  RTT( ping )    : %d 个样本, min/avg/max = %.1f / %.1f / %.1f ms'
              % (len(rtts), min(rtts), sum(rtts) / len(rtts), max(rtts)))
    else:
        print('  RTT( ping )    : 无样本')

    outdir = os.path.join(LAB_DIR, dname)
    if os.path.isdir(outdir):
        for name in sorted(os.listdir(outdir)):
            path = os.path.join(outdir, name)
            if not os.path.isfile(path):
                continue
            with open(path, errors='replace') as fh:
                nlines = sum(1 for _ in fh)
            print('  文件 %-16s %7.1f KB, %6d 行' % (name, os.path.getsize(path) / 1024.0, nlines))
    print('  用时 %.1f s, 返回码 %s' % (elapsed, rc))


def run_experiment(script, argv, dname, title, quiet=False):
    print('\n' + '=' * 74)
    print('== %s' % title)
    print('== 目录 %s/   命令 %s %s' % (dname, script, ' '.join(argv)))
    print('=' * 74, flush=True)

    _clear_dir(os.path.join(LAB_DIR, dname))
    os.makedirs(os.path.join(LAB_DIR, dname), exist_ok=True)
    _CURRENT['csv'] = os.path.join(LAB_DIR, dname, 'throughput.csv')
    _CURRENT['tag'] = '%s %s' % (script, ' '.join(argv))
    del _PUMPS[:]
    del _EXTRAS[:]

    saved_argv, sys.argv = sys.argv, [script] + argv
    start = time.time()
    rc = 0
    try:
        runpy.run_path(os.path.join(LAB_DIR, script), run_name='__main__')
    except SystemExit as exc:
        rc = exc.code if exc.code is not None else 0
    except Exception:                                             # noqa: BLE001
        rc = 1
        traceback.print_exc()
    finally:
        sys.argv = saved_argv

    for th in _PUMPS:
        th.join(timeout=5)
    if not quiet:
        summarize(dname, time.time() - start, rc, list(_PUMPS))
    return rc


def build_plan(args):
    plan = []
    if not args.skip_reproduce:
        dname = 'qlen-%d' % args.maxq
        plan.append((REPRODUCE, ['-q', str(args.maxq)], dname,
                     '实验 1/4: 复现 bufferbloat (maxq=%d, 瓶颈 10 Mbit)' % args.maxq))
    algos = [a.strip() for a in args.algos.split(',') if a.strip()]
    for i, algo in enumerate(algos, start=1):
        plan.append((MITIGATE, ['-a', algo], algo,
                     '实验 %d/4: 缓解对比 (%s, 动态带宽 100/10/1/50/1/100 Mbit)' % (i + 1, algo)))
    return plan, algos


SELFTEST_SAMPLE = """------------------------------------------------------------
Client connecting to 10.0.2.22, TCP port 5001
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-1.0000 sec   954 KBytes  7.82 Mbits/sec
[  1] 1.0000-2.0000 sec  1.20 MBytes  10.1 Mbits/sec
[  1]  2.0- 3.0 sec  1.25 MBytes  10.5 Mbits/sec
[  1] 3.0000-4.0000 sec  0.000 Bytes  0.000 bits/sec
[  1] 4.0000-5.0000 sec   512 Bytes   4.19 Kbits/sec
[  1] 5.0000-6.0000 sec  1.00 GBytes  8.59 Gbits/sec
[  1] 0.0000-6.0142 sec  1.20 GBytes  1.60 Gbits/sec
"""


def selftest():
    """离线自检 iperf 解析器：验证无前缀单位(Bytes/bits/sec)、K/M/G、summary 识别。"""
    import io
    import tempfile
    d = tempfile.mkdtemp(prefix='run_lab_selftest_')
    pump = ThroughputLogger(io.BytesIO(SELFTEST_SAMPLE.encode('utf-8')),
                            os.path.join(d, 'throughput.csv'),
                            os.path.join(d, 'iperf-raw.txt'), 'selftest')
    pump.run()
    expect = [(1.0, 7.82), (2.0, 10.1), (3.0, 10.5), (4.0, 0.0),
              (5.0, 0.00419), (6.0, 8590.0)]
    ok = len(pump.intervals) == len(expect) and all(
        abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-3
        for a, b in zip(pump.intervals, expect))
    ok = ok and pump.summary is not None and abs(pump.summary - 1600.0) < 1.0
    ok = ok and pump.parse_errors == 0 and pump.rows == len(expect) + 1
    print('解析行数 %d, 未解析 %d, summary=%s' % (pump.rows, pump.parse_errors, pump.summary))
    for (end, rate) in pump.intervals:
        print('  end=%6.2f s  rate=%9.4f Mbit/s' % (end, rate))
    print('selftest: %s' % ('PASS' if ok else 'FAIL'))
    print('样本文件: %s' % d)
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description='Oct9 bufferbloat 批量实验驱动')
    parser.add_argument('--maxq', '-q', type=int, default=1000,
                        help='reproduce 的缓冲区包数, 默认 1000')
    parser.add_argument('--algos', default='taildrop,red,codel',
                        help='mitigate 要跑的算法, 逗号分隔, 默认全部三个')
    parser.add_argument('--skip-reproduce', action='store_true', help='跳过复现实验')
    parser.add_argument('--keep-old', action='store_true', help='保留旧输出目录（默认先删掉）')
    parser.add_argument('--quiet', action='store_true', help='不打印每个实验的摘要')
    parser.add_argument('--dry-run', action='store_true', help='只打印实验计划')
    parser.add_argument('--rtt-interval', type=float, default=0.1,
                        help='RTT 探针间隔秒数，默认 0.1（root 下最小约 0.002）')
    parser.add_argument('--qlen-interval', type=float, default=0.02,
                        help='队列/TCP 监视采样间隔秒数，默认 0.02')
    parser.add_argument('--keep-tso', action='store_true',
                        help='不关闭 TSO/GSO（默认关闭；开着会让"1000 包"缓冲变成 ~60 MB）')
    parser.add_argument('--old-rtt-monitor', action='store_true',
                        help='用 utils.py 原版线程式 rtt 监视器（RTT 大时时间戳会错序）')
    parser.add_argument('--selftest', action='store_true', help='离线自检 iperf 解析器后退出')
    args = parser.parse_args()

    # 原脚本用相对路径写 cwnd.txt/qlen.txt/rtt.txt，必须固定工作目录，
    # 否则从别处启动时监控文件会落到调用者的 cwd 里。
    os.chdir(LAB_DIR)

    if args.old_rtt_monitor:
        print('rtt 监视器: utils.py 原版线程式（每 100 ms 一个 ping -c 1）')
    else:
        utils.rtt_monitor = rtt_monitor_single      # 单进程 ping -D -i <interval>
        print('rtt 监视器: 单进程 ping -D -i %g（时间戳=发送时刻，行序=发送序）'
              % args.rtt_interval)

    if args.rtt_interval < 0.002 or args.qlen_interval < 0.002:
        parser.error('采样间隔不得小于 0.002 s（iputils 对 root 的下限；再小会变成泛洪）')
    _CFG['rtt_interval'] = args.rtt_interval
    _CFG['qlen_interval'] = args.qlen_interval
    _CFG['disable_offload'] = not args.keep_tso

    if args.selftest:
        return selftest()

    if args.maxq <= 0:
        parser.error('--maxq 必须为正整数')

    plan, algos = build_plan(args)
    unknown = [a for a in algos if a not in SUPPORTED_ALGOS]
    if unknown:
        parser.error('不支持的算法 %s，可选: %s' % (unknown, ', '.join(SUPPORTED_ALGOS)))

    print('工作目录: %s' % LAB_DIR)
    print('python  : %s' % sys.executable)
    print('实验计划:')
    for script, argv, dname, title in plan:
        print('  - %-28s -> %-14s %s' % (script, dname + '/', title))
    print('每个实验约 65 s（内部固定 60 s 采集），共约 %d 分钟。'
          % round(len(plan) * 70 / 60.0))

    if args.dry_run:
        print('\n[dry-run] 未执行任何实验。')
        return 0

    if os.geteuid() != 0:
        print('\n[!] 需要 root 权限（Mininet 要建 namespace/veth 并配置 tc）。\n'
              '    请用: sudo %s %s' % (sys.executable, os.path.basename(__file__)), file=sys.stderr)
        return 1

    if args.keep_old:
        for _, _, dname, _ in plan:                # 只清本脚本产出的文件，保留旧监控文件
            for name in ('throughput.csv', 'iperf-raw.txt'):
                path = os.path.join(LAB_DIR, dname, name)
                if os.path.exists(path):
                    os.remove(path)
    print('清理 Mininet 残留 ...')
    subprocess.call(['mn', '-c'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    rcs = []
    for script, argv, dname, title in plan:
        rcs.append(run_experiment(script, argv, dname, title, quiet=args.quiet))

    print('\n' + '=' * 74)
    print('== 全部结束: %s' % ', '.join('%s rc=%s' % (d, r) for (_, _, d, _), r in zip(plan, rcs)))
    print('== 结果: %s' % ', '.join('%s/throughput.csv' % d for _, _, d, _ in plan))
    print('=' * 74)
    return 0 if all(r == 0 for r in rcs) else 1


if __name__ == '__main__':
    sys.exit(main())
