#!/usr/bin/env python3
"""
build_batches.py — 工商反查名单预处理
输入: 公司全称名单 txt（每行一个，# 开头为注释/跳过）
输出: <输出目录>/ 下生成
  - keywords.json   关键词映射 [{full, kw, skip?}]
  - batch_001.txt   每批 10 个关键词的逗号分隔粘贴串
  - batches.json    每批包含的公司 [{batch, kws: [{kw, full}]}]
用法:
  python3 build_batches.py 名单.txt 输出目录 [每批数量=10]
"""
import json
import os
import re
import sys

try:  # Windows 默认 GBK，输出 ✓ → 重定向到文件会 UnicodeEncodeError
    from _console import setup as _utf8_stdio
    _utf8_stdio()
except Exception:
    pass

# 探迹单关键词上限
MAX_KW_LEN = 10

# 常见结尾公司类型后缀（从长到短匹配）
SUFFIXES = [
    "股份有限公司", "有限责任公司", "有限公司",
    "合伙企业（普通合伙）", "合伙企业(普通合伙)", "合伙企业",
    "（普通合伙）", "(普通合伙)",
    "经营部", "服务中心", "服务部", "商贸部", "电子商务商行",
    "日用品商行", "百货商行", "商行", "贸易部", "营业部",
    "电子商务工作室", "工作室", "中心", "厂",
]

# 开头行政区划前缀
PREFIXES = [
    "内蒙古自治区", "广西壮族自治区", "新疆维吾尔自治区", "宁夏回族自治区",
    "西藏自治区", "黑龙江省", "黑龙江省",
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽",
    "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "宁夏", "新疆", "西藏",
    "郑州", "洛阳", "开封", "安阳", "新乡", "南阳", "许昌",
    "广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆",
    "江门", "茂名", "惠州", "梅州", "汕尾", "河源", "阳江", "清远",
    "东莞", "中山", "潮州", "揭阳", "云浮",
    "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州",
    "舟山", "台州", "丽水", "义乌", "诸暨", "慈溪", "余姚", "乐清",
    "南京", "苏州", "无锡", "常州", "南通", "徐州", "扬州", "镇江",
    "泰州", "盐城", "淮安", "连云港", "宿迁", "昆山",
    "成都", "绵阳", "德阳", "宜宾", "泸州", "南充",
    "福州", "厦门", "泉州", "漳州", "莆田", "三明", "南平", "龙岩", "宁德",
    "济南", "青岛", "淄博", "烟台", "潍坊", "临沂", "济宁", "泰安",
    "威海", "日照", "德州", "聊城", "滨州", "菏泽", "东营", "枣庄",
    "高密", "寿光", "即墨", "胶州", "平度", "莱州", "龙口", "海丰", "陆丰",
    "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感",
    "荆州", "黄冈", "咸宁", "随州", "仙桃", "潜江", "天门",
    "长沙", "株洲", "湘潭", "衡阳", "岳阳", "常德", "张家界", "益阳",
    "郴州", "永州", "怀化", "娄底", "邵阳",
    "石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口",
    "承德", "沧州", "廊坊", "衡水",
    "郑州", "太原", "西安", "兰州", "西宁", "银川", "海口", "三亚",
    "南宁", "柳州", "桂林", "贵阳", "昆明", "南昌", "赣州", "九江",
    "合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆",
    "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城",
    "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新",
    "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛",
    "长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城",
    "哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春",
    "佳木斯", "七台河", "牡丹江", "黑河", "绥化",
]


def strip_brackets(name: str) -> str:
    """去掉名称中部的括号行政区划段。"""
    return re.sub(r"[（(][^（）()]{1,8}[)）]", "", name)


# 行政区划后缀字（剥前缀时与其组合，如 "深圳"+"市"="深圳市" 一起剥）
ADMIN_TAILS = ("省", "市", "县", "区", "自治州", "地区", "盟", "自治县", "旗")


def strip_prefix(name: str) -> str:
    """剥开头行政区划前缀（含省/市/县等后缀字整体剥），仅当剥后剩余 >= 4 字符。取最长匹配。"""
    best = None
    for p in PREFIXES:
        for tail in ("",) + ADMIN_TAILS:
            cand = p + tail
            if name.startswith(cand) and len(name) - len(cand) >= 4:
                if best is None or len(cand) > len(best):
                    best = cand
    if best:
        return name[len(best):]
    return name


def strip_suffix(name: str) -> str:
    """剥结尾公司类型后缀，仅当剥后剩余 >= 4 字符。"""
    for s in SUFFIXES:
        if name.endswith(s) and len(name) - len(s) >= 4:
            return name[:-len(s)]
    return name


def make_keyword(full: str) -> str:
    """公司全称 → 探迹查询关键词（≤10 字符）。

    策略：先剥括号与公司类型后缀；≤10 字符则保留行政区划前缀（唯一性更好）；
    超长才剥行政区划前缀；仍超长则截断。
    """
    kw = strip_brackets(full)
    kw = strip_suffix(kw)
    if len(kw) <= MAX_KW_LEN:
        return kw
    kw2 = strip_prefix(kw)
    if len(kw2) <= MAX_KW_LEN:
        return kw2
    return kw2[:MAX_KW_LEN]


def is_skippable(full: str) -> bool:
    """判断是否为无法查询的主体（英文/拼音/过短）。"""
    if len(full) < 4:
        return True
    # 全英文字母
    if re.fullmatch(r"[A-Za-z0-9\s&\.\-'()]+", full):
        return True
    return False


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    src, outdir = sys.argv[1], sys.argv[2]
    batch_size = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    os.makedirs(outdir, exist_ok=True)

    entries, seen = [], set()
    for line in open(src, encoding="utf-8"):
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        if name in seen:
            continue
        seen.add(name)
        if is_skippable(name):
            entries.append({"full": name, "kw": "", "skip": True})
            continue
        entries.append({"full": name, "kw": make_keyword(name)})

    with open(os.path.join(outdir, "keywords.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)

    active = [e for e in entries if not e.get("skip")]
    batches = []
    for i in range(0, len(active), batch_size):
        chunk = active[i:i + batch_size]
        idx = i // batch_size + 1
        paste = ",".join(e["kw"] for e in chunk)
        with open(os.path.join(outdir, f"batch_{idx:03d}.txt"), "w", encoding="utf-8") as f:
            f.write(paste)
        batches.append({"batch": idx, "kws": [{"kw": e["kw"], "full": e["full"]} for e in chunk]})

    with open(os.path.join(outdir, "batches.json"), "w", encoding="utf-8") as f:
        json.dump(batches, f, ensure_ascii=False, indent=1)

    print(f"总条目 {len(entries)}（可查 {len(active)}，跳过 {len(entries)-len(active)}）")
    print(f"生成 {len(batches)} 批 → {outdir}/")
    for b in batches:
        print(f"  batch_{b['batch']:03d}.txt: {b['kws'][0]['kw']} ... 共{len(b['kws'])}个")


if __name__ == "__main__":
    main()
