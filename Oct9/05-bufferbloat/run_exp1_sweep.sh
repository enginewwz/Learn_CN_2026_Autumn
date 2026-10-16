#!/usr/bin/env bash
# 实验1（复现 bufferbloat）缓冲区大小扫描：同一套拓扑/参数，只改 --maxq 跑多轮，
# 然后出一张三联对比图（cwnd / 瓶颈队列 / RTT）。
#
#   sudo ./run_exp1_sweep.sh                       # 默认 maxq=10,50,200,1000，约 5 分钟
#   sudo ./run_exp1_sweep.sh --maxq 10,100,1000    # 自选档位
#   ./run_exp1_sweep.sh --dry-run                  # 只打印将要执行的命令（不用 root）
#
# 产物：
#   05-bufferbloat/runs/exp1-maxq-<N>/qlen-<N>/    每档的原始数据
#   ../report/qlen-<N>/exp1-{cwnd,qlen,rtt}.png    每档三张单图
#   ../report/exp1-sweep.png                       ★ 三联图（cwnd/队列/RTT 各一个面板）
#   ../report/exp1-sweep-cwnd|qlen|rtt.png         ★ 每种指标单独一张（四个档位同一坐标系）
#   ../report/exp1-sweep-grid.png                  ★ 4×3 宫格：每档一行、每指标一格，各自纵坐标
#   logs/exp1-sweep-<时间戳>.log                    全流程日志
#
# 注意：会临时占用根目录下的 qlen-<N>/。若该目录原本已存在（例如主实验的 qlen-1000），
# 脚本会先备份、跑完再原样恢复，不会破坏主数据。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
report_dir="$(cd "$here/../report" && pwd)"
script_dir="$report_dir/scripts"

maxq_list="10,50,200,1000"
rtt_interval=0.02
qlen_interval=0.01
dry_run=0

while [ $# -gt 0 ]; do
  case "$1" in
    --maxq) maxq_list="$2"; shift 2 ;;
    --rtt-interval) rtt_interval="$2"; shift 2 ;;
    --qlen-interval) qlen_interval="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
IFS=',' read -r -a maxqs <<< "${maxq_list//，/,}"

mkdir -p "$here/logs" "$report_dir"
log="$here/logs/exp1-sweep-$(date +%Y%m%d-%H%M%S).log"
if [ "$dry_run" -eq 0 ]; then
  exec > >(tee -a "$log") 2>&1
fi
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mpl}"
mkdir -p "$MPLCONFIGDIR"

