# src/lookup/_private/__init__.py
"""Private lookup implementations — NOT included in open-source distribution.

This module is only available when installing the commercial extra:
    pip install amz-lead[lookup-private] --index-url https://git.yourdomain.com/api/pypi/simple

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
# from .ocr_phone import OCRPhoneRecognizer       # macOS Vision + Tesseract 兜底
# from .ocr_rapid import RapidOCREngine, extract_phones_from_image  # RapidOCR (Linux/Windows, Apache-2.0)
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
#     "RapidOCREngine",
#     "extract_phones_from_image",
#     "ExcelBackfiller",
#     "BatchBuilder",
#     "TycLookup",
#     "PinyinResolver",
# ]

# OCR 栈说明:
# - ocr_phone.py: macOS Vision (Apple 内置) + Tesseract 兜底 —— 仅 macOS，零依赖
# - ocr_rapid.py: RapidOCR (ONNXRuntime + OpenCV headless) —— Linux/Windows/Server，Apache-2.0
# 推荐部署策略:
#   macOS 开发机: 优先 ocr_phone (系统级 Vision，最快最准)
#   Linux 服务器/容器: 仅 ocr_rapid (pip install rapidocr-onnxruntime onnxruntime opencv-python-headless)