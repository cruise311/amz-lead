# -*- coding: utf-8 -*-
"""captcha_agent.py —— 极验 v4 点选题「AI 在环」自愈

适用：天眼查 / 企查查等站点的 geetest v4 图标点选题（captcha_type=icon）。

## 为什么不复刻协议

2026-11-05 实测：gt4solver 那套 load → 生成 w → verify 协议层能跑通（官方 demo
端到端成功），但识别层对天眼查的艺术字完全失灵（模型只训过正楷）。
2026-11-06 换思路：**真实浏览器里控件自己会算 w，只要像人一样点对位置即可**。
唯一缺的是「点哪几个」——这一步交给 agent 看图决定。实测真实验证码
4 次尝试通过 2 次（准确率 50%）。

## 协议（脚本 ↔ agent）

1. 脚本截图验证码控件 → `captcha_dataset/<id>.png`
2. 写 `<id>.req.json`（图片尺寸、控件框、URL），表示「等你给答案」
3. agent 读图，写 `<id>.ans.json`：`{"points": [[x,y], ...], "submit": true}`
   - 坐标系 = **截图像素**（左上角原点），数组顺序 = 点击顺序
4. 脚本换算成页面坐标 → 拟人轨迹点击 → 点 `.geetest_submit` 提交 → 校验
5. 成功把 (图, 答案) 追加进 `captcha_dataset/index.jsonl`，供以后做 hash 字典

## 关键坑（都踩过了）

- 提交键是 `div.geetest_submit`。写成 `.geetest_commit` / `button.geetest_btn` 时
  答案对了也不会提交，表现为「控件一直在、像答案错了」——2026-11-06 半天卡在这。
- 题面规则是**按右上角箭头方向的顺序**点击主图中朝向相同的物体；主图物体经过
  旋转/变色/描边，所以「同一个字/同一个图标」的直线匹配不可靠。
- 鸟类、蝴蝶、乌龟这类「头在哪看不清」的图标准确率明显低于飞机/鱼/汽车，
  失败就重试，3 轮全错概率约 12%，此时应退回人工而不是硬刚。

## 环境变量

- `CAPTCHA_DIR`：截图与答案目录，默认 `scripts/captcha_dataset`

## 用法

```python
import captcha_agent as ca
if ca.wall_present(pg):
    ok = ca.handle_wall(pg, tag="shenzhen", rounds=3)
```
"""
import json
import os
import random
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CAP_DIR = os.environ.get("CAPTCHA_DIR") or os.path.join(HERE, "captcha_dataset")
os.makedirs(CAP_DIR, exist_ok=True)

# 控件选择器：从外到内，取第一个可见且够大的
SELS = [
    ".geetest_panel",
    "[class*='geetest_panel']",
    ".geetest_box",
    "[class*='geetest_holder']",
    ".geetest_window",
    "[class*='geetest']",
]

# 撞墙判定文案（各站略有差异，可用 add_wall_marks 追加）
WALL_MARKS = ("身份验证", "点击按钮开始验证", "请在下图依次点击", "请完成下方验证")
# 明确失败文案：命中就提前退出本轮，不用空等 16 秒
FAIL_MARKS = ("验证失败", "请重新验证", "验证已过期", "请重试")


def add_wall_marks(*marks):
    """站点文案不同时追加判定词。"""
    global WALL_MARKS
    WALL_MARKS = tuple(WALL_MARKS) + tuple(m for m in marks if m not in WALL_MARKS)


def _png_size(path):
    """读 PNG 头拿宽高（不依赖 PIL）。"""
    with open(path, "rb") as f:
        head = f.read(33)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    w = int.from_bytes(head[16:20], "big")
    h = int.from_bytes(head[20:24], "big")
    return w, h


def _body_text(pg):
    try:
        return pg.inner_text("body")
    except Exception:
        return ""


def wall_present(pg):
    t = _body_text(pg)
    if not t:
        return True          # 拿不到正文按「还在墙上」处理，宁可重试
    return any(m in t for m in WALL_MARKS)


def failed(pg):
    t = _body_text(pg)
    return any(m in t for m in FAIL_MARKS)


def expand_widget(pg):
    """先点「点击按钮开始验证」，把弹层拉出来。"""
    for sel in (".geetest_btn_click", ".geetest_btn"):
        try:
            el = pg.query_selector(sel)
            if el and el.is_visible():
                el.click()
                pg.wait_for_timeout(1800)
                return True
        except Exception:
            continue
    return False


def find_widget(pg):
    """返回 (ElementHandle, bounding_box)，取面积最大的可见 geetest 元素。"""
    best = None
    for sel in SELS:
        try:
            for el in pg.query_selector_all(sel):
                try:
                    if not el.is_visible():
                        continue
                    b = el.bounding_box()
                    if not b or b["width"] < 240 or b["height"] < 120:
                        continue
                    area = b["width"] * b["height"]
                    if best is None or area > best[2]:
                        best = (el, b, area)
                except Exception:
                    continue
        except Exception:
            continue
        if best:
            break
    return (best[0], best[1]) if best else (None, None)


