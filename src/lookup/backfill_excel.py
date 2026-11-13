#!/usr/bin/env python3
"""
backfill_excel.py — 把 round{N}_tungee.jsonl 查询结果回填到客户反查记录表。

纪律(继承 v1 data-format.md):
  1. 先自动备份原表到 ./backups/
  2. 只写空单元格,绝不覆盖已有人工标注(--overwrite 才允许覆盖)
  3. 拿到号的行整行填深绿 FF00B050;blocked/notfound/nocontact 只留 JSON,备注列可选追加
  4. 回填后输出公司集合 diff 校验(丢失/新增必须为空才算成功)

用法:
  python3 backfill_excel.py --json leads/round1_tungee.jsonl \
      --xlsx 客户反查记录表11.2.xlsx --sheet "9.3平台反查"
  # 列映射默认对应「9.3平台反查」表,其他表用 --col 覆盖,如:
  #   --col name=C --col phone=G --col note=I     (四色主表布局)
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime

from openpyxl import load_workbook
from openpyxl.styles import PatternFill

try:  # Windows 默认 GBK，输出 ✓ → 重定向到文件会 UnicodeEncodeError
    from _console import setup as _utf8_stdio
    _utf8_stdio()
except Exception:
    pass

GOT_FILL = PatternFill("solid", start_color="FF00B050", end_color="FF00B050")

DEFAULT_COLS = {"name": "D", "addr": "E", "brief": "G", "contact": "K",
                "phone": "L", "email": "N", "scale": "A", "note": None}


def load_results(path):
    recs = {}
    if path.endswith(".jsonl"):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            old = recs.get(r["full"])
            # 续跑会追加新行:有号的那条优先,否则取最新一条,避免旧空记录遮蔽新结果
            if old is None or (r.get("phones") and not old.get("phones")):
                recs[r["full"]] = r
    else:
        recs = json.load(open(path, encoding="utf-8"))
    return recs


def norm(s):
    """匹配容忍:去空白、括号备注后缀(匹配只看主体名)。"""
    return re.sub(r"[\s（）()]+", "", s or "")


HEADER_HINTS = {
    "name": ("公司名称", "企业名称", "公司全称", "客户名称"),
    "addr": ("地址",),
    "brief": ("客户简况", "简况", "简介"),
    "contact": ("联系人",),
    "phone": ("电话", "手机"),
    "email": ("邮箱", "邮件"),
}


def detect_cols(ws):
    """按表头文字识别列字母，识别不到就返回空（由 DEFAULT_COLS 兜底）。"""
    found = {}
    for cell in ws[1]:
        if not cell.value:
            continue
        text = str(cell.value).replace("\n", "").strip()
        letter = cell.column_letter
        for key, hints in HEADER_HINTS.items():
            if key in found:
                continue
            if any(h in text for h in hints):
                found[key] = letter
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="round{N}_tungee.jsonl 或 .json")
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--sheet", default="9.3平台反查")
    ap.add_argument("--col", action="append", default=[],
                    help="覆盖列映射,如 --col phone=G,可多次")
    ap.add_argument("--note-text", default="", help="追加到备注列的文字,如 round1探迹2026-11-03补查")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="集合校验不通过（表中有公司不在本次结果里）时返回非 0")
    args = ap.parse_args()

    cols = dict(DEFAULT_COLS)
    for c in args.col:
        k, v = c.split("=", 1)
        cols[k] = v.upper()

    recs = load_results(args.json)
    got = {r["full"]: r for r in recs.values() if r.get("phones")}
    print(f"结果 {len(recs)} 条,其中有号 {len(got)} 条")

    # 备份
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join("backups", f"备份_{os.path.basename(args.xlsx)}_{stamp}.xlsx")
    shutil.copy2(args.xlsx, bak)
    print(f"已备份 → {bak}")

    wb = load_workbook(args.xlsx)
    if args.sheet not in wb.sheetnames:
        sys.exit(f"找不到 Sheet「{args.sheet}」,现有: {wb.sheetnames}")
    ws = wb[args.sheet]

    auto = detect_cols(ws)
    if auto:
        print(f"按表头识别列：{auto}")
        cols.update({k: v for k, v in auto.items() if k not in dict((c.split('=',1)[0], 1) for c in args.col)})

    name_col = cols["name"]
    sheet_names = {}  # norm(name) -> row
    for row in range(2, ws.max_row + 1):
        v = ws[f"{name_col}{row}"].value
        if v and str(v).strip():
            sheet_names[norm(str(v))] = row

    written, colored, missing = 0, 0, []
    for full, r in recs.items():
        row = sheet_names.get(norm(full))
        if row is None:
            # 容忍反向包含(表内名带后缀备注)
            cands = [rn for nm, rn in sheet_names.items() if norm(full) in nm or nm in norm(full)]
            row = cands[0] if len(cands) == 1 else None
        if row is None:
            missing.append(full)
            continue

        wrote_this = False

        def put(letter, value):
            nonlocal written, wrote_this
            if not value or not letter:
                return
            cell = ws[f"{letter}{row}"]
            if cell.value in (None, "") or args.overwrite:
                cell.value = value
                written += 1
                wrote_this = True

        put(cols["addr"], r.get("addr") or (r.get("notes", "") if r.get("status") == "notfound" else ""))
        put(cols["brief"], r.get("brief"))
        put(cols["contact"], r.get("contact"))
        put(cols["phone"], "、".join(r.get("phones") or []) or None)
        put(cols["email"], "、".join(r.get("emails") or []) or None)
        if r.get("scale"):
            put(cols["scale"], r["scale"])
        if args.note_text and cols["note"]:
            ncell = ws[f"{cols['note']}{row}"]
            tag = f"{args.note_text}:{r.get('status') or 'got'}"
            if args.note_text not in (ncell.value or ""):
                ncell.value = ((ncell.value + "\n") if ncell.value else "") + tag

        # 只有真正写入过数据(或本来就已有号)才染绿,避免"没写值也染色"的误导
        if r.get("phones") and (wrote_this or args.overwrite or
                                ws[f"{cols['phone']}{row}"].value not in (None, "")):
            for c in range(1, ws.max_column + 1):
                ws.cell(row=row, column=c).fill = GOT_FILL
            colored += 1

    wb.save(args.xlsx)

    # 集合完整性校验(v1 纪律)
    json_set = {norm(k) for k in recs}
    sheet_set = set(sheet_names)
    lost = [s for s in sheet_set if s not in json_set]
    print(f"回填完成:写值 {written} 处,深绿 {colored} 行")
    print(f"未在表中匹配到 {len(missing)} 家: {missing[:5]}{'...' if len(missing) > 5 else ''}")
    if lost:
        print(f"提示:表中有 {len(lost)} 家不在本次结果里(若为历史轮次已查公司则正常): {lost[:5]}")
        if args.strict:
            print("--strict 已开启：集合校验未通过，请确认后重跑（备份在 backups/）")
            sys.exit(1)
    print("校验:确认上面 diff 符合预期后再使用新表;异常时从 backups/ 恢复。")


if __name__ == "__main__":
    main()
