#!/usr/bin/env bash
# Oct9 bufferbloat 对比实验一键脚本：跑 A/B 两组并出图 + 汇总表。
#
#   sudo ./run_compare.sh                  # 默认 A=TSO 关（推荐） B=TSO 开，各 3 个缓解实验
#   sudo ./run_compare.sh --with-reproduce # 连实验1(复现) 也各跑一遍
#   ./run_compare.sh --dry-run             # 只打印将要执行的命令
#   sudo ./run_compare.sh --rtt-interval 0.01 --qlen-interval 0.005   # 更高采样率
#
# 产物：
#   runs/<A 标签>/{taildrop,red,codel}/...     原始数据
#   runs/<B 标签>/...
#   ../report/<A 标签>/exp234-*.png            图（曲线默认连续，不断线）
#   ../report/<B 标签>/exp234-*.png
#   ../report/compare.md                       两张汇总表
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
report_dir="$(cd "$here/../report" && pwd)"
script_dir="$report_dir/scripts"

label_a="tso-off"
label_b="tso-on"
rtt_interval="0.02"
qlen_interval="0.01"
maxq="1000"
algos="taildrop,red,codel"
with_reproduce=0
dry_run=0

while [ $# -gt 0 ]; do
  case "$1" in
    --label-a) label_a="$2"; shift 2 ;;
    --label-b) label_b="$2"; shift 2 ;;
    --rtt-interval) rtt_interval="$2"; shift 2 ;;
    --qlen-interval) qlen_interval="$2"; shift 2 ;;
    --maxq) maxq="$2"; shift 2 ;;
    --algos) algos="$2"; shift 2 ;;
    --with-reproduce) with_reproduce=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# 选一个带 mininet 的 python（和 run_lab.sh 同样的策略）
pick_python() {
  local user_home c cands=()
  user_home="$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)"
  [ -n "${PYTHON:-}" ] && cands+=("$PYTHON")
  [ -n "$(command -v python3 2>/dev/null || true)" ] && cands+=("$(command -v python3)")
  cands+=("$user_home/anaconda3/envs/CN_lab_env/bin/python3"
          "$user_home/miniconda3/envs/CN_lab_env/bin/python3"
          "$user_home/anaconda3/bin/python3"
          "/usr/bin/python3")
  for c in "${cands[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    "$c" -c 'import mininet' >/dev/null 2>&1 && { printf '%s' "$c"; return 0; }
  done
  return 1
}

py="$(pick_python || true)"
user_home="$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)"
plot_py=""
for c in "${PYTHON:-}" "$(command -v python3 || true)" \
         "$user_home/anaconda3/envs/CN_lab_env/bin/python3" \
         "$user_home/miniconda3/envs/CN_lab_env/bin/python3" \
         "$user_home/anaconda3/bin/python3"; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  "$c" -c 'import matplotlib' >/dev/null 2>&1 && { plot_py="$c"; break; }
done

