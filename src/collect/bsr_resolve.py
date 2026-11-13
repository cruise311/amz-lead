#!/usr/bin/env python3
"""榨干模式（Scrapling 版）：把 bsr_full/state_<类目>.json 里已收集的 ASIN 全部解卖家。
只做 商品页→卖家ID/品牌，不爬树。断点 resolve_<类目>.jsonl，可随时停续。
v1.1：瞬时失败（超时/bm-verify 小页）不再当「已完成·无卖家」记录——旧行为会把
      失败与死 ASIN 混在一起永久丢数据；连续 3 次失败才写 unresolved 终态。
v1.2：结构告警——滚动窗口（近 100 条真页记录）title 缺失率 ≥50% 或 3P 命中率 ≤5%
      即写 ALARM_<类目>.flag 并退出码 3 停机，防亚马逊改版后字符串判据静默失效。
用法: python3 bsr_resolve.py [--cat 类目] [--max-requests 0]
"""
import argparse
import csv
import html
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
OUTDIR = BASE / "bsr_full"
CAT = "pet-supplies"
STATE = OUTDIR / "state.json"
PROG = OUTDIR / "resolve_progress.jsonl"
OUT = OUTDIR / "sellers_all.csv"
FAILS = OUTDIR / "resolve_fails.json"      # v1.1：瞬时失败计数（断点辅助文件）
FAIL_MAX = 3

# v1.2 结构告警阈值（误报的代价=停一次+人工删标记；漏报的代价=静默丢一周数据，宁可保守）
ALARM_WINDOW = 100        # 滚动窗口：最近 N 条【已通过真页校验】的记录
ALARM_WINDOW_MIN = 50     # 窗口攒够这么多条才开始判，避免小样本误杀
ALARM_TITLE_MISSING = 0.20  # 真商品页（原始 HTML 含 id="productTitle"）解析失败率 ≥20% 即判据失效
ALARM_SELLER_HIT = 0.05     # v1.2.3 起弃用：全自营叶子会误杀，改由 canary 判据兜底
# v1.2.3 canary：定期抓已知 3P 商品页，验证 seller 抽取是否仍有效
# v1.3.3 起双样本，任一正常即不告警（单样本变质不再误报）
CANARY_ASINS = [
    "B0HPPS4YF5",  # sporting（2026-11-11 实测有卖家 A1XY0RNJ4W2GVE）
    "B0HD73DXLB",  # hi 类目 BSR#49（2026-11-11 09:31 生产 resolver 成功解析，卖家 A3OZTQPCG1XF24）
]
CANARY_EVERY = 100          # 每处理 N 条做一次检查
CANARY_FAIL_MAX = 2         # 连续失败 N 次才判失效（防单次瞬时失败误报）
# v1.2.5 叶子级早停：同一 BSR 叶子连续 N 个 ASIN 都没有 3P 卖家 → 跳过该叶剩余。
# 依据（2026-11-11 实测）：44 个 ≥4 条记录的叶子里 70% 的 3P 率=0（中位 0），
# 且叶子内 3P 分布与位置无关（前段 68 / 后段 74）——说明 3P 是"叶子属性"不是"位置属性"。
# 预期：请求降 55-60%，3P 产出降约 7-10%，单位时间产出约翻倍。
# 设 0 关闭该策略。被跳过的 ASIN 记为 kind=skipped_1p_leaf（可日后删记录重跑）。
EARLY_STOP_AFTER = 3

# 不要手传 UA！Scrapling 底层 curl_cffi impersonate='chrome' 会模拟最新 Chrome TLS 指纹，
# 并自动生成与之【同版本号】的 UA + 全套请求头——指纹与身份永远一致。
# 手传 UA 会覆盖自动 UA，造成「指纹 2026 / 名片 2024」矛盾信号，直接触发验证码。

stats = {"req": 0, "fail": 0, "hit": 0}


def set_cat(cat: str):
    """切类目：state_<cat>.json / resolve_<cat>.jsonl / sellers_<cat>.csv（pet 兼容旧文件）。"""
    global CAT, STATE, PROG, OUT, FAILS
    CAT = cat
    legacy = OUTDIR / "state.json"
    if cat == "pet-supplies" and legacy.exists():
        STATE = legacy
        PROG = OUTDIR / "resolve_progress.jsonl"
        OUT = OUTDIR / "sellers_all.csv"
        FAILS = OUTDIR / "resolve_fails.json"
    else:
        STATE = OUTDIR / f"state_{cat}.json"
        PROG = OUTDIR / f"resolve_{cat}.jsonl"
        OUT = OUTDIR / f"sellers_{cat}.csv"
        FAILS = OUTDIR / f"resolve_fails_{cat}.json"


