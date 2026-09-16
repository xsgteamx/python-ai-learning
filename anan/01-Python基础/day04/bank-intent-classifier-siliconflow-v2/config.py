"""项目配置：从环境变量读取 SiliconFlow API 参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int


def get_settings() -> Settings:
    """读取运行配置。

    API Key 不允许写死在源码中，必须通过环境变量或 .env 文件提供。
    """
    return Settings(
        api_key=os.getenv("SILICONFLOW_API_KEY", "").strip(),
        base_url=os.getenv(
            "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
        ).rstrip("/"),
        model=os.getenv(
            "SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V3.2"
        ).strip(),
        timeout_seconds=float(os.getenv("SILICONFLOW_TIMEOUT", "30")),
        max_retries=max(0, int(os.getenv("SILICONFLOW_MAX_RETRIES", "3"))),
    )
