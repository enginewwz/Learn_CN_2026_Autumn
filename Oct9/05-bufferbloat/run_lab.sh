#!/usr/bin/env bash
# Oct9/05-bufferbloat 批量实验启动器。
#
#   sudo ./run_lab.sh                     # 1 个复现 + 3 个缓解实验
#   sudo ./run_lab.sh --maxq 10           # 小缓冲复现
#   ./run_lab.sh --dry-run                # 只看计划
#
# 作用：挑一个装了 mininet 的 python，把 run_lab.py 的终端输出（进度 + 摘要）
# tee 到 logs/run-<时间戳>.log；iperf 逐秒吞吐率只落盘，不打到终端。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
logdir="$here/logs"
mkdir -p "$logdir"
log="$logdir/run-$(date +%Y%m%d-%H%M%S).log"

user_home="$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)"

pick_python() {
  local cands=() c
  [ -n "${PYTHON:-}" ] && cands+=("$PYTHON")
  [ -n "$(command -v python3 2>/dev/null || true)" ] && cands+=("$(command -v python3)")
  cands+=("$user_home/anaconda3/envs/CN_lab_env/bin/python3"
          "$user_home/miniconda3/envs/CN_lab_env/bin/python3"
          "$user_home/anaconda3/bin/python3"
          "/usr/bin/python3")
  for c in "${cands[@]}"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if "$c" -c 'import mininet' >/dev/null 2>&1; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

if ! py="$(pick_python)"; then
  echo "错误: 没找到能 import mininet 的 python3。" >&2
  echo "      例如: PYTHON=$user_home/anaconda3/envs/CN_lab_env/bin/python3 $0" >&2
  exit 1
fi

echo "[run_lab] python : $py"
echo "[run_lab] 工作目录: $here"
echo "[run_lab] 终端日志: $log"
echo "[run_lab] 数据文件: $here/{qlen-*,taildrop,red,codel}/throughput.csv (+ rtt.txt, iperf-raw.txt, ...)"
echo

set +e
"$py" "$here/run_lab.py" "$@" 2>&1 | tee "$log"
rc="${PIPESTATUS[0]}"
set -e

echo
echo "[run_lab] 退出码 $rc；完整终端日志: $log"
exit "$rc"