def fetch_dp(asin: str) -> str | None:
    """用 Scrapling 抓商品页，自动处理 bm-verify 中转页，返回 HTML 或 None。
    遇验证码不记 done（返回特殊标记），让主循环冷却后跳过（下次运行重试）。"""
    url = f"https://www.amazon.com/dp/{asin}"
    try:
        r = Fetcher.get(url, timeout=30)
        stats["req"] += 1
        h = getattr(r, "html_content", None) or str(r)
        # 验证码页特征：小页面 + validateCaptcha / Robot Check
        if "validateCaptcha" in h or "Robot Check" in h or (
                len(h) < 6000 and "captcha" in h.lower()):
            stats["captcha"] = stats.get("captcha", 0) + 1
            return "CAPTCHA"
        # 判断是否拿到了真页：商品页（含 productTitle，可能是 1P 自营无卖家）
        # 或 商品列表页（死 ASIN 跳转，含 seller=）。两者都收，绝不重试——
        # v1.2.2：旧逻辑要求必须含 seller=，导致 1P 页被判失败、每个重试 3 次白烧请求
        # （实测 1P 页 1.4MB、有 productTitle、无 seller=，占深榜请求的 60%+）。
        if len(h) > 20000 and ('id="productTitle"' in h or "seller=" in h):
            return h
        # v1.2.7（借鉴联想机实测）：小页/降级页 = 被拦截，**绝不能写进度**。
        # 旧行为把它算 fail，累计 3 次后写 unresolved 终态 —— 等于把 ASIN 永久作废，
        # 而它可能只是被临时拦截。拦截页返回独立标记，主循环不写任何记录、不计失败，
        # 让它留在队列下一轮重试。
        stats["blocked"] = stats.get("blocked", 0) + 1
        return "BLOCKED"
    except Exception as e:
        stats["fail"] += 1
        if "timeout" in str(e).lower():
            print(f"    {asin}: 超时", flush=True)
        return None


def parse_seller(h: str):
    sid = ""
    m = re.search(r'seller=([A-Z0-9]{10,20})', h) or \
        re.search(r'seller\.html\?seller=([A-Z0-9]{10,20})', h)
    if m:
        sid = m.group(1)
    brand = ""
    m = re.search(r'id="bylineInfo"[^>]*>\s*(?:[^<]*?([A-Za-z0-9][^<]{1,60}?))\s*</', h)
    if m:
        brand = m.group(1).strip()
    return sid, brand


def parse_product(h: str):
    """从同一份 /dp/ HTML 顺手提取 标题/价格/BSR——零额外请求。
    提不到就留空，绝不抛异常影响主流程。"""
    title = price = bsr = ""
    m = re.search(r'id="productTitle"[^>]*>\s*([^<]{3,300}?)\s*<', h)
    if m:
        title = html.unescape(m.group(1).strip())
    # 价格：a-offscreen 首个带数字的（buybox 优先出现）；IP 在中国时为 CNY 计价
    m = re.search(r'class="a-offscreen"[^>]*>\s*([^<]*?\d[\d,.]*?)\s*<', h)
    if m:
        price = html.unescape(m.group(1)).strip()
    # BSR：如 "#598 in Sports & Outdoors ("，类目实体字符还原
    m = re.search(r'#([\d,]{1,12})\s+in\s+([^<]{2,60}?)\s*\(', h)
    if m:
        bsr = f"#{m.group(1)} in {html.unescape(m.group(2)).strip()}"
    return title, price, bsr


