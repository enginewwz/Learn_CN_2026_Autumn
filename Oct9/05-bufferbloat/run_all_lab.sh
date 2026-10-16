#!/usr/bin/env bash
# Oct9 实验 05-bufferbloat 最终版一键脚本：
#   TSO/GSO 开 + 关 各跑**完整一套**（实验1 复现 + 实验2/3/4 三个 AQM），
#   然后出全套图（每组 5 张）、A/B 对比图（每个算法一张，实验1 是 cwnd/队列/RTT 三联）、
#   汇总表，并做结果自检（含相位边界偏离告警）。
#
#   sudo ./run_all_lab.sh                 # 全部，约 9~10 分钟
#   ./run_all_lab.sh --dry-run            # 只打印将要执行的命令（不需要 root）
#   sudo ./run_all_lab.sh --no-ab         # 只跑 TSO 关那一套
#   sudo ./run_all_lab.sh --with-smallbuf # 额外跑 maxq=10 的复现实验（教科书锯齿）
#   sudo ./run_all_lab.sh --rtt-interval 0.01 --qlen-interval 0.005   # 100/200 Hz
#
# 产物：
#   qlen-<maxq>/ taildrop/ red/ codel/              主数据 = TSO/GSO 关（也是绘图脚本默认输入）
#   runs/tso-off/{qlen-<maxq>,taildrop,red,codel}/  TSO 关副本
#   runs/tso-on/{qlen-<maxq>,taildrop,red,codel}/   TSO 开
#   ../report/exp1-*.png exp234-*.png               主图（TSO 关）
#   ../report/tso-off/  ../report/tso-on/           每组各 5 张
#   ../report/compare/compare-<algo>.png            A/B 对比图（taildrop/red/codel/qlen-<maxq>）
#   ../report/compare.md                            六相位汇总表（taildrop/red/codel）
#   ../report/qlen-10/exp1-*.png                    （--with-smallbuf 时）
#   logs/all-<时间戳>.log                            全流程日志
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
report_dir="$(cd "$here/../report" && pwd)"
script_dir="$report_dir/scripts"

maxq=1000
algos="taildrop,red,codel"
rtt_interval=0.02
qlen_interval=0.01
label_a="tso-off"
label_b="tso-on"
do_ab=1
with_smallbuf=0
quiet=0
dry_run=0

while [ $# -gt 0 ]; do
  case "$1" in
    --maxq) maxq="$2"; shift 2 ;;
    --algos) algos="$2"; shift 2 ;;
    --rtt-interval) rtt_interval="$2"; shift 2 ;;
    --qlen-interval) qlen_interval="$2"; shift 2 ;;
    --label-a) label_a="$2"; shift 2 ;;
    --label-b) label_b="$2"; shift 2 ;;
    --no-ab) do_ab=0; shift ;;
    --with-smallbuf) with_smallbuf=1; shift ;;
    --quiet) quiet=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

IFS=',' read -r -a algo_arr <<< "$algos"
main_dirs=("qlen-$maxq" "${algo_arr[@]}")

mkdir -p "$here/logs" "$report_dir" "$here/runs"
log="$here/logs/all-$(date +%Y%m%d-%H%M%S).log"
if [ "$dry_run" -eq 0 ]; then
  exec > >(tee -a "$log") 2>&1
fi
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mpl}"
mkdir -p "$MPLCONFIGDIR"

