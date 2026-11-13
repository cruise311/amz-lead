# 私有仓库设置指南

## 概述

将 `src/lookup/_private/` 移至独立的私有仓库 `amz-lead-private`，通过私有 PyPI 源分发。

## 步骤 1：创建私有仓库

```bash
# 1. 在 GitHub/GitLab/Gitea 创建私有仓库
# 名称：amz-lead-private
# 可见性：Private

# 2. 本地初始化
cd ~/amz-lead
mkdir -p ../amz-lead-private/src/amz_lead_lookup_private
cp -r src/lookup/_private/* ../amz-lead-private/src/amz_lead_lookup_private/
```

## 步骤 2：私有仓库结构

```
amz-lead-private/
├── src/
│   └── amz_lead_lookup_private/
│       ├── __init__.py          # 导出所有实现类
│       ├── tungee_runner.py
│       ├── tungee_unlock_api.py
│       ├── captcha_agent.py
│       ├── ocr_phone.py
│       ├── ocr_rapid.py
│       ├── backfill_excel.py
│       ├── build_batches.py
│       ├── tyc_lookup.py
│       └── pinyin_resolve.py
├── pyproject.toml
├── README.md
├── LICENSE-PRIVATE.md
└── .gitignore
```

## 步骤 3：私有仓库 pyproject.toml

```toml
# amz-lead-private/pyproject.toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "amz-lead-lookup-private"
version = "0.1.0"
description = "Private lookup implementations for amz-lead (commercial license required)"
readme = "README.md"
license = {text = "Proprietary"}
requires-python = ">=3.10"
dependencies = [
    "scrapling>=0.1.17",
    "curl_cffi>=0.7.0",
    "rapidocr-onnxruntime>=1.3.0",
    "onnxruntime>=1.18.0",
    "opencv-python-headless>=4.8.0",
    "numpy>=1.24.0",
    "pandas>=2.0.0",
    "openpyxl>=3.1.0",
    "tenacity>=8.2.0",
    "pydantic>=2.5.0",
    "pyyaml>=6.0.0",
    "python-dotenv>=1.0.0",
    "playwright>=1.40.0",
    "lxml>=4.9.0",
]

[project.entry-points]
# 无 CLI 入口，仅作为库被 amz-lead 导入

[tool.setuptools.packages.find]
where = ["src"]
include = ["amz_lead_lookup_private*"]
```

## 步骤 4：发布到私有 PyPI

### 方案 A：GitHub Packages (推荐，免费私有)
```bash
# 1. 在 GitHub 启用 Packages
# 2. Settings -> Developer settings -> Personal access tokens -> Generate token (packages:read/write)
# 3. 配置 ~/.pypirc
cat > ~/.pypirc << 'EOF'
[distutils]
index-servers = github
[github]
repository = https://pypi.pkg.github.com/cruise311
username = cruise311
password = ghp_xxxxxxxxxxxx
EOF

# 4. 发布
cd ~/amz-lead-private
pip install build twine
python -m build
twine upload --repository github dist/*
```

### 方案 B：自建 PyPI (devpi / pypiserver / Nexus)
```bash
# 使用 devpi (推荐团队内部)
pip install devpi-server devpi-client
devpi-server --start
devpi use http://localhost:3141/root/pypi
devpi login root --password=""
devpi user -c youruser password=yourpass
devpi index -c yourindex bases=root/pypi
devpi use yourindex
cd ~/amz-lead-private && python -m build && twine upload -r yourindex dist/*
```

### 方案 C：Git 依赖 (最简单，无需 PyPI 服务)
```bash
# 直接从私有 Git 仓库安装
pip install git+ssh://git@github.com/cruise311/amz-lead-private.git@main

# 或指定 tag
pip install git+ssh://git@github.com/cruise311/amz-lead-private.git@v0.1.0
```

## 步骤 5：主项目集成

### 修改 amz-lead/pyproject.toml
```toml
[project.optional-dependencies]
lookup-private = [
    "amz-lead-lookup-private @ git+ssh://git@github.com/cruise311/amz-lead-private.git@main",
    # 或私有 PyPI:
    # "amz-lead-lookup-private @ https://pypi.yourdomain.com/simple/amz-lead-lookup-private",
]
```

### 使用方式
```bash
# 开发环境
pip install -e ".[dev,lookup-private]"

# 生产部署 (CI/CD 中配置 SSH key 或 PyPI token)
pip install "amz-lead[lookup-private]" --index-url https://pypi.yourdomain.com/simple
```

## 步骤 6：更新导入路径

主项目 `src/lookup/cli.py` 中：
```python
# 从私有包导入
try:
    from amz_lead_lookup_private import (
        TungeeRunner,
        CaptchaAgent,
        RapidOCREngine,
        ExcelBackfiller,
        BatchBuilder,
        TycLookup,
    )
    _HAS_PRIVATE = True
except ImportError:
    _HAS_PRIVATE = False
```

## 权限管理

| 角色 | 权限 |
|---|---|
| 核心开发 | 读/写私有仓库、发布版本 |
| CI/CD | 只读私有仓库、读私有 PyPI |
| 外包/实习生 | 仅开源仓库，无私有访问 |

## 版本同步策略

- `amz-lead` (开源) 与 `amz-lead-lookup-private` (闭源) **同步版本号**
- 发布 `amz-lead@v0.2.0` 时，同步发布 `amz-lead-lookup-private@v0.2.0`
- 使用 Git tag 触发 CI 自动发布两个包