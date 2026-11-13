#!/usr/bin/env python3
"""resolver 舰队管理器：巡检各类目 ASIN 池，自动拉起/补充解析进程（最多 3 个并发）。

配合 bsr_chain.py --only-walk 使用：链只画树，舰队负责把每个类目的榜单 ASIN 全部解成卖家。
每 5 分钟巡检一次；待解 ≥ PENDING_MIN 才开新流；同类目不重复开。
"""
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTDIR = BASE / "bsr_full"
MAX_RESOLVERS = 1      # 同一 IP 3 并发打 /dp/ 页触发验证码风暴（实测 47/50），回退单流
PENDING_MIN = 150      # 待解 ASIN 达到这个数才值得开一条流
CHECK_EVERY = 300      # 巡检间隔（秒）


def pool_for(cat: str) -> set:
    """类目的全部榜单 ASIN（兼容 pet-supplies 旧 state.json）。"""
    p = OUTDIR / ("state.json" if cat == "pet-supplies" and (OUTDIR / "state.json").exists()
                  else f"state_{cat}.json")
    if not p.exists():
        return set()
    try:
        s = json.loads(p.read_text())
        asins = set()
        for v in s.get("leaf_items", {}).values():
            asins.update(v)
        return asins
    except Exception:
        return set()  # state 正在被 walker 写入，下轮再试


def done_for(cat: str) -> set:
    p = OUTDIR / ("resolve_progress.jsonl" if cat == "pet-supplies" and
                  (OUTDIR / "resolve_progress.jsonl").exists()
                  else f"resolve_{cat}.jsonl")
    done = set()
    if p.exists():
        for line in p.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["asin"])
            except Exception:
                pass
    return done


def running_cats() -> set:
    out = subprocess.run(["pgrep", "-lf", "bsr_resolve.py"],
                         capture_output=True, text=True).stdout or ""
    cats = set()
    for line in out.splitlines():
        parts = line.split()
        if "--cat" in parts:
            i = parts.index("--cat")
            if i + 1 < len(parts):
                cats.add(parts[i + 1])
    return cats


def load_weights():
    """v1.1 类目产出率权重：yield_weights.txt 每行 '类目 权重'。
    实测依据（2026-11-11 合并三机数据）：industrial 50% / home-garden 40% /
    sporting 24% / automotive 11%——按待解量排序会把最不值钱的类目排最前。"""
    w = {}
    p = BASE / "yield_weights.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) == 2:
                try:
                    w[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return w


def main():
    # 可选白名单：脚本目录存在 cats.txt 时，只管理列出的类目（双机分工防重叠）
    W = load_weights()
    if W:
        print(f"[fleet] 产出率权重生效：{W}", flush=True)
    allow = None
    wl = BASE / "cats.txt"
    if wl.exists():
        allow = {l.strip() for l in wl.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")}
        print(f"[fleet] cats.txt 白名单生效：{sorted(allow)}", flush=True)
    print(f"[fleet] 舰队管理器启动：最多 {MAX_RESOLVERS} 个并发解析，"
          f"每 {CHECK_EVERY}s 巡检", flush=True)
    while True:
        try:
            busy = running_cats()
            slots = MAX_RESOLVERS - len(busy)
            if slots > 0:
                pend = []
                for f in sorted(glob.glob(str(OUTDIR / "state_*.json"))) + \
                         ([str(OUTDIR / "state.json")] if (OUTDIR / "state.json").exists() else []):
                    cat = ("pet-supplies" if os.path.basename(f) == "state.json"
                           else os.path.basename(f)[6:-5])
                    if allow is not None and cat not in allow:
                        continue
                    if cat in busy:
                        continue
                    # v1.2：结构告警类目跳过派活（每轮巡检打印提醒，人工删标记后自动恢复）
                    if (OUTDIR / f"ALARM_{cat}.flag").exists():
                        print(f"[fleet] {time.strftime('%H:%M')} ⚠ {cat} 处于结构告警"
                              f"（ALARM_{cat}.flag），跳过派活；人工核对后删除标记恢复", flush=True)
                        continue
                    n = len(pool_for(cat) - done_for(cat))
                    if n >= PENDING_MIN:
                        # v1.1 排序 = 待解量 × 类目产出率（缺省 0.25），
                        # 而不是裸待解量——避免把请求压在低产类目上
                        pend.append((n * W.get(cat, 0.25), n, cat))
                pend.sort(reverse=True)  # 先啃"预计能出卖家最多"的
                for _score, n, cat in pend[:slots]:
                    log = open(OUTDIR / f"resolve_{cat}.log", "ab")
                    subprocess.Popen(
                        [sys.executable, "bsr_resolve.py", "--cat", cat],
                        stdout=log, stderr=subprocess.STDOUT,
                        cwd=BASE,  # v1.1：显式工作目录，从任意目录启动舰队都不会找不到脚本
                        start_new_session=True)  # 舰队重启不连坐
                    print(f"[fleet] {time.strftime('%H:%M')} 启动 resolver {cat}"
                          f"（待解 {n}）", flush=True)
                    busy.add(cat)
                    if len(busy) >= MAX_RESOLVERS:
                        break
            else:
                print(f"[fleet] {time.strftime('%H:%M')} 满编：{sorted(busy)}", flush=True)
        except Exception as e:
            print(f"[fleet] 巡检异常: {e}", flush=True)
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
