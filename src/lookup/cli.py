#!/usr/bin/env python3
"""
amz-lead 补号层命令行入口 — **开源版仅提供接口定义**

实际的补号实现（探迹登录、验证码自愈、OCR识别、Excel回填）在私有模块 `_private` 中，
**不包含在开源分发中**。

使用方式：
1. 自行实现 `ILookupProvider` 接口（参考 `amz_lead.lookup.StubLookupProvider`）
2. 商业版：`pip install amz-lead[lookup-private]`（需商业授权）
3. 云服务版：联系商务获取 API 密钥

用法示例（使用存根提供者测试流水线）：
    amz-lookup --input data/processed/leads_resolved.csv --output data/enriched/leads_enriched.csv --provider stub
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amz_lead.lookup import ILookupProvider, StubLookupProvider, CompanyInfo, PhoneResult
from amz_lead.utils.config import load_settings
from amz_lead.utils.logging import setup_logging
import pandas as pd

logger = setup_logging(__name__)

# 尝试导入私有实现（商业版安装后可用）
try:
    from amz_lead.lookup._private.tungee_runner import TungeeRunner
    _HAS_PRIVATE = True
except ImportError:
    _HAS_PRIVATE = False
    TungeeRunner = None


def parse_args():
    p = argparse.ArgumentParser(description="Phone Number Enrichment — Interface Only (Private Implementation Required)")
    p.add_argument("-i", "--input", required=True, help="输入 CSV/XLSX 文件（含 resolved 主体名）")
    p.add_argument("-o", "--output", help="输出文件路径")
    p.add_argument("--config", default="configs/settings.yaml", help="配置文件")
    p.add_argument("--provider", choices=["stub", "tungee"], default="stub",
                   help="补号提供者：stub=测试存根，tungee=私有实现（需商业版）")
    p.add_argument("--batch-size", type=int, default=10, help="单批处理数量")
    p.add_argument("--max-rows", type=int, default=0, help="最大处理行数（0=全部）")
    p.add_argument("--build-batches", action="store_true", help="生成外呼工单分批文件")
    p.add_argument("--dry-run", action="store_true", help="试运行：只统计不实际查询")
    return p.parse_args()


def load_companies(input_path: Path) -> list[CompanyInfo]:
    """从 CSV 加载公司信息。"""
    df = pd.read_csv(input_path, dtype=str).fillna("")
    companies = []
    for _, row in df.iterrows():
        companies.append(CompanyInfo(
            name=row.get("company_name") or row.get("chinese_name") or row.get("店铺名") or "",
            unified_code=row.get("unified_code") or row.get("统一社会信用代码") or "",
            city=row.get("city") or row.get("城市") or "",
            address=row.get("address") or row.get("注册地址") or "",
            legal_rep=row.get("legal_rep") or row.get("法人代表") or "",
            confidence=float(row.get("confidence") or row.get("置信度") or 0.0),
        ))
    return companies


def save_results(output_path: Path, results: list[PhoneResult]) -> None:
    """保存结果到 CSV。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = [{
        "company_name": r.company_name,
        "phone": r.phone,
        "phone_label": r.phone_label,
        "email": r.email,
        "status": r.status,
        "source": r.source,
        "confidence": r.confidence,
        "error": r.error,
    } for r in results]
    pd.DataFrame(data).to_csv(output_path, index=False, encoding="utf-8-sig")
    logger.info(f"结果已保存: {output_path} ({len(results)} 条)")


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

    # 选择提供者
    if args.provider == "tungee":
        if not _HAS_PRIVATE:
            logger.error("探迹提供者需要商业版：`pip install amz-lead[lookup-private]`")
            sys.exit(1)
        provider = TungeeRunner(settings)
    else:
        provider = StubLookupProvider()

    logger.info(f"使用提供者: {provider.name} ({'私有实现' if args.provider == 'tungee' else '存根测试'})")

    companies = load_companies(input_path)
    if args.max_rows > 0:
        companies = companies[:args.max_rows]
    logger.info(f"加载公司数: {len(companies)}")

    if args.dry_run:
        logger.info("试运行模式：仅健康检查")
        ok = provider.health_check()
        logger.info(f"健康检查: {'通过' if ok else '失败'}")
        return

    if args.build_batches:
        logger.info("分批功能需要私有实现，请使用商业版")
        return

    # 执行批量查询
    logger.info("开始批量查询...")
    results = provider.lookup_batch(companies, max_workers=4)

    # 统计
    stats = {"callable": 0, "need_verify": 0, "no_phone": 0, "error": 0}
    for r in results:
        stats[r.status] = stats.get(r.status, 0) + 1
    logger.info(f"查询完成: 可打 {stats['callable']} | 需核实 {stats['need_verify']} | 无号 {stats['no_phone']} | 错误 {stats['error']}")

    save_results(output_path, results)


if __name__ == "__main__":
    main()