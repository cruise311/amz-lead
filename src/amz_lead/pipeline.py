#!/usr/bin/env python3
"""
amz-lead 全链路流水线入口

一键跑通：采集 -> 还原 -> 评分 -> 补号 -> 分批

用法：
    amz-pipeline --category home-garden --limit 100 --output demo_leads.csv
    amz-pipeline --config configs/cats.yaml --mode incremental --stages collect,resolve,score
"""

import argparse
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amz_lead.utils.config import load_settings
from amz_lead.utils.logging import setup_logging

logger = setup_logging(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="amz-lead Full Pipeline Runner")
    p.add_argument("-c", "--category", help="类目 ID（单类目模式）")
    p.add_argument("--config", default="configs/cats.yaml", help="类目配置")
    p.add_argument("--settings", default="configs/settings.yaml", help="全局配置")
    p.add_argument("--marketplace", default="com", help="站点")
    p.add_argument("--limit", type=int, default=0, help="采集条数限制")
    p.add_argument("-o", "--output", help="最终输出文件（基础名，自动加后缀）")
    p.add_argument("--mode", choices=["full", "incremental", "test"], default="incremental")
    p.add_argument("--days", type=int, default=7, help="增量天数")
    p.add_argument("--stages", default="collect,resolve,score,lookup,batch",
                   help="执行阶段，逗号分隔：collect,resolve,score,lookup,batch")
    p.add_argument("--skip-collect", action="store_true", help="跳过采集，直接用现有 raw 文件")
    p.add_argument("--skip-lookup", action="store_true", help="跳过补号（无探迹额度时用）")
    p.add_argument("--concurrent", type=int, default=4, help="采集并发数")
    p.add_argument("--headless/--no-headless", default=True)
    p.add_argument("--dry-run", action="store_true", help="试运行：只打印将执行的命令")
    return p.parse_args()


def run_cmd(cmd, dry_run=False):
    logger.info(f"$ {' '.join(cmd)}")
    if dry_run:
        return True
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"命令失败: {' '.join(cmd)}")
        logger.error(f"stderr: {result.stderr}")
        return False
    logger.info(f"stdout: {result.stdout.strip()}")
    return True


def main():
    args = parse_args()
    settings = load_settings(args.settings)

    # 确定文件路径
    data_root = Path(settings.storage.data_root)
    data_root.mkdir(parents=True, exist_ok=True)

    base_name = args.output or f"leads_{args.marketplace}_{args.category or 'all'}"
    raw_file = data_root / "raw" / f"{base_name}_raw.csv"
    resolved_file = data_root / "processed" / f"{base_name}_resolved.csv"
    scored_file = data_root / "processed" / f"{base_name}_scored.csv"
    enriched_file = data_root / "enriched" / f"{base_name}_enriched.csv"
    batch_dir = data_root / "enriched" / "batches"

    stages = [s.strip() for s in args.stages.split(",")]

    # 阶段 1: 采集
    if "collect" in stages and not args.skip_collect:
        logger.info("=" * 60)
        logger.info("阶段 1/5: 采集")
        logger.info("=" * 60)
        cmd = [
            "amz-collect",
            "--config", args.config,
            "--marketplace", args.marketplace,
            "--mode", args.mode,
            "--days", str(args.days),
            "--limit", str(args.limit),
            "--output", str(raw_file),
            "--concurrent", str(args.concurrent),
        ]
        if args.headless:
            cmd.append("--headless")
        else:
            cmd.append("--no-headless")
        if args.category:
            cmd.extend(["--category", args.category])
        else:
            cmd.append("--all-enabled")
        if not run_cmd(cmd, args.dry_run):
            sys.exit(1)

    # 阶段 2: 还原
    if "resolve" in stages:
        logger.info("=" * 60)
        logger.info("阶段 2/5: 还原")
        logger.info("=" * 60)
        if not raw_file.exists():
            logger.error(f"原始文件不存在: {raw_file}，请先运行采集阶段")
            sys.exit(1)
        cmd = [
            "amz-resolve",
            "--input", str(raw_file),
            "--output", str(resolved_file),
            "--config", args.settings,
        ]
        if not run_cmd(cmd, args.dry_run):
            sys.exit(1)

    # 阶段 3: 评分
    if "score" in stages:
        logger.info("=" * 60)
        logger.info("阶段 3/5: 评分")
        logger.info("=" * 60)
        input_file = resolved_file if resolved_file.exists() else raw_file
        cmd = [
            "amz-resolve",
            "--input", str(input_file),
            "--output", str(scored_file),
            "--config", args.settings,
            "--rescore",
        ]
        if not run_cmd(cmd, args.dry_run):
            sys.exit(1)

    # 阶段 4: 补号
    if "lookup" in stages and not args.skip_lookup:
        logger.info("=" * 60)
        logger.info("阶段 4/5: 补号")
        logger.info("=" * 60)
        input_file = scored_file if scored_file.exists() else (resolved_file if resolved_file.exists() else raw_file)
        cmd = [
            "amz-lookup",
            "--input", str(input_file),
            "--output", str(enriched_file),
            "--config", args.settings,
            "--batch-size", "10",
        ]
        if not run_cmd(cmd, args.dry_run):
            sys.exit(1)

    # 阶段 5: 分批
    if "batch" in stages:
        logger.info("=" * 60)
        logger.info("阶段 5/5: 生成外呼工单分批")
        logger.info("=" * 60)
        input_file = enriched_file if enriched_file.exists() else (scored_file if scored_file.exists() else resolved_file)
        cmd = [
            "amz-lookup",
            "--input", str(input_file),
            "--output", str(batch_dir / f"{base_name}_batch.csv"),
            "--config", args.settings,
            "--build-batches",
            "--batch-size", "50",
        ]
        if not run_cmd(cmd, args.dry_run):
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("流水线执行完成")
    logger.info("=" * 60)
    logger.info(f"产出文件:")
    for f in [raw_file, resolved_file, scored_file, enriched_file]:
        if f.exists():
            logger.info(f"  {f}")
    if batch_dir.exists():
        logger.info(f"  {batch_dir}/ (分批工单)")


if __name__ == "__main__":
    main()