def main():
    global max_req
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", default="pet-supplies", help="zgbs 类目 slug")
    ap.add_argument("--max-requests", type=int, default=0)
    args = ap.parse_args()
    max_req = args.max_requests
    set_cat(args.cat)
    fails = json.loads(FAILS.read_text() or "{}") if FAILS.exists() else {}
    win = deque(maxlen=ALARM_WINDOW)      # v1.2：结构告警滚动窗口（仅真页记录）
    print(f"榨干模式启动（类目 {CAT}，Scrapling curl_cffi 指纹）", flush=True)

    state = json.loads(STATE.read_text())
    all_asins = OrderedDict()
    for nid, lst in state.get("leaf_items", {}).items():
        for a in lst:
            all_asins.setdefault(a, nid)
    done = {r.get("asin") for r in state.get("seller_rows", []) if r.get("asin")}
    done |= {r.get("asin") for r in iter_prog()} if PROG.exists() else set()
    todo = [(a, nid) for a, nid in all_asins.items() if a not in done]
    print(f"目标 ASIN {len(all_asins)} | 已解 {len(done)} | 待解 {len(todo)}", flush=True)

    f = PROG.open("a", encoding="utf-8")
    t0 = time.time()
    captcha_streak = 0
    cap_events = deque()   # v1.2.6 验证码事件时间戳（1 小时滚动窗口）
    canary_fail = 0        # v1.2.3：canary 连续失败计数
    canary_done = 0        # 已做 canary 检查次数
    leaf_stat = {}         # v1.2.5 叶子级早停：nid -> [尝试数, 3P命中数]
    skip_nid = None        # 已判定为 1P 叶子、剩余 ASIN 直接跳过的节点
    for i, (a, nid) in enumerate(todo):
        if max_req and stats["req"] >= max_req:
            print("达到请求上限，收工", flush=True)
            break
        # v1.2.5 早停：该叶子已判定为全自营，剩余 ASIN 只记标记不发请求
        if EARLY_STOP_AFTER and skip_nid == nid:
            f.write(json.dumps({"asin": a, "node_id": nid, "seller_id": "",
                                "kind": "skipped_1p_leaf", "ts": time.time()},
                               ensure_ascii=False) + "\n")
            stats["skipped"] = stats.get("skipped", 0) + 1
            continue
        h = fetch_dp(a)
        if h == "CAPTCHA":
            # v1.2.6 分级刹车（移植自华为机验证过的设计）：用 1 小时滚动窗口计数，
            # 不用纯连续 streak——纯连续有个死洞：暂停结束后 streak 归零，永远升不到 5 次，
            # 高档位和 flag 就成了摆设；窗口计数才能让"停完还撞"继续升级。
            #   1-2 次 → 60-120s（瞬时波动，别过度反应）
            #   3-4 次 → 暂停 20 分钟
            #   5+  次 → 暂停 60 分钟 + 写 CAPTCHA_BRAKE flag
            now_c = time.time()
            cap_events.append(now_c)
            while cap_events and now_c - cap_events[0] > 3600:
                cap_events.popleft()
            n1h = len(cap_events)
            stats["last_cap"] = now_c
            if n1h >= 5:
                flag = OUTDIR / f"CAPTCHA_BRAKE_{CAT}.flag"
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.write_text(json.dumps({"cat": CAT, "ts": now_c, "count_1h": n1h,
                                            "action": "pause_60min"}, ensure_ascii=False))
                print(f"  🛑 1小时内验证码 {n1h} 次，刹车 60 分钟并写 {flag.name}", flush=True)
                time.sleep(3600)
            elif n1h >= 3:
                print(f"  ⚠ 1小时内验证码 {n1h} 次，刹车 20 分钟", flush=True)
                time.sleep(1200)
            else:
                time.sleep(random.uniform(60, 120))
            continue
        captcha_streak = 0
        if h == "BLOCKED":
            # v1.2.7：被拦截的降级页/壳页——不写进度、不计失败、不算验证码。
            # 写进 progress 会让这个 ASIN 永久作废（旧行为最严重的数据损失），
            # 而它下轮很可能就正常返回。只做短睡退避，留在队列里。
            stats["blk"] = stats.get("blk", 0) + 1
            if stats["blk"] % 20 == 1:
                print(f"    {a}: 被拦截（降级页），不记进度，下轮重试"
                      f"（累计拦截 {stats['blk']}）", flush=True)
            time.sleep(random.uniform(15.0, 25.0))
            continue
        if not h:
            # v1.1：瞬时失败（超时/bm-verify 小页）不记 done——旧行为直接写成
            # 「seller_id 为空」的完成记录，和死 ASIN 混在一起；连续 FAIL_MAX 次
            # 失败才写 unresolved 终态，既不丢数据也不无限重试耗请求。
            fails[a] = fails.get(a, 0) + 1
            save_fails(fails)
            if fails[a] >= FAIL_MAX:
                f.write(json.dumps({"asin": a, "node_id": nid, "seller_id": "",
                                    "brand": "", "title": "", "price": "", "bsr": "",
                                    "unresolved": f"fetch_failed_x{FAIL_MAX}",
                                    "ts": time.time()}, ensure_ascii=False) + "\n")
                fails.pop(a, None)
                save_fails(fails)
                print(f"    {a}: 连续 {FAIL_MAX} 次取不到页，记 unresolved 终态", flush=True)
            time.sleep(random.uniform(8.0, 12.0))
            continue
        sid, brand = parse_seller(h)
        title, price, bsr = parse_product(h)
        if sid:
            stats["hit"] += 1
        # v1.2.2：区分三类页面，便于统计真实产出结构
        # 3p=有第三方卖家 / 1p=亚马逊自营商品页(无卖家) / dead=死ASIN列表页
        kind = "3p" if sid else ("1p" if title else "dead")
        # v1.2.5 叶子级早停统计
        if EARLY_STOP_AFTER:
            st = leaf_stat.setdefault(nid, [0, 0])
            st[0] += 1
            if kind == "3p":
                st[1] += 1
            elif st[0] >= EARLY_STOP_AFTER and st[1] == 0:
                skip_nid = nid
                print(f"  ⏭ 叶子 {nid} 连续 {st[0]} 个无 3P，跳过该叶剩余 ASIN",
                      flush=True)
        f.write(json.dumps({"asin": a, "node_id": nid, "seller_id": sid,
                            "brand": brand, "title": title, "price": price,
                            "bsr": bsr, "kind": kind, "ts": time.time()},
                           ensure_ascii=False) + "\n")
        # v1.2.1 结构告警：只把「真商品页」计入窗口。
        # 死 ASIN 跳转的商品列表页既无 productTitle 也无卖家，是深榜常态
        # （实测标题缺失率 sporting 52% / automotive 96% / toys 98%），
        # 混进窗口会把健康的爬取误判成改版。判据失效的真正信号是：
        # HTML 里明明有 id="productTitle" 标记，却解析不出标题。
        if 'id="productTitle"' in (h or ""):
            win.append((bool(title), bool(sid)))
        reason = alarm_check(win)
        if reason:
            f.flush()
            f.close()
            alarm_stop(reason, a, win)
        # v1.2.3 canary：定期验证 seller 抽取是否仍有效（替代已弃用的命中率判据）
        # v1.3.3 双样本交叉：同一轮检查两个不同 ASIN，任一正常即不告警——
        # 单样本变质（转 1P / 下架 / 地区屏蔽）不再可能造成误报。
        # 只有轮内全部样本都抽不出卖家，才计一次失败；连续 CANARY_FAIL_MAX 轮才停机。
        if (i + 1) % CANARY_EVERY == 0:
            checked, extracted = [], []
            for casin in CANARY_ASINS:
                hc = fetch_dp(casin)
                if not hc or hc == "CAPTCHA":
                    continue  # 验证码/取页失败不算抽取失效的证据，该样本本轮弃权
                # v1.2.4 修复：parse_seller 返回 (卖家ID, 品牌)，此处必须取第一个。
                # 旧写法 `_, csid = ...` 把品牌当卖家ID——canary 实际在检查"品牌行在不在"，
                # 对 seller 抽取失效（场景B：seller 没了但品牌还在）完全漏报。
                csid, _cbrand = parse_seller(hc)
                checked.append(casin)
                if csid:
                    extracted.append(csid)
            canary_done += 1
            if not checked:
                pass                       # 全部样本都取不到干净页：证据不足，不动计数
            elif extracted:
                canary_fail = 0            # 至少一个样本抽取正常 → 抽取管线健康
            else:
                canary_fail += 1
                print(f"  ⚠ canary 全部样本抽不出卖家 {canary_fail}/{CANARY_FAIL_MAX}"
                      f"（已查 {len(checked)} 个：{','.join(checked)}）", flush=True)
                if canary_fail >= CANARY_FAIL_MAX:
                    f.flush()
                    f.close()
                    alarm_stop(f"canary 连续 {canary_fail} 轮全部样本抽不出卖家"
                               f"（{','.join(checked)}）——seller 抽取已失效", a, win)
        if (i + 1) % 50 == 0:
            f.flush()
            el = time.time() - t0
            rate = (i + 1) / el * 3600
            remain = (len(todo) - i - 1) / max(rate, 1) * 3600
            tm = sum(1 for t, _ in win if not t)
            print(f"[{i+1}/{len(todo)}] 3P命中 {stats['hit']} | 失败 {stats['fail']} "
                  f"| 验证码 {stats.get('captcha', 0)} | 拦截 {stats.get('blk', 0)} "
                  f"| 速率 {rate:.0f}/h "
                  f"| 标题缺失 {tm}/{len(win)} "
                  f"| 跳过 {stats.get('skipped', 0)} "
                  f"| 剩余约 {remain/3600:.1f}h", flush=True)
        # v1.2.6 节流：基线 4-6s；只在"最近 30 分钟撞过验证码"时 ×1.5，
        # 之后回基线。删掉原来的 1.5^cycles（最高 5 倍、持续数小时）——
        # 那是"低速持续探测"，比短暂停顿更伤 IP 声誉（Mac 就是这样被磨坏的）。
        throttle = 1.5 if time.time() - stats.get("last_cap", 0) < 1800 else 1.0
        time.sleep(random.uniform(4.0, 6.0) * throttle)
    f.close()

    # 合并导出 sellers_all.csv（state 旧样本 + 本轮进度，按 ASIN 去重）
    merge_export()


