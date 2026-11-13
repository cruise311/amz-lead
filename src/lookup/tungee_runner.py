#!/usr/bin/env python3
"""
tungee_runner.py — 探迹物流版批量查号确定性执行器(无 AI 在环)。

每批 10 个关键词「企业名称·文本包含任一」OR 筛选 → 批量解锁 → 逐家详情读号:
  1) 优先从页面 XHR 响应里直接提取号码(明文则零 OCR)
  2) 拿不到 → 截详情面板图 → 本地 OCR(ocr_phone.py,Apple Vision/tesseract,免费离线)
  3) 仍拿不到 → 图片存 pending_ocr/,后续人工或 AI 读图
每处理完一家立即落盘 JSONL,中断后重跑自动续(已处理的公司跳过)。

用法:
  python3 tungee_runner.py --login                          # 首次:打开浏览器手动登录(profile 持久化)
  python3 tungee_runner.py --discover batch_001.txt         # 校准:跑一批并导出接口/DOM 线索
  python3 tungee_runner.py --batches leads/batches.json --out leads/ --round 1
  python3 tungee_runner.py --only "深圳嘉南科技有限公司" --out leads/

依赖: pip3 install playwright && python3 -m playwright install chromium
"""
import argparse
import json
import os
import re
import sys
import time
from collections import deque
from datetime import date
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("缺依赖:先运行 bash scripts/setup.sh(或 pip3 install playwright && python3 -m playwright install chromium)")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # Windows 默认 GBK，输出 ✓ → 重定向到文件会 UnicodeEncodeError
    from _console import setup as _utf8_stdio
    _utf8_stdio()
except Exception:
    pass

WORKBENCH = "https://logistics.tungee.com/customer-seeking/advanced-filter/enterprise"
PROFILE_DIR = os.path.expanduser("~/.tungee-profile")