run_one() {                       # run_one <标签> <额外参数...>
  local label="$1"; shift
  [ -n "$label" ] || { echo "错误: 标签为空" >&2; return 1; }
  case "$label" in */*|.*) echo "错误: 标签不能含 / 或以 . 开头: $label" >&2; return 1 ;; esac
  mkdir -p "$here/runs/$label"
  echo
  echo "==================== 跑 $label ===================="
  # 结果先落在 05-bufferbloat/ 下，再整体挪进 runs/<label>/
  local repro=()
  [ "$with_reproduce" -eq 1 ] || repro=(--skip-reproduce)
  "$py" "$here/run_lab.py" --quiet "${repro[@]}" \
      --rtt-interval "$rtt_interval" --qlen-interval "$qlen_interval" \
      --maxq "$maxq" --algos "$algos" "$@"
  rm -rf "$here/runs/$label"/*
  local d
  for d in ${algos//,/ }; do mv "$here/$d" "$here/runs/$label/$d"; done
  if [ "$with_reproduce" -eq 1 ]; then
    mv "$here/qlen-$maxq" "$here/runs/$label/qlen-$maxq"
  fi
  echo "数据 -> $here/runs/$label/"
}

echo "[compare] python(mininet): ${py:-未找到}"
echo "[compare] python(绘图)   : ${plot_py:-未找到}"
echo "[compare] A=$label_a (TSO 关, 默认)  B=$label_b (--keep-tso)"
echo "[compare] 采样: RTT ${rtt_interval}s ($(awk "BEGIN{printf \"%.0f\", 1/$rtt_interval}") Hz), 队列 ${qlen_interval}s"

if [ "$dry_run" -eq 1 ]; then
  echo
  echo "[dry-run] 将执行:"
  echo "  $py $here/run_lab.py --quiet --rtt-interval $rtt_interval --qlen-interval $qlen_interval --maxq $maxq --algos $algos"
  echo "  mv {${algos//,/ }} runs/$label_a/"
  echo "  $py $here/run_lab.py --quiet --rtt-interval $rtt_interval --qlen-interval $qlen_interval --maxq $maxq --algos $algos --keep-tso"
  echo "  mv ... runs/$label_b/"
  echo "  $plot_py $script_dir/plot_bufferbloat.py --data-dir runs/$label_a --outdir $report_dir/$label_a"
  echo "  $plot_py $script_dir/plot_bufferbloat.py --data-dir runs/$label_b --outdir $report_dir/$label_b"
  echo "  $plot_py $script_dir/compare_runs.py runs/$label_a runs/$label_b --labels 'TSO 关' 'TSO 开' --algos $algos --out $report_dir/compare.md --figure-dir $report_dir/compare"
  exit 0
fi

if [ -z "$py" ]; then echo "错误: 找不到带 mininet 的 python（可用 PYTHON=... 指定）" >&2; exit 1; fi
if [ "$(id -u)" -ne 0 ]; then
  echo "错误: 需要 root（Mininet 建 namespace/veth + tc）。请用: sudo $0 $*" >&2; exit 1
fi

run_one "$label_a"
run_one "$label_b" --keep-tso

echo
echo "==================== 出图 + 汇总 ===================="
if [ -z "$plot_py" ]; then
  echo "[warn] 没找到带 matplotlib 的 python，跳过绘图（数据已就绪）" >&2
else
  for lb in "$label_a" "$label_b"; do
    mkdir -p "$report_dir/$lb"
    MPLCONFIGDIR=/tmp/mpl "$plot_py" "$script_dir/plot_bufferbloat.py" \
        --data-dir "$here/runs/$lb" --outdir "$report_dir/$lb" || true
  done
  mkdir -p "$report_dir/compare"
  MPLCONFIGDIR=/tmp/mpl "$plot_py" "$script_dir/compare_runs.py" \
      "$here/runs/$label_a" "$here/runs/$label_b" \
      --labels "TSO 关" "TSO 开" --algos "$algos" --out "$report_dir/compare.md" \
      --figure-dir "$report_dir/compare" || true
fi

# 以 root 跑出来的文件默认属 root/nobody，用户之后不带 sudo 重新出图会 Permission denied
if [ "$(id -u)" -eq 0 ]; then
  chmod -R a+rwX "$here/runs" "$report_dir/compare" 2>/dev/null || true
  for lb in "$label_a" "$label_b"; do
    chmod -R a+rwX "$report_dir/$lb" 2>/dev/null || true
  done
  [ -n "${SUDO_UID:-}" ] && chown -R "${SUDO_UID}:${SUDO_GID:-$SUDO_UID}" \
      "$here/runs" "$report_dir/compare" "$report_dir/$label_a" "$report_dir/$label_b" 2>/dev/null || true
  [ -f "$report_dir/compare.md" ] && chmod a+rw "$report_dir/compare.md" 2>/dev/null || true
fi

echo
echo "[compare] 完成。数据: $here/runs/{$label_a,$label_b}/"
echo "[compare] 图:   $report_dir/{$label_a,$label_b}/"
echo "[compare] 汇总: $report_dir/compare.md"
