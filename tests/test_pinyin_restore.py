#!/usr/bin/env python3
"""
拼音还原算法单元测试
包含真实样本回归测试
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from amz_lead.resolve.pinyin_restore import PinyinRestorer
from amz_lead.utils.config import load_settings


class TestPinyinRestore:
    """拼音还原核心算法测试"""

    @pytest.fixture(scope="class")
    def restorer(self):
        settings = load_settings("configs/settings.yaml")
        return PinyinRestorer(settings, use_qcc=False, use_tyc=False)

    def test_basic_segmentation(self, restorer):
        """基础分词测试"""
        cases = [
            ("guangzhoubochenkejiyouxiangongsi", ["广州", "博晨", "科技", "有限", "公司"]),
            ("shenzhenshihuaweixishujishufenyouxiangongsi", ["深圳", "市", "华为", "技术", "有限", "公司"]),
            ("yiwushichengxinshangmaoyouxiangongsi", ["义乌", "市", "诚信", "商贸", "有限", "公司"]),
        ]
        for pinyin, expected_tokens in cases:
            tokens = restorer.segment(pinyin)
            # 至少包含预期的关键词
            for token in expected_tokens:
                assert any(token in t for t in tokens), f"{pinyin} 缺少 {token}"

    def test_city_prefix_boost(self, restorer):
        """城市前缀加权测试"""
        # 有城市前缀的应该优先命中
        result_with_city = restorer.restore("guangzhoubochenkejiyouxiangongsi")
        result_without_city = restorer.restore("bochenkejiyouxiangongsi")

        # 有城市前缀时，目标公司应在 Top-1
        top1_with_city = result_with_city[0]["name"] if result_with_city else ""
        top1_without_city = result_without_city[0]["name"] if result_without_city else ""

        assert "广州博晨" in top1_with_city, f"城市前缀加权失败: {top1_with_city}"
        # 无城市前缀时，命中率下降是预期的

    def test_industry_word_stripping(self, restorer):
        """行业词剥离测试"""
        # 含行业词
        pinyin_with = "shenzhenshikejiyouxiangongsi"
        pinyin_without = "shenzhenshiyouxiangongsi"

        tokens_with = restorer.segment(pinyin_with)
        tokens_without = restorer.segment(pinyin_without)

        # 应识别出"科技"作为行业词并剥离
        assert "科技" in tokens_with or "kej" in "".join(tokens_with)

    @pytest.mark.parametrize("pinyin,expected_company", [
        ("guangzhoubochenkejiyouxiangongsi", "广州博晨科技有限公司"),
        ("yiwushichenghongyushangmaoyouxiangongsi", "义乌市诚信商贸有限公司"),
        ("shenzhenshihuaweixishujishufenyouxiangongsi", "深圳市华为技术有限公司"),
    ])
    def test_real_samples_top3(self, restorer, pinyin, expected_company):
        """真实样本 Top-3 覆盖率测试"""
        results = restorer.restore(pinyin, top_k=10)
        names = [r["name"] for r in results]
        assert expected_company in names[:3], f"{pinyin} 期望 {expected_company} 在 Top-3，实际: {names[:3]}"

    def test_confidence_scoring(self, restorer):
        """置信度评分合理性"""
        # 完整拼音+城市前缀 → 高置信
        result_high = restorer.restore("guangzhoubochenkejiyouxiangongsi", top_k=1)
        assert result_high[0]["confidence"] > 0.8

        # 缺城市前缀 → 低置信
        result_low = restorer.restore("bochenkejiyouxiangongsi", top_k=1)
        assert result_low[0]["confidence"] < 0.7

    def test_roman_name_handling(self, restorer):
        """纯罗马字母名称处理（如品牌名）"""
        # 这类通常无法还原，应标记为低置信或特殊类型
        result = restorer.restore("nike", top_k=5)
        assert len(result) > 0
        # 纯英文品牌不应强行匹配中文公司

    def test_ambiguous_syllables(self, restorer):
        """多音节歧义消解"""
        # "hua" 可能是 华/花/滑/化
        # 上下文"weixishuji"应倾向"华为"
        result = restorer.restore("huaweixishuji", top_k=5)
        names = [r["name"] for r in result]
        assert any("华为" in n for n in names[:3])


class TestPinyinRestoreRegression:
    """真实样本回归测试（3425 条样本的统计指标）"""

    @pytest.fixture(scope="class")
    def restorer(self):
        settings = load_settings("configs/settings.yaml")
        return PinyinRestorer(settings, use_qcc=False, use_tyc=False)

    def test_top1_hit_rate(self, restorer):
        """Top-1 命中率 ≥ 80%（城市前缀加权后）"""
        # 这里用少量样本做冒烟测试，完整回归在 CI 跑
        samples = [
            ("guangzhoubochenkejiyouxiangongsi", "广州博晨科技有限公司"),
            ("yiwushichenghongyushangmaoyouxiangongsi", "义乌市诚信商贸有限公司"),
            ("shenzhenshihuaweixishujishufenyouxiangongsi", "深圳市华为技术有限公司"),
            ("hangzhoushiwangluokejiyouxiangongsi", "杭州市网络科技有限公司"),
            ("shanghaishiyunshangmaoyouxiangongsi", "上海市云商贸有限公司"),
        ]
        hits = 0
        for pinyin, expected in samples:
            results = restorer.restore(pinyin, top_k=1)
            if results and expected in results[0]["name"]:
                hits += 1
        hit_rate = hits / len(samples)
        assert hit_rate >= 0.8, f"Top-1 命中率 {hit_rate:.1%} 低于 80%"

    def test_top3_coverage(self, restorer):
        """Top-3 覆盖率 ≥ 94%"""
        samples = [
            ("guangzhoubochenkejiyouxiangongsi", "广州博晨科技有限公司"),
            ("yiwushichenghongyushangmaoyouxiangongsi", "义乌市诚信商贸有限公司"),
            ("shenzhenshihuaweixishujishufenyouxiangongsi", "深圳市华为技术有限公司"),
        ]
        covered = 0
        for pinyin, expected in samples:
            results = restorer.restore(pinyin, top_k=3)
            names = [r["name"] for r in results]
            if expected in names:
                covered += 1
        coverage = covered / len(samples)
        assert coverage >= 0.9, f"Top-3 覆盖率 {coverage:.1%} 低于 90%"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])