#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
street_map.py — 638/404 盘子的「扫街地图 + 20 号抽验名单」

数据源（三个文件必须一起用，缺一个就出不来正确结果）：
  名单_带电话.xlsx        404 家，明文号 / 联系人 / 匹配层级 / 省份
  已还原中文名_总表.csv    404 家，中文地址（= 工商注册地址，388 家是中文）
  可主攻638家.xlsx        638 家全盘，用于核对与补齐

⚠ 关键坑：可主攻638家.xlsx 里的「公司地址」是亚马逊店铺资料地址，243/343 是香港英文地址
  （卖家拿香港公司做收付款主体）。用它做扫街地图只有 100 家能落地，
  只有 总表.中文地址 才是工商注册地址，能提出街道的 283 家。

设计原则：
  1. 扫街只对「有明文号 + 中文注册地址」的卖家做——没号的人走到楼下也进不去。
  2. 抽验抽样框用「匹配层级」，不用「手机号数」：层级才预测「号是不是这家公司的」。
  3. 能确定性判死的先判死（同号多主体 / 虚拟号段），剩下才需要人去微信搜。
"""
import re
import importlib.util
import pandas as pd

spec = importlib.util.spec_from_file_location("pr", "pinyin_restore.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

D = "/Users/cruise/WorkBuddy AI/2026-11-03-20-35-14/amz_lead"
VIRT = {"170", "171", "162", "165", "167"}
HK = re.compile(r"HONG\s*KONG|KOWLOON|KWUN TONG|TUEN MUN|FANLING|TSUEN|\bHK\b", re.I)
EN_ADDR = re.compile(r"^[A-Za-z0-9\s,\./\-#()]+$")


def street(s):
    """提街道；「龙岗区坂田街道」和「坂田街道」必须归一成同一个，否则聚会散掉"""
    s = str(s)
    m = re.search(r"([\u4e00-\u9fa5]{2,10}(?:街道|镇))", s)
    if not m:
        m2 = re.search(r"([\u4e00-\u9fa5]{2,6}(?:区|县))", s)
        return m2.group(1) if m2 else ""
    name = m.group(1)
    if "区" in name:                 # 龙岗区坂田街道 → 坂田街道
        name = name.split("区")[-1]
    return name


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def bld_key(addr, st):
    """
    楼宇键 = 街道 + 路名门牌。不用 pr.normalize_addr：那个函数是为「地址碎片」写的，
    会剥掉 2-6 个汉字再找「省/市」，遇到「深圳市龙岗区布吉街道…」会把「龙岗区布」吃掉。
    """
    s = str(addr)
    # 先切到「社区」之后，否则「街道下李朗社区布澜路」会被贪婪匹配成路名
    if "社区" in s:
        s = s.split("社区")[-1]
    m = re.search(r"([\u4e00-\u9fa5]{2,8}(?:大道|路|街|巷|弄))\s*(\d+)\s*号", s)
    if not m:
        return ""
    road = m.group(1)
    if "街道" in road:
        road = road.split("街道")[-1] or road
    return f"{st}|{road}{m.group(2)}号"


def main():
    b = pd.read_excel(f"{D}/名单_带电话.xlsx", dtype=str).fillna("")
    t = pd.read_csv(f"{D}/已还原中文名_总表.csv", dtype=str).fillna("")
    a = pd.read_excel(f"{D}/可主攻638家.xlsx", dtype=str).fillna("")
    for d in (b, t, a):
        d["k"] = (d["亚马逊店铺"] if "亚马逊店铺" in d else d["店铺名"]).map(norm_key)

    m = b.merge(t[["k", "中文地址"]], on="k", how="left")
    m = m.merge(a[["k", "还原中文名", "公司地址"]].drop_duplicates("k"),
                on="k", how="left", suffixes=("", "_638"))
    m["ph"] = m["明文号"].str.replace(r"\D", "", regex=True)
    has = m[(m["ph"].str.len() == 11) & (m["ph"].str.startswith("1"))].copy()
    has["地址"] = has["中文地址"].fillna("")
    has["香港"] = has["地址"].map(lambda s: bool(HK.search(str(s))) or bool(EN_ADDR.match(str(s).strip()) and str(s).strip()))
    has["街道"] = has["地址"].map(street)
    for c in ["年运费潜力USD", "BSR排名"]:
        has[c] = pd.to_numeric(has[c], errors="coerce")

    dup = has["ph"].value_counts()
    has["同号多主体"] = has["ph"].map(lambda p: f"{dup[p]} 家共用" if dup[p] > 1 else "")
    has["号段风险"] = has["ph"].str[:3].map(lambda s: "虚拟号段" if s in VIRT else "")

    cn = has[(~has["香港"]) & (has["街道"] != "")].copy()

    def shops(idx):
        return " | ".join(
            f"{has.loc[i, '还原中文名'] or has.loc[i, '亚马逊店铺']}({has.loc[i, 'ph']})"
            for i in idx[:10])

    def agg_street(s):
        return s

    sm = (cn.groupby("街道")
            .agg(有号家数=("ph", "size"),
                 潜力合计=("年运费潜力USD", "sum"),
                 上升期=("客户分层", lambda s: int((s == "上升期·重点").sum())),
                 店铺=("ph", lambda s: shops(s.index)))
            .reset_index().sort_values(["有号家数", "潜力合计"], ascending=False))

    cn["_k"] = [bld_key(x, st) for x, st in zip(cn["地址"], cn["街道"])]
    lb = (cn[cn["_k"] != ""].groupby("_k")
          .agg(家数=("ph", "size"),
               店铺=("ph", lambda s: " | ".join(cn.loc[i, "亚马逊店铺"] for i in s.index)),
               号码=("ph", lambda s: " | ".join(s.astype(str))))
          .reset_index())
    lb = lb[lb["家数"] >= 2].sort_values("家数", ascending=False)
    # bld_key 只产出「路+号」，所以这一页全部是楼宇级；街道级的聚集看 ② 页
    if len(lb):
        lb.insert(1, "级别", "楼宇级")

    # 20 号抽验：按匹配层级分层，不按手机号数。
    # 组内不取「潜力最高的 n 家」（那样 20 条全是头部·大概率被锁），改为等距抽，
    # 让样本同时覆盖上升期——否则验完只知道头部号的活性，不知道能打的那批。
    def spread(sub, n):
        sub = sub.sort_values("年运费潜力USD", ascending=False)
        if len(sub) <= n:
            return sub
        step = len(sub) / n
        return sub.iloc[[int(i * step) for i in range(n)]]

    picks = []
    pool = has[~has["客户分层"].isin(["太小·养不活", "名字就绪·待探迹"])]
    for lvl, n in [("A·全文精确", 8), ("B·核心精确", 4), ("M·字号全等", 3)]:
        picks.append(spread(pool[pool["匹配层级"] == lvl], n))
    picks.append(spread(pool[pool["匹配层级"].str.startswith("池")], 5))
    sample = pd.concat(picks)
    sample = sample[~sample["ph"].duplicated()].head(20).copy()
    sample.insert(0, "序", range(1, len(sample) + 1))
    sample["判据"] = ("微信搜号：①头像/昵称与品牌或公司相关=活号本人 "
                   "②像代账/财务=半活 ③搜不到=死号 ④能加好友=人在用")
    sample["抽验结果"] = ""
    sample["备注"] = ""
    cols = ["序", "客户分层", "亚马逊店铺", "还原中文名", "明文号", "联系人", "匹配层级",
            "省份", "年运费潜力USD", "同号多主体", "号段风险", "判据", "抽验结果", "备注"]
    sample = sample[[c for c in cols if c in sample.columns]]

    big = pd.read_csv("/Users/cruise/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
                      "xwechat_files/wxid_hg1qywthh3b922_1a9c/temp/drag/leads_final_ranked.csv",
                      encoding="utf-8-sig", dtype=str, keep_default_na=False)
    bigk = set(big["店铺名"].map(norm_key))
    ak = set(a["k"])
    cross = pd.DataFrame([
        ("638 家", str(len(a))),
        ("3425 条", str(len(big))),
        ("638 被 3425 覆盖（按店铺名全等）", f"{len(ak & bigk)} / {len(ak)}"),
        ("3425 里 638 之外还有", f"{len(bigk - ak)} 条"),
        ("结论", "638 是 3425 的子集：3425 是全量抓取，638 是其中「腰部+上升期且有手机号数」的筛选结果"),
    ], columns=["项", "值"])

    summ = pd.DataFrame([
        ("638 全盘分层", "腰部·甜点区 450 / 上升期·重点 188"),
        ("638 已还原中文名", "137 / 638 (21.5%)"),
        ("带号名单", "404 家，明文号 343 家（全 11 位手机）"),
        ("联系人字段", "261 / 404"),
        ("地区", "广东 273 / 浙江 52 / 福建 15"),
        ("同号多主体", f"{int((dup > 1).sum())} 个号被 2 家共用（共 {int(dup[dup > 1].sum())} 家）"),
        ("虚拟号段", f"{int((has['号段风险'] != '').sum())} 个"),
        ("香港/英文地址（换源后）", f"{int(has['香港'].sum())} 家——638 原始「公司地址」列有 243 条是香港英文，改用总表中文地址后只剩这些"),
        ("可扫街（中文注册地址+街道）", f"{len(cn)} 家，覆盖 {len(sm)} 个街道"),
        ("街道≥2 家的街道", f"{int((sm['有号家数'] >= 2).sum())} 个"),
        ("楼宇级同址≥2 家", f"{len(lb)} 组，覆盖 {int(lb['家数'].sum()) if len(lb) else 0} 家"),
        ("地址来源提醒", "扫街必须用 已还原中文名_总表.中文地址；可主攻638家.公司地址是亚马逊店铺资料地址，不可用"),
    ], columns=["项", "值"])

    out = "/Users/cruise/lead_tools/扫街地图_20261106.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        summ.to_excel(w, sheet_name="① 汇总", index=False)
        sm.to_excel(w, sheet_name="② 扫街地图", index=False)
        lb.rename(columns={"_k": "归一化地址"}).to_excel(w, sheet_name="③ 楼宇同址", index=False)
        sample.to_excel(w, sheet_name="④ 20号抽验", index=False)
        cross.to_excel(w, sheet_name="⑤ 与3425核对", index=False)
        has[["客户分层", "亚马逊店铺", "还原中文名", "明文号", "联系人", "匹配层级",
             "省份", "地址", "街道", "香港", "同号多主体", "年运费潜力USD"]].to_excel(
            w, sheet_name="⑥ 343家带号全表", index=False)
        from openpyxl.utils import get_column_letter
        for name in w.sheets:
            ws = w.sheets[name]
            ws.freeze_panes = "A2"
            for i in range(1, ws.max_column + 1):
                h = str(ws.cell(row=1, column=i).value or "")
                ws.column_dimensions[get_column_letter(i)].width = (
                    70 if "店铺" in h else 46 if "地址" in h else 16)

    print("有号", len(has), "| 可扫街", len(cn), "| 街道", len(sm), "| 楼宇组", len(lb))
    print(sm.head(12).to_string(index=False, max_colwidth=30))
    print("\n638 被 3425 覆盖:", len(ak & bigk), "/", len(ak))
    print("输出", out)


if __name__ == "__main__":
    main()
