#!/usr/bin/env python3
"""tungee_unlock_api.py — 探迹接口级解锁快路 (2026-11-03 实战验证版)

与 tungee_runner.py (UI 勾选+OCR) 的区别:
  - 解锁走页面内 fetch PUT /cgi/logistics-common/api/lead/unlock (stat=1 精确确认, 不靠 OCR)
  - 读号走解锁列表页 UI 明文 (无需截图/OCR)
  - 150 家实测: 77 家解锁 77 成功、67 个明文号, 全程 ~25 分钟, 零 AI 消耗

用法:
  python3 scripts/tungee_unlock_api.py --batches leads/batches.json --out leads/ --round 1
  python3 scripts/tungee_unlock_api.py --names "广州嘉俊贸易有限公司,义乌市序娅饰品有限公司" --out leads/ --round 1
  首次登录: 先跑 tungee_runner.py --login 或打开可见窗口登录一次
输出: leads/round{N}_tungee_api.jsonl (字段与 runner JSONL 兼容, backfill_excel.py 直接消费)

关键坑 (全部内置, 改参数前先读):
  1. 搜索关键词用「城市+字号」短词, 全名带行业词 0 结果率高; 短词命中的外地同名企业用
     「城市前缀 + 剥行业词字号相等」双校验拦截
  2. 解锁列表翻页必须点 li.ant-pagination-item[title='N'] + wait_for_function 页码切换,
     next 按钮会失灵(内容不刷新)
  3. fetch unlock 在任意已登录 tungee 页面 context 执行即带凭证; 前 3 家先验证, stat!=1 立即停
  4. 企业详情页路由未破解(enterprise/{id} 全 404), 不要再试; 明文号在 unlock-list/enterprise
"""
import argparse, json, os, re, sys, time

from playwright.sync_api import sync_playwright

try:  # Windows 默认 GBK，输出 ✓ → 重定向到文件会 UnicodeEncodeError
    from _console import setup as _utf8_stdio
    _utf8_stdio()
except Exception:
    pass

PROFILE_DIR = os.path.expanduser(os.environ.get("TUNGEE_PROFILE", "~/.tungee-profile"))
COOKIE_FILE = os.path.join(PROFILE_DIR, "cookies_persist.json")
L_HOME = "https://logistics.tungee.com/home"
UNLOCK_LIST = "https://logistics.tungee.com/unlock-list/enterprise"

INDUSTRY = (r'(网络科技|电子商务|电子科技|信息科技|信息技术|科技|进出口|工贸|商贸|实业|贸易|网络|'
            r'文化传媒|广告|商务咨询|建筑设备租赁|日用百货|首饰|珠宝|箱包|皮具|五金|服饰|服装|'
            r'办公用品|机械设备|劳务|信息咨询|饰品有限公司)')

JS_TEXT_HITS = r"""(txt) => {
  const hits=[];
  const key = txt.replace(/[\s\u3000]+/g,"");
  document.querySelectorAll('div,span,li,a,button').forEach(el=>{
    const norm=(el.innerText||"").replace(/[\s\u3000]+/g,"");
    if(norm!==key) return;
    const r=el.getBoundingClientRect();
    if(r.width>0&&r.height>0) hits.push({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),a:Math.round(r.width*r.height)});
  });
  hits.sort((p,q)=>p.a-q.a);
  return hits;
}"""

JS_SELECTS = """() => Array.from(document.querySelectorAll('.ant-select')).filter(e=>e.offsetParent)
  .map(e=>{const r=e.getBoundingClientRect();return {txt:(e.innerText||'').trim().slice(0,20),x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})"""

FETCH_UNLOCK = """
async (eid) => {
  const fd = new FormData();
  fd.append('source', '19');
  fd.append('enterprise_id', eid);
  fd.append('entity_type', 'enterprise');
  const r = await fetch('/cgi/logistics-common/api/lead/unlock', {method: 'PUT', body: fd, credentials: 'include'});
  return {status: r.status, body: (await r.text()).slice(0, 200)};
}
"""