step() { echo; echo "==================== $* ===================="; }
run()  { if [ "$dry_run" -eq 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

pick_py() {
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

echo "[sweep] 工作目录 : $here"
echo "[sweep] 日志     : $log"
echo "[sweep] mininet  : ${py:-未找到}"
echo "[sweep] 绘图     : ${plot_py:-未找到（将跳过出图）}"
echo "[sweep] maxq 档位: ${maxqs[*]}   (共 ${#maxqs[@]} 轮，约 $(( ${#maxqs[@]} * 70 / 60 + 1 )) 分钟)"
echo "[sweep] 采样     : RTT ${rtt_interval}s / 队列 ${qlen_interval}s"

if [ "$dry_run" -eq 0 ]; then
  [ -n "$py" ] || { echo "错误: 找不到带 mininet 的 python（可用 PYTHON=... 指定）" >&2; exit 1; }
  [ "$(id -u)" -eq 0 ] || { echo "错误: 需要 root。请用: sudo $0 $*" >&2; exit 1; }
fi

step "0/3 预检"
run "$py" "$here/run_lab.py" --selftest
if [ "$dry_run" -eq 0 ]; then
  mn -c >/dev/null 2>&1 || true
fi

step "1/3 逐档跑实验1"
for n in "${maxqs[@]}"; do
  n="$(echo "$n" | tr -d ' ')"
  [ -n "$n" ] || continue
  echo "-- maxq=$n"
  run mkdir -p "$here/runs/exp1-maxq-$n"
  # 根目录已有同名的 qlen-<n>（主实验数据）就先挪开，跑完恢复
  if [ -d "$here/qlen-$n" ]; then
    run mv "$here/qlen-$n" "$here/qlen-$n.keep"
  fi
  run "$py" "$here/run_lab.py" --maxq "$n" --algos "" --quiet \
      --rtt-interval "$rtt_interval" --qlen-interval "$qlen_interval"
  run rm -rf "$here/runs/exp1-maxq-$n/qlen-$n"
  run mv "$here/qlen-$n" "$here/runs/exp1-maxq-$n/qlen-$n"
  if [ -d "$here/qlen-$n.keep" ]; then
    run mv "$here/qlen-$n.keep" "$here/qlen-$n"
    echo "   （已恢复原有根目录 qlen-$n/）"
  fi
  echo "   数据 -> $here/runs/exp1-maxq-$n/qlen-$n/"
done

step "2/3 出图"
if [ -n "$plot_py" ]; then
  for n in "${maxqs[@]}"; do
    n="$(echo "$n" | tr -d ' ')"
    [ -n "$n" ] || continue
    run mkdir -p "$report_dir/qlen-$n"
    run "$plot_py" "$script_dir/plot_bufferbloat.py" \
        --data-dir "$here/runs/exp1-maxq-$n" --outdir "$report_dir/qlen-$n" --maxq "$n"
  done
  echo "-- 汇总三联图"
  run "$plot_py" "$script_dir/plot_exp1_sweep.py" \
      --data-dir "$here/runs" --maxq "$(echo "${maxqs[*]}" | tr ' ' ',')" \
      --out "$report_dir/exp1-sweep.png"
else
  echo "[warn] 没有带 matplotlib 的 python，跳过出图"
fi

step "3/3 结果自检"
if [ "$dry_run" -eq 0 ] && [ -n "$py" ]; then
  "$py" - "$here" "$script_dir" "${maxqs[@]}" <<'PYEOF' || true
import os, re, statistics, sys
import numpy as np
here, scripts = sys.argv[1], sys.argv[2]
maxqs = sys.argv[3:]
sys.path.insert(0, scripts)
try:
    import plot_bufferbloat as pb
except Exception:
    pb = None
print("  %-8s %-22s %-10s %-16s %s" % ("maxq", "RTT 中位/最大(ms)", "样本(Hz)",
                                       "队列KB 中位/最大", "理论上限(ms)"))
for n in maxqs:
    d = os.path.join(here, "runs", "exp1-maxq-%s" % n, "qlen-%s" % n)
    f = os.path.join(d, "rtt.txt")
    if not os.path.exists(f):
        print("  %-8s 缺数据 %s" % (n, d))
        continue
    t, v = [], []
    for line in open(f, errors="replace"):
        m = re.search(r"time=([\d.]+)\s*ms", line)
        if m:
            t.append(float(line.split(",")[0]))
            v.append(float(m.group(1)))
    t = np.array(sorted(t))
    hz = len(t) / 60.0
    gap = float(np.diff(t).max()) if len(t) > 1 else 0.0
    qb = []
    qf = os.path.join(d, "qlen-r1eth1.txt")
    if os.path.exists(qf):
        for line in open(qf, errors="replace"):
            c = line.split(",")
            if len(c) == 3:
                try:
                    qb.append(int(c[2]))
                except ValueError:
                    pass
    lim = float(n) * 1500 * 8 / 10e6 * 1000
    print("  %-8s %7.1f / %-10.1f %5d (%2.0f, gap %4.2fs) %7.0f / %-9.0f %.0f%s"
          % (n, statistics.median(v), max(v), len(t), hz, gap,
             statistics.median(qb) / 1024 if qb else -1,
             max(qb) / 1024 if qb else -1, lim,
             "  <<< 实测明显超过理论上限（TSO/宿主因素）" if v and max(v) > lim * 1.5 else ""))
PYEOF
fi

if [ "$dry_run" -eq 0 ] && [ "$(id -u)" -eq 0 ]; then
  chmod -R a+rwX "$here/runs" "$report_dir" 2>/dev/null || true
  [ -n "${SUDO_UID:-}" ] && chown -R "${SUDO_UID}:${SUDO_GID:-$SUDO_UID}" \
      "$here/runs" "$report_dir" 2>/dev/null || true
fi
[ -f "$log" ] && chmod a+rw "$log" 2>/dev/null || true

step "完成"
echo "数据  : $here/runs/exp1-maxq-<N>/qlen-<N>/"
echo "单档图: $report_dir/qlen-<N>/exp1-{cwnd,qlen,rtt}.png"
echo "汇总图: $report_dir/exp1-sweep.png（三联）+ exp1-sweep-{cwnd,qlen,rtt}.png（单指标）+ exp1-sweep-grid.png（宫格）"
echo "日志  : $log"
