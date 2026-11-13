#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
20 个抽验号 + 全量法人核查（2026-11-06 实测，企查查 T+0）。
台账：法人/状态/参保/实缴/成立 + 姓氏匹配 + 可打性评级。
"""
import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

OUT = os.path.expanduser("~/lead_tools/20号抽验_20261106.xlsx")

# ---------- 全量数据 ----------
# 序, 层级, 店铺, 主体, 手机号, 掩码, 潜力, 分层, 法人, 匹配, 状态, 参保, 注册资本, 成立, 评级, 结论
ROWS = [
    (1, "A·全文精确", "XIN ZHAN DA", "深圳市鑫展达科技有限公司", "13692224221", "王**", 396000,
     "头部·大概率被锁", "张斌", "✗ 不同姓", "存续", "0", "30 万", "2015-05-07", "低优先",
     "号主姓王、法人张斌，不是同一人；参保 0 人、注册在福田城中村住宅楼 → 空壳，号大概率代账"),
    (2, "A·全文精确", "simingzhichuang", "深圳市思铭智创科技有限公司", "13265891670", "", 20160,
     "上升期·重点", "梁思敏", "无掩码·搜人名", "存续", "0", "1 万", "2025-06-05", "可打",
     "无掩码，但法人名到手：微信搜「梁思敏」比搜号更准；2025-06 新设、参保 0，属起步期"),
    (3, "A·全文精确", "UNNMIIY-FITNESS", "深圳市豪情网络科技有限公司", "13544060604", "欧**", 7560,
     "上升期·重点", "苏茜", "✗ 不同姓", "存续", "3", "100 万", "2018-09-06", "可打",
     "法人苏茜、号主姓欧 → 合伙人或运营负责人，可打但话术别喊老板，问「负责物流的是哪位」"),
    (4, "A·全文精确", "Sanjour Direct", "深圳市远腾邦科技有限公司", "18718687948", "", 5040,
     "上升期·重点", "林铭勋", "无掩码·搜人名", "存续", "0", "0.1 万", "2019-07-26", "不建议",
     "注册资本 1000 元 + 参保 0 人 = 典型店铺挂靠主体，实际经营方不是这家，号源可疑"),
    (5, "A·全文精确", "Doorich US", "深圳明安创达商贸有限公司", "18124360049", "", 3240,
     "小而稳·可培育", "张焱", "无掩码·搜人名", "存续", "3", "10 万（实缴 10 万）", "2025-09-22", "优先打",
     "★潜力排倒三但质地最好：当年新设即实缴 10 万、一般纳税人、参保 3 人、法人张焱可直接搜"),
    (6, "A·全文精确", "Zhenpeii-US", "义乌市振沛电子商务有限公司", "18305899425", "楼**", 2520,
     "小而稳·可培育", "楼双倩", "✓ 同姓·罕见", "存续", "3", "3 万", "2023-05-31", "优先打",
     "★楼姓罕见，同姓基本确认是老板娘本人；参保 3 人、2026-06 还在变更 → 活跃"),
    (7, "A·全文精确", "Xinyujiachuang", "深圳市鑫宇嘉创科技有限公司", "17666129161", "", 1728,
     "小而稳·可培育", "庹伟", "无掩码·搜人名", "存续", "1", "1 万（实缴 0.2 万）", "2025-08-27", "可打",
     "庹姓极罕见，直接搜人名即可定位；2025-08 新设、参保 1 人"),
    (8, "A·全文精确", "WEMOH", "深圳市微摩科技有限公司", "15625263182", "牛**", 1210,
     "小而稳·可培育", "牛立争", "✓ 同姓·罕见", "吊销", "—", "10 万（实缴 10 万）", "2012-05-21", "不建议",
     "★姓氏对上但公司 2013 年后就没再变更、现已吊销 → 国内主体废弃，卖家现在用别的壳，号多半已弃用"),
    (9, "B·核心精确", "MAJALIS", "佛山斯歌尔建材有限公司", "15820237636", "温**", 50400,
     "上升期·重点", "温小龙", "✓ 同姓·罕见", "存续", "5", "200 万", "2023-06-25", "优先打",
     "★同姓 + 参保 5 人 + 200 万注册资本 → 20 条里最像老板本人且真在经营的一条"),
    (10, "B·核心精确", "YarrowHome", "苏州易航信息科技有限公司", "13918501578", "卓**", 8400,
     "腰部·甜点区", "卓之航", "✓ 同姓·罕见", "在业", "1", "1000 万（未实缴）", "2021-08-31", "可打",
     "卓姓罕见、法人卓之航对得上；但主体主营网约车+增值电信，与家居品类错配 → 可能多主体运营，电话需确认"),
    (11, "B·核心精确", "PETSTA-US", "深圳市妍慕医疗科技有限公司", "13202281706", "拜**", 5760,
     "腰部·甜点区", "拜文超", "✓ 同姓·罕见", "注销", "—", "100 万", "2019-01-09", "不建议",
     "★拜姓罕见、拜文超完全对上 → 号当初是真的；但公司 2021-02 已注销，主体彻底没了"),
    (12, "B·核心精确", "Belemar", "汕头市贝乐马科技有限公司", "15013907159", "王**", 3168,
     "腰部·甜点区", "王钿植", "≈ 同姓·常见", "存续", "0", "100 万", "2025-01-14", "低优先",
     "王姓全国第一，同姓不构成证据；参保 0、注册在澄海社区民房 5 楼 → 小作坊型，2025 新设"),
    (13, "M·字号全等", "COOLJOB Safety", "无锡鑫亚电子有限公司", "13861867604", "曹*", 64800,
     "上升期·重点", "曹勤", "✓ 两字全对", "存续", "0", "150 万（实缴 150 万）", "2005-12-08", "可打",
     "★上一轮怀疑错了：掩码「曹*」两字正好对上法人「曹勤」，主体是对的，不是无锡新亚。但主体卖电子元件、店铺卖劳保，品类错配需确认"),
    (14, "M·字号全等", "Mart Cobra", "广州破壳数码科技有限公司", "13066368796", "李**", 45360,
     "上升期·重点", "李童童", "≈ 同姓·常见", "存续", "2", "100 万（实缴 3 万）", "2010-04-13", "可打",
     "李姓常见，同姓无证据价值；但 2010 年老公司、参保 2 人、2025-05 仍在变更 → 真在经营的数码卖家"),
    (15, "M·字号全等", "Kollody", "合肥可丽迪教育科技有限公司", "15155942727", "冯**", 5760,
     "上升期·重点", "冯永乐", "≈ 同姓·常见", "注销", "—", "30 万", "2022-04-15", "不建议",
     "★2022-04 成立、2022-07 就注销，只活 3 个月 → 明显是买来办事的壳；冯姓常见，同姓无意义"),
    (16, "池-core", "MOVE SPEED Direct", "义乌市易速电子商务有限公司", "15356924016", "黄**", 40320,
     "上升期·重点", "黄勇慧", "≈ 同姓·常见", "存续", "1", "10 万", "2014-07-14", "低优先",
     "黄姓常见；主体卖化妆品/服装/纹身器材，与 MOVE SPEED 数码配件错配，池匹配疑似张冠李戴"),
    (17, "池-core", "Budding Trade", "义乌市誉腾商贸有限公司", "18818275558", "万*", 30240,
     "头部·大概率被锁", "万鑫", "✓ 两字全对", "存续", "2", "10 万", "2016-09-13", "优先打",
     "★掩码「万*」两字对上法人「万鑫」；但 2016 年成立后从未变更，十年没动过，需电话确认是否还在经营"),
    (18, "池-core+省份", "meggieo", "深圳市屹鑫科技有限公司", "18929329927", "麻**", 8400,
     "上升期·重点", "麻建金", "✓ 同姓·罕见", "存续", "1", "10 万（实缴 10 万）", "2012-02-14", "优先打",
     "★麻姓极罕见、完全对上；2012 年老公司实缴到位、2026-08 仍在变更 → 稳定经营"),
    (19, "池-core", "BBXTYLY", "深圳市裔嘉益贸易有限公司", "18979761877", "陈**", 2520,
     "小而稳·可培育", "钟平", "✗ 不同姓", "存续", "—", "—", "2018-05-21", "低优先",
     "法人钟平、号主姓陈，不是同一人；同字号还有一家「裔嘉益电子科技」（法人邹清泉），家族多主体"),
    (20, "池-core", "HBlife Offical Flagship Store", "深圳市亨捷贸易有限公司", "13580555996", "肖*", 1512,
     "小而稳·可培育", "肖婷", "✓ 两字全对", "存续", "0", "5 万（实缴 2.7 万）", "2017-05-26", "可打",
     "掩码「肖*」两字对上法人「肖婷」，号基本是本人；但参保 0 人，团队可能只有夫妻两人"),
]

HEAD = ["序", "层级", "亚马逊店铺", "还原主体", "手机号", "掩码", "可打性",
        "法定代表人", "姓氏匹配", "登记状态", "参保", "注册资本", "成立日期",
        "年运费潜力USD", "客户分层", "结论", "① 微信", "② 头像", "③ 本人"]

FILL = {"A": "E8F3EC", "B": "EAF1FA", "M": "FDF3E3", "池": "F7EDED"}
RATE_FILL = {"优先打": "1E6B3A", "可打": "4A7C59", "低优先": "9A6B1F", "不建议": "B3261E"}

thin = Side(style="thin", color="D9D9D9")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
HDR_FILL = PatternFill("solid", fgColor="2F4A45")
HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
BODY = Font(size=10)
WARN = Font(size=10, color="B3261E")
GOOD = Font(size=10, color="1E6B3A", bold=True)
DIM = Font(size=10, color="7A8A87")

wb = Workbook()

# ================= Sheet 1 =================
ws = wb.active
ws.title = "20号抽验"

ws.merge_cells("A1:S1")
c = ws["A1"]
c.value = "探迹明码号 · 20 条全量核查（2026-11-06）｜法人 20/20 已核"
c.font = Font(bold=True, size=14, color="1F2D2B")
c.alignment = Alignment(vertical="center")
ws.row_dimensions[1].height = 28

ws.merge_cells("A2:S2")
c = ws["A2"]
c.value = ("可打性已按「姓氏匹配 × 主体健康度」预排：优先打 5 / 可打 7 / 低优先 4 / 不建议 4。"
          "掩码姓氏与法人姓氏同为罕见姓或两字全对 → 号大概就是老板；已注销/吊销 → 主体废弃，号可能已弃用。")
c.font = Font(size=9.5, color="5A6B68")
c.alignment = Alignment(vertical="center", wrap_text=True)
ws.row_dimensions[2].height = 30

HDR = 4
for j, h in enumerate(HEAD, 1):
    cell = ws.cell(row=HDR, column=j, value=h)
    cell.fill = HDR_FILL
    cell.font = HDR_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = BORDER
ws.row_dimensions[HDR].height = 32

for i, row in enumerate(ROWS):
    (no, tier, shop, ent, phone, mask, pot, seg, legal, match,
     st, ins, cap, est, rate, concl) = row
    r = HDR + 1 + i
    key = "池" if tier.startswith("池") else tier[0]
    fill = PatternFill("solid", fgColor=FILL[key])
    vals = [no, tier, shop, ent, phone, mask or "—", rate, legal, match,
            st, ins, cap, est, pot, seg, concl, "", "", ""]
    for j, v in enumerate(vals, 1):
        cell = ws.cell(row=r, column=j, value=v)
        cell.fill = fill
        cell.border = BORDER
        cell.font = BODY
        if j == 5:
            cell.number_format = "@"
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif j == 7:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = Font(size=10, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=RATE_FILL[rate])
        elif j == 8:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = Font(size=10, bold=True)
        elif j == 9:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if match.startswith("✓"):
                cell.font = GOOD
            elif match.startswith("✗"):
                cell.font = WARN
            elif match.startswith("≈"):
                cell.font = DIM
        elif j == 10:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if st in ("注销", "吊销"):
                cell.font = WARN
        elif j == 11:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if ins == "0":
                cell.font = WARN
        elif j == 14:
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="right", vertical="center")
        elif j in (1, 2, 6, 12, 13, 15):
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=(j in (12, 13, 15)))
        elif j == 16:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if concl.startswith("★"):
                cell.font = WARN
        else:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[r].height = 42

LAST = HDR + len(ROWS)

dv1 = DataValidation(type="list", formula1='"未测,能搜到,搜不到"', allow_blank=True, showDropDown=False)
ws.add_data_validation(dv1); dv1.add(f"Q{HDR+1}:Q{LAST}")
dv2 = DataValidation(type="list", formula1='"未填,像品牌/公司,像代账/中介,像个人生活号,空号"',
                     allow_blank=True, showDropDown=False)
ws.add_data_validation(dv2); dv2.add(f"R{HDR+1}:R{LAST}")
dv3 = DataValidation(type="list", formula1='"未填,本人,非本人,存疑"', allow_blank=True, showDropDown=False)
ws.add_data_validation(dv3); dv3.add(f"S{HDR+1}:S{LAST}")

SR = LAST + 2
items = [("统计", None), ("优先打", f'=COUNTIF(G{HDR+1}:G{LAST},"优先打")'),
         ("可打", f'=COUNTIF(G{HDR+1}:G{LAST},"可打")'),
         ("低优先", f'=COUNTIF(G{HDR+1}:G{LAST},"低优先")'),
         ("不建议", f'=COUNTIF(G{HDR+1}:G{LAST},"不建议")'),
         ("标注本人", f'=COUNTIF(S{HDR+1}:S{LAST},"本人")')]
for k, (lab, formula) in enumerate(items):
    cell = ws.cell(row=SR, column=1 + k * 2, value=lab)
    cell.font = Font(bold=True, size=10)
    if formula:
        v = ws.cell(row=SR, column=2 + k * 2, value=formula)
        v.font = Font(bold=True, size=10)

widths = [4.5, 11, 26, 28, 12.5, 8, 8, 10, 15, 8, 6, 17, 11, 13, 15, 52, 11, 14, 9]
for j, w in enumerate(widths, 1):
    ws.column_dimensions[get_column_letter(j)].width = w
ws.freeze_panes = f"C{HDR+1}"
ws.auto_filter.ref = f"A{HDR}:S{LAST}"

# ================= Sheet 2: 优先打 =================
ws2 = wb.create_sheet("优先打9条")
ws2.merge_cells("A1:H1")
c = ws2["A1"]
c.value = "先打这 9 条（优先打 5 + 可打里姓氏硬证据 4）——按这个顺序，不用测完 20 个"
c.font = Font(bold=True, size=13, color="1F2D2B")
ws2.row_dimensions[1].height = 26

ORDER = [9, 18, 6, 17, 5, 13, 20, 10, 3]
H2 = ["顺序", "店铺", "主体", "手机号", "法定代表人", "证据强度", "为什么它能排前面"]
HR = 3
for j, h in enumerate(H2, 1):
    cell = ws2.cell(row=HR, column=j, value=h)
    cell.fill = HDR_FILL; cell.font = HDR_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = BORDER
ws2.row_dimensions[HR].height = 30

REASON = {
    9: ("温** / 温小龙", "强 · 罕见姓同姓", "参保 5 人 + 200 万注册资本 + 2023 年还在变更，20 条里唯一「同姓 + 真在经营 + 体量够」三项全中的"),
    18: ("麻** / 麻建金", "强 · 罕见姓同姓", "2012 年老公司、实缴 10 万到位、2026-08 仍在变更；麻姓全国不到十万分之一"),
    6: ("楼** / 楼双倩", "强 · 罕见姓同姓", "参保 3 人、2026-06 变更过；义乌北苑，货代顺路可扫"),
    17: ("万* / 万鑫", "强 · 两字全对", "潜力 $30,240 排第二；唯一顾虑是 2016 年后从未变更，电话先确认是否还在做"),
    5: ("无掩码 / 张焱", "中 · 有法人名可搜", "实缴 10 万 + 一般纳税人 + 参保 3 人，质地最好；潜力只 $3,240，但这是能成交的那种"),
    13: ("曹* / 曹勤", "强 · 两字全对", "潜力 $64,800 最高；但主体卖电子元件、店铺卖劳保，电话第一句要确认是不是同一盘生意"),
    20: ("肖* / 肖婷", "强 · 两字全对", "号基本是本人；参保 0、夫妻两人团队，适合先加微信不急着报价"),
    10: ("卓** / 卓之航", "强 · 罕见姓同姓", "主体主营网约车+增值电信，与家居错配；可能是多主体运营，值得问清物流怎么走"),
    3: ("欧** / 苏茜", "中 · 不同姓但公司正常", "号主是合伙人或运营，参保 3 人、100 万注册资本，能找到决策人"),
}

for i, no in enumerate(ORDER):
    row = next(x for x in ROWS if x[0] == no)
    _, tier, shop, ent, phone, mask, pot, seg, legal, match, st, ins, cap, est, rate, _ = row
    ev, strength, why = REASON[no]
    r = HR + 1 + i
    vals = [i + 1, shop, ent, phone, legal, strength, why]
    for j, v in enumerate(vals, 1):
        cell = ws2.cell(row=r, column=j, value=v)
        cell.border = BORDER
        cell.font = BODY
        if j == 1:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = Font(size=11, bold=True)
        elif j == 4:
            cell.number_format = "@"
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif j == 5:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = Font(size=10, bold=True)
        elif j == 6:
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = GOOD if strength.startswith("强") else DIM
        else:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws2.row_dimensions[r].height = 40

w2 = [6, 26, 30, 13, 12, 18, 74]
for j, w in enumerate(w2, 1):
    ws2.column_dimensions[get_column_letter(j)].width = w
ws2.freeze_panes = f"A{HR+1}"

# ================= Sheet 3: 不宜打 =================
ws3 = wb.create_sheet("不建议4条")
ws3.merge_cells("A1:F1")
c = ws3["A1"]
c.value = "这 4 条别浪费电话费（主体已废或空壳）"
c.font = Font(bold=True, size=13, color="B3261E")
ws3.row_dimensions[1].height = 26

H3 = ["序", "店铺", "主体", "法定代表人 / 掩码", "硬伤", "真实情况"]
for j, h in enumerate(H3, 1):
    cell = ws3.cell(row=3, column=j, value=h)
    cell.fill = PatternFill("solid", fgColor="8C1D18")
    cell.font = HDR_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = BORDER
ws3.row_dimensions[3].height = 28

BAD = [
    (8, "WEMOH", "深圳市微摩科技有限公司", "牛立争 / 牛**", "已吊销，2013 年后无变更",
     "姓氏对上了，说明号当初是真的。但公司吊销 12 年，卖家早换壳了（大概率香港公司），这号多半没人接"),
    (11, "PETSTA-US", "深圳市妍慕医疗科技有限公司", "拜文超 / 拜**", "已注销（2021-02）",
     "拜姓罕见、完全对上——号码是真的，主体死了。若还想联系，只能靠法人名去搜现用主体"),
    (15, "Kollody", "合肥可丽迪教育科技有限公司", "冯永乐 / 冯**", "成立 3 个月即注销",
     "2022-04 成立、2022-07 注销，标准买壳办事。冯姓常见，同姓无任何意义"),
    (4, "Sanjour Direct", "深圳市远腾邦科技有限公司", "林铭勋 / 无掩码", "注册资本 1000 元 + 参保 0",
     "注册资本 0.1 万是店铺挂靠主体典型特征，实际经营方是别人；号大概率是挂靠中介的"),
]
for i, (no, shop, ent, who, flaw, truth) in enumerate(BAD):
    r = 4 + i
    for j, v in enumerate([no, shop, ent, who, flaw, truth], 1):
        cell = ws3.cell(row=r, column=j, value=v)
        cell.border = BORDER
        cell.font = BODY
        if j == 1:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        elif j == 5:
            cell.font = WARN
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        else:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws3.row_dimensions[r].height = 46

w3 = [5, 24, 30, 16, 24, 82]
for j, w in enumerate(w3, 1):
    ws3.column_dimensions[get_column_letter(j)].width = w

# ================= Sheet 4: 判据 =================
ws4 = wb.create_sheet("判据与决策")
ws4.column_dimensions["A"].width = 22
ws4.column_dimensions["B"].width = 82


def block(row, title, lines, color="1F2D2B"):
    c = ws4.cell(row=row, column=1, value=title)
    c.font = Font(bold=True, size=11.5, color=color)
    c.alignment = Alignment(vertical="top")
    r = row
    for ln in lines:
        cell = ws4.cell(row=r, column=2, value=ln)
        cell.font = Font(size=10)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws4.row_dimensions[r].height = 20
        r += 1
    return r


r = 1
c = ws4.cell(row=r, column=1, value="判据与决策")
c.font = Font(bold=True, size=14, color="1F2D2B")
ws4.row_dimensions[r].height = 26
r += 2

r = block(r, "手机号能查到名字吗", [
    "完整真名：查不到，也不该查。实名只在运营商，非授权查询触刑法 253 条之一 + PIPL，市面「手机号查姓名」全是黑产。",
    "你手上已有的 王** / 楼** 就是「掩码真名」——姓是真的、名被隐去，来自实名数据源，不是猜的。",
    "合法替代：从公司主体反查法定代表人，再和掩码姓氏比对。同姓 → 号大概就是老板本人。",
], color="B3261E")
r += 1

r = block(r, "姓氏匹配怎么判（20 条实测校准）", [
    "✓ 强证据一：罕见姓同姓。楼/牛/温/卓/拜/麻 六个姓全国都不到十万分之一，同姓即本人 —— 6 条。",
    "✓ 强证据二：掩码只有两个字且全对。曹*/曹勤、万*/万鑫、肖*/肖婷，掩码长度=姓名字数，完全吻合 —— 3 条。",
    "≈ 无效证据：常见姓同姓。王/李/冯/黄/陈 占全国人口 3% 以上，同姓什么都说明不了 —— 5 条。",
    "✗ 反向证据：不同姓。张斌 vs 王**、苏茜 vs 欧**、钟平 vs 陈** —— 号是合伙人/运营/财务，话术别喊老板。",
    "结论：20 条里 9 条姓氏证据成立（6 罕见姓 + 3 两字全对），探迹这批掩码名来源可靠。",
])
r += 1

r = block(r, "主体健康度（比号码更能决定值不值得打）", [
    "已注销/吊销 → 主体废弃，号可能已弃用（微摩吊销 12 年、妍慕注销 5 年、可丽迪活 3 个月）",
    "注册资本 0.1 万 / 参保 0 → 店铺挂靠主体，实际经营方是别人（远腾邦、鑫展达、贝乐马、亨捷）",
    "实缴到位 + 参保 ≥3 + 一般纳税人 → 真在经营（明安创达、斯歌尔、振沛）",
    "品类错配 → 主体主营与店铺品类对不上，可能多主体运营，电话第一句要确认（易航网约车、鑫亚电子、易速化妆品）",
])
r += 1

r = block(r, "顺序建议（已按此排「优先打9条」）", [
    "1. 先打 9 条：5 条优先打 + 4 条姓氏硬证。9 条里成交 1 条就够覆盖全部时间成本。",
    "2. 剩下 6 条「低优先」等第一批回音再说；4 条不建议直接放弃，别手贱去打。",
    "3. 微信搜索顺序与电话一致，先加微信再打电话——这批人几乎不在电话里谈物流。",
    "4. 打完的结论填回「20号抽验」页 ① ② ③ 三列，底部统计会自动出活号率。",
])
r += 1

r = block(r, "九条实测里的三个反常识", [
    "① 潜力最高那条（鑫展达 $396,000）参保 0、注册在城中村住宅 → 空壳，反而最不值得打。",
    "② WEMOH 姓氏对上了，但公司吊销 12 年 → 号可能还对，主体早废了。「号真」和「主体活」是两件事。",
    "③ 质地最好的不是潜力最高的，是明安创达（$3,240，新设即实缴 10 万 + 参保 3 人 + 一般纳税人）。",
    "④ 上一轮我怀疑 13 条主体还原错（以为是无锡新亚安全用品），实际「曹*」正好对上「曹勤」两字 —— 我错了，台账已改。",
])

wb.save(OUT)
print("saved:", OUT, "| rows:", len(ROWS))

# 自检
assert len(ROWS) == 20
from collections import Counter
cnt = Counter(x[14] for x in ROWS)
print("评级分布:", dict(cnt))
print("姓氏证据成立:", sum(1 for x in ROWS if x[9].startswith("✓")))
print("不同姓:", sum(1 for x in ROWS if x[9].startswith("✗")))
print("无掩码:", sum(1 for x in ROWS if x[9].startswith("无掩码")))
print("常见姓:", sum(1 for x in ROWS if x[9].startswith("≈")))
print("主体失效:", [x[0] for x in ROWS if x[10] in ("注销", "吊销")])
