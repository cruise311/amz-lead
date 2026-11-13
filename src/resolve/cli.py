#!/usr/bin/env python3
"""
amz-lead 还原层命令行入口

用法：
    amz-resolve --input data/raw/leads_raw.csv --output data/processed/leads_resolved.csv
    amz-resolve --rescore --input data/processed/leads_resolved.csv
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amz_lead.resolve.pinyin_restore import PinyinRestorer
from amz_lead.resolve.leads_score import LeadScorer
from amz_lead.utils.config import load_settings
from amz_lead.utils.logging import setup_logging

logger = setup_logging(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="Amazon Seller Entity Resolution & Scoring")
    p.add_argument("-i", "--input", required=True, help="输入 CSV 文件")
    p.add_argument("-o", "--output", help="输出 CSV 文件")
    p.add_argument("--config", default="configs/settings.yaml", help="配置文件")
    p.add_argument("--rescore", action="store_true", help="仅重新评分（输入需含 resolved 字段）")
    p.add_argument("--batch-size", type=int, default=500, help="批处理大小")
    p.add_argument("--no-qcc", action="store_true", help="禁用企查查核验")
    p.add_argument("--no-tyc", action="store_true", help="禁用天眼查核验")
    return p.parse_args()


def main():
    args = parse_args()
    settings = load_settings(args.config)

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"输入文件不存在: {input_path}")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.parent / f"{input_path.stem}_resolved.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.rescore:
        # 仅重新评分
        logger.info("重新评分模式...")
        scorer = LeadScorer(settings)
        scorer.rescore_file(input_path, output_path)
        logger.info(f"评分完成: {output_path}")
        return

    # 完整还原流程
    logger.info(f"开始还原: {input_path} -> {output_path}")
    restorer = PinyinRestorer(
        settings=settings,
        use_qcc=not args.no_qcc,
        use_tyc=not args.no_tyc,
        batch_size=args.batch_size,
    )
    restorer.process_file(input_path, output_path)
    logger.info(f"还原完成: {output_path}")

    # 自动评分
    logger.info("自动评分...")
    scorer = LeadScorer(settings)
    scorer.score_file(output_path, output_path)
    logger.info(f"评分完成: {output_path}")


if __name__ == "__main__":
    main()