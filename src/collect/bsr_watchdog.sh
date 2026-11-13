#!/bin/bash
# BSR 爬虫守护脚本（LaunchDaemon 每 5 分钟巡检 + 开机自启，不依赖用户登录）
# 幂等：已在跑就跳过；进程死了自动补拉；断电重启后自动续跑
# v1.1：路径可移植 —— DIR 取本脚本所在目录（即 amz_lead/），PY 依次探测：
#   1) 环境变量 PY_OVERRIDE（LaunchDaemon 的 EnvironmentVariables 里可设）
#   2) 脚本同目录 .python_path 文件（一行 python3 绝对路径，推荐）
#   3) PATH 里的 python3
# 注意：LaunchDaemon 环境的 PATH 通常只有系统目录，命令行能用的 python3 未必找得到，
#       装了 scrapling 的那个解释器务必用 1) 或 2) 指明。
# v1.3.1 合并线上两处本地改进（2026-11-11，见 07_线上同步_v1.3.1.md）：
#   ① pgrep 模式加 [n]/[e]/[y] 字符类——模式串自身不会出现在被匹配进程的命令行里，
#     彻底防 pgrep -f 匹配到自己的监控命令（坑#10 的彻底解，比 python.* 前缀更稳）
#   ② BSR 补抓完成后建 .backfill_done（人工创建），守护见到就不再拉起——
#     否则补抓"跑完即退出"会被每 5 分钟重复拉起、重复读一遍供给表
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "$PY_OVERRIDE" ]; then
  PY="$PY_OVERRIDE"
elif [ -f "$DIR/.python_path" ]; then
  PY="$(cat "$DIR/.python_path")"
else
  PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
  echo "!! 找不到 python3：请创建 $DIR/.python_path 或设置 PY_OVERRIDE"
  exit 1
fi
LOG="$DIR/bsr_full/watchdog.log"

{
  echo "=== watchdog $(date '+%F %T') ==="

  cd "$DIR" || exit 1

  # 手动暂停开关（2026-11-11 加）：bsr_full/PAUSE_UNTIL 内写 unix 时间戳，
  # 未到期则本轮不拉任何爬虫——用于风控恶化时主动冷却，避免越压越封。
  # 用法：date -v+2H +%s > bsr_full/PAUSE_UNTIL   （2 小时后自动恢复）
  if [ -f "$DIR/bsr_full/PAUSE_UNTIL" ]; then
    pu=$(cat "$DIR/bsr_full/PAUSE_UNTIL" 2>/dev/null || echo 0)
    if [ "$(date +%s)" -lt "${pu:-0}" ]; then
      echo "手动暂停中（至 $(date -r "$pu" '+%F %T' 2>/dev/null || echo "$pu")），跳过所有拉起"
      exit 0
    fi
  fi

  # 开机初期网络可能未就绪，最多等 60 秒
  for i in $(seq 1 12); do
    if /sbin/ping -c1 -t3 1.1.1.1 > /dev/null 2>&1; then
      break
    fi
    sleep 5
  done

  # 画树链
  if ! pgrep -f "python.*bsr_chai[n].py --only-walk" > /dev/null; then
    if [ -f "$DIR/bsr_full/.chain_done" ]; then
      echo "画树链已全部完成（.chain_done 存在），跳过"
    else
      # macOS 没有 setsid，用 nohup + 后台；LaunchDaemon 配了 AbandonProcessGroup 子进程不会被回收
      "$PY" bsr_chain.py --only-walk < /dev/null >> "$LOG" 2>&1 &
      echo "拉起画树链 pid=$!"
    fi
  else
    echo "画树链在跑"
  fi

  # 解析舰队
  if ! pgrep -f "python.*resolver_fle[e]t.py" > /dev/null; then
    "$PY" resolver_fleet.py < /dev/null >> "$LOG" 2>&1 &
    echo "拉起解析舰队 pid=$!"
  else
    echo "解析舰队在跑"
  fi

  # BSR 补抓（跑完建 .backfill_done 后不再拉起）
  if [ -f "$DIR/bsr_full/.backfill_done" ]; then
    echo "BSR补抓已完成（.backfill_done），跳过"
  elif ! pgrep -f "python.*backfill_bsr_suppl[y].py" > /dev/null; then
    "$PY" backfill_bsr_supply.py < /dev/null >> "$DIR/bsr_full/backfill_bsr.log" 2>&1 &
    echo "拉起BSR补抓 pid=$!"
  else
    echo "BSR补抓在跑"
  fi
} >> "$LOG" 2>&1
