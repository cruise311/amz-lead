#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
亚马逊卖家店铺名（罗马化）→ 中文工商主体候选还原  v2

输入：xlsx，需含「店铺公司名」「店铺公司地址」两列（列名容错）
输出：xlsx，两个 sheet
  1) 候选还原   每条一个切分结果 + top-N 中文全称候选 + 可查性分级
  2) 地址检索点 按「路+门牌」聚类的同址店铺组，供网页版高级搜索逐个攻破

v2 关键修正：
  地址栏大量是碎片（"泰叶路159弄33号"），没有"市/区"，所以城市不能靠地址取。
  改为：用合法音节表对拼音串做最长匹配切分，前 2 个音节（或 xxs hi 三个）判定为城市前缀。
  城市只需知道"在哪切"，不需要猜出正确汉字——检索串不带城市反而命中面更宽。

设计原则：
  脚本只做「确定性切分 + 候选生成」，命中与否交给企查查/天眼查搜索验证。
  脚本不做任何假的相似度打分，候选列是"待验证假设"，不是结论。
"""
import re
import sys
import itertools
from collections import defaultdict

import pandas as pd
from pypinyin import lazy_pinyin

# ---------- 组织形式词（从尾部剥，长的先匹配）----------
ORG_WORDS = [
    "youxianzerengongsi", "qiyeyouxiangongsi", "youxiangongsi", "youxiangong",
    "gongsi", "shanghang", "jingyingbu", "gongzuoshi", "hehuoqiye",
    "limitedliabilitycompany", "companylimited", "company", "limited",
    "corporation", "incorporated", "trading", "store", "firm", "group",
    "ltd", "inc", "co", "llc", "corp",
]

# ---------- 行业 / 领域词（可叠加，如 wenhua + keji）----------
IND_WORDS = [
    "dianzishangwu", "wangluokeji", "dianzikeji", "shangwuxinxi", "gongyinglian",
    "wenhuachuanmei", "jishukaifa", "xinxijishu", "dianzishang",
    "shangmao", "maoyi", "keji", "shiye", "gongmao", "chuanmei", "wenhua",
    "zixun", "guanli", "touzi", "jiaoyu", "guanggao", "zhubao", "shoushi",
    "shipin", "fushi", "xieye", "piju", "jiaju", "meizhuang", "rihua",
    "riyongpin", "wujin", "jixie", "shebei", "dianqi", "qiche", "peijian",
    "yiyao", "baojian", "nongye", "jiancai", "zhuangshi", "ruanjian",
    "wangluo", "xinxi", "jishu", "shangwu", "jinkou", "chukou",
    "ecommerce", "technology", "commerce", "industrial", "industry",
    "jewelry", "food", "garment", "textile", "electronics", "machinery",
]

# ---------- 企业字号高频用字（人工排序，仅用于同音字排序，不影响切分）----------
CURATED = (
    "鑫盛达通联泰恒嘉诚信宏伟华兴隆昌源丰聚汇众合优品美雅尚潮欧亚星云海天青"
    "木森林金银宝捷速航舟帆桥智创新科技数码电子商贸进出口供应链仓储物流跨境"
    "环球世界国东南西北中龙凤鹏鹰骏腾飞跃翔辉耀晨曦阳明光影色彩锦绣丝绸"
    "亿万千百十利来福祥瑞吉庆喜乐安康宁和顺遂成成功名仕"
    "凯特威派领创拓探索求真务实精益臻极志高卓越大匠工坊"
    "邦讯动互易佰寻瀚通四海九州五洲宇浩轩朗清泉佳家加贝宝儿儿童趣玩玩家居"
    "生活日用百货商店铺号社团体米果糖果茶酒饮食味厨房厨具家电家具灯饰布艺"
    "服饰鞋帽箱包皮具饰品普瑞维尔克斯伦迪卡纳维拉蒂芙兰蔻姿诗黛雀羚相宜本草"
    # 音译/虚词用字：不加这些，"通之联"这种三字字号永远排不进候选
    "之与和而尔斯维卡纳拉蒂普瑞特琳娜妮莎贝菈洛桑塞诺德曼斯提姆珀伦"
    # 常见字号字补表（按大致频率），缺了这块 bochen 只会出"剥郴"这种废字
    "博辰臣城承川传春纯恩发方峰锋富高歌格古广涵汉好禾弘红虹鸿湖虎化皇惠火"
    "集建剑江杰晶景敬久巨君开可空快坤蓝雷力立丽良亮灵流陆路罗玛迈麦茂魅门"
    "梦密绵妙民铭摩牧南妮派平奇琪启前强巧秋全群然润赛三山少申深神升圣胜师石"
    "时世市首书舒双水硕思松苏素速索特提田同统图途万旺微卫未稳沃五希熙喜系先"
    "现祥向象小心星行兄雄秀旭玄旋学雪迅延研洋姚业叶一伊仪宜义艺意毅引印应英"
    "盈营永游友有羽雨语玉育元园远苑月悦越允运泽展战长昭兆哲珍振正知制质竹主"
    "筑子紫自宗总尊佐选码牌族家纺纺器玩创游娱影音视"
)
CURATED = list(dict.fromkeys(CURATED))

# 英文噪声词：出现在罗马化中文名里（Commercial Development / Business Office 等），
# 和字号无关，不剥掉会导致整串切不出音节
EN_WORDS = [
    "commercial", "development", "business", "office", "district", "county",
    "international", "enterprise", "holding", "industrial", "science",
    "digital", "culture", "media", "supply", "chain", "logistics", "import",
    "export", "service", "solution", "management", "consulting", "investment",
    "capital", "innovation", "technology", "commerce", "trading", "trade",
    "jewelry", "garment", "textile", "electronics", "machinery", "network",
    "ecommerce", "store", "shop", "firm", "studio", "workshop", "city",
]

# 只做「拼接前」剔除的地理/修饰噪声（不含主体类型词）
NOISE = {
    "city", "district", "county", "commercial", "development", "business",
    "office", "international", "enterprise", "holding", "industrial",
    "science", "digital", "culture", "media", "supply", "chain", "logistics",
    "import", "export", "service", "solution", "management", "consulting",
    "investment", "capital", "innovation",
}

# 地址里出现「市/区/县」时才用的行政区后缀
ADMIN_SUFFIX = ("市", "区", "县", "镇")


def norm(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).replace("\u3000", " ")
    s = re.sub(r"[\s\.\,\&\-\_\(\)（）【】\[\]/\\、，。：:；;'\"+]+", "", s)
    return s.lower()


def strip_suffix(word, table, max_iter=1):
    """从尾部剥离词表词，返回 (剩余, 命中列表)"""
    hit = []
    for _ in range(max_iter):
        found = False
        for w in sorted(table, key=len, reverse=True):
            if word.endswith(w) and len(word) > len(w) + 1:
                word, found = word[: -len(w)], True
                hit.append(w)
                break
        if not found:
            break
    return word, hit


def build_pinyin_index():
    """GB2312 一级字 → 拼音反查表，CURATED 字排在同音字最前"""
    rev = defaultdict(list)
    for b1 in range(0xB0, 0xF8):
        for b2 in range(0xA1, 0xFF):
            try:
                c = bytes([b1, b2]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            try:
                py = lazy_pinyin(c)[0]
            except Exception:
                continue
            if py and c not in rev[py]:
                rev[py].append(c)
    # CURATED 优先（CURATED 里可能有不属于 GB2312 的字，一并加入）
    ranked = defaultdict(list)
    for c in CURATED:
        try:
            py = lazy_pinyin(c)[0]
        except Exception:
            continue
        if c not in ranked[py]:
            ranked[py].append(c)
    for py, lst in rev.items():
        for c in lst:
            if c not in ranked[py]:
                ranked[py].append(c)
    return ranked


REV = build_pinyin_index()
SYLLABLES = set(REV.keys())          # 合法音节全集（约 400 个）
MAX_SYL = max(len(s) for s in SYLLABLES)


def split_syllables(word):
    """
    拼音串 → 音节序列。必须带回溯，不能用贪心：
    "kuningmeng"(酷宁梦) 贪心会取 kun+ing 直接失败，回溯后才能得 ku+ning+meng。
    """
    if not word:
        return None
    n = len(word)
    memo = {}

    def go(i):
        if i == n:
            return ()
        if i in memo:
            return memo[i]
        memo[i] = None
        for L in range(min(MAX_SYL, n - i), 0, -1):
            seg = word[i:i + L]
            if seg in SYLLABLES:
                rest = go(i + L)
                if rest is not None:
                    memo[i] = (seg,) + rest
                    return memo[i]
        return None

    r = go(0)
    return list(r) if r is not None else None


def clean_tokens(raw):
    """
    按空格/标点切词，丢掉英文噪声词（City / Commercial / Development / Co / Ltd…）。
    必须在拼接前按词丢——"Yiwu City Zhenfei" 里的 city 在中间，
    只做后缀剥离是剥不掉的。
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    parts = re.split(r"[\s\.\,\&\-\_\(\)（）【】\[\]/\\、，。：:；;'\"+]+", str(raw))
    # 只丢「纯地理/修饰噪声词」。不能丢 ltd/co/trading/store/firm——
    # 它们是判断"是不是公司主体"的证据，丢了会把公司误判成个人卖家。
    keep = [p for p in parts if p and p.lower() not in NOISE]
    return "".join(keep).lower()