def iter_prog():
    if not PROG.exists():
        return
    for line in PROG.open(encoding="utf-8"):
        try:
            yield json.loads(line)
        except Exception:
            pass


def save_fails(d):
    try:
        FAILS.write_text(json.dumps(d))
    except Exception:
        pass


def alarm_check(win):
    """v1.2.3 结构告警判据（纯函数，可单测）。防「字符串判据静默失效」：
    HTML 里明明有 productTitle 标记，却解析不出标题——结构已变或风控发真壳页。
    返回 None=正常；否则返回触发原因。win: [(title_present, seller_present), ...]

    v1.2.3：删除「3P 命中率 ≤5%」判据——实测会误杀。深榜存在全自营叶子节点
    （sporting 的 NCAA/NFL 授权商品叶，连续 50 个商品页 100% 是 1P），
    命中率低分不清「抽取坏了」和「这个叶子全是自营」。改由 canary 判据兜底
    （见 main() 里 CANARY_ASINS：定期抓已知 3P 商品页交叉验证，全部抽不出才算真失效）。"""
    if len(win) < ALARM_WINDOW_MIN:
        return None
    n = len(win)
    title_missing = sum(1 for t, _ in win if not t) / n
    if title_missing >= ALARM_TITLE_MISSING:
        return (f"title缺失率 {title_missing:.0%} ≥ {ALARM_TITLE_MISSING:.0%}"
                f"（近{n}条）——页面结构可能已变，或风控在发「真壳页」")
    return None