CITY_CN = ("内蒙古自治区", "广西壮族自治区", "新疆维吾尔自治区", "宁夏回族自治区", "西藏自治区",
           "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "宁波", "温州", "嘉兴", "湖州",
           "绍兴", "金华", "衢州", "舟山", "台州", "义乌", "诸暨", "慈溪", "余姚", "乐清",
           "南京", "苏州", "无锡", "常州", "南通", "徐州", "扬州", "镇江", "泰州", "盐城",
           "淮安", "连云港", "宿迁", "昆山", "成都", "绵阳", "德阳", "宜宾", "泸州", "南充",
           "福州", "厦门", "泉州", "漳州", "莆田", "三明", "南平", "龙岩", "宁德", "晋江",
           "济南", "青岛", "淄博", "烟台", "潍坊", "临沂", "济宁", "泰安", "威海", "日照",
           "德州", "聊城", "滨州", "菏泽", "东营", "枣庄", "武汉", "黄石", "十堰", "宜昌",
           "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "长沙", "株洲",
           "湘潭", "衡阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底",
           "邵阳", "石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德",
           "沧州", "廊坊", "衡水", "太原", "西安", "兰州", "西宁", "银川", "海口", "三亚",
           "南宁", "柳州", "桂林", "贵阳", "昆明", "南昌", "赣州", "九江", "合肥", "芜湖",
           "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州",
           "六安", "亳州", "池州", "宣城", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州",
           "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛", "长春", "四平", "辽源",
           "通化", "白山", "松原", "白城", "哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山",
           "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化", "郑州", "开封",
           "洛阳", "安阳", "新乡", "南阳", "许昌", "东莞", "中山", "珠海", "汕头", "佛山",
           "韶关", "湛江", "肇庆", "江门", "茂名", "惠州", "梅州", "汕尾", "河源", "阳江",
           "清远", "潮州", "揭阳", "云浮", "香港", "澳门", "台湾")

PHONE_RE = re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)')
MASKED_RE = re.compile(r'(1[3-9]\d)\*{2,6}(\d{3,4})')

# 运行模式（--mode）：
#   headless 真静默，不弹窗不抢焦点（默认）
#   silent   离屏窗口：浏览器真实存在但放在屏幕外，指纹最接近真人，风控更宽松
#   visible  前台窗口，仅校准时用
MODE = "headless"
HEADLESS = True
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


def apply_mode(mode):
    global MODE, HEADLESS, LAUNCH_ARGS
    MODE = mode
    base = ["--disable-blink-features=AutomationControlled"]
    if mode == "headless":
        HEADLESS, LAUNCH_ARGS = True, base
    elif mode == "silent":
        HEADLESS = False
        LAUNCH_ARGS = base + ["--window-position=-32000,-32000", "--window-size=1280,900"]
    else:
        HEADLESS, LAUNCH_ARGS = False, base


def click_text(pg, txt, max_area=20000, wait=1200):
    hits = [h for h in pg.evaluate(JS_TEXT_HITS, txt) if h["a"] < max_area]
    if not hits:
        return False
    pg.mouse.click(hits[0]["x"], hits[0]["y"])
    pg.wait_for_timeout(wait)
    return True


def short_kw(name):
    """公司全名 -> 纯字号（不带城市、不带行业、不带组织形式）

    实测（2026-11-03）：探迹的企业名称筛选走分词索引，
      「辰万」→ 50 条命中；「广州市辰万」/「广州市辰万贸易」/全名 → 0 条。
    跨词短语不匹配，所以关键词必须是单个分词单元，通常就是字号。
    """
    n = re.sub(r'（个体工商户）|\(个体工商户\)', '', name)
    n = re.sub(r'(有限责任公司|有限公司|经营部|商行|商会)$', '', n)
    prev = None
    while prev != n:
        prev = n
        n = re.sub(INDUSTRY + r'$', '', n)
    for city in sorted(CITY_CN, key=len, reverse=True):
        if n.startswith(city):
            n = n[len(city):]
            break
    n = re.sub(r'^(?:省|市|县|区|盟|旗)', '', n)
    return n or name