step() { echo; echo "==================== $* ===================="; }
run()  { if [ "$dry_run" -eq 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

pick_py() {                        # $1 = 需要能 import 的模块名
  local mod="$1" user_home c cands=()
  user_home="$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)"
  [ -n "${PYTHON:-}" ] && cands+=("$PYTHON")
  [ -n "$(command -v python3 2>/dev/null || true)" ] && cands+=("$(command -v python3)")
  cands+=("$user_home/anaconda3/envs/CN_lab_env/bin/python3"
          "$user_home/miniconda3/envs/CN_lab_env/bin/python3"
          "$user_home/anaconda3/bin/python3" "/usr/bin/python3")
  for c in "${cands[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    "$c" -c "import $mod" >/dev/null 2>&1 && { printf '%s' "$c"; return 0; }
  done
  return 1
}

py="$(pick_py mininet || true)"
plot_py="$(pick_py matplotlib || true)"

echo "[all] 工作目录 : $here"
echo "[all] 日志     : $log"
echo "[all] mininet  : ${py:-未找到}"
echo "[all] 绘图     : ${plot_py:-未找到（将跳过出图）}"
echo "[all] 采样     : RTT ${rtt_interval}s / 队列 ${qlen_interval}s"
echo "[all] 每组实验 : ${main_dirs[*]}   ← 共 ${#main_dirs[@]} 个"
if [ "$do_ab" -eq 1 ]; then
  echo "[all] 分组     : $label_b(TSO 开) + $label_a(TSO 关)，合计 $(( ${#main_dirs[@]} * 2 )) 个实验"
  echo "[all] 预计耗时 : 约 $(( ${#main_dirs[@]} * 2 * 70 / 60 + 1 )) 分钟（含出图）"
else
  echo "[all] 分组     : $label_a(TSO 关) 单组，共 ${#main_dirs[@]} 个实验"
fi

if [ "$dry_run" -eq 0 ]; then
  [ -n "$py" ] || { echo "错误: 找不到带 mininet 的 python（可用 PYTHON=... 指定）" >&2; exit 1; }
  [ "$(id -u)" -eq 0 ] || { echo "错误: 需要 root。请用: sudo $0 $*" >&2; exit 1; }
fi

lab() {                              # lab <run_lab.py 额外参数...>
  local args=("$here/run_lab.py" --rtt-interval "$rtt_interval"
              --qlen-interval "$qlen_interval" --maxq "$maxq")
  [ "$quiet" -eq 1 ] && args+=(--quiet)
  run "$py" "${args[@]}" "$@"
}
plot() { run "$plot_py" "$script_dir/plot_bufferbloat.py" "$@"; }
cmp_() { run "$plot_py" "$script_dir/compare_runs.py" "$@"; }

# ---------------------------------------------------------------- 0. 预检
step "0/5 预检"
run "$py" "$here/run_lab.py" --selftest
if [ "$dry_run" -eq 0 ]; then
  echo "清理 Mininet 残留 ..."
  mn -c >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------- 1. TSO 开
if [ "$do_ab" -eq 1 ]; then
  step "1/5 TSO/GSO **开**的一组（--keep-tso，${#main_dirs[@]} 个实验）"
  run rm -rf "$here/runs/$label_b"
  run mkdir -p "$here/runs/$label_b"
  lab --keep-tso --algos "$algos"          # ← 关键：这组保留 TSO/GSO
  for d in "${main_dirs[@]}"; do
    run mv "$here/$d" "$here/runs/$label_b/$d"
  done
else
  step "1/5 跳过 TSO 开那一组"
fi

# ---------------------------------------------------------------- 2. TSO 关
step "2/5 TSO/GSO **关**的一组（${#main_dirs[@]} 个实验，留在根目录作为主数据）"
if [ "$do_ab" -eq 1 ]; then
  run rm -rf "$here/runs/$label_a"
  run mkdir -p "$here/runs/$label_a"
fi
lab --algos "$algos"
if [ "$do_ab" -eq 1 ]; then
  for d in "${main_dirs[@]}"; do
    run cp -r "$here/$d" "$here/runs/$label_a/$d"
  done
fi

# ---------------------------------------------------------------- 3. 出图
step "3/5 出图 + 汇总"
if [ -n "$plot_py" ]; then
  echo "-- 主图（根目录 = TSO 关）"
  plot
  if [ "$do_ab" -eq 1 ]; then
    for lb in "$label_a" "$label_b"; do
      echo "-- $lb：每组 exp1 三张 + exp234 两张"
      run mkdir -p "$report_dir/$lb"
      plot --data-dir "$here/runs/$lb" --outdir "$report_dir/$lb"
    done
    echo "-- A/B 对比图（每个算法一张）+ 汇总表"
    run mkdir -p "$report_dir/compare"
    cmp_ "$here/runs/$label_a" "$here/runs/$label_b" --labels "TSO 关" "TSO 开" \
         --algos "$algos,qlen-$maxq" --quiet --figure-dir "$report_dir/compare"
    cmp_ "$here/runs/$label_a" "$here/runs/$label_b" --labels "TSO 关" "TSO 开" \
         --algos "$algos" --out "$report_dir/compare.md" --figure-dir "$report_dir/compare"
  fi
else
  echo "[warn] 没有带 matplotlib 的 python，跳过出图（数据已就绪）"
fi

# ---------------------------------------------------------------- 4. 可选小缓冲
if [ "$with_smallbuf" -eq 1 ]; then
  step "4/5 可选：maxq=10 的复现实验（教科书锯齿）"
  lab --maxq 10 --algos ""
  [ -n "$plot_py" ] && { run mkdir -p "$report_dir/qlen-10"
                         plot --maxq 10 --outdir "$report_dir/qlen-10"; }
else
  step "4/5 跳过可选小缓冲实验"
fi

# ---------------------------------------------------------------- 5. 自检
step "5/5 结果自检"
if [ "$dry_run" -eq 0 ] && [ -n "$py" ]; then
  "$py" - "$here" "$script_dir" "$maxq" "$do_ab" "$label_a" "$label_b" "${algos//,/ }" <<'PYEOF' || true
import os, re, statistics, sys
import numpy as np

here, scripts, maxq, do_ab, la, lb = sys.argv[1:7]
algos = sys.argv[7:]
sys.path.insert(0, scripts)
try:
    import plot_bufferbloat as pb
except Exception:
    pb = None


def report(d, check_phase):
    rtt_f, tcp_f, q_f = (os.path.join(d, f) for f in
                         ("rtt.txt", "tcp.txt", "qlen-r1eth1.txt"))
    if not os.path.exists(rtt_f):
        print("  %-46s 缺 rtt.txt" % os.path.relpath(d, here))
        return
    t, v = [], []
    with open(rtt_f, errors="replace") as fh:
        for line in fh:
            m = re.search(r"time=([\d.]+)\s*ms", line)
            if m:
                t.append(float(line.split(",")[0]))
                v.append(float(m.group(1)))
    t = np.array(sorted(t))
    dt = np.diff(t) if len(t) > 1 else np.array([0.0])
    srtt = 0
    if os.path.exists(tcp_f):
        with open(tcp_f, errors="replace") as fh:
            srtt = sum(1 for ln in fh if not ln.startswith("#")
                       and len(ln.split(",")) == 6
                       and ln.split(",")[1].strip() not in ("0", ""))
    qmed = qmax = 0.0
    if os.path.exists(q_f):
        with open(q_f, errors="replace") as fh:
            by = [int(x.split(",")[2]) for x in fh if len(x.split(",")) == 3]
        if by:
            qmed, qmax = statistics.median(by) / 1024.0, max(by) / 1024.0

    phase = "—"
    if check_phase and pb is not None:
        t0 = pb.read_t0(d, verbose=False) or t[0]
        onset = next((tt - t0 for tt, x in zip(t, v) if x > 50), None)
        if onset is not None:
            dev = (onset - 10.0) * 1000.0
            phase = "%+.0f ms%s" % (dev, "  <== 偏差偏大" if abs(dev) > 300 else "")
    print("  %-46s %5d 样本 (%.0f Hz) 最大间隔 %5.2fs | 中位RTT %7.1f ms | "
          "srtt %4d 行 | 队列 中位/最大 %7.0f/%7.0f KB | 相位边界 %s"
          % (os.path.relpath(d, here), len(t), len(t) / 60.0, float(dt.max()),
             statistics.median(v) if v else -1, srtt, qmed, qmax, phase))


for name in ["qlen-%s" % maxq] + algos:
    report(os.path.join(here, name), check_phase=(name != "qlen-%s" % maxq))
if do_ab == "1":
    for name in ["qlen-%s" % maxq] + algos:
        for lb2 in (la, lb):
            report(os.path.join(here, "runs", lb2, name),
                   check_phase=(name != "qlen-%s" % maxq))

print("\n  判读: 根目录与 %s = TSO 关（taildrop 队列应 ≤ ~1.5 MB、50/100 Mbit 相位 RTT 百毫秒内）；"
      "%s = TSO 开（队列可到十几 MB、RTT 秒级）。相位边界应接近 0 ms。" % (la, lb))
PYEOF
fi

# ---------------------------------------------------------------- 收尾
if [ "$dry_run" -eq 0 ] && [ "$(id -u)" -eq 0 ]; then
  chmod -R a+rwX "$here/runs" "$report_dir" 2>/dev/null || true
  [ -n "${SUDO_UID:-}" ] && chown -R "${SUDO_UID}:${SUDO_GID:-$SUDO_UID}" \
      "$here/runs" "$report_dir" 2>/dev/null || true
fi
[ -f "$log" ] && chmod a+rw "$log" 2>/dev/null || true

step "完成"
echo "主数据: $here/{$(echo "${main_dirs[*]}" | tr ' ' ',')}/   (TSO/GSO 关)"
[ "$do_ab" -eq 1 ] && echo "A/B   : $here/runs/{$label_a,$label_b}/{$(echo "${main_dirs[*]}" | tr ' ' ',')}/"
echo "主图  : $report_dir/{exp1-*.png,exp234-*.png}"
[ "$do_ab" -eq 1 ] && echo "每组图: $report_dir/{$label_a,$label_b}/"
[ "$do_ab" -eq 1 ] && echo "对比图: $report_dir/compare/compare-*.png   汇总表: $report_dir/compare.md"
echo "日志  : $log"
