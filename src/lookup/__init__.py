# src/lookup/__init__.py
"""Lookup layer — Interface definitions only.

Implementation classes (tungee_runner, captcha_agent, ocr_phone, etc.)
are in the private module `_private` and NOT included in the open-source distribution.

To use phone enrichment, you must either:
1. Implement `ILookupProvider` yourself (see example below)
2. Use the closed-source `amz-lead[lookup-private]` extra (commercial license required)
3. Use the hosted cloud service (contact for pricing)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompanyInfo:
    """Input: resolved company information."""
    name: str                           # 公司全称
    unified_code: str = ""              # 统一社会信用代码
    city: str = ""                      # 注册城市
    address: str = ""                   # 注册地址
    legal_rep: str = ""                 # 法人代表
    confidence: float = 0.0             # 还原置信度 0-1


@dataclass
class PhoneResult:
    """Output: phone enrichment result."""
    company_name: str                   # 查询的公司名
    phone: str = ""                     # 手机号（中间4位已脱敏：138****1234）
    phone_raw: str = ""                 # 原始号码（仅闭源版返回，开源版为空）
    phone_label: str = ""               # 号码标签：mobile/landline/unknown
    email: str = ""                     # 邮箱
    status: str = "no_phone"            # 状态：callable/need_verify/no_phone/error
    source: str = ""                    # 数据来源：tungee/tyc/qcc/manual
    confidence: float = 0.0             # 结果置信度 0-1
    error: str = ""                     # 错误信息（如有）


class ILookupProvider(ABC):
    """Phone lookup provider interface.

    All implementations must be thread-safe and handle rate limiting internally.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier: 'tungee', 'tyc', 'qcc', 'custom'."""
        ...

    @abstractmethod
    def lookup(self, company: CompanyInfo) -> PhoneResult:
        """Single company phone lookup."""
        ...

    def lookup_batch(self, companies: list[CompanyInfo], max_workers: int = 4) -> list[PhoneResult]:
        """Batch lookup with concurrency control.

        Default implementation uses ThreadPoolExecutor.
        Override for provider-specific batch APIs.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = [None] * len(companies)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(self.lookup, c): i
                for i, c in enumerate(companies)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = PhoneResult(
                        company_name=companies[idx].name,
                        status="error",
                        error=str(e),
                    )
        return results

    @abstractmethod
    def health_check(self) -> bool:
        """Check if provider is available (login valid, quota > 0)."""
        ...


# Example: Minimal stub implementation for testing without accounts
class StubLookupProvider(ILookupProvider):
    """Stub provider for testing pipeline without real accounts."""

    @property
    def name(self) -> str:
        return "stub"

    def lookup(self, company: CompanyInfo) -> PhoneResult:
        return PhoneResult(
            company_name=company.name,
            phone="138****0000",
            phone_label="mobile",
            status="need_verify",
            source="stub",
            confidence=0.1,
            error="Stub provider - no real lookup performed",
        )

    def health_check(self) -> bool:
        return True


__all__ = [
    "CompanyInfo",
    "PhoneResult",
    "ILookupProvider",
    "StubLookupProvider",
]