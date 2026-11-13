#!/usr/bin/env python3
"""pet-supplies 全树 walker — BSR 类目树爬法正式版（v1.1，试点已验证参数）。
策略:
  - BFS 从 zgbs 根下钻（cap 深度 4），每个节点只抓一次
  - 部门级/有子节点的内部节点: 只做发现，不抓榜单（试点: 3P 率 12%，全是 1P 大牌）
  - 叶子节点（无子节点或到深度上限）: 抓 1-2 页 Top 100 榜单 + 每节点抽样 4 个商品页解卖家ID
  - ASIN 全局去重（跨节点见过即不再抓商品页）
节流: 2.5-4.5s/请求；503/验证码退避 90-900s；硬上限 --max-requests；断点 state_<类目>.json
指纹: 与 bsr_resolve.py 同一套 Scrapling/curl_cffi，绝不手传 UA（坑#1）
失败节点不标 visited：网络失败/超时的节点下次续跑自动重试（v1.1 修复，旧行为会永久丢子树）
v1.2 结构告警：连续 20 个叶子榜单页 raw ASIN < 5 → 写 ALARM_<类目>.flag 并退出码 3 停机
      （zgbs 结构改版后 extract_asins 会静默产出空榜单，树的「完成」是假的）
v1.3 多榜单：--list best/new（new=真入口 /gp/new-releases；/New-Releases/zgbs 是软别名，
      实测内容与 Best Sellers 30/30 全同，见坑#26）。非 best 榜单断点加 __<kind> 后缀，
      fleet/resolver/seller_products 以伪类目 <类目>__<kind> 零改动兼容。
产出: bsr_full/{nodes,items,sellers}_<文件前缀>.csv + 交叉 3425 报告（报告文件缺失时只跳过报告）
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from collections import OrderedDict, deque
from pathlib import Path

try:
    from scrapling.fetchers import Fetcher
except ImportError:
    print("缺少 scrapling: pip install scrapling", flush=True)
    raise

BASE = Path(__file__).resolve().parent
OUT = BASE / "bsr_full"
try:
    OUT.mkdir(exist_ok=True)
except FileExistsError:
    pass  # 沙箱 mkdir 拦截器对已存在目录会误抛 EEXIST
CAT = "pet-supplies"        # 类目 slug（URL 用）
LIST = "best"               # 榜单类型（v1.3：best/new）
FILE_CAT = CAT              # 文件命名用；非 best 榜单 = <cat>__<kind>，fleet 以它为伪类目
ROOT_ID = "root"
N_LEVEL_CAP = 4
SAMPLE_PER_NODE = 4

# v1.3 榜单类型（2026-11-11 实测校准，详见 06_修改说明_v1.3.md 与坑#26）：
#   best = Best Sellers，/Best-Sellers/zgbs/<cat>（沿用历史断点命名）
#   new  = New Releases，真入口 /gp/new-releases/<cat>。
#          ⚠ /New-Releases/zgbs/<cat> 是软别名——实测返回内容与 Best Sellers 30/30 全同，
#            title 仍是 "Amazon Best Sellers"。绝不因 200 + URL 形似就当它是新榜单。
#   movers（未提供）= /gp/movers-and-shakers 页面榜单由前端异步加载（amz-aapi token），
#          静态 HTML 无 ASIN（data-asin=0、无 /dp/ 链接），需浏览器渲染抓取，别加进来空转。
LISTS = {
    "best": {"prefix": "Best-Sellers/zgbs", "seg": "zgbs"},
    "new":  {"prefix": "gp/new-releases", "seg": "new-releases"},
}

# 不要手传 UA！与 bsr_resolve.py 同理：curl_cffi impersonate='chrome' 模拟最新 Chrome
# TLS/HTTP2 指纹，并自动生成与之同版本号的 UA + 全套请求头。
# 手传 UA = 「指纹 2026 / 名片 2024」矛盾信号，直接触发验证码（坑#1，实测 94% 验证码率）。

STATE = OUT / "state.json"
state: dict = {}
stats = {"req": 0, "503": 0, "captcha": 0, "fail": 0, "cap_hit": False}


def set_cat(cat: str, kind: str = "best"):
    """切类目/榜单：best 沿用历史命名 state_<cat>.json（pet 兼容旧 state.json）；
    其他榜单文件名加 __<kind> 后缀（state_<cat>__new.json）。fleet 从文件名猜出的
    伪类目 <cat>__<kind> 会原样流经 pool_for/done_for/bsr_resolve.set_cat，解析侧零改动兼容。"""
    global CAT, LIST, FILE_CAT, STATE, state
    if kind not in LISTS:
        raise SystemExit(f"未知榜单类型 {kind}（可选：{'/'.join(LISTS)}）")
    CAT = cat
    LIST = kind
    FILE_CAT = cat if kind == "best" else f"{cat}__{kind}"
    if cat == "pet-supplies" and kind == "best" and (OUT / "state.json").exists():
        STATE = OUT / "state.json"
    else:
        STATE = OUT / f"state_{FILE_CAT}.json"
    state = json.loads(STATE.read_text() or "{}") if STATE.exists() else {}


def save_state():
    STATE.write_text(json.dumps(state, ensure_ascii=False))


def get(url):
    """Scrapling Fetcher 抓取（与 bsr_resolve 同一套指纹）。返回 HTML 或 None。"""
    if MAX_REQ and stats["req"] >= MAX_REQ:
        stats["cap_hit"] = True
        return None
    for _attempt in range(2):
        time.sleep(random.uniform(2.5, 4.5))
        try:
            r = Fetcher.get(url, timeout=30)
            stats["req"] += 1
            h = getattr(r, "html_content", None) or str(r)
            status = getattr(r, "status", 200) or 200
        except Exception:
            stats["fail"] += 1
            time.sleep(15)
            continue
        # 验证码页特征（与 bsr_resolve.fetch_dp 同判据）：先看内容再看状态码
        if "validateCaptcha" in h or "Robot Check" in h or (
                len(h) < 6000 and "captcha" in h.lower()):
            stats["captcha"] += 1
            stats["streak"] = stats.get("streak", 0) + 1
            cool = random.uniform(90, 180) if stats["streak"] < 5 else random.uniform(600, 900)
            print(f"  ⚠ 验证码(连续{stats['streak']})，冷却{cool:.0f}s: {url[:70]}", flush=True)
            time.sleep(cool)
            continue
        stats["streak"] = 0
        if status in (503, 429):
            stats["503"] += 1
            time.sleep(random.uniform(60, 120))
            continue
        if status != 200:
            stats["fail"] += 1
            return None
        return h
    return None


def node_url(nid):
    pre = LISTS[LIST]["prefix"]
    if nid == ROOT_ID:
        return f"https://www.amazon.com/{pre}/{CAT}/"
    return f"https://www.amazon.com/{pre}/{CAT}/{nid}"


def extract_children(html):
    """子节点: [(id, name)]。链接格式随榜单类型不同（2026-11-11 实测）：
    zgbs 系（best）= /Best-Sellers-.../zgbs/<cat>/<nid>；gp 系（new）= /gp/new-releases/<cat>/<nid>，
    两种格式在页面上干净分离（gp 页 zgbs 正则命中 0），按榜单切正则互不误收。"""
    seg = LISTS[LIST]["seg"]
    kids = OrderedDict()
    pat = r'href="(/[^"]*?/' + seg + r'/' + CAT + r'/(\d+)[^"]*)"[^>]*>([^<]{2,60})<'
    for m in re.finditer(pat, html):
        nid, name = m.group(2), m.group(3).strip()
        if nid in kids:
            continue
        kids[nid] = re.sub(r"\s+", " ", name)
    return kids


def extract_asins(html):
    seen = OrderedDict()
    for m in re.finditer(r'data-asin="([A-Z0-9]{10})"', html):
        seen.setdefault(m.group(1), None)
    if not seen:
        for m in re.finditer(r'/dp/([A-Z0-9]{10})[?"&]', html):
            seen.setdefault(m.group(1), None)
    return list(seen)


def main():
    global MAX_REQ
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", default="pet-supplies", help="zgbs 类目 slug，如 home-and-kitchen")
    ap.add_argument("--list", dest="list_kind", choices=sorted(LISTS), default="best",
                    help="榜单类型：best=Best Sellers（默认）/ new=New Releases（/gp/new-releases）")
    ap.add_argument("--max-requests", type=int, default=0, help="0 = 不设上限，跑完整棵树")
    ap.add_argument("--no-sample", action="store_true",
                    help="不抽样商品页（卖家解析交给 resolver 舰队，避免重复请求）")
    args = ap.parse_args()
    if args.no_sample:
        global SAMPLE_PER_NODE
        SAMPLE_PER_NODE = 0
    MAX_REQ = args.max_requests
    set_cat(args.cat, args.list_kind)
    print(f"全树 walker 启动（类目 {CAT}，榜单 {LIST}，文件前缀 {FILE_CAT}，上限 {MAX_REQ} 请求）", flush=True)

    # 断点恢复
    visited: dict[str, dict] = state.setdefault("nodes", {})
    leaf_items: dict[str, list[str]] = state.setdefault("leaf_items", {})
    seller_rows: list[dict] = state.get("seller_rows", [])
    seen_asins: set[str] = state.setdefault("seen_asins", [])
    seen_asins = set(seen_asins)
    # v1.1 迁移：旧版把抓取失败的节点也写进 visited（fetched=False），清掉让它们重新入队
    dead_nodes = [k for k, v in list(visited.items()) if not v.get("fetched", True)]
    for k in dead_nodes:
        visited.pop(k, None)
    if dead_nodes:
        print(f"断点迁移：清理 {len(dead_nodes)} 个历史失败节点，重新入队", flush=True)

    # 从断点重建队列：已访问节点的 child_ids 里未访问的重新入队
    queue = deque()
    for vid, info in visited.items():
        for cid in info.get("child_ids", []):
            if cid not in visited:
                queue.append((cid, info.get("level", 0) + 1, vid))
    if not queue:
        # 老格式断点迁移：names 里未访问的全部按 L3 入队（父子关系未存，仅影响深度标签）
        for cid in state.get("names", {}):
            if cid not in visited:
                queue.append((cid, 3, ""))
        if queue:
            print(f"老断点迁移：{len(queue)} 个未访问节点重新入队", flush=True)
    else:
        print(f"断点续跑：{len(queue)} 个未访问节点入队", flush=True)
    if ROOT_ID not in visited:
        queue.appendleft((ROOT_ID, 0, ""))
    nodes_out = []
    zero_leaf_streak = 0   # v1.2：连续「榜单页几乎没 ASIN」计数（结构告警用），不跨运行持久化

    while queue:
        if stats["cap_hit"]:
            print("到达请求上限，收工（断点已存）", flush=True)
            break
        nid, lvl, parent = queue.popleft()
        if nid in visited:
            continue
        html = get(node_url(nid))
        if not html:
            # v1.1：失败不写 visited —— 下次续跑由父节点 child_ids 重新入队重试。
            # 旧行为在这里就标 visited，瞬时失败会永久丢掉这棵子树。
            continue
        if nid == ROOT_ID:
            mt = re.search(r"<title>([^<]*)</title>", html, re.I | re.S)
            if mt and "undefined" in mt.group(1).lower():
                # 坑#4 落地（v1.3 泛化到各榜单类型）：假 slug 返回 200 但 title 含 undefined；
                # 退出码 2 让 bsr_chain 写 fail 标记，而不是把无效 slug 当叶子空跑
                print(f"!! slug「{CAT}」无效（title: {mt.group(1).strip()[:60]}），退出码 2", flush=True)
                sys.exit(2)
        visited[nid] = {"level": lvl, "parent": parent, "fetched": True}
        kids = extract_children(html)
        name = kids.pop(nid, "")  # 有的页面会链接自己
        is_leaf = (not kids) or lvl >= N_LEVEL_CAP
        visited[nid]["name"] = ""
        visited[nid]["children"] = len(kids)
        visited[nid]["child_ids"] = list(kids.keys())
        nodes_out.append((nid, lvl, parent, is_leaf, len(kids)))
        for cid, cname in kids.items():
            if cid not in visited and cid not in {q[0] for q in queue}:
                state["names"] = state.get("names", {})
                state["names"][cid] = cname
                queue.append((cid, lvl + 1, nid))
        if nid == ROOT_ID or (kids and lvl < N_LEVEL_CAP):
            state["nodes"] = {**visited, "names": state.get("names", {})}
            save_state()
            print(f"L{lvl} {nid}: 内部节点, 子节点 {len(kids)}（不抓榜单）", flush=True)
            continue
        # 叶子: 抓榜单 1-2 页
        asins: list[str] = []
        pg1_raw = 0
        pg1_fetched = False
        for pg in (1, 2):
            u = node_url(nid) + ("?pg=2" if pg == 2 else "")
            h = get(u)
            if not h:
                continue
            page_asins = extract_asins(h)
            asins += page_asins
            if pg == 1:
                pg1_raw = len(page_asins)
                pg1_fetched = True
            if pg == 1 and len(page_asins) < 40:
                break
        # v1.2 结构告警：只统计「拿到了页面但抽不出 ASIN」（结构信号）；取页失败不计入
        # （网络/风控问题由 v1.1 失败重试兜底，混进来会把断网误报成改版）。raw 数不去重
        # 不排 seen——uniq=0 可能只是跨节点重复，属正常。
        if not pg1_fetched:
            pass
        elif pg1_raw < 5:
            zero_leaf_streak += 1
        else:
            zero_leaf_streak = 0
        if zero_leaf_streak >= 20:
            # v1.3：告警标记用 FILE_CAT —— fleet 按伪类目（文件名）查 ALARM_<伪类目>.flag，
            # 两个名字必须一致，否则告警后 fleet 会继续派活
            (OUT / f"ALARM_{FILE_CAT}.flag").write_text(json.dumps({
                "cat": FILE_CAT, "list": LIST, "ts": time.time(),
                "reason": "连续 20 个叶子榜单页 raw ASIN < 5 —— 榜单页结构可能已改版",
                "last_node": nid}, ensure_ascii=False))
            print(f"\n!!! 结构告警：连续 {zero_leaf_streak} 个叶子榜单页 raw ASIN < 5，"
                  f"榜单页结构可能已改版（详见 bsr_full/ALARM_{FILE_CAT}.flag）", flush=True)
            sys.exit(3)
        uniq = [a for a in OrderedDict.fromkeys(asins) if a not in seen_asins]
        seen_asins.update(uniq)
        leaf_items[nid] = uniq
        # 抽样商品页解卖家
        picked = uniq[:SAMPLE_PER_NODE] if len(uniq) > SAMPLE_PER_NODE else uniq
        for a in picked:
            if f"p_{a}" in state:
                continue
            h = get(f"https://www.amazon.com/dp/{a}")
            rec = {"node_id": nid, "asin": a, "seller_id": "", "brand": ""}
            if h:
                m = re.search(r'seller=([A-Z0-9]{10,20})', h) or \
                    re.search(r'seller\.html\?seller=([A-Z0-9]{10,20})', h)
                if m:
                    rec["seller_id"] = m.group(1)
                m = re.search(r'id="bylineInfo"[^>]*>\s*(?:[^<]*?([A-Za-z0-9][^<]{1,60}?))\s*</', h)
                if m:
                    rec["brand"] = m.group(1).strip()
            state[f"p_{a}"] = {"done": True}
            seller_rows.append(rec)
        state["nodes"] = visited
        state["leaf_items"] = leaf_items
        state["seller_rows"] = seller_rows
        state["seen_asins"] = list(seen_asins)
        save_state()
        print(f"L{lvl} {nid}: 叶子, ASIN {len(uniq)}, 抽样 {len(picked)}, "
              f"3P {sum(1 for r in seller_rows[-len(picked):] if r['seller_id'])}, "
              f"请求 {stats['req']}/{MAX_REQ}", flush=True)

    # 汇总落盘（按文件前缀命名=类目或类目__榜单，避免互相覆盖）
    with (OUT / f"nodes_{FILE_CAT}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "level", "parent", "is_leaf", "children", "name"])
        names = state.get("names", {})
        for nid, lvl, parent, is_leaf, nk in nodes_out:
            w.writerow([nid, lvl, parent, is_leaf, nk, names.get(nid, "")])
    items_rows = [(nid, a) for nid, lst in leaf_items.items() for a in lst]
    with (OUT / f"items_{FILE_CAT}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "asin"])
        w.writerows(items_rows)
    if seller_rows:
        with (OUT / f"sellers_{FILE_CAT}.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["node_id", "asin", "seller_id", "brand"])
            w.writeheader()
            w.writerows(seller_rows)

    # 汇总报告
    resolved = {r["seller_id"] for r in seller_rows if r["seller_id"]}
    n_leaf = sum(1 for _, _, _, lf, _ in nodes_out if lf)
    print(f"\n===== 全树 walker 报告（{FILE_CAT}）=====")
    print(f"节点 {len(nodes_out)}（叶子 {n_leaf}）| 榜单ASIN(新) {len(seen_asins)} | "
          f"商品页 {len(seller_rows)} | 解出3P卖家 {len(resolved)}")
    print(f"请求 {stats['req']} | 503={stats['503']} 验证码={stats['captcha']} 失败={stats['fail']}")
    print(f"产出: bsr_full/{{nodes,items,sellers}}_{FILE_CAT}.csv")
    # 交叉 3425（可选报告：文件缺失只跳过报告——否则 rc≠0 会让链把整个类目标成失败）
    try:
        big_ids = set()
        for r in csv.DictReader(open(BASE / "leads_final_ranked_3425.csv", encoding="utf-8-sig")):
            sid = (r.get("卖家ID") or "").strip()
            if sid:
                big_ids.add(sid)
        overlap = resolved & big_ids
        print(f"与 3425 重合 {len(overlap)} | 新卖家 {len(resolved - big_ids)}（新覆盖率 "
              f"{len(resolved - big_ids) / max(len(resolved), 1) * 100:.0f}%）")
    except FileNotFoundError:
        print("（未找到 leads_final_ranked_3425.csv，跳过交叉报告）")


if __name__ == "__main__":
    main()
