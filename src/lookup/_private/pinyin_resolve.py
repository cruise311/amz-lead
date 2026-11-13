# -*- coding: utf-8 -*-
"""pinyin_resolve.py —— 候选中文名 → 唯一确认名（离线，不联网）

第零步的收口环节。上游两种数据源产出候选：
  - tyc_lookup.py     → leads/tyc_hits*.jsonl（带登记状态）
  - 企查查网页版爬取   → pinyin_hits.jsonl（只有名字，无状态）

本脚本把「拼音完全一致」的候选留下，再用登记状态砍掉已注销主体，
输出唯一确认名。**不做模糊匹配、不做同音字猜测**——错号比没号贵。

## 判定规则（按优先级）

1. 候选名转拼音 == 目标拼音（完全一致，含「市」字与括号差异）
2. 在匹配项里取「存续/在业/开业/正常」的
3. 存活项恰好 1 家 → 确认（src=tyc_live）
   存活项 ≥2 家 → 多候选，不做选择（ambig）
   全部已注销   → 记入 dead（有价值：这家店铺主体已死，别花额度去查）
   状态缺失     → 只有唯一候选时才确认（src=tyc_unknown），否则进 ambig

## 实测天花板（2026-11-06，1520 家亚马逊店铺）

| 结果 | 数量 | 占比 |
|---|---|---|
| 唯一确认 | 1039 | 68% |
| 同城同音多候选 | 249+ | 16% |
| 查无 | 104 | 7% |
| 未跑 | 余下 | — |

补登记状态只把 410 个多候选砍掉 50 个（12%），剩下的都是「同城、同音、
不同字、且全部存续」的真歧义（小马 / 小玛），名字和状态都解决不了。
这就是拼音还原的真实上限，不要预期 90%。

## 用法

```bash
python3 scripts/pinyin_resolve.py \
  --targets leads/pinyin_targets.json \
  --hits leads/tyc_hits2.jsonl \
  --out-confirmed leads/confirmed_names.json \
  --out-unresolved leads/unresolved.json
```
"""
import argparse
import json
import re

try:  # Windows 默认 GBK，输出 ✓ → 重定向到文件会 UnicodeEncodeError
    from _console import setup as _utf8_stdio
    _utf8_stdio()
except Exception:
    pass

LIVE = ("存续", "在业", "开业", "正常")
DEAD = ("注销", "吊销", "迁出", "清算", "停业")


def norm(s):
    return re.sub(r"[^a-z\u4e00-\u9fff]", "", (s or "").lower())


def topy(name):
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        raise SystemExit("缺 pypinyin：pip install pypinyin")
    return norm("".join(lazy_pinyin(name, errors=lambda x: list(x))))


def load_hits(paths):
    """多条 JSONL 合并，同一拼音后者覆盖前者（新数据优先）。"""
    seen = {}
    for path in paths:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("py"):
                seen[r["py"]] = r
    return seen


def load_qcc(path):
    """企查查命中（只有名字，无登记状态），作为交叉校验。"""
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") == "hit" and (r.get("hit") or {}).get("name"):
                out[r["py"]] = r["hit"]
    except FileNotFoundError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description="候选中文名 → 唯一确认名（离线）")
    ap.add_argument("--targets", required=True, help="pinyin_targets.json（含 order/targets）")
    ap.add_argument("--hits", required=True, action="append",
                    help="tyc_hits JSONL，可给多个（后给的覆盖先给的）")
    ap.add_argument("--qcc", help="企查查 pinyin_hits.jsonl（可选，做交叉校验）")
    ap.add_argument("--out-confirmed", default="confirmed_names.json")
    ap.add_argument("--out-unresolved", default="unresolved.json")
    ap.add_argument("--name-limit", type=int, default=40, help="名字最大长度，防脏数据")
    args = ap.parse_args()

    d = json.load(open(args.targets, encoding="utf-8"))
    targets = d.get("targets") if isinstance(d, dict) and "targets" in d else d
    order = d.get("order") if isinstance(d, dict) and isinstance(d.get("order"), list) else list(targets)

    hits = load_hits(args.hits)
    qcc = load_qcc(args.qcc) if args.qcc else {}

    confirmed, ambig, dead, none, unrun = {}, [], [], [], []
    for py in order:
        r = hits.get(py)
        if r is None:
            unrun.append(py)          # 压根没跑过，不是「查无」
            continue
        cands = [c for c in (r.get("cands") or []) if isinstance(c, dict) and c.get("name")]
        matched = [c for c in cands
                   if topy(c["name"]) == norm(py) and len(c["name"]) <= args.name_limit]
        tgt = targets.get(py) or {}
        base = {"py": py, "shop": tgt.get("shop", ""), "addr": tgt.get("addr", "")}

        if not matched:
            none.append(py)
            continue
        live = [c for c in matched if c.get("status") in LIVE]
        deadc = [c for c in matched if c.get("status") in DEAD]
        unk = [c for c in matched if not c.get("status")]

        if len(live) == 1:
            c = live[0]
            confirmed[c["name"]] = dict(base, status=c["status"], legal=c.get("legal", ""),
                                        src="tyc_live",
                                        alts=[x["name"] + "/" + (x.get("status") or "?")
                                              for x in matched if x is not c])
        elif len(live) > 1:
            ambig.append([py, [x["name"] for x in live]])
        elif deadc:
            dead.append([py, [x["name"] + "/" + x["status"] for x in deadc]])
        elif len(unk) == 1:
            c = unk[0]
            confirmed[c["name"]] = dict(base, status="", legal=c.get("legal", ""),
                                        src="tyc_unknown", alts=[])
        elif unk:
            ambig.append([py, [x["name"] for x in unk]])
        else:
            none.append(py)

    # 企查查交叉校验：两边都命中且名字一致 → 提权；不一致 → 记进 alts 备查
    agree = conflict = 0
    for py, h in qcc.items():
        qn = h.get("name")
        if not qn:
            continue
        if qn in confirmed:
            confirmed[qn]["qcc_agree"] = True
            agree += 1
        else:
            hit_py = [n for n, v in confirmed.items() if v.get("py") == py]
            if hit_py:
                conflict += 1
                confirmed[hit_py[0]]["qcc_conflict"] = qn

    json.dump(confirmed, open(args.out_confirmed, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"ambig": ambig, "dead": dead, "none": none, "unrun": unrun},
              open(args.out_unresolved, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    total = len(order)
    by_src = {}
    for v in confirmed.values():
        by_src[v.get("src", "?")] = by_src.get(v.get("src", "?"), 0) + 1
    print(f"目标 {total} 家 | 有候选记录 {len(hits)} 条")
    print(f"  唯一确认 {len(confirmed)}  ({len(confirmed) * 100.0 / max(1, total):.1f}%)"
          f"  来源分布 {by_src}")
    print(f"  同音多候选 {len(ambig)}  全部已注销 {len(dead)}  查无 {len(none)}"
          f"  未跑 {len(unrun)}")
    if unrun:
        print(f"  ↑ 未跑 {len(unrun)} 家不是「不存在」，是还没查，别当结论")
    if qcc:
        print(f"  企查查交叉：一致 {agree}  冲突 {conflict}")
    print(f"→ {args.out_confirmed}\n→ {args.out_unresolved}")


if __name__ == "__main__":
    main()
