# src/lookup/_private/ocr_rapid.py
"""RapidOCR 实现 — Apache-2.0 许可，替代 paddleocr(AGPL)。

仅在安装了 rapidocr-onnxruntime 后可用：
    pip install rapidocr-onnxruntime onnxruntime opencv-python-headless

用法:
    from amz_lead.lookup._private.ocr_rapid import RapidOCREngine
    engine = RapidOCREngine()
    phones = engine.extract_phones("screenshot.png")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

try:
    from rapidocr_onnxruntime import RapidOCR
    _HAS_RAPIDOCR = True
except ImportError:
    _HAS_RAPIDOCR = False
    RapidOCR = None  # type: ignore

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|0\d{2,3}-?\d{7,8}(?!\d)|400-?\d{3}-?\d{4}(?!\d)")


class RapidOCREngine:
    """RapidOCR 封装，提供与现有 ocr_phone.py 兼容的接口。"""

    def __init__(self, use_angle_cls: bool = False, det_model: str | None = None, rec_model: str | None = None):
        """
        Args:
            use_angle_cls: 是否启用方向分类（竖屏截图通常不需要）
            det_model: 检测模型路径（None 使用默认）
            rec_model: 识别模型路径（None 使用默认）
        """
        if not _HAS_RAPIDOCR:
            raise RuntimeError(
                "rapidocr-onnxruntime 未安装。请运行："
                "pip install rapidocr-onnxruntime onnxruntime opencv-python-headless"
            )
        self._engine = RapidOCR(
            use_angle_cls=use_angle_cls,
            det_model_path=det_model,
            rec_model_path=rec_model,
        )

    def ocr_text(self, image_path: str | Path) -> str:
        """OCR 识别整图文本，返回拼接后的字符串。"""
        result, _ = self._engine(str(image_path))
        if not result:
            return ""
        # result: List[List[float, float, float, float], str, float] = [box, text, score]
        lines = [item[1] for item in result if item[1].strip()]
        return "\n".join(lines)

    def extract_phones(self, image_path: str | Path) -> list[str]:
        """从图片中提取所有手机号/座机/400 号码。"""
        text = self.ocr_text(image_path)
        # 探迹展示格式常为「159 2587 4653」，去掉数字间空格再匹配
        text = re.sub(r"(?<=\d) (?=\d)", "", text)
        out = set()
        for h in PHONE_RE.findall(text):
            if isinstance(h, tuple):  # 捕获组变体
                h = next(g for g in h if g)
            out.add(h)
        return sorted(out)


def get_engine() -> RapidOCREngine:
    """工厂函数：单例模式避免重复加载模型。"""
    if not hasattr(get_engine, "_instance"):
        get_engine._instance = RapidOCREngine()
    return get_engine._instance


def extract_phones_from_image(image_path: str | Path) -> list[str]:
    """兼容旧接口的快捷函数。"""
    return get_engine().extract_phones(image_path)


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("用法: python ocr_rapid.py <image1> [image2...]")
        sys.exit(1)

    engine = get_engine()
    result = {}
    for path in sys.argv[1:]:
        try:
            phones = engine.extract_phones(path)
            text = engine.ocr_text(path)
            result[path] = {"engine": "rapidocr", "phones": phones, "raw": text[:400] if text else None}
        except Exception as e:
            result[path] = {"error": f"{e.__class__.__name__}: {e}"}

    print(json.dumps(result, ensure_ascii=False, indent=1))