def alarm_stop(reason: str, last_asin: str, win) -> None:
    """写告警标记 → 停机。标记存在期间 fleet 跳过该类目；人工核对后删标记恢复。"""
    flag = OUTDIR / f"ALARM_{CAT}.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)  # 告警是安全机制，目录缺失也不能炸
    n = len(win)
    flag.write_text(json.dumps({
        "cat": CAT, "ts": time.time(), "reason": reason,
        "window": n,
        "title_missing_rate": round(sum(1 for t, _ in win if not t) / max(n, 1), 3),
        "seller_hit_rate": round(sum(1 for _, s in win if s) / max(n, 1), 3),
        "last_asin": last_asin,
    }, ensure_ascii=False))
    print(f"\n!!! 结构告警：{reason}", flush=True)
    print(f"!!! 已停机并写 {flag.name}。人工核对页面结构后删除该文件才能恢复本类目。", flush=True)
    sys.exit(3)


def merge_export():
    state = json.loads(STATE.read_text())
    rows = [r for r in state.get("seller_rows", []) if r.get("asin")]
    rows += list(iter_prog())
    seen, out = set(), []
    for r in rows:
        a = r["asin"]
        if a in seen:
            continue
        seen.add(a)
        out.append(r)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["卖家ID", "店招", "品牌", "ASIN", "来源节点", "商品标题", "价格", "BSR"])
        for r in out:
            w.writerow([r.get("seller_id", ""), r.get("shop_name", ""),
                        r.get("brand", ""), r["asin"], r.get("node_id", ""),
                        r.get("title", ""), r.get("price", ""), r.get("bsr", "")])
    sids = {r["seller_id"] for r in out if r.get("seller_id")}
    print(f"导出 {OUT.name}: ASIN {len(out)} | 唯一卖家 {len(sids)}", flush=True)


if __name__ == "__main__":
    main()