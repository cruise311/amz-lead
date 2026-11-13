# src/lookup/_private/__init__.py
"""Private lookup implementations — NOT included in open-source distribution.

This module is only available when installing the commercial extra:
    pip install amz-lead[lookup-private]

Requires commercial license. Contact: cloud@amz-lead.example.com
"""

# The actual implementation classes are conditionally imported
# to avoid exposing them in the open-source package metadata.
# This file exists as a placeholder for the package structure.

__all__ = []

# Actual exports (when commercially licensed):
# from .tungee_runner import TungeeRunner
# from .tungee_unlock_api import TungeeUnlockAPI
# from .captcha_agent import CaptchaAgent
# from .ocr_phone import OCRPhoneRecognizer
# from .backfill_excel import ExcelBackfiller
# from .build_batches import BatchBuilder
# from .tyc_lookup import TycLookup
# from .pinyin_resolve import PinyinResolver
#
# __all__ = [
#     "TungeeRunner",
#     "TungeeUnlockAPI",
#     "CaptchaAgent",
#     "OCRPhoneRecognizer",
#     "ExcelBackfiller",
#     "BatchBuilder",
#     "TycLookup",
#     "PinyinResolver",
# ]