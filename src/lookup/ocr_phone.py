#!/usr/bin/env python3
"""
ocr_phone.py — 号码截图本地 OCR(免费、离线,符合只用免费方案的原则)。

优先级:
  1. macOS Apple Vision 框架(pyobjc,系统内置识别引擎,无需联网)
  2. tesseract CLI(Windows/Linux 兜底,brew install tesseract / winget安装)

用法:
  python3 ocr_phone.py leads/pending_ocr/*.png     # 每张图输出 JSON: {图: [号码]}
  或被 tungee_runner.py 以模块方式调用 extract_phones_from_image()
"""
import json
import os
import re
import shutil
import subprocess
import sys

try:  # Windows 默认 GBK，输出 ✓ → 重定向到文件会 UnicodeEncodeError
    from _console import setup as _utf8_stdio
    _utf8_stdio()
except Exception:
    pass

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|0\d{2,3}-?\d{7,8}(?!\d)|400-?\d{3}-?\d{4}(?!\d)")


def _ocr_vision(path):
    """macOS Apple Vision。返回整图文本。实测:fast 档识别数字 100% 且暖启动 0.01s/图;
    accurate 档反而把数字行误读(7J/I&;E4F),禁用。中文行可能乱码,但号码提取不受影响。"""
    import Vision
    from Foundation import NSURL

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setUsesLanguageCorrection_(False)  # 号码场景关纠错,防数字被"纠正"
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(path), None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision 识别失败: {err}")
    lines = []
    for obs in (req.results() or []):
        for cand in obs.topCandidates_(1):
            lines.append(cand.string())
    return "\n".join(lines)


def _tesseract_cmd():
    """定位 tesseract:环境变量 TESSERACT_CMD > PATH > Windows 常见安装路径。"""
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if cmd:
        return cmd
    for base in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                 r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(base):
            return base
    return "tesseract"


def _ocr_tesseract(path):
    """tesseract 兜底:数字白名单 + 常规各跑一遍,合并文本。"""
    texts = []
    for extra in (["-c", "tessedit_char_whitelist=0123456789*-", "--psm", "6"],
                  ["--psm", "6"]):
        try:
            r = subprocess.run([_tesseract_cmd(), path, "stdout"] + extra,
                               capture_output=True, text=True, timeout=30)
            texts.append(r.stdout or "")
        except FileNotFoundError:
            raise RuntimeError(
                "未安装 tesseract:macOS 用 brew install tesseract;"
                "Windows 用 winget install UB-Mannheim.TesseractOCR(装完重开终端,"
                "或设环境变量 TESSERACT_CMD 指向 tesseract.exe)")
    return "\n".join(texts)


def ocr_text(path):
    engine = None
    try:
        text = _ocr_vision(path)
        engine = "vision"
    except ImportError:
        text = _ocr_tesseract(path)
        engine = "tesseract"
    return text, engine


def extract_phones_from_image(path):
    text, _ = ocr_text(path)
    # 探迹展示格式常为「159 2587 4653」,去掉数字间空格再匹配
    text = re.sub(r"(?<=\d) (?=\d)", "", text)
    out = set()
    for h in PHONE_RE.findall(text):
        if isinstance(h, tuple):  # 有捕获组的变体:取第一个非空组
            h = next(g for g in h if g)
        out.add(h)
    return sorted(out)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    result = {}
    for path in sys.argv[1:]:
        try:
            text, engine = ocr_text(path)
            text = re.sub(r"(?<=\d) (?=\d)", "", text)
            phones = set()
            for h in PHONE_RE.findall(text):
                if isinstance(h, tuple):
                    h = next(g for g in h if g)
                phones.add(h)
            result[path] = {"engine": engine, "phones": sorted(phones),
                            "raw": text.strip()[:400] if not phones else None}
        except Exception as e:
            result[path] = {"error": f"{e.__class__.__name__}: {e}"}
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
