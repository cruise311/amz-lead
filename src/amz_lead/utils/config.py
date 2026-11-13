"""
配置加载工具
支持 YAML + 环境变量 + .local.yaml 覆盖
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml
from pydantic import BaseModel


class Settings(BaseModel):
    """全局配置模型"""
    storage: Dict[str, Any] = {}
    collect: Dict[str, Any] = {}
    resolve: Dict[str, Any] = {}
    lookup: Dict[str, Any] = {}
    scoring: Dict[str, Any] = {}
    logging: Dict[str, Any] = {}
    monitoring: Dict[str, Any] = {}

    class Config:
        extra = "allow"


def load_settings(config_path: str = "configs/settings.yaml") -> Settings:
    """
    加载配置，优先级：
    1. 默认配置
    2. configs/settings.yaml
    3. configs/settings.local.yaml（不提交 Git，存敏感信息）
    4. 环境变量（AMZ_LEAD_ 前缀）
    """
    project_root = Path(__file__).parent.parent.parent
    config_file = project_root / config_path
    local_file = project_root / config_path.replace(".yaml", ".local.yaml")

    # 1. 基础配置
    data = {}
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # 2. 本地覆盖
    if local_file.exists():
        with open(local_file, "r", encoding="utf-8") as f:
            local_data = yaml.safe_load(f) or {}
        data = _deep_merge(data, local_data)

    # 3. 环境变量覆盖
    data = _apply_env_overrides(data)

    return Settings(**data)


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """深度合并字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(data: Dict) -> Dict:
    """应用环境变量覆盖"""
    # 映射：环境变量 -> 配置路径
    env_mapping = {
        "AMZ_LEAD_TUNGEE_USERNAME": "lookup.tungee.username",
        "AMZ_LEAD_TUNGEE_PASSWORD": "lookup.tungee.password",
        "AMZ_LEAD_TYC_API_KEY": "lookup.tyc.api_key",
        "AMZ_LEAD_QCC_API_KEY": "lookup.qcc.api_key",
        "AMZ_LEAD_PROXY_URL": "collect.proxy.url",
        "AMZ_LEAD_DATA_ROOT": "storage.data_root",
        "AMZ_LEAD_LOG_LEVEL": "logging.level",
    }

    for env_key, config_path in env_mapping.items():
        value = os.getenv(env_key)
        if value is not None:
            _set_nested(data, config_path.split("."), value)

    return data


def _set_nested(d: Dict, keys: list, value: Any):
    """设置嵌套字典值"""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    # 类型转换
    if value.lower() in ("true", "false"):
        value = value.lower() == "true"
    elif value.isdigit():
        value = int(value)
    elif value.replace(".", "").isdigit():
        value = float(value)
    d[keys[-1]] = value


def load_yaml_config(path: str) -> Dict:
    """加载单个 YAML 配置文件"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# 单例
_settings: Optional[Settings] = None


def get_settings(config_path: str = "configs/settings.yaml") -> Settings:
    """获取全局配置单例"""
    global _settings
    if _settings is None:
        _settings = load_settings(config_path)
    return _settings