def human_click(pg, x, y):
    """拟人轨迹：分多段移动 + 随机停顿，降低行为检测命中率。"""
    steps = random.randint(10, 22)
    pg.mouse.move(x, y, steps=steps)
    pg.wait_for_timeout(random.randint(60, 180))
    pg.mouse.down()
    pg.wait_for_timeout(random.randint(40, 120))
    pg.mouse.up()


def solve_once(pg, tag, wait_sec=900, cleared=None):
    """走一轮：截图 → 等答案 → 点击 → 提交 → 校验。

    cleared: 自定义「已通过」判定；默认「页面里没有撞墙文案」。
    返回 True 表示控件消失/验证通过。
    """
    cleared = cleared or (lambda: not wall_present(pg))
    el, box = find_widget(pg)
    if not el:
        return False

    cid = time.strftime("%m%d_%H%M%S") + "_" + str(tag)[:12].replace("/", "_")
    png = os.path.join(CAP_DIR, cid + ".png")
    try:
        el.screenshot(path=png, timeout=15000, animations="disabled")
    except Exception:
        try:
            el.screenshot(path=png, timeout=15000)
        except Exception:
            return False
    iw, ih = _png_size(png)
    if not iw:
        return False
    scale = iw / box["width"] if box["width"] else 1.0

    req = {"id": cid, "png": png, "img_w": iw, "img_h": ih,
           "box": box, "ts": time.time(), "url": pg.url}
    req_path = os.path.join(CAP_DIR, cid + ".req.json")
    ans_path = os.path.join(CAP_DIR, cid + ".ans.json")
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False)
    print(f"CAPTCHA_PENDING {req_path}", flush=True)

    # ---- 等 agent 写答案 ----
    pts = None
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if os.path.exists(ans_path):
            try:
                with open(ans_path, encoding="utf-8") as f:
                    pts = json.load(f).get("points") or []
                break
            except Exception:
                pass
        pg.wait_for_timeout(1000)
    if not pts:
        print("CAPTCHA_NO_ANSWER", flush=True)
        return False

    print(f"CAPTCHA_ANSWER {len(pts)} 点: {pts}", flush=True)
    for i, p in enumerate(pts):
        ax = box["x"] + p[0] / scale
        ay = box["y"] + p[1] / scale
        human_click(pg, ax, ay)
        pg.wait_for_timeout(random.randint(280, 620))
        print(f"  click#{i + 1} -> ({ax:.0f},{ay:.0f})", flush=True)

    pg.wait_for_timeout(600)
    # ---- 提交（极验 v4 是 div.geetest_submit，文本 OK/确认）----
    submitted = False
    for sel in (".geetest_submit", ".geetest_commit", "button.geetest_btn"):
        try:
            b = pg.query_selector(sel)
            if b and b.is_visible():
                b.click()
                submitted = True
                pg.wait_for_timeout(900)
                print("CAPTCHA_SUBMIT", sel, flush=True)
                break
        except Exception:
            continue
    if not submitted:
        for txt in ("OK", "确认", "确定"):
            try:
                b = pg.get_by_text(txt, exact=True).first
                if b and b.is_visible():
                    b.click()
                    submitted = True
                    pg.wait_for_timeout(900)
                    print("CAPTCHA_SUBMIT text", txt, flush=True)
                    break
            except Exception:
                continue
    if not submitted:
        print("CAPTCHA_NO_SUBMIT_BTN", flush=True)

    # ---- 校验结果（命中失败文案就早退，不空等 16 秒）----
    ok = False
    for _ in range(20):
        pg.wait_for_timeout(800)
        if cleared():
            ok = True
            break
        if find_widget(pg)[0] is None:
            ok = True
            break
        if failed(pg):
            break

    rec = {"id": cid, "png": png, "points": pts, "ok": ok,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(CAP_DIR, "index.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("CAPTCHA_OK" if ok else "CAPTCHA_FAIL", flush=True)
    return ok


def handle_wall(pg, tag="wall", rounds=3, cleared=None, wait_sec=900):
    """撞墙自愈主入口。返回 True=通过，False=放弃（应交回人工）。"""
    cleared = cleared or (lambda: not wall_present(pg))
    if cleared():
        return True
    for r in range(1, rounds + 1):
        print(f"CAPTCHA_ROUND {r}/{rounds}", flush=True)
        expand_widget(pg)
        pg.wait_for_timeout(1500)
        if solve_once(pg, f"{tag}_r{r}", wait_sec=wait_sec, cleared=cleared):
            return True
        pg.wait_for_timeout(1200)
        if cleared():
            return True
    return False
