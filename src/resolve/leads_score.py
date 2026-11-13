#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
leads_score.py — 对「已排序的亚马逊卖家名单 CSV」做分诊，输出可执行的打法表。

和 pinyin_restore.py 的分工：
  pinyin_restore.py  处理「只有名字+地址」的原始名单（无销量、无排序）
  leads_score.py     处理「已打分排序」的名单（带 SellerID / BSR / 分层 / 运费潜力）

它补的是这份名单缺的三样：主体可查性、国别可打性、以及「先打谁」。

用法:
  python3 leads_score.py 输入.csv 输出.xlsx
"""
import re
import sys
import importlib.util
import pandas as pd

spec = importlib.util.spec_from_file_location("pr", "pinyin_restore.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


# ---------- 国别：决定「这单能不能做」，比名字可查性更重要 ----------
def country(addr):
    s = str(addr).upper()
    if not s.strip():
        return "空", "地址缺失"
    if re.search(r"[\u4e00-\u9fa5]", s) or re.search(r"\bCN$", s):
        return "CN", "大陆主体，直接查"
    if re.search(r"HONG\s*KONG|HONGKONG|\bHK\b|KOWLOON|TSUEN|KWUN TONG|新界|香港", s):
        return "HK", "香港主体：多为收付款壳，实际运营在深圳/东莞，按店铺名找大陆运营方"
    if re.search(r"\bUS$|\bUSA\b|UNITED STATES", s):
        return "US", "美国主体：不是中国出口方，货代做不进去，跳过"
    return "其他", "第三国/地址异常"


# ---------- 打法：把「可查性 × 国别 × 分层」压成一句能立刻做的事 ----------
def first_action(level, ctry, tier, ind_cn):
    if ctry == "US":
        return "跳过——美国主体，不是你的客户"
    if ctry == "HK":
        return "先用店铺名/品牌名搜领英或 1688，找大陆运营主体；HK 地址本身查不出人"
    if level == "L1":
        return "名称即信用代码，企查查直接查 → 核社保人数 → 取公示电话"
    if level in ("L2", "L2E"):
        return "企查查搜「检索串」（已带城市，别去掉城市）→ 核社保人数 → 取公示电话"
    if level == "L4":
        return "英文品牌名，还原不了：USPTO TSDR 查商标申请人 / 领英搜公司名 / 店铺页 Brand"
    return "主体异常，人工看一眼"


def tier_rank(t):
    return {"上升期·重点": 0, "腰部·甜点区": 1, "小而稳·可培育": 2,
            "头部·大概率被锁": 3, "太小·养不活": 4, "名字就绪·待探迹": 5}.get(t, 9)


def main(src, dst):
    df = pd.read_csv(src, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    for c in ["年运费潜力USD", "BSR排名", "评论数", "估算月销", "估算客单价USD", "运费率"]:
        df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")

    rows = []
    for _, r in df.iterrows():
        res = pr.classify(r["公司全称"], r["公司地址"])
        ctry, ctry_note = country(r["公司地址"])
        ind_cn = pr.IND_CN.get(res["ind"].split("+")[0], "") if res["ind"] else ""
        rows.append({
            "原优先级": int(r["优先级"]) if str(r["优先级"]).isdigit() else None,
            "客户分层": r["客户分层"],
            "年运费潜力USD": r["年运费潜力USD"],
            "店铺名": r["店铺名"],
            "公司全称(原)": r["公司全称"],
            "国别": ctry,
            "可查性": res["level"],
            "推荐检索串": res["query"],
            "候选(前5)": " / ".join(res["cands"][:5]) if res["cands"] else "",
            "公司地址": r["公司地址"],
            "类目": r["类目"],
            "BSR排名": r["BSR排名"],
            "评论数": r["评论数"],
            "估算月销": r["估算月销"],
            "估算客单价USD": r["估算客单价USD"],
            "运费率": r["运费率"],
            "推荐档位": r["推荐档位"],
            "卖家ID": r["卖家ID"],
            "电话": "",
            "建议第一步": "",
            "国别说明": ctry_note,
        })
    out = pd.DataFrame(rows)
    out["建议第一步"] = [
        first_action(a, b, c, "") for a, b, c in zip(out["可查性"], out["国别"], out["客户分层"])
    ]

    # 真正能打的集合：大陆/香港 + 非「太小/待探迹」+ 主体可查
    playable = out[(out["国别"].isin(["CN", "HK"]))
                   & (~out["客户分层"].isin(["太小·养不活", "名字就绪·待探迹"]))]
    playable = playable.assign(_t=playable["客户分层"].map(tier_rank)) \
                       .sort_values(["_t", "年运费潜力USD"], ascending=[True, False]) \
                       .drop(columns=["_t"]).reset_index(drop=True)
    playable.insert(0, "序", range(1, len(playable) + 1))

    # 地址聚类（只在能打的集合里做，打不到的聚出来没意义）
    playable["_addr"] = [pr.normalize_addr(a) for a in playable["公司地址"]]
    grp = (playable[playable["_addr"] != ""]
           .groupby("_addr")
           .agg(店铺数=("店铺名", "size"),
                店铺=("店铺名", lambda s: " | ".join(s.astype(str)[:6])),
                分层=("客户分层", lambda s: s.value_counts().index[0]),
                可查性=("可查性", lambda s: "".join(sorted(set(s)))),
                年运费潜力合计=("年运费潜力USD", "sum"),
                地址原文=("公司地址", lambda s: str(s.iloc[0])[:80]))
           .reset_index().sort_values(["店铺数", "年运费潜力合计"], ascending=False))
    grp = grp[grp["店铺数"] >= 2].reset_index(drop=True)
    if len(grp):
        grp.insert(0, "序", range(1, len(grp) + 1))
        grp["级别"] = grp["_addr"].str.contains("号").map({True: "楼宇级", False: "片区级"})

    # 汇总
    n = len(out)
    def pct(x):
        return f"{x} ({x / n * 100:.1f}%)"
    summary = [
        ("名单总条数", str(n)),
        ("站点", "100% 美国站（这份名单没有第二个国家，按国家分类无信息量）"),
        ("联系电话填充", f"{(out['电话']!='').sum()} 条（源文件该列全空）"),
        ("邮箱填充", "7 条 (0.2%)"),
        ("主体可还原合计", pct(int(out["可查性"].isin(["L1", "L2", "L2E"]).sum()))),
        ("  L2 拼音公司", pct(int((out["可查性"] == "L2").sum()))),
        ("  L2E 英文式主体", pct(int((out["可查性"] == "L2E").sum()))),
        ("  L4 品牌名/真美国公司", pct(int((out["可查性"] == "L4").sum()))),
        ("  L5 港台海外", pct(int((out["可查性"] == "L5").sum()))),
        ("国别 大陆", pct(int((out["国别"] == "CN").sum()))),
        ("国别 香港", pct(int((out["国别"] == "HK").sum()))),
        ("国别 美国", pct(int((out["国别"] == "US").sum()))),
        ("可打集合（大陆/香港 × 非太小）", str(len(playable))),
        ("同址≥2 家的地址组", f"{len(grp)} 组，覆盖 {int(grp['店铺数'].sum()) if len(grp) else 0} 家"),
        ("⚠ 年运费潜力", "100% 由 月销(8档) × 客单价(8档) × 运费率(7档) × 12 算出，是档位估值不是实测"),
    ]
    sum_df = pd.DataFrame(summary, columns=["项", "值"])

    # 实测记录：命中率不能靠声称，必须留可复核的抽样证据
    tests = pd.DataFrame([
        ("深圳合宇科技", "深圳市合宇科技有限公司", "91440300593003484D", "第1候选命中", "2026-11-06"),
        ("深圳领干哲科技", "深圳市灵感者科技有限公司", "91440300MA5FN73119", "命中但候选字全错（实为灵感者），靠城市+行业锚点捞回", "2026-11-06"),
        ("无锡新雅安全", "无锡新亚安全用品有限公司", "913202056701352465", "第3候选命中；行业词「安全用品」由 SAFETY PRODUCTS 译出", "2026-11-06"),
        ("合肥摩诚贸易", "未命中（就近命中合肥默诚百货商行）", "—", "候选字 5 个全不中，此类须换字号重试或放弃", "2026-11-06"),
    ], columns=["检索串", "命中主体", "信用代码", "结论", "实测日"])

    with pd.ExcelWriter(dst, engine="openpyxl") as w:
        sum_df.to_excel(w, sheet_name="① 汇总", index=False)
        playable.drop(columns=["_addr"]).to_excel(w, sheet_name="② 优先打", index=False)
        if len(grp):
            grp.rename(columns={"_addr": "归一化地址"}).to_excel(
                w, sheet_name="③ 地址聚类", index=False)
        out.sort_values(["国别", "年运费潜力USD"], ascending=[True, False]).to_excel(
            w, sheet_name="④ 全量标注", index=False)
        tests.to_excel(w, sheet_name="⑤ 实测记录", index=False)

        from openpyxl.utils import get_column_letter
        for name in w.sheets:
            ws = w.sheets[name]
            ws.freeze_panes = "A2"
            for i in range(1, ws.max_column + 1):
                hdr = ws.cell(row=1, column=i).value or ""
                ws.column_dimensions[get_column_letter(i)].width = (
                    40 if "全称" in str(hdr) or "检索串" in str(hdr) or "地址" in str(hdr)
                    else 26 if "候选" in str(hdr) or "店铺" in str(hdr)
                    else 12)

    print(f"总条数 {n}")
    print(out["可查性"].value_counts().sort_index().to_string())
    print(f"可打集合 {len(playable)} 条 | 地址组 {len(grp)} 组")
    print(f"输出 {dst}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: leads_score.py 输入.csv 输出.xlsx")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