# 三音节及以上的城市（拼音前缀），用于修正"前两音节=城市"的默认规则
LONG_CITY = {
    "shijiazhuang", "wulumuqi", "huhehaote", "haerbin", "qiqihaer",
    "hulunbeier", "xishuangbanna", "akesu", "kashi", "eerduosi",
}


def addr_admin(addr):
    """地址里能识别的行政区名（中文 + 拼音），可能为空"""
    if addr is None or (isinstance(addr, float) and pd.isna(addr)):
        return []
    a = str(addr)
    out = []
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,5})(市|区|县|镇|街道)", a):
        cn = m.group(1)
        out.append((cn + m.group(2), "".join(lazy_pinyin(cn + m.group(2))),
                    "".join(lazy_pinyin(cn))))
    return out


def cut_city(syls, addr):
    """从音节序列前面切掉城市，返回 (字号音节, 城市拼音)"""
    if not syls:
        return [], ""
    joined = "".join(syls)
    # 1) 地址里的行政区拼音，最长优先
    cands = []
    for cn, py_full, py_short in addr_admin(addr):
        cands += [py_full, py_short]
    for c in sorted(set(cands), key=len, reverse=True):
        if c and joined.startswith(c):
            n = len(split_syllables(c) or [])
            if 0 < n < len(syls):
                return syls[n:], c
    # 2) 长城市名
    for c in LONG_CITY:
        if joined.startswith(c):
            n = len(split_syllables(c) or [])
            if 0 < n < len(syls):
                return syls[n:], c
    # 3) 默认：前 2 音节 = 城市；若第 3 音节是 shi/xian 则取 3
    if len(syls) >= 4:
        if syls[2] in ("shi", "xian") and len(syls) >= 5:
            return syls[3:], "".join(syls[:3])
        return syls[2:], "".join(syls[:2])
    return syls, ""


