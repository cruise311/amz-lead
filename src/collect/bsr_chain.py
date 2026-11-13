#!/usr/bin/env python3
"""多类目 BSR 流水线：逐类目执行 全树walker → 榨干resolver，断点+完成标记。
用法: python3 bsr_chain.py [--cats slug1,slug2,...] [--only-walk]
每个类目产出 bsr_full/state_<cat>.json + resolve_<cat>.jsonl + sellers_<cat>.csv。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "bsr_full"

# 货代相关性优先级排序（大件/重货/中国卖家密集类目在前）
# slug 已按 2026-11-06 官方 zgbs 目录校准（/Best-Sellers/zgbs 顶层 40 部门）
_DEFAULT_CATS_ALL = [
    "home-garden",             # 家居花园（大件重货主力）
    "kitchen",                 # 厨房餐饮（中国卖家密集）
    "sporting-goods",          # 运动户外（原 sports-and-outdoors 为错误 slug）
    "hi",                      # 工具与家装（hi = home improvement）
    "toys-and-games",          # 玩具（断点续跑中）
    "lawn-garden",             # 庭院草坪
    "office-products",         # 办公
    "hpc",                     # 健康个护
    "baby-products",           # 母婴
    "automotive",              # 汽配
    "wireless",                # 手机配件（中国卖家最密）
    "electronics",             # 电子
    "industrial",              # 工业品（B2B 大件）
    "arts-crafts",             # 手工艺
    "beauty",                  # 美妆
    "grocery",                 # 食品
]

# 三机分工：cats.txt 存在时以它为准（白名单），防止跨机器重复爬
_wl = BASE / "cats.txt"
if _wl.exists():
    DEFAULT_CATS = [l.strip() for l in _wl.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")]
else:
    DEFAULT_CATS = _DEFAULT_CATS_ALL


def done_marker(cat):
    return OUT / f"done_{cat}.flag"


def done_walk_marker(cat):
    """v1.1（坑#6 代码修复）：--only-walk 的树级完成标记与全链 done_ 分开。
    旧版 only-walk 走完树也写 done_ 标记，导致解析阶段被整个跳过。"""
    return OUT / f"done_walk_{cat}.flag"


def fail_marker(cat):
    return OUT / f"failed_{cat}.flag"


def state_path(cat):
    if cat == "pet-supplies" and (OUT / "state.json").exists():
        return OUT / "state.json"
    return OUT / f"state_{cat}.json"


def run(cmd):
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=BASE).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cats", default=",".join(DEFAULT_CATS))
    ap.add_argument("--only-walk", action="store_true", help="只画树不解析（调试用）")
    args = ap.parse_args()
    cats = [c.strip() for c in args.cats.split(",") if c.strip()]

    t0 = time.time()
    for i, cat in enumerate(cats):
        if done_marker(cat).exists() or \
                (args.only_walk and done_walk_marker(cat).exists()):
            print(f"[{i+1}/{len(cats)}] {cat} 已完成，跳过", flush=True)
            continue
        if fail_marker(cat).exists():
            print(f"[{i+1}/{len(cats)}] {cat} 之前失败（slug 无效?），跳过", flush=True)
            continue
        print(f"\n########## [{i+1}/{len(cats)}] 类目 {cat} ##########", flush=True)

        # 1) 全树 walker（无上限，跑完整棵树；only-walk 模式连抽样也关掉，全部交给 resolver）
        cmd = [sys.executable, "bsr_full.py", "--cat", cat]
        if args.only_walk:
            cmd.append("--no-sample")
        rc = run(cmd)
        sp = state_path(cat)
        n_nodes = 0
        if sp.exists():
            try:
                n_nodes = len(json.loads(sp.read_text()).get("nodes", {}))
            except Exception:
                pass
        if rc != 0 or n_nodes < 5:
            fail_marker(cat).write_text(f"walker rc={rc} nodes={n_nodes}")
            print(f"!! {cat} walker 失败（rc={rc}, nodes={n_nodes}），标记跳过", flush=True)
            continue

        # 2) 榨干 resolver
        if not args.only_walk:
            run([sys.executable, "bsr_resolve.py", "--cat", cat])
            done_marker(cat).write_text("ok")
            print(f"##### {cat} 完成（累计 {time.time()-t0:.0f}s）#####", flush=True)
        else:
            # v1.1（坑#6 修复）：only-walk 只写树级标记，不再写全链 done_ 标记
            done_walk_marker(cat).write_text("walk-only")
            print(f"##### {cat} 树完成，未解析（--only-walk；解析交给 resolver 舰队/全链重跑）"
                  f"（累计 {time.time()-t0:.0f}s）#####", flush=True)

    print(f"\n===== 流水线全部结束，用时 {(time.time()-t0)/3600:.1f}h =====", flush=True)


if __name__ == "__main__":
    main()