# ==================== CALIBRATION ====================
# 所有易变元素定位集中在这里。页面改版或点击落空时,用 --discover 导出的线索修改本区块即可,
# 不要动其他逻辑。定位语法为 Playwright selector(text= 按 v1 文档记录的按钮文字匹配)。
CALIB = {
    "btn_clear": 'text="清空"',
    "btn_add_cond": 'text="添加条件"',
    "opt_company_name": 'text="企业名称"',
    "btn_filter": 'text="筛选"',
    "btn_collapse": 'text="收起"',
    "opt_unlocked_view": 'text="未解锁企业"',
    "btn_select_all": 'input[type="checkbox"]',   # 表头全选框,discover 模式可确认
    "btn_unlock": 'text="解锁"',
    "btn_confirm": 'text="确定"',
    "btn_reveal": 'text="查看"',      # 打码号行内揭示按钮（v2 曾丢失导致 KeyError）
    "btn_claim": 'text="领取并查看"',
    "btn_claim_confirm": 'text="领取"',
    "btn_close_detail": '[class*="close"]',
    "blocked_marker": "不能查看他人跟进",
    "nocontact_marker": "暂无联系人信息",
    "panel_clip_width": 520,      # 详情面板截图宽度(贴窗口右侧)
    "filter_wait_sec": 4,         # v1 实测安全节奏,不要调快
    "per_company_wait_sec": 2,
    "batch_cooldown_sec": 10,
}

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|0\d{2,3}-?\d{7,8}(?!\d)|400-?\d{3}-?\d{4}(?!\d)")
MASKED_RE = re.compile(r"(?<!\d)1[3-9]\d\*{3,6}\d{3,4}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def enclosing_object(text, pos):
    """找包住 pos 的最小 JSON 对象（按花括号配对，跳过字符串内的括号）。

    比固定字符窗口可靠：一条列表响应里多家公司挨得很近，窗口法会串号。
    """
    start = text.rfind("{", 0, pos + 1)
    while start >= 0:
        depth, i, in_str, esc = 0, start, False, False
        while i < len(text):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    if i > pos:
                        return text[start:i + 1]
                    break
            i += 1
        start = text.rfind("{", 0, start)
    return None


class Sniffer:
    """滚动缓冲:记录探迹域名的 JSON 响应,供读号时反查。"""

    def __init__(self, dump_dir=None):
        self.buf = deque(maxlen=300)
        self.dump_dir = dump_dir
        self._n = 0

    def on_response(self, resp):
        try:
            url = resp.url
            host = urlparse(url).netloc
            if "tungee" not in host:
                return
            ctype = resp.headers.get("content-type", "")
            if "json" not in ctype and "text" not in ctype:
                return
            body = resp.text()
            if not body or len(body) > 500000:
                return
            self.buf.append({"url": url, "text": body})
            if self.dump_dir:
                self._n += 1
                with open(os.path.join(self.dump_dir, f"req_{self._n:04d}.json"), "w", encoding="utf-8") as f:
                    json.dump({"url": url, "body": body[:20000]}, f, ensure_ascii=False)
        except Exception:
            pass

    def scan(self, marker):
        """在最近的响应里找含 marker 的那条，只取「包住该公司名的最小 JSON 对象」提号。

        列表接口一条响应常含多家公司，全文正则会把别家的号记到这家头上（错号比没号贵）。
        切不出对象时退化为 marker 后 1200 字符的窗口，宁可漏号也不配错人。
        """
        marker = marker[:8]
        for item in reversed(self.buf):
            text = item["text"]
            pos = text.find(marker)
            if pos < 0:
                continue
            scope = enclosing_object(text, pos) or text[pos: pos + 1200]
            return {
                "phones": sorted(set(PHONE_RE.findall(scope))),
                "masked": sorted(set(MASKED_RE.findall(scope))),
                "emails": sorted(set(EMAIL_RE.findall(scope))),
                "url": item["url"],
            }
        return None


def click_first(page, sel, timeout=4000, required=True, what=""):
    loc = page.locator(sel).first
    try:
        loc.click(timeout=timeout)
        return True
    except Exception as e:
        if required:
            raise RuntimeError(f"点击失败[{what or sel}]:{e.__class__.__name__}。页面可能改版,跑 --discover 校准。")
        return False


def shot(page, outdir, name):
    path = os.path.join(outdir, "debug", f"{name}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    page.screenshot(path=path, full_page=False)
    return path


def goto_next_page(page):
    """结果列表翻到下一页；没有下一页返回 False。"""
    try:
        nxt = page.locator("li.ant-pagination-next")
        if nxt.count() == 0:
            return False
        if "disabled" in (nxt.first.get_attribute("class") or ""):
            return False
        nxt.first.click()
        page.wait_for_timeout(2500)
        return True
    except Exception:
        return False


def open_batch(page, paste, outdir, step_tag):
    """按 v1 文档的操作序列设置筛选条件并执行。"""
    page.goto(WORKBENCH, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    click_first(page, CALIB["btn_clear"], required=False, what="清空")
    click_first(page, CALIB["btn_add_cond"], what="添加条件")
    click_first(page, CALIB["opt_company_name"], what="企业名称条件项")
    box = page.locator("input:visible").last
    box.click()
    box.type(paste, delay=30)  # v1 实测:直接 type 最稳,合成粘贴事件不带内容
    click_first(page, CALIB["btn_filter"], what="筛选")
    page.wait_for_timeout(CALIB["filter_wait_sec"] * 1000)
    if outdir:
        shot(page, outdir, f"{step_tag}_filtered")


def unlock_hits(page, outdir, step_tag):
    """切「未解锁企业」视图 → 全选 → 解锁(弹窗确认,必要时补一次)。消耗解锁额度。"""
    if not click_first(page, CALIB["opt_unlocked_view"], required=False, what="未解锁企业视图"):
        return 0
    page.wait_for_timeout(1500)
    page.locator(CALIB["btn_select_all"]).first.check(timeout=3000)
    click_first(page, CALIB["btn_unlock"], what="解锁")
    click_first(page, CALIB["btn_confirm"], required=False, what="解锁确认")
    page.wait_for_timeout(2500)
    # v1 文档:有时需两次补漏
    try:
        page.locator(CALIB["btn_select_all"]).first.check(timeout=2000)
        click_first(page, CALIB["btn_unlock"], required=False, what="解锁补漏")
        click_first(page, CALIB["btn_confirm"], required=False, what="解锁补漏确认")
        page.wait_for_timeout(2000)
    except Exception:
        pass
    if outdir:
        shot(page, outdir, f"{step_tag}_unlocked")
    click_first(page, CALIB["btn_collapse"], required=False, what="收起筛选区")
    return 1


def read_one(page, sniffer, full, kw, outdir, idx):
    """打开单家详情并读号:XHR 响应 → OCR 截图 → pending。返回 v1 兼容的记录 dict。"""
    rec = {"full": full, "kw": kw, "phones": [], "emails": [], "masked": [],
           "notes": "", "source": "tungee-script", "ts": date.today().isoformat()}
    try:
        page.get_by_text(full, exact=False).first.click(timeout=4000)
    except Exception:
        rec["status"] = "notfound"
        return rec
    page.wait_for_timeout(CALIB["per_company_wait_sec"] * 1000)

    body = page.locator("body").inner_text(timeout=3000)
    if CALIB["blocked_marker"] in body:
        rec["status"] = "blocked"
        return rec

    hit = sniffer.scan(full)
    if hit:
        rec["phones"], rec["masked"], rec["emails"] = hit["phones"], hit["masked"], hit["emails"]

    if not rec["phones"]:
        # 公海企业:先领取(消耗领取配额,非解锁额度)
        if "领取并查看" in body:
            click_first(page, CALIB["btn_claim"], required=False, what="领取并查看")
            click_first(page, CALIB["btn_claim_confirm"], required=False, what="领取确认")
            page.wait_for_timeout(2000)
            body = page.locator("body").inner_text(timeout=3000)
            hit = sniffer.scan(full)
            if hit:
                rec["phones"], rec["masked"], rec["emails"] = hit["phones"], hit["masked"], hit["emails"]
        # 打码号点「查看」逐个揭示(常一次点完全部)
        if rec["masked"]:
            for _ in range(6):
                if not click_first(page, CALIB["btn_reveal"], required=False, what="查看"):
                    break
                page.wait_for_timeout(800)
            hit = sniffer.scan(full)
            if hit:
                rec["phones"], rec["masked"], rec["emails"] = hit["phones"], rec["masked"], hit["emails"]

    if not rec["phones"]:
        # 兜底:详情面板截两张(顶部+滚动后),本地 OCR 合并(实测:号码行漏识别的主因是滚出视口)
        vp = page.viewport_size
        clip = {"x": max(0, vp["width"] - CALIB["panel_clip_width"]), "y": 0,
                "width": CALIB["panel_clip_width"], "height": vp["height"]}
        pngs = []
        slug = re.sub(r"[^\w\u4e00-\u9fa5]+", "", full)[:40] or f"i{idx:04d}"
        for shot_i, scroll in enumerate((0, 600)):
            if scroll:
                page.mouse.wheel(0, scroll)
                page.wait_for_timeout(700)
            # 文件名带公司名,merge_ocr.py 才能把图对回公司（idx 重跑会变,不可靠）
            png = os.path.join(outdir, "pending_ocr", f"{date.today().isoformat()}_{slug}_{shot_i}.png")
            os.makedirs(os.path.dirname(png), exist_ok=True)
            page.screenshot(path=png, clip=clip)
            pngs.append(png)
        try:
            import ocr_phone
            found = set()
            for png in pngs:
                found |= set(ocr_phone.extract_phones_from_image(png))
            rec["phones"] = sorted(found)
            rec["notes"] = "OCR读号"
        except Exception as e:
            rec["notes"] = f"待读图:{pngs[0]}({e.__class__.__name__})"
            rec["status"] = "pending_ocr"

    if not rec["phones"] and rec["status"] != "pending_ocr":
        rec["status"] = "nocontact" if CALIB["nocontact_marker"] in body else "nocontact"

    # 关详情面板
    click_first(page, CALIB["btn_close_detail"], required=False, what="关闭详情")
    page.wait_for_timeout(600)
    return rec


TERMINAL = {"blocked", "notfound", "nocontact"}


def load_done(path):
    """已处理集合:只认「拿到号」或「终态」的记录。

    pending_ocr / error 不算完成——否则 OCR 补完的号没有回写入口,重跑又跳过,
    这批图就永久丢了(旧版就是这么丢号的)。
    """
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("phones") or rec.get("status") in TERMINAL:
                done.add(rec.get("full"))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="打开浏览器手动登录探迹后回车退出")
    ap.add_argument("--discover", metavar="PASTE_TXT", help="校准模式:传入单批粘贴串 txt")
    ap.add_argument("--batches", help="batches.json 路径")
    ap.add_argument("--only", help="单家快查:公司全称")
    ap.add_argument("--out", default="leads", help="输出目录")
    ap.add_argument("--round", type=int, default=1, dest="rnd")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 批(试跑用)")
    ap.add_argument("--max-pages", type=int, default=3, help="结果列表最多翻几页(默认 3)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    discover_dir = os.path.join(args.out, "discover") if args.discover else None
    if discover_dir:
        os.makedirs(discover_dir, exist_ok=True)
    out_jsonl = os.path.join(args.out, f"round{args.rnd}_tungee.jsonl")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=False,
            viewport={"width": 1680, "height": 950},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        sniffer = Sniffer(dump_dir=discover_dir)
        page.on("response", sniffer.on_response)

        if args.login:
            page.goto(WORKBENCH, wait_until="domcontentloaded")
            print("请在打开的浏览器里完成探迹登录(短信验证码最稳)。检测到进入工作台后自动保存退出,最长等 10 分钟。")
            time.sleep(15)  # 等未登录重定向完成,避免登录页误判
            deadline = time.time() + 600
            logged_in, seen = False, 0
            while time.time() < deadline:
                time.sleep(5)
                try:
                    # 工作台独有元素「添加条件」连续两次出现才算登录成功
                    if page.locator(CALIB["btn_add_cond"]).count() > 0:
                        seen += 1
                        if seen >= 2:
                            logged_in = True
                            break
                    else:
                        seen = 0
                except Exception:
                    pass  # 页面导航中,继续等
            print("登录成功,登录态已持久化到 ~/.tungee-profile" if logged_in
                  else "10 分钟未见登录成功,退出(可重跑 --login)")
            ctx.close()
            return

        # 组装任务
        tasks = []
        if args.discover:
            paste = open(args.discover, encoding="utf-8").read().strip()
            kws = [x for x in paste.split(",") if x]
            tasks.append({"batch": 0, "paste": paste,
                          "kws": [{"kw": k[:10], "full": k} for k in kws]})
        elif args.only:
            from build_batches import make_keyword, is_skippable
            if is_skippable(args.only):
                sys.exit("该名称疑似英文/拼音主体,无法直查,请先做中文名还原。")
            tasks.append({"batch": 0, "paste": make_keyword(args.only),
                          "kws": [{"kw": make_keyword(args.only), "full": args.only}]})
        else:
            batches = json.load(open(args.batches, encoding="utf-8"))
            for b in batches:
                b["paste"] = ",".join(k["kw"] for k in b["kws"])
            tasks = batches[:args.limit] if args.limit else batches
        # 续跑:跳过已处理公司
        done = load_done(out_jsonl)

        for b in tasks:
            tag = f"batch{b['batch']:03d}"
            todo = [k for k in b["kws"] if k["full"] not in done]
            print(f"[{tag}] {len(b['kws'])} 家,续跑跳过 {len(b['kws']) - len(todo)} 家")
            if not todo:
                continue
            try:
                open_batch(page, b["paste"], args.out, tag)
                unlock_hits(page, args.out, tag)
            except RuntimeError as e:
                print(f"[{tag}] 筛选/解锁失败:{e}")
                shot(page, args.out, f"{tag}_error")
                continue
            # 结果列表可能分页：逐页找出本批目标再读号，避免第 2 页起被误记 notfound
            pending = {k["full"]: k for k in todo}
            page_no = 0
            while pending and page_no < args.max_pages:
                page_no += 1
                try:
                    rows_text = page.evaluate(
                        "()=>[...document.querySelectorAll('tr.ant-table-row')].map(tr=>tr.innerText||'').join('\\n')")
                except Exception:
                    rows_text = ""
                on_page = [k for full, k in pending.items() if full in rows_text]
                print(f"  -- 第 {page_no} 页，本页目标 {len(on_page)} 家")
                for i, k in enumerate(on_page):
                    rec = read_one(page, sniffer, k["full"], k["kw"], args.out, i)
                    with open(out_jsonl, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    state = rec.get("status") or (f"got {len(rec['phones'])} 号" + (f"(打码{len(rec['masked'])})" if rec["masked"] else ""))
                    print(f"  {k['full'][:20]:<22} → {state}")
                    if rec.get("status") == "pending_ocr":
                        print(f"    待读图:{rec['notes']}")
                    pending.pop(k["full"], None)
                if not pending or not goto_next_page(page):
                    break
            for full in pending:
                rec = {"full": full, "kw": pending[full]["kw"], "phones": [], "emails": [], "masked": [],
                       "notes": f"翻了 {args.max_pages} 页仍未出现", "source": "tungee-script",
                       "ts": date.today().isoformat(), "status": "notfound"}
                with open(out_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"  {full[:20]:<22} → 翻页后仍未找到")
            time.sleep(CALIB["batch_cooldown_sec"])

        ctx.close()
    print(f"完成 → {out_jsonl}")


if __name__ == "__main__":
    main()
