# Private Module License Notice

## ⚠️ This directory contains PROPRIETARY code — NOT open source

The implementation files in `src/lookup/_private/` are **excluded from the MIT license** that covers the rest of this repository.

### Files affected:
- `tungee_runner.py`
- `tungee_unlock_api.py`
- `captcha_agent.py`
- `ocr_phone.py`
- `backfill_excel.py`
- `build_batches.py`
- `tyc_lookup.py`
- `pinyin_resolve.py`

### License terms:
- **Commercial license required** for any use, modification, or distribution
- **Not permitted** to copy, modify, distribute, or sublicense without written agreement
- **Not permitted** to reverse engineer or derive implementations from the public interface
- Available only via commercial agreement: cloud@amz-lead.example.com

### Open-source components (MIT licensed):
- `src/lookup/__init__.py` — Interface definitions (`ILookupProvider`, `CompanyInfo`, `PhoneResult`)
- `src/lookup/cli.py` — CLI entry point (uses interface only)
- `StubLookupProvider` — Test stub implementation

### Compliance:
If you received this code without a commercial license, you are in violation of copyright law. Please delete all files in `_private/` and contact us for licensing options.