def gen_candidates(syls, topk=8, topn=16):
    """
    候选生成：以「每音节首字」为基准，逐个位置替换其余同音字。
    不用 itertools.product——那样所有候选会锁死首字，全是"剥郴/剥臣/剥辰"这种同一前缀的废品。
    真实字号通常与最高频组合只差 1-2 个字，汉明距离采样才是对的策略。
    """
    if not syls:
        return []
    k = topk if len(syls) <= 2 else max(4, topk - 3)
    pools = []
    for s in syls:
        pool = REV.get(s, [])[:k]
        if not pool:
            return []
        pools.append(pool)

    base = [p[0] for p in pools]
    out = ["".join(base)]
    for pos in range(len(pools)):
        for alt in pools[pos][1:]:
            combo = base.copy()
            combo[pos] = alt
            out.append("".join(combo))
            if len(out) >= topn:
                return out
    return out


CODE_RE = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{18}$")   # 统一社会信用代码
REGNO_RE = re.compile(r"^\d{15}$")                    # 老注册号
GANGTAI = ("hongkong", "hk", "香港", "taiwan", "tw", "台湾", "macau", "macao", "澳门")

# 港台判断必须带词边界。用子串匹配会把 "EASTWEST" 里的 "tw"、"Shanghai" 里的 "hk" 当成
# 港台主体——2026-11-06 实测 3425 条名单里 EASTWEST ELECTRONIC COMMERCE CO US LIMITED
# 就被误判成 L5（实际是美国主体）。误判的代价是整条线索被直接丢弃，且不会有人复核。
GANGTAI_RE = re.compile(
    r"hong\s*kong|\bhongkong\b|香港|\bhk\b|taiwan|\btw\b|台湾|macau|macao|澳门", re.I
)
TW_ADDR = ("台东", "台北", "新北", "台中", "台南", "高雄", "桃园", "新竹", "彰化", "屏东")

IND_CN = {
    "dianzishangwu": "电子商务", "dianzishang": "电商", "maoyi": "贸易",
    "keji": "科技", "shangmao": "商贸", "shiye": "实业", "wangluokeji": "网络科技",
    "zhubao": "珠宝", "shipin": "食品", "gongyinglian": "供应链",
    "wenhua": "文化", "chuanmei": "传媒", "shoushi": "首饰", "fushi": "服饰",
    "jewelry": "珠宝", "trading": "贸易", "technology": "科技",
}

# 城市拼音 → 汉字。检索串必须带城市，否则「博晨科技」会被全国同名主体淹没，
# 企查查接口一次只回 5 个候选，真目标直接掉出视野——这是命中率被低估的主因之一。
CITY_CN = {
    "yiwu": "义乌", "guangzhou": "广州", "shenzhen": "深圳", "dongguan": "东莞",
    "shanghai": "上海", "hangzhou": "杭州", "jinhua": "金华", "xiamen": "厦门",
    "fuzhou": "福州", "quanzhou": "泉州", "putian": "莆田", "wuhan": "武汉",
    "zhengzhou": "郑州", "qingdao": "青岛", "shaoxing": "绍兴", "tianjin": "天津",
    "hefei": "合肥", "changsha": "长沙", "wuzhou": "梧州", "nanjing": "南京",
    "suzhou": "苏州", "wuxi": "无锡", "ningbo": "宁波", "wenzhou": "温州",
    "foshan": "佛山", "zhongshan": "中山", "huizhou": "惠州", "shantou": "汕头",
    "zhuhai": "珠海", "jiangmen": "江门", "chengdu": "成都", "chongqing": "重庆",
    "xian": "西安", "kunming": "昆明", "nanning": "南宁", "guiyang": "贵阳",
    "lanzhou": "兰州", "taiyuan": "太原", "shijiazhuang": "石家庄", "baoding": "保定",
    "langfang": "廊坊", "tangshan": "唐山", "shenyang": "沈阳", "dalian": "大连",
    "changchun": "长春", "haerbin": "哈尔滨", "jinan": "济南", "yantai": "烟台",
    "weifang": "潍坊", "linyi": "临沂", "zibo": "淄博", "nanchang": "南昌",
    "ganzhou": "赣州", "jiujiang": "九江", "yichun": "宜春", "shangrao": "上饶",
    "xuzhou": "徐州", "nantong": "南通", "yangzhou": "扬州", "changzhou": "常州",
    "zhenjiang": "镇江", "taizhou": "台州", "jiaxing": "嘉兴", "huzhou": "湖州",
    "quzhou": "衢州", "lishui": "丽水", "zhuji": "诸暨", "haining": "海宁",
    "tongxiang": "桐乡", "cixi": "慈溪", "yuyao": "余姚", "leqing": "乐清",
    "ruian": "瑞安", "wenling": "温岭", "yongkang": "永康", "dongyang": "东阳",
    "pujiang": "浦江", "lanxi": "兰溪", "jiangyin": "江阴", "changshu": "常熟",
    "kunshan": "昆山", "zhangjiagang": "张家港", "taicang": "太仓", "yixing": "宜兴",
    "jintan": "金坛", "liyang": "溧阳", "rugao": "如皋", "haimen": "海门",
    "qidong": "启东", "shishi": "石狮", "jinjiang": "晋江", "nanan": "南安",
    "huian": "惠安", "anxi": "安溪", "dehua": "德化", "longyan": "龙岩",
    "sanming": "三明", "nanping": "南平", "ningde": "宁德", "fuan": "福安",
    "pingyixian": "平邑", "huaibei": "淮北", "loudi": "娄底", "shuoxing": "",
}


