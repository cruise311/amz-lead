#!/usr/bin/env python3
"""
amz-lead 采集层命令行入口

用法：
    amz-collect --category home-garden --limit 500 --output data/raw/leads_home_garden.csv
    amz-collect --config configs/cats.yaml --mode incremental --days 7
    amz-collect --marketplace com --all-enabled-categories
"""

import argparse
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from amz_lead.collect.bsr_full import BSRCrawler
from amz_lead.collect.bsr_chain import CategoryChain
from amz_lead.collect.resolver_fleet import ResolverFleet
from amz_lead.utils.config import load_settings
from amz_lead.utils.logging import setup_logging

logger = setup_logging(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="Amazon BSR Seller Crawler")
    p.add_argument("-c", "--category", help="单类目 ID（如 home-garden）")
    p.add_argument("--config", default="configs/cats.yaml", help="类目配置文件")
    p.add_argument("--marketplace", default="com", help="站点：com/de/fr/co.uk/co.jp")
    p.add_argument("--limit", type=int, default=0, help="限制采集条数（0=不限制）")
    p.add_argument("-o", "--output", help="输出文件路径")
    p.add_argument("--mode", choices=["full", "incremental", "test"], default="incremental")
    p.add_argument("--days", type=int, default=7, help="增量模式：过去 N 天")
    p.add_argument("--all-enabled", action="store_true", help="采集所有启用的类目")
    p.add_argument("--concurrent", type=int, default=4, help="并发实例数")
    p.add_argument("--headless/--no-headless", default=True)
    p.add_argument("--resume", action="store_true", help="从断点续采")
    return p.parse_args()


def main():
    args = parse_args()
    settings = load_settings(args.config)

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(settings.storage.data_root) / "raw" / f"leads_{args.marketplace}_{args.category or 'all'}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 初始化爬虫
    crawler = BSRCrawler(
        marketplace=args.marketplace,
        settings=settings,
        headless=args.headless,
    )

    if args.all_enabled:
        # 全量采集所有启用类目
        chain = CategoryChain(settings)
        enabled_cats = [c for c in chain.categories if c.enabled]
        logger.info(f"开始全量采集 {len(enabled_cats)} 个启用类目")
        for cat in enabled_cats:
            logger.info(f"采集类目: {cat.id} ({cat.zh_name})")
            crawler.crawl_category(cat.id, limit=args.limit, resume=args.resume)
    elif args.category:
        logger.info(f"采集单类目: {args.category}")
        crawler.crawl_category(args.category, limit=args.limit, resume=args.resume)
    else:
        logger.error("必须指定 --category 或 --all-enabled")
        sys.exit(1)

    # 导出结果
    crawler.export_csv(output_path)
    logger.info(f"采集完成，结果已导出: {output_path}")


if __name__ == "__main__":
    main()