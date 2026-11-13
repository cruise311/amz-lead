#!/usr/bin/env python3
"""
amz-lead 补号层命令行入口

用法：
    amz-lookup --input data/processed/leads_resolved.csv --output data/enriched/leads_enriched.csv
    amz-lookup --build-batches --input data/enriched/leads_enriched.csv --batch-size 50
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amz_lead.lookup.tungee_runner import TungeeRunner
from amz_lead.lookup.build_batches import BatchBuilder
from amz_lead.lookup.backfill_excel import ExcelBackfiller
from amz_lead.utils.config import load_settings
from amz_lead.utils.logging import setup_logging

logger = setup_logging(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="Phone Number Enrichment via Tanji/TYC/QCC")
    p.add_argument("-i", "--input", required=True, help="输入 CSV/XLSX 文件（含 resolved 主体名）")
    p.add_argument("-o", "--output", help="输出文件路径")
    p.add_argument("--config", default="configs/settings.yaml", help="配置文件")
    p.add_argument("--batch-size", type=int, default=10, help="单批解锁数量（探迹限制）")
    p.add_argument("--max-batches", type=int, default=0, help="最大批次数（0=不限制）")
    p.add_argument("--build-batches", action="store_true", help="生成外呼工单分批文件")
    p.add_argument("--backfill", action="store_true", help="将结果回填到 Excel 模板")
    p.add_argument("--template", help="回填用的 Excel 模板路径")
    p.add_argument("--dry-run", action="store_true", help="试运行：只统计可解锁数量，不实际解锁")
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
        output_path = input_path.parent / f"{input_path.stem}_enriched.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        logger.info("试运行模式：统计可解锁数量...")
        runner = TungeeRunner(settings, dry_run=True)
        stats = runner.count_unlockable(input_path)
        logger.info(f"可解锁: {stats['unlockable']}/{stats['total']} 条")
        return

    if args.build_batches:
        logger.info("生成外呼工单分批...")
        builder = BatchBuilder(settings)
        builder.build(input_path, output_path.parent / "batches", batch_size=args.batch_size)
        logger.info("分批完成")
        return

    if args.backfill:
        if not args.template:
            logger.error("回填模式需要 --template 指定 Excel 模板")
            sys.exit(1)
        logger.info(f"回填到 Excel 模板: {args.template}")
        backfiller = ExcelBackfiller(settings)
        backfiller.backfill(input_path, Path(args.template), output_path)
        logger.info(f"回填完成: {output_path}")
        return

    # 标准补号流程
    logger.info(f"开始补号: {input_path} -> {output_path}")
    runner = TungeeRunner(
        settings=settings,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
    )
    runner.process_file(input_path, output_path)
    logger.info(f"补号完成: {output_path}")


if __name__ == "__main__":
    main()