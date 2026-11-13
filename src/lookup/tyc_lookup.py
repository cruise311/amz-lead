# -*- coding: utf-8 -*-
"""tyc_lookup.py —— 拼音名 → 中文企业名（天眼查网页版，带登记状态）

## 它解决什么

线索表里只有亚马逊店铺的**拼音**名（如 `shenzhenshijuxinlikejiyouxiangongsi`），
下游（探迹/企查查/工商）都只吃中文名。天眼查是当前唯一能「拼音直接搜出候选」的
中文数据源，所以拿它做第零步：拼音 → 候选中文名 + 登记状态。

## 为什么必须抓登记状态

2026-11-06 实测：同一拼音常对应 2-3 家同城同音不同字的真实公司
（烜柯 / 轩客 / 玄科），只看名字无法消歧。但其中往往 2 家已注销，
登记状态能把多选题塌缩成单选题。这一字段是 v3.6 唯一被验证有硬价值的增量。

## 输出（JSONL，一家一行，可直接喂 pinyin_resolve.py）

```json
{"py": "shenzhen...", "cands": [{"name": "深圳市…有限公司",
  "status": "存续", "legal": "张三"}], "n": 1, "ts": "2026-11-06 14:30:00"}
```

## 用法

```bash
# 只跑一批（默认不自动过验证码，撞墙就通知+等人）
python3 scripts/tyc_lookup.py --input pending.json --out leads/tyc_hits.jsonl

# 开着验证码自愈（需要 agent 在线看图，见 references/captcha-selfheal.md）
python3 scripts/tyc_lookup.py --input pending.json --out leads/tyc_hits.jsonl \
    --auto-captcha --verify-wait 1800

# 首跑先探 20 家，确认能出候选再放量
python3 scripts/tyc_lookup.py --input pending.json --out leads/tyc_hits.jsonl --limit 20
```

## 输入格式（三选一，自动识别）

- JSON 数组：`["shenzhen...", ...]`
- JSON 对象：`{"order": [...], "targets": {...}}`（即 pinyin_targets.json，取 order）
- 纯文本：一行一个拼音
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

try:  # Windows 默认 GBK，输出 ✓ → 重定向到文件会 UnicodeEncodeError
    from _console import setup as _utf8_stdio
    _utf8_stdio()
except Exception:
    pass

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 登记状态词表：前者算活公司，后者算死公司
LIVE_WORDS = ("存续", "在业", "开业", "正常")
DEAD_WORDS = ("注销", "吊销", "迁出", "清算", "停业")
STATUS_WORDS = LIVE_WORDS + DEAD_WORDS

SEARCH_URL = "https://www.tianyancha.com/search?key="


# ---------------------------------------------------------------- 输入 / 输出

def load_input(path):
    """支持 JSON 数组 / pinyin_targets 结构 / 纯文本。"""
    raw = open(path, encoding="utf-8").read().strip()
    if not raw:
        return []
    if raw[0] in "[{":
        d = json.loads(raw)
        if isinstance(d, list):
            return [str(x) for x in d]
        if isinstance(d, dict):
            if isinstance(d.get("order"), list):
                return [str(x) for x in d["order"]]
            if isinstance(d.get("targets"), dict):
                return list(d["targets"])
            return list(d)
        return []
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


def load_done(path):
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("cands") is not None:
                    done.add(r["py"])
            except Exception:
                continue
    return done


def append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 解析

def parse_cands(txt, limit=10):
    """从搜索结果页正文提取候选：名称 + 登记状态 + 法人。

    状态取「名字附近窗口内出现的第一个状态词」——天眼查列表卡片把状态放在
    公司名同一区块，前 60 / 后 220 字符的窗口足够覆盖，且不会串到隔壁卡片
    （隔壁卡片的名字本身就是窗口边界，实测 2026-11-06：300 家无串状态）。
    """
    cands, seen = [], set()
    for m in re.finditer(r"([\u4e00-\u9fa5（）()]{4,40}?(?:有限公司|有限责任公司|股份公司))", txt):
        nm = m.group(1)
        if nm in seen or len(nm) < 6:
            continue
        seen.add(nm)
        lo, hi = max(0, m.start() - 60), min(len(txt), m.end() + 220)
        win = txt[lo:hi].replace("\n", " ")
        st = next((w for w in STATUS_WORDS if w in win), "")
        cands.append({"name": nm, "status": st})
    legals = re.findall(r"法定代表人[：:]\s*([\u4e00-\u9fa5]{2,4})", txt)
    for i, c in enumerate(cands):
        if i < len(legals):
            c["legal"] = legals[i]
    return cands[:limit]


# ---------------------------------------------------------------- 主流程

def notify(msg):
    """撞墙且不自动过验证码时提醒人。

    macOS 走系统通知中心；Windows 走 MessageBeep（osascript 在 Windows 上不存在，
    原先被 except 吞掉后只剩终端响铃，人不在电脑前就完全无感）；其他系统只响铃。
    """
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e",
                            f'display notification "{msg}" with title "天眼查批量" sound name "Glass"'],
                           timeout=10)
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
    print("\a" * 3, flush=True)


def main():
    ap = argparse.ArgumentParser(description="拼音名 → 中文企业名（天眼查）")
    ap.add_argument("--input", required=True, help="拼音清单（json 数组 / pinyin_targets / txt）")
    ap.add_argument("--out", required=True, help="输出 JSONL（可续跑）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 家（探路用）")
    ap.add_argument("--delay", type=float, default=3.5, help="每家间隔秒数（默认 3.5，别调小）")
    ap.add_argument("--max-cands", type=int, default=10, help="每家最多保留候选数")
    ap.add_argument("--profile", default="~/.tyc-profile", help="浏览器持久化目录")
    ap.add_argument("--headless", action="store_true", help="无头模式（风控更严，默认关）")
    ap.add_argument("--auto-captcha", action="store_true",
                    help="撞墙时自动过验证码（需要 agent 在线看图；默认关，改为通知+等人工）")
    ap.add_argument("--verify-wait", type=int, default=1800,
                    help="人工过验证的最长等待秒数（默认 1800）")
    ap.add_argument("--answer-wait", type=int, default=180,
                    help="--auto-captcha 时等 AI 给答案的秒数（默认 180；"
                         "超时即转人工，避免没人看图时干等半小时）")
    ap.add_argument("--max-streak", type=int, default=3,
                    help="连续撞墙 N 次直接停止（默认 3；风控升级时一次一墙，别硬跑）")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺 playwright：pip install -r requirements.txt 后重试")

    ca = None
    if args.auto_captcha:
        try:
            import captcha_agent as ca
        except ImportError:
            sys.exit("--auto-captcha 需要 scripts/captcha_agent.py 在同目录")

    todo = load_input(args.input)
    done = load_done(args.out)
    todo = [p for p in todo if p not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"待查 {len(todo)} 家（已跳过 {len(done)}）", flush=True)
    if not todo:
        print("ALL_DONE", flush=True)
        return

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    profile = os.path.expanduser(args.profile)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile, headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900}, user_agent=UA)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()

        def goto_search(kw):
            pg.goto(SEARCH_URL + urllib.parse.quote(kw),
                    wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(3200)

        def walled():
            if ca is None:
                # 没接验证码模块时，用天眼查的固定文案兜底
                try:
                    t = pg.inner_text("body")
                except Exception:
                    return True
                return ("身份验证" in t) or ("点击按钮开始验证" in t)
            try:
                if ca.wall_present(pg):
                    return True
                return ca.find_widget(pg)[0] is not None
            except Exception:
                return True

        def clear_wall(tag):
            """过墙。返回 True=已通过。"""
            if ca is not None:
                if ca.handle_wall(pg, tag=tag, rounds=3, wait_sec=args.answer_wait):
                    print("VERIFY_OK: 自动通过", flush=True)
                    return True
                print("VERIFY_FALLBACK: 自动失败, 转人工", flush=True)
            notify("验证码需要人工, 请到天眼查窗口点一次")
            for _ in range(max(1, args.verify_wait // 2)):
                pg.wait_for_timeout(2000)
                if not walled():
                    return True
            return False

        streak = 0
        for idx, kw in enumerate(todo, 1):
            goto_search(kw)
            if walled():
                streak += 1
                print(f"WALL: 第 {idx}/{len(todo)} 家撞墙(连续{streak})", flush=True)
                if streak >= args.max_streak:
                    print(f"WALL_FATAL: 连续 {streak} 次撞墙，停止（换 IP / 冷却 12-24h 再来）",
                          flush=True)
                    break
                if not clear_wall(kw[:8]):
                    print("VERIFY_TIMEOUT: 退出", flush=True)
                    break
                streak = 0
                goto_search(kw)
                if walled():
                    append(args.out, {"py": kw, "cands": [], "n": 0, "note": "wall",
                                      "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
                    continue
            txt = pg.inner_text("body")
            cands = parse_cands(txt, limit=args.max_cands)
            append(args.out, {"py": kw, "cands": cands, "n": len(cands),
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            if idx % 10 == 0 or idx <= 3:
                st = ",".join(c.get("status", "") for c in cands[:4])
                print(f"[{idx}/{len(todo)}] {kw[:26]}: {len(cands)} 候选 [{st}]", flush=True)
            time.sleep(args.delay)

        print(f"RUN_END: {len(load_done(args.out))} 家已落盘 → {args.out}", flush=True)
        ctx.close()


if __name__ == "__main__":
    main()