def split_name(s):
    """公司名 -> (城市+字号, 行业词)

    例：广州倍升商贸有限公司 -> ('广州倍升', '商贸')
        广州倍升网络科技有限公司 -> ('广州倍升', '网络科技')
    行业词必须一起比：只比「城市+字号」会把「倍升商贸」和「倍升网络科技」当成同一家，
    实测 3 家测试全部命中错误主体——错号比没号贵。
    """
    n = re.sub(r'(有限责任公司|有限公司|经营部|商行|商会)$', '', s)
    industry = ""
    prev = None
    while prev != n:
        prev = n
        m = re.search(INDUSTRY + r'$', n)
        if m:
            industry = m.group(1) + industry
            n = n[:m.start()]
    n = re.sub(r'^(.{2,4}?)(自治区|省|市|县|区)', r'\1', n)
    return n, industry


def brandz(s):
    """公司名 -> 城市+字号（保留兼容旧调用）"""
    return split_name(s)[0]


def dismiss_ant_modal(pg):
    """关掉 ant-design 模态弹窗(登录后首访公告/通知类, .ant-modal-wrap 拦截一切点击,
    2026-11-05 实测卡死 open_workbench; swal 处理器不覆盖这种)"""
    try:
        if pg.locator(".ant-modal-wrap:visible").count() == 0:
            return True
        for txt in ("我知道了", "知道了", "确定", "不再提示", "稍后再说", "关闭"):
            try:
                pg.get_by_text(txt, exact=True).first.click(timeout=1200)
                pg.wait_for_timeout(600)
                if pg.locator(".ant-modal-wrap:visible").count() == 0:
                    return True
            except Exception:
                continue
        # 兜底: 移除遮罩与弹窗 DOM, 恢复 body 滚动
        pg.evaluate("""() => {
            document.querySelectorAll('.ant-modal-wrap, .ant-modal-root, .ant-modal-mask')
                .forEach(e => e.remove());
            document.body.classList.remove('ant-scrolling-effect');
            document.body.style.removeProperty('overflow');
            document.body.style.removeProperty('padding-right');
        }""")
        pg.wait_for_timeout(400)
        return pg.locator(".ant-modal-wrap:visible").count() == 0
    except Exception:
        return False