def city_cn(city_py):
    if not city_py:
        return ""
    # 坑：python 的 "shi" 是 3 个字母。写成 [:-2] 会得到 "shenzhensh"，查表必空，
    # 于是「深圳市」开头的拼音名全被生成裸字号检索串（无城市前缀）——全国同名主体一淹，
    # 命中率被系统性低估。2026-11-06 实测：2046 条 L2/L2E 里 shenzhenshi 类全中这个坑。
    c = city_py[:-3] if city_py.endswith("shi") else city_py
    return CITY_CN.get(c, CITY_CN.get(city_py, ""))


def classify(name_raw, addr):
    raw = "" if name_raw is None or (isinstance(name_raw, float) and pd.isna(name_raw)) else str(name_raw).strip()
    n = clean_tokens(raw)

    if CODE_RE.match(n.upper()) or REGNO_RE.match(n):
        return dict(level="L1", note="名称即统一社会信用代码，直接查，无需还原",
                    city="", zihao="", ind="", org="", cands=[], query=n.upper())

    if GANGTAI_RE.search(n) or (addr and any(t in str(addr) for t in TW_ADDR)):
        return dict(level="L5", note="港台/海外主体，内地工商库无此主体，走境外库",
                    city="", zihao="", ind="", org="", cands=[], query="")

    # 英文式主体先走专用路径。必须用 raw 而不是 clean_tokens 之后的结果：
    # clean_tokens 会把 PRODUCTS / TRADING / TECHNOLOGY 这些词当噪声删掉并把剩余词粘成一串，
    # 英文的词边界一丢，城市和字号就再也分不开了（2026-11-06 实测：L2E 直接归零）。
    en = classify_en(raw, addr)
    if en:
        return en

    body, org = strip_suffix(n, ORG_WORDS, max_iter=2)
    body, inds = strip_suffix(body, IND_WORDS, max_iter=3)
    # 再剥英文噪声词（Commercial Development / Business Office / District 等）
    body, en = strip_suffix(body, EN_WORDS, max_iter=10)
    inds = inds + en

    if not org:
        return dict(level="L4", note="无组织形式词，疑似个人卖家主体，走法人姓名反查",
                    city="", zihao=body, ind="+".join(inds), org="", cands=[], query="")

    syls = split_syllables(body)
    if not syls:
        return dict(level="L4", note="字号段不是合法拼音（疑似英文品牌名或采集端截断），走品牌/商标反查",
                    city="", zihao=body, ind="+".join(inds), org="+".join(org),
                    cands=[], query="")

    zihao_syls, city = cut_city(syls, addr)
    if not zihao_syls:
        return dict(level="L3", note="切掉了城市后字号为空，名称结构异常",
                    city=city, zihao="", ind="+".join(inds), org="+".join(org),
                    cands=[], query="")

    cands = gen_candidates(zihao_syls)
    if not cands:
        return dict(level="L3", note="字号音节含非常用音，无法映射汉字",
                    city=city, zihao="".join(zihao_syls), ind="+".join(inds),
                    org="+".join(org), cands=[], query="")

    ind_cn = IND_CN.get(inds[0], "") if inds else ""
    c_cn = city_cn(city)
    queries = [f"{c_cn}{c}{ind_cn}" for c in cands[:5]] or [f"{c_cn}{c}" for c in cands[:5]]
    return dict(level="L2", note="切分成功，候选需搜索验证（候选是假设不是结论）",
                city=city, zihao="".join(zihao_syls), ind="+".join(inds),
                org="+".join(org), cands=cands, query=" / ".join(queries))


# ---------- 英文式主体（WUXI XINYA SAFETY PRODUCTS CO., LTD）----------
# 这类名字不是拼音串，是「城市拼音 + 字号拼音 + 英文行业词」，3425 条名单里占 L4 的 15%。
# 不能直接送回拼音管线（SAFETY PRODUCTS 永远切不出音节），必须先按英文行业词切开。
EN_IND = {
    "product": "产品", "products": "产品", "safety": "安全", "trading": "贸易",
    "trade": "贸易", "technology": "科技", "technologies": "科技",
    "electronic": "电子", "electronics": "电子", "commerce": "电子商务",
    "ecommerce": "电子商务", "industrial": "实业", "industry": "实业",
    "import": "进出口", "export": "进出口", "bamboo": "竹木", "wood": "木制品",
    "garment": "服装", "garments": "服装", "apparel": "服饰", "textile": "纺织",
    "jewelry": "珠宝", "hardware": "五金", "plastic": "塑料", "metal": "金属",
    "machinery": "机械", "packaging": "包装", "packing": "包装", "furniture": "家具",
    "household": "家居", "home": "家居", "appliance": "电器", "appliances": "电器",
    "toy": "玩具", "toys": "玩具", "craft": "工艺", "crafts": "工艺",
    "sport": "体育", "sports": "体育", "outdoor": "户外", "pet": "宠物",
    "beauty": "美容", "cosmetic": "化妆品", "cosmetics": "化妆品", "food": "食品",
    "daily": "日用品", "supplies": "用品", "supply": "用品", "material": "材料",
    "equipment": "设备", "tool": "工具", "tools": "工具", "auto": "汽车",
    "parts": "配件", "accessories": "配件", "accessory": "配件", "bag": "箱包",
    "bags": "箱包", "shoes": "鞋", "fabric": "面料", "printing": "印刷",
    "logistics": "物流", "network": "网络", "digital": "数码", "science": "科技",
    "intelligent": "智能", "hanger": "衣架", "hangers": "衣架", "optic": "光电",
    "lighting": "照明", "solar": "太阳能", "energy": "能源", "medical": "医疗",
}
EN_ORG = {"co", "ltd", "limited", "llc", "inc", "corp", "corporation", "company",
          "incorporated", "ltda", "gmbh", "plc"}


def classify_en(n_raw, addr):
    """
    只处理「城市拼音开头」的英文式主体。处理不了（真美国公司 / 纯品牌名）返回 None，
    让它落到 L4 走商标反查——把两类混在一起会让 L4 看着很大但没法行动。
    """
    tokens = re.findall(r"[A-Za-z]+", n_raw)
    if len(tokens) < 2:
        return None
    low = [t.lower() for t in tokens]
    if not any(t in EN_ORG for t in low):
        return None

    # 城市：首词可能带 shi/区（foshanshinanhaiqu），按最长匹配剥
    t0 = low[0]
    city_key, district = None, ""
    for c in sorted(CITY_CN, key=len, reverse=True):
        if t0 == c:
            city_key = c
            break
        if t0.startswith(c) and len(t0) > len(c) + 3:
            city_key, district = c, t0[len(c):]
            break
    if not city_key:
        return None

    rest = low[1:]
    # 字号 = 城市之后、第一个行业词/组织词之前的部分。
    # 虚词必须跳过——"Import and Export" 里的 and、"E-commerce" 里的 e 若进了字号，
    # 会拼出 "创宏汇平蛾" 这种多一个音节的假名（2026-11-06 实测）。
    STOP = {"and", "of", "the", "for", "with", "a", "an"}
    zihao_toks, inds = [], []
    for t in rest:
        if t in EN_ORG:
            break
        if t in STOP:
            continue
        if t == "e":            # E-commerce 被正则切成 e + commerce
            inds.append("ecommerce")
            continue
        if t in EN_IND or t in IND_WORDS or t in EN_WORDS:
            inds.append(t)
            continue
        if not inds:
            zihao_toks.append(t)
    if not zihao_toks:
        return None
    zihao_raw = "".join(zihao_toks)
    syls = split_syllables(zihao_raw)
    if not syls:
        return None
    cands = gen_candidates(syls)
    if not cands:
        return None
    c_cn = city_cn(city_key)
    ind_cn = EN_IND.get(inds[0], "") if inds else ""
    queries = [f"{c_cn}{c}{ind_cn}" for c in cands[:5]]
    return dict(level="L2E", note="英文式主体（城市+字号拼音+英文行业词），候选需搜索验证",
                city=city_key, zihao=zihao_raw, ind="+".join(inds),
                org="+".join([t for t in rest if t in EN_ORG]), cands=cands,
                query=" / ".join(queries))


def normalize_addr(addr):
    """
    地址归一：只保留「路/街/大道/弄 + 门牌」，没有门牌则退到「街道/镇」片区级。
    省/市前缀一律剥掉——否则「北苑街道」「义乌市北苑街道」「浙江省义乌市北苑街道」
    会被当成三个不同的地方，同址聚类就散了。
    """
    if addr is None or (isinstance(addr, float) and pd.isna(addr)):
        return ""
    a = str(addr).replace("\n", " ").strip()
    # 剥掉开头的省/市（可能多级）
    for _ in range(3):
        a2 = re.sub(r"^[\u4e00-\u9fa5]{2,6}(?:省|市|自治区|自治州)", "", a)
        if a2 == a:
            break
        a = a2
    m = re.search(r"([\u4e00-\u9fa5]{2,10}(?:路|街|大道|道|巷|弄))[\s]*(\d+)\s*号", a)
    if m:
        return f"{m.group(1)}{m.group(2)}号"
    m2 = re.findall(r"([\u4e00-\u9fa5]{2,8}(?:街道|镇))", a)
    if m2:
        return m2[-1]
    m3 = re.search(r"([\u4e00-\u9fa5]{2,10}(?:路|街|大道|道|巷|弄))", a)
    return m3.group(1) if m3 else ""