def open_workbench(pg):
    """进工作台。鉴权失败弹窗会挡住「高级筛选」，必须先点掉再点；
    连续三次进不去说明登录态真的失效，直接报错，别在坏页面上跑出一堆假 not_found。"""
    for attempt in range(3):
        pg.goto(L_HOME, wait_until="domcontentloaded")
        pg.wait_for_timeout(3000)
        try:
            pg.get_by_text("我知道了", exact=True).first.click(timeout=1500)
            pg.wait_for_timeout(800)
        except Exception:
            pass
        dismiss_ant_modal(pg)
        loc = pg.get_by_text("高级筛选", exact=True)
        if loc.count() == 0:
            pg.reload(wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            continue
        loc.first.click(timeout=8000)
        pg.wait_for_timeout(5000)
        try:
            pg.get_by_text("我知道了", exact=True).first.click(timeout=1200)
        except Exception:
            pass
        return True
    raise RuntimeError("进不去工作台（鉴权失败弹窗反复出现），请先手动登录一次再跑")


def set_keywords(pg, paste):
    click_text(pg, "清空", wait=2500)
    if not click_text(pg, "添加条件", wait=1500):
        return False
    sels = [s for s in pg.evaluate(JS_SELECTS) if "请选择" in s["txt"]]
    if not sels:
        return False
    pg.mouse.click(sels[-1]["x"], sels[-1]["y"])
    pg.wait_for_timeout(1500)
    if not click_text(pg, "企业名称", max_area=8000, wait=1800):
        return False
    fields = [s for s in pg.evaluate(JS_SELECTS) if "请输入关键词" in s["txt"]]
    if not fields:
        return False
    box = max(fields, key=lambda f: f["y"])
    pg.mouse.click(box["x"], box["y"])
    pg.wait_for_timeout(400)
    pg.keyboard.type(paste, delay=20)
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(1500)
    return True


PAGE_CAP = 20  # advance-search 单次返回上限的经验值，达到即认为可能截断


def dismiss_popup(pg):
    """关掉 swal 弹窗(SweetAlert, 会拦截全部点击, 2026-11-04 实测是搜索未跑通真凶)"""
    try:
        ov = pg.locator(".swal-overlay--show-modal")
        if ov.count() == 0:
            return True
        for sel in (".swal-button--confirm", ".swal-button", ".swal-button--cancel",
                    "button", ".swal-close"):
            btn = ov.first.locator(sel)
            if btn.count() > 0:
                try:
                    btn.first.click(timeout=2000)
                    pg.wait_for_timeout(800)
                    if pg.locator(".swal-overlay--show-modal").count() == 0:
                        return True
                except Exception:
                    continue
        # 兜底: 直接移除覆盖层 DOM
        pg.evaluate(
            "() => document.querySelectorAll('.swal-overlay').forEach(e => e.remove())")
        pg.wait_for_timeout(400)
        return pg.locator(".swal-overlay--show-modal").count() == 0
    except Exception:
        return False


def search_batch(pg, kws, max_pages=4):
    """一批关键词搜索，自动翻页收集。返回 (data, ok, truncated)

    ok=False 表示操作没跑通（按钮点空/接口没响应），调用方记 search_error 而不是 not_found。
    实测单页上限 50 条，同字号在全国可能上百家，必须翻页，否则目标永远在第二页之后。
    """
    if not set_keywords(pg, "，".join(kws)):
        if dismiss_popup(pg):
            if not set_keywords(pg, "，".join(kws)):
                return [], False, False
        else:
            return [], False, False
    box = {"data": None}

    def on_resp(resp):
        try:
            if resp.url.split("?")[0].endswith("/cgi/logistics/api/advance-search") and resp.request.method == "POST":
                box["data"] = resp.json()
        except Exception:
            pass

    pg.on("response", on_resp)
    clicked = False
    data = []
    try:
        clicked = click_text(pg, "筛选", wait=7000)
        pg.wait_for_timeout(2500)
        if not clicked or box["data"] is None:
            # 弹窗间歇弹出会拦截点击: 清掉后重试本步骤
            if dismiss_popup(pg):
                clicked = click_text(pg, "筛选", wait=7000)
                pg.wait_for_timeout(2500)
        if box["data"] is None:
            # 弹窗拦截/节流自愈：清弹窗 → 回工作台重置 → 重试一次
            dismiss_popup(pg)
            try:
                open_workbench(pg)
                pg.wait_for_timeout(2500)
                if not set_keywords(pg, "，".join(kws)):
                    return [], False, False
                clicked = click_text(pg, "筛选", wait=7000)
                pg.wait_for_timeout(3000)
            except Exception:
                pass
            if box["data"] is None:
                return [], False, False
        data = (box["data"] or {}).get("data") or []
        seen = {d.get("_id") for d in data}
        for _ in range(max_pages - 1):
            if len(data) < PAGE_CAP:
                break
            nxt = pg.locator("li.ant-pagination-next")
            if nxt.count() == 0 or "disabled" in (nxt.first.get_attribute("class") or ""):
                break
            box["data"] = None
            nxt.first.click()
            pg.wait_for_timeout(2200)
            more = (box["data"] or {}).get("data") or []
            if not more:
                break
            data += [d for d in more if d.get("_id") not in seen]
            seen.update(d.get("_id") for d in more)
    finally:
        pg.remove_listener("response", on_resp)
    if not clicked:
        return [], False, False
    return data, True, len(data) >= PAGE_CAP * max_pages


def norm_name(s):
    return re.sub(r"[\s（）()]+", "", s or "")


def row_company(r):
    """从一行表格文本里取出「公司名」单元格（精确匹配用，避免子串误配）"""
    for cell in r.split(" | "):
        c = cell.strip()
        if re.search(r"(公司|商行|经营部|中心|厂|店|合作社)$", c) and len(c) >= 6:
            return norm_name(c)
    return ""


def absorb(r, rec):
    """从一行里吸收全部手机号 + 打码号 + 联系人姓名（只取第一个会白丢号）"""
    phones = list(dict.fromkeys(PHONE_RE.findall(r)))
    if phones:
        rec["phones"] = list(dict.fromkeys((rec.get("phones") or []) + phones))
        rec.setdefault("notes", []).append("探迹解锁")
        m = re.search(r"(1[3-9]\d{9})\s*([\u4e00-\u9fa5]{1,3}\*{0,2})", r)
        if m and not rec.get("contact"):
            rec["contact"] = m.group(2)
    masked = [a + "****" + b for a, b in MASKED_RE.findall(r)]
    if masked and not rec.get("phones"):
        rec["masked"] = list(dict.fromkeys(masked))


def grab_unlock_list(pg, want, timeout_pages=20):
    """解锁列表页翻页抓明文号. want: {公司全名: rec dict}. 原地更新 rec"""
    pg.goto(UNLOCK_LIST, wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_timeout(5000)
    remain = lambda: {k: v for k, v in want.items() if not v.get("phones") and not v.get("masked")}
    by_name = {norm_name(k): v for k, v in want.items()}

    def sweep(rows):
        for r in rows:
            key = row_company(r)
            rec = by_name.get(key)
            if rec and not rec.get("phones"):
                absorb(r, rec)

    for page_no in range(1, timeout_pages + 1):
        sweep(pg.evaluate("()=>[...document.querySelectorAll('tr.ant-table-row')].map(tr=>(tr.innerText||'').replace(/\\n/g,' | '))"))
        if not remain():
            return True
        nxt = pg.query_selector(f"li.ant-pagination-item[title='{page_no + 1}']")
        if not nxt:
            break
        nxt.click()
        try:
            pg.wait_for_function(
                f"()=>{{const e=document.querySelector('.ant-pagination-item.active');return e&&e.title==='{page_no + 1}'}}",
                timeout=8000)
        except Exception:
            pass
        pg.wait_for_timeout(2500)

    # 兜底: 解锁列表搜索框逐家搜（限定企业名称输入框，别误填筛选区）
    for nm, rec in list(remain().items()):
        box = pg.query_selector('input[placeholder*="企业名称"]') or pg.query_selector('input[placeholder*="请输入"]')
        if not box:
            break
        box.fill("")
        box.type(nm[:12], delay=25)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(3200)
        sweep(pg.evaluate("()=>[...document.querySelectorAll('tr.ant-table-row')].map(tr=>(tr.innerText||'').replace(/\\n/g,' | '))"))
    return not remain()


def pick(data, nm):
    """在一批搜索结果里给 nm 选主体。返回 (cand, evidence, ambiguous_name)

    exact  = 名称归一后完全相等
    strong = 城市+字号 与 行业词 都相等
    其余   = ambiguous（同字号不同行业，极可能是另一家公司，不消耗额度）
    """
    key = re.sub(r"[\s（）()]+", "", nm)
    for d in data:
        if re.sub(r"[\s（）()]+", "", d.get("name") or "") == key:
            return d, "exact", ""
    bz, ind = split_name(nm)
    city = nm[:2]
    ambiguous = ""
    for cand in data:
        cn = cand.get("name") or ""
        if not cn.startswith(city):
            continue
        cbz, cind = split_name(cn)
        if cbz != bz:
            continue
        if cind == ind:
            return cand, "strong", ""
        ambiguous = ambiguous or cn
    return None, "", ambiguous


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches")
    ap.add_argument("--names", help="逗号分隔公司全名")
    ap.add_argument("--out", required=True)
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--mode", choices=["headless", "silent", "visible"], default="headless",
                    help="headless=真静默(默认) / silent=离屏窗口(防风控) / visible=前台(校准)")
    ap.add_argument("--search-only", action="store_true", help="只搜索拿 eid，不解锁（不消耗额度）")
    ap.add_argument("--retry-failed", action="store_true", help="重试上次 unlock_fail 的公司（额度恢复后用）")
    ap.add_argument("--headless", action="store_true", help="兼容旧参数，等价 --mode headless")
    args = ap.parse_args()
    apply_mode("headless" if args.headless else args.mode)

    os.makedirs(args.out, exist_ok=True)
    out_jsonl = os.path.join(args.out, f"round{args.round}_tungee_api.jsonl")

    # 名单
    names = []
    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
    elif args.batches:
        for b in json.load(open(args.batches, encoding="utf-8")):
            for k in b.get("kws", []):
                if k:
                    names.append(k)
    # 断点续跑
    recs = {}
    if os.path.exists(out_jsonl):
        for line in open(out_jsonl, encoding="utf-8"):
            try:
                r = json.loads(line)
                recs[r["full"]] = r
            except Exception:
                pass
    for nm in names:
        recs.setdefault(nm, {"full": nm, "kw": short_kw(nm), "phones": [], "masked": [],
                             "emails": [], "addr": "", "brief": "", "contact": "",
                             "status": "pending", "notes": []})

    todo = [nm for nm in names
            if recs[nm]["status"] in ("pending", "searched", "search_error", "search_truncated")
            and not recs[nm].get("eid")]
    print(f"[API 快路] 模式={MODE} 名单 {len(names)}, 待搜索 {len(todo)}", flush=True)

    def save():
        # 原子写：JSONL 是唯一事实源，进程被杀时不能留半截文件
        tmp = out_jsonl + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for nm in names:
                f.write(json.dumps(recs[nm], ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out_jsonl)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=HEADLESS,
            viewport={"width": 1680, "height": 950},
            args=LAUNCH_ARGS)
        if os.path.exists(COOKIE_FILE):
            ck = json.load(open(COOKIE_FILE))
            for c in ck:
                c["expires"] = time.time() + 86400 * 30
            ctx.add_cookies(ck)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()

        # ---- 阶段1: 分批搜索拿 eid ----
        open_workbench(pg)
        RETRYABLE = ("pending", "searched", "search_error", "search_truncated")
        items = [nm for nm in todo if recs[nm]["status"] in RETRYABLE]
        # 一家一搜：探迹的企业名称筛选是分词索引，只有单个分词（字号）能命中，
        # 「城市+字号」和全名都返回 0；同字号全国几十家，靠翻页找目标。
        print(f"[搜索] {len(items)} 家，逐家按字号搜", flush=True)
        for n, nm in enumerate(items, 1):
            kw = short_kw(nm)
            # 翻页后筛选面板状态残留，下一家必失败(2026-11-04 实测复现)
            # → 每家搜索前都回工作台重置页面
            if n > 1:
                try:
                    open_workbench(pg)
                    pg.wait_for_timeout(2500)
                except Exception:
                    pass
            data, ok, truncated = search_batch(pg, [kw], max_pages=5)
            if not ok:
                rec = recs[nm]
                rec["tries"] = rec.get("tries", 0) + 1
                rec["status"] = "search_failed" if rec["tries"] >= 3 else "search_error"
                print(f"  {n}/{len(items)} {kw:<8} 搜索未跑通", flush=True)
                continue
            d, evidence, ambiguous = pick(data, nm)
            rec = recs[nm]
            if d:
                rec.update({"eid": d["_id"], "company_found": d["name"], "status": "got_eid",
                            "match_evidence": evidence,
                            "brief": f"成立 {d.get('foundTime', '')} "
                                     f"{d.get('operStatus_v2', {}).get('first', '')}".strip()})
                print(f"  {n}/{len(items)} ✓ {kw:<8} → {d['name'][:20]} ({evidence}, 候选{len(data)})", flush=True)
            elif ambiguous:
                rec.update({"status": "ambiguous", "ambiguous_with": ambiguous})
                print(f"  {n}/{len(items)} ? {kw:<8} 同字号异行业: {ambiguous[:20]}", flush=True)
            else:
                rec["status"] = "search_truncated" if truncated else "searched"
                print(f"  {n}/{len(items)} × {kw:<8} 候选{len(data)} 未命中", flush=True)
            if n % 5 == 0:
                save()
            time.sleep(3)
        save()

        # ---- 阶段1b: 单家补搜（专治同字号不同行业、以及 20 条截断）----
        retry = [nm for nm in names
                 if not recs[nm].get("eid")
                 and recs[nm]["status"] in ("ambiguous", "search_truncated", "searched")]
        if retry:
            print(f"[单家补搜] {len(retry)} 家", flush=True)
            for n, nm in enumerate(retry, 1):
                kw = short_kw(nm)
                data, ok, _ = search_batch(pg, [kw], max_pages=5)
                if not ok:
                    print(f"  {n}/{len(retry)} {nm[:14]} 搜索失败", flush=True)
                    continue
                d, evidence, ambiguous = pick(data, nm)
                if d:
                    recs[nm].update({"eid": d["_id"], "company_found": d["name"],
                                     "status": "got_eid", "match_evidence": evidence})
                    print(f"  {n}/{len(retry)} ✓ {nm[:14]} → {d['name'][:18]} ({evidence})", flush=True)
                elif ambiguous:
                    recs[nm].update({"status": "ambiguous", "ambiguous_with": ambiguous})
                else:
                    recs[nm]["status"] = "searched"
                if n % 5 == 0:
                    save()
                time.sleep(1.2)
            save()

        # ---- 阶段2: fetch unlock (前 3 家验证额度) ----
        if args.search_only:
            save()
            found = sum(1 for r in recs.values() if r.get("eid"))
            print(f"[仅搜索] 命中 eid {found}/{len(names)}；未解锁、未消耗额度", flush=True)
            ctx.close()
            return
        want_status = {"got_eid"} | ({"unlock_fail"} if args.retry_failed else set())
        to_unlock = [nm for nm in names if recs[nm].get("eid") and recs[nm]["status"] in want_status]
        print(f"[解锁] {len(to_unlock)} 家", flush=True)
        fail_streak = 0
        for n, nm in enumerate(to_unlock, 1):
            try:
                r = pg.evaluate(FETCH_UNLOCK, recs[nm]["eid"])
                body = json.loads(r["body"]) if r["body"].startswith("{") else {}
                ok = body.get("stat") == 1
                recs[nm]["status"] = "unlocked" if ok else "unlock_fail"
                if not ok:
                    fail_streak += 1
                    print(f"  {n}/{len(to_unlock)} {nm[:16]} stat={body.get('stat')} {r['body'][:60]}", flush=True)
                    if fail_streak >= 3:
                        print("  !! 连续 3 家失败, 疑似额度耗尽, 停止解锁", flush=True)
                        break
                else:
                    fail_streak = 0
            except Exception as e:
                recs[nm]["status"] = "unlock_fail"
                print(f"  {n} ERR {str(e)[:60]}", flush=True)
            if n % 10 == 0:
                save()
            time.sleep(3)
        save()

        # ---- 额度账本：跑完能对账，不用猜烧了多少 ----
        try:
            ok_n = sum(1 for nm in to_unlock if recs[nm]["status"] == "unlocked")
            fail_n = len(to_unlock) - ok_n
            ledger = os.path.join(args.out, "quota_ledger.jsonl")
            with open(ledger, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "round": args.round,
                                    "requested": len(to_unlock), "unlocked": ok_n,
                                    "failed": fail_n}, ensure_ascii=False) + "\n")
            total = 0
            for line in open(ledger, encoding="utf-8"):
                total += json.loads(line).get("unlocked", 0)
            print(f"[额度] 本轮解锁 {ok_n} 家（失败 {fail_n}），累计已用 {total} 条", flush=True)
        except Exception as e:
            print(f"[额度] 账本写入失败：{type(e).__name__}", flush=True)

        # ---- 阶段3: 解锁列表抓明文号 ----
        want = {nm: recs[nm] for nm in names if recs[nm]["status"] == "unlocked"}
        if want:
            print(f"[抓号] 解锁列表 {len(want)} 家", flush=True)
            grab_unlock_list(pg, want)
            for nm, rec in want.items():
                if rec.get("phones"):
                    rec["status"] = "done"
                elif rec.get("masked"):
                    rec["status"] = "masked"
                else:
                    rec["status"] = "no_contact"  # 解锁成功但探迹无联系人(实测 total=0)
        for nm in names:
            if recs[nm]["status"] == "searched":
                recs[nm]["status"] = "not_found"  # 探迹未收录
        save()

        ph = sum(1 for r in recs.values() if r.get("phones"))
        st = {}
        for r in recs.values():
            st[r["status"]] = st.get(r["status"], 0) + 1
        print(f"\n[完成] {dict(sorted(st.items()))} | 明文手机 {ph}", flush=True)
        print(f"输出: {out_jsonl} (backfill_excel.py 直接消费)", flush=True)
        ctx.close()


if __name__ == "__main__":
    main()