def main(paths, out):
    frames = []
    for p in paths:
        df = pd.read_excel(p)
        name_col = next((c for c in df.columns if "公司名" in str(c)), None) \
            or next((c for c in df.columns if "店铺" in str(c)), None)
        addr_col = next((c for c in df.columns if "地址" in str(c)), None)
        if name_col is None:
            print(f"[skip] {p} 找不到名称列: {list(df.columns)}")
            continue
        frames.append(pd.DataFrame({
            "店铺公司名": df[name_col],
            "店铺公司地址": df[addr_col] if addr_col is not None else "",
            "来源文件": str(p).split("/")[-1],
        }).dropna(subset=["店铺公司名"]))
    data = pd.concat(frames, ignore_index=True)
    print(f"读入 {len(data)} 条")

    rows = []
    for _, r in data.iterrows():
        res = classify(r["店铺公司名"], r["店铺公司地址"])
        c = (res["cands"] + [""] * 10)[:10]
        rows.append({
            "店铺公司名(原)": r["店铺公司名"],
            "店铺公司地址(原)": r["店铺公司地址"],
            "可查性": res["level"],
            "城市拼音": res["city"],
            "字号拼音": res["zihao"],
            "行业词": res["ind"],
            "组织形式": res["org"],
            **{f"候选{i+1}": c[i] for i in range(10)},
            "推荐检索串": res["query"],
            "说明": res["note"],
            "命中主体": "", "电话": "", "电话标签": "", "状态": "",
        })
    out_df = pd.DataFrame(rows)
    out_df["_addr_key"] = [normalize_addr(a) for a in data["店铺公司地址"]]

    grp = (out_df[out_df["_addr_key"] != ""]
           .groupby("_addr_key")
           .agg(店铺数=("店铺公司名(原)", "size"),
                店铺=("店铺公司名(原)", lambda s: " | ".join(s.astype(str))),
                地址原文=("店铺公司地址(原)", lambda s: str(s.iloc[0])))
           .reset_index().sort_values(["店铺数", "_addr_key"], ascending=[False, True]))
    grp = grp[grp["店铺数"] >= 2].reset_index(drop=True)
    grp.insert(0, "序", range(1, len(grp) + 1))
    grp["级别"] = grp["_addr_key"].str.contains("号").map({True: "楼宇级", False: "片区级"})
    for col in ["在册企业数(待填)", "命中主体(待填)", "电话(待填)", "状态"]:
        grp[col] = ""
    def first_action(row):
        k, lvl, n = row["_addr_key"], row["级别"], row["店铺数"]
        if lvl == "楼宇级":
            return (f"企查查网页版 → 高级搜索 → 注册地址含「{k}」→ 按成立日期倒序，"
                    f"只留电子商务/贸易/科技类主体，与这 {n} 家店铺名逐个对字号")
        return (f"片区级 {n} 家，地址粒度太粗：先在企查查按「{k} + 电子商务/贸易」筛，"
                f"或直接用该片区做扫楼/加本地卖家群，不要逐条查工商")

    grp["第一步动作"] = grp.apply(first_action, axis=1)
    grp["优先级"] = grp["店铺数"].map(lambda n: "优先" if n >= 4 else "次优先")
    grp = grp[["序", "_addr_key", "级别", "店铺数", "优先级", "店铺", "地址原文",
               "第一步动作", "在册企业数(待填)", "命中主体(待填)", "电话(待填)", "状态"]]
    grp = grp.rename(columns={"_addr_key": "归一化地址"})

    with pd.ExcelWriter(out, engine="openpyxl") as w:
        out_df.drop(columns=["_addr_key"]).to_excel(w, sheet_name="候选还原", index=False)
        grp.to_excel(w, sheet_name="地址检索点", index=False)
        from openpyxl.utils import get_column_letter
        for name, widths in (
            ("候选还原", [44, 40, 8, 14, 16, 18, 16] + [9] * 10 + [32, 34, 26, 16, 12, 10]),
            ("地址检索点", [5, 30, 8, 7, 7, 52, 30, 62, 14, 26, 16, 10]),
        ):
            ws = w.sheets[name]
            for i, wd in enumerate(widths, 1):
                ws.column_dimensions[get_column_letter(i)].width = wd
            ws.freeze_panes = "A2"

    print("\n=== 可查性分布 ===")
    for k, v in out_df["可查性"].value_counts().sort_index().items():
        print(f"  {k}: {v} 条 ({v / len(out_df) * 100:.1f}%)")
    print(f"\n=== 地址聚类（≥2 家）: {len(grp)} 组，覆盖 {int(grp['店铺数'].sum())} 条 ===")
    print(grp[["序", "级别", "归一化地址", "店铺数"]].to_string(index=False))
    print(f"\n输出: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: pinyin_restore.py out.xlsx in1.xlsx [in2.xlsx ...]")
        sys.exit(1)
    main(sys.argv[2:], sys.argv